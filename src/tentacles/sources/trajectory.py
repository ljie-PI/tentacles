"""Local action trajectory and distilled-source metadata for `type: browser`.

One exploration run appends one JSONL step per browser action: the decision page
context the Agent acted on and the observation it got back. That file is what
the distillation turn reads, so it lands under the ignored `data/` tree and never
in the Store or the repository. The metadata of a distilled script is the same
kind of local-only artifact: it names the selectors the fixed script kept, but
never cookies, account names or page bodies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tentacles.diagnostics import redact_debug_context
from tentacles.errors import ConfigError

TRAJECTORY_DIR = "trajectories"
META_DIR = "browser_sources"
# The digest is the decision context of one step, not the page: a body-wide A11y
# excerpt is already bounded in `browser_session`, this cuts it for the JSONL.
A11Y_DIGEST_CHARS = 2048
MAX_CANDIDATE_SELECTORS = 12


def trajectory_path(data_dir: Path, source_id: str, run_id: int) -> Path:
    """`<data_dir>/trajectories/<source_id>/<run_id>.jsonl`, inside that subtree."""
    root = data_dir.expanduser().resolve() / TRAJECTORY_DIR
    return _contained(root, root / source_id / f"{run_id}.jsonl")


def meta_path(data_dir: Path, source_id: str) -> Path:
    """`<data_dir>/browser_sources/<source_id>.meta.json`, inside that subtree."""
    root = data_dir.expanduser().resolve() / META_DIR
    return _contained(root, root / f"{source_id}.meta.json")


def _contained(root: Path, path: Path) -> Path:
    """Both artifacts are path-derived from config values, so prove the containment."""
    if not path.resolve().is_relative_to(root):
        raise ConfigError(f"browser source artifacts must stay inside {root}, not {path}")
    return path


@dataclass(frozen=True)
class TrajectoryStep:
    """One recorded browser action with the page context that motivated it."""

    step: int
    timestamp: datetime
    decision_context: dict[str, Any]
    action: dict[str, Any]
    observation: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "timestamp": _isoformat(self.timestamp),
            "decision_context": self.decision_context,
            "action": self.action,
            "observation": self.observation,
        }


class TrajectoryRecorder:
    """Append-only JSONL of one exploration run, already redacted on write."""

    def __init__(self, path: Path, *, secrets: Sequence[str] = (),
                 now: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self._secrets = tuple(secret for secret in secrets if secret)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._steps: list[TrajectoryStep] = []

    @classmethod
    def open(cls, data_dir: Path, source_id: str, run_id: int, *,
             secrets: Sequence[str] = (), now: Callable[[], datetime] | None = None) -> TrajectoryRecorder:
        path = trajectory_path(data_dir, source_id, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Start every run from an empty file: a re-run of the same id overwrites
        # its own record instead of appending to a stale trajectory.
        path.write_text("", encoding="utf-8")
        return cls(path, secrets=secrets, now=now)

    @property
    def steps(self) -> list[TrajectoryStep]:
        """The recorded steps in execution order (the distillation input)."""
        return list(self._steps)

    def record(self, *, tool: str, args: Mapping[str, Any], rationale: str,
               context: Mapping[str, Any], observation: Mapping[str, Any]) -> TrajectoryStep:
        """Append one step; the caller supplies what it observed, not what it hoped."""
        step = TrajectoryStep(
            step=len(self._steps) + 1, timestamp=self._now(),
            decision_context=dict(context),
            action={"tool": tool, "args": dict(args), "rationale": rationale},
            observation=dict(observation),
        )
        line = redact_debug_context(step.as_dict(), self._secrets)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_dump(line) + "\n")
        self._steps.append(step)
        return step


@dataclass(frozen=True)
class BrowserSourceMeta:
    """Local-only summary of a distilled script; never written to the repository."""

    source_id: str
    script: str
    page_url: str
    items: int
    selectors: list[str]
    steps: int
    trial_seconds: float
    model: str
    reasoning_level: str
    trajectory: str
    distilled_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "script": self.script,
            "page_url": self.page_url,
            "items": self.items,
            "selectors": list(self.selectors),
            "steps": self.steps,
            "trial_seconds": round(self.trial_seconds, 3),
            "model": self.model,
            "reasoning_level": self.reasoning_level,
            "trajectory": self.trajectory,
            "distilled_at": _isoformat(self.distilled_at),
        }


def write_meta(data_dir: Path, meta: BrowserSourceMeta) -> Path:
    """Write the metadata atomically so a half-written file is never read."""
    path = meta_path(data_dir, meta.source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(_dump(redact_debug_context(meta.as_dict())) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def digest(a11y_snapshot: str, *, limit: int = A11Y_DIGEST_CHARS) -> str:
    """Bound an A11y excerpt for a decision context or a meta file."""
    text = " ".join(a11y_snapshot.split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _dump(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
