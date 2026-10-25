"""Feedback and evolution through Service, with real SQLite and fake HTTP/time."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import rss_feed
from test_models import CHAT_URL, chat_reply, model_config
from test_pipelines import FEED, pipeline_config
from tentacles.errors import ConfigError, Conflict, NotFound, UpstreamError
from tentacles.service import TentaclesService


def test_feedback_validates_item_and_persona_and_survives_restart(write_config, web):
    config = pipeline_config()
    config['personas'] = {'work': {'profile': 'Science'}}
    config['pipelines'] = []
    path = write_config(config)
    web.serve(FEED, rss_feed({'guid': 'one', 'title': 'Robotics research'}))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport())
        try:
            await service.fetch_sources()
            item = service.list_items().items[0]
            feedback = service.submit_feedback(item.id, rating=-1, note='Too speculative')
            assert feedback.item_id == item.id and feedback.persona == 'work'
            assert feedback.rating == -1 and feedback.note == 'Too speculative'
            with pytest.raises(NotFound):
                service.submit_feedback('missing', rating=1)
            with pytest.raises(NotFound):
                service.submit_feedback(item.id, rating=1, persona='missing')
            with pytest.raises(ConfigError):
                service.submit_feedback(item.id, rating=0)
            with pytest.raises(ConfigError):
                service.submit_feedback(item.id, rating=True)
            await service.set_persona('default', profile='General', skip_tags=True)
            default = service.submit_feedback(item.id, rating=1)
            assert default.persona == 'default' and default.id > feedback.id
        finally:
            await service.aclose()
        service = TentaclesService.open(path, env={}, transport=web.transport())
        try:
            assert service.get_item(item.id).id == item.id
            # A persisted feedback is observed by evolution in the next slice.
            assert service.submit_feedback(item.id, rating=1, persona='work').id > default.id
        finally:
            await service.aclose()

    asyncio.run(scenario())


def evolved_content(**overrides):
    return {'profile': 'AI and robotics first. Avoid speculative promotion.',
            'tags': [{'tag': tag, 'description': f'{tag} research'}
                     for tag in ['AI', 'Robotics', 'Science', 'Space', 'Energy']],
            'include': ['AI', 'Robotics', 'ai'], 'exclude': ['Advertisement'],
            'summary': 'Strengthen robotics; reduce speculation', **overrides}


def test_evolution_preview_is_read_only_and_json_proposal_applies_once(write_config, open_service, web):
    config = {**pipeline_config(), **model_config()}
    path = write_config(config)
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=chat_reply('```json\n' + json.dumps(evolved_content()) + '\n```'))

    web.handle(CHAT_URL, respond)
    service = open_service(path)

    async def scenario():
        before = service.get_persona('default')
        runs = service.list_runs()
        with pytest.raises(ConfigError):
            await service.evolve_persona('default')
        with pytest.raises(ConfigError):
            await service.evolve_persona('default', instruction='   ')
        proposal = await service.evolve_persona('default', instruction='More robotics', dry_run=True)
        assert proposal.base_version == before.version and proposal.name == 'default'
        assert proposal.profile == evolved_content()['profile']
        assert proposal.include == ['AI', 'Robotics'] and proposal.feedback_count == 0
        assert '-Science and AI first' in proposal.diff and '+AI and robotics first' in proposal.diff
        assert proposal.summary == 'Strengthen robotics; reduce speculation'
        assert service.get_persona('default') == before and service.list_runs() == runs
        # Deserialized JSON is the same public input as the CLI pipe.
        applied = await service.apply_persona(json.loads(proposal.model_dump_json()))
        assert applied.version == 2 and applied.source == 'llm' and applied.note == proposal.summary
        assert applied.tags == proposal.tags and applied.include == proposal.include
        with pytest.raises(Conflict):
            await service.apply_persona(proposal)
        assert len(service.persona_history('default')) == 2
        [run, *_] = service.list_runs(kind='persona')
        assert run.status == 'failed'  # the rejected duplicate apply
        user = json.loads(seen[0]['messages'][1]['content'])
        assert user['instruction'] == 'More robotics' and user['persona']['version'] == 1
        system = seen[0]['messages'][0]['content']
        for rule in ['结构', '优先级', '指令', '证据', '单条', '长度', '原名']:
            assert rule in system

    asyncio.run(scenario())


def test_feedback_evolution_is_bounded_persistent_and_never_reuses_before_last_llm(write_config, web):
    config = {**pipeline_config(), **model_config(),
              'persona_update': {'feedback_window': '24h', 'max_feedback': 2, 'max_profile_chars': 500}}
    path = write_config(config)
    web.serve(FEED, rss_feed({'guid': 'one', 'title': 'Robotics research', 'description': 'Evidence'}))
    seen = []
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]

    def respond(request):
        seen.append(json.loads(json.loads(request.content)['messages'][1]['content']))
        return httpx.Response(200, json=chat_reply(json.dumps(evolved_content())))

    web.handle(CHAT_URL, respond)

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: now[0])
        try:
            await service.fetch_sources()
            item = service.list_items().items[0]
            service.submit_feedback(item.id, rating=-1, note='outside window')
            now[0] += timedelta(days=2)
            with pytest.raises(ConfigError):
                await service.evolve_persona('default')
            service.submit_feedback(item.id, rating=-1, note='beyond count cap')
            now[0] += timedelta(seconds=1)
            first = service.submit_feedback(item.id, rating=-1, note='less hype')
            second = service.submit_feedback(item.id, rating=1, note='more robotics')
            await service.set_persona('other', profile='Other persona', skip_tags=True)
            service.submit_feedback(item.id, rating=-1, persona='other', note='different persona')
        finally:
            await service.aclose()
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: now[0])
        try:
            now[0] += timedelta(seconds=1)
            proposal = await service.evolve_persona('default', dry_run=True)
            assert proposal.feedback_count == 2
            assert [f['id'] for f in seen[-1]['feedback']] == [second.id, first.id]
            assert seen[-1]['feedback'][0]['title'] == 'Robotics research'
            assert seen[-1]['feedback'][0]['summary'] == 'Evidence'
            applied = await service.evolve_persona('default')
            assert applied.source == 'llm' and applied.version == 2
            [run] = service.list_runs(kind='persona', target='default')
            assert run.stats['feedback_count'] == 2 and run.cost == 0.01
            # The exact application timestamp is not later than the LLM version.
            service.submit_feedback(item.id, rating=1, note='same timestamp')
            with pytest.raises(ConfigError):
                await service.evolve_persona('default')
            now[0] += timedelta(seconds=1)
            latest = service.submit_feedback(item.id, rating=1, note='new evidence')
            now[0] += timedelta(seconds=1)
            await service.set_persona('default', include_add=['Robotics'])
            await service.rollback_persona('default', 1)
            evolved = await service.evolve_persona('default', dry_run=True)
            assert evolved.feedback_count == 1 and [f['id'] for f in seen[-1]['feedback']] == [latest.id]
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('content', [
    evolved_content(profile=' '), evolved_content(profile='x' * 501),
    evolved_content(summary=''), evolved_content(tags=[]),
    evolved_content(tags=[{'tag': 'AI'}] * 5),
    evolved_content(tags=[{'tag': ''}] * 5), evolved_content(include=[' ']),
    evolved_content(exclude=['x' * 257]), evolved_content(extra='unexpected'),
    'not JSON', None,
])
def test_invalid_evolution_is_atomic_and_preserves_known_billing(write_config, open_service, web, content):
    config = {**model_config(), 'personas': {'default': {'profile': 'Original'}},
              'persona_update': {'max_profile_chars': 500}}
    web.serve(CHAT_URL, json.dumps(chat_reply(json.dumps(content))), headers={'content-type': 'application/json'})
    service = open_service(write_config(config))

    async def scenario():
        with pytest.raises(UpstreamError, match='invalid persona evolution output'):
            await service.evolve_persona('default', instruction='More robotics')
        assert [v.profile for v in service.persona_history('default')] == ['Original']
        [run] = service.list_runs(kind='persona')
        assert run.status == 'failed' and run.cost == 0.01
        assert run.stats['usage']['total_tokens'] == 15
        assert 'not JSON' not in run.error

    asyncio.run(scenario())


def test_direct_evolution_rejects_concurrent_edit_without_losing_its_cost(write_config):
    path = write_config({**model_config(), 'personas': {'default': {'profile': 'Original'}}})

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=chat_reply(json.dumps(evolved_content())))

        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
        other = TentaclesService.open(path, env={})
        try:
            pending = asyncio.create_task(service.evolve_persona('default', instruction='More robotics'))
            await asyncio.wait_for(entered.wait(), 2)
            await other.set_persona('default', profile='Human edit', skip_tags=True)
            release.set()
            with pytest.raises(Conflict):
                await pending
            assert service.get_persona('default').profile == 'Human edit'
            run = next(r for r in service.list_runs(kind='persona') if r.status == 'failed')
            assert run.cost == 0.01 and run.stats['usage']['total_tokens'] == 15
            proposal = await service.evolve_persona('default', instruction='Preview', dry_run=True)
            await other.set_persona('default', include_add=['Manual keyword'])
            with pytest.raises(Conflict):
                await service.apply_persona(proposal)
            assert service.get_persona('default').include == ['Manual keyword']
        finally:
            release.set()
            await service.aclose()
            await other.aclose()

    asyncio.run(scenario())


def test_cancel_evolution_drains_http_and_records_failed_run_without_new_version(write_config):
    path = write_config({**model_config(), 'personas': {'default': {'profile': 'Original'}}})

    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def respond(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
        try:
            pending = asyncio.create_task(service.evolve_persona('default', instruction='More robotics'))
            await asyncio.wait_for(entered.wait(), 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert cleaned.is_set()
            assert service.get_persona('default').version == 1
            [run] = service.list_runs(kind='persona')
            assert run.status == 'failed'
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_reevaluation_since_limits_evaluations_but_never_resets_consumption(write_config, web):
    path = write_config(pipeline_config())
    now = [datetime(2026, 10, 5, tzinfo=timezone.utc)]
    web.serve(FEED, rss_feed({'guid': 'older', 'title': 'AI old'}))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: now[0])
        try:
            await service.fetch_sources()
            await service.run_pipeline('daily')  # consume the older item
            now[0] += timedelta(hours=3)
            web.serve(FEED, rss_feed({'guid': 'newer', 'title': 'AI new'}))
            await service.fetch_sources()
            await service.run_pipeline('daily')
            before = {i.title: service.get_item(i.id).evaluations[0].evaluated_at
                      for i in service.list_items().items}
            now[0] += timedelta(hours=1)
            outcome = await service.filter_pipeline('daily', reevaluate=True, since='2h')
            assert outcome.stats['evaluated'] == 1
            after = {i.title: service.get_item(i.id).evaluations[0].evaluated_at
                     for i in service.list_items().items}
            assert after['AI old'] == before['AI old']
            assert after['AI new'] == now[0] > before['AI new']
            assert (await service.run_pipeline('daily')).status == 'no_new'
            with pytest.raises(ConfigError):
                await service.filter_pipeline('daily', since='2h')
            with pytest.raises(ConfigError):
                await service.filter_pipeline('daily', reevaluate=True, since='invalid')
            with pytest.raises(ConfigError):
                await service.filter_pipeline('daily', reevaluate=True, since=now[0] + timedelta(hours=1))
            # An explicit since can also widen the normal pipeline window.
            now[0] += timedelta(days=2)
            outcome = await service.filter_pipeline('daily', reevaluate=True, since='3d')
            assert outcome.stats['evaluated'] == 2
            assert (await service.run_pipeline('daily')).status == 'no_new'
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_evolution_upstream_errors_do_not_expose_feedback_or_response(write_config, open_service, web):
    service = open_service(write_config({**model_config(), 'personas': {'default': {'profile': 'Private profile'}}}))
    web.serve(CHAT_URL, 'echoed private profile and feedback', status=400)

    async def scenario():
        with pytest.raises(UpstreamError) as failure:
            await service.evolve_persona('default', instruction='Private instruction')
        assert 'echoed private' not in str(failure.value)
        [run] = service.list_runs(kind='persona')
        assert 'private' not in run.error.lower()
        assert service.get_persona('default').version == 1

    asyncio.run(scenario())
