"""No source URL, provider content or raw exception text reaches diagnostics."""
import logging

import httpx
import pytest

from test_api import local_client
from tentacles.config import load_config
from tentacles.server import create_app


@pytest.mark.parametrize('failure', ['unexpected', 'httpx', 'http', 'parse'])
def test_source_failure_diagnostics_are_safe_in_logs_and_public_state(write_config, caplog, failure):
    marker = 'synthetic-private-source-token'
    url = f'https://fixture.test/rss?access_token={marker}'
    path = write_config({'server': {'scheduler': False}, 'sources': [
        {'id': 'news', 'type': 'rss', 'url': url, 'schedule': '1h', 'headers': {'Authorization': marker}}]})

    def respond(request):
        if failure == 'unexpected':
            raise RuntimeError(marker)
        if failure == 'httpx':
            raise httpx.ReadError(marker, request=request)
        if failure == 'http':
            return httpx.Response(503, text=marker)
        return httpx.Response(200, text=f'<broken>{marker}</mismatch>')

    with caplog.at_level(logging.WARNING), local_client(create_app(load_config(path, env={}), transport=httpx.MockTransport(respond))) as client:
        response = client.post('/api/sources/news/fetch')
        assert response.status_code == 502
        runs, sources = client.get('/api/runs'), client.get('/api/sources')
        assert runs.json()[0]['status'] == 'failed' and sources.json()[0]['last_status'] == 'error'
        assert marker not in response.text + runs.text + sources.text + caplog.text
        assert url not in caplog.text and 'Traceback' not in caplog.text
        if failure == 'http':
            assert 'HTTP 503' in sources.json()[0]['last_error']
