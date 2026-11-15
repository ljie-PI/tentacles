"""`type: browser`: replay a fixed Playwright script in your headed Chrome.

The script lives in the repository (`browser.scripts_dir`), passes a static AST
gate, and only ever sees one isolated work tab in the configured Chrome profile.
Whatever happens, that tab is reclaimed and every failure site lands in the
`run_errors` ledger under `phase='browser_replay'`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Type, cast
from urllib.parse import urljoin

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tentacles.asyncutil import run_store_call
from tentacles.config import AppConfig, ReasoningLevel, chrome_profile_name, reject_headless
from tentacles.diagnostics import DiagnosticEvent, stack_frames
from tentacles.errors import ConfigError, UpstreamError
from tentacles.models import Item, ItemObservation, LinkDisposition, normalize_url
from tentacles.sources.base import FetchContext, FetchResult, Source, register
from tentacles.sources.browser_contract import (
    BrowserExtractOutput, ExtractScript, RawBrowserItem, ScriptGateError,
)
from tentacles.sources.browser_script import load_script
from tentacles.sources.browser_session import page_snapshot, work_tab
from tentacles.text import truncate
from tentacles.timeutil import to_utc

log = logging.getLogger(__name__)


class BrowserPiOptions(BaseModel):
    """Source-level overrides for the exploration agent of #82 (validated only)."""

    model_config = ConfigDict(extra='forbid')

    binary: str | None = None
    model: str | None = None
    reasoning_level: ReasoningLevel | None = None
    timeout: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class BrowserOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    page_url: str
    task: str = Field(default='', max_length=4000)
    chrome_profile: str | None = None
    on_detail_error: Literal['fail_batch', 'record_and_skip'] = 'record_and_skip'
    script_path: str | None = None
    max_items: int = Field(default=25, ge=1, le=200)
    timeout: float = Field(default=60, gt=0, allow_inf_nan=False)
    # Declared only so `headless: true` fails with a clear message, never silently.
    headless: bool = False
    pi: BrowserPiOptions | None = None

    _headed_only = field_validator('headless')(reject_headless)

    @field_validator('page_url')
    @classmethod
    def _url(cls, value: str) -> str:
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            raise ValueError('must be an http(s) URL without credentials or controls') from None
        if (url.scheme not in {'http', 'https'} or not url.host or url.userinfo or
                any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError('must be an http(s) URL without credentials or controls')
        return str(url)

    @field_validator('chrome_profile')
    @classmethod
    def _profile(cls, value: str | None) -> str | None:
        return chrome_profile_name(value) if value is not None else None


@register
class BrowserSource(Source):
    """Headed Chrome replay of `<browser.scripts_dir>/<source_id>.py`."""

    type: ClassVar[str] = 'browser'
    Options: ClassVar[Type[BaseModel]] = BrowserOptions
    supports_updates: ClassVar[bool] = True

    async def fetch(self, cursor: dict[str, Any], ctx: FetchContext) -> FetchResult:
        opts = cast(BrowserOptions, self.options)
        config = ctx.config
        if config is None:
            raise ConfigError('browser sources need the loaded application config (the `browser` block)')
        try:
            script = _script_path(config, self.spec.id, opts)
            extract = load_script(script)
        except ScriptGateError as exc:
            return self._failed(opts, ctx, script=script, exc=exc, snapshot={})
        try:
            output, snapshot = await self._replay(extract, opts, config)
        except _ReplayFailed as failure:
            return self._failed(opts, ctx, script=script, exc=failure.error, snapshot=failure.snapshot,
                                timeout=failure.timeout)
        raw_items = output.items[:opts.max_items]
        if not raw_items:
            self._record(ctx, opts, error_code='script_assertion_failed', severity='warning',
                         message=f'replay script {script.name} returned no items',
                         target_url=output.page_url, snapshot=snapshot)
            return FetchResult([], _cursor(opts, script, 0), {'entries': 0, 'no_items': True})
        return await self._items(raw_items, output, opts, ctx, snapshot, cursor, script)

    async def _replay(self, extract: ExtractScript, opts: BrowserOptions,
                      config: AppConfig) -> tuple[BrowserExtractOutput, dict[str, Any]]:
        """Run the script against one isolated work tab, keeping its failure site."""
        async with work_tab(config, chrome_profile=opts.chrome_profile) as page:
            try:
                output = await asyncio.wait_for(
                    extract(page, page_url=opts.page_url, max_items=opts.max_items,
                            timeout_ms=int(opts.timeout * 1000)), timeout=opts.timeout)
            except (PlaywrightTimeoutError, asyncio.TimeoutError) as exc:
                raise _ReplayFailed(exc, await page_snapshot(page), timeout=True) from None
            except Exception as exc:
                raise _ReplayFailed(exc, await page_snapshot(page)) from None
            try:
                return BrowserExtractOutput.model_validate(output), await page_snapshot(page)
            except Exception as exc:
                raise _ReplayFailed(exc, await page_snapshot(page)) from None

    async def _items(self, raw_items: list[RawBrowserItem], output: BrowserExtractOutput,
                     opts: BrowserOptions, ctx: FetchContext, snapshot: dict[str, Any],
                     cursor: dict[str, Any], script: Path) -> FetchResult:
        """Turn raw entries into Items, then drop what this source already stored."""
        items, seen, duplicates = [], set(), 0
        for raw in raw_items:
            item = _item(self.spec.id, output.page_url, raw, script)
            if item.external_id in seen:
                duplicates += 1
                continue
            seen.add(item.external_id)
            items.append(item)
        kept, links = await self._classify(items, ctx)
        stats: dict[str, Any] = {'entries': len(raw_items), 'duplicates': duplicates,
                                 'skipped_known': len(items) - len(kept)}
        if self.spec.update_existing:
            stats['refresh_links'] = sum(1 for item in kept if links.get(item.url) == 'refresh')
        observations = [_observation(item) for item in kept] if self.spec.update_existing else []
        return FetchResult(kept, _cursor(opts, script, len(kept)), stats, observations)

    async def _classify(self, items: list[Item], ctx: FetchContext) -> tuple[list[Item], dict[str, LinkDisposition]]:
        """Skip entries this source already holds unless an in-place update is allowed."""
        urls = [item.url for item in items if item.url]
        if not urls:
            return items, {}
        if self.spec.update_existing and ctx.classify_links is not None:
            links = await run_store_call(ctx.classify_links, urls)
            return [item for item in items if links.get(item.url, 'new') != 'skip'], links
        seen = await run_store_call(ctx.seen_links, urls) if ctx.seen_links is not None else set()
        return [item for item in items if item.url not in seen], {url: 'skip' for url in seen}

    def _failed(self, opts: BrowserOptions, ctx: FetchContext, *, script: Path, exc: BaseException,
                snapshot: dict[str, Any], timeout: bool = False) -> FetchResult:
        """Record the replay failure; `on_detail_error` decides the batch outcome."""
        if isinstance(exc, ScriptGateError):
            message = exc.message
        else:
            message = (f'browser replay timed out ({type(exc).__name__})' if timeout
                       else f'browser replay script failed ({type(exc).__name__})')
        self._record(ctx, opts, error_code='playwright_selector_timeout' if timeout else 'script_assertion_failed',
                     message=message, exc=exc, snapshot=snapshot,
                     target_url=_failed_target(opts, snapshot))
        if opts.on_detail_error == 'fail_batch':
            raise UpstreamError(f'browser replay failed for {script.name} ({type(exc).__name__})')
        return FetchResult([], _cursor(opts, script, 0), {'entries': 0, 'replay_failed': True})

    def _record(self, ctx: FetchContext, opts: BrowserOptions, *, error_code: str, message: str,
                snapshot: dict[str, Any], target_url: str, exc: BaseException | None = None,
                severity: str = 'error') -> None:
        if ctx.record_error is None:
            return
        context: dict[str, Any] = {'page_url': opts.page_url, **snapshot}
        if exc is not None:
            context['traceback'] = stack_frames(exc)
        ctx.record_error(DiagnosticEvent(
            phase='browser_replay', error_code=error_code, message=message, severity=severity,
            source_id=self.spec.id, page_url=opts.page_url, target_url=target_url,
            exception_type=type(exc).__name__ if exc is not None else None, retryable=False,
            debug_context=context))


class _ReplayFailed(Exception):
    """Internal carrier: the failing page state captured before its tab closed."""

    def __init__(self, error: BaseException, snapshot: dict[str, Any], *, timeout: bool = False) -> None:
        super().__init__(str(error))
        self.error, self.snapshot, self.timeout = error, snapshot, timeout


def _item(source_id: str, page_url: str, raw: RawBrowserItem, script: Path) -> Item:
    url = _entry_url(page_url, raw.url)
    content = raw.content or truncate(raw.summary, 2000)
    meta: dict[str, Any] = {'browser_script': script.name, 'browser_entry_url': page_url}
    if raw.comments_url and (comments := _entry_url(page_url, raw.comments_url)):
        meta['comments_url'] = comments
    return Item(source_id=source_id, kind='browser', url=url, page_url=page_url, title=raw.title,
                author=raw.author, summary=truncate(raw.summary or content, 2000), content=content,
                published=_published(raw.published), meta=meta)


def _observation(item: Item) -> ItemObservation:
    return ItemObservation(stable_external_id=True, title=item.title, url=item.url,
                           author=item.author or None, summary=item.summary, content=item.content,
                           published=item.published, meta=item.meta)


def _entry_url(page_url: str, value: str | None) -> str:
    """Resolve a script-provided link against the entry page; non-http(s) is unusable."""
    if not value or not value.strip():
        return ''
    try:
        url = httpx.URL(urljoin(page_url, value.strip()))
    except (ValueError, httpx.InvalidURL):
        return ''
    if url.scheme not in {'http', 'https'} or not url.host or url.userinfo:
        return ''
    return normalize_url(str(url))


def _published(value: str | None) -> datetime | None:
    """ISO 8601 only: a fixed script resolves relative dates, not the runtime."""
    if not value or not value.strip():
        return None
    try:
        return to_utc(datetime.fromisoformat(value.strip().replace('Z', '+00:00')))
    except ValueError:
        return None


def _script_path(config: AppConfig, source_id: str, opts: BrowserOptions) -> Path:
    """`script_path` when configured, else `<scripts_dir>/<source_id>.py`; never outside."""
    scripts_dir = config.browser_scripts_dir
    if opts.script_path is None:
        return scripts_dir / f'{source_id}.py'
    path = config.resolve(Path(opts.script_path))
    if path.suffix != '.py' or not path.is_relative_to(scripts_dir):
        raise ConfigError(f'replay script must be a .py file inside {scripts_dir}')
    return path


def _failed_target(opts: BrowserOptions, snapshot: dict[str, Any]) -> str:
    """Point the ledger at the page that failed, not just the configured entry."""
    current = snapshot.get('current_url')
    if isinstance(current, str) and current.startswith(('http://', 'https://')):
        return current
    return opts.page_url


def _cursor(opts: BrowserOptions, script: Path, count: int) -> dict[str, Any]:
    return {'page_url': opts.page_url, 'script': script.name, 'items': count}
