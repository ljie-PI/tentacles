"""Management HTTP contracts; the Service and SQLite remain real."""
import httpx

from conftest import rss_feed
from test_api import local_client
from test_models import CHAT_URL, chat_reply, model_config
from test_pipelines import FEED, pipeline_config, three_entries
from tentacles.config import load_config
from tentacles.server import create_app


def management_config(**processor):
    return {**model_config(), **pipeline_config(processor={'type': 'digest', **processor}),
            'server': {'scheduler': False}}


def test_pipeline_report_model_routes_and_typed_contracts(write_config, web):
    web.serve(FEED, three_entries())
    web.handle(CHAT_URL, lambda request: httpx.Response(200, json=chat_reply('## Findings\n\nFacts [1].')))
    path = write_config(management_config())
    with local_client(create_app(load_config(path, env={}), transport=web.transport())) as client:
        assert client.post('/api/sources/news/fetch').status_code == 200
        pipelines = client.get('/api/pipelines')
        assert pipelines.status_code == 200 and pipelines.json()[0]['id'] == 'daily'
        filtered = client.post('/api/pipelines/daily/filter')
        assert filtered.status_code == 200 and filtered.json()['pipeline_id'] == 'daily'
        outcome = client.post('/api/pipelines/daily/run')
        assert outcome.status_code == 200 and outcome.json()['status'] == 'ok'
        report_id = outcome.json()['report_id']
        page = client.get('/api/reports?pipeline=daily&limit=5&offset=0')
        assert page.status_code == 200 and page.json()['items'][0]['id'] == report_id
        detail = client.get(f'/api/reports/{report_id}')
        assert detail.status_code == 200 and isinstance(detail.json()['item_ids'], list)
        assert isinstance(detail.json()['markdown'], str)
        models = client.get('/api/models')
        assert models.status_code == 200 and next(row for row in models.json() if row['name'] == 'fast')['key_configured']
        assert 'private-test-key' not in models.text
        probe = client.post('/api/models/fast/test')
        assert probe.status_code == 200 and probe.json()['ok']
        assert isinstance(probe.json()['latency_ms'], (int, float))
        schema = client.get('/api/openapi.json').json()
        for route in ['/api/pipelines', '/api/reports', '/api/models/{name}/test']:
            assert route in schema['paths']


def test_management_error_and_reevaluation_body_contracts(write_config, web):
    web.serve(FEED, three_entries())
    web.serve(CHAT_URL, 'private-test-key provider denied', status=500)
    path = write_config(management_config())
    with local_client(create_app(load_config(path, env={}), transport=web.transport())) as client:
        for method, route in [('GET', '/api/reports/missing'), ('POST', '/api/pipelines/missing/run'),
                              ('POST', '/api/models/missing/test')]:
            response = client.request(method, route)
            assert response.status_code == 404 and response.json()['error']['code'] == 'not_found'
        for query in ['limit=0', 'offset=9223372036854775808']:
            response = client.get(f'/api/reports?{query}')
            assert response.status_code == 400 and response.json()['error']['code'] == 'invalid'
        assert client.post('/api/pipelines/daily/filter', json={'reevaluate_typo': True}).status_code == 400
        assert client.post('/api/pipelines/daily/filter', json={'since': '7d'}).status_code == 400
        assert client.post('/api/sources/news/fetch').status_code == 200
        failed = client.post('/api/pipelines/daily/run')
        assert failed.status_code == 502 and failed.json()['error']['code'] == 'upstream'
        probe = client.post('/api/models/fast/test')
        assert probe.status_code == 200 and probe.json()['ok'] is False
        assert probe.json()['error'] and 'private-test-key' not in probe.text


def test_new_management_operations_keep_bearer_and_origin_guards(write_config):
    data = management_config()
    data['server']['token'] = 'browser-token'
    path = write_config(data)
    with local_client(create_app(load_config(path, env={}))) as client:
        for route in ['/api/pipelines/daily/filter', '/api/pipelines/daily/run',
                      '/api/models/fast/test', '/api/scheduler/tick']:
            assert client.post(route).status_code == 401
            rejected = client.post(route, headers={'Authorization': 'Bearer browser-token', 'Origin': 'http://untrusted.test'})
            assert rejected.status_code == 403 and rejected.json()['error']['code'] == 'forbidden'
        assert client.get('/api/runs', headers={'Authorization': 'Bearer browser-token'}).json() == []


def test_busy_pipeline_maps_to_conflict_without_starting_another_operation(write_config, web):
    import asyncio
    import threading
    entered, release = threading.Event(), threading.Event()
    path = write_config(management_config())

    async def respond(request):
        if request.url == FEED:
            return httpx.Response(200, text=three_entries())
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return httpx.Response(200, json=chat_reply('Facts [1].'))

    with local_client(create_app(load_config(path, env={}), transport=httpx.MockTransport(respond))) as client:
        assert client.post('/api/sources/news/fetch').status_code == 200
        thread = threading.Thread(target=lambda: client.post('/api/pipelines/daily/run'))
        try:
            thread.start()
            assert entered.wait(3)
            response = client.post('/api/pipelines/daily/run')
            assert response.status_code == 409 and response.json()['error']['code'] == 'busy'
        finally:
            release.set()
            thread.join(5)
        assert client.get('/api/reports').status_code == 200
