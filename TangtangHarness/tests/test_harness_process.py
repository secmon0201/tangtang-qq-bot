import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import psutil

from tangtang_harness.harness_process import HarnessProcessError, status, stop


ROOT = Path(__file__).resolve().parents[1]


def wait_port(port):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                return
        time.sleep(.1)
    raise AssertionError(f'port {port} did not open')


def test_isolated_harness_lifecycle_stops_only_its_process(tmp_path):
    root = tmp_path / 'harness'
    root.mkdir()
    command = [sys.executable, '-m', 'tangtang_harness', '--root', str(root), '--port', '8127']
    process = subprocess.Popen(command, cwd=ROOT, env={**__import__('os').environ, 'PYTHONUNBUFFERED': '1'},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_port(8127)
        assert status(root)['state'] == 'running'
        record = json.loads((root / 'data' / 'process.json').read_text(encoding='utf-8'))
        assert record['port'] == 8127
        assert record['pid'] == process.pid or record['pid'] in [child.pid for child in psutil.Process(process.pid).children(recursive=True)]
        result = stop(root)
        assert result['state'] == 'stopped'
        process.wait(timeout=5)
        assert not psutil.pid_exists(process.pid)
        assert not (root / 'data' / 'process.json').exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_wrong_process_record_is_refused(tmp_path):
    root = tmp_path / 'harness'
    (root / 'data').mkdir(parents=True)
    (root / 'data' / 'process.json').write_text(json.dumps({
        'pid': __import__('os').getpid(), 'root': str(root), 'created': 0,
        'cmdline': ['foreign'], 'port': 8127}), encoding='utf-8')
    assert status(root)['state'] == 'error'
    try:
        stop(root)
    except HarnessProcessError as exc:
        assert '未停止' in str(exc)
    else:
        raise AssertionError('foreign process record was accepted')
