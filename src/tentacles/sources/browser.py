"""`type: browser`: replay a fixed Playwright script in your headed Chrome.

The script lives in the repository (`browser.scripts_dir`), passes a static AST
gate, and only ever sees one isolated work tab in the configured Chrome profile.
A source without a script yet is a cold start: the exploration Agent drives that
tab, its trajectory is recorded, and the distilled script has to pass the static
gate plus a standalone trial run before it is installed here. A script that no
longer replays (error, timeout or zero entries) wakes one healing session that
has to pass the same gates; only then is the old text replaced. Every installed
script is offered to the review branch of `script_pr`. Whatever happens, that
tab is reclaimed and every failure site lands in the `run_errors` ledger under
`browser_replay`, `browser_heal`, `browser_distill` or `script_pr`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Type, cast
from urllib.parse import urljoin

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tentacles.asyncutil import run_store_call
from tentacles.config import (
    AppConfig, BrowserPiSpec, ReasoningLevel, chrome_profile_name, reject_headless,
)
from tentacles.diagnostics import DiagnosticEvent, clip, configured_secrets, stack_frames
from tentacles.errors import ConfigError
from tentacles.models import Item, ItemObservation, LinkDisposition, normalize_url
from tentacles.sources.base import DiagnosedError, FetchContext, FetchResult, Source, register
from tentacles.sources.browser_contract import (
    BrowserExtractOutput, ExtractScript, RawBrowserItem, ScriptGateError,
)
from tentacles.sources.browser_heal import HealFailed, HealRequest, heal
from tentacles.sources.browser_list import (
    BrowserListSelectors, BrowserPrefilterOptions, BrowserScrollOptions, run_browser_list,
)
from tentacles.sources.browser_script import load_script
from tentacles.sources.browser_session import page_snapshot, work_tab
from tentacles.sources.distiller import DistillFailed, DistillRequest, distill
from tentacles.sources.script_pr import ScriptPublication, publish_script
from tentacles.sources.trajectory import meta_path
from tentacles.text import truncate
from tentacles.timeutil import to_utc

log = logging.getLogger(__name__)


class BrowserPiOptions(BaseModel):
    """Source-level overrides for the exploration agent of a cold start."""

    model_config = ConfigDict(extra='forbid')

    binary: str | None = None
    model: str | None = None
    reasoning_level: ReasoningLevel | None = None
    timeout: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class BrowserOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    page_url: str
    mode: Literal['script', 'list'] = 'script'
    task: str = Field(default='', max_length=4000)
    chrome_profile: str | None = None
    on_detail_error: Literal['fail_batch', 'record_and_skip'] = 'record_and_skip'
    script_path: str | None = None
    max_items: int = Field(default=25, ge=1, le=500)
    timeout: float = Field(default=60, gt=0, allow_inf_nan=False)
    scroll: BrowserScrollOptions = Field(default_factory=BrowserScrollOptions)
    selectors: BrowserListSelectors | None = None
    prefilter: BrowserPrefilterOptions | None = None
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
        if opts.mode == 'list':
            return await self._fetch_list(opts, config, ctx)
        script = _script_path(config, self.spec.id, opts)
        if ctx.distill or not script.exists():
            return await self._distill(script, opts, ctx, cursor)
        try:
            extract = load_script(script)
        except ScriptGateError as exc:
            # A rejected script never runs, so this configuration error has to fail
            # the source: degrading it to an empty batch would no-op every fetch.
            self._record(ctx, opts, error_code='script_assertion_failed', message=exc.message,
                         exc=exc, snapshot={}, target_url=opts.page_url)
            raise DiagnosedError(exc.message) from None
        try:
            output, snapshot = await self._replay(extract, opts, config)
        except _ReplayBroken as broken:
            self._record_replay(opts, ctx, broken.failure)
            return await self._heal(script, opts, ctx, cursor, broken.failure)
        raw_items = output.items[:opts.max_items]
        if not raw_items:
            # A page that yields nothing is a broken script too: the site may have
            # moved its container, so this round heals instead of silently no-oping.
            failure = _ReplayFailure(error=None, snapshot=snapshot, empty=True)
            self._record_replay(opts, ctx, failure, target_url=output.page_url)
            return await self._heal(script, opts, ctx, cursor, failure)
        return await self._items(raw_items, output, opts, ctx, cursor, script,
                                 extra_stats={'browser_mode': 'replay'})

    async def _distill(self, script: Path, opts: BrowserOptions, ctx: FetchContext,
                       cursor: dict[str, Any]) -> FetchResult:
        """Cold start: explore with pi, record the trajectory, gate and install a script."""
        config = cast(AppConfig, ctx.config)
        request = DistillRequest(
            source_id=self.spec.id, run_id=ctx.run_id, page_url=opts.page_url, task=opts.task,
            max_items=opts.max_items, timeout=opts.timeout, chrome_profile=opts.chrome_profile,
            script_path=script, pi=_pi_spec(config, opts))
        stats: dict[str, Any] = {'browser_mode': 'distill'}
        try:
            distilled = await distill(request, config=config, on_usage=ctx.metrics.record,
                                      secrets=configured_secrets(config))
        except DistillFailed as failure:
            stats['browser_distill_failed'] = True
            trajectory = failure.context.get('trajectory_path')
            if isinstance(trajectory, str) and trajectory:
                stats['trajectory_path'] = trajectory
            self._record(ctx, opts, phase='browser_distill', error_code=failure.error_code,
                         message=failure.message, exc=failure.error, snapshot=failure.context,
                         target_url=opts.page_url)
            if opts.on_detail_error == 'fail_batch':
                # The concrete failure is in the ledger; no generic whole-source row on top.
                raise DiagnosedError(
                    f'browser distill failed for {self.spec.id} ({failure.message})') from None
            # No script was installed, so the old cursor stays: this round fetched nothing.
            return FetchResult([], cursor, {'entries': 0, **stats})
        stats['trajectory_path'] = str(distilled.trajectory)
        stats['script_path'] = str(distilled.path)
        await self._publish_script(
            distilled.path, opts, ctx, stats, reason='distill',
            detail=(f'distilled from {distilled.steps} trajectory step(s); the standalone trial '
                    f'returned {len(distilled.output.items)} entries in {distilled.trial_seconds:.1f}s'))
        raw_items = distilled.output.items[:opts.max_items]
        return await self._items(raw_items, distilled.output, opts, ctx, cursor, distilled.path,
                                 extra_stats=stats)

    async def _replay(self, extract: ExtractScript, opts: BrowserOptions,
                      config: AppConfig) -> tuple[BrowserExtractOutput, dict[str, Any]]:
        """Run the script against one isolated work tab, keeping its failure site."""
        async with work_tab(config, chrome_profile=opts.chrome_profile) as page:
            try:
                output = await asyncio.wait_for(
                    extract(page, page_url=opts.page_url, max_items=opts.max_items,
                            timeout_ms=int(opts.timeout * 1000)), timeout=opts.timeout)
            except (PlaywrightTimeoutError, asyncio.TimeoutError) as exc:
                raise _ReplayBroken(_ReplayFailure(exc, await page_snapshot(page), timeout=True)) from None
            except Exception as exc:
                raise _ReplayBroken(_ReplayFailure(exc, await page_snapshot(page))) from None
            try:
                return BrowserExtractOutput.model_validate(output), await page_snapshot(page)
            except Exception as exc:
                raise _ReplayBroken(_ReplayFailure(exc, await page_snapshot(page))) from None

    async def _items(self, raw_items: list[RawBrowserItem], output: BrowserExtractOutput,
                     opts: BrowserOptions, ctx: FetchContext, cursor: dict[str, Any], script: Path,
                     *, extra_stats: dict[str, Any] | None = None) -> FetchResult:
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
        stats: dict[str, Any] = {**(extra_stats or {}), 'entries': len(raw_items),
                                 'duplicates': duplicates,
                                 'skipped_known': len(items) - len(kept)}
        if self.spec.update_existing:
            stats['refresh_links'] = sum(1 for item in kept if links.get(item.url) == 'refresh')
        observations = [_observation(item) for item in kept] if self.spec.update_existing else []
        return FetchResult(kept, _cursor(opts, script, len(kept)), stats, observations)

    async def _fetch_list(self, opts: BrowserOptions, config: AppConfig, ctx: FetchContext) -> FetchResult:
        """Scroll and extract items via CSS selectors + engagement prefilter without pi."""
        if ctx.distill:
            raise ConfigError('sources distill requires a browser source in script mode')
        if opts.selectors is None:
            raise ConfigError('browser list mode requires selectors')
        try:
            scan = await run_browser_list(
                config,
                source_id=self.spec.id,
                page_url=opts.page_url,
                chrome_profile=opts.chrome_profile,
                selectors=opts.selectors,
                scroll=opts.scroll,
                prefilter=opts.prefilter,
                max_items=opts.max_items,
                timeout=opts.timeout,
                ctx=ctx,
            )
        except (DiagnosedError, ConfigError):
            raise
        except Exception as exc:
            self._record(
                ctx,
                opts,
                phase='browser_replay',
                error_code='unexpected_exception',
                message=f'browser list failed ({type(exc).__name__})',
                exc=exc,
                snapshot={'browser_mode': 'list'},
                target_url=opts.page_url,
            )
            raise DiagnosedError(f'browser list failed ({type(exc).__name__})') from None

        kept, links = await self._classify(scan.items, ctx)
        stats: dict[str, Any] = {
            **scan.stats,
            'entries': len(scan.items),
            'duplicates': 0,
            'skipped_known': len(scan.items) - len(kept),
        }
        if self.spec.update_existing:
            stats['refresh_links'] = sum(1 for item in kept if links.get(item.url) == 'refresh')
        observations = [_observation(item) for item in kept] if self.spec.update_existing else []
        next_cursor = {'page_url': opts.page_url, 'mode': 'list', 'items': len(kept)}
        return FetchResult(kept, next_cursor, stats, observations)

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

    async def _heal(self, script: Path, opts: BrowserOptions, ctx: FetchContext,
                    cursor: dict[str, Any], failure: _ReplayFailure) -> FetchResult:
        """One healing session; a failed repair keeps the old script and fails the run."""
        config = cast(AppConfig, ctx.config)
        stats: dict[str, Any] = {'browser_mode': 'heal'}
        request = HealRequest(
            source_id=self.spec.id, run_id=ctx.run_id, page_url=opts.page_url, task=opts.task,
            max_items=opts.max_items, timeout=opts.timeout, chrome_profile=opts.chrome_profile,
            script_path=script, pi=_pi_spec(config, opts), old_source=_read_script(script),
            failure=failure.describe(), snapshot=failure.snapshot,
            meta=_local_meta(config, self.spec.id))
        try:
            healed = await heal(request, config=config, on_usage=ctx.metrics.record,
                                secrets=configured_secrets(config))
        except HealFailed as exc:
            stats['browser_heal_failed'] = True
            trajectory = exc.context.get('trajectory_path')
            if isinstance(trajectory, str) and trajectory:
                stats['trajectory_path'] = trajectory
            self._record(ctx, opts, phase='browser_heal', error_code='pi_heal_exhausted',
                         message=exc.message, exc=exc.error,
                         snapshot={**failure.snapshot, **exc.context},
                         target_url=_failed_target(opts, failure.snapshot))
            # A fixed script that no longer replays is not a per-item skip: the run
            # fails so the consumed pi cost and the ledger row stay visible.
            raise DiagnosedError(
                f'browser script self-healing failed for {self.spec.id} ({exc.message})') from None
        stats['trajectory_path'] = str(healed.trajectory)
        stats['script_path'] = str(healed.path)
        stats['heal_attempts'] = healed.attempts
        await self._publish_script(
            healed.path, opts, ctx, stats, reason='heal',
            detail=(f'repaired after {healed.attempts} trial attempt(s); the standalone trial '
                    f'returned {len(healed.output.items)} entries in {healed.trial_seconds:.1f}s'))
        raw_items = healed.output.items[:opts.max_items]
        return await self._items(raw_items, healed.output, opts, ctx, cursor, healed.path,
                                 extra_stats=stats)

    async def _publish_script(self, script: Path, opts: BrowserOptions, ctx: FetchContext,
                              stats: dict[str, Any], *, reason: str, detail: str) -> None:
        """Offer the installed script as a review PR; a failure only degrades the round."""
        config = cast(AppConfig, ctx.config)
        publication: ScriptPublication = await publish_script(
            script, source_id=self.spec.id, spec=config.browser.pr, reason=reason,
            detail=detail, secrets=configured_secrets(config))
        stats['pr_status'] = publication.status
        if publication.branch:
            stats['pr_branch'] = publication.branch
        if publication.pr_url:
            stats['pr_url'] = publication.pr_url
        if publication.status != 'error':
            return
        self._record(ctx, opts, phase='script_pr', error_code='git_or_pr_failed', severity='warning',
                     message=f'cannot publish the {reason} script of {self.spec.id!r}: {publication.detail}',
                     snapshot={'pr_branch': publication.branch, 'pr_status': publication.status},
                     target_url=opts.page_url)

    def _record_replay(self, opts: BrowserOptions, ctx: FetchContext, failure: _ReplayFailure,
                       *, target_url: str | None = None) -> None:
        """Record the replay failure site before the healing session starts."""
        self._record(ctx, opts,
                     error_code='playwright_selector_timeout' if failure.timeout else 'script_assertion_failed',
                     message=failure.message(), exc=failure.error, snapshot=failure.snapshot,
                     severity='warning' if failure.empty else 'error',
                     target_url=target_url or _failed_target(opts, failure.snapshot))

    def _record(self, ctx: FetchContext, opts: BrowserOptions, *, error_code: str, message: str,
                snapshot: dict[str, Any], target_url: str, exc: BaseException | None = None,
                severity: str = 'error', phase: str = 'browser_replay') -> None:
        if ctx.record_error is None:
            return
        context: dict[str, Any] = {'page_url': opts.page_url, **snapshot}
        if exc is not None:
            context['traceback'] = stack_frames(exc)
        ctx.record_error(DiagnosticEvent(
            phase=phase, error_code=error_code, message=message, severity=severity,
            source_id=self.spec.id, page_url=opts.page_url, target_url=target_url,
            exception_type=type(exc).__name__ if exc is not None else None, retryable=False,
            debug_context=context))


class _ReplayBroken(Exception):
    """Internal control flow: the replay failed, carrying the captured failure site."""

    def __init__(self, failure: _ReplayFailure) -> None:
        super().__init__(failure.message())
        self.failure = failure


@dataclass(frozen=True)
class _ReplayFailure:
    """Internal carrier: the failing page state captured before its tab closed.

    An empty result (`error is None, empty=True`) is a failure too: a page that
    yields nothing no longer tells a fixed script apart from a site change.
    """

    error: BaseException | None
    snapshot: dict[str, Any]
    timeout: bool = False
    empty: bool = False

    def message(self) -> str:
        if self.empty:
            return 'browser replay script returned no items'
        name = type(self.error).__name__
        if self.timeout:
            return f'browser replay timed out ({name})'
        return f'browser replay script failed ({name})'

    def describe(self) -> str:
        """What the healing prompt reads: the failure, bounded, never a whole page."""
        if self.empty:
            return 'the replay script completed but returned no items'
        detail = clip(str(self.error), 500)
        if self.timeout:
            return f'the replay timed out ({type(self.error).__name__}): {detail}'
        return f'the replay raised {type(self.error).__name__}: {detail}'


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


def _pi_spec(config: AppConfig, opts: BrowserOptions) -> BrowserPiSpec:
    """Source-level `pi` overrides win over the `browser.pi` block."""
    base, override = config.browser.pi, opts.pi
    if override is None:
        return base
    return BrowserPiSpec(binary=override.binary or base.binary,
                         model=override.model or base.model,
                         reasoning_level=override.reasoning_level or base.reasoning_level,
                         timeout=override.timeout or base.timeout)


def _read_script(script: Path) -> str:
    """The installed script the healing session must repair (bounded by its prompt)."""
    try:
        return script.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _local_meta(config: AppConfig, source_id: str) -> dict[str, Any]:
    """The local-only meta file of the installed script, or nothing when absent."""
    try:
        raw = json.loads(meta_path(config.data_dir, source_id).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _failed_target(opts: BrowserOptions, snapshot: dict[str, Any]) -> str:
    """Point the ledger at the page that failed, not just the configured entry."""
    current = snapshot.get('current_url')
    if isinstance(current, str) and current.startswith(('http://', 'https://')):
        return current
    return opts.page_url


def _cursor(opts: BrowserOptions, script: Path, count: int) -> dict[str, Any]:
    return {'page_url': opts.page_url, 'script': script.name, 'items': count}
