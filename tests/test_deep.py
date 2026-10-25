"""Deep processing at Service; outbound HTTP is the only model substitute."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from conftest import rss_feed
from test_models import CHAT_URL, chat_reply, model_config
from test_pipelines import FEED, pipeline_config


def deep_config(**processor):
    return {**model_config(), **pipeline_config(filters=[], processor={'type': 'deep', **processor})}


def test_below_threshold_consumes_without_empty_report_or_model_key(web, write_config, open_service):
    web.serve(FEED, rss_feed({'title': 'Lonely weather bulletin', 'guid': 'weather'}))
    data = deep_config()
    data['models']['fast']['api_key'] = ''
    service = open_service(write_config(data))

    async def scenario():
        await service.fetch_sources()
        result = await service.run_pipeline('daily')
        assert result.status == 'ok' and result.report_id is None
        assert result.stats['consumed'] == 1
        assert service.list_reports().total == 0
        [item] = service.list_items().items
        assert service.get_item(item.id).evaluations[0].reported_at is not None
        assert (await service.run_pipeline('daily')).status == 'no_new'

    asyncio.run(scenario())


TITLES = [
    '央行宣布下调存款利率', '央行下调存款利率0.25个百分点', '央行宣布下调存款利率，年内第三次',
    '轨道空间站启动扩建实验舱计划', '轨道空间站宣布扩建实验舱', '新一轮空间站扩建将新增实验舱',
    '今天本地花园气温记录',
]


def event_feed():
    return rss_feed(*[{'title': title, 'guid': f'event-{i}', 'link': f'https://news.test/{i}',
                      'description': f'独立材料 {i}'} for i, title in enumerate(TITLES)])


def test_eligible_events_generate_separate_reports_and_consume_even_unreported_items(web, write_config, open_service):
    web.serve(FEED, event_feed())

    def respond(request):
        body = json.loads(request.content)
        assert 'investigative' in body['messages'][0]['content']
        assert '背景 / 各方说法 / 数据与疑点 / 影响与后续' in body['messages'][0]['content']
        assert '尚待确认的问题' in body['messages'][0]['content']
        return httpx.Response(200, json=chat_reply('## 导语\n\n材料事实 [来源1]。\n\n## 尚待确认的问题\n\n需要核实 [来源2]。'))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(deep_config()))

    async def scenario():
        assert (await service.fetch_sources())[0].new == 7
        result = await service.run_pipeline('daily')
        assert result.status == 'ok' and result.report_id
        reports = service.list_reports().items
        assert len(reports) == 2
        assert result.report_id in {r.id for r in reports}
        assert set(result.stats['report_ids']) == {r.id for r in reports}
        assert sorted(len(r.item_ids) for r in reports) == [3, 3]
        titles = {item.id: item.title for item in service.list_items().items}
        for report in reports:
            assert report.title.startswith('深度报道：') and report.kind == 'deep'
            assert report.title.removeprefix('深度报道：') in {titles[i] for i in report.item_ids}
            assert report.usage.total_tokens == 15 and report.cost == 0.01
            assert '[来源3]' in report.markdown and '## Sources' in report.markdown
            assert Path(report.path).read_text(encoding='utf-8') == report.markdown
            assert Path(report.path).stat().st_mode & 0o777 == 0o600
        assert result.stats['consumed'] == 7 and service.list_pipelines()[0].pending == 0
        for item in service.list_items().items:
            assert service.get_item(item.id).evaluations[0].reported_at is not None
        run = next(r for r in service.list_runs(kind='pipeline') if r.id == result.run_id)
        assert run.cost == 0.02 and run.stats['usage']['total_tokens'] == 30
        assert (await service.run_pipeline('daily')).status == 'no_new'
        await service.set_persona('default', profile='New priorities', skip_tags=True)
        await service.filter_pipeline('daily', reevaluate=True, since='7d')
        assert (await service.run_pipeline('daily')).status == 'no_new'
        assert service.list_reports().total == 2

    asyncio.run(scenario())


@pytest.mark.parametrize('minimum,maximum,count', [(4, 3, 0), (3, 1, 1), (1, 3, 3)])
def test_configurable_threshold_and_report_cap_still_consume_all_candidates(
        web, write_config, open_service, minimum, maximum, count):
    web.serve(FEED, event_feed())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('报道依据 [来源1]。')))
    service = open_service(write_config(deep_config(min_cluster_size=minimum, max_reports=maximum)))

    async def scenario():
        await service.fetch_sources()
        result = await service.run_pipeline('daily')
        assert result.status == 'ok' and result.stats['consumed'] == 7
        assert service.list_reports().total == count
        if count == 1:
            assert len(service.get_report(result.report_id).item_ids) == 3  # largest event, not singleton
        assert service.list_pipelines()[0].pending == 0
        for item in service.list_items().items:
            assert service.get_item(item.id).evaluations[0].reported_at is not None
        assert (await service.run_pipeline('daily')).status == 'no_new'

    asyncio.run(scenario())


@pytest.mark.parametrize('processor', ['deep', 'none'])
def test_processor_item_cap_leaves_only_unselected_items_pending(web, write_config, open_service, processor):
    web.serve(FEED, event_feed())
    data = deep_config(type=processor, max_items=2, min_cluster_size=3)
    data['models']['fast']['api_key'] = ''
    service = open_service(write_config(data))

    async def scenario():
        await service.fetch_sources()
        for expected, pending in [(2, 5), (2, 3), (2, 1), (1, 0)]:
            result = await service.run_pipeline('daily')
            assert result.status == 'ok' and result.stats['consumed'] == expected
            assert result.report_id is None and result.stats['report_ids'] == []
            assert service.list_pipelines()[0].pending == pending
        assert service.list_reports().total == 0
        assert (await service.run_pipeline('daily')).status == 'no_new'

    asyncio.run(scenario())


def test_prompt_override_uses_event_scoped_items_and_model_override(web, write_config, open_service):
    data = deep_config(min_cluster_size=1, model='max', language='fr', instructions='Reste bref',
                       prompt_file='deep.prompt')
    data['models']['max'] = {**data['models']['fast'], 'model': 'max-chat'}
    path = write_config(data)
    (path.parent / 'deep.prompt').write_text('[system]\nCustom investigation\n[user]\n'
        'PROFILE={{ persona }}\nPIPE={{ pipeline.id }}\nTIME={{ now }}\n'
        '{% for item in items %}[{{ item.number }}] {{ item.title }} {{ item.summary }} {{ item.url }}\n{% endfor %}',
        encoding='utf-8')
    web.serve(FEED, rss_feed({'title': 'Unique bulletin', 'guid': 'one', 'link': 'https://news.test/one',
                            'description': 'Excerpt ' * 300}))

    def respond(request):
        body = json.loads(request.content)
        assert body['model'] == 'max-chat'
        assert 'Custom investigation' in body['messages'][0]['content']
        assert 'fr' in body['messages'][0]['content'] and 'Reste bref' in body['messages'][0]['content']
        user = body['messages'][1]['content']
        assert 'PROFILE=Science and AI first' in user and 'PIPE=daily' in user
        assert '[1] Unique bulletin' in user and 'https://news.test/one' in user
        assert len(user) < 1450  # source summary bounded to 1200 chars
        return httpx.Response(200, json=chat_reply('Analyse [1]. private-test-key'))

    web.handle(CHAT_URL, respond)
    service = open_service(path)

    async def scenario():
        await service.fetch_sources()
        result = await service.run_pipeline('daily')
        assert result.status == 'ok'
        report = service.get_report(result.report_id)
        assert report.model == 'actual-chat' and report.title == '深度报道：Unique bulletin'
        assert 'Analyse [1]' in report.markdown and 'private-test-key' not in report.markdown
        assert Path(report.path).read_text(encoding='utf-8') == report.markdown

    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['http', 'citation', 'empty', 'body'])
def test_later_event_failure_publishes_nothing_preserves_usage_and_retries_entire_batch(
        web, write_config, open_service, failure):
    web.serve(FEED, event_feed())
    responses = [httpx.Response(200, json=chat_reply('First event [来源1].'))]
    if failure == 'http':
        responses.append(httpx.Response(500, text='private-test-key private upstream details'))
        expected_cost, expected_tokens = 0.01, 15
    else:
        reply = chat_reply({'citation': 'Invented [来源99].', 'empty': '', 'body': 'unused'}[failure])
        if failure == 'body':
            reply['choices'] = []
        responses.append(httpx.Response(200, json=reply))
        expected_cost, expected_tokens = 0.02, 30

    def respond(request):
        return responses.pop(0) if responses else httpx.Response(200, json=chat_reply('Recovered [来源1].'))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(deep_config()))

    async def scenario():
        await service.fetch_sources()
        result = await service.run_pipeline('daily')
        assert result.status == 'error' and result.report_id is None
        assert 'private' not in result.error
        assert service.list_reports().total == 0 and service.list_pipelines()[0].pending == 7
        assert not list(service.config.reports_dir.rglob('*.md'))
        for item in service.list_items().items:
            assert service.get_item(item.id).evaluations[0].reported_at is None
        run = next(r for r in service.list_runs(kind='pipeline') if r.id == result.run_id)
        assert run.status == 'failed' and run.cost == expected_cost
        assert run.stats['usage']['total_tokens'] == expected_tokens
        result = await service.run_pipeline('daily')
        assert result.status == 'ok' and result.stats['consumed'] == 7
        assert service.list_reports().total == 2

    asyncio.run(scenario())


@pytest.mark.parametrize('field,value', [('min_cluster_size', 0), ('min_cluster_size', 501),
                                         ('max_reports', 0), ('max_reports', 501)])
def test_deep_bounds_fail_at_configuration_load(write_config, open_service, field, value):
    from tentacles.errors import ConfigError
    with pytest.raises(ConfigError, match=field):
        open_service(write_config(deep_config(**{field: value})))


def test_later_sql_failure_rolls_back_all_reports_files_and_consumption(web, write_config, open_service):
    import sqlite3
    web.serve(FEED, event_feed())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Confirmed [来源1].')))
    service = open_service(write_config(deep_config()))

    async def scenario():
        await service.fetch_sources()
        with sqlite3.connect(service.config.db_path) as conn:
            conn.execute('''CREATE TRIGGER fail_second_deep BEFORE INSERT ON reports
                WHEN NEW.kind = 'deep' AND (SELECT COUNT(*) FROM reports) = 1
                BEGIN SELECT RAISE(ABORT, 'injected disk database failure'); END''')
        result = await service.run_pipeline('daily')
        assert result.status == 'error' and result.report_id is None
        assert service.list_reports().total == 0
        assert not list(service.config.reports_dir.rglob('*.md'))
        assert service.list_pipelines()[0].pending == 7
        run = next(r for r in service.list_runs(kind='pipeline') if r.id == result.run_id)
        assert run.status == 'failed' and run.cost == 0.02 and run.stats['consumed'] == 0
        with sqlite3.connect(service.config.db_path) as conn:
            conn.execute('DROP TRIGGER fail_second_deep')
        assert (await service.run_pipeline('daily')).status == 'ok'
        assert service.list_reports().total == 2

    asyncio.run(scenario())


def test_cancelled_later_event_keeps_entire_batch_pending_and_releases_lease(web, write_config):
    from tentacles.service import TentaclesService
    path = write_config(deep_config())
    web.serve(FEED, event_feed())

    async def scenario():
        entered = asyncio.Event()
        responses = 0
        blocked = True

        async def respond(request):
            nonlocal responses
            if request.url == FEED:
                return httpx.Response(200, text=event_feed())
            responses += 1
            if blocked and responses == 2:
                entered.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json=chat_reply('Evidence [来源1].'))

        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
        task = None
        try:
            await service.fetch_sources()
            task = asyncio.create_task(service.run_pipeline('daily'))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert service.list_reports().total == 0 and service.list_pipelines()[0].pending == 7
            assert not list(service.config.reports_dir.rglob('*.md'))
            run = service.list_runs(kind='pipeline')[0]
            assert run.status == 'failed' and run.cost == 0.01 and run.stats['usage']['total_tokens'] == 15
            blocked = False
            assert (await service.run_pipeline('daily')).status == 'ok'
            assert service.list_reports().total == 2
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await service.aclose()

    asyncio.run(scenario())


def test_expired_batch_owner_cannot_publish_over_replacement_or_consume_twice(web, write_config):
    from datetime import datetime, timedelta, timezone
    from tentacles.service import TentaclesService
    path = write_config(deep_config())
    now = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
    web.serve(FEED, event_feed())

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        responses = 0

        async def respond(request):
            nonlocal responses
            responses += 1
            if responses == 2:
                entered.set()
                await release.wait()
            return httpx.Response(200, json=chat_reply('Facts [来源1].'))

        fetcher = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: now[0])
        first = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: now[0])
        second = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: now[0])
        task = None
        try:
            await fetcher.fetch_sources()
            task = asyncio.create_task(first.run_pipeline('daily'))
            await asyncio.wait_for(entered.wait(), 2)
            now[0] += timedelta(hours=1)
            winner = await second.run_pipeline('daily')
            assert winner.status == 'ok' and len(winner.stats['report_ids']) == 2
            release.set()
            loser = await task
            assert loser.status == 'error' and loser.report_id is None
            assert second.list_reports().total == 2
            assert len(list(second.config.reports_dir.rglob('*.md'))) == 2
            assert second.list_pipelines()[0].last_status == 'ok'
            assert second.list_pipelines()[0].pending == 0
            abandoned = next(r for r in second.list_runs(kind='pipeline') if r.id == loser.run_id)
            assert abandoned.status == 'failed' and abandoned.cost == 0.02
            assert abandoned.stats['usage']['total_tokens'] == 30
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            for service in [fetcher, first, second]:
                await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('cancel_again', [False, True])
def test_cancel_during_batch_publication_drains_and_preserves_whole_commit(web, write_config, cancel_again):
    import sqlite3
    import threading
    from tentacles.service import TentaclesService
    path = write_config(deep_config())
    web.serve(FEED, event_feed())

    async def scenario():
        connection = timer = task = None
        responses = 0

        def respond(request):
            nonlocal connection, timer, responses
            responses += 1
            if responses == 2:
                connection = sqlite3.connect(service.config.db_path, check_same_thread=False)
                connection.execute('BEGIN IMMEDIATE')
                timer = threading.Timer(0.2, connection.commit)
                timer.start()
                asyncio.get_running_loop().call_later(0.03, task.cancel)
                if cancel_again:
                    asyncio.get_running_loop().call_later(0.06, task.cancel)
            return httpx.Response(200, json=chat_reply('Facts [来源1].'))

        web.handle(CHAT_URL, respond)
        service = TentaclesService.open(path, env={}, transport=web.transport())
        try:
            await service.fetch_sources()
            task = asyncio.create_task(service.run_pipeline('daily'))
            with pytest.raises(asyncio.CancelledError):
                await task
            reports = service.list_reports().items
            assert len(reports) == 2
            for report in reports:
                assert Path(report.path).read_text(encoding='utf-8') == report.markdown
            assert service.list_runs(kind='pipeline')[0].status == 'ok'
            assert service.list_pipelines()[0].pending == 0
            assert (await service.run_pipeline('daily')).status == 'no_new'
        finally:
            if timer:
                timer.join()
            if connection:
                connection.close()
            await service.aclose()

    asyncio.run(scenario())
