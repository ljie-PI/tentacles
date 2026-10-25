"""HTTP adapter contracts over the real Service with external HTTP substitutes."""
import httpx
from fastapi.testclient import TestClient

from conftest import rss_feed
from tentacles.config import load_config
from tentacles.server import create_app

FEED = 'https://news.test/rss'


def test_health_sources_items_runs_and_scheduler_contracts(write_config, web):
    web.serve(FEED, rss_feed({'title': 'API bulletin', 'guid': 'one', 'description': 'Readable body'}))
    path = write_config({'sources': [{'id': 'news', 'type': 'rss', 'url': FEED, 'schedule': '1h'}],
                         'server': {'scheduler': False}})
    with TestClient(create_app(load_config(path, env={}), transport=web.transport())) as client:
        assert client.get('/api/health').json() == {'ok': True}
        response = client.get('/api/sources')
        assert response.status_code == 200 and response.json()[0]['id'] == 'news'
        assert client.post('/api/sources/news/fetch').status_code == 200
        page = client.get('/api/items').json()
        assert page['total'] == 1
        item_id = page['items'][0]['id']
        assert client.get(f'/api/items/{item_id}').json()['content']
        assert client.get('/api/runs').json()[0]['kind'] == 'fetch'
        assert client.get('/api/scheduler').json()[0]['target'] == 'news'
        schema = client.get('/api/openapi.json').json()
        assert '/api/items' in schema['paths'] and '/api/sources/{source_id}/fetch' in schema['paths']


def test_bearer_auth_health_exception_and_openapi_security(write_config):
    path = write_config({'server': {'scheduler': False, 'token': 'local-test-token'}})
    with TestClient(create_app(load_config(path, env={}))) as client:
        assert client.get('/api/health').status_code == 200
        for route in ['/api/sources', '/api/items', '/api/runs', '/api/scheduler', '/api/openapi.json']:
            denied = client.get(route)
            assert denied.status_code == 401
            assert denied.json()['error']['code'] == 'unauthorized'
            assert denied.headers['www-authenticate'] == 'Bearer'
            assert client.get(route, headers={'Authorization': 'Bearer wrong'}).status_code == 401
            assert client.get(route, headers={'Authorization': 'Bearer local-test-token'}).status_code == 200
        schema = client.get('/api/openapi.json', headers={'Authorization': 'Bearer local-test-token'}).json()
        assert schema['paths']['/api/items']['get']['security'] == [{'HTTPBearer': []}]
        assert not schema['paths']['/api/health']['get'].get('security')


def test_errors_are_uniform_and_cors_is_allowlisted(write_config):
    path = write_config({'server': {'scheduler': False, 'cors_origins': ['http://trusted.test']}})
    with TestClient(create_app(load_config(path, env={})), raise_server_exceptions=False) as client:
        for route, code, status in [('/api/items/no-such-id', 'not_found', 404),
                                    ('/api/items?limit=0', 'invalid', 400),
                                    ('/api/items?limit=nope', 'invalid', 400),
                                    ('/api/items?kinds=wrong', 'invalid', 400),
                                    ('/api/missing-route', 'not_found', 404)]:
            response = client.get(route)
            assert response.status_code == status
            assert set(response.json()) == {'error'} and response.json()['error']['code'] == code
        response = client.options('/api/items', headers={'Origin': 'http://trusted.test',
                                                        'Access-Control-Request-Method': 'GET'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'http://trusted.test'
        response = client.options('/api/items', headers={'Origin': 'http://untrusted.test',
                                                        'Access-Control-Request-Method': 'GET'})
        assert response.status_code == 400 and 'access-control-allow-origin' not in response.headers


def test_scheduler_recovers_from_round_exception_and_can_be_disabled(write_config):
    import time
    from datetime import datetime, timezone
    flag = [False]

    def clock():
        if flag[0]:
            flag[0] = False
            raise RuntimeError('private clock failure')
        return datetime(2030, 1, 1, tzinfo=timezone.utc)

    path = write_config({'server': {'poll_interval': 0.01}})
    with TestClient(create_app(load_config(path, env={}), clock=clock)) as client:
        flag[0] = True
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = client.get('/api/scheduler/loop').json()
            if status['failed_cycles'] and status['cycles'] >= 3:
                break
            time.sleep(0.01)
        assert status['running'] and status['failed_cycles'] >= 1 and status['cycles'] >= 3
        assert status['last_error'] is None
    path = write_config({'server': {'scheduler': False}})
    with TestClient(create_app(load_config(path, env={}))) as client:
        status = client.get('/api/scheduler/loop').json()
        assert not status['enabled'] and not status['running'] and status['cycles'] == 0


def test_static_site_fallback_never_catches_api_or_reads_outside_dist(write_config, tmp_path):
    root = tmp_path / 'web'
    root.mkdir()
    (root / 'index.html').write_text('<h1>Tentacles application</h1>')
    (root / 'app.js').write_text('console.log("bundled")')
    secret = tmp_path / 'secret.txt'
    secret.write_text('private outside root')
    (root / 'escape.txt').symlink_to(secret)
    path = write_config({'server': {'scheduler': False, 'static_dir': str(root)}})
    with TestClient(create_app(load_config(path, env={}))) as client:
        for route in ['/', '/feed', '/sources', '/settings', '/unknown-non-api-path']:
            assert 'Tentacles application' in client.get(route).text
        assert 'console.log' in client.get('/app.js').text
        assert client.get('/api/missing').status_code == 404
        assert client.get('/api').status_code == 404
        assert 'private outside root' not in client.get('/escape.txt').text
        assert 'private outside root' not in client.get('/%2e%2e/secret.txt').text


def test_single_source_failure_maps_upstream_and_unexpected_clock_error_is_sanitized(write_config, web):
    from datetime import datetime, timezone
    flag = [False]

    def clock():
        if flag[0]:
            raise RuntimeError('private-test-token provider-secret')
        return datetime(2030, 1, 1, tzinfo=timezone.utc)

    path = write_config({'sources': [{'id': 'news', 'type': 'rss', 'url': FEED, 'schedule': '1h'}],
                         'server': {'scheduler': False}})
    web.serve(FEED, 'private upstream body', status=500)
    with TestClient(create_app(load_config(path, env={}), transport=web.transport(), clock=clock),
                    raise_server_exceptions=False) as client:
        response = client.post('/api/sources/news/fetch')
        assert response.status_code == 502 and response.json()['error']['code'] == 'upstream'
        assert 'private' not in response.text
        flag[0] = True
        response = client.get('/api/items?since=1d')
        assert response.status_code == 500 and response.json()['error']['code'] == 'error'
        assert 'private' not in response.text and 'provider-secret' not in response.text


def test_shutdown_drains_scheduler_before_closing_service(write_config):
    import asyncio
    import threading
    entered, release = threading.Event(), threading.Event()

    async def respond(request):
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return httpx.Response(200, text=rss_feed({'title': 'Drained bulletin', 'guid': 'one'}))

    path = write_config({'sources': [{'id': 'news', 'type': 'rss', 'url': FEED, 'schedule': '1h'}]})
    client = TestClient(create_app(load_config(path, env={}), transport=httpx.MockTransport(respond)))
    thread = None
    try:
        client.__enter__()
        assert entered.wait(3)
        thread = threading.Thread(target=lambda: client.__exit__(None, None, None))
        thread.start()
        thread.join(0.05)
        assert thread.is_alive()
        release.set()
        thread.join(4)
        assert not thread.is_alive()
    finally:
        release.set()
        if thread:
            thread.join(5)
        else:
            client.__exit__(None, None, None)
    data = {'sources': [{'id': 'news', 'type': 'rss', 'url': FEED, 'schedule': '1h'}],
            'server': {'scheduler': False}}
    path = write_config(data)
    with TestClient(create_app(load_config(path, env={}))) as reopened:
        runs = reopened.get('/api/runs?kind=fetch').json()
        assert runs[0]['status'] == 'ok'
        assert reopened.get('/api/items').json()['total'] == 1


def test_single_source_busy_maps_to_409_with_real_shared_lease(write_config):
    import asyncio
    import threading
    entered, release = threading.Event(), threading.Event()

    async def respond(request):
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return httpx.Response(200, text=rss_feed())

    path = write_config({'sources': [{'id': 'news', 'type': 'rss', 'url': FEED, 'schedule': '1h'}],
                         'server': {'scheduler': False}})
    with TestClient(create_app(load_config(path, env={}), transport=httpx.MockTransport(respond))) as client:
        thread = threading.Thread(target=lambda: client.post('/api/sources/news/fetch'))
        try:
            thread.start()
            assert entered.wait(3)
            response = client.post('/api/sources/news/fetch')
            assert response.status_code == 409 and response.json()['error']['code'] == 'busy'
        finally:
            release.set()
            thread.join(5)


def test_mutation_body_typos_are_invalid_not_broad_fetches(write_config):
    path = write_config({'server': {'scheduler': False}})
    with TestClient(create_app(load_config(path, env={}))) as client:
        response = client.post('/api/sources/fetch', json={'id_typo': ['secret-input']})
        assert response.status_code == 400 and response.json()['error']['code'] == 'invalid'
        assert 'secret-input' not in response.text


def test_unexpected_error_keeps_cors_headers_for_allowed_origin(write_config):
    from datetime import datetime, timezone
    fail = [False]

    def clock():
        if fail[0]:
            raise RuntimeError('private provider content')
        return datetime(2030, 1, 1, tzinfo=timezone.utc)

    path = write_config({'server': {'scheduler': False, 'cors_origins': ['http://trusted.test']}})
    with TestClient(create_app(load_config(path, env={}), clock=clock), raise_server_exceptions=False) as client:
        fail[0] = True
        response = client.get('/api/items?since=1d', headers={'Origin': 'http://trusted.test'})
        assert response.status_code == 500 and response.headers['access-control-allow-origin'] == 'http://trusted.test'
        assert 'private' not in response.text


def test_bearer_token_is_not_forwarded_to_source_http(write_config, web):
    path = write_config({'server': {'scheduler': False, 'token': 'browser-only-token'},
                         'sources': [{'id': 'news', 'type': 'rss', 'url': FEED, 'schedule': '1h'}]})

    def respond(request):
        assert 'authorization' not in request.headers
        return httpx.Response(200, text=rss_feed())

    web.handle(FEED, respond)
    with TestClient(create_app(load_config(path, env={}), transport=web.transport())) as client:
        response = client.post('/api/sources/news/fetch', headers={'Authorization': 'Bearer browser-only-token'})
        assert response.status_code == 200
