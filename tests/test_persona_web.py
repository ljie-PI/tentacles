"""Browser-required semantics through Service, with external HTTP substitutes."""
import asyncio
import json

import httpx
import pytest

from conftest import rss_feed
from test_models import CHAT_URL, chat_reply, model_config
from test_persona_evolution import evolved_content
from test_pipelines import FEED, pipeline_config
from tentacles.errors import Conflict


def test_explicit_versions_prevent_stale_edits_previews_and_rollback(write_config, open_service):
    service = open_service(write_config({**model_config(), **pipeline_config()}))

    async def scenario():
        current = await service.set_persona('default', profile='Current priorities', skip_tags=True, expected_version=1)
        assert current.version == 2
        with pytest.raises(Conflict):
            await service.set_persona('default', profile='Stale content', expected_version=1)
        with pytest.raises(Conflict):
            await service.set_persona('default', include_add=['Stale keyword'], skip_tags=True, expected_version=1)
        with pytest.raises(Conflict):
            await service.evolve_persona('default', instruction='Stale instruction', dry_run=True, expected_version=1)
        with pytest.raises(Conflict):
            await service.rollback_persona('default', 1, expected_version=1)
        assert service.get_persona('default') == current
        assert len(service.persona_history('default')) == 2
        restored = await service.rollback_persona('default', 1, expected_version=2)
        assert restored.version == 3

    asyncio.run(scenario())


def test_latest_feedback_is_readable_and_evolution_can_exclude_it(write_config, open_service, web):
    web.serve(FEED, rss_feed({'title': 'Research evidence', 'guid': 'one'}))
    expected_feedback = [False]

    def respond(request):
        body = json.loads(json.loads(request.content)['messages'][1]['content'])
        assert bool(body['feedback']) == expected_feedback[0]
        return httpx.Response(200, json=chat_reply(json.dumps(evolved_content())))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config({**model_config(), **pipeline_config()}))

    async def scenario():
        await service.fetch_sources()
        item = service.list_items().items[0]
        assert service.get_feedback(item.id, persona='default') is None
        service.submit_feedback(item.id, rating=1, persona='default')
        latest = service.submit_feedback(item.id, rating=-1, persona='default', note='Too speculative')
        assert service.get_feedback(item.id, persona='default') == latest
        excluded = await service.evolve_persona('default', instruction='More verified research', dry_run=True,
                                                use_feedback=False, expected_version=1)
        assert excluded.feedback_count == 0
        expected_feedback[0] = True
        included = await service.evolve_persona('default', dry_run=True, use_feedback=True, expected_version=1)
        assert included.feedback_count == 2  # append-only evidence remains unchanged
        assert service.get_persona('default').version == 1

    asyncio.run(scenario())


def test_latest_feedback_is_persistent_operation_order_even_when_clock_moves_back(write_config, web):
    from datetime import datetime, timedelta, timezone
    from tentacles.service import TentaclesService
    now = [datetime(2030, 1, 2, tzinfo=timezone.utc)]
    path = write_config(pipeline_config())
    web.serve(FEED, rss_feed({'title': 'Feedback ordering', 'guid': 'one'}))

    async def scenario():
        first = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: now[0])
        try:
            await first.fetch_sources()
            item = first.list_items().items[0]
            first.submit_feedback(item.id, rating=1)
            now[0] -= timedelta(hours=1)
            latest = first.submit_feedback(item.id, rating=-1)
        finally:
            await first.aclose()
        reopened = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: now[0])
        try:
            assert reopened.get_feedback(item.id) == latest
        finally:
            await reopened.aclose()

    asyncio.run(scenario())
