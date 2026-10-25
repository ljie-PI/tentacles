"""Persona/feedback HTTP contracts, using real domain operations."""
import json

import httpx

from conftest import rss_feed
from test_api import local_client
from test_models import CHAT_URL, chat_reply, model_config
from test_persona_evolution import evolved_content
from test_pipelines import FEED, pipeline_config
from tentacles.config import load_config
from tentacles.server import create_app


def persona_config():
    return {**model_config(), **pipeline_config(), 'server': {'scheduler': False}}


def test_feedback_persona_edit_preview_apply_and_rollback_contracts(write_config, web):
    web.serve(FEED, rss_feed({'title': 'Robotics evidence', 'guid': 'one'}))
    web.handle(CHAT_URL, lambda request: httpx.Response(200, json=chat_reply(json.dumps(evolved_content()))))
    path = write_config(persona_config())
    with local_client(create_app(load_config(path, env={}), transport=web.transport())) as client:
        assert client.post('/api/sources/news/fetch').status_code == 200
        item_id = client.get('/api/items').json()['items'][0]['id']
        assert client.get(f'/api/items/{item_id}/feedback?persona=default').json() is None
        feedback = client.post(f'/api/items/{item_id}/feedback', json={'rating': -1, 'persona': 'default'})
        assert feedback.status_code == 200 and feedback.json()['rating'] == -1
        assert client.get(f'/api/items/{item_id}/feedback?persona=default').json()['id'] == feedback.json()['id']
        assert client.get('/api/personas').status_code == 200
        assert client.get('/api/personas/current?name=default').json()['version'] == 1
        edit = {'name': 'default', 'expected_version': 1, 'profile': 'Edited interests', 'skip_tags': True}
        changed = client.patch('/api/personas', json=edit)
        assert changed.status_code == 200 and changed.json()['version'] == 2
        stale = client.patch('/api/personas', json=edit)
        assert stale.status_code == 409 and stale.json()['error']['code'] == 'conflict'
        assert client.get('/api/personas/current?name=default&version=1').json()['version'] == 1
        preview = client.post('/api/personas/evolve', json={'name': 'default', 'expected_version': 2,
                                                           'instruction': 'Less speculation', 'use_feedback': True})
        assert preview.status_code == 200 and preview.json()['feedback_count'] == 1
        assert client.get('/api/personas/current?name=default').json()['version'] == 2
        applied = client.post('/api/personas/apply', json=preview.json())
        assert applied.status_code == 200 and applied.json()['version'] == 3
        assert client.post('/api/personas/apply', json=preview.json()).status_code == 409
        restored = client.post('/api/personas/rollback', json={'name': 'default', 'version': 1, 'expected_version': 3})
        assert restored.status_code == 200 and restored.json()['version'] == 4
        history = client.get('/api/personas/history?name=default')
        assert history.status_code == 200 and len(history.json()) == 4
        ignored = client.post('/api/personas/evolve', json={'name': 'default', 'expected_version': 4,
                                                           'instruction': 'Use only my instruction', 'use_feedback': False})
        assert ignored.status_code == 200 and ignored.json()['feedback_count'] == 0


def test_persona_names_with_slashes_and_invalid_inputs_are_unambiguous(write_config):
    path = write_config(persona_config())
    with local_client(create_app(load_config(path, env={}))) as client:
        name = '旅行 / 工作'
        result = client.patch('/api/personas', json={'name': name, 'expected_version': 0,
                                                     'profile': 'Personal interests', 'skip_tags': True})
        assert result.status_code == 200 and result.json()['name'] == name
        assert client.get('/api/personas/current', params={'name': name}).json()['name'] == name
        assert client.get('/api/personas/history', params={'name': name}).status_code == 200
        for version in ['0', '9223372036854775808']:
            assert client.get('/api/personas/current', params={'name': name, 'version': version}).status_code == 400
        assert client.patch('/api/personas', json={'name': name, 'expected_version': True, 'profile': 'Bad'}).status_code == 400
        assert client.post('/api/personas/evolve', json={'name': name, 'expected_version': 1, 'use_feedback': False}).status_code == 400
        assert client.get('/api/personas/current?name=missing').status_code == 404


def test_feedback_validation_and_persona_operations_keep_security(write_config, web):
    data = persona_config();data['server']['token'] = 'browser-token'
    web.serve(FEED, rss_feed({'title': 'Feedback contract', 'guid': 'one'}))
    path = write_config(data)
    with local_client(create_app(load_config(path, env={}), transport=web.transport())) as client:
        for method, route in [('GET', '/api/personas'), ('PATCH', '/api/personas'),
                              ('POST', '/api/personas/apply'), ('GET', '/api/items/missing/feedback')]:
            assert client.request(method, route).status_code == 401
        headers = {'Authorization': 'Bearer browser-token'}
        client.headers.update(headers)
        assert client.post('/api/personas/evolve', headers={'Origin': 'http://untrusted.test'},
                           json={'name': 'default', 'expected_version': 1, 'instruction': 'More science'}).status_code == 403
        assert client.post('/api/sources/news/fetch').status_code == 200
        item_id = client.get('/api/items').json()['items'][0]['id']
        for rating in [True, 0, '1', 2]:
            assert client.post(f'/api/items/{item_id}/feedback', json={'rating': rating}).status_code == 400
        assert client.get('/api/items/missing/feedback').status_code == 404
        assert client.get(f'/api/items/{item_id}/feedback?persona=missing').status_code == 404
        assert client.patch('/api/personas', json={'name': 'default', 'profile': 'No version'}).status_code == 400
        assert client.patch('/api/personas', json={'name': 'default', 'expected_version': 1, 'tags': []}).status_code == 400


def test_profile_write_keeps_compare_and_swap_after_paid_tag_extraction(write_config, web):
    import asyncio
    import threading
    entered, release = threading.Event(), threading.Event()

    async def respond(request):
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return httpx.Response(200, json=chat_reply(json.dumps({'tags': evolved_content()['tags']})))

    path = write_config(persona_config())
    with local_client(create_app(load_config(path, env={}), transport=httpx.MockTransport(respond))) as client:
        responses = []
        worker = threading.Thread(target=lambda: responses.append(client.patch('/api/personas', json={
            'name': 'default', 'expected_version': 1, 'profile': 'Slow model edit'})))
        try:
            worker.start();assert entered.wait(3)
            winner = client.patch('/api/personas', json={'name': 'default', 'expected_version': 1,
                                                        'profile': 'Winning edit', 'skip_tags': True})
            assert winner.status_code == 200 and winner.json()['version'] == 2
        finally:
            release.set();worker.join(5)
        assert responses[0].status_code == 409
        assert client.get('/api/personas/current?name=default').json()['profile'] == 'Winning edit'
        failed = next(row for row in client.get('/api/runs?kind=persona').json() if row['status'] == 'failed')
        assert failed['cost'] == 0.01
