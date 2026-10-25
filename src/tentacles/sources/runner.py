"""Run one fetch of one source: cursor in, items stored, cursor/state/run out."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, TYPE_CHECKING

from tentacles.asyncutil import run_store_call
from tentacles.config import SourceSpec
from tentacles.errors import TentaclesError
from tentacles.net import HttpPool
from tentacles.schemas import FetchOutcome
from tentacles.sources.base import FetchContext, build_source
from tentacles.store import Store
from tentacles.sources.imap_client import ImapFactory, connect_imap

if TYPE_CHECKING:
    from tentacles.llm.registry import ModelRegistry

log = logging.getLogger(__name__)

# 其他来源在这段时间内报道过的近似标题视为同一条（跨来源转载）
CROSS_SOURCE_WINDOW = timedelta(hours=48)


async def fetch_source(spec: SourceSpec, *, store: Store, http: HttpPool,
                       clock: Callable[[], datetime], run_id: int | None = None,
                       started_at: datetime | None = None,
                       imap_factory: ImapFactory = connect_imap, timezone: str = 'UTC',
                       models: ModelRegistry | None = None) -> FetchOutcome:
    """Never raises for fetch failures (network, parsing or the store itself):
    they are recorded on the source state and the run where possible, and the
    previous cursor is kept so the next fetch retries. Cancellation is re-raised
    after worker IO completes; an already committed success stays successful.

    Store calls run in a worker thread so a locked database can't stall the
    event loop shared by concurrent fetches.
    """
    ctx = FetchContext(http=http, now=started_at or clock(), imap_factory=imap_factory, timezone=timezone,
                       seen_links=lambda urls: store.seen_links(spec.id, urls), models=models)
    completed = False

    def start_run() -> None:
        nonlocal run_id
        run_id = store.start_run("fetch", spec.id, ctx.now)

    try:
        if run_id is None:
            await run_store_call(start_run)
        assert run_id is not None
        state = await run_store_call(store.get_source_state, spec.id)
        source = build_source(spec)
        result = await source.fetch(state.cursor, ctx)
        for item in result.items:
            item.fetched_at = ctx.now
        title_since = ctx.now - CROSS_SOURCE_WINDOW if source.dedup_titles else None
        new_ids, _ = await run_store_call(store.upsert_items, result.items,
                                       source_id=spec.id, title_since=title_since, run_id=run_id)

        dup = len(result.items) - len(new_ids)
        stats = {**result.stats, **_model_stats(ctx), "fetched": len(result.items), "new": len(new_ids), "dup": dup}
        state.cursor = result.cursor
        state.last_fetch_at, state.last_status, state.last_error = ctx.now, "ok", None
        def record_success() -> None:
            nonlocal completed
            assert run_id is not None
            store.save_fetch_success(state, run_id, now=clock(), stats=stats, cost=ctx.metrics.cost)
            completed = True

        await run_store_call(record_success)
    except BaseException as exc:  # noqa: BLE001 — one broken source must not stop the others
        error = exc.message if isinstance(exc, TentaclesError) else f"fetch failed ({type(exc).__name__})"
        if not completed:
            log.warning("fetch %s failed (%s)", spec.id, type(exc).__name__)
            await run_store_call(_record_failure, store, spec.id, run_id, error, ctx.now, clock,
                                 _model_stats(ctx), ctx.metrics.cost)
        if not isinstance(exc, Exception):  # 取消 / 中断：记录后继续向上抛
            raise
        return FetchOutcome(source_id=spec.id, run_id=run_id or 0, status="error", new=0, dup=0,
                            error=error, stats=_model_stats(ctx))
    return FetchOutcome(source_id=spec.id, run_id=run_id, status="ok", new=len(new_ids), dup=dup,
                        stats=stats)


def _model_stats(ctx: FetchContext) -> dict[str, Any]:
    if not ctx.metrics.models:
        return {}
    return {'usage': ctx.metrics.usage.model_dump(), 'cost': ctx.metrics.cost, 'models': ctx.metrics.models}


def _record_failure(store: Store, source_id: str, run_id: int | None, error: str, now: datetime,
                    clock: Callable[[], datetime], stats: dict[str, Any], cost: float | None) -> None:
    """Best effort: the store itself may be what failed (e.g. database locked).
    Only status fields are updated, so a concurrent success keeps its cursor."""
    try:
        store.save_fetch_failure(source_id, run_id, started_at=now, finished_at=clock(), error=error,
                                 stats=stats or None, cost=cost)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not record failure of fetch run %s (%s)", run_id, type(exc).__name__)
