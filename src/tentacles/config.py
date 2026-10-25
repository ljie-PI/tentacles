"""Single YAML config: loading, ${ENV} interpolation and validation.

Every error is a ConfigError whose message names the offending location in the
file, e.g. ``sources[1].url``. Relative paths resolve against the directory the
config file lives in.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator

from tentacles.errors import ConfigError, NotFound
from tentacles.schedule import Schedule
from tentacles.llm.config import Defaults, ModelSpec, STAGES
from tentacles.personas.models import PersonaSeed, PersonaUpdate, persona_name
from tentacles.pipelines.config import PipelineSpec

DEFAULT_CONFIG = Path("config") / "tentacles.yaml"
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class AppSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Shanghai"
    data_dir: Path = Path("../data")
    reports_dir: Path = Path("../reports")

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown timezone {v!r}")
        return v


class SourceSpec(BaseModel):
    """Fields common to every source; type-specific options stay in the extras
    and are validated by the source type's own options model."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    type: str
    schedule: str
    name: str | None = None
    enabled: bool = True

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, v: str) -> str:
        Schedule.parse(v)
        return v.strip()

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: AppSpec = Field(default_factory=AppSpec)
    sources: list[SourceSpec] = Field(default_factory=list)
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    defaults: Defaults = Field(default_factory=Defaults)
    personas: dict[str, PersonaSeed] = Field(default_factory=dict)
    pipelines: list[PipelineSpec] = Field(default_factory=list)
    persona_update: PersonaUpdate = Field(default_factory=PersonaUpdate)

    _path: Path = PrivateAttr(default=Path(DEFAULT_CONFIG))

    @property
    def path(self) -> Path:
        return self._path

    def resolve(self, p: Path) -> Path:
        p = p.expanduser()
        return p if p.is_absolute() else (self._path.parent / p).resolve()

    @property
    def data_dir(self) -> Path:
        return self.resolve(self.app.data_dir)

    @property
    def reports_dir(self) -> Path:
        return self.resolve(self.app.reports_dir)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tentacles.db"

    def source(self, source_id: str) -> SourceSpec:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise NotFound(f"unknown source {source_id!r}")


def format_loc(loc: Sequence[str | int]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "<root>"


def interpolate(obj: Any, env: Mapping[str, str], where: str = "") -> Any:
    """Replace ${VAR} / ${VAR:-default} in every string, recursively."""
    if isinstance(obj, str):
        def sub(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            raise ConfigError(f"missing env {name} at {where or '<root>'}")
        return _ENV_REF.sub(sub, obj)
    if isinstance(obj, dict):
        return {k: interpolate(v, env, f"{where}.{k}" if where else str(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate(v, env, f"{where}[{i}]") for i, v in enumerate(obj)]
    return obj


def find_config(path: Path | str | None = None, env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    chosen = Path(path) if path else Path(env["TENTACLES_CONFIG"]) if env.get("TENTACLES_CONFIG") \
        else DEFAULT_CONFIG
    return chosen.expanduser().resolve()


def _load_env(config_path: Path, env: Mapping[str, str] | None) -> dict[str, str]:
    """Process env wins over .env files (next to the config, then one level up)."""
    merged: dict[str, str] = {}
    for d in (config_path.parent.parent, config_path.parent):
        f = d / ".env"
        if f.is_file():
            merged.update({k: v for k, v in dotenv_values(f).items() if v is not None})
    merged.update(os.environ if env is None else env)
    return merged


def load_config(path: Path | str | None = None, *, env: Mapping[str, str] | None = None) -> AppConfig:
    config_path = find_config(path, env)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: top level must be a mapping")

    data = interpolate(raw, _load_env(config_path, env))
    try:
        config = AppConfig.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise ConfigError(f"{format_loc(err['loc'])}: {err['msg']}") from exc
    config._path = config_path
    _check_sources(config)
    _check_models(config)
    pipeline_ids: set[str] = set()
    for i, pipeline in enumerate(config.pipelines):
        if pipeline.id in pipeline_ids:
            raise ConfigError(f'pipelines[{i}].id: duplicate pipeline id')
        pipeline_ids.add(pipeline.id)
        unknown = set(pipeline.sources) - {source.id for source in config.sources}
        if unknown:
            raise ConfigError(f'pipelines[{i}].sources: unknown source {sorted(unknown)[0]!r}')
    for name in config.personas:
        if persona_name(name) != name:
            raise ConfigError('personas: names must not have leading or trailing whitespace')
    return config


def _check_sources(config: AppConfig) -> None:
    from tentacles.sources import validate_source  # source types register on import

    seen: set[str] = set()
    for i, spec in enumerate(config.sources):
        if spec.id in seen:
            raise ConfigError(f"sources[{i}].id: duplicate source id {spec.id!r}")
        seen.add(spec.id)
        validate_source(spec, where=f"sources[{i}]")


def _check_models(config: AppConfig) -> None:
    # RSS-only configurations remain valid without any model configuration.
    if not config.models and not config.defaults.models:
        return
    for name in config.models:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise ConfigError('models: names must use letters, digits, dot, dash or underscore')
    for stage in STAGES:
        where = f'defaults.models.{stage}'
        default_name = config.defaults.models.get(stage)
        if not default_name:
            raise ConfigError(f'{where}: a default model is required')
        spec = config.models.get(default_name)
        if spec is None:
            raise ConfigError(f'{where}: unknown model {default_name!r}')
        kind = 'decision' if stage == 'decision' else 'chat'
        if spec.kind != kind:
            raise ConfigError(f'{where}: model {default_name!r} must have kind {kind}')
