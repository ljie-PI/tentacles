"""Scheduled pipeline operations through Service and injected time/HTTP."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import rss_feed
from test_pipelines import FEED, pipeline_config, three_entries
from test_scheduler import Clock
from tentacles.service import TentaclesService


def scheduled_config(schedule='1d', filter_schedule='30m'):
    data = pipeline_config(schedule=schedule)
    data['sources'][0]['schedule'] = '5m'
    if filter_schedule is not None:
        data['pipelines'][0]['filter_schedule'] = filter_schedule
    return data


def test_tick_fetches_then_filters_then_runs_due_pipelines(write_config, web):
    clock = Clock()
    data = scheduled_config()
    data['pipelines'].append({**data['pipelines'][0], 'id': 'second'})
    web.serve(FEED, three_entries())
    service = TentaclesService.open(write_config(data), env={}, transport=web.transport(), clock=clock)

    async def scenario():
        try:
            outcomes = await service.tick()
            assert [r.kind for r in outcomes] == ['fetch', 'filter', 'filter', 'pipeline', 'pipeline']
            assert [r.target for r in outcomes] == ['news', 'daily', 'second', 'daily', 'second']
            assert all(r.status == 'ok' for r in outcomes)
            assert outcomes[1].stats['candidates'] == outcomes[2].stats['candidates'] == 3
            assert outcomes[3].stats['consumed'] == outcomes[4].stats['consumed'] == 2
            assert await service.tick() == []
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_scheduler_and_pipeline_views_show_only_enabled_scheduled_jobs_and_pending(write_config, web):
    clock = Clock()
    data = scheduled_config()
    data['pipelines'].append({**data['pipelines'][0], 'id': 'no-filter', 'filter_schedule': None})
    data['pipelines'].append({**data['pipelines'][0], 'id': 'disabled', 'enabled': False})
    web.serve(FEED, three_entries())
    service = TentaclesService.open(write_config(data), env={}, transport=web.transport(), clock=clock)

    async def scenario():
        try:
            await service.fetch_sources()
            await service.filter_pipeline('daily')
            tasks = {(t.kind, t.target): t for t in service.scheduler_status()}
            assert set(tasks) == {('fetch', 'news'), ('filter', 'daily'),
                                  ('pipeline', 'daily'), ('pipeline', 'no-filter')}
            assert tasks['filter', 'daily'].pending == tasks['pipeline', 'daily'].pending == 2
            assert tasks['filter', 'daily'].schedule == '30m'
            assert tasks['filter', 'daily'].next_run_at == clock.now + timedelta(minutes=30)
            views = {p.id: p for p in service.list_pipelines()}
            assert views['daily'].next_run_at == clock.now
            assert views['daily'].filter_next_run_at == clock.now + timedelta(minutes=30)
            assert views['daily'].pending == 2
            assert views['no-filter'].filter_next_run_at is None
            assert views['disabled'].next_run_at is views['disabled'].filter_next_run_at is None
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_cron_waits_for_trigger_persists_on_restart_and_schedule_edits_apply(write_config, web):
    clock = Clock()
    data = scheduled_config(schedule='0 10 * * *', filter_schedule='@hourly')
    data['sources'][0]['enabled'] = False
    path = write_config(data)
    web.serve(FEED, three_entries())

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        try:
            await service.fetch_sources(['news'])
            assert await service.tick() == []
            [view] = service.list_pipelines()
            assert view.next_run_at == datetime(2026, 10, 25, 2, tzinfo=timezone.utc)
            clock.now = datetime(2026, 10, 25, 1, tzinfo=timezone.utc)
            [filtered] = await service.tick()
            assert filtered.kind == 'filter' and filtered.stats['candidates'] == 3
            assert service.list_pipelines()[0].pending == 2
        finally:
            await service.aclose()
        clock.now += timedelta(minutes=30)
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        try:
            assert await service.tick() == []
            clock.now = datetime(2026, 10, 25, 2, tzinfo=timezone.utc)
            results = await service.tick()
            assert [r.kind for r in results] == ['filter', 'pipeline']
            assert results[-1].status == 'ok' and results[-1].stats['consumed'] == 2
        finally:
            await service.aclose()
        clock.now += timedelta(minutes=5)
        data['pipelines'][0].update(schedule='5m', filter_schedule=None)
        write_config(data)
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        try:
            [result] = await service.tick()
            assert result.kind == 'pipeline' and result.status == 'no_new'
            assert await service.tick() == []
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('activation', ['enable', 'add-filter'])
def test_newly_activated_unrun_cron_job_waits_from_activation_time(write_config, web, activation):
    clock = Clock()
    data = scheduled_config(schedule='@daily' if activation == 'add-filter' else '@hourly',
                            filter_schedule=None if activation == 'add-filter' else '@hourly')
    data['sources'][0]['enabled'] = False
    data['pipelines'][0]['enabled'] = activation != 'enable'
    path = write_config(data)

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        try:
            assert await service.tick() == []
        finally:
            await service.aclose()
        clock.now = datetime(2026, 10, 25, 1, 10, tzinfo=timezone.utc)
        data['pipelines'][0].update(enabled=True, filter_schedule='@hourly')
        write_config(data)
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        try:
            assert await service.tick() == []
            assert service.list_pipelines()[0].filter_next_run_at == datetime(2026, 10, 25, 2, tzinfo=timezone.utc)
            clock.now = datetime(2026, 10, 25, 2, tzinfo=timezone.utc)
            results = await service.tick()
            assert [r.kind for r in results] == (['filter'] if activation == 'add-filter' else ['filter', 'pipeline'])
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('active_entry', ['manual', 'tick'])
def test_manual_and_tick_runs_share_one_pipeline_lease(write_config, active_entry):
    from test_models import model_config, chat_reply
    clock = Clock()
    data = {**model_config(), **scheduled_config(filter_schedule=None)}
    data['pipelines'][0]['processor'] = {'type': 'digest'}
    path = write_config(data)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model_calls = []

        async def respond(request):
            if request.url.path == '/rss':
                return httpx.Response(200, text=three_entries())
            model_calls.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(200, json=chat_reply('Research [1]. Weather [2].'))

        transport = httpx.MockTransport(respond)
        one = TentaclesService.open(path, env={}, transport=transport, clock=clock)
        two = TentaclesService.open(path, env={}, transport=transport, clock=clock)
        task = None
        try:
            await one.fetch_sources()
            task = asyncio.create_task(one.run_pipeline('daily') if active_entry == 'manual' else one.tick())
            await asyncio.wait_for(entered.wait(), 2)
            root_state = next(t for t in two.scheduler_status() if t.kind == 'pipeline')
            assert root_state.running and root_state.pending == 2
            if active_entry == 'manual':
                [waiter] = await two.tick()
                assert waiter.kind == 'pipeline' and waiter.status == 'busy'
            else:
                assert (await two.run_pipeline('daily')).status == 'busy'
            release.set()
            result = await task
            assert (result.status if active_entry == 'manual' else result[0].status) == 'ok'
            assert len(model_calls) == 1 and two.list_reports().total == 1
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            await one.aclose()
            await two.aclose()

    asyncio.run(scenario())


def test_due_pipeline_runs_are_serial_and_failure_does_not_abort_following_jobs(write_config):
    import json
    from test_models import model_config, chat_reply
    clock = Clock()
    data = {**model_config(), **scheduled_config(schedule='1h', filter_schedule=None)}
    data['pipelines'][0]['processor'] = {'type': 'digest'}
    data['pipelines'].append({**data['pipelines'][0], 'id': 'second'})
    observed = []
    active = peak = 0
    observer = None

    async def respond(request):
        nonlocal active, peak
        if request.url.path == '/rss':
            return httpx.Response(200, text=three_entries())
        pipeline_id = json.loads(json.loads(request.content)['messages'][-1]['content'])['pipeline']['id']
        active += 1
        peak = max(peak, active)
        observed.append(pipeline_id)
        await asyncio.sleep(0.02)
        clock.now += timedelta(minutes=1)
        active -= 1
        if pipeline_id == 'daily':
            return httpx.Response(500)
        assert observer.list_runs(kind='pipeline', target='daily')[0].status == 'failed'
        return httpx.Response(200, json=chat_reply('Research [1]. Weather [2].'))

    service = TentaclesService.open(write_config(data), env={}, transport=httpx.MockTransport(respond), clock=clock)
    observer = service

    async def scenario():
        try:
            results = await service.tick()
            assert [r.kind for r in results] == ['fetch', 'pipeline', 'pipeline']
            assert [r.status for r in results] == ['ok', 'error', 'ok']
            assert observed == ['daily', 'second'] and peak == 1
            assert service.list_reports().total == 1
            views = {p.id: p for p in service.list_pipelines()}
            assert views['daily'].pending == 2 and views['second'].pending == 0
            assert views['daily'].next_run_at == datetime(2026, 10, 25, 1, 10, tzinfo=timezone.utc)
            assert await service.tick() == []
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_scheduled_run_uses_previously_passed_items_when_manual_filter_is_busy(write_config, web):
    import threading
    from test_models import model_config, chat_reply, CHAT_URL
    clock = Clock()
    data = {**model_config(), **scheduled_config(filter_schedule='30m')}
    data['pipelines'][0]['processor'] = {'type': 'digest'}
    path = write_config(data)
    web.serve(FEED, three_entries())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Research [1]. Weather [2].')))

    async def scenario():
        armed = False
        entered, release = threading.Event(), threading.Event()
        observer = None

        def gated_clock():
            if armed and threading.current_thread() is not threading.main_thread() and any(
                    r.status == 'running' for r in observer.list_runs(kind='filter')):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('filter remained blocked')
            return clock()

        one = TentaclesService.open(path, env={}, transport=web.transport(), clock=gated_clock)
        observer = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        task = None
        try:
            await observer.fetch_sources()
            await observer.filter_pipeline('daily')
            armed = True
            task = asyncio.create_task(one.filter_pipeline('daily'))
            assert await asyncio.to_thread(entered.wait, 2)
            results = await observer.tick()
            assert [r.kind for r in results] == ['filter', 'pipeline']
            assert results[0].status == 'busy'
            assert results[1].status == 'ok' and results[1].stats['filter_status'] == 'busy'
            assert observer.list_reports().total == 1
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            await one.aclose()
            await observer.aclose()

    asyncio.run(scenario())


def test_tick_waits_for_every_source_before_any_pipeline_job(write_config):
    clock = Clock()
    data = scheduled_config()
    data['sources'].append({'id': 'slow', 'type': 'rss', 'url': 'https://slow.test/rss', 'schedule': '5m'})
    data['pipelines'][0]['sources'].append('slow')
    path = write_config(data)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            if request.url.host == 'slow.test':
                entered.set()
                await release.wait()
                return httpx.Response(200, text=rss_feed({'title': 'AI robotics event', 'guid': 'slow',
                                                        'description': 'Different new material'}))
            return httpx.Response(200, text=three_entries())

        transport = httpx.MockTransport(respond)
        service = TentaclesService.open(path, env={}, transport=transport, clock=clock)
        observer = TentaclesService.open(path, env={}, transport=transport, clock=clock)
        task = None
        try:
            task = asyncio.create_task(service.tick())
            await asyncio.wait_for(entered.wait(), 2)

            async def wait_for_fast_source():
                while next(s for s in observer.list_sources() if s.id == 'news').last_status != 'ok':
                    await asyncio.sleep(0.005)
            await asyncio.wait_for(wait_for_fast_source(), 2)
            assert observer.list_runs(kind='filter') == observer.list_runs(kind='pipeline') == []
            release.set()
            outcomes = await task
            assert [r.kind for r in outcomes] == ['fetch', 'fetch', 'filter', 'pipeline']
            assert outcomes[2].stats['candidates'] == 4
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            await service.aclose()
            await observer.aclose()

    asyncio.run(scenario())


def test_due_standalone_filters_are_serial(write_config, web):
    import threading
    clock = Clock()
    data = scheduled_config(schedule='@daily')
    data['sources'][0]['enabled'] = False
    data['pipelines'].append({**data['pipelines'][0], 'id': 'second'})
    path = write_config(data)
    web.serve(FEED, three_entries())

    async def scenario():
        entered, release = threading.Event(), threading.Event()
        armed = False
        observer = None

        def gated_clock():
            if armed and threading.current_thread() is not threading.main_thread() and any(
                    r.status == 'running' for r in observer.list_runs(kind='filter', target='daily')):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('first filter was not released')
            return clock()

        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=gated_clock)
        observer = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        task = None
        try:
            await observer.fetch_sources(['news'])
            armed = True
            task = asyncio.create_task(service.tick())
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.sleep(0.02)
            assert observer.list_runs(kind='filter', target='second') == []
            release.set()
            outcomes = await task
            assert [r.target for r in outcomes] == ['daily', 'second']
            assert all(r.kind == 'filter' and r.status == 'ok' for r in outcomes)
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            await service.aclose()
            await observer.aclose()

    asyncio.run(scenario())


def test_cancelled_tick_releases_pipeline_and_next_due_tick_retries(write_config):
    from test_models import model_config, chat_reply
    clock = Clock()
    data = {**model_config(), **scheduled_config(schedule='1h', filter_schedule='10m')}
    data['pipelines'][0]['processor'] = {'type': 'digest'}
    path = write_config(data)

    async def scenario():
        entered = asyncio.Event()
        blocked = True

        async def respond(request):
            if request.url.path == '/rss':
                return httpx.Response(200, text=three_entries())
            if blocked:
                entered.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json=chat_reply('Research [1]. Weather [2].'))

        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=clock)
        task = None
        try:
            task = asyncio.create_task(service.tick())
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            state = next(t for t in service.scheduler_status() if t.kind == 'pipeline')
            assert not state.running and state.last_status == 'error' and state.pending == 2
            assert state.next_run_at == clock.now + timedelta(hours=1)
            assert await service.tick() == []
            blocked = False
            clock.now += timedelta(hours=1)
            results = await service.tick()
            assert results[-1].kind == 'pipeline' and results[-1].status == 'ok'
            assert service.list_reports().total == 1
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await service.aclose()

    asyncio.run(scenario())


def test_activation_keeps_prior_manual_run_progress(write_config, web):
    clock = Clock()
    data = scheduled_config(schedule='@hourly', filter_schedule=None)
    data['sources'][0]['enabled'] = data['pipelines'][0]['enabled'] = False
    path = write_config(data)

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        try:
            assert (await service.run_pipeline('daily')).status == 'no_new'
        finally:
            await service.aclose()
        clock.now = datetime(2026, 10, 25, 1, 10, tzinfo=timezone.utc)
        data['pipelines'][0]['enabled'] = True
        write_config(data)
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        try:
            [result] = await service.tick()
            assert result.kind == 'pipeline' and result.status == 'no_new'
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_pipeline_cron_uses_configured_timezone_and_skips_dst_gap(write_config, web):
    clock = Clock(now=datetime(2026, 3, 8, 6, tzinfo=timezone.utc))
    data = scheduled_config(schedule='30 2 * * *', filter_schedule=None)
    data['app'] = {'timezone': 'America/New_York'}
    data['sources'][0]['enabled'] = False
    service = TentaclesService.open(write_config(data), env={}, transport=web.transport(), clock=clock)

    async def scenario():
        try:
            expected = datetime(2026, 3, 9, 6, 30, tzinfo=timezone.utc)
            assert service.list_pipelines()[0].next_run_at == expected
            clock.now = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
            assert await service.tick() == []
            clock.now = expected
            [result] = await service.tick()
            assert result.kind == 'pipeline' and result.status == 'no_new'
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_unrun_cron_catches_up_after_restart_and_history_only_config_does_not_reset_it(write_config, web):
    clock = Clock()
    data = scheduled_config(schedule='@hourly', filter_schedule=None)
    data['sources'][0]['enabled'] = False
    path = write_config(data)

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        try:
            assert await service.tick() == []
        finally:
            await service.aclose()
        clock.now += timedelta(minutes=30)
        history_path = write_config({}, name='history.yaml')
        history = TentaclesService.open(history_path, env={}, transport=web.transport(), clock=clock)
        try:
            assert history.list_pipelines() == []
        finally:
            await history.aclose()
        clock.now = datetime(2026, 10, 25, 1, 10, tzinfo=timezone.utc)
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)
        try:
            [outcome] = await service.tick()
            assert outcome.kind == 'pipeline' and outcome.status == 'no_new'
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('schedule', ['0s', '@yearly', '60 * * * *', 'every hour'])
def test_invalid_filter_schedules_fail_at_config_loading(write_config, schedule):
    from tentacles.errors import ConfigError
    with pytest.raises(ConfigError, match=r'pipelines\[0\].filter_schedule'):
        TentaclesService.open(write_config(scheduled_config(filter_schedule=schedule)), env={})


def test_v6_upgrade_preserves_manual_progress_and_waits_for_first_automatic_cron(write_config, tmp_path, web):
    import sqlite3
    from pathlib import Path
    clock = Clock(now=datetime(2030, 1, 1, 1, 10, tzinfo=timezone.utc))
    data = scheduled_config(schedule='@hourly', filter_schedule='@hourly')
    data['sources'][0]['enabled'] = False
    path = write_config(data)
    database = tmp_path / 'data' / 'tentacles.db'
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.executescript((Path(__file__).parent/'fixtures'/'pipeline-schedules-v6.sql').read_text())
    service = TentaclesService.open(path, env={}, transport=web.transport(), clock=clock)

    async def scenario():
        try:
            assert service.get_persona('default').profile == 'Legacy interests'
            [pipeline] = service.list_pipelines()
            assert pipeline.next_run_at == datetime(2030, 1, 1, 2, tzinfo=timezone.utc)
            assert pipeline.filter_next_run_at == datetime(2030, 1, 1, 1, tzinfo=timezone.utc)
            [filtered] = await service.tick()
            assert filtered.kind == 'filter'
            clock.now = datetime(2030, 1, 1, 2, tzinfo=timezone.utc)
            assert [r.kind for r in await service.tick()] == ['filter', 'pipeline']
            assert web.requests == []
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_overlapping_tick_defers_dependent_jobs_until_owning_fetch_finishes(write_config):
    from test_models import model_config, chat_reply
    clock = Clock()
    data = {**model_config(), **scheduled_config()}
    data['pipelines'][0].update(window='30m', processor={'type': 'digest'})
    path = write_config(data)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            if request.url.path == '/rss':
                entered.set()
                await release.wait()
                return httpx.Response(200, text=three_entries())
            return httpx.Response(200, json=chat_reply('Research [1]. Weather [2].'))

        transport = httpx.MockTransport(respond)
        one = TentaclesService.open(path, env={}, transport=transport, clock=clock)
        two = TentaclesService.open(path, env={}, transport=transport, clock=clock)
        task = None
        try:
            task = asyncio.create_task(one.tick())
            await asyncio.wait_for(entered.wait(), 2)
            clock.now += timedelta(minutes=1)
            waiting = await two.tick()
            assert [(r.kind, r.status) for r in waiting] == [('fetch', 'busy')]
            assert two.list_runs(kind='filter') == two.list_runs(kind='pipeline') == []
            clock.now += timedelta(minutes=1)
            release.set()
            owner = await task
            assert [r.kind for r in owner] == ['fetch', 'filter', 'pipeline']
            assert owner[-1].status == 'ok' and two.list_reports().total == 1
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            await one.aclose()
            await two.aclose()

    asyncio.run(scenario())


def test_busy_source_only_defers_its_dependent_pipelines(write_config):
    from test_models import model_config, chat_reply
    clock = Clock()
    data = {**model_config(), **scheduled_config()}
    data['sources'].append({'id': 'other', 'type': 'rss', 'url': 'https://other.test/rss',
                            'enabled': False, 'schedule': '1h'})
    data['pipelines'][0]['processor'] = {'type': 'digest'}
    data['pipelines'].append({**data['pipelines'][0], 'id': 'independent', 'sources': ['other']})
    path = write_config(data)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            if request.url.host == 'other.test':
                return httpx.Response(200, text=rss_feed(
                    {'title': 'AI astronomy mission', 'guid': 'independent-a', 'description': 'New telescope'},
                    {'title': 'Independent cooking tips', 'guid': 'independent-b', 'description': 'Cooking guide'}))
            if request.url.path == '/rss':
                entered.set()
                await release.wait()
                return httpx.Response(200, text=three_entries())
            return httpx.Response(200, json=chat_reply('Material [1]. More [2].'))

        transport = httpx.MockTransport(respond)
        one = TentaclesService.open(path, env={}, transport=transport, clock=clock)
        two = TentaclesService.open(path, env={}, transport=transport, clock=clock)
        task = None
        try:
            await one.fetch_sources(['other'])
            task = asyncio.create_task(one.tick())
            await asyncio.wait_for(entered.wait(), 2)
            waiting = await two.tick()
            assert [(r.kind, r.target) for r in waiting] == [
                ('fetch', 'news'), ('filter', 'independent'), ('pipeline', 'independent')]
            assert waiting[-1].status == 'ok'
            assert two.list_runs(kind='pipeline', target='daily') == []
            release.set()
            assert (await task)[-1].status == 'ok'
            assert two.list_reports().total == 2
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            await one.aclose()
            await two.aclose()

    asyncio.run(scenario())
