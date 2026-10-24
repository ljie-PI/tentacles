"""The spec explicitly permits pure-function tests at the scheduling seam."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tentacles.schedule import Schedule


def test_cron_lists_ranges_steps_and_sunday_alias():
    schedule = Schedule.parse('0,30 9-17/2 * * 7')
    after = datetime.fromisoformat('2026-10-24T23:00:00+00:00')
    assert schedule.next_after(after, ZoneInfo('Asia/Shanghai')).isoformat() == '2026-10-25T01:00:00+00:00'


@pytest.mark.parametrize('expression,zone,after,expected', [
    ('0 0 1 * 1', 'Asia/Shanghai', '2026-10-25T00:00:00+00:00', '2026-10-25T16:00:00+00:00'),
    ('0 0 1 * 1', 'Asia/Shanghai', '2026-10-31T00:00:00+00:00', '2026-10-31T16:00:00+00:00'),
    ('0 0 1 1 *', 'Asia/Shanghai', '2026-12-31T16:00:00+00:00', '2027-12-31T16:00:00+00:00'),
    ('30 2 * * *', 'America/New_York', '2026-03-07T07:30:00+00:00', '2026-03-09T06:30:00+00:00'),
    ('0 * * * *', 'America/New_York', '2026-03-08T06:59:00+00:00', '2026-03-08T07:00:00+00:00'),
    ('0,30 1 * * *', 'America/New_York', '2026-11-01T05:30:00+00:00', '2026-11-01T06:00:00+00:00'),
    ('30 2 * * *', 'Asia/Shanghai', '1991-04-12T18:30:00+00:00', '1991-04-14T17:30:00+00:00'),
    ('0 0 29 2 *', 'Asia/Shanghai', '2096-02-29T00:00:00+00:00', '2104-02-28T16:00:00+00:00'),
    ('@hourly', 'Asia/Shanghai', '2026-10-25T01:00:00+00:00', '2026-10-25T02:00:00+00:00'),
    ('@daily', 'Asia/Shanghai', '2026-10-25T01:00:00+00:00', '2026-10-25T16:00:00+00:00'),
    ('@weekly', 'Asia/Shanghai', '2026-10-25T01:00:00+00:00', '2026-10-31T16:00:00+00:00'),
])
def test_next_cron_occurrence(expression, zone, after, expected):
    assert Schedule.parse(expression).next_after(
        datetime.fromisoformat(after), ZoneInfo(zone)) == datetime.fromisoformat(expected)


@pytest.mark.parametrize('expression,expected', [('0 0 */1 * 1', '2026-11-02T00:00:00+00:00'),
                                               ('0 0 */2 * 1', '2026-11-09T00:00:00+00:00')])
def test_wildcard_day_steps_use_vixie_and_semantics(expression, expected):
    after = datetime.fromisoformat('2026-10-26T00:00:00+00:00')
    assert Schedule.parse(expression).next_after(after, ZoneInfo('UTC')) == datetime.fromisoformat(
        expected)


def test_wildcard_weekday_steps_can_have_a_leap_day_gap_over_eight_years():
    after = datetime.fromisoformat('2020-03-01T00:00:00+00:00')
    assert Schedule.parse('0 0 29 2 */6').next_after(after, ZoneInfo('UTC')) == datetime.fromisoformat(
        '2032-02-29T00:00:00+00:00')
