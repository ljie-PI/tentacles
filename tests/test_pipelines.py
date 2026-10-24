"""Pipelines end-to-end at Service; only HTTP/time are replaced."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from conftest import rss_feed
from tentacles.errors import ConfigError, NotFound
from tentacles.service import TentaclesService

FEED = 'https://news.example.com/rss'


def pipeline_config(*, filters=None, processor=None, **pipeline):
    return {'sources': [{'id': 'news', 'type': 'rss', 'url': FEED, 'schedule': '1h'}],
            'personas': {'default': {'profile': 'Science and AI first', 'include': ['AI'],
                                     'exclude': ['Advertisement']}},
            'pipelines': [{'id': 'daily', 'persona': 'default', 'sources': ['news'], 'window': '24h',
                'filters': [{'type': 'keyword'}] if filters is None else filters,
                'processor': {'type': 'none'} if processor is None else processor, **pipeline}]}


def three_entries():
    return rss_feed(
        {'title': 'AI research launch', 'guid': 'ai', 'description': 'Included science research'},
        {'title': 'Open garden weather', 'guid': 'neutral', 'description': 'Neutral overview'},
        {'title': 'Advertisement AI sale', 'guid': 'ad', 'description': 'Spam campaign'})


def test_keyword_filter_records_scores_and_pipeline_queries(web, write_config, open_service):
    web.serve(FEED, three_entries())
    service = open_service(write_config(pipeline_config()))

    async def scenario():
        assert (await service.fetch_sources())[0].new == 3
        outcome = await service.filter_pipeline('daily')
        assert outcome.status == 'ok'
        assert (outcome.stats['candidates'], outcome.stats['evaluated']) == (3, 3)
        items = service.list_items(pipeline='daily').items
        by_title = {item.title: item for item in items}
        assert by_title['AI research launch'].score == 1
        assert by_title['Open garden weather'].score == 0.5
        assert by_title['Advertisement AI sale'].score == 0
        assert by_title['Advertisement AI sale'].passed is False
        assert all(item.stage == 'keyword' and len(item.stage_scores) == 1 for item in items)
        assert service.list_items(pipeline='daily', passed=True).total == 2
        assert service.list_items(pipeline='daily', passed=False).total == 1
        again = await service.filter_pipeline('daily')
        assert again.stats['candidates'] == 0

    asyncio.run(scenario())


def test_first_run_writes_report_and_second_run_does_not_repeat(web, write_config, open_service):
    from test_models import model_config, chat_reply, CHAT_URL
    web.serve(FEED, three_entries())

    def respond(request):
        body = json.loads(request.content)
        assert any('Science and AI first' in message['content'] for message in body['messages'])
        assert body['model'] == 'test-chat'
        return httpx.Response(200, json=chat_reply('## 今日要闻\n\nResearch [1]. Weather [2].'))

    web.handle(CHAT_URL, respond)
    config = {**model_config(), **pipeline_config(processor={'type': 'digest'})}
    service = open_service(write_config(config))

    async def scenario():
        await service.fetch_sources()
        first = await service.run_pipeline('daily')
        assert first.status == 'ok' and first.report_id
        report = service.get_report(first.report_id)
        assert report.pipeline_id == 'daily' and report.kind == 'digest'
        assert 'Research [1]' in report.markdown and 'AI research launch' in report.markdown
        assert len(report.item_ids) == 2 and report.usage.total_tokens == 15 and report.cost == 0.01
        assert Path(report.path).parent.name == 'daily'
        assert Path(report.path).read_text(encoding='utf-8') == report.markdown
        assert all(service.get_item(item_id).evaluations[0].reported_at for item_id in report.item_ids)
        assert service.list_reports(pipeline='daily').total == 1
        second = await service.run_pipeline('daily')
        assert second.status == 'no_new' and second.report_id is None
        assert service.list_reports().total == 1
        run = next(r for r in service.list_runs(kind='pipeline') if r.id == first.run_id)
        assert run.status == 'ok' and run.cost == 0.01 and run.stats['usage']['total_tokens'] == 15

    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['http', 'citations'])
def test_report_failure_leaves_items_pending_and_retry_can_succeed(web, write_config, open_service, failure):
    from test_models import model_config, chat_reply, CHAT_URL
    web.serve(FEED, three_entries())
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            if failure == 'http':
                return httpx.Response(500, text='private provider response')
            return httpx.Response(200, json=chat_reply('Bad source [99]'))
        return httpx.Response(200, json=chat_reply('Valid research [1] and weather [2].'))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})}))

    async def scenario():
        await service.fetch_sources()
        failed = await service.run_pipeline('daily')
        assert failed.status == 'error' and failed.report_id is None
        assert service.list_reports().total == 0
        passed = service.list_items(pipeline='daily', passed=True).items
        assert all(service.get_item(item.id).evaluations[0].reported_at is None for item in passed)
        run = next(r for r in service.list_runs(kind='pipeline') if r.id == failed.run_id)
        assert run.status == 'failed' and run.stats.get('consumed', 0) == 0
        if failure == 'citations':
            assert run.cost == 0.01 and run.stats['usage']['total_tokens'] == 15
        retried = await service.run_pipeline('daily')
        assert retried.status == 'ok' and retried.report_id
        assert service.list_reports().total == 1

    asyncio.run(scenario())


def test_digest_renders_sectioned_custom_prompt_language_and_instructions(web, write_config, open_service):
    from test_models import model_config, chat_reply, CHAT_URL
    path = write_config({**model_config(), **pipeline_config(processor={
        'type': 'digest', 'language': 'fr', 'instructions': 'Be concise', 'prompt_file': 'digest.prompt'})})
    (path.parent / 'digest.prompt').write_text('[system]\nCustom editor\n[user]\n'
        'PROFILE={{ persona }}\nPIPE={{ pipeline.id }}\nTIME={{ now }}\n'
        '{% for item in items %}[{{ item.number }}] {{ item.title }}\n{% endfor %}', encoding='utf-8')
    web.serve(FEED, three_entries())

    def respond(request):
        body = json.loads(request.content)
        system, user = body['messages'][0]['content'], body['messages'][1]['content']
        assert 'Custom editor' in system and 'fr' in system and 'Be concise' in system
        assert 'PROFILE=Science and AI first' in user and 'PIPE=daily' in user
        assert '[1] AI research launch' in user and '[2] Open garden weather' in user
        return httpx.Response(200, json=chat_reply('Recherche [1]. Météo [2].'))

    web.handle(CHAT_URL, respond)
    service = open_service(path)

    async def scenario():
        await service.fetch_sources()
        result = await service.run_pipeline('daily')
        assert result.status == 'ok'
        assert '日报' not in service.get_report(result.report_id).title

    asyncio.run(scenario())


@pytest.mark.parametrize('stages,score,passed,traces', [
    ([{'type': 'keyword', 'min_score': 0.6}], 0.5, False, 1),
    ([{'type': 'keyword', 'reject_below': 0.4, 'min_score': 0.9}], 0.5, True, 1),
    ([{'type': 'keyword', 'accept_above': 0.5}, {'type': 'keyword', 'include': ['garden']}], 0.5, True, 1),
    ([{'type': 'keyword'}, {'type': 'keyword', 'include': ['GARDEN']}], 1.0, True, 2),
    ([], 1.0, True, 0),
])
def test_cascade_thresholds_forwarding_and_no_stage_behavior(web, write_config, open_service, stages, score, passed, traces):
    web.serve(FEED, rss_feed({'title': 'Open garden weather', 'guid': 'n', 'description': 'Neutral overview'}))
    service = open_service(write_config(pipeline_config(filters=stages)))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        [item] = service.list_items(pipeline='daily').items
        assert (item.score, item.passed, len(item.stage_scores)) == (score, passed, traces)
        assert result.stats['evaluated'] == 1
        if len(stages) == 2:
            expected = 0 if traces == 1 else 1
            assert result.stats['stages'][0]['forwarded'] == expected
            assert result.stats['stages'][1]['passed'] == expected

    asyncio.run(scenario())


def test_explicit_zero_rejection_takes_precedence_over_min_score(web, write_config, open_service):
    web.serve(FEED, three_entries())
    service = open_service(write_config(pipeline_config(filters=[{'type': 'keyword', 'reject_below': 0, 'min_score': 1}])))
    asyncio.run(service.fetch_sources())
    asyncio.run(service.filter_pipeline('daily'))
    assert service.list_items(pipeline='daily', passed=True).total == 3


def test_stage_error_forwards_but_final_error_is_not_cached(web, write_config):
    data = pipeline_config(filters=[{'type': 'keyword', 'reject_below': 0}, {'type': 'keyword', 'regex': True}])
    data['personas']['default']['include'] = ['[']
    data['personas']['default']['exclude'] = []
    path = write_config(data)
    web.serve(FEED, three_entries())

    async def scenario():
        service = TentaclesService.open(path, transport=web.transport(), env={})
        try:
            await service.fetch_sources()
            result = await service.filter_pipeline('daily')
            assert result.stats['evaluated'] == 0 and result.stats['errors'] == 3
            assert result.stats['stages'][0]['forwarded'] == 3
            assert result.stats['stages'][1]['errors'] == 3
            assert service.list_items(pipeline='daily').total == 0
        finally:
            await service.aclose()
        data['pipelines'][0]['filters'] = [{'type': 'keyword', 'regex': True}, {'type': 'keyword'}]
        write_config(data)
        service = TentaclesService.open(path, transport=web.transport(), env={})
        try:
            result = await service.filter_pipeline('daily')
            assert result.stats['candidates'] == result.stats['evaluated'] == 3
            assert result.stats['stages'][0]['errors'] == result.stats['stages'][0]['forwarded'] == 3
            assert all(item.stage_scores[0].score is None for item in service.list_items(pipeline='daily').items)
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_valid_regex_matches_title_and_summary_ignoring_case(web, write_config, open_service):
    web.serve(FEED, rss_feed({'title': 'Neutral update', 'guid': 'x', 'description': 'Neural NETWORK progress'}))
    service = open_service(write_config(pipeline_config(filters=[{
        'type': 'keyword', 'regex': True, 'include': ['neural\\s+network'], 'exclude': ['SPAM.*SALE']}])) )
    asyncio.run(service.fetch_sources())
    asyncio.run(service.filter_pipeline('daily'))
    [item] = service.list_items(pipeline='daily').items
    assert item.score == 1 and item.passed


def test_pathological_regex_is_an_uncached_stage_error_not_a_hung_pipeline(web, write_config, open_service):
    data = pipeline_config(filters=[{'type': 'keyword', 'regex': True, 'include': ['(a|aa)+$']}])
    data['personas']['default']['include'] = []
    data['personas']['default']['exclude'] = []
    web.serve(FEED, rss_feed({'title': 'a' * 1000 + '!', 'guid': 'slow', 'description': 'text'}))
    service = open_service(write_config(data))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.stats['evaluated'] == 0 and result.stats['errors'] == 1
        assert service.list_items(pipeline='daily').total == 0

    asyncio.run(scenario())


def test_caps_pending_count_none_processor_and_explicit_reevaluation(web, write_config):
    data = pipeline_config(max_items=1, processor={'type': 'none', 'max_items': 1})
    path = write_config(data)
    web.serve(FEED, three_entries())

    async def scenario():
        service = TentaclesService.open(path, transport=web.transport(), env={})
        try:
            await service.fetch_sources()
            for _ in range(3):
                assert (await service.filter_pipeline('daily')).stats['candidates'] == 1
            [view] = service.list_pipelines()
            assert view.pending == 2
            assert (await service.run_pipeline('daily')).status == 'ok'
            assert service.list_pipelines()[0].pending == 1
            assert service.list_reports().total == 0
        finally:
            await service.aclose()
        data['pipelines'][0]['max_items'] = 10
        data['pipelines'][0]['filters'][0]['min_score'] = 0.6
        write_config(data)
        service = TentaclesService.open(path, transport=web.transport(), env={})
        try:
            assert (await service.filter_pipeline('daily')).stats['candidates'] == 0
            assert service.list_items(pipeline='daily', passed=True).total == 2
            forced = await service.filter_pipeline('daily', reevaluate=True)
            assert forced.stats['candidates'] == 3
            assert service.list_items(pipeline='daily', passed=True).total == 1
            assert service.list_pipelines()[0].pending == 1
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_invalid_pipeline_startup_does_not_freeze_a_seed_that_never_started(write_config):
    data = pipeline_config(persona='missing')
    data['personas']['default']['profile'] = 'Bad startup seed'
    path = write_config(data)
    with pytest.raises(ConfigError, match='persona'):
        TentaclesService.open(path, env={})
    data['pipelines'][0]['persona'] = 'default'
    data['personas']['default']['profile'] = 'Corrected seed'
    write_config(data)
    service = TentaclesService.open(path, env={})
    try:
        assert service.get_persona('default').profile == 'Corrected seed'
    finally:
        asyncio.run(service.aclose())


def test_concurrent_pipeline_runs_return_busy_across_services(web, write_config):
    from test_models import model_config, chat_reply
    path = write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})})
    web.serve(FEED, three_entries())

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=chat_reply('Research [1]. Weather [2].'))

        one = TentaclesService.open(path, env={}, transport=web.transport())
        two = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
        task = None
        try:
            await one.fetch_sources()
            task = asyncio.create_task(two.run_pipeline('daily'))
            await asyncio.wait_for(entered.wait(), 2)
            assert (await one.run_pipeline('daily')).status == 'busy'
            release.set()
            assert (await task).status == 'ok'
            assert one.list_reports().total == 1
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            await one.aclose()
            await two.aclose()

    asyncio.run(scenario())


def test_run_skips_busy_filter_and_processes_already_passed_items(web, write_config):
    import threading
    from datetime import datetime, timezone
    from test_models import model_config, chat_reply, CHAT_URL
    path = write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})})
    web.serve(FEED, three_entries())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Research [1]. Weather [2].')))

    async def scenario():
        armed = False
        entered, release = threading.Event(), threading.Event()
        observer = None

        def clock():
            if armed and any(r.status == 'running' for r in observer.list_runs(kind='filter')):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('filter did not release')
            return datetime.now(timezone.utc)

        one = TentaclesService.open(path, env={}, clock=clock, transport=web.transport())
        observer = TentaclesService.open(path, env={}, transport=web.transport())
        task = None
        try:
            await observer.fetch_sources()
            await observer.filter_pipeline('daily')
            armed = True
            task = asyncio.create_task(one.filter_pipeline('daily'))
            assert await asyncio.to_thread(entered.wait, 2)
            assert (await observer.filter_pipeline('daily')).status == 'busy'
            result = await observer.run_pipeline('daily')
            assert result.status == 'ok' and result.stats['filter_status'] == 'busy'
            release.set()
            assert (await task).status == 'ok'
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            await one.aclose()
            await observer.aclose()

    asyncio.run(scenario())


def test_publish_sql_failure_rolls_back_file_record_and_consumption(web, write_config):
    import sqlite3
    from test_models import model_config, chat_reply, CHAT_URL
    web.serve(FEED, three_entries())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Research [1]. Weather [2].')))
    service = TentaclesService.open(write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})}),
                                    env={}, transport=web.transport())
    connection = sqlite3.connect(service.config.db_path)
    connection.executescript("""
        CREATE TRIGGER reject_report BEFORE INSERT ON reports
        BEGIN SELECT RAISE(ABORT, 'test report insert failure'); END;
    """)

    async def scenario():
        try:
            await service.fetch_sources()
            failed = await service.run_pipeline('daily')
            assert failed.status == 'error'
            assert service.list_reports().total == 0
            assert list(service.config.reports_dir.rglob('*.md')) == []
            assert service.list_pipelines()[0].pending == 2
            connection.execute('DROP TRIGGER reject_report')
            assert (await service.run_pipeline('daily')).status == 'ok'
            assert service.list_reports().total == 1
            assert len(list(service.config.reports_dir.rglob('*.md'))) == 1
        finally:
            connection.close()
            await service.aclose()

    asyncio.run(scenario())


def test_filesystem_failure_is_retryable_and_does_not_consume(web, write_config):
    from test_models import model_config, chat_reply, CHAT_URL
    web.serve(FEED, three_entries())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Research [1]. Weather [2].')))
    service = TentaclesService.open(write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})}),
                                    env={}, transport=web.transport())
    service.config.reports_dir.write_text('not a directory', encoding='utf-8')

    async def scenario():
        try:
            await service.fetch_sources()
            assert (await service.run_pipeline('daily')).status == 'error'
            assert service.list_pipelines()[0].pending == 2
            assert service.list_reports().total == 0
            service.config.reports_dir.unlink()
            assert (await service.run_pipeline('daily')).status == 'ok'
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_cancelled_model_call_keeps_pending_items_and_releases_pipeline_lock(web, write_config):
    from test_models import model_config, chat_reply
    path = write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})})
    web.serve(FEED, three_entries())

    async def scenario():
        entered = asyncio.Event()
        blocked = True

        async def respond(request):
            if blocked:
                entered.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json=chat_reply('Research [1]. Weather [2].'))

        one = TentaclesService.open(path, env={}, transport=web.transport())
        worker = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
        try:
            await one.fetch_sources()
            task = asyncio.create_task(worker.run_pipeline('daily'))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert one.list_reports().total == 0 and one.list_pipelines()[0].pending == 2
            assert one.list_runs(kind='pipeline')[0].status == 'failed'
            blocked = False
            assert (await worker.run_pipeline('daily')).status == 'ok'
        finally:
            await one.aclose()
            await worker.aclose()

    asyncio.run(scenario())


def test_expired_pipeline_owner_cannot_publish_or_consume_replacements(web, write_config):
    from datetime import datetime, timedelta, timezone
    from test_models import model_config, chat_reply
    path = write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})})
    web.serve(FEED, three_entries())
    now = [datetime(2030, 1, 1, tzinfo=timezone.utc)]

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        attempts = 0

        async def respond(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                entered.set()
                await release.wait()
            return httpx.Response(200, json=chat_reply('Research [1]. Weather [2].'))

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
            assert winner.status == 'ok'
            release.set()
            loser = await task
            assert loser.status == 'error'
            assert second.list_reports().total == 1
            assert len(list(second.config.reports_dir.rglob('*.md'))) == 1
            assert second.list_pipelines()[0].last_status == 'ok'
            abandoned = next(r for r in second.list_runs(kind='pipeline') if r.id == loser.run_id)
            assert abandoned.status == 'failed' and abandoned.cost == 0.01
            assert abandoned.stats['usage']['total_tokens'] == 15
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            for service in [fetcher, first, second]:
                await service.aclose()

    asyncio.run(scenario())


def test_report_files_are_private_by_default(web, write_config, open_service):
    import os
    from test_models import model_config, chat_reply, CHAT_URL
    web.serve(FEED, three_entries())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Research [1]. Weather [2].')))
    service = open_service(write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})}))

    async def scenario():
        await service.fetch_sources()
        result = await service.run_pipeline('daily')
        assert result.status == 'ok'
        if os.name == 'posix':
            assert Path(service.get_report(result.report_id).path).stat().st_mode & 0o777 == 0o600

    asyncio.run(scenario())


def test_report_and_run_finish_times_include_model_latency(web, write_config):
    from datetime import datetime, timedelta, timezone
    from test_models import model_config, chat_reply, CHAT_URL
    now = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
    web.serve(FEED, three_entries())

    def respond(request):
        now[0] += timedelta(minutes=2)
        return httpx.Response(200, json=chat_reply('Research [1]. Weather [2].'))

    web.handle(CHAT_URL, respond)
    service = TentaclesService.open(write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})}),
                                    env={}, transport=web.transport(), clock=lambda: now[0])

    async def scenario():
        try:
            await service.fetch_sources()
            result = await service.run_pipeline('daily')
            assert result.status == 'ok'
            run = next(r for r in service.list_runs(kind='pipeline') if r.id == result.run_id)
            assert run.finished_at == now[0] and run.finished_at - run.started_at == timedelta(minutes=2)
            assert service.get_report(result.report_id).created_at == now[0]
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('cancel_again', [False, True])
def test_cancellation_during_publish_drains_io_and_preserves_committed_report(web, write_config, cancel_again):
    import sqlite3
    import threading
    from test_models import model_config, chat_reply, CHAT_URL
    path = write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})})
    web.serve(FEED, three_entries())

    async def scenario():
        connection = timer = task = None

        def respond(request):
            nonlocal connection, timer
            connection = sqlite3.connect(service.config.db_path, check_same_thread=False)
            connection.execute('BEGIN IMMEDIATE')
            timer = threading.Timer(0.2, connection.commit)
            timer.start()
            asyncio.get_running_loop().call_later(0.03, task.cancel)
            if cancel_again:
                asyncio.get_running_loop().call_later(0.06, task.cancel)
            return httpx.Response(200, json=chat_reply('Research [1]. Weather [2].'))

        web.handle(CHAT_URL, respond)
        service = TentaclesService.open(path, env={}, transport=web.transport())
        try:
            await service.fetch_sources()
            task = asyncio.create_task(service.run_pipeline('daily'))
            with pytest.raises(asyncio.CancelledError):
                await task
            [report] = service.list_reports().items
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


@pytest.mark.parametrize('change,fragment', [
    ({'sources': ['missing']}, 'sources'), ({'persona': 'missing'}, 'persona'),
    ({'window': '0h'}, 'window'), ({'window': 'yesterday'}, 'window'),
    ({'max_items': 0}, 'max_items'), ({'processor': {'type': 'digest', 'model': 'missing'}}, 'model'),
    ({'filters': [{'type': 'keyword', 'reject_below': -1}]}, 'reject_below'),
    ({'filters': [{'type': 'keyword', 'accept_above': 0.2}]}, 'accept_above'),
    ({'filters': [{'type': 'keyword', 'regex': True, 'include': ['[']}]}, 'regular expression'),
])
def test_invalid_pipeline_configuration_has_a_located_error(write_config, change, fragment):
    from test_models import model_config
    data = {**model_config(), **pipeline_config(**change)}
    with pytest.raises(ConfigError, match=fragment):
        TentaclesService.open(write_config(data), env={})


def test_latest_persona_evaluation_and_historical_pipeline_queries_survive_reload(web, write_config):
    data = pipeline_config()
    path = write_config(data)
    web.serve(FEED, three_entries())

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport())
        try:
            await service.fetch_sources()
            await service.filter_pipeline('daily')
            await service.set_persona('default', exclude_add=['garden'])
            result = await service.filter_pipeline('daily')
            assert result.stats['candidates'] == 3
            assert service.list_items(pipeline='daily', passed=True).total == 1
            assert all(i.persona_version == 2 for i in service.list_items(pipeline='daily').items)
        finally:
            await service.aclose()
        write_config({})
        service = TentaclesService.open(path, env={})
        try:
            assert service.list_items(pipeline='daily', passed=True).total == 1
            assert service.list_reports(pipeline='daily').total == 0
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_digest_materials_are_grouped_by_persona_labels_with_stable_numbering(web, write_config, open_service):
    from test_models import model_config, chat_reply, CHAT_URL
    from test_personas import BASE_TAGS
    web.serve(FEED, three_entries())

    def respond(request):
        body = json.loads(request.content)
        payload = json.loads(body['messages'][-1]['content'])
        if 'profile' in payload:
            return httpx.Response(200, json=chat_reply(json.dumps({'tags': BASE_TAGS})))
        assert list(payload['groups']) == ['AI', 'other']
        assert payload['groups']['AI'][0]['number'] == 1
        assert payload['groups']['other'][0]['number'] == 2
        return httpx.Response(200, json=chat_reply('AI research [1]. Weather [2].'))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config({**model_config(), **pipeline_config(processor={'type': 'digest'})}))

    async def scenario():
        await service.set_persona('default', profile='AI then science')
        await service.fetch_sources()
        result = await service.run_pipeline('daily')
        assert result.status == 'ok'
        report = service.get_report(result.report_id)
        assert [service.get_item(item_id).evaluations[0].score for item_id in report.item_ids] == [1.0, 0.5]

    asyncio.run(scenario())


def test_filter_window_uses_fetch_time_not_old_publication_time(web, write_config):
    from datetime import datetime, timedelta, timezone
    now = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
    web.serve(FEED, rss_feed({'title': 'AI historical publication', 'guid': 'old-publication',
        'description': 'Freshly fetched', 'pubDate': 'Mon, 01 Jan 2001 00:00:00 GMT'}))
    service = TentaclesService.open(write_config(pipeline_config()), env={},
                                    transport=web.transport(), clock=lambda: now[0])

    async def scenario():
        try:
            await service.fetch_sources()
            assert (await service.filter_pipeline('daily')).stats['candidates'] == 1
            now[0] += timedelta(hours=25)
            await service.set_persona('default', include_add=['new'])
            assert (await service.filter_pipeline('daily')).stats['candidates'] == 0
        finally:
            await service.aclose()

    asyncio.run(scenario())
