"""Collect configuration errors without bootstrapping services or writing state.

Semantic checks inspect independent fields even when another part of the document
has a schema error. Only a completely valid document becomes a runtime AppConfig.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from tentacles.config import AppConfig, AppSpec, SourceSpec, format_loc
from tentacles.errors import ConfigError, ConfigIssue, ConfigValidationError
from tentacles.llm.config import ModelSpec, STAGES
from tentacles.personas.models import persona_name
from tentacles.pipelines.adapters import ModelRequirement
from tentacles.pipelines.registry import PROCESSORS, STAGES as FILTER_STAGES
from tentacles.prompts import load_prompt


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _entries(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _schema_errors(exc: ValidationError, prefix: Sequence[str | int] = ()) -> list[ConfigIssue]:
    errors = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False):
        loc = (*prefix, *error['loc'])
        # Tagged-union labels are Pydantic implementation details, not YAML keys.
        if len(loc) > 4 and loc[0] == 'pipelines' and loc[2] == 'filters':
            loc = (*loc[:4], *loc[5:])
        message = error['msg']
        # These validators can quote input text (which may contain interpolated
        # secrets). Never include that text in public configuration diagnostics.
        if error['type'] == 'value_error' and loc and loc[-1] in {
                'timezone', 'schedule', 'filter_schedule', 'window', 'feedback_window', 'cors_origins'}:
            message = f"invalid {loc[-1]}"
        if error['type'] == 'union_tag_invalid':
            loc = (*loc, 'type')
            message = 'unknown filter stage type (expected keyword, decision or llm)'
        errors.append(ConfigIssue(format_loc(loc), message))
    return errors


def read_persona_names(db_path: Path) -> set[str]:
    """Read just names, including live WAL data, without Store or migrations.

    Even mode=ro may create WAL/SHM or write shared-memory read marks. Copy the
    database and journals into a private, disposable directory instead; SQLite
    recovers that copy, never the source. Do not copy SHM: SQLite rebuilds it
    from the committed WAL frames. Immutable reads would silently miss those.

    Reject/retry copies changed by writers or checkpoints rather than accepting
    a torn snapshot. Three attempts bound contention; continuous churn fails
    explicitly instead of silently treating existing Personas as absent.
    A pre-persona schema is legitimate. Other read failures must not be mistaken
    for an empty database, nor expose SQLite error text or filesystem details.
    """
    try:
        db_path = db_path.resolve()
        paths = [db_path, Path(str(db_path) + '-wal'), Path(str(db_path) + '-journal')]
        for _ in range(3):
            before = _file_states(paths)
            if before[0] is None:
                return set()
            with TemporaryDirectory(prefix='tentacles-config-') as directory:
                snapshot = Path(directory) / db_path.name
                try:
                    for source, state in zip(paths, before):
                        if state is not None:
                            shutil.copyfile(source, Path(directory) / source.name)
                except FileNotFoundError:
                    continue  # A checkpoint or journal cleanup raced the copy.
                if before != _file_states(paths):
                    continue
                with closing(sqlite3.connect(snapshot)) as conn:
                    conn.execute('PRAGMA query_only = ON')
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='persona_versions'").fetchone() is None:
                        return set()
                    return {row[0] for row in conn.execute("SELECT DISTINCT name FROM persona_versions")}
    except (OSError, sqlite3.Error):
        raise ConfigValidationError([
            ConfigIssue('app.data_dir', 'cannot read existing Persona names from database')
        ]) from None
    raise ConfigValidationError([
        ConfigIssue('app.data_dir', 'database changed while reading Persona names; retry config check')
    ])


def _file_states(paths: list[Path]) -> list[tuple[int, ...] | None]:
    states: list[tuple[int, ...] | None] = []
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            states.append(None)
        else:
            states.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    return states


def _path(value: Any, config_path: Path) -> Path | None:
    try:
        path = TypeAdapter(Path).validate_python(value).expanduser()
        return path if path.is_absolute() else (config_path.parent / path).resolve()
    except (ValidationError, OSError, ValueError, RuntimeError):
        return None


def _model_error(models: Mapping[str, ModelSpec], defaults: Mapping[str, Any],
                 stage: str, override: Any, where: str, *, kind: str = 'chat') -> ConfigIssue | None:
    # Invalid scalar types already have schema diagnostics.
    if override is not None and not isinstance(override, str):
        return None
    name = override if override is not None else defaults.get(stage)
    model = models.get(name) if isinstance(name, str) else None
    if model is None or model.kind != kind:
        return ConfigIssue(where, f'requires an existing {kind} model or {stage}-stage default')
    return None


def _adapter_model_errors(requirements: tuple[ModelRequirement, ...], data: Mapping[str, Any],
                          models: Mapping[str, ModelSpec], defaults: Mapping[str, Any],
                          where: str) -> list[ConfigIssue]:
    errors = []
    for requirement in requirements:
        override = data.get('model')
        if requirement.optional and override is None:
            continue
        error = _model_error(models, defaults, requirement.stage, override, f'{where}.model',
                             kind=requirement.kind)
        if error is not None:
            errors.append(error)
    return errors


def _source_options(data: Mapping[str, Any], where: str) -> tuple[BaseModel | None, list[ConfigIssue]]:
    from tentacles.sources import SOURCE_TYPES

    source_type = data.get('type')
    if not isinstance(source_type, str):
        return None, []
    cls = SOURCE_TYPES.get(source_type)
    if cls is None:
        return None, [ConfigIssue(f'{where}.type', 'unknown source type')]
    options = {key: value for key, value in data.items() if key not in SourceSpec.model_fields}
    errors: list[ConfigIssue] = []
    try:
        updates = TypeAdapter(bool).validate_python(data.get('update_existing', False))
    except ValidationError:
        updates = False  # common schema already reports invalid booleans
    supports_updates = cls.supports_updates and (source_type != 'web' or options.get('mode', 'list') == 'list')
    if updates and not supports_updates:
        errors.append(ConfigIssue(f'{where}.update_existing', 'updates are not supported for this source type/mode'))
    # Independent cross-field rules must still run when another option (or
    # another semantic rule) fails. Keep field parsing in the Source schemas.
    if source_type == 'web':
        mode = options.get('mode', 'list')
        if mode == 'list' and options.get('selectors') is None:
            errors.append(ConfigIssue(f'{where}.selectors', 'list mode requires selectors'))
        if mode in ('list', 'page') and options.get('model') is not None:
            errors.append(ConfigIssue(f'{where}.model', 'model override is only supported in llm mode'))
    try:
        return cls.Options.model_validate(options), errors
    except ValidationError as exc:
        errors.extend(ConfigIssue(f'{where}.{error.path}', error.message)
                      for error in _schema_errors(exc))
        return None, errors


def validate_source(spec: SourceSpec, where: str, config: AppConfig | None = None) -> BaseModel:
    """Runtime source construction uses the same source-local schema checks."""
    options, errors = _source_options(spec.model_dump(), where)
    if config is not None:
        error = _source_model_error(spec.model_dump(), config.models,
                                    {str(key): value for key, value in config.defaults.models.items()}, where)
        if error is not None:
            errors.append(error)
    if errors:
        raise ConfigValidationError(errors)
    assert options is not None
    return options


def _source_model_error(data: Mapping[str, Any], models: Mapping[str, ModelSpec],
                        defaults: Mapping[str, Any], where: str) -> ConfigIssue | None:
    if data.get('type') == 'web' and data.get('mode', 'list') == 'llm':
        return _model_error(models, defaults, 'extract', data.get('model'), f'{where}.model')
    return None


def validate_config(config: AppConfig | Mapping[str, Any], existing_personas: Collection[str] = (),
                    *, path: Path | None = None) -> list[ConfigIssue]:
    """Return all schema and reference errors; perform only local file reads."""
    data = config.model_dump() if isinstance(config, AppConfig) else config
    config_path = config.path if isinstance(config, AppConfig) else path or Path.cwd() / 'tentacles.yaml'
    errors: list[ConfigIssue] = []
    try:
        AppConfig.model_validate(data)
    except ValidationError as exc:
        errors.extend(_schema_errors(exc))

    models: dict[str, ModelSpec] = {}
    for name, value in _mapping(data.get('models')).items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', str(name)):
            errors.append(ConfigIssue(f'models.{name}', 'names must use letters, digits, dot, dash or underscore'))
        try:
            models[name] = ModelSpec.model_validate(value)
        except ValidationError:
            pass  # Already collected by the application schema.
    defaults = _mapping(_mapping(data.get('defaults')).get('models'))
    if data.get('models') or defaults:
        for stage in STAGES:
            default_name = defaults.get(stage)
            model = models.get(default_name) if isinstance(default_name, str) else None
            where = f'defaults.models.{stage}'
            if not default_name:
                errors.append(ConfigIssue(where, 'a default model is required'))
            elif model is None:
                errors.append(ConfigIssue(where, 'unknown model'))
            elif model.kind != ('decision' if stage == 'decision' else 'chat'):
                errors.append(ConfigIssue(where, f'model must have kind {"decision" if stage == "decision" else "chat"}'))

    source_ids: set[str] = set()
    for i, value in enumerate(_entries(data.get('sources'))):
        source = _mapping(value)
        where = f'sources[{i}]'
        source_id = source.get('id')
        if isinstance(source_id, str):
            if source_id in source_ids:
                errors.append(ConfigIssue(f'{where}.id', 'duplicate source id'))
            source_ids.add(source_id)
        _, source_errors = _source_options(source, where)
        errors.extend(source_errors)
        error = _source_model_error(source, models, defaults, where)
        if error is not None:
            errors.append(error)

    seeds = _mapping(data.get('personas'))
    for name, value in seeds.items():
        where = f'personas.{name}'
        try:
            if persona_name(str(name)) != name:
                errors.append(ConfigIssue(where, 'names must not have leading or trailing whitespace'))
        except ConfigError as exc:
            errors.append(ConfigIssue(where, exc.message))
        seed = _mapping(value)
        if name not in existing_personas and seed.get('profile_file') is not None:
            profile = _path(seed['profile_file'], config_path)
            if profile is None:
                errors.append(ConfigIssue(f'{where}.profile_file', 'invalid profile file path'))
            else:
                try:
                    profile.read_text(encoding='utf-8')
                except (OSError, UnicodeError):
                    errors.append(ConfigIssue(f'{where}.profile_file', 'cannot read UTF-8 profile file'))

    pipeline_ids: set[str] = set()
    for i, value in enumerate(_entries(data.get('pipelines'))):
        pipeline = _mapping(value)
        where = f'pipelines[{i}]'
        pipeline_id = pipeline.get('id')
        if isinstance(pipeline_id, str):
            if pipeline_id in pipeline_ids:
                errors.append(ConfigIssue(f'{where}.id', 'duplicate pipeline id'))
            pipeline_ids.add(pipeline_id)
        for j, source_id in enumerate(_entries(pipeline.get('sources'))):
            if isinstance(source_id, str) and source_id not in source_ids:
                errors.append(ConfigIssue(f'{where}.sources[{j}]', 'unknown source'))
        persona = pipeline.get('persona', 'default')
        if isinstance(persona, str) and persona not in seeds and persona not in existing_personas:
            errors.append(ConfigIssue(f'{where}.persona', 'unknown Persona'))
        for j, value in enumerate(_entries(pipeline.get('filters'))):
            filter_data = _mapping(value)
            stage_type = filter_data.get('type', 'keyword')
            adapter = FILTER_STAGES.get(stage_type) if isinstance(stage_type, str) else None
            if adapter is not None:
                errors.extend(_adapter_model_errors(adapter.models, filter_data, models, defaults,
                                                     f'{where}.filters[{j}]'))
        processor = _mapping(pipeline.get('processor'))
        processor_type = processor.get('type', 'digest')
        processor_adapter = PROCESSORS.get(processor_type) if isinstance(processor_type, str) else None
        if processor_adapter is not None:
            errors.extend(_adapter_model_errors(processor_adapter.models, processor, models, defaults,
                                                 f'{where}.processor'))
        if processor.get('prompt_file') is not None:
            prompt = _path(processor['prompt_file'], config_path)
            if prompt is None:
                errors.append(ConfigIssue(f'{where}.processor.prompt_file', 'invalid prompt file path'))
            else:
                try:
                    load_prompt(prompt)
                except ConfigError as exc:
                    errors.append(ConfigIssue(f'{where}.processor.prompt_file', exc.message))
    return errors


def parse_config(data: Mapping[str, Any], path: Path, errors: list[ConfigIssue]) -> AppConfig:
    """Resolve database-only names before checking references, never via Store."""
    data_dir = _path(_mapping(data.get('app')).get('data_dir', AppSpec().data_dir), path)
    names: set[str] = set()
    if data_dir is None:
        errors.append(ConfigIssue('app.data_dir', 'invalid data directory path'))
    else:
        try:
            names = read_persona_names(data_dir / 'tentacles.db')
        except ConfigValidationError as exc:
            errors.extend(exc.errors)
    errors.extend(validate_config(data, names, path=path))
    if errors:
        raise ConfigValidationError(errors)
    config = AppConfig.model_validate(data)
    config._path = path
    return config
