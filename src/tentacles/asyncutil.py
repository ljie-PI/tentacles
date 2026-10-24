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



async def gather_draining(tasks: list[asyncio.Task[_T]]) -> list[_T]:
    """On cancellation/failure, drain every child before its owner closes resources."""
    running = asyncio.gather(*tasks)
    try:
        # Owner cancellation must not first cancel children through gather and
        # then cancel them again while their asynchronous cleanup is in progress.
        return list(await asyncio.shield(running))
    except BaseException:
        for task in tasks:
            task.cancel()
        cleanup = asyncio.gather(*tasks, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                pass
        cleanup.result()
        if running.done() and not running.cancelled():
            running.exception()  # consume the original aggregate's terminal error
        raise
