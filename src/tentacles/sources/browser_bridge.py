"""Local tool bridge between a controlled `pi` process and the work tab.

The Agent never receives a shell, a filesystem or a Playwright handle: it gets
the tools the shipped extension (`browser_bridge.js`) registers, and each call
arrives here as one JSON request on a loopback endpoint. This module executes
that action on the single work tab, answers with the observation, and appends the
step to the run's trajectory — the decision context it acted on first, then what
the page looked like afterwards.

The bridge is per-run: one run's token, one run's tab, one run's trajectory.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import secrets
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx
from playwright.async_api import Page

from tentacles.diagnostics import clip, redact_url
from tentacles.errors import ConfigError
from tentacles.sources.browser_contract import BrowserExtractOutput
from tentacles.sources.browser_session import page_snapshot
from tentacles.sources.trajectory import MAX_CANDIDATE_SELECTORS, TrajectoryRecorder, digest

log = logging.getLogger(__name__)

TOKEN_HEADER = "x-tentacles-bridge"
TOOL_PATH = "/tool"
TOOLS = frozenset({"navigate", "snapshot", "click", "fill", "scroll", "evaluate", "submit_result"})
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESULT_CHARS = 6000
REQUEST_TIMEOUT_S = 30.0
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found",
            405: "Method Not Allowed", 500: "Internal Server Error"}
# Selector hints for the decision context: the structural candidates a page
# actually offers, ordered by how often they occur. Read once per step.
_CANDIDATES_JS = """
() => {
  const seen = new Map();
  const attributes = ["slot", "role", "data-testid", "itemprop", "aria-label"];
  const walk = (node, depth) => {
    if (!node || depth > 6) return;
    for (const element of node.children) {
      const tag = element.tagName.toLowerCase();
      let selector = tag;
      for (const name of attributes) {
        const value = element.getAttribute(name);
        if (value) {
          selector = `${tag}[${name}="${value.slice(0, 32)}"]`;
          break;
        }
      }
      if (selector === tag && element.classList.length) selector = `${tag}.${element.classList[0]}`;
      seen.set(selector, (seen.get(selector) || 0) + 1);
      walk(element, depth + 1);
    }
  };
  walk(document.body, 0);
  return Array.from(seen.entries()).sort((left, right) => right[1] - left[1]).slice(0, 24).map((entry) => entry[0]);
}
"""


class BrowserBridge:
    """Executes one Agent tool call at a time on the run's work tab."""

    def __init__(self, recorder: TrajectoryRecorder, *, page_url: str, max_items: int,
                 timeout_ms: int) -> None:
        self._recorder = recorder
        self._entry_url = page_url
        self._max_items = max_items
        self._timeout_ms = timeout_ms
        self.page: Page | None = None
        self.exploration: BrowserExtractOutput | None = None

    def attach(self, page: Page) -> None:
        self.page = page

    def detach(self) -> None:
        self.page = None

    async def run(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Run one tool call and record it; a failing action is an observation too."""
        tool = payload.get("tool")
        if not isinstance(tool, str) or tool not in TOOLS:
            return {"ok": False, "error": f"unknown browser tool {tool!r}"}
        raw_args = payload.get("args")
        args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        rationale = args.pop("rationale", "")
        page = self.page
        if page is None:
            return {"ok": False, "error": "no work tab is attached to the bridge"}
        context = await self._context(page)
        matched: int | None = None
        data: dict[str, Any] = {}
        error: str | None = None
        try:
            data, matched = await self._act(page, tool, args)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a page that refuses a selector is a normal observation
            error = f"{type(exc).__name__}: {clip(str(exc), 300)}"
        observation: dict[str, Any] = {
            "ok": error is None, "url_after": redact_url(_current_url(page)),
            "page_title": context["page_title"], "matched_count": matched,
        }
        if error is not None:
            observation["error"] = error
        self._recorder.record(tool=tool, args=args, rationale=str(rationale or ""),
                              context=context, observation=observation)
        result: dict[str, Any] = {"ok": error is None, "observation": observation, "data": data}
        if error is not None:
            result["error"] = error
        return result

    async def _act(self, page: Page, tool: str, args: Mapping[str, Any]) -> tuple[dict[str, Any], int | None]:
        """Execute one tool call and report how much it matched."""
        if tool == "navigate":
            url = _web_url(args.get("url"))
            response = await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            return {"url": redact_url(_current_url(page)),
                    "status": response.status if response is not None else None}, None
        if tool == "snapshot":
            snapshot = await page_snapshot(page)
            candidates = await self._candidates(page)
            return {"a11y_snapshot": digest(str(snapshot.get("a11y_snapshot", ""))),
                    "candidate_selectors": candidates}, len(candidates)
        if tool == "click":
            locator = _locator(page, args)
            matched = await locator.count()
            await locator.first.click(timeout=self._timeout_ms)
            return {"selector": args.get("selector")}, matched
        if tool == "fill":
            locator = _locator(page, args)
            matched = await locator.count()
            await locator.first.fill(str(args.get("value") or ""), timeout=self._timeout_ms)
            if bool(args.get("submit")):
                await page.keyboard.press("Enter")
            return {"selector": args.get("selector"), "submitted": bool(args.get("submit"))}, matched
        if tool == "scroll":
            delta = _scroll_delta(args)
            await page.mouse.wheel(0, delta)
            return {"scroll_y": await page.evaluate("() => window.scrollY")}, None
        if tool == "evaluate":
            code = args.get("code")
            if not isinstance(code, str) or not code.strip():
                raise ValueError("evaluate needs a JavaScript expression in `code`")
            value = await page.evaluate(code)
            return {"result": _bounded(value)}, _matched_count(value)
        if tool == "submit_result":
            output = _exploration_output(args, fallback_url=self._entry_url)
            self.exploration = output
            return {"received": len(output.items), "page_url": output.page_url}, len(output.items)
        raise ValueError(f"unsupported browser tool {tool!r}")

    async def _context(self, page: Page) -> dict[str, Any]:
        """What the Agent knew when it acted: address, title, A11y digest, selectors."""
        snapshot = await page_snapshot(page)
        return {
            "page_url": redact_url(str(snapshot.get("current_url", ""))),
            "page_title": clip(str(snapshot.get("page_title", "")), 200),
            "a11y_snapshot_digest": digest(str(snapshot.get("a11y_snapshot", ""))),
            "candidate_selectors": await self._candidates(page),
        }

    async def _candidates(self, page: Page) -> list[str]:
        try:
            found = await page.evaluate(_CANDIDATES_JS)
        except Exception as exc:
            log.debug("browser bridge: the page offered no selector candidates (%s)", type(exc).__name__)
            return []
        if not isinstance(found, list):
            return []
        return [clip(str(item), 80) for item in found if isinstance(item, str)][:MAX_CANDIDATE_SELECTORS]


class BridgeServer:
    """One run's loopback JSON endpoint for the shipped pi extension."""

    def __init__(self, bridge: BrowserBridge, endpoint: str, *, token: str) -> None:
        self._bridge = bridge
        self._endpoint = endpoint
        self._token = token
        self._server: asyncio.AbstractServer | None = None
        self._url = ""

    @classmethod
    def open(cls, bridge: BrowserBridge, endpoint: str) -> BridgeServer:
        return cls(bridge, endpoint, token=secrets.token_urlsafe(24))

    @property
    def url(self) -> str:
        return self._url

    def child_env(self) -> dict[str, str]:
        """The two variables the extension reads; the token is per run."""
        return {"TENTACLES_BROWSER_BRIDGE_URL": self._url,
                "TENTACLES_BROWSER_BRIDGE_TOKEN": self._token}

    async def __aenter__(self) -> BridgeServer:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def start(self) -> None:
        host, port = _loopback(self._endpoint)
        try:
            self._server = await asyncio.start_server(self._handle, host, port, limit=MAX_REQUEST_BYTES)
        except OSError as exc:
            raise ConfigError(
                f"cannot listen on {self._endpoint} for the browser bridge ({exc.strerror}); "
                "free that port or change browser.webbridge_url") from None
        self._url = f"http://{_url_host(host)}:{port}{TOOL_PATH}"

    async def aclose(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(_read_request(reader), REQUEST_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                ValueError, ConnectionError):
            await _respond(writer, 400, {"ok": False, "error": "malformed bridge request"})
            return
        method, target, headers, body = request
        try:
            status, payload = await self._dispatch(method, target, headers, body)
        except Exception as exc:
            log.warning("browser bridge: tool call failed (%s)", type(exc).__name__)
            status, payload = 500, {"ok": False, "error": f"{type(exc).__name__}: {clip(str(exc), 200)}"}
        await _respond(writer, status, payload)

    async def _dispatch(self, method: str, target: str, headers: Mapping[str, str],
                        body: bytes) -> tuple[int, dict[str, Any]]:
        if target.split("?", 1)[0] != TOOL_PATH:
            return 404, {"ok": False, "error": f"unknown bridge path {target!r}"}
        if method != "POST":
            return 405, {"ok": False, "error": "the bridge accepts POST only"}
        if not hmac.compare_digest(headers.get(TOKEN_HEADER, ""), self._token):
            return 401, {"ok": False, "error": "bad bridge token"}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return 400, {"ok": False, "error": "the bridge body is not JSON"}
        if not isinstance(payload, Mapping):
            return 400, {"ok": False, "error": "the bridge body must be a JSON object"}
        return 200, await self._bridge.run(payload)


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes]:
    """Read one HTTP/1.1 request: head, then exactly `content-length` bytes."""
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    request = lines[0].split(" ")
    if len(request) < 2:
        raise ValueError("malformed request line")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    try:
        size = int(headers.get("content-length", "0"))
    except ValueError:
        raise ValueError("malformed content-length") from None
    if size < 0 or size > MAX_REQUEST_BYTES:
        raise ValueError("bridge body too large")
    body = await reader.readexactly(size) if size else b""
    return request[0], request[1], headers, body


async def _respond(writer: asyncio.StreamWriter, status: int, payload: Mapping[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    head = (f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}\r\n"
            "content-type: application/json; charset=utf-8\r\n"
            f"content-length: {len(body)}\r\n"
            "connection: close\r\n\r\n").encode("latin-1")
    with contextlib.suppress(OSError, ConnectionError):
        writer.write(head + body)
        await writer.drain()
    with contextlib.suppress(OSError, ConnectionError):
        writer.close()
        await writer.wait_closed()


def _loopback(endpoint: str) -> tuple[str, int]:
    """The bridge is a local control channel: loopback host, explicit port."""
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ConfigError("browser.webbridge_url must be an http endpoint such as http://127.0.0.1:10086")
    if parsed.hostname not in LOOPBACK_HOSTS:
        raise ConfigError(
            f"browser.webbridge_url must be a loopback address ({parsed.hostname!r} is not this host)")
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        raise ConfigError("browser.webbridge_url must carry an explicit port")
    return parsed.hostname, port


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _web_url(value: Any) -> str:
    """Only a real web address: the bridge never navigates to a local file."""
    text = str(value or "").strip()
    try:
        url = httpx.URL(text)
    except httpx.InvalidURL:
        raise ValueError("navigate needs an http(s) URL in `url`") from None
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        raise ValueError("navigate needs an http(s) URL in `url`")
    return str(url)


def _locator(page: Page, args: Mapping[str, Any]) -> Any:
    selector = args.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("this tool needs a CSS selector in `selector`")
    return page.locator(selector.strip())


def _scroll_delta(args: Mapping[str, Any]) -> int:
    """`amount` in pixels, signed by `direction`; bounded so one step stays small."""
    amount = args.get("amount", 800)
    try:
        pixels = int(amount)
    except (TypeError, ValueError):
        pixels = 800
    pixels = max(50, min(abs(pixels), 5000))
    return -pixels if str(args.get("direction") or "down").lower() == "up" else pixels


def _bounded(value: Any) -> Any:
    """Keep a page-evaluated value only if its JSON form fits the model's budget."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return clip(str(value), MAX_RESULT_CHARS)
    if len(text) <= MAX_RESULT_CHARS:
        return value
    return clip(text, MAX_RESULT_CHARS)


def _matched_count(value: Any) -> int | None:
    return len(value) if isinstance(value, (list, dict)) else None


def _exploration_output(args: Mapping[str, Any], *, fallback_url: str) -> BrowserExtractOutput:
    """Validate what the Agent claims it extracted as the run's sample entries."""
    items = args.get("items")
    if not isinstance(items, list):
        raise ValueError("submit_result needs an `items` list")
    page_url = args.get("page_url")
    entry = page_url.strip() if isinstance(page_url, str) and page_url.strip() else fallback_url
    return BrowserExtractOutput.model_validate({"page_url": entry, "items": items})


def _current_url(page: Page) -> str:
    try:
        return page.url
    except Exception:
        return ""
