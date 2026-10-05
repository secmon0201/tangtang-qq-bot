import asyncio
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

import psutil
import pytest

from tangtang_harness.speech_runtime import SpeechRuntimeError, SpeechRuntimeManager


API_SOURCE = '''
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('-a')
parser.add_argument('-p')
parser.add_argument('-c')
args = parser.parse_args()
from GPT_SoVITS.text import english
config = Path(args.c)
weight_path = Path(config.read_text(encoding='utf-8').splitlines()[0])
weight = weight_path.read_bytes().decode()
config.write_text(str(weight_path) + '\\nupdated by synthetic API\\n', encoding='utf-8')
cache = Path(os.environ['HF_HOME'])
cache.mkdir(parents=True, exist_ok=True)
(cache / 'synthetic.cache').write_text('cache', encoding='utf-8')
output = Path(os.environ['TANGTANG_SPEECH_OUTPUT'])
(output / 'synthetic.wav').write_bytes(b'RIFF synthetic')
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'],
    creationflags=subprocess.CREATE_NO_WINDOW)
Path('ready.json').write_text(json.dumps({
    'pid': os.getpid(), 'child_pid': child.pid, 'cwd': os.getcwd(),
    'script': __file__, 'args': vars(args), 'weight': weight,
    'environment': {name: os.environ[name] for name in
        ('TEMP', 'TMP', 'TMPDIR', 'HF_HOME', 'TORCH_HOME', 'XDG_CACHE_HOME',
         'NUMBA_CACHE_DIR', 'MPLCONFIGDIR', 'TANGTANG_SPEECH_OUTPUT', 'NLTK_DATA')}
}), encoding='utf-8')
while True:
    time.sleep(1)
'''


def hashes(directory):
    return {str(file.relative_to(directory)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in directory.rglob('*') if file.is_file()}


def wait_ready(manager):
    path = manager.source / 'ready.json'
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                pass
        if manager._child is not None and manager._child.poll() is not None:
            pytest.fail((manager.directory / 'server.log').read_text(encoding='utf-8'))
        time.sleep(.02)
    pytest.fail('Synthetic API did not become ready')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / 'harness'
    vendor = tmp_path / 'vendor'
    vendor.mkdir()
    api = vendor / 'api_v2.py'
    api.write_text(API_SOURCE, encoding='utf-8')
    text = vendor / 'GPT_SoVITS' / 'text'
    text.mkdir(parents=True)
    (text / 'english.py').write_text(
        "from pathlib import Path\nPath(__file__).with_name('engdict_cache.pickle').write_bytes(b'cache')\n",
        encoding='utf-8')
    (text / 'cmudict.rep').write_text('small resource', encoding='utf-8')
    (text / 'unused.bin').write_bytes(b'do not copy model binaries')
    weights = vendor / 'weights.ckpt'
    weights.write_bytes(b'synthetic read-only weights')
    legacy_configs = vendor / 'GPT_SoVITS' / 'configs'
    legacy_configs.mkdir()
    (legacy_configs / 'tts_infer.yaml').write_text('legacy config remains unchanged', encoding='utf-8')
    config = root / 'config'
    config.mkdir(parents=True)
    yaml = config / 'tts.yaml'
    yaml.write_text(str(weights) + '\n', encoding='utf-8')
    values = {'enabled': True, 'python': sys.executable, 'api_script': str(api),
              'tts_config': str(yaml), 'retry_seconds': 15}
    (config / 'speech.json').write_text(json.dumps(values), encoding='utf-8')
    manager = SpeechRuntimeManager(root)
    monkeypatch.setattr(manager, '_port_available', lambda: True)
    initial = hashes(vendor)
    yield manager, vendor, initial, values
    manager.stop()


def test_disabled_default_has_no_process_or_runtime_files(tmp_path):
    manager = SpeechRuntimeManager(tmp_path)
    assert manager.status()['state'] == 'disabled'
    assert manager.start()['state'] == 'disabled'
    assert not manager.directory.exists()


def test_real_synthetic_process_owns_all_writes_and_stops_tree(runtime):
    manager, vendor, initial, values = runtime
    started = manager.start()
    assert started['state'] == 'running'
    ready = wait_ready(manager)
    assert ready['args']['a'] == '127.0.0.1' and ready['args']['p'] == '9890'
    assert ready['args']['c'] == values['tts_config']
    assert ready['weight'] == 'synthetic read-only weights'
    assert Path(ready['cwd']).is_relative_to(manager.directory)
    assert Path(ready['script']).is_relative_to(manager.directory)
    assert all(Path(path).is_relative_to(manager.directory) for path in ready['environment'].values())
    assert (manager.source / 'GPT_SoVITS/text/engdict_cache.pickle').read_bytes() == b'cache'
    assert (manager.source / 'GPT_SoVITS/text/cmudict.rep').read_text() == 'small resource'
    assert not (manager.source / 'GPT_SoVITS/text/unused.bin').exists()
    assert Path(values['tts_config']).read_text().endswith('updated by synthetic API\n')
    assert hashes(vendor) == initial
    assert manager.start()['pid'] == started['pid']
    # A separate CLI/health-manager instance can identify and stop the same process.
    restored = SpeechRuntimeManager(manager.root)
    assert restored.status()['pid'] == started['pid']
    assert restored.start()['pid'] == started['pid']
    restored.stop()
    assert not psutil.pid_exists(ready['pid'])
    assert not psutil.pid_exists(ready['child_pid'])
    assert not psutil.pid_exists(started['pid'])
    assert restored.status()['state'] == 'stopped'
    assert hashes(vendor) == initial


def test_harness_stop_closes_orphan_speech_without_disabling_saved_gate(runtime):
    from tangtang_harness.harness_process import stop

    manager, vendor, initial, values = runtime
    manager.start()
    ready = wait_ready(manager)
    assert stop(manager.root)['state'] == 'stopped'
    assert not psutil.pid_exists(ready['pid'])
    assert not psutil.pid_exists(ready['child_pid'])
    assert not manager.stop_path.exists()
    assert json.loads(manager.config_path.read_text(encoding='utf-8')) == values
    assert hashes(vendor) == initial


def test_mismatched_identity_never_terminates_recorded_pid(runtime):
    manager, _, _, _ = runtime
    manager.start()
    ready = wait_ready(manager)
    record = manager._record()
    tampered = {**record, 'created': record['created'] - 100}
    manager.record_path.write_text(json.dumps(tampered), encoding='utf-8')
    try:
        assert manager.status()['state'] == 'error'
        with pytest.raises(SpeechRuntimeError, match='未操作'):
            manager.stop()
        assert psutil.pid_exists(ready['pid']) and psutil.pid_exists(ready['child_pid'])
    finally:
        manager.record_path.write_text(json.dumps(record), encoding='utf-8')
        manager.stop()


@pytest.mark.parametrize('field,value,error', [
    ('port', 9880, '9890'),
    ('host', '0.0.0.0', '127.0.0.1'),
    ('python', 'relative/python.exe', '绝对路径'),
    ('api_script', 'relative/api.py', '绝对路径'),
])
def test_bad_input_never_launches(runtime, field, value, error):
    manager, _, _, values = runtime
    manager.config_path.write_text(json.dumps({**values, field: value}), encoding='utf-8')
    with pytest.raises(SpeechRuntimeError, match=error):
        manager.start()
    assert manager._child is None


def test_foreign_yaml_and_occupied_port_never_launch(runtime):
    manager, vendor, initial, values = runtime
    foreign = vendor / 'GPT_SoVITS/configs/tts_infer.yaml'
    manager.config_path.write_text(json.dumps({**values, 'tts_config': str(foreign)}), encoding='utf-8')
    with pytest.raises(SpeechRuntimeError, match='不能使用旧'):
        manager.start()
    manager.config_path.write_text(json.dumps(values), encoding='utf-8')
    manager._port_available = lambda: False
    with pytest.raises(SpeechRuntimeError, match='未停止占用'):
        manager.start()
    assert manager._child is None and hashes(vendor) == initial


@pytest.mark.asyncio
async def test_background_retry_is_bounded_and_manual_stop_is_preserved(runtime):
    manager, _, _, _ = runtime
    first = await manager.ensure_running()
    ready = await asyncio.to_thread(wait_ready, manager)
    # Terminate the real synthetic tree without recording an operator stop.
    manager.stop(disable=False)
    assert not manager.stop_path.exists()
    # Simulate an exited service during the already-open retry interval.
    manager._retry_at = time.monotonic() + 15
    assert (await manager.ensure_running())['pid'] is None
    manager._retry_at = 0
    (manager.source / 'ready.json').unlink()
    recovered = await manager.ensure_running()
    current = await asyncio.to_thread(wait_ready, manager)
    assert recovered['pid'] != first['pid']
    assert current['pid'] != ready['pid']
    manager.stop()
    manager._retry_at = 0
    assert (await manager.ensure_running())['state'] == 'stopped'
    assert manager._child.poll() is not None
    (manager.source / 'ready.json').unlink()
    assert manager.start()['state'] == 'running'
    await asyncio.to_thread(wait_ready, manager)
    assert not manager.stop_path.exists()


@pytest.mark.asyncio
async def test_background_failure_is_explicit_and_does_not_repeatedly_spawn(runtime):
    manager, _, _, _ = runtime
    attempts = []
    def blocked_port():
        attempts.append(1)
        return False
    manager._port_available = blocked_port
    first = await manager.ensure_running()
    assert '9890' in first['error'] and first['next_retry_seconds'] > 0
    await asyncio.gather(*(manager.ensure_running() for _ in range(5)))
    assert len(attempts) == 1 and manager._child is None


@pytest.mark.asyncio
async def test_cancelled_background_launch_finishes_before_shutdown_stop(runtime):
    manager, _, _, _ = runtime
    entered, release = threading.Event(), threading.Event()
    original = manager._start
    def slow_start(explicit):
        entered.set()
        assert release.wait(timeout=5)
        return original(explicit)
    manager._start = slow_start
    task = asyncio.create_task(manager.ensure_running())
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    ready = await asyncio.to_thread(wait_ready, manager)
    manager.stop(disable=False)
    assert not psutil.pid_exists(ready['pid'])
    assert not psutil.pid_exists(ready['child_pid'])
