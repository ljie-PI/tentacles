"""Atomic YAML persistence and validation for Web/API source mutations."""
from __future__ import annotations

import os
from collections.abc import Collection
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

import yaml

from tentacles.api.models import SourceCreate, SourceUpdate
from tentacles.config import AppConfig, SourceSpec, _load_env, interpolate
from tentacles.config_validation import parse_config, validate_config
from tentacles.errors import ConfigError, ConfigIssue, ConfigValidationError, Conflict, NotFound
from tentacles.schemas import SourceConfigView

_TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    'rss': ('url', 'max_age_days', 'timeout'),
    'imap': ('host', 'port', 'ssl', 'folders', 'include_subfolders', 'initial_days',
             'max_fetch', 'search', 'exclude_senders', 'timeout'),
    'web': ('url', 'mode', 'selectors', 'selector', 'fetch_content', 'on_detail_error',
            'emit_baseline', 'max_input_chars', 'model', 'concurrency', 'timeout'),
    'browser': ('page_url', 'task', 'chrome_profile', 'max_items', 'on_detail_error', 'timeout'),
}

_NULL_IF_BLANK = frozenset({'name', 'selector', 'model', 'chrome_profile', 'task'})


def get_source_config(config: AppConfig, source_id: str) -> SourceConfigView:
    spec = config.source(source_id)
    return _view_of(config, spec)


def _view_of(config: AppConfig, spec: SourceSpec) -> SourceConfigView:
    opts = spec.options
    source_type = cast(Any, spec.type)
    selectors = opts.get('selectors')
    clean_selectors = ({k: str(v) for k, v in selectors.items() if v is not None}
                       if isinstance(selectors, dict) else None)
    return SourceConfigView(
        id=spec.id,
        type=source_type,
        name=spec.name,
        schedule=spec.schedule,
        enabled=spec.enabled,
        update_existing=spec.update_existing,
        url=opts.get('url'),
        max_age_days=opts.get('max_age_days'),
        timeout=opts.get('timeout'),
        host=opts.get('host'),
        port=opts.get('port'),
        ssl=opts.get('ssl'),
        folders=list(opts['folders']) if isinstance(opts.get('folders'), list) else None,
        include_subfolders=opts.get('include_subfolders'),
        initial_days=opts.get('initial_days'),
        max_fetch=opts.get('max_fetch'),
        search=opts.get('search'),
        exclude_senders=list(opts['exclude_senders']) if isinstance(opts.get('exclude_senders'), list) else None,
        mode=opts.get('mode'),
        selectors=clean_selectors,
        selector=opts.get('selector'),
        fetch_content=opts.get('fetch_content'),
        on_detail_error=opts.get('on_detail_error'),
        emit_baseline=opts.get('emit_baseline'),
        max_input_chars=opts.get('max_input_chars'),
        model=opts.get('model'),
        concurrency=opts.get('concurrency'),
        page_url=opts.get('page_url'),
        task=opts.get('task'),
        chrome_profile=opts.get('chrome_profile'),
        max_items=opts.get('max_items'),
        pipelines=[p.id for p in config.pipelines if spec.id in p.sources],
    )


def _load_raw_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError):
        raise ConfigValidationError([ConfigIssue('<root>', 'cannot read UTF-8 config file')]) from None
    except yaml.YAMLError:
        raise ConfigValidationError([ConfigIssue('<root>', 'invalid YAML')]) from None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigValidationError([ConfigIssue('<root>', 'top level must be a mapping')])
    return raw


def _clean_value(key: str, value: Any) -> Any:
    if key in _NULL_IF_BLANK and isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if key == 'selectors' and isinstance(value, dict):
        cleaned = {k: v.strip() for k, v in value.items() if isinstance(v, str) and v.strip()}
        return cleaned or None
    return value


def _apply_fields(entry: dict[str, Any], source_type: str, values: dict[str, Any], fields: set[str]) -> None:
    allowed = {'name', 'schedule', 'enabled', 'update_existing', *_TYPE_FIELDS[source_type]}
    for key in fields & allowed:
        val = _clean_value(key, values.get(key))
        if val is None:
            entry.pop(key, None)
        else:
            entry[key] = val
    if source_type == 'web':
        mode = entry.get('mode', 'list')
        if mode in ('list', 'page'):
            entry.pop('model', None)
        if mode != 'list':
            entry.pop('selectors', None)
        if mode != 'page':
            entry.pop('selector', None)


def _sync_pipelines(raw: dict[str, Any], source_id: str, selected: list[str]) -> None:
    pipelines = raw.get('pipelines')
    if pipelines is None:
        if selected:
            raise NotFound(f'unknown pipeline {selected[0]!r}')
        return
    if not isinstance(pipelines, list):
        raise ConfigError('pipelines must be a list')
    known: dict[str, dict[str, Any]] = {
        p['id']: p for p in pipelines if isinstance(p, dict) and isinstance(p.get('id'), str)
    }
    target = set(selected)
    for pid in selected:
        if pid not in known:
            raise NotFound(f'unknown pipeline {pid!r}')
    for pid, p in known.items():
        current = list(p.get('sources') or [])
        if pid in target and source_id not in current:
            current.append(source_id)
        elif pid not in target and source_id in current:
            current = [sid for sid in current if sid != source_id]
        p['sources'] = current


def _validate_and_save(raw: dict[str, Any], path: Path, existing_personas: Collection[str]) -> AppConfig:
    env_values = _load_env(path, None)
    errors: list[ConfigIssue] = []
    interpolated = interpolate(deepcopy(raw), env_values, errors=errors)
    parsed = parse_config(interpolated, path, errors)
    semantic_errors = validate_config(parsed, existing_personas)
    if semantic_errors:
        raise ConfigValidationError(semantic_errors)
    rendered = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.tentacles-',
                                suffix='.yaml.tmp', delete=False) as tmp:
            tmp.write(rendered)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return parsed


def create_source_in_config(config: AppConfig, payload: SourceCreate, *,
                            existing_personas: Collection[str]) -> tuple[AppConfig, SourceConfigView]:
    source_id = payload.id.strip()
    if any(s.id == source_id for s in config.sources):
        raise Conflict(f'source {source_id!r} already exists')
    raw = _load_raw_yaml(config.path)
    sources = list(raw.get('sources') or [])
    entry: dict[str, Any] = {'id': source_id, 'type': payload.type}
    fields = set(payload.model_fields_set) | {'schedule', 'enabled', 'update_existing'}
    _apply_fields(entry, payload.type, payload.model_dump(), fields)
    sources.append(entry)
    raw['sources'] = sources
    if payload.pipelines:
        _sync_pipelines(raw, source_id, payload.pipelines)
    updated = _validate_and_save(raw, config.path, existing_personas)
    return updated, get_source_config(updated, source_id)


def update_source_in_config(config: AppConfig, source_id: str, payload: SourceUpdate, *,
                            existing_personas: Collection[str]) -> tuple[AppConfig, SourceConfigView]:
    spec = config.source(source_id)
    raw = _load_raw_yaml(config.path)
    sources = raw.get('sources')
    if not isinstance(sources, list):
        raise NotFound(f'unknown source {source_id!r}')
    target: dict[str, Any] | None = None
    for item in sources:
        if isinstance(item, dict) and item.get('id') == source_id:
            target = item
            break
    if target is None:
        raise NotFound(f'unknown source {source_id!r}')
    _apply_fields(target, spec.type, payload.model_dump(), set(payload.model_fields_set))
    if 'pipelines' in payload.model_fields_set:
        _sync_pipelines(raw, source_id, payload.pipelines or [])
    updated = _validate_and_save(raw, config.path, existing_personas)
    return updated, get_source_config(updated, source_id)
