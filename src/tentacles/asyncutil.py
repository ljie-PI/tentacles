"""Cancellation-safe worker IO for SQLite operations shared by async tasks."""
from __future__ import annotations

import asyncio
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")


async def run_store_call(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
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

