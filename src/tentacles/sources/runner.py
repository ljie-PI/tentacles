"""Run one fetch of one source: cursor in, items stored, cursor/state/run out."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, TypeVar

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
_T = TypeVar("_T")


async def _store_call(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Drain worker IO before cancellation can close its shared SQLite connection."""
    worker = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError as exc:
            if worker.cancelled():
                raise
            cancellation = exc
        except BaseException:
            if cancellation is not None:
                raise cancellation
            raise
        else:
            if cancellation is not None:
                raise cancellation
            return result


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

    def start_run() -> None:
        nonlocal run_id
        run_id = store.start_run("fetch", spec.id, ctx.now)

    try:
        await _store_call(start_run)
        assert run_id is not None
        state = await _store_call(store.get_source_state, spec.id)
        source = build_source(spec)
        result = await source.fetch(state.cursor, ctx)
        suppressed: list[Item] = []
        items = result.items
        for item in items:
            item.fetched_at = ctx.now
        if source.dedup_titles:
            items, suppressed = await _store_call(
                _drop_similar, items, store, spec.id, ctx.now)
        new_ids, _ = await _store_call(store.upsert_items, items, suppressed=suppressed)

        dup = len(result.items) - len(new_ids)
        stats = {**result.stats, "fetched": len(result.items), "new": len(new_ids), "dup": dup}
        state.cursor = result.cursor
        state.last_fetch_at, state.last_status, state.last_error = ctx.now, "ok", None
        await _store_call(_record_success, store, state, run_id, stats, clock)
    except BaseException as exc:  # noqa: BLE001 — one broken source must not stop the others
        error = exc.message if isinstance(exc, TentaclesError) else f"{type(exc).__name__}: {exc}"
        log.warning("fetch %s failed: %s", spec.id, error)
        await _store_call(_record_failure, store, spec.id, run_id, error, ctx.now, clock)
        if not isinstance(exc, Exception):  # 取消 / 中断：记录后继续向上抛
            raise
        return FetchOutcome(source_id=spec.id, run_id=run_id or 0, status="error", new=0, dup=0,
                            error=error)
    return FetchOutcome(source_id=spec.id, run_id=run_id, status="ok", new=len(new_ids), dup=dup,
                        stats=stats)


def _drop_similar(items: list[Item], store: Store, source_id: str,
                  now: datetime) -> tuple[list[Item], list[Item]]:
    """Drop near-identical titles in the batch or recently stored by another source."""
    candidates = store.unknown(items)
    if not candidates:
        return [], []
    return split_similar(candidates, store.recent_titles(
        exclude_source=source_id, since=now - CROSS_SOURCE_WINDOW))


def _record_success(store: Store, state: SourceState, run_id: int,
                    stats: dict, clock: Callable[[], datetime]) -> None:
    store.save_source_state(state)
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
