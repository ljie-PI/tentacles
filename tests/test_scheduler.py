"""Scheduler behavior through Service, with real SQLite and injected time/HTTP."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from conftest import rss_feed
from tentacles.service import TentaclesService


@dataclass
class Clock:
    now: datetime = datetime(2026, 10, 25, 0, 10, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def sources(*schedules):
    return {'sources': [{'id': f'n{i}', 'type': 'rss', 'url': f'https://n{i}.test/rss',
                         'schedule': s} for i, s in enumerate(schedules)]}


def test_interval_due_is_start_based_and_persists_across_restart(write_config):
    path = write_config(sources('30m'))
    clock = Clock()

    def respond(request):
        clock.now += timedelta(minutes=2)
        return httpx.Response(200, text=rss_feed())

    async def scenario():
        service = TentaclesService.open(path, transport=httpx.MockTransport(respond), clock=clock)
        try:
            [result] = await service.tick()
            assert result.status == 'ok'
            [task] = service.scheduler_status()
            assert task.last_run_at == datetime(2026, 10, 25, 0, 10, tzinfo=timezone.utc)
            assert task.next_run_at == datetime(2026, 10, 25, 0, 40, tzinfo=timezone.utc)
            assert task.running is False and task.last_status == 'ok'
            assert service.list_sources()[0].next_run_at == task.next_run_at
            assert await service.tick() == []
        finally:
            await service.aclose()
        service = TentaclesService.open(path, transport=httpx.MockTransport(respond), clock=clock)
        try:
            assert await service.tick() == []
            clock.now = datetime(2026, 10, 25, 0, 40, tzinfo=timezone.utc)
            assert (await service.tick())[0].status == 'ok'
            assert len(service.list_runs()) == 2
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_cron_waits_on_first_appearance_and_frequency_edits_apply(write_config):
    path = write_config(sources('@hourly'))
    clock = Clock()
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=rss_feed()))

    async def scenario():
        service = TentaclesService.open(path, transport=transport, clock=clock)
        try:
            assert await service.tick() == []
            assert service.scheduler_status()[0].next_run_at == datetime(2026, 10, 25, 1, tzinfo=timezone.utc)
        finally:
            await service.aclose()
        clock.now += timedelta(minutes=50)
        service = TentaclesService.open(path, transport=transport, clock=clock)
        try:
            assert (await service.tick())[0].status == 'ok'
        finally:
            await service.aclose()
        clock.now += timedelta(minutes=5)
        write_config(sources('5m'))
        service = TentaclesService.open(path, transport=transport, clock=clock)
        try:
            assert (await service.tick())[0].status == 'ok'
            assert await service.tick() == []
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_failed_tasks_advance_schedule_and_record_errors(write_config):
    clock = Clock()
    service = TentaclesService.open(write_config(sources('1h')),
        transport=httpx.MockTransport(lambda r: httpx.Response(500)), clock=clock)

    async def scenario():
        try:
            assert (await service.tick())[0].status == 'error'
            assert await service.tick() == []
            [state] = service.scheduler_status()
            [run] = service.list_runs()
            assert state.last_run_at == clock.now
            assert state.next_run_at == clock.now + timedelta(hours=1)
            assert state.last_status == 'error' and state.last_error
            assert state.last_error == run.error and run.status == 'failed'
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_tick_and_manual_fetch_share_atomic_lock_between_services(write_config):
    path = write_config(sources('1h'))
    clock = Clock()

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, text=rss_feed())

        one = TentaclesService.open(path, transport=httpx.MockTransport(respond), clock=clock)
        two = TentaclesService.open(path, transport=httpx.MockTransport(respond), clock=clock)
        try:
            active = asyncio.create_task(one.tick())
            await asyncio.wait_for(entered.wait(), 2)
            assert two.scheduler_status()[0].running is True
            assert (await two.tick())[0].status == 'busy'
            assert (await two.fetch_sources())[0].status == 'busy'
            release.set()
            assert (await active)[0].status == 'ok'
            assert len(two.list_runs()) == 1
        finally:
            release.set()
            await one.aclose()
            await two.aclose()

    asyncio.run(scenario())


def test_tick_limits_concurrency_and_skips_disabled_sources(write_config):
    data = sources(*(['1h'] * 9))
    data['sources'][-1]['enabled'] = False
    clock = Clock()

    async def scenario():
        active = peak = 0
        started = set()
        release = asyncio.Event()

        async def respond(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            started.add(request.url.host)
            if active == 4:
                release.set()
            await asyncio.wait_for(release.wait(), 2)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(200, text=rss_feed())

        service = TentaclesService.open(write_config(data), transport=httpx.MockTransport(respond), clock=clock)
        try:
            results = await service.tick()
            assert len(results) == 8 and all(r.status == 'ok' for r in results)
            assert peak == 4
            assert 'n8.test' not in started
            assert service.list_sources()[-1].next_run_at is None
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_expired_lease_is_reclaimed_and_stale_success_cannot_write(write_config):
    path = write_config(sources('2w'))
    clock = Clock()

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        attempts = 0

        async def respond(request):
            nonlocal attempts
            attempts += 1
            attempt = attempts
            if attempt == 1:
                entered.set()
                await release.wait()
            return httpx.Response(200, headers={'etag': f'v{attempt}'}, text=rss_feed(
                {'title': f'Bulletin {attempt}', 'guid': str(attempt)}))

        transport = httpx.MockTransport(respond)
        one = TentaclesService.open(path, transport=transport, clock=clock)
        two = TentaclesService.open(path, transport=transport, clock=clock)
        active = None
        try:
            active = asyncio.create_task(one.tick())
            await asyncio.wait_for(entered.wait(), 2)
            clock.now += timedelta(hours=1)
            assert two.scheduler_status()[0].running is False
            assert (await two.tick())[0].status == 'ok'
            release.set()
            assert (await active)[0].status == 'error'
            [task] = two.scheduler_status()
            assert task.last_status == 'ok' and not task.running
            assert task.last_run_at == clock.now
            assert [i.title for i in two.list_items().items] == ['Bulletin 2']
            assert [r.status for r in two.list_runs()] == ['ok', 'failed']
            assert await two.tick() == []
        finally:
            release.set()
            if active:
                await active
            await one.aclose()
            await two.aclose()

    asyncio.run(scenario())


def test_cancellation_releases_lease_and_records_failure(write_config):
    import pytest
    clock = Clock()

    async def scenario():
        entered = asyncio.Event()

        async def respond(request):
            entered.set()
            await asyncio.Event().wait()

        service = TentaclesService.open(write_config(sources('1h')),
                                        transport=httpx.MockTransport(respond), clock=clock)
        try:
            active = asyncio.create_task(service.tick())
            await asyncio.wait_for(entered.wait(), 2)
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
            [state] = service.scheduler_status()
            assert not state.running and state.last_status == 'error'
            assert state.last_run_at == clock.now
            assert await service.tick() == []
            assert service.list_runs()[0].status == 'failed'
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_a_lease_acquisition_failure_does_not_abort_other_sources(write_config):
    import sqlite3
    path = write_config(sources('1h', '1h'))
    service = TentaclesService.open(path, clock=Clock(),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=rss_feed())))
    connection = sqlite3.connect(service.config.db_path)
    connection.executescript("""
        CREATE TRIGGER reject_first_run BEFORE INSERT ON runs WHEN NEW.target = 'n0'
        BEGIN SELECT RAISE(ABORT, 'test acquisition failure'); END;
    """)

    async def scenario():
        try:
            outcomes = {r.source_id: r for r in await service.tick()}
            assert outcomes['n0'].status == 'error'
            assert 'test acquisition failure' in outcomes['n0'].error
            assert outcomes['n1'].status == 'ok'
        finally:
            connection.close()
            await service.aclose()

    asyncio.run(scenario())


def test_completed_run_survives_interrupted_lease_release_without_early_rerun(write_config):
    import sqlite3
    clock = Clock()
    path = write_config(sources('2w'))
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=rss_feed()))

    async def scenario():
        service = TentaclesService.open(path, transport=transport, clock=clock)
        connection = sqlite3.connect(service.config.db_path)
        # Simulate the historical crash window: the successful run is durable,
        # but its scheduler update is lost. Assertions remain at the Service seam.
        connection.executescript("""
            CREATE TRIGGER interrupt_release BEFORE UPDATE OF last_run_at ON task_state
            BEGIN SELECT RAISE(IGNORE); END;
        """)
        try:
            assert (await service.tick())[0].status == 'ok'
            assert service.list_runs()[0].status == 'ok'
        finally:
            connection.execute('DROP TRIGGER interrupt_release')
            connection.close()
            await service.aclose()
        clock.now += timedelta(hours=1)
        service = TentaclesService.open(path, transport=transport, clock=clock)
        try:
            assert await service.tick() == []
            [state] = service.scheduler_status()
            assert state.last_run_at == datetime(2026, 10, 25, 0, 10, tzinfo=timezone.utc)
            assert state.next_run_at == datetime(2026, 11, 8, 0, 10, tzinfo=timezone.utc)
            assert state.last_status == 'ok' and not state.running
            assert len(service.list_runs()) == 1
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_task_release_errors_are_isolated_and_completion_is_atomic(write_config):
    import sqlite3
    clock = Clock()
    service = TentaclesService.open(write_config(sources('1h', '1h')), clock=clock,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=rss_feed())))
    connection = sqlite3.connect(service.config.db_path)
    connection.executescript("""
        CREATE TRIGGER reject_completion BEFORE UPDATE OF last_run_at ON task_state
        WHEN NEW.target = 'n0'
        BEGIN SELECT RAISE(ABORT, 'test completion failure'); END;
    """)

    async def scenario():
        try:
            outcomes = {r.source_id: r for r in await service.tick()}
            assert outcomes['n0'].status == 'error'
            assert outcomes['n1'].status == 'ok'
            # A cursor/run success cannot commit without advancing scheduling state.
            runs = {r.target: r for r in service.list_runs()}
            assert runs['n0'].status != 'ok'
            assert runs['n1'].status == 'ok'
        finally:
            connection.close()
            await service.aclose()

    asyncio.run(scenario())


def test_completion_failure_does_not_replace_cancellation(write_config):
    import sqlite3
    import pytest

    async def scenario():
        entered = asyncio.Event()

        async def respond(request):
            entered.set()
            await asyncio.Event().wait()

        service = TentaclesService.open(write_config(sources('1h')), clock=Clock(),
                                        transport=httpx.MockTransport(respond))
        connection = sqlite3.connect(service.config.db_path)
        connection.executescript("""
            CREATE TRIGGER reject_completion BEFORE UPDATE OF last_run_at ON task_state
            BEGIN SELECT RAISE(ABORT, 'test completion failure'); END;
        """)
        try:
            active = asyncio.create_task(service.tick())
            await asyncio.wait_for(entered.wait(), 2)
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
        finally:
            connection.close()
            await service.aclose()

    asyncio.run(scenario())


def test_expired_failure_is_visible_while_the_replacement_is_running(write_config):
    clock = Clock()
    path = write_config(sources('2w'))

    async def scenario():
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        attempts = 0

        async def respond(request):
            nonlocal attempts
            attempt = attempts
            attempts += 1
            entered[attempt].set()
            await release[attempt].wait()
            return httpx.Response(200, text=rss_feed())

        one = TentaclesService.open(path, clock=clock, transport=httpx.MockTransport(respond))
        two = TentaclesService.open(path, clock=clock, transport=httpx.MockTransport(respond))
        active = []
        try:
            active.append(asyncio.create_task(one.tick()))
            await asyncio.wait_for(entered[0].wait(), 2)
            clock.now += timedelta(hours=1)
            active.append(asyncio.create_task(two.tick()))
            await asyncio.wait_for(entered[1].wait(), 2)
            [state] = two.scheduler_status()
            assert state.running
            assert state.last_run_at == datetime(2026, 10, 25, 0, 10, tzinfo=timezone.utc)
            assert state.last_status == 'error'
            assert state.last_error == 'task lease expired'
        finally:
            for event in release:
                event.set()
            await asyncio.gather(*active, return_exceptions=True)
            await one.aclose()
            await two.aclose()

    asyncio.run(scenario())
