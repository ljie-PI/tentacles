"""Repair a fixed `type: browser` script in one controlled `pi` session.

A daily replay is zero-token and therefore fragile: when the target site
changes, the installed script raises, times out or returns no items. This module
owns that recovery. One `pi --mode rpc` session is primed with the old script,
the failure site (traceback plus the live page snapshot) and the selector
summary of the local meta file, and rewrites the script; every candidate still
has to pass the same static gate and standalone trial a cold start passed. At
most two candidates per replay. If none passes, nothing is written: the old
script stays byte-identical and the caller records `pi_heal_exhausted`.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from tentacles.config import AppConfig, BrowserPiSpec
from tentacles.diagnostics import clip
from tentacles.sources.browser_bridge import BridgeServer, BrowserBridge
from tentacles.sources.browser_session import work_tab
from tentacles.sources.distiller import (
    BRIDGE_EXTENSION, SCRIPT_CONTRACT, DistillFailed, DistillRequest, InstalledScript,
    bounded_prompt, prepare_candidate,
)
from tentacles.sources.pi_rpc import PiRpcClient, PiRpcError, PiUsage
from tentacles.sources.trajectory import TrajectoryRecorder

log = logging.getLogger(__name__)

MAX_OLD_SCRIPT_CHARS = 24_000
MAX_FAILURE_CHARS = 4000
MAX_META_CHARS = 2000
# The meta file is local, but only its selector summary belongs in a prompt;
# `trajectory` is an absolute host path and stays out.
_META_KEYS = ("source_id", "script", "page_url", "items", "selectors", "steps",
              "trial_seconds", "model", "reasoning_level", "distilled_at")

HEAL_RULES = (
    "You are repairing the fixed Playwright script that replays one page every day.\n"
    "A live replay just failed, so the page or the script has changed.\n"
    "Return exactly one ```python block and nothing else.\n\n" + SCRIPT_CONTRACT
)


class HealFailed(Exception):
    """A replay failure that one healing session could not repair."""

    def __init__(self, message: str, *, context: Mapping[str, Any] | None = None,
                 error: BaseException | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = dict(context or {})
        self.error = error


@dataclass(frozen=True)
class HealRequest:
    """What one repair needs from its source, without the adapter's options model."""

    source_id: str
    run_id: int
    page_url: str
    task: str
    max_items: int
    timeout: float
    chrome_profile: str | None
    script_path: Path
    pi: BrowserPiSpec
    old_source: str
    failure: str
    snapshot: dict[str, Any]
    meta: dict[str, Any]

    def distill_request(self) -> DistillRequest:
        """The trial gate takes the same shape a cold start gives it."""
        return DistillRequest(
            source_id=self.source_id, run_id=self.run_id, page_url=self.page_url, task=self.task,
            max_items=self.max_items, timeout=self.timeout, chrome_profile=self.chrome_profile,
            script_path=self.script_path, pi=self.pi)


class RepairSession:
    """One controlled pi session whose every turn gets the real work tab.

    The tab lives for the length of one `ask`, so the Agent can inspect the live
    page it must repair, and it is closed before the candidate is gated: the
    trial needs the same Chrome profile and only one work tab may hold it.
    """

    def __init__(self, pi: PiRpcClient, request: HealRequest, config: AppConfig,
                 bridge: BrowserBridge) -> None:
        self._pi = pi
        self._request = request
        self._config = config
        self._bridge = bridge

    async def ask(self, prompt: str) -> str:
        async with work_tab(self._config, chrome_profile=self._request.chrome_profile) as page:
            self._bridge.attach(page)
            try:
                await _open_entry(page, self._request)
                return await self._pi.ask(prompt, timeout=self._request.pi.timeout)
            finally:
                self._bridge.detach()


@asynccontextmanager
async def repair_session(request: HealRequest, config: AppConfig, *, bridge: BrowserBridge,
                         on_usage: Callable[[PiUsage], None] | None = None
                         ) -> AsyncIterator[RepairSession]:
    """The one Agent session a replay failure may spend, from spawn to forced exit."""
    async with BridgeServer.open(bridge, config.browser.webbridge_url) as server:
        async with PiRpcClient(request.pi, BRIDGE_EXTENSION, cwd=config.data_dir,
                               env=server.child_env(), on_usage=on_usage) as pi:
            yield RepairSession(pi, request, config, bridge)


async def heal(request: HealRequest, *, config: AppConfig,
               on_usage: Callable[[PiUsage], None] | None = None,
               secrets: Sequence[str] = ()) -> InstalledScript:
    """Repair, gate and install one script; raise `HealFailed` rather than overwrite junk."""
    if not request.task.strip():
        raise HealFailed(
            f"browser source {request.source_id!r} has no `task` text to describe the repair")
    recorder = TrajectoryRecorder.open(config.data_dir, request.source_id, request.run_id, secrets=secrets)
    bridge = BrowserBridge(recorder, page_url=request.page_url, max_items=request.max_items,
                           timeout_ms=int(request.timeout * 1000))
    try:
        async with repair_session(request, config, bridge=bridge, on_usage=on_usage) as session:
            accepted = await prepare_candidate(
                request.distill_request(), config=config, recorder=recorder, ask=session.ask,
                prompt=_heal_prompt(request), rules=HEAL_RULES, kind="heal")
    except DistillFailed as exc:
        raise HealFailed(exc.message, context=exc.context, error=exc.error) from None
    except PiRpcError as exc:
        raise HealFailed(exc.message, error=exc,
                         context={"trajectory_path": str(recorder.path)}) from None
    installed = accepted.install()
    log.info("browser: healed %s after %d trial attempt(s) into %s",
             request.source_id, installed.attempts, installed.path)
    return installed


async def _open_entry(page: Any, request: HealRequest) -> None:
    """Put the entry page in front of the Agent; a failed load is the snapshot's job."""
    try:
        await page.goto(request.page_url, wait_until="domcontentloaded",
                        timeout=int(request.timeout * 1000))
    except Exception as exc:
        log.debug("browser heal: cannot preload %s (%s)", request.page_url, type(exc).__name__)


def _heal_prompt(request: HealRequest) -> str:
    return bounded_prompt("\n".join([
        HEAL_RULES,
        "",
        f"Source id: {request.source_id}",
        f"Entry page: {request.page_url}",
        f"Item cap: {request.max_items}",
        "",
        "Collection task:",
        request.task.strip(),
        "",
        "How the replay failed:",
        clip(request.failure, MAX_FAILURE_CHARS),
        "",
        "Live page when the replay broke (address, title, A11y excerpt):",
        json.dumps(request.snapshot, ensure_ascii=False, default=str),
        "",
        "Local metadata of the installed script:",
        json.dumps(_meta_summary(request.meta), ensure_ascii=False, default=str),
        "",
        "The installed script that must be repaired:",
        "```python",
        clip(request.old_source, MAX_OLD_SCRIPT_CHARS),
        "```",
        "",
        "You have the work tab at the entry page: inspect the live structure with",
        "browser_snapshot / browser_evaluate before you rewrite the selectors.",
    ]))


def _meta_summary(meta: Mapping[str, Any]) -> dict[str, Any]:
    """The local meta file's selector summary, bounded and without host paths."""
    summary = {key: meta[key] for key in _META_KEYS if key in meta}
    selectors = summary.get("selectors")
    if isinstance(selectors, list):
        summary["selectors"] = [clip(str(selector), 120) for selector in selectors]
    text = json.dumps(summary, ensure_ascii=False, default=str)
    return summary if len(text) <= MAX_META_CHARS else {**summary, "truncated": True}
