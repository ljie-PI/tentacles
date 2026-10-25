"""LLM filtering at Service, replacing only outbound HTTP and time."""
import asyncio
import json

import httpx
import pytest

from conftest import rss_feed
from test_models import model_config, chat_reply, CHAT_URL
from test_pipelines import FEED, pipeline_config, three_entries
from test_personas import BASE_TAGS
from tentacles.errors import ConfigError
from tentacles.service import TentaclesService


def llm_config(*, stages=None, batch_size=20):
    return {**model_config(), **pipeline_config(filters=stages if stages is not None else [
        {'type': 'llm', 'batch_size': batch_size}])}


def test_llm_filters_numbered_batches_and_missing_results_score_zero(write_config, open_service, web):
    web.serve(FEED, three_entries())
    batches = []

    def respond(request):
        body = json.loads(request.content)
        content = json.loads(body['messages'][-1]['content'])
        if 'profile' in content:
            return httpx.Response(200, json=chat_reply(json.dumps({'tags': BASE_TAGS})))
        batches.append(content)
        assert len(content['items']) <= 2
        assert len(content['tags']) == 5 and 'Science' in content['persona']
        system = body['messages'][0]['content']
        assert '0.3' in system and '标题党' in system and '广告' in system and '优先' in system
        result = [{'id': item['id'], 'tag_id': 1, 'score': 0.9, 'reason': 'Useful AI research'}
                  for item in content['items'] if item['title'] == 'AI research launch']
        result.append({'id': 9999, 'score': 'not a number'})  # unknown rows ignored before validation
        return httpx.Response(200, json=chat_reply(json.dumps(result)))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(llm_config(batch_size=2)))

    async def scenario():
        await service.set_persona('default', profile='AI, Science and space interests')
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.status == 'ok'
        items = {i.title: i for i in service.list_items(pipeline='daily').items}
        assert items['AI research launch'].score == 0.9 and items['AI research launch'].tags == ['AI']
        assert items['AI research launch'].reason == 'Useful AI research'
        assert items['Open garden weather'].score == items['Advertisement AI sale'].score == 0
        assert all(i.stage == 'llm' for i in items.values())
        assert len(batches) == 2
        assert (await service.filter_pipeline('daily')).stats['candidates'] == 0

    asyncio.run(scenario())


def test_failed_json_splits_once_preserving_ids_and_counts_all_attempts(write_config, open_service, web):
    web.serve(FEED, three_entries())
    requested = []

    def respond(request):
        items = json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
        ids = [item['id'] for item in items]
        requested.append(ids)
        text = 'broken JSON' if len(requested) == 1 else json.dumps([
            {'id': number, 'tag_id': 1, 'score': 0.8, 'reason': f'number {number}'} for number in ids])
        return httpx.Response(200, json=chat_reply(text))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(llm_config(batch_size=4)))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.status == 'ok' and result.stats['evaluated'] == 3
        assert requested == [[1, 2, 3], [1], [2, 3]]
        assert result.stats['usage'] == {'input_tokens': 36, 'output_tokens': 9, 'total_tokens': 45}
        assert service.list_runs(kind='filter')[0].cost == pytest.approx(0.03)
        assert all(item.tags == ['Science and AI first'] for item in service.list_items(pipeline='daily').items)

    asyncio.run(scenario())


@pytest.mark.parametrize('wrapper', ['fence', 'prose'])
def test_dirty_json_is_repaired_without_split_or_losing_reason_text(write_config, open_service, web, wrapper):
    web.serve(FEED, rss_feed({'title': 'AI news', 'guid': 'x', 'description': 'news'}))
    requests = []

    def respond(request):
        requests.append(request)
        raw = '[{"id":"1","tag_id":"1","score":0.9,"reason":"text ,} stays",},]'
        text = '```json\n' + raw + '\n```' if wrapper == 'fence' else 'Explanation before ' + raw + ' after'
        return httpx.Response(200, json=chat_reply(text))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(llm_config()))

    async def scenario():
        await service.fetch_sources()
        await service.filter_pipeline('daily')
        [item] = service.list_items(pipeline='daily').items
        assert item.score == 0.9 and item.reason == 'text ,} stays'
        assert len(requests) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize('followup', [False, True])
def test_failed_retry_half_errors_all_its_items_without_poisoning_other_half(write_config, open_service, web, followup):
    from test_decision_filter import five_entries
    web.serve(FEED, five_entries())
    requested = []
    retrying = [False]

    def respond(request):
        items = json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
        ids = [item['id'] for item in items]
        requested.append(ids)
        bad = not retrying[0] and (len(ids) == 4 or ids == [3, 4])
        text = 'not JSON' if bad else json.dumps([
            {'id': n, 'tag_id': 1, 'score': 0.9, 'reason': 'selected'} for n in ids])
        return httpx.Response(200, json=chat_reply(text))

    stages = [{'type': 'llm', 'batch_size': 4, 'accept_above': 0.8}]
    if followup:
        stages.append({'type': 'keyword'})
    web.handle(CHAT_URL, respond)
    service = open_service(write_config(llm_config(stages=stages)))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert requested == [[1, 2, 3, 4], [1, 2], [3, 4], [5]]
        assert result.stats['stages'][0]['errors'] == 2
        if followup:
            assert result.stats['evaluated'] == 5
            assert result.stats['stages'][0]['forwarded'] == 2
            assert len([i for i in service.list_items(pipeline='daily').items if i.stage == 'keyword']) == 2
        else:
            assert result.stats['evaluated'] == 3 and result.stats['errors'] == 2
            assert service.list_items(pipeline='daily').total == 3
            retrying[0] = True
            retried = await service.filter_pipeline('daily')
            assert retried.stats['candidates'] == retried.stats['evaluated'] == 2
            assert service.list_items(pipeline='daily').total == 5

    asyncio.run(scenario())


def test_singleton_bad_json_has_one_nonempty_retry_and_no_recursion(write_config, open_service, web):
    web.serve(FEED, rss_feed({'title': 'AI news', 'guid': 'x', 'description': 'news'}))
    requests = []

    def respond(request):
        requests.append(json.loads(json.loads(request.content)['messages'][-1]['content'])['items'])
        return httpx.Response(200, json=chat_reply('still broken'))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(llm_config(batch_size=1)))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.stats['errors'] == 1 and result.stats['evaluated'] == 0
        assert len(requests) == 2 and all(len(items) == 1 for items in requests)
        assert service.list_runs(kind='filter')[0].cost == 0.02

    asyncio.run(scenario())


@pytest.mark.parametrize('row', [
    {'id': 1, 'tag_id': 1, 'score': 1.5, 'reason': 'bad score'},
    {'id': 1, 'tag_id': 1, 'score': True, 'reason': 'bad boolean'},
    {'id': 1, 'tag_id': [1, 2], 'score': 0.9, 'reason': 'too many tags'},
    {'id': 1, 'tag_id': 99, 'score': 0.9, 'reason': 'unknown tag'},
    {'id': 1, 'tag_id': 1, 'score': 0.9, 'reason': None},
])
def test_invalid_known_rows_are_errors_not_cached_negative_matches(write_config, open_service, web, row):
    web.serve(FEED, rss_feed({'title': 'AI news', 'guid': 'x', 'description': 'news'}))
    calls = []
    web.handle(CHAT_URL, lambda r: calls.append(r) or httpx.Response(200, json=chat_reply(json.dumps([row]))))
    service = open_service(write_config(llm_config()))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.stats['evaluated'] == 0 and result.stats['errors'] == 1
        assert len(calls) == 2 and service.list_items(pipeline='daily').total == 0

    asyncio.run(scenario())


def test_duplicate_known_ids_are_not_arbitrarily_overwritten(write_config, open_service, web):
    web.serve(FEED, rss_feed({'title': 'AI news', 'guid': 'x', 'description': 'news'}))
    rows = [{'id': 1, 'tag_id': 1, 'score': 0.9, 'reason': 'first'},
            {'id': 1, 'tag_id': 1, 'score': 0.4, 'reason': 'second'}]
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply(json.dumps(rows))))
    service = open_service(write_config(llm_config()))

    async def scenario():
        await service.fetch_sources()
        outcome = await service.filter_pipeline('daily')
        assert outcome.stats['errors'] == 1 and service.list_items(pipeline='daily').total == 0

    asyncio.run(scenario())


@pytest.mark.parametrize('status', [400, 401, 413, 500])
def test_http_failure_is_not_misclassified_as_json_retry(write_config, open_service, web, status):
    web.serve(FEED, three_entries())
    web.serve(CHAT_URL, 'private provider body', status=status)
    service = open_service(write_config(llm_config()))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.stats['evaluated'] == 0 and result.stats['errors'] == 3
        assert len(web.requests_to(CHAT_URL)) == 1
        assert service.list_items(pipeline='daily').total == 0

    asyncio.run(scenario())


@pytest.mark.parametrize('changes', [{'batch_size': 0}, {'batch_size': 101}, {'model': 'missing'}, {'model': 'decision'}])
def test_invalid_stage_configuration_is_rejected_before_running(write_config, changes):
    with pytest.raises(ConfigError):
        TentaclesService.open(write_config(llm_config(stages=[{'type': 'llm', **changes}])), env={})


def test_explicit_override_is_a_chat_model_and_default_uses_filter_phase(write_config, open_service, web):
    data = llm_config(stages=[{'type': 'llm', 'model': 'alternate'}])
    data['models']['alternate'] = {**data['models']['fast'], 'base_url': 'https://alternate.test/v1', 'model': 'alternate'}
    web.serve(FEED, rss_feed({'title': 'AI news', 'guid': 'x', 'description': 'news'}))

    def respond(request):
        assert json.loads(request.content)['model'] == 'alternate'
        return httpx.Response(200, json=chat_reply('[{"id":1,"tag_id":1,"score":0.9,"reason":"matched"}]', model='actual-alternate'))

    web.handle('https://alternate.test/v1/chat/completions', respond)
    service = open_service(write_config(data))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.stats['stages'][0]['model_name'] == 'alternate'
        [item] = service.list_items(pipeline='daily').items
        assert item.stage_scores[0].details['model'] == 'actual-alternate'
        assert web.requests_to(CHAT_URL) == []

    asyncio.run(scenario())


def test_decision_pass_reject_and_only_ambiguous_items_reach_llm(write_config, open_service, web):
    from test_decision_filter import DECISION_URL, reply
    data = llm_config(stages=[{'type': 'decision', 'reject_below': 0.2, 'accept_above': 0.8},
                             {'type': 'llm', 'batch_size': 10}])
    data['models']['decision']['api_key'] = 'private-router-key'
    web.serve(FEED, three_entries())
    probabilities = {'AI research launch': 0.9, 'Open garden weather': 0.5, 'Advertisement AI sale': 0.1}
    web.handle(DECISION_URL, lambda r: httpx.Response(200, json=reply({'relevance': {
        'type': 'noul', 'noul': probabilities[json.loads(r.content)['state']['title']]}})))

    def respond(request):
        items = json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
        assert [item['title'] for item in items] == ['Open garden weather']
        return httpx.Response(200, json=chat_reply('[{"id":1,"tag_id":1,"score":0.7,"reason":"science context"}]'))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(data))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        by_title = {i.title: i for i in service.list_items(pipeline='daily').items}
        assert by_title['AI research launch'].stage == by_title['Advertisement AI sale'].stage == 'decision'
        assert by_title['Open garden weather'].stage == 'llm'
        assert len(by_title['Open garden weather'].stage_scores) == 2
        assert result.stats['usage']['total_tokens'] == 51
        assert service.list_runs(kind='filter')[0].cost == pytest.approx(0.013)

    asyncio.run(scenario())


@pytest.mark.parametrize('entry', ['filter', 'pipeline'])
def test_cancellation_between_batches_keeps_known_usage_and_stops_new_requests(write_config, web, entry):
    from test_decision_filter import five_entries
    web.serve(FEED, five_entries())
    path = write_config(llm_config(batch_size=2))

    async def scenario():
        blocked = asyncio.Event()
        calls = active = 0

        async def respond(request):
            nonlocal calls, active
            calls += 1
            active += 1
            try:
                items = json.loads(json.loads(request.content)['messages'][-1]['content'])['items']
                if calls == 2:
                    blocked.set()
                    await asyncio.Event().wait()
                return httpx.Response(200, json=chat_reply(json.dumps([
                    {'id': item['id'], 'tag_id': 1, 'score': 0.9, 'reason': 'matched'} for item in items])))
            finally:
                active -= 1

        seed = TentaclesService.open(path, env={}, transport=web.transport())
        worker = None
        task = None
        try:
            await seed.fetch_sources()
            worker = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
            task = asyncio.create_task(worker.filter_pipeline('daily') if entry == 'filter' else worker.run_pipeline('daily'))
            await asyncio.wait_for(blocked.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert active == 0 and calls == 2
            run = worker.list_runs(kind=entry)[0]
            assert run.status == 'failed' and run.cost == 0.01
            assert run.stats['usage']['total_tokens'] == 15
            assert worker.list_reports().total == 0
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if worker:
                await worker.aclose()
            await seed.aclose()

    asyncio.run(scenario())


def test_empty_persona_is_a_stage_error_not_an_unreliable_zero_match(write_config, open_service, web):
    data = llm_config()
    data['personas']['default']['profile'] = ''
    web.serve(FEED, three_entries())
    service = open_service(write_config(data))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.stats['errors'] == 3 and result.stats['evaluated'] == 0
        assert web.requests_to(CHAT_URL) == []

    asyncio.run(scenario())


@pytest.mark.parametrize('text', ['[1]', '[[{"id":1,"tag_id":1,"score":0.9,"reason":"nested"}]]'])
def test_wrong_row_shapes_are_not_cached_as_missing_matches(write_config, open_service, web, text):
    web.serve(FEED, rss_feed({'title': 'AI news', 'guid': 'x', 'description': 'news'}))
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply(text)))
    service = open_service(write_config(llm_config()))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.stats['errors'] == 1 and result.stats['evaluated'] == 0
        assert len(web.requests_to(CHAT_URL)) == 2
        assert service.list_items(pipeline='daily').total == 0
        assert (await service.filter_pipeline('daily')).stats['candidates'] == 1

    asyncio.run(scenario())


def test_invalid_chat_content_keeps_valid_billing_without_split(write_config, open_service, web):
    web.serve(FEED, three_entries())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply(None)))
    service = open_service(write_config(llm_config()))

    async def scenario():
        await service.fetch_sources()
        result = await service.filter_pipeline('daily')
        assert result.stats['errors'] == 3 and result.stats['evaluated'] == 0
        assert len(web.requests_to(CHAT_URL)) == 1
        run = service.list_runs(kind='filter')[0]
        assert run.stats['usage']['total_tokens'] == 15 and run.cost == 0.01
        assert service.list_items(pipeline='daily').total == 0

    asyncio.run(scenario())


@pytest.mark.parametrize('operation', ['report', 'persona'])
def test_chat_billing_callback_also_protects_other_recorded_operations(write_config, open_service, web, operation):
    web.serve(FEED, three_entries())
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply(None)))
    data = {**model_config(), **pipeline_config(filters=[], processor={'type': 'digest'})}
    service = open_service(write_config(data))

    async def scenario():
        if operation == 'report':
            await service.fetch_sources()
            assert (await service.run_pipeline('daily')).status == 'error'
            run = service.list_runs(kind='pipeline')[0]
        else:
            result = await service.set_persona('default', profile='Changed interests')
            assert result.tag_status == 'error'
            run = service.list_runs(kind='persona')[0]
        assert run.cost == 0.01 and run.stats['usage']['total_tokens'] == 15

    asyncio.run(scenario())
