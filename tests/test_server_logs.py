"""Production Uvicorn logging contract, not just TestClient's error response."""
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest


@pytest.mark.parametrize('startup_failure', [False, True])
def test_real_server_never_logs_private_exception_content(tmp_path, startup_failure):
    marker = 'synthetic-private-provider-content-do-not-log'
    flag = tmp_path / 'fail-clock'
    if startup_failure:
        flag.touch()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    script = '''
from pathlib import Path
from datetime import datetime, timezone
import uvicorn
from tentacles.config import AppConfig
from tentacles.server import create_app
root = Path(ROOT)
def clock():
    if (root / 'fail-clock').exists():
        raise RuntimeError(MARKER)
    return datetime(2030, 1, 1, tzinfo=timezone.utc)
config = AppConfig.model_validate({'app': {'data_dir': str(root/'data'), 'reports_dir': str(root/'reports')},
                                  'server': {'scheduler': False}})
uvicorn.run(create_app(config, clock=clock), host='127.0.0.1', port=PORT, access_log=False, log_level='warning')
'''.replace('ROOT', repr(str(tmp_path))).replace('MARKER', repr(marker)).replace('PORT', str(port))
    process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if not startup_failure:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2) as client:
                deadline = time.monotonic() + 8
                while True:
                    if process.poll() is not None:
                        pytest.fail('server exited before readiness')
                    try:
                        if client.get('/api/health').status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    if time.monotonic() >= deadline:
                        pytest.fail('server readiness timeout')
                    time.sleep(0.02)
                flag.touch()
                response = client.get('/api/items?since=1d')
                assert response.status_code == 500 and response.json()['error']['code'] == 'error'
                assert marker not in response.text
            process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=8)
        assert marker not in stdout + stderr and 'Traceback' not in stderr
        if startup_failure:
            assert 'application lifecycle failed' in stderr
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=8)
