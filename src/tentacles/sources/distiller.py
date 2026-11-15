"""Distill a recorded pi exploration into a fixed `type: browser` script.

A cold start runs the Agent over the real work tab (see `browser_bridge`), then
this module turns the recorded trajectory into repository code. The script is
repository code, so nothing is written to `browser.scripts_dir` until the merged
static gate (`browser_script.load_script`) accepts it **and** a standalone trial
run on a fresh real `BrowserContext` returns usable entries. One corrective turn
is allowed, fed with the failure and the page it happened on; a second rejection
fails the fetch and leaves no script behind.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tentacles.config import AppConfig, BrowserPiSpec
from tentacles.diagnostics import clip, validation_errors
from tentacles.errors import ConfigError
from tentacles.sources.browser_bridge import BridgeServer, BrowserBridge
from tentacles.sources.browser_contract import BrowserExtractOutput, ScriptGateError
from tentacles.sources.browser_script import load_script
from tentacles.sources.browser_session import page_snapshot, work_tab
from tentacles.sources.pi_rpc import PiRpcClient, PiRpcError, PiUsage
from tentacles.sources.trajectory import (
    MAX_CANDIDATE_SELECTORS, BrowserSourceMeta, TrajectoryRecorder, TrajectoryStep,
    trajectory_path, write_meta,
)

log = logging.getLogger(__name__)

# One initial script plus at most one corrective turn (#82).
MAX_CORRECTION_ROUNDS = 1
MAX_PROMPT_STEPS = 40
MAX_PROMPT_CHARS = 48_000
MAX_SAMPLE_ITEMS = 5
MAX_FEEDBACK_CHARS = 6000
BRIDGE_EXTENSION = Path(__file__).with_name("browser_bridge.js")
_GATE_ERROR_CODE = "pi_script_rejected"
_RPC_ERROR_CODE = "pi_rpc_failed"


class DistillFailed(Exception):
    """A cold start that could not produce a script this round."""

    def __init__(self, error_code: str, message: str, *, context: Mapping[str, Any] | None = None,
                 error: BaseException | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.context = dict(context or {})
        self.error = error


@dataclass(frozen=True)
class DistillRequest:
    """What one cold start needs from its source, without the adapter's options model."""

    source_id: str
    run_id: int
    page_url: str
    task: str
    max_items: int
    timeout: float
    chrome_profile: str | None
    script_path: Path
    pi: BrowserPiSpec


@dataclass(frozen=True)
class DistilledScript:
    """A script that passed both gates, with the local artifacts to report."""

    path: Path
    trajectory: Path
    output: BrowserExtractOutput
    source: str
    trial_seconds: float
    selectors: list[str]
    steps: int
    meta: Path


async def distill(request: DistillRequest, *, config: AppConfig,
                  on_usage: Callable[[PiUsage], None] | None = None,
                  secrets: Sequence[str] = ()) -> DistilledScript:
    """Explore, distill and gate one source; raise `DistillFailed` rather than write junk."""
    if not request.task.strip():
        raise DistillFailed(_RPC_ERROR_CODE,
                            f"browser source {request.source_id!r} has no `task` text and no fixed script")
    recorder = TrajectoryRecorder.open(config.data_dir, request.source_id, request.run_id, secrets=secrets)
    bridge = BrowserBridge(recorder, page_url=request.page_url, max_items=request.max_items,
                            timeout_ms=int(request.timeout * 1000))
    try:
        async with BridgeServer.open(bridge, config.browser.webbridge_url) as server:
            async with PiRpcClient(request.pi, BRIDGE_EXTENSION, cwd=config.data_dir,
                                   env=server.child_env(), on_usage=on_usage) as pi:
                await _explore(request, config, bridge, pi, recorder)
                source, output, seconds = await _distill_script(
                    request, config, pi, recorder, samples=bridge.exploration)
    except PiRpcError as exc:
        raise DistillFailed(_RPC_ERROR_CODE, exc.message, error=exc,
                            context={"trajectory_path": str(recorder.path)}) from None
    return _publish(config.data_dir, request, recorder, source=source, output=output, seconds=seconds)


async def _explore(request: DistillRequest, config: AppConfig, bridge: BrowserBridge,
                   pi: PiRpcClient, recorder: TrajectoryRecorder) -> None:
    """Drive the work tab through the Agent's only tools, recording every step."""
    async with work_tab(config, chrome_profile=request.chrome_profile) as page:
        bridge.attach(page)
        try:
            await pi.ask(_exploration_prompt(request), timeout=request.pi.timeout)
        finally:
            bridge.detach()
    if not recorder.steps:
        raise DistillFailed(
            _RPC_ERROR_CODE,
            f"the exploration agent performed no browser action for {request.source_id!r}",
            context={"trajectory_path": str(recorder.path), "stderr": pi.stderr_tail})


async def _distill_script(request: DistillRequest, config: AppConfig, pi: PiRpcClient,
                          recorder: TrajectoryRecorder,
                          *, samples: BrowserExtractOutput | None) -> tuple[str, BrowserExtractOutput, float]:
    """Ask for a script, then let the gates decide; one corrective turn at most."""
    candidate = trajectory_path(config.data_dir, request.source_id, request.run_id).with_suffix(".candidate.py")
    prompt = _distillation_prompt(request, recorder.steps,
                                  [item.model_dump() for item in samples.items[:MAX_SAMPLE_ITEMS]]
                                  if samples is not None else [])
    feedback: str | None = None
    last_problem = "the agent produced no script"
    try:
        for round_index in range(MAX_CORRECTION_ROUNDS + 1):
            reply = await pi.ask(feedback or prompt, timeout=request.pi.timeout)
            source = _python_block(reply)
            failure: DistillFailed | None
            if source is None:
                failure = DistillFailed(
                    _GATE_ERROR_CODE, "your answer contained no ```python block, so there was no script to verify")
            else:
                output, seconds, failure = await _gate(candidate, request, config, source)
                if failure is None:
                    assert output is not None
                    return source, output, seconds
            assert failure is not None
            last_problem = failure.message
            log.warning("browser: distilled script for %s rejected (round %d): %s",
                        request.source_id, round_index + 1, clip(failure.message, 200))
            feedback = _correction_prompt(failure.message, failure.context)
        raise DistillFailed(
            _GATE_ERROR_CODE,
            f"no distilled script for {request.source_id!r} passed the replay gate "
            f"after {MAX_CORRECTION_ROUNDS + 1} attempt(s)",
            context={"trajectory_path": str(recorder.path),
                     "last_failure": clip(last_problem, MAX_FEEDBACK_CHARS)})
    finally:
        candidate.unlink(missing_ok=True)


async def _gate(candidate: Path, request: DistillRequest, config: AppConfig, source: str
                ) -> tuple[BrowserExtractOutput | None, float, DistillFailed | None]:
    """Run both gates on one candidate; the merged loader is the static contract."""
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(source, encoding="utf-8")
    try:
        extract = load_script(candidate)
    except ScriptGateError as exc:
        return None, 0.0, DistillFailed(_GATE_ERROR_CODE, exc.message)
    return await _trial(extract, request, config)


async def _trial(extract: Any, request: DistillRequest, config: AppConfig
                 ) -> tuple[BrowserExtractOutput | None, float, DistillFailed | None]:
    """Gate 2: the script must work on a real tab, once, with no help from the Agent."""
    started = time.monotonic()
    async with work_tab(config, chrome_profile=request.chrome_profile) as page:
        try:
            output = await asyncio.wait_for(
                extract(page, page_url=request.page_url, max_items=request.max_items,
                        timeout_ms=int(request.timeout * 1000)),
                timeout=request.timeout)
        except (PlaywrightTimeoutError, asyncio.TimeoutError):
            return None, 0.0, DistillFailed(
                _GATE_ERROR_CODE, f"the trial run timed out after {request.timeout:g}s",
                context=await page_snapshot(page))
        except Exception as exc:
            return None, 0.0, DistillFailed(
                _GATE_ERROR_CODE, f"the trial run raised {type(exc).__name__}: {clip(str(exc), 300)}",
                context=await page_snapshot(page), error=exc)
        snapshot = await page_snapshot(page)
    seconds = time.monotonic() - started
    try:
        validated = BrowserExtractOutput.model_validate(output)
    except Exception as exc:
        return None, seconds, DistillFailed(
            _GATE_ERROR_CODE, "the trial run did not return a BrowserExtractOutput "
            f"({'; '.join(validation_errors(exc))})", context=snapshot, error=exc)
    problems = trial_problems(validated, request.page_url)
    if problems:
        return None, seconds, DistillFailed(
            _GATE_ERROR_CODE, "the trial run returned unusable entries: " + "; ".join(problems),
            context=snapshot)
    return validated, seconds, None


def trial_problems(output: BrowserExtractOutput, page_url: str) -> list[str]:
    """What a trial run must not return: no entries, an unusable link, a wrong page."""
    problems: list[str] = []
    if not output.items:
        problems.append("no items")
    for index, item in enumerate(output.items):
        if _resolved(item.url, output.page_url or page_url) is None:
            problems.append(f"item {index} has no usable http(s) link ({item.url!r})")
    if not _same_site(output.page_url, page_url):
        problems.append(f"page_url {output.page_url!r} is not the entry page {page_url!r}")
    return problems


def _resolved(value: str | None, page_url: str) -> str | None:
    """Resolve a script link the way ingest will, and require a real web address."""
    if not value or not value.strip():
        return None
    try:
        url = httpx.URL(urljoin(page_url, value.strip()))
    except (ValueError, httpx.InvalidURL):
        return None
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        return None
    return str(url)


def _same_site(actual: str, expected: str) -> bool:
    """A redirect may change `www.` or the path; a different host is a different page."""
    left, right = urlsplit(actual), urlsplit(expected)
    if left.scheme not in {"http", "https"} or not left.hostname or not right.hostname:
        return False
    return _host(left.hostname) == _host(right.hostname)


def _host(host: str) -> str:
    lowered = host.lower()
    return lowered[4:] if lowered.startswith("www.") else lowered


def _publish(data_dir: Path, request: DistillRequest, recorder: TrajectoryRecorder, *, source: str,
             output: BrowserExtractOutput, seconds: float) -> DistilledScript:
    """Write the script and its local metadata only after both gates passed."""
    path = request.script_path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".py.tmp")
    temporary.write_text(source, encoding="utf-8")
    temporary.replace(path)
    try:
        load_script(path)
    except ScriptGateError as exc:  # pragma: no cover - the same text passed Gate 1
        raise ConfigError(f"distilled script {path.name} failed its final gate: {exc.message}") from None
    selectors = _script_selectors(source, recorder.steps)
    meta = write_meta(data_dir, BrowserSourceMeta(
        source_id=request.source_id, script=path.name, page_url=request.page_url,
        items=len(output.items), selectors=selectors, steps=len(recorder.steps),
        trial_seconds=seconds, model=request.pi.model, reasoning_level=request.pi.reasoning_level,
        trajectory=str(recorder.path), distilled_at=datetime.now(timezone.utc)))
    log.info("browser: distilled %s from %d trajectory step(s) into %s",
             request.source_id, len(recorder.steps), path)
    return DistilledScript(path=path, trajectory=recorder.path, output=output, source=source,
                           trial_seconds=seconds, selectors=selectors, steps=len(recorder.steps),
                           meta=meta)


def _script_selectors(source: str, steps: Sequence[TrajectoryStep]) -> list[str]:
    """The observed selector candidates the fixed script actually kept."""
    kept: list[str] = []
    for step in steps:
        candidates = step.decision_context.get("candidate_selectors")
        if not isinstance(candidates, list):
            continue
        for selector in candidates:
            if isinstance(selector, str) and selector in source and selector not in kept:
                kept.append(selector)
    return kept[:MAX_CANDIDATE_SELECTORS]


EXPLORATION_RULES = """\
You are exploring one web page so that a fixed replay script can be written for it.
You have exactly these tools: browser_navigate, browser_snapshot, browser_click,
browser_fill, browser_scroll, browser_evaluate, browser_submit_result.
Rules:
- Start with browser_navigate to the entry page, then inspect it with browser_snapshot.
- browser_evaluate is how you read many entries at once: return plain JSON.
- Only read what the task asks for. Never sign in as someone else, never submit a form
  that changes an account, and never open a local file.
- Every tool call takes a `rationale` sentence saying why that action is needed.
- Finish by calling browser_submit_result once with the entries you found
  (title, url, author, summary, published, comments_url) and then stop.
- You cannot run shell commands, write files or start other programs."""


def _exploration_prompt(request: DistillRequest) -> str:
    return _bounded("\n".join([
        EXPLORATION_RULES,
        "",
        f"Source id: {request.source_id}",
        f"Entry page: {request.page_url}",
        f"Collect at most {request.max_items} entries.",
        "",
        "Collection task:",
        request.task.strip(),
    ]))


DISTILL_RULES = """\
You are writing the fixed Playwright script that replaces your exploration run.
Return exactly one ```python block and nothing else.

The script must define exactly this entry point:

    async def extract(page: Page, *, page_url: str, max_items: int,
                      timeout_ms: int = 30_000) -> BrowserExtractOutput

Its only allowed imports are:

    from playwright.async_api import Page
    from tentacles.sources.browser_contract import BrowserExtractOutput, RawBrowserItem

A static gate rejects the script before it runs, so also obey:
- module level holds only imports, constants and definitions - no calls or statements;
- no class, no lambda value tricks, no `eval`, `exec`, `open`, `getattr`, `setattr`,
  no `__dunder__` names, no `format`/`format_map`, no raw CDP session, no `file:` URL;
- every name you read must be one you bound yourself or one of these builtins:
  abs all any bool chr dict divmod enumerate float frozenset int isinstance len list
  max min ord print range repr reversed round set sorted str sum tuple zip, plus
  AssertionError AttributeError Exception IndexError KeyError RuntimeError
  StopIteration TimeoutError TypeError ValueError.

Make it a reliable replay, not a transcript of one lucky run:
- navigate with `page_url` and the given `timeout_ms`, then wait for the structural
  container you saw in the trajectory before reading it;
- return `BrowserExtractOutput(page_url=<the page you read>, items=[RawBrowserItem(...)])`
  with at most `max_items` entries, each with a non-empty title and a link that is
  absolute or relative to the entry page;
- `published` must be ISO 8601 when the page carries a timestamp, otherwise omit it;
- skip promoted, placeholder and skeleton nodes; never hard-code today's date, a
  fixed item count or a one-off hashed class name."""


def _distillation_prompt(request: DistillRequest, steps: Sequence[TrajectoryStep],
                         samples: Sequence[Mapping[str, Any]]) -> str:
    return _bounded("\n".join([
        DISTILL_RULES,
        "",
        f"Source id: {request.source_id}",
        f"Entry page: {request.page_url}",
        f"Item cap: {request.max_items}",
        "",
        "Collection task:",
        request.task.strip(),
        "",
        "Recorded trajectory (page context, action and observation per step):",
        _steps_json(steps),
        "",
        "Entries the exploration submitted:",
        json.dumps(list(samples), ensure_ascii=False, default=str),
    ]))


def _correction_prompt(problem: str, snapshot: Mapping[str, Any] | None) -> str:
    return _bounded("\n".join([
        DISTILL_RULES,
        "",
        "The script you just produced did not pass the verification gate.",
        f"Failure: {clip(problem, 1500)}",
        "Page state where the trial run stopped:",
        json.dumps(dict(snapshot or {}), ensure_ascii=False, default=str),
        "",
        "Fix the script and answer again with one complete ```python block.",
    ]))


def _steps_json(steps: Sequence[TrajectoryStep]) -> str:
    """The trajectory as the distillation turn reads it: bounded, ordered, no bodies."""
    window = list(steps)[-MAX_PROMPT_STEPS:]
    return json.dumps([step.as_dict() for step in window], ensure_ascii=False, default=str)


def _bounded(prompt: str) -> str:
    return prompt if len(prompt) <= MAX_PROMPT_CHARS else prompt[: MAX_PROMPT_CHARS - 1] + "…"


def _python_block(reply: str) -> str | None:
    """The last fenced block of the answer: the model's script, nothing around it."""
    marker = reply.rfind("```")
    if marker < 0:
        return None
    opening = reply.rfind("```", 0, marker)
    if opening < 0:
        return None
    block = reply[opening + 3:marker]
    newline = block.find("\n")
    if newline < 0:
        return None
    language, body = block[:newline].strip().lower(), block[newline + 1:]
    if language not in {"", "python", "py"} or not body.strip():
        return None
    return body.strip("\n") + "\n"
