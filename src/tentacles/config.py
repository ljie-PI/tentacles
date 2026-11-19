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
from typing import Any, Literal
from urllib.parse import urlsplit
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

ReasoningLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
DEFAULT_CHROME_PROFILE = "Default"
DEFAULT_BROWSER_SCRIPTS_DIR = Path("../scripts/browser_sources")
HEADLESS_ERROR = "browser sources run headed Chrome only; headless is not supported"
# Chrome receives the profile as `--profile-directory=<name>`, so a value that
# could introduce another flag or leave the User Data directory is rejected.
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,63}")


def reject_headless(value: bool) -> bool:
    """`headless: true` is a configuration error: browser sources stay headed."""
    if value:
        raise ValueError(HEADLESS_ERROR)
    return value


def chrome_profile_name(value: str) -> str:
    """Validate a Chrome profile directory name used as `--profile-directory`."""
    if not _PROFILE_NAME.fullmatch(value.strip()):
        raise ValueError("must be a Chrome profile directory name such as Default or Profile 1")
    return value.strip()


def _branch_name(value: str) -> str:
    """A git branch (or branch prefix) that `git worktree`/`git push` accept verbatim."""
    name = value.strip()
    if not name or name.startswith("-") or any(char.isspace() for char in name) or ".." in name:
        raise ValueError("must be a git branch name without whitespace, a leading '-' or '..'")
    return name


class StorageSpec(BaseModel):
    """冷热分层：热库保留窗口，以及按自然年归档库的位置。"""

    model_config = ConfigDict(extra="forbid")

    hot_retention_days: int = Field(default=365, ge=1, le=36500)
    archive_dir: Path = Path("../data/archive")


class AppSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Shanghai"
    data_dir: Path = Path("../data")
    reports_dir: Path = Path("../reports")
    storage: StorageSpec = Field(default_factory=StorageSpec)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown timezone {v!r}")
        return v


_SOURCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
_RESERVED_SOURCE_IDS = frozenset({"fetch"})


def validate_source_id(value: str) -> str:
    """Validate a source identifier for URL routing, file paths and git branches."""
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 128 or not re.fullmatch(_SOURCE_ID_PATTERN, cleaned):
        raise ValueError("source id must be 1..128 chars of letters, digits, '_', '.' or '-'")
    if cleaned in _RESERVED_SOURCE_IDS:
        raise ValueError(f"source id {cleaned!r} is reserved")
    if ".." in cleaned or cleaned.endswith(".") or cleaned.endswith(".lock"):
        raise ValueError("source id must not contain '..' or end with '.' or '.lock'")
    return cleaned


class SourceSpec(BaseModel):
    """Fields common to every source; type-specific options stay in the extras
    and are validated by the source type's own options model."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=128, pattern=_SOURCE_ID_PATTERN)
    type: str
    schedule: str
    name: str | None = None
    enabled: bool = True
    update_existing: bool = False

    _valid_id = field_validator("id")(validate_source_id)

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, v: str) -> str:
        Schedule.parse(v)
        return v.strip()

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class BrowserPiSpec(BaseModel):
    """Agent settings for browser exploration and self-healing (#82, #83)."""

    model_config = ConfigDict(extra="forbid")

    binary: str = "pi"
    model: str = "anthropic/claude-sonnet-4-6"
    reasoning_level: ReasoningLevel = "high"
    timeout: float = Field(default=300, gt=0, allow_inf_nan=False)


class BrowserPrSpec(BaseModel):
    """Review-branch publication of distilled and self-healed scripts (#83)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    base_branch: str = "master"
    branch_prefix: str = "auto/browser-source-"
    labels: list[str] = Field(default_factory=lambda: ["ready-for-human"])

    _base_branch = field_validator("base_branch")(_branch_name)
    _branch_prefix = field_validator("branch_prefix")(_branch_name)

    @field_validator("labels")
    @classmethod
    def _label_names(cls, value: list[str]) -> list[str]:
        labels = [label.strip() for label in value if label.strip()]
        if not labels:
            raise ValueError("must name at least one PR label such as ready-for-human")
        return list(dict.fromkeys(labels))


class BrowserSpec(BaseModel):
    """The headed-only Chrome runtime shared by every `type: browser` source."""

    model_config = ConfigDict(extra="forbid")

    # Chrome's Remote Debugging endpoint; attached to with connect_over_cdp.
    cdp_url: str = "http://127.0.0.1:9222"
    # Reserved for the exploration bridge of #82.
    webbridge_url: str = "http://127.0.0.1:10086"
    # Launch a headed Chrome for the target profile when nothing listens on cdp_url.
    auto_launch: bool = False
    chrome_executable: Path | None = None
    chrome_user_data_dir: Path | None = None
    default_profile: str = DEFAULT_CHROME_PROFILE
    scripts_dir: Path = DEFAULT_BROWSER_SCRIPTS_DIR
    headless: bool = False
    pr: BrowserPrSpec = Field(default_factory=BrowserPrSpec)
    pi: BrowserPiSpec = Field(default_factory=BrowserPiSpec)

    _headed_only = field_validator("headless")(reject_headless)

    @field_validator("cdp_url")
    @classmethod
    def _cdp_endpoint(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError("must be an http(s) endpoint with an explicit port") from None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or port is None:
            raise ValueError("must be an http(s) endpoint with an explicit port")
        return value.rstrip("/")

    @field_validator("default_profile")
    @classmethod
    def _profile(cls, value: str) -> str:
        return chrome_profile_name(value)


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
    browser: BrowserSpec = Field(default_factory=BrowserSpec)

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

    @property
    def archive_dir(self) -> Path:
        return self.resolve(self.app.storage.archive_dir)

    @property
    def browser_scripts_dir(self) -> Path:
        """The repository-controlled directory holding fixed Playwright scripts."""
        return self.resolve(self.browser.scripts_dir)

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


def load_raw_yaml(config_path: Path) -> tuple[str, dict[str, Any]]:
    """Read UTF-8 YAML text and its uninterpolated top-level mapping."""
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ConfigValidationError([ConfigIssue('<root>', 'cannot read UTF-8 config file')]) from None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        location = f' at line {mark.line + 1}, column {mark.column + 1}' if mark is not None else ''
        raise ConfigValidationError([ConfigIssue('<root>', f'invalid YAML{location}')]) from None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigValidationError([ConfigIssue('<root>', 'top level must be a mapping')])
    return text, raw


def load_env_values(config_path: Path, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Load merged .env and process environment values, wrapping decode/IO errors."""
    try:
        return _load_env(config_path, env)
    except (OSError, UnicodeError):
        raise ConfigValidationError([ConfigIssue('<root>', 'cannot read UTF-8 environment file')]) from None


def load_config(path: Path | str | None = None, *, env: Mapping[str, str] | None = None) -> AppConfig:
    from tentacles.config_validation import parse_config

    config_path = find_config(path, env)
    _, raw = load_raw_yaml(config_path)

    errors: list[ConfigIssue] = []
    env_values = load_env_values(config_path, env)
    data = interpolate(raw, env_values, errors=errors)
    return parse_config(data, config_path, errors)
