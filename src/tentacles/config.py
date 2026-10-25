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
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from tentacles.errors import ConfigIssue, ConfigValidationError, NotFound
from tentacles.server_config import ServerSpec
from tentacles.schedule import Schedule
from tentacles.llm.config import Defaults, ModelSpec
from tentacles.personas.models import PersonaSeed, PersonaUpdate
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
    server: ServerSpec = Field(default_factory=ServerSpec)

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


def interpolate(obj: Any, env: Mapping[str, str], where: str = "", *,
                errors: list[ConfigIssue] | None = None) -> Any:
    """Replace ${VAR} / ${VAR:-default} in every string, recursively."""
    if errors is None:
        errors = []
        result = interpolate(obj, env, where, errors=errors)
        if errors:
            raise ConfigValidationError(errors)
        return result
    if isinstance(obj, str):
        def sub(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            errors.append(ConfigIssue(where or '<root>', f'missing env {name}'))
            return ''
        return _ENV_REF.sub(sub, obj)
    if isinstance(obj, dict):
        return {k: interpolate(v, env, f"{where}.{k}" if where else str(k), errors=errors) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate(v, env, f"{where}[{i}]", errors=errors) for i, v in enumerate(obj)]
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
    from tentacles.config_validation import parse_config

    config_path = find_config(path, env)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        raise ConfigValidationError([ConfigIssue('<root>', 'cannot read UTF-8 config file')]) from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        location = f' at line {mark.line + 1}, column {mark.column + 1}' if mark is not None else ''
        raise ConfigValidationError([ConfigIssue('<root>', f'invalid YAML{location}')]) from None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigValidationError([ConfigIssue('<root>', 'top level must be a mapping')])

    errors: list[ConfigIssue] = []
    try:
        env_values = _load_env(config_path, env)
    except (OSError, UnicodeError):
        raise ConfigValidationError([ConfigIssue('<root>', 'cannot read UTF-8 environment file')]) from None
    data = interpolate(raw, env_values, errors=errors)
    return parse_config(data, config_path, errors)
