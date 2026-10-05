from fastapi.testclient import TestClient

from tangtang_harness import app as app_module
from tangtang_harness.config import HarnessConfig
from tangtang_harness.runtime import Runtime
from tangtang_harness.public_gateway import create_public_gateway


def test_watchdog_console_reads_supervisor_history_from_runtime_root(tmp_path, monkeypatch):
    runtime = Runtime(HarnessConfig(root=tmp_path))
    calls = []
    snapshot = {
        'enabled': True,
        'last_check_at': 1900000000.0,
        'last_check_outcome': 'succeeded',
        'services': [
            {'service': 'harness', 'desired': True, 'observed_state': 'running', 'pid': 101},
            {'service': 'speech', 'desired': False, 'observed_state': 'disabled', 'pid': None},
        ],
        'events': [{'id': 1, 'ts': 1900000000.0, 'service': 'harness',
                    'event': 'recovery_succeeded', 'source': 'watchdog',
                    'reason': '进程退出', 'outcome': 'succeeded', 'pid': 101, 'detail': ''}],
        'counts': {'recoveries_24h': 1, 'failures_24h': 0},
    }

    class ReadOnlyStore:
        def __init__(self, root):
            assert root == tmp_path

        def view(self, *, limit=100):
            calls.append(limit)
            return snapshot

    monkeypatch.setattr(app_module, 'SupervisorStore', ReadOnlyStore)
    client = TestClient(app_module.create_app(runtime=runtime))
    assert client.get('/api/watchdog').json() == snapshot
    assert client.get('/api/watchdog?limit=25').json() == snapshot
    assert calls == [100, 25]
    assert runtime.config.mode == 'observe'
    assert not runtime.store.requests()
    assert not (tmp_path / 'data' / 'supervisor.db').exists()


def test_watchdog_history_stays_off_the_public_business_gateway(tmp_path):
    runtime = Runtime(HarnessConfig(root=tmp_path))
    app = app_module.create_app(runtime=runtime)
    client = TestClient(create_public_gateway(app, base_url='https://bot.example.invalid'))
    assert client.get('/api/watchdog').status_code == 404
    assert not (tmp_path / 'data' / 'supervisor.db').exists()


def test_watchdog_console_does_not_create_guard_state_or_allow_control(tmp_path):
    runtime = Runtime(HarnessConfig(root=tmp_path))
    client = TestClient(app_module.create_app(runtime=runtime))
    result = client.get('/api/watchdog')
    assert result.status_code == 200
    assert result.json()['enabled'] is False
    assert result.json()['events'] == []
    assert not (tmp_path / 'data' / 'supervisor.db').exists()
    assert client.post('/api/watchdog', json={'enabled': True}).status_code == 405
    for limit in ('0', '-1', '501', 'invalid'):
        assert client.get('/api/watchdog?limit=' + limit).status_code == 422
    assert not (tmp_path / 'data' / 'supervisor.db').exists()
