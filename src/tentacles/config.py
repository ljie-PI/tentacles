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
from tentacles.timeutil import is_duration

DEFAULT_CONFIG = Path("config") / "tentacles.yaml"
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_CRON_FIELD = re.compile(r"^[\d*/,-]+$")
_SCHEDULE_ALIASES = {"@hourly", "@daily", "@weekly"}


def check_schedule_shape(expr: str) -> str:
    """Interval (30m), alias (@daily) or 5 cron fields; cron semantics are checked by the scheduler."""
    expr = expr.strip()
    fields = expr.split()
    if is_duration(expr) or expr in _SCHEDULE_ALIASES or (
            len(fields) == 5 and all(_CRON_FIELD.match(f) for f in fields)):
        return expr
    raise ValueError(f"invalid schedule {expr!r}: use an interval like 30m/2h/1d, "
                     "@hourly/@daily/@weekly, or a 5-field cron expression")


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
        return check_schedule_shape(v)

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: AppSpec = Field(default_factory=AppSpec)
    sources: list[SourceSpec] = Field(default_factory=list)

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
    return config


def _check_sources(config: AppConfig) -> None:
    from tentacles.sources import validate_source  # source types register on import

    seen: set[str] = set()
    for i, spec in enumerate(config.sources):
        if spec.id in seen:
            raise ConfigError(f"sources[{i}].id: duplicate source id {spec.id!r}")
        seen.add(spec.id)
        validate_source(spec, where=f"sources[{i}]")
