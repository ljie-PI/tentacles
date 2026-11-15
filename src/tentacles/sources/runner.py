"""Fetch domain work: cursor in, items stored, next cursor committed."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from tentacles.asyncutil import run_store_call
from tentacles.config import AppConfig, SourceSpec
from tentacles.diagnostics import DiagnosticEvent, classify_failure, stack_frames
from tentacles.errors import TentaclesError
from tentacles.models import has_original_url
from tentacles.net import HttpPool
from tentacles.runs import RunOperation
from tentacles.sources.base import FetchContext, build_source
from tentacles.store import RunWriter, Store
from tentacles.sources.imap_client import ImapFactory, connect_imap

if TYPE_CHECKING:
    from tentacles.llm.registry import ModelRegistry

log = logging.getLogger(__name__)

# 其他来源在这段时间内报道过的近似标题视为同一条（跨来源转载）
CROSS_SOURCE_WINDOW = timedelta(hours=48)


def _page_url(spec: SourceSpec) -> str | None:
    """The configured entry address a top-level source failure points at."""
    url = spec.options.get('url')
    return url if isinstance(url, str) and url else None


def _fetch_failure(spec: SourceSpec, exc: Exception) -> DiagnosticEvent:
    """Record what a whole-source failure could not classify any further."""
    error_code, retryable, http_status = classify_failure(exc)
    message = exc.message if isinstance(exc, TentaclesError) else f'source fetch failed ({type(exc).__name__})'
    return DiagnosticEvent(phase='fetch_page', error_code=error_code, message=message, retryable=retryable,
                           http_status=http_status, source_id=spec.id, page_url=_page_url(spec),
                           exception_type=type(exc).__name__,
                           debug_context={'exception': stack_frames(exc)})


async def fetch_source(spec: SourceSpec, operation: RunOperation, *, store: Store, http: HttpPool,
                       imap_factory: ImapFactory = connect_imap, timezone: str = 'UTC',
                       models: ModelRegistry | None = None, config: AppConfig | None = None) -> None:
    """The lifecycle owns errors, cancellation and terminal state.

    Share its accumulator so even billed responses drained during cancellation
    remain attached to this Run. Store calls stay off the shared event loop.
    """
    ctx = FetchContext(http=http, now=operation.started_at, imap_factory=imap_factory, timezone=timezone,
                       seen_links=lambda urls: store.seen_links(spec.id, urls), models=models,
                       metrics=operation.metrics,
                       classify_links=lambda urls: store.classify_links(spec.id, urls),
                       config=config, record_error=operation.record_error)
    state = await run_store_call(store.get_source_state, spec.id)
    source = build_source(spec)
    try:
        result = await source.fetch(state.cursor, ctx)
    except Exception as exc:
        # The Run's ledger outlives this batch: the event is collected before
        # the failure reaches RunLifecycle's rollback-and-cleanup path.
        operation.record_error(_fetch_failure(spec, exc))
        raise
    # 非邮件条目必须留有原文详情或入口页链接，否则无法回溯未截断全文。
    reachable = [index for index, item in enumerate(result.items)
                 if item.kind == 'email' or has_original_url(item.url, item.page_url)]
    dropped = len(result.items) - len(reachable)
    if dropped:
        log.warning('%s: dropped %d item(s) without an http(s) original or page URL', spec.id, dropped)
        result.items = [result.items[index] for index in reachable]
        if result.observations:
            result.observations = [result.observations[index] for index in reachable]
        result.stats['invalid_url'] = dropped
    for item in result.items:
        item.fetched_at = ctx.now
    title_since = ctx.now - CROSS_SOURCE_WINDOW if source.dedup_titles else None
    state.cursor = result.cursor
    state.last_fetch_at, state.last_status, state.last_error = ctx.now, "ok", None
    stats = {**result.stats, "fetched": len(result.items), "new": 0, "updated": 0, "dup": 0}

    def publish(writer: RunWriter) -> None:
        saved = writer.upsert_items(result.items, source_id=spec.id, title_since=title_since,
                                    observations=result.observations if spec.update_existing else [])
        writer.save_source_state(state)
        stats.update(new=len(saved.new_ids), updated=saved.updated, dup=saved.dup)

    await operation.commit(publish, stats=stats)
