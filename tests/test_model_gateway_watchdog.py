import importlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gateway():
    assert (ROOT / 'scripts/model_gateway.py').is_file(), 'Local model gateway supervision is missing'
    return importlib.import_module('scripts.model_gateway')


def test_gateway_config_is_opt_in_and_rejects_remote_health_urls(gateway, tmp_path):
    assert gateway.load_config({}) is None
    script = tmp_path / 'server.js'
    script.write_text('')
    values = dict(MODEL_GATEWAY_ENABLED='true', MODEL_GATEWAY_SCRIPT=str(script),
                  MODEL_GATEWAY_NODE=shutil.which('node.exe'),
                  MODEL_GATEWAY_HEALTH_URL='https://example.invalid/v1/models')
    with pytest.raises(ValueError, match='loopback'):
        gateway.load_config(values)
    values['MODEL_GATEWAY_HEALTH_URL'] = 'http://127.0.0.1:3123/v1/models'
    assert gateway.load_config(values).script == script.resolve()


def test_process_ownership_resolves_relative_entrypoint_and_rejects_other_projects(gateway, tmp_path):
    config = SimpleNamespace(script=tmp_path / 'server.js', node=Path('C:/runtime/node.exe'))
    owned = SimpleNamespace(info=dict(pid=123, exe=str(config.node), cmdline=['node', 'server.js'], cwd=str(tmp_path)))
    foreign = SimpleNamespace(info=dict(pid=456, exe=str(config.node), cmdline=['node', 'server.js'], cwd=str(tmp_path/'other')))
    assert gateway.owns_process(config, owned)
    assert not gateway.owns_process(config, foreign)
    owned.info['cmdline'] = ['node', '-e', 'server.js']
    assert not gateway.owns_process(config, owned)


def test_foreign_listener_is_never_restarted(gateway, monkeypatch, tmp_path):
    config = SimpleNamespace()
    monkeypatch.setattr(gateway, 'inspect_gateway', lambda _: dict(status='foreign_listener', healthy=False, pids=[]))
    monkeypatch.setattr(gateway, 'launch_gateway', lambda *_: pytest.fail('Must not start a second service'))
    assert gateway.recover_gateway(config, tmp_path)['status'] == 'foreign_listener'


def test_healthy_gateway_does_not_restart(gateway, monkeypatch, tmp_path):
    monkeypatch.setattr(gateway, 'inspect_gateway', lambda _: dict(status='healthy', healthy=True, pids=[123]))
    monkeypatch.setattr(gateway, 'launch_gateway', lambda *_: pytest.fail('Healthy process must remain'))
    assert gateway.recover_gateway(SimpleNamespace(), tmp_path)['healthy']


def test_missing_gateway_is_launched_and_rechecked(gateway, monkeypatch, tmp_path):
    states = iter([dict(status='missing', healthy=False, pids=[]), dict(status='healthy', healthy=True, pids=[123])])
    monkeypatch.setattr(gateway, 'inspect_gateway', lambda _: next(states))
    calls = []
    monkeypatch.setattr(gateway, 'launch_gateway', lambda *_: calls.append('launch'))
    assert gateway.recover_gateway(SimpleNamespace(), tmp_path)['healthy']
    assert calls == ['launch']


def test_health_probe_is_bounded_and_ignores_proxy_environment(gateway, monkeypatch):
    import httpx
    seen = {}
    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, *args, **kwargs):
            raise httpx.ReadTimeout('synthetic unresponsive gateway')
    monkeypatch.setattr(gateway.httpx, 'Client', Client)
    assert not gateway.probe_health(SimpleNamespace(health_url='http://127.0.0.1:3123/v1/models', api_key=''))
    assert seen['trust_env'] is False
    assert seen['timeout'] <= 3


@pytest.mark.parametrize('status_code', [401, 429, 502])
def test_responsive_gateway_error_does_not_trigger_process_restart(gateway, monkeypatch, status_code):
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=status_code)
    monkeypatch.setattr(gateway.httpx, 'Client', Client)
    with pytest.raises(gateway.HealthResponseError):
        gateway.probe_health(SimpleNamespace(health_url='http://127.0.0.1:3123/v1/models', api_key=''))


def test_unresponsive_gateway_is_stopped_before_launch(gateway, monkeypatch, tmp_path):
    states = iter([
        dict(status='unresponsive', healthy=False, pids=[123], identities={'123': 42}),
        dict(status='missing', healthy=False, pids=[]),
        dict(status='healthy', healthy=True, pids=[456]),
    ])
    monkeypatch.setattr(gateway, 'inspect_gateway', lambda _: next(states))
    calls = []
    monkeypatch.setattr(gateway, 'stop_owned_gateway', lambda cfg, pid, created: calls.append(('stop', pid, created)))
    monkeypatch.setattr(gateway, 'launch_gateway', lambda *_: calls.append(('launch',)))
    assert gateway.recover_gateway(SimpleNamespace(), tmp_path)['healthy']
    assert calls == [('stop', 123, 42), ('launch',)]


def test_reused_pid_is_not_terminated(gateway, monkeypatch, tmp_path):
    config = SimpleNamespace(script=tmp_path/'server.js', node=Path('C:/runtime/node.exe'))
    class Process:
        def as_dict(self, **kwargs):
            return dict(exe=str(config.node), cmdline=['node', str(config.script)], cwd=str(tmp_path))
        def create_time(self):
            return 99
        def terminate(self):
            pytest.fail('Reused PID must not be stopped')
    monkeypatch.setattr(gateway.psutil, 'Process', lambda _: Process())
    with pytest.raises(RuntimeError, match='ownership'):
        gateway.stop_owned_gateway(config, 123, 42)


def test_listener_owned_by_another_process_is_reported(gateway, monkeypatch, tmp_path):
    config = SimpleNamespace(script=tmp_path/'server.js', node=Path('C:/runtime/node.exe'), port=3123)
    monkeypatch.setattr(gateway.psutil, 'process_iter', lambda *_: [])
    monkeypatch.setattr(gateway.psutil, 'net_connections', lambda **_: [
        SimpleNamespace(pid=999, status=gateway.psutil.CONN_LISTEN, laddr=SimpleNamespace(port=3123))])
    monkeypatch.setattr(gateway, 'probe_health', lambda _: pytest.fail('Foreign service must not be probed'))
    assert gateway.inspect_gateway(config)['status'] == 'foreign_listener'


def test_real_hung_node_gateway_recovers_without_touching_foreign_listener(gateway, tmp_path):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    folder = tmp_path / 'gateway with spaces'
    folder.mkdir()
    script = folder / 'server.js'
    script.write_text(f"require('http').createServer((req,res)=>{{}}).listen({port}, '127.0.0.1');")
    config = gateway.load_config(dict(MODEL_GATEWAY_ENABLED='true', MODEL_GATEWAY_SCRIPT=str(script),
        MODEL_GATEWAY_NODE=shutil.which('node.exe'), MODEL_GATEWAY_HEALTH_URL=f'http://127.0.0.1:{port}/v1/models'))
    gateway.launch_gateway(config, tmp_path)
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail('Fixture gateway failed to listen')
                time.sleep(0.1)
        script.write_text("require('http').createServer((req,res)=>{res.setHeader('Content-Type','application/json');"
            "res.end(JSON.stringify({data:[{id:'synthetic-model'}]}));})"
            f".listen({port}, '127.0.0.1');")
        recovered = gateway.recover_gateway(config, tmp_path)
        assert recovered['healthy']
        # A different configured entrypoint must not adopt or stop the same listener.
        other_script = folder / 'other.js'
        other_script.write_text('')
        foreign = gateway.GatewayConfig(other_script, config.node, config.health_url, config.port, '')
        assert gateway.recover_gateway(foreign, tmp_path)['status'] == 'foreign_listener'
        assert gateway.inspect_gateway(config)['pids'] == recovered['pids']
    finally:
        state = gateway.inspect_gateway(config)
        for process_id in state['pids']:
            gateway.stop_owned_gateway(config, process_id, state['identities'][str(process_id)])


def test_watchdog_gateway_threshold_cooldown_and_check_isolation(tmp_path):
    probe = ROOT / 'tests/fixtures/watchdog_gateway_probe.ps1'
    assert probe.is_file(), 'Gateway watchdog behavior probe is missing'
    completed = subprocess.run([shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', str(probe), '-WatchdogPath', str(ROOT/'scripts/watch_qq_transport.ps1')],
        capture_output=True, text=True, timeout=15, check=True)
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == dict(recoveries=1, checks=8, isolated=True)
