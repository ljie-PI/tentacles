"""A polling loop that drains the current tick before Service is closed."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable

from tentacles.api.models import SchedulerLoopView
from tentacles.service import TentaclesService
from tentacles.timeutil import to_iso

log = logging.getLogger(__name__)


async def poll_scheduler(service: TentaclesService, stop: asyncio.Event, status: SchedulerLoopView,
                         clock: Callable[[], datetime]) -> None:
    status.running = True
    try:
        while not stop.is_set():
            try:
                results = await service.tick()
                status.last_tick_at = to_iso(clock())
                status.last_error = ('one or more scheduled operations failed'
                                     if any(result.status == 'error' for result in results) else None)
            except Exception as exc:
                status.last_error = type(exc).__name__
            status.cycles += 1
            if status.last_error is not None:
                status.failed_cycles += 1
                log.warning('scheduler cycle failed (%s)', status.last_error)
            try:
                await asyncio.wait_for(stop.wait(), timeout=status.poll_interval)
            except asyncio.TimeoutError:
                pass
    finally:
        status.running = False
