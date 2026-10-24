"""Validated intervals and five-field cron, computed in the configured timezone.

Cron candidates are local wall times round-tripped through UTC: imaginary times
are skipped, and both occurrences of an ambiguous time are real triggers.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from tentacles.timeutil import is_duration, parse_duration, to_utc

_ALIASES = {'@hourly': '0 * * * *', '@daily': '0 0 * * *', '@weekly': '0 0 * * 0'}
_FIELD = re.compile(r'^(\*|[0-9]+(?:-[0-9]+)?)(?:/([0-9]+))?$')


def _field_values(text: str, low: int, high: int) -> tuple[int, ...]:
    values: set[int] = set()
    for part in text.split(','):
        match = _FIELD.fullmatch(part)
        if not match:
            raise ValueError(f'invalid cron field {text!r}')
        span, step_text = match.groups()
        step = int(step_text or 1)
        if step < 1:
            raise ValueError('cron step must be positive')
        if span == '*':
            start, stop = low, high
        elif '-' in span:
            start, stop = map(int, span.split('-'))
        else:
            start = int(span)
            stop = high if step_text else start
        if not low <= start <= stop <= high:
            raise ValueError(f'cron field {text!r} must be within {low}..{high}')
        values.update(range(start, stop + 1, step))
    return tuple(sorted(values))


@dataclass(frozen=True)
class Cron:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: tuple[int, ...]
    months: tuple[int, ...]
    weekdays: tuple[int, ...]
    any_day: bool
    any_weekday: bool

    def matches_day(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom = day.day in self.days
        dow = (day.weekday() + 1) % 7 in self.weekdays
        if self.any_day:
            return dow
        if self.any_weekday:
            return dom
        return dom or dow

    def next_after(self, after: datetime, zone: ZoneInfo) -> datetime:
        after = to_utc(after)
        day = after.astimezone(zone).date()
        # The longest gap for a valid calendar date is eight years (Feb 29
        # over a non-leap century). Never silently accept impossible dates.
        for _ in range(366 * 8 + 1):
            if self.matches_day(day):
                earliest: datetime | None = None
                for hour in self.hours:
                    for minute in self.minutes:
                        wall = datetime(day.year, day.month, day.day, hour, minute)
                        for fold in (0, 1):
                            candidate = to_utc(wall.replace(tzinfo=zone, fold=fold))
                            if (candidate > after and
                                    candidate.astimezone(zone).replace(tzinfo=None) == wall and
                                    (earliest is None or candidate < earliest)):
                                earliest = candidate
                if earliest is not None:
                    return earliest
            try:
                day += timedelta(days=1)
            except OverflowError:
                break
        raise ValueError('cron has no representable future occurrence')


@dataclass(frozen=True)
class Schedule:
    interval: timedelta | None = None
    cron: Cron | None = None

    @classmethod
    @lru_cache(maxsize=256)
    def parse(cls, expression: str) -> Schedule:
        expression = expression.strip()
        if is_duration(expression):
            try:
                interval = parse_duration(expression)
            except OverflowError as exc:
                raise ValueError('schedule interval is too large') from exc
            if interval <= timedelta(0):
                raise ValueError('schedule interval must be positive')
            return cls(interval=interval)
        fields = _ALIASES.get(expression, expression).split()
        if len(fields) != 5:
            raise ValueError('schedule requires a positive interval, @hourly/@daily/@weekly, or 5 cron fields')
        minute, hour, day, month, weekday = fields
        cron = Cron(
            _field_values(minute, 0, 59), _field_values(hour, 0, 23),
            _field_values(day, 1, 31), _field_values(month, 1, 12),
            tuple(sorted({d % 7 for d in _field_values(weekday, 0, 7)})),
            day == '*', weekday == '*',
        )
        if cron.any_weekday and not cron.any_day and not any(
                d <= calendar.monthrange(2000, m)[1] for m in cron.months for d in cron.days):
            raise ValueError('cron has no possible calendar date')
        return cls(cron=cron)

    def next_after(self, after: datetime, zone: ZoneInfo) -> datetime:
        if self.interval is not None:
            return to_utc(after) + self.interval
        assert self.cron is not None
        return self.cron.next_after(after, zone)
