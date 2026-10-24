"""HTTP error metadata and a batch-local, interruptible shared retry gate."""
from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from tentacles.errors import UpstreamError
from tentacles.timeutil import to_utc


class ModelHTTPError(UpstreamError):
    def __init__(self, message: str, status: int, retry_after: float | None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

    @property
    def decision_fatal(self) -> bool:
        return self.status in {401, 403} or self.status == 402 and self.retry_after is None


class ModelCredentialsError(UpstreamError):
    pass


class RequestGate:
    """One resume deadline for all workers, and a terminal dispatch stop signal."""
    def __init__(self) -> None:
        self.resume_at = 0.0
        self.failure: UpstreamError | None = None
        self._stopped = asyncio.Event()

    def pause(self, seconds: float) -> None:
        self.resume_at = max(self.resume_at, asyncio.get_running_loop().time() + seconds)

    def stop(self, error: UpstreamError) -> None:
        if self.failure is None:
            self.failure = error
            self._stopped.set()

    async def wait(self, seconds: float = 0) -> None:
        local_deadline = asyncio.get_running_loop().time() + seconds
        while True:
            if self.failure is not None:
                raise self.failure
            delay = max(local_deadline, self.resume_at) - asyncio.get_running_loop().time()
            if delay <= 0:
                return
            try:
                await asyncio.wait_for(self._stopped.wait(), delay)
            except asyncio.TimeoutError:
                pass


def retry_after_seconds(response: httpx.Response | None, now: datetime) -> float | None:
    value = response.headers.get('Retry-After') if response is not None else None
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            seconds = (deadline - to_utc(now)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return min(60.0, max(0.0, seconds)) if math.isfinite(seconds) else None


def retry_delay(response: httpx.Response | None, attempt: int, now: datetime) -> float:
    hint = retry_after_seconds(response, now)
    return hint if hint is not None else min(30.0, 2 ** min(attempt, 5) + random.random())
