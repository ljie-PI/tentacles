"""Run one fetch of one source: cursor in, items stored, cursor/state/run out."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable

from tentacles.config import SourceSpec
from tentacles.dedup import split_similar
from tentacles.errors import TentaclesError
from tentacles.models import Item
from tentacles.net import HttpPool
from tentacles.schemas import FetchOutcome
from tentacles.sources.base import FetchContext, build_source
from tentacles.store import SourceState, Store

log = logging.getLogger(__name__)

# 其他来源在这段时间内报道过的近似标题视为同一条（跨来源转载）
CROSS_SOURCE_WINDOW = timedelta(hours=48)
# 游标里由 runner 维护的键：被判为跨来源重复的外部 id，避免窗口过后又被当成新条目
SUPPRESSED_KEY = "_suppressed"
SUPPRESSED_LIMIT = 500


async def fetch_source(spec: SourceSpec, *, store: Store, http: HttpPool,
                       clock: Callable[[], datetime]) -> FetchOutcome:
    """Never raises for fetch failures (network, parsing or the store itself):
    they are recorded on the source state and the run where possible, and the
    previous cursor is kept so the next fetch retries. Cancellation is recorded
    the same way and then re-raised.

    Store calls run in a worker thread so a locked database can't stall the
    event loop shared by concurrent fetches.
    """
    ctx = FetchContext(http=http, now=clock())
    run_id: int | None = None
    try:
        run_id = await asyncio.to_thread(store.start_run, "fetch", spec.id, ctx.now)
        state = await asyncio.to_thread(store.get_source_state, spec.id)
        source = build_source(spec)
        result = await source.fetch(state.cursor, ctx)
        suppressed: list[str] = list(state.cursor.get(SUPPRESSED_KEY, []))
        items = result.items
        if source.dedup_titles:
            items, suppressed = await asyncio.to_thread(
                _drop_cross_source, items, store, spec.id, ctx.now, suppressed)
        new_ids, _ = await asyncio.to_thread(store.upsert_items, items)

        dup = len(result.items) - len(new_ids)
        stats = {**result.stats, "fetched": len(result.items), "new": len(new_ids), "dup": dup}
        state.cursor = {**result.cursor, SUPPRESSED_KEY: suppressed} if suppressed else result.cursor
        state.last_fetch_at, state.last_status, state.last_error = ctx.now, "ok", None
        await asyncio.to_thread(_record_success, store, state, len(new_ids), run_id, stats, clock)
    except BaseException as exc:  # noqa: BLE001 — one broken source must not stop the others
        error = exc.message if isinstance(exc, TentaclesError) else f"{type(exc).__name__}: {exc}"
        log.warning("fetch %s failed: %s", spec.id, error)
        _record_failure(store, spec.id, run_id, error, ctx.now, clock)
        if not isinstance(exc, Exception):  # 取消 / 中断：记录后继续向上抛
            raise
        return FetchOutcome(source_id=spec.id, run_id=run_id or 0, status="error", new=0, dup=0,
                            error=error)
    return FetchOutcome(source_id=spec.id, run_id=run_id, status="ok", new=len(new_ids), dup=dup,
                        stats=stats)


def _drop_cross_source(items: list[Item], store: Store, source_id: str, now: datetime,
                       suppressed: list[str]) -> tuple[list[Item], list[str]]:
    """Drop entries already reported by another source (near-identical title within
    the window), remembering them so they stay dropped after the window passes."""
    fresh = store.unknown(items)  # 先用便宜的索引查询排除已入库的
    remembered = set(suppressed)
    candidates = [it for it in fresh if it.external_id not in remembered]
    if not candidates:  # 304 或 feed 无变化时的常态：不必加载其他来源的标题
        return [], suppressed
    kept, similar = split_similar(candidates, store.recent_titles(
        exclude_source=source_id, since=now - CROSS_SOURCE_WINDOW))
    return kept, (suppressed + [it.external_id for it in similar])[-SUPPRESSED_LIMIT:]


def _record_success(store: Store, state: SourceState, new: int, run_id: int,
                    stats: dict, clock: Callable[[], datetime]) -> None:
    store.save_source_state(state)
    store.add_source_items(state.source_id, new)
    store.finish_run(run_id, status="ok", now=clock(), stats=stats)


def _record_failure(store: Store, source_id: str, run_id: int | None, error: str, now: datetime,
                    clock: Callable[[], datetime]) -> None:
    """Best effort: the store itself may be what failed (e.g. database locked).
    The stored cursor is re-read so a failure never overwrites it."""
    try:
        state = store.get_source_state(source_id)
        state.last_fetch_at, state.last_status, state.last_error = now, "error", error
        store.save_source_state(state)
        if run_id is not None:
            store.finish_run(run_id, status="failed", now=clock(), error=error)
    except Exception:  # noqa: BLE001
        log.exception("could not record failure of fetch run %s", run_id)
