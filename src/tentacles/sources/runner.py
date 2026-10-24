"""Run one fetch of one source: cursor in, items stored, cursor/state/run out."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from tentacles.config import SourceSpec
from tentacles.dedup import drop_similar_to
from tentacles.errors import TentaclesError
from tentacles.net import HttpPool
from tentacles.schemas import FetchOutcome
from tentacles.sources.base import FetchContext, build_source
from tentacles.store import Store

log = logging.getLogger(__name__)

# 其他来源在这段时间内报道过的近似标题视为同一条（跨来源转载）
CROSS_SOURCE_WINDOW = timedelta(hours=48)


async def fetch_source(spec: SourceSpec, *, store: Store, http: HttpPool,
                       clock: Callable[[], datetime]) -> FetchOutcome:
    """Never raises for fetch failures: they are recorded on the source state and
    the run, and the previous cursor is kept so the next fetch retries.
    Cancellation is recorded the same way and then re-raised."""
    ctx = FetchContext(http=http, now=clock())
    run_id = store.start_run("fetch", spec.id, ctx.now)
    state = store.get_source_state(spec.id)
    try:
        source = build_source(spec)
        result = await source.fetch(state.cursor, ctx)
        items = result.items
        if source.dedup_titles:
            items = drop_similar_to(items, store.recent_titles(
                exclude_source=spec.id, since=ctx.now - CROSS_SOURCE_WINDOW))
        new_ids, dup = store.upsert_items(items)
    except BaseException as exc:  # noqa: BLE001 — one broken source must not stop the others
        error = exc.message if isinstance(exc, TentaclesError) else f"{type(exc).__name__}: {exc}"
        log.warning("fetch %s failed: %s", spec.id, error)
        state.last_fetch_at, state.last_status, state.last_error = ctx.now, "error", error
        store.save_source_state(state)
        store.finish_run(run_id, status="failed", now=clock(), error=error)
        if not isinstance(exc, Exception):  # 取消 / 中断：记录后继续向上抛
            raise
        return FetchOutcome(source_id=spec.id, run_id=run_id, status="error", new=0, dup=0, error=error)

    dup += len(result.items) - len(items)
    stats = {**result.stats, "fetched": len(result.items), "new": len(new_ids), "dup": dup}
    state.cursor, state.last_fetch_at = result.cursor, ctx.now
    state.last_status, state.last_error = "ok", None
    state.total_items += len(new_ids)
    store.save_source_state(state)
    store.finish_run(run_id, status="ok", now=clock(), stats=stats)
    return FetchOutcome(source_id=spec.id, run_id=run_id, status="ok", new=len(new_ids), dup=dup,
                        stats=stats)
