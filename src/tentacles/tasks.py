"""Persistent task identity, scheduling state and lease acquisition results."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from tentacles.schedule import Schedule


@dataclass(frozen=True)
class Task:
    kind: str
    target: str
    schedule: str
    timezone: str
    enabled: bool = True


@dataclass
class TaskState:
    first_seen_at: datetime
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    token: str | None = None
    lease_until: datetime | None = None
    started_at: datetime | None = None
    run_id: int | None = None

    def running(self, now: datetime) -> bool:
        return self.token is not None and self.lease_until is not None and self.lease_until > now

    def next_run(self, task: Task) -> datetime:
        schedule = Schedule.parse(task.schedule)
        anchor = self.started_at if self.token else self.last_run_at
        if anchor is None and schedule.interval is not None:
            return self.first_seen_at
        next_run = schedule.next_after(anchor or self.first_seen_at, ZoneInfo(task.timezone))
        # Abandoned tasks become retryable at lease expiry, even for long intervals.
        return min(next_run, self.lease_until) if self.token and self.lease_until else next_run


@dataclass(frozen=True)
class Claim:
    status: Literal['ready', 'busy', 'not_due']
    run_id: int | None = None
