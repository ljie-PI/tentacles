"""Fetch domain work: cursor in, items stored, next cursor committed."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from tentacles.asyncutil import run_store_call
from tentacles.config import SourceSpec
from tentacles.net import HttpPool
from tentacles.runs import RunOperation
from tentacles.sources.base import FetchContext, build_source
from tentacles.store import Store
from tentacles.sources.imap_client import ImapFactory, connect_imap

if TYPE_CHECKING:
    from tentacles.llm.registry import ModelRegistry

# 其他来源在这段时间内报道过的近似标题视为同一条（跨来源转载）
CROSS_SOURCE_WINDOW = timedelta(hours=48)


async def fetch_source(spec: SourceSpec, operation: RunOperation, *, store: Store, http: HttpPool,
                       imap_factory: ImapFactory = connect_imap, timezone: str = 'UTC',
                       models: ModelRegistry | None = None) -> None:
    """The lifecycle owns errors, cancellation and terminal state.

    Share its accumulator so even billed responses drained during cancellation
    remain attached to this Run. Store calls stay off the shared event loop.
    """
    ctx = FetchContext(http=http, now=operation.started_at, imap_factory=imap_factory, timezone=timezone,
                       seen_links=lambda urls: store.seen_links(spec.id, urls), models=models,
                       metrics=operation.metrics)
    state = await run_store_call(store.get_source_state, spec.id)
    source = build_source(spec)
    result = await source.fetch(state.cursor, ctx)
    for item in result.items:
        item.fetched_at = ctx.now
    title_since = ctx.now - CROSS_SOURCE_WINDOW if source.dedup_titles else None
    new_ids, _ = await run_store_call(store.upsert_items, result.items,
                                   source_id=spec.id, title_since=title_since, run_id=operation.run_id)
    state.cursor = result.cursor
    state.last_fetch_at, state.last_status, state.last_error = ctx.now, "ok", None
    await operation.commit(lambda writer: writer.save_source_state(state), stats={
        **result.stats, "fetched": len(result.items), "new": len(new_ids), "dup": len(result.items) - len(new_ids)})
