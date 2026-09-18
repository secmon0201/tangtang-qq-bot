"""Bounded supervision of an explicitly configured, local Node model gateway."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from dotenv import dotenv_values
import httpx
import psutil


@dataclass(frozen=True)
class GatewayConfig:
    script: Path
    node: Path
    health_url: str
    port: int
    api_key: str


class HealthResponseError(RuntimeError):
    """The gateway responds, but its health response cannot authorize a restart."""


def load_config(values):
    if str(values.get('MODEL_GATEWAY_ENABLED', '')).lower() not in {'true', '1', 'yes'}:
        return None
    url = values.get('MODEL_GATEWAY_HEALTH_URL') or 'http://127.0.0.1:3123/v1/models'
    parsed = urlsplit(url)
    try:
        local = ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        local = False
    if not local or parsed.scheme != 'http' or parsed.username or parsed.password:
        raise ValueError('Health URL must use an explicit loopback HTTP address')
    script = Path(values.get('MODEL_GATEWAY_SCRIPT') or '')
    node = Path(values.get('MODEL_GATEWAY_NODE') or shutil.which('node.exe') or '')
    if not script.is_absolute() or not script.is_file() or not node.is_file():
        raise ValueError('Gateway script and Node executable must exist')
    return GatewayConfig(script.resolve(), node.resolve(), url, parsed.port or 80,
                         values.get('MODEL_GATEWAY_API_KEY') or '')


def owns_process(config, process):
    info = process.info
    args = info.get('cmdline') or []
    if len(args) != 2 or args[1].startswith('-') or not info.get('exe'):
        return False
    entrypoint = Path(args[1])
    if not entrypoint.is_absolute():
        if not info.get('cwd'):
            return False
        entrypoint = Path(info['cwd']) / entrypoint
    return (Path(info['exe']).resolve() == config.node.resolve()
            and entrypoint.resolve() == config.script.resolve())


def probe_health(config):
    headers = {'Authorization': f'Bearer {config.api_key}'} if config.api_key else {}
    try:
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.get(config.health_url, headers=headers)
        if response.status_code != 200:
            raise HealthResponseError('Health endpoint rejected request')
        try:
            body = response.json()
        except ValueError as exc:
            raise HealthResponseError('Health endpoint did not return JSON') from exc
        if not (isinstance(body, dict) and isinstance(body.get('data'), list)
                and bool(body['data'])
                and all(isinstance(item, dict) and item.get('id') for item in body['data'])):
            raise HealthResponseError('Health endpoint did not return a model list')
        return True
    except httpx.HTTPError:
        return False


def inspect_gateway(config):
    owned = []
    for process in psutil.process_iter(['pid', 'exe', 'cmdline', 'cwd', 'create_time']):
        try:
            if owns_process(config, process):
                owned.append(process.info)
        except (psutil.Error, OSError):
            continue
    listeners = {conn.pid for conn in psutil.net_connections(kind='tcp')
                 if conn.status == psutil.CONN_LISTEN and conn.laddr.port == config.port}
    pids = [item['pid'] for item in owned]
    result = dict(healthy=False, pids=pids,
                  identities={str(item['pid']): item['create_time'] for item in owned})
    if listeners - set(pids):
        return dict(result, status='foreign_listener')
    if len(owned) > 1:
        return dict(result, status='ambiguous_ownership')
    if not owned:
        return dict(result, status='missing')
    healthy = bool(listeners) and probe_health(config)
    return dict(result, status='healthy' if healthy else 'unresponsive', healthy=healthy)


def stop_owned_gateway(config, process_id, created_at):
    try:
        process = psutil.Process(process_id)
        process.info = process.as_dict(attrs=['pid', 'exe', 'cmdline', 'cwd'])
        if process.create_time() != created_at or not owns_process(config, process):
            raise RuntimeError('Gateway process ownership changed')
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    except psutil.NoSuchProcess:
        pass


def launch_gateway(config, root):
    log_dir = root / 'logs'
    log_dir.mkdir(exist_ok=True)
    with (log_dir / 'model-gateway.out.log').open('ab') as stdout, \
            (log_dir / 'model-gateway.err.log').open('ab') as stderr:
        subprocess.Popen([str(config.node), str(config.script)], cwd=config.script.parent,
                         stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                         creationflags=subprocess.CREATE_NO_WINDOW)


def recover_gateway(config, root):
    before = inspect_gateway(config)
    if before['status'] not in {'missing', 'unresponsive'}:
        return before
    for process_id in before['pids']:
        stop_owned_gateway(config, process_id, before['identities'][str(process_id)])
    # Check again after terminating a process; never compete with a foreign listener.
    if before['pids']:
        after_stop = inspect_gateway(config)
        if after_stop['status'] != 'missing':
            return after_stop
    launch_gateway(config, root)
    deadline = time.monotonic() + 8
    while True:
        result = inspect_gateway(config)
        if result['healthy'] or result['status'] not in {'missing', 'unresponsive'}:
            return result
        if time.monotonic() >= deadline:
            return result
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recover', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        config = load_config(dotenv_values(root / '.env'))
        if config is None:
            result = dict(status='disabled', healthy=False, pids=[])
        elif args.recover:
            # The OS releases this nonblocking lock even if the supervisor times out.
            import msvcrt
            (root / 'data').mkdir(exist_ok=True)
            with (root / 'data/model-gateway.lock').open('a+b') as lock:
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    result = dict(status='recovery_in_progress', healthy=False, pids=[])
                else:
                    result = recover_gateway(config, root)
        else:
            result = inspect_gateway(config)
    except HealthResponseError:
        result = dict(status='health_response_rejected', healthy=False, pids=[])
    except Exception as exc:
        # Configuration, URLs and exception messages may contain secrets.
        result = dict(status='check_failed', healthy=False, pids=[], error=type(exc).__name__)
    result['checked_at'] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(result))


if __name__ == '__main__':
    main()
