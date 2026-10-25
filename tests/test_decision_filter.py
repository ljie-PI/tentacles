"""Decision filtering through Service, with protocol-level HTTP doubles."""
import asyncio
import json

import httpx
import pytest

from conftest import rss_feed
from test_models import model_config
from test_pipelines import FEED, pipeline_config, three_entries
from tentacles.errors import ConfigError
from tentacles.service import TentaclesService

DECISION_URL = 'https://router.test/api/alpha/decisions'


def decision_config(*, stages=None, **model_options):
    data = {**model_config(), **pipeline_config(filters=stages if stages is not None else [{'type': 'decision'}])}
    data['models']['decision'].update({'api_key': 'private-router-key', 'retries': 0, 'concurrency': 2, **model_options})
    return data


def reply(answers, *, model='typesafe/jev-1.13-20260917', tokens=10, cost=0.001):
    return {'model': model, 'answers': answers,
            'usage': {'input_tokens': tokens, 'output_tokens': 2, 'cost': cost}}


def test_noul_stage_sends_persona_material_and_caches_normalized_scores(write_config, open_service, web):
    web.serve(FEED, three_entries())
    probabilities = {'AI research launch': 0.9, 'Open garden weather': 0.5, 'Advertisement AI sale': 0.1}

    def respond(request):
        body = json.loads(request.content)
        assert request.headers['authorization'] == 'Bearer private-router-key'
        assert body['provider']['data_collection'] == 'deny'
        assert body['model'] == 'typesafe/jev-1.13'
        assert 'Science and AI first' in body['state']['persona']
        assert body['state']['source'] == 'news' and 'author' in body['state']
        assert body['questions']['relevance']['type'] == 'noul' and 'tag' not in body['questions']
        return httpx.Response(200, json=reply({'relevance': {'type': 'noul',
            'noul': probabilities[body['state']['title']]}}))

    web.handle(DECISION_URL, respond)
    service = open_service(write_config(decision_config()))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.status == 'ok'
        items = service.list_items(pipeline='daily').items
        assert {item.title: item.score for item in items} == probabilities
        assert service.list_items(pipeline='daily', passed=True).total == 2
        assert all(item.stage == 'decision' for item in items)
        assert all(item.stage_scores[0].details['model'] == 'typesafe/jev-1.13-20260917' for item in items)
        assert (await service.filter_pipeline('daily')).stats['candidates'] == 0
        assert len(web.requests_to(DECISION_URL)) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize('choice', ['label', 'other'])
def test_score_scale_and_label_choice_are_normalized_and_preserved(write_config, open_service, web, choice):
    from test_models import chat_reply, CHAT_URL
    from test_personas import BASE_TAGS
    web.serve(FEED, rss_feed({'title': 'AI news', 'guid': 'x', 'author': 'writer@example.test',
                            'description': 'x' * 2500}))
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply(json.dumps({'tags': BASE_TAGS}))))

    def respond(request):
        body = json.loads(request.content)
        assert body['questions']['relevance']['type'] == 'score'
        assert len(body['questions']['relevance']['criteria']) == 4
        assert len(body['state']['summary']) == 1000 and body['state']['author']
        options = body['questions']['tag']['criteria']
        assert len(options) == 6 and 'other' in options
        selected = next(key for key, value in options.items() if value.startswith('AI:')) if choice == 'label' else 'other'
        return httpx.Response(200, json=reply({
            'relevance': {'type': 'score', 'score': 2.25, 'confidence': 0.8,
                          'probabilities': {'2': 0.75, '3': 0.25}},
            'tag': {'type': 'choice', 'choice': selected, 'confidence': 0.9,
                    'probabilities': {selected: 1.0}}}))

    web.handle(DECISION_URL, respond)
    service = open_service(write_config(decision_config(stages=[{'type': 'decision', 'relevance_type': 'score'}])))

    async def scenario():
        await service.set_persona('default', profile='AI and science priorities')
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.status == 'ok'
        [item] = service.list_items(pipeline='daily').items
        assert item.score == 0.75 and item.tags == (['AI'] if choice == 'label' else [])
        details = item.stage_scores[0].details
        assert details['answers']['relevance']['raw_score'] == 2.25
        assert details['answers']['relevance']['confidence'] == 0.8
        assert details['answers']['tag']['confidence'] == 0.9

    asyncio.run(scenario())


@pytest.mark.parametrize('preference,expected', [({}, 'deny'), ({'data_collection': None}, 'deny'),
                                              ({'data_collection': 'allow'}, 'allow')])
def test_provider_preferences_keep_privacy_default_unless_explicitly_allowed(write_config, open_service, web, preference, expected):
    web.serve(FEED, rss_feed({'title': 'AI item', 'guid': 'x', 'description': 'news'}))

    def respond(request):
        body = json.loads(request.content)
        assert body['provider']['data_collection'] == expected
        assert body['provider']['order'] == ['TypeSafe']
        return httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': 0.8}}))

    web.handle(DECISION_URL, respond)
    service = open_service(write_config(decision_config(extra_body={'provider': {'order': ['TypeSafe'], **preference}})))

    async def scenario():
        await service.fetch_sources()
        await service.filter_pipeline('daily')
        assert service.list_items(pipeline='daily').total == 1

    asyncio.run(scenario())


def test_fixed_workers_keep_item_mapping_and_accumulate_usage_out_of_order(write_config, open_service, web):
    web.serve(FEED, three_entries())
    probabilities = {'AI research launch': 0.91, 'Open garden weather': 0.55, 'Advertisement AI sale': 0.1}
    started, finished = [], []
    active = peak = 0
    second_started = None

    async def respond(request):
        nonlocal active, peak
        title = json.loads(request.content)['state']['title']
        started.append(title)
        position = len(started)
        active += 1
        peak = max(peak, active)
        try:
            if position == 1:
                await asyncio.wait_for(second_started.wait(), 2)
                await asyncio.sleep(0.02)
            else:
                second_started.set()
        finally:
            active -= 1
        finished.append(title)
        return httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': probabilities[title]}}))

    service = TentaclesService.open(write_config(decision_config(concurrency=2)), env={},
                                    transport=httpx.MockTransport(lambda r: httpx.Response(200, text=three_entries())))
    # Open a second adapter on the same persistent data with the async model transport.
    path = service.config.path

    async def scenario():
        nonlocal second_started
        second_started = asyncio.Event()
        worker = None
        try:
            await service.fetch_sources()
            worker = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
            result = await worker.filter_pipeline('daily')
            assert result.status == 'ok'
            assert peak == 2 and started != finished
            assert {i.title: i.score for i in worker.list_items(pipeline='daily').items} == probabilities
            stage = result.stats['stages'][0]
            assert stage['usage'] == {'input_tokens': 30, 'output_tokens': 6, 'total_tokens': 36}
            assert stage['models'] == ['typesafe/jev-1.13-20260917']
            run = worker.list_runs(kind='filter')[0]
            assert run.cost == pytest.approx(0.003) and run.stats['usage']['total_tokens'] == 36
        finally:
            if worker:
                await worker.aclose()
            await service.aclose()

    asyncio.run(scenario())


def five_entries():
    return rss_feed(*({'title': title, 'guid': str(i), 'description': title + ' details'} for i, title in enumerate(
        ['AI launch', 'Space orbit', 'Garden harvest', 'Robot release', 'Health study'])))


@pytest.mark.parametrize('retries', [0, 1])
def test_429_pauses_every_worker_until_a_shared_resume_point(write_config, web, retries):
    web.serve(FEED, five_entries())
    path = write_config(decision_config(retries=retries, concurrency=2))

    async def scenario():
        second_started, limited = asyncio.Event(), asyncio.Event()
        starts = []
        first_title = None
        limit_time = None
        attempts = {}

        async def respond(request):
            nonlocal first_title, limit_time
            title = json.loads(request.content)['state']['title']
            attempts[title] = attempts.get(title, 0) + 1
            starts.append((title, asyncio.get_running_loop().time()))
            if len(starts) == 1:
                first_title = title
                await asyncio.wait_for(second_started.wait(), 2)
                limit_time = asyncio.get_running_loop().time()
                limited.set()
                return httpx.Response(429, headers={'Retry-After': '0.08'})
            if len(starts) == 2:
                second_started.set()
                await asyncio.wait_for(limited.wait(), 2)
            return httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': 0.8}}))

        seed = TentaclesService.open(path, env={}, transport=web.transport())
        worker = None
        try:
            await seed.fetch_sources()
            worker = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
            result = await worker.filter_pipeline('daily')
            assert result.status == 'ok'
            assert result.stats['evaluated'] == (5 if retries else 4)
            assert limit_time is not None
            assert all(start >= limit_time + 0.06 for _, start in starts[2:])
            assert attempts[first_title] == (2 if retries else 1)
        finally:
            if worker:
                await worker.aclose()
            await seed.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('status', [400, 413])
def test_item_http_error_forwards_only_that_item_and_cascade_thresholds_apply(write_config, open_service, web, status):
    web.serve(FEED, three_entries())

    def respond(request):
        title = json.loads(request.content)['state']['title']
        if title == 'Advertisement AI sale':
            return httpx.Response(status, text='private provider body')
        value = 0.9 if title == 'AI research launch' else 0.5
        return httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': value}}))

    web.handle(DECISION_URL, respond)
    data = decision_config(stages=[{'type': 'decision', 'reject_below': 0.2, 'accept_above': 0.8}, {'type': 'keyword'}])
    service = open_service(write_config(data))

    async def scenario():
        await service.fetch_sources()
        outcome = await service.filter_pipeline('daily')
        assert outcome.status == 'ok' and outcome.stats['evaluated'] == 3
        by_title = {i.title: i for i in service.list_items(pipeline='daily').items}
        assert by_title['AI research launch'].stage == 'decision'
        assert len(by_title['AI research launch'].stage_scores) == 1
        assert by_title['Open garden weather'].stage == 'keyword'
        assert by_title['Advertisement AI sale'].passed is False
        assert by_title['Advertisement AI sale'].stage_scores[0].score is None
        [decision, keyword] = outcome.stats['stages']
        assert (decision['passed'], decision['forwarded'], decision['errors']) == (1, 2, 1)
        assert (keyword['passed'], keyword['rejected']) == (1, 1)

    asyncio.run(scenario())


@pytest.mark.parametrize('status', [401, 403, 402])
def test_terminal_status_stops_dispatch_but_drains_inflight_and_caches_valid_results(write_config, web, status):
    web.serve(FEED, three_entries())
    path = write_config(decision_config(concurrency=2))

    async def scenario():
        second_started, terminal_returned, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []
        cancelled = False
        fatal = True

        async def respond(request):
            nonlocal cancelled
            calls.append(json.loads(request.content)['state']['title'])
            position = len(calls)
            if fatal and position == 1:
                await asyncio.wait_for(second_started.wait(), 2)
                terminal_returned.set()
                return httpx.Response(status, text='private-router-key private provider body')
            if fatal and position == 2:
                second_started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled = True
                    raise
            return httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': 0.9}}))

        seed = TentaclesService.open(path, env={}, transport=web.transport())
        worker = None
        task = None
        try:
            await seed.fetch_sources()
            worker = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
            task = asyncio.create_task(worker.filter_pipeline('daily'))
            await asyncio.wait_for(terminal_returned.wait(), 2)
            await asyncio.sleep(0.02)
            assert len(calls) == 2 and not cancelled and not task.done()
            release.set()
            result = await task
            assert result.status == 'error'
            assert worker.list_items(pipeline='daily').total == 1
            run = worker.list_runs(kind='filter')[0]
            assert run.status == 'failed' and run.cost == pytest.approx(0.001)
            assert run.stats['usage']['total_tokens'] == 12
            assert 'private' not in run.error
            fatal = False
            retried = await worker.filter_pipeline('daily')
            assert retried.status == 'ok' and retried.stats['candidates'] == 2
            assert worker.list_items(pipeline='daily').total == 3
            assert len(calls) == 4
        finally:
            release.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            if worker:
                await worker.aclose()
            await seed.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('processor,relevance,expected', [('none', 0.9, 0.003), ('digest', 0.9, 0.013),
                                                       ('none', 0.1, 0.003)])
def test_pipeline_totals_include_decision_usage_with_or_without_report(write_config, open_service, web, processor, relevance, expected):
    from test_models import chat_reply, CHAT_URL
    web.serve(FEED, three_entries())
    web.handle(DECISION_URL, lambda r: httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': relevance}})))
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Digest [1]. Material [2].')))
    data = decision_config()
    data['pipelines'][0]['processor'] = {'type': processor}
    service = open_service(write_config(data))

    async def scenario():
        await service.fetch_sources()
        result = await service.run_pipeline('daily')
        assert result.status == ('ok' if relevance >= 0.5 else 'no_new')
        root = service.list_runs(kind='pipeline')[0]
        child = service.list_runs(kind='filter')[0]
        assert root.cost == pytest.approx(expected) and child.cost == pytest.approx(0.003)
        assert root.stats['filter_run_id'] == child.id
        assert root.stats['usage']['total_tokens'] == (51 if processor == 'digest' else 36)
        if processor == 'digest':
            report = service.get_report(result.report_id)
            assert report.cost == 0.01 and report.usage.total_tokens == 15

    asyncio.run(scenario())


@pytest.mark.parametrize('entry', ['filter', 'pipeline'])
def test_cancelled_batch_records_returned_usage_before_closing_every_worker(write_config, web, entry):
    web.serve(FEED, three_entries())
    path = write_config(decision_config(concurrency=2))

    async def scenario():
        returned, another_started = asyncio.Event(), asyncio.Event()
        calls = 0
        active = 0

        async def respond(request):
            nonlocal calls, active
            calls += 1
            active += 1
            try:
                if calls == 1:
                    returned.set()
                    return httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': 0.9}}))
                another_started.set()
                await asyncio.Event().wait()
            finally:
                active -= 1

        seed = TentaclesService.open(path, env={}, transport=web.transport())
        worker = None
        task = None
        try:
            await seed.fetch_sources()
            worker = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
            task = asyncio.create_task(worker.filter_pipeline('daily') if entry == 'filter' else worker.run_pipeline('daily'))
            await asyncio.wait_for(returned.wait(), 2)
            await asyncio.wait_for(another_started.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert active == 0
            run = worker.list_runs(kind=entry)[0]
            assert run.status == 'failed' and run.cost == pytest.approx(0.001)
            assert run.stats['usage']['total_tokens'] == 12
            if entry == 'pipeline':
                assert run.stats['filter_run_id'] == worker.list_runs(kind='filter')[0].id
            await worker.aclose()
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if worker:
                await worker.aclose()
            await seed.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('hint', ['0', 'Wed, 01 Jan 2020 00:00:00 GMT'])
def test_explicit_retry_after_makes_402_transient(write_config, open_service, web, hint):
    web.serve(FEED, rss_feed({'title': 'AI item', 'guid': 'x', 'description': 'news'}))
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(402, headers={'Retry-After': hint})
        return httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': 0.8}}))

    web.handle(DECISION_URL, respond)
    service = open_service(write_config(decision_config(retries=1)))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.status == 'ok' and result.stats['evaluated'] == 1 and len(attempts) == 2

    asyncio.run(scenario())


def test_terminal_response_interrupts_workers_waiting_on_a_long_cooldown(write_config, web):
    web.serve(FEED, three_entries())
    path = write_config(decision_config(retries=1, concurrency=2))

    async def scenario():
        second_started, limited = asyncio.Event(), asyncio.Event()
        calls = []

        async def respond(request):
            calls.append(request)
            if len(calls) == 1:
                await asyncio.wait_for(second_started.wait(), 2)
                limited.set()
                return httpx.Response(429, headers={'Retry-After': '60'})
            second_started.set()
            await limited.wait()
            return httpx.Response(401)

        seed = TentaclesService.open(path, env={}, transport=web.transport())
        worker = None
        try:
            await seed.fetch_sources()
            worker = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
            outcome = await asyncio.wait_for(worker.filter_pipeline('daily'), 2)
            assert outcome.status == 'error' and len(calls) == 2
            assert worker.list_runs(kind='filter')[0].status == 'failed'
        finally:
            if worker:
                await worker.aclose()
            await seed.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('answer', [
    {'type': 'score', 'score': 0.8}, {'type': 'noul', 'noul': 1.5}, {'type': 'noul', 'noul': True},
    {'type': 'noul', 'noul': '0.9'}, {'type': 'noul'}, {'type': 'noul', 'noul': None}])
def test_invalid_answer_is_uncached_but_returned_usage_is_recorded(write_config, open_service, web, answer):
    web.serve(FEED, rss_feed({'title': 'AI item', 'guid': 'x', 'description': 'news'}))
    current = [answer]
    web.handle(DECISION_URL, lambda r: httpx.Response(200, json=reply({'relevance': current[0]})))
    service = open_service(write_config(decision_config()))

    async def scenario():
        await service.fetch_sources()
        failed = await service.filter_pipeline('daily')
        assert failed.stats['evaluated'] == 0 and failed.stats['errors'] == 1
        assert service.list_runs(kind='filter')[0].cost == pytest.approx(0.001)
        assert service.list_items(pipeline='daily').total == 0
        current[0] = {'type': 'noul', 'noul': 0.9}
        retry = await service.filter_pipeline('daily')
        assert retry.stats['candidates'] == retry.stats['evaluated'] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize('model_name', ['missing', 'fast'])
def test_stage_override_requires_an_existing_decision_kind(write_config, model_name):
    with pytest.raises(ConfigError, match=r'filters\[0\].model'):
        TentaclesService.open(write_config(decision_config(stages=[{'type': 'decision', 'model': model_name}])), env={})


def test_multiple_rate_limits_extend_one_shared_deadline(write_config, web):
    web.serve(FEED, five_entries())
    path = write_config(decision_config(retries=1, concurrency=3))

    async def scenario():
        three_started, first_limit = asyncio.Event(), asyncio.Event()
        starts, later_deadline = [], None

        async def respond(request):
            nonlocal later_deadline
            starts.append(asyncio.get_running_loop().time())
            position = len(starts)
            if position == 3:
                three_started.set()
                await first_limit.wait()
            elif position <= 2:
                await asyncio.wait_for(three_started.wait(), 2)
                if position == 1:
                    first_limit.set()
                    return httpx.Response(429, headers={'Retry-After': '0.04'})
                await asyncio.sleep(0.02)
                later_deadline = asyncio.get_running_loop().time() + 0.08
                return httpx.Response(429, headers={'Retry-After': '0.08'})
            return httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': 0.8}}))

        seed = TentaclesService.open(path, env={}, transport=web.transport())
        worker = None
        try:
            await seed.fetch_sources()
            worker = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
            result = await worker.filter_pipeline('daily')
            assert result.status == 'ok' and result.stats['evaluated'] == 5
            assert later_deadline is not None
            assert all(start >= later_deadline - 0.01 for start in starts[3:])
            assert len(starts) == 7
        finally:
            if worker:
                await worker.aclose()
            await seed.aclose()

    asyncio.run(scenario())


def test_explicit_model_override_uses_its_endpoint_and_preserves_actual_name(write_config, open_service, web):
    data = decision_config(stages=[{'type': 'decision', 'model': 'alternate'}])
    data['models']['alternate'] = {**data['models']['decision'], 'model': 'custom-decision',
                                  'base_url': 'https://alternate.test/api/v1'}
    web.serve(FEED, rss_feed({'title': 'AI item', 'guid': 'x', 'description': 'news'}))

    def respond(request):
        assert json.loads(request.content)['model'] == 'custom-decision'
        return httpx.Response(200, json=reply({'relevance': {'type': 'noul', 'noul': 0.9}}, model='custom-20300101'))

    web.handle('https://alternate.test/api/alpha/decisions', respond)
    service = open_service(write_config(data))

    async def scenario():
        await service.fetch_sources()
        outcome = await service.filter_pipeline('daily')
        assert outcome.status == 'ok' and outcome.stats['evaluated'] == 1
        [item] = service.list_items(pipeline='daily').items
        assert item.stage_scores[0].details['model'] == 'custom-20300101'
        assert outcome.stats['stages'][0]['model_name'] == 'alternate'
        assert web.requests_to(DECISION_URL) == []

    asyncio.run(scenario())


def test_missing_key_is_a_batch_upstream_error_only_when_called(write_config, open_service, web):
    data = decision_config(api_key='')
    web.serve(FEED, three_entries())
    service = open_service(write_config(data))
    assert service.check_config().ok

    async def scenario():
        await service.fetch_sources()
        outcome = await service.filter_pipeline('daily')
        assert outcome.status == 'error' and 'decision' in outcome.error
        assert web.requests_to(DECISION_URL) == []
        assert service.list_runs(kind='filter')[0].status == 'failed'

    asyncio.run(scenario())


@pytest.mark.parametrize('change,known_cost', [({'model': None}, 0.001), ({'model': 7}, 0.001),
                                            ({'usage': None}, None), ({'usage': {'input_tokens': 10}}, None)])
def test_required_response_metadata_is_validated_without_dropping_known_cost(write_config, open_service, web, change, known_cost):
    web.serve(FEED, rss_feed({'title': 'AI item', 'guid': 'x', 'description': 'news'}))
    data = {**reply({'relevance': {'type': 'noul', 'noul': 0.8}}), **change}
    web.handle(DECISION_URL, lambda r: httpx.Response(200, json=data))
    service = open_service(write_config(decision_config()))

    async def scenario():
        await service.fetch_sources()
        outcome = await service.filter_pipeline('daily')
        assert outcome.stats['evaluated'] == 0 and outcome.stats['errors'] == 1
        assert service.list_runs(kind='filter')[0].cost == known_cost

    asyncio.run(scenario())


@pytest.mark.parametrize('retries', [0, 2])
@pytest.mark.parametrize('hint', ['-1', '-0.5', '-0'])
def test_negative_retry_after_cannot_make_a_permanent_402_transient(write_config, open_service, web, retries, hint):
    web.serve(FEED, three_entries())
    web.serve(DECISION_URL, 'credit failure', status=402, headers={'Retry-After': hint})
    service = open_service(write_config(decision_config(retries=retries, concurrency=1)))

    async def scenario():
        await service.fetch_sources()
        outcome = await service.filter_pipeline('daily')
        assert outcome.status == 'error'
        assert len(web.requests_to(DECISION_URL)) == 1
        assert service.list_runs(kind='filter')[0].status == 'failed'

    asyncio.run(scenario())


@pytest.mark.parametrize('cancel_again', [False, True])
def test_owner_cancellation_never_interrupts_a_workers_async_http_cleanup(write_config, web, cancel_again, caplog):
    web.serve(FEED, three_entries())
    path = write_config(decision_config(concurrency=2))

    async def scenario():
        both_started, cleanup_started, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
        active = started = 0

        async def respond(request):
            nonlocal active, started
            active += 1
            started += 1
            position = started
            if started == 2:
                both_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                if position == 2:
                    cleanup_started.set()
                    await release_cleanup.wait()
                active -= 1

        seed = TentaclesService.open(path, env={}, transport=web.transport())
        worker = None
        task = None
        try:
            await seed.fetch_sources()
            worker = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
            task = asyncio.create_task(worker.filter_pipeline('daily'))
            await asyncio.wait_for(both_started.wait(), 2)
            task.cancel()
            await asyncio.wait_for(cleanup_started.wait(), 2)
            await asyncio.sleep(0.01)
            assert not task.done() and active == 1
            if cancel_again:
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done() and active == 1
            release_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert active == 0
            await asyncio.sleep(0)
            assert not any('CancelledError exception in shielded future' in r.getMessage() for r in caplog.records)
            await worker.aclose()
        finally:
            release_cleanup.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            if worker:
                await worker.aclose()
            await seed.aclose()

    asyncio.run(scenario())
