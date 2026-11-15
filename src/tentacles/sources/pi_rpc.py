"""Controlled `pi --mode rpc` subprocess for browser exploration and distillation.

Pi is started as a long-lived child process speaking one JSON record per line on
stdin/stdout. This client owns that child's whole policy: an ephemeral session
that discovers nothing (no extensions, MCP servers, skills, prompt templates or
context files), no built-in tools, and the configured model and thinking level
pinned on the command line. The only tools the Agent can call are the ones the
shipped bridge extension registers, so every browser action passes this process.

Usage reported by the provider is forwarded as it arrives, so the tokens and the
cost of an exploration land on the Run that started it. Cancellation and
timeouts always take the child down: no pi process outlives its fetch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from tentacles.config import BrowserPiSpec
from tentacles.errors import UpstreamError
from tentacles.llm.types import TokenUsage

log = logging.getLogger(__name__)

BRIDGE_URL_ENV = "TENTACLES_BROWSER_BRIDGE_URL"
BRIDGE_TOKEN_ENV = "TENTACLES_BROWSER_BRIDGE_TOKEN"
STDERR_TAIL_CHARS = 2000
SHUTDOWN_TIMEOUT_S = 10.0
# One protocol record can carry a whole assistant message or tool result; the
# default 64 KiB stream limit would tear such a line apart mid-JSON.
MAX_RECORD_BYTES = 1 << 20
DIALOG_METHODS = frozenset({"select", "confirm", "input", "editor"})


class PiRpcError(UpstreamError):
    """The controlled pi subprocess failed, timed out or answered with an error."""


@dataclass
class PiUsage:
    """One assistant message's provider-reported usage (`UsageRecord` shape)."""

    model: str
    usage: TokenUsage
    cost: float | None


def rpc_argv(spec: BrowserPiSpec, extension: Path) -> list[str]:
    """The only command line this client runs: minimal privilege, model pinned."""
    return [
        spec.binary, "--mode", "rpc", "--no-session", "--no-context-files",
        "--no-extensions", "--no-mcp", "--no-skills", "--no-prompt-templates",
        "--no-builtin-tools", "--model", spec.model, "--thinking", spec.reasoning_level,
        "--extension", str(extension),
    ]


class PiRpcClient:
    """One minimal-privilege pi RPC session, from spawn to forced exit.

    One session serves both turns of a cold start (exploration, then
    distillation with at most one correction), so the corrective turn keeps the
    context it needs and the usage of both turns bills the same Run.
    """

    def __init__(self, spec: BrowserPiSpec, extension: Path, *, cwd: Path,
                 env: Mapping[str, str] | None = None,
                 on_usage: Callable[[PiUsage], None] | None = None) -> None:
        self._spec = spec
        self._argv = rpc_argv(spec, extension)
        self._cwd = cwd
        # The child inherits this process's environment (PATH, provider credentials)
        # plus the two run-scoped bridge variables.
        self._env = {**os.environ, **(env or {})}
        self._on_usage = on_usage
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._settled = asyncio.Event()
        self._settled.set()
        self._aborted = False
        self._stderr: list[str] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._last_text = ""
        self._model_error: str | None = None
        self._closed = False

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    @property
    def last_text(self) -> str:
        """The text blocks of the most recent completed assistant message."""
        return self._last_text

    @property
    def stderr_tail(self) -> str:
        return "".join(self._stderr)[-STDERR_TAIL_CHARS:]

    async def __aenter__(self) -> PiRpcClient:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=str(self._cwd), env=self._env,
                limit=MAX_RECORD_BYTES)
        except OSError as exc:
            raise PiRpcError(
                f"cannot start the exploration agent {self._argv[0]!r} ({type(exc).__name__}); "
                "install pi, or set browser.pi.binary (or the per-source pi.binary)") from None
        self._tasks = [asyncio.create_task(self._read_stdout()), asyncio.create_task(self._read_stderr())]

    async def ask(self, message: str, *, timeout: float | None = None) -> str:
        """Send one prompt, wait until pi has nothing left to do, return its text."""
        if self._process is None:
            raise PiRpcError("the pi rpc session is not running")
        deadline = timeout or self._spec.timeout
        self._settled.clear()
        self._aborted = False
        self._model_error = None
        await self.request({"type": "prompt", "message": message}, timeout=deadline)
        try:
            await asyncio.wait_for(self._settled.wait(), deadline)
        except asyncio.TimeoutError:
            await self._abort()
            raise PiRpcError(
                f"the pi exploration turn did not settle within {deadline:g}s") from None
        if self._aborted:
            raise PiRpcError("the pi exploration turn was aborted")
        if self._model_error is not None:
            raise PiRpcError(f"the model call failed: {self._model_error}")
        return self._last_text

    async def request(self, command: Mapping[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        """Send one command and return its response data, or raise `PiRpcError`."""
        if self._process is None or self._closed:
            raise PiRpcError("the pi rpc session is not running")
        request_id = uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, **command})
            return await asyncio.wait_for(future, timeout or self._spec.timeout)
        except asyncio.TimeoutError:
            raise PiRpcError(
                f"pi did not answer {command.get('type')!r} within {timeout or self._spec.timeout:g}s") from None
        finally:
            self._pending.pop(request_id, None)

    async def aclose(self) -> None:
        """Shut the child down; stdin close first, signals only if it lingers."""
        process, self._process = self._process, None
        self._closed = True
        if process is None:
            self._cancel_tasks()
            return
        self._fail_pending(PiRpcError("the pi rpc session was closed"))
        if process.stdin is not None and not process.stdin.is_closing():
            with contextlib.suppress(OSError, RuntimeError):
                process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), SHUTDOWN_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("browser: pi did not exit after stdin closed; terminating it")
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
            self._cancel_tasks()

    async def _abort(self) -> None:
        """Ask pi to stop the current run; a dead child needs no abort."""
        with contextlib.suppress(PiRpcError):
            await self.request({"type": "abort"}, timeout=min(self._spec.timeout, 30.0))

    async def _write(self, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise PiRpcError("the pi rpc session is not running")
        try:
            process.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            raise PiRpcError(f"the pi rpc session stopped accepting commands: {self.stderr_tail}") from None

    async def _read_stdout(self) -> None:
        """Dispatch one JSON record per line; LF only, never at other separators."""
        process = self._process
        assert process is not None and process.stdout is not None
        while line := await process.stdout.readline():
            text = line.rstrip(b"\r\n").decode("utf-8", "replace")
            if not text.strip():
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                log.warning("browser: pi wrote a non-JSON protocol line (%d chars)", len(text))
                continue
            if isinstance(record, dict):
                self._dispatch(record)
        # The child exited: nobody is going to answer an outstanding command.
        self._fail_pending(PiRpcError(f"the pi rpc session exited: {self.stderr_tail}"))

    async def _read_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        while line := await process.stderr.readline():
            text = line.decode("utf-8", "replace").rstrip("\n")
            if not text:
                continue
            self._stderr.append(text + "\n")
            if len(self._stderr) > 200:
                del self._stderr[:-200]
            log.debug("browser: pi stderr: %s", text)

    def _dispatch(self, record: dict[str, Any]) -> None:
        kind = record.get("type")
        if kind == "response":
            self._resolve(record)
        elif kind == "message_end":
            message = record.get("message")
            if isinstance(message, dict):
                self._message_end(message)
        elif kind == "agent_settled":
            self._aborted = bool(record.get("aborted"))
            self._settled.set()
        elif kind == "extension_ui_request":
            self._answer_dialog(record)

    def _resolve(self, record: dict[str, Any]) -> None:
        request_id = record.get("id")
        future = self._pending.get(request_id) if isinstance(request_id, str) else None
        if future is None or future.done():
            return
        if record.get("success"):
            data = record.get("data")
            future.set_result(dict(data) if isinstance(data, dict) else {})
        else:
            future.set_exception(PiRpcError(
                f"pi rejected {record.get('command')!r}: {record.get('error') or 'no reason given'}"))
            future.exception()  # mark retrieved; the awaiting caller still raises

    def _message_end(self, message: Mapping[str, Any]) -> None:
        if message.get("role") != "assistant":
            return
        self._last_text = _text_of(message.get("content"))
        if message.get("stopReason") == "error":
            # The reader must keep draining the stream: a failed turn still ends
            # with `agent_settled`, which is what the awaiting turn reacts to.
            reason = message.get("errorMessage")
            self._model_error = reason if isinstance(reason, str) and reason else "no reason given"
        self._record_usage(message)

    def _record_usage(self, message: Mapping[str, Any]) -> None:
        """Forward one assistant message's tokens and cost to the Run."""
        usage = message.get("usage")
        if not isinstance(usage, dict) or self._on_usage is None:
            return
        cost = usage.get("cost")
        total = cost.get("total") if isinstance(cost, dict) else None
        model = message.get("model")
        self._on_usage(PiUsage(
            model=model if isinstance(model, str) and model else self._spec.model,
            usage=TokenUsage(input_tokens=_count(usage.get("input")),
                             output_tokens=_count(usage.get("output")),
                             total_tokens=_count(usage.get("totalTokens"))),
            cost=float(total) if isinstance(total, (int, float)) else None))

    def _answer_dialog(self, record: Mapping[str, Any]) -> None:
        """An unattended session cancels dialogs; notifications need no answer."""
        if record.get("method") not in DIALOG_METHODS:
            return
        request_id = record.get("id")
        if not isinstance(request_id, str):
            return
        self._tasks.append(asyncio.create_task(self._write_quietly(
            {"type": "extension_ui_response", "id": request_id, "cancelled": True})))

    async def _write_quietly(self, payload: Mapping[str, Any]) -> None:
        with contextlib.suppress(PiRpcError):
            await self._write(payload)

    def _fail_pending(self, error: PiRpcError) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
                future.exception()
        self._pending.clear()

    def _cancel_tasks(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks = []


def _text_of(content: Any) -> str:
    """Concatenate the text blocks of one assistant message."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [block.get("text") for block in content
             if isinstance(block, dict) and block.get("type") == "text"
             and isinstance(block.get("text"), str)]
    return "\n".join(str(part) for part in parts)


def _count(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and value >= 0 else 0
