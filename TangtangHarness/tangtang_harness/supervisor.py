"""Windows service recovery with persistent operator intent and local history."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import json
import msvcrt
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

from .config import DEFAULT_ROOT

SERVICES = ('harness', 'core', 'snowluma', 'speech')
RETRY_SECONDS = (60, 120, 300, 900)


def safe_detail(value) -> str:
    text = str(value or '')
    text = re.sub(r'(?i)(authorization|api[_-]?key|access[_-]?token|password)(\s*[=:]\s*)\S+',
                  r'\1\2[redacted]', text)
    return text[-600:]


class SupervisorStore:
    """Small sidecar database; console reads never create it or change intent."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.path = self.root / 'data' / 'supervisor.db'
        self._initialized = False

    @property
    def exists(self):
        return self.path.is_file()

    def initialize(self):
        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=3)) as conn, conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS services(
                    service TEXT PRIMARY KEY, desired INTEGER, reason TEXT NOT NULL DEFAULT 'not_configured',
                    updated_at REAL, observed_state TEXT, pid INTEGER, last_seen_at REAL,
                    last_recovery_at REAL, next_retry_at REAL NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL NOT NULL,service TEXT NOT NULL,
                    event TEXT NOT NULL,source TEXT NOT NULL,reason TEXT NOT NULL,outcome TEXT NOT NULL,
                    pid INTEGER,detail TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_time ON events(ts);
            ''')
            conn.executemany('INSERT OR IGNORE INTO services(service) VALUES(?)', [(s,) for s in SERVICES])
        self._initialized = True

    @contextmanager
    def connect(self, *, readonly=False):
        if not readonly:
            self.initialize()
        conn = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=3) if readonly else sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            if not readonly:
                conn.commit()
        finally:
            conn.close()

    def meta(self, key, default=None):
        if not self.exists:
            return default
        with self.connect(readonly=True) as conn:
            row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, **values):
        with self.connect() as conn:
            conn.executemany('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                             [(key, json.dumps(value)) for key, value in values.items()])

    def record(self, service, event, source='watchdog', reason='', outcome='', pid=None, detail='', now=None):
        with self.connect() as conn:
            conn.execute('INSERT INTO events(ts,service,event,source,reason,outcome,pid,detail) VALUES(?,?,?,?,?,?,?,?)',
                         (time.time() if now is None else now, service, event, source, reason, outcome, pid, safe_detail(detail)))

    def set_enabled(self, enabled, source='operator'):
        self.set_meta(enabled=bool(enabled))
        self.record('watchdog', 'watchdog_enabled' if enabled else 'watchdog_disabled', source, outcome='succeeded')

    def set_intents(self, values, source='operator', reason='manual_start'):
        if set(values) - set(SERVICES):
            raise ValueError('Unknown supervised service')
        now = time.time()
        with self.connect() as conn:
            for service, desired in values.items():
                old = conn.execute('SELECT desired FROM services WHERE service=?', (service,)).fetchone()[0]
                conn.execute('UPDATE services SET desired=?,reason=?,updated_at=?,next_retry_at=0,failures=0 WHERE service=?',
                             (int(bool(desired)), reason, now, service))
                if old != int(bool(desired)):
                    conn.execute('INSERT INTO events(ts,service,event,source,reason,outcome,detail) VALUES(?,?,?,?,?,?,?)',
                                 (now, service, 'intent_changed', source, reason, 'accepted', 'on' if desired else 'off'))

    def get_services(self):
        if not self.exists:
            return {s: {'service': s, 'desired': None, 'reason': 'not_configured', 'failures': 0, 'next_retry_at': 0} for s in SERVICES}
        with self.connect(readonly=True) as conn:
            return {row['service']: dict(row) for row in conn.execute('SELECT * FROM services')}

    def observe(self, service, state, pid=None, error='', now=None):
        now = time.time() if now is None else now
        prior = self.get_services()[service]
        with self.connect() as conn:
            conn.execute('UPDATE services SET observed_state=?,pid=?,last_seen_at=?,last_error=? WHERE service=?',
                         (state, pid, now, safe_detail(error), service))
            if state == 'running':
                conn.execute('UPDATE services SET failures=0,next_retry_at=0 WHERE service=?', (service,))
        if prior.get('observed_state') != state or prior.get('pid') != pid:
            self.record(service, 'state_changed', outcome=state, pid=pid, detail=error, now=now)

    def recovery(self, service, *, success, pid=None, error='', now):
        row = self.get_services()[service]
        failures = 0 if success else int(row.get('failures', 0)) + 1
        with self.connect() as conn:
            conn.execute('UPDATE services SET failures=?,next_retry_at=?,last_error=? WHERE service=?',
                         (failures, 0 if success else now + RETRY_SECONDS[min(failures - 1, len(RETRY_SECONDS) - 1)], safe_detail(error), service))
            if success:
                conn.execute('UPDATE services SET last_recovery_at=? WHERE service=?', (now, service))
        self.record(service, 'recovery_succeeded' if success else 'recovery_failed',
                    outcome='succeeded' if success else 'failed', pid=pid, detail=error, now=now)

    def view(self, limit=100):
        services = []
        for row in self.get_services().values():
            item = dict(row)
            # ``None`` means the service has never been adopted or explicitly
            # controlled.  Keep that distinction visible to the operator;
            # coercing it to ``False`` would look like a saved manual stop.
            if item.get('desired') is not None:
                item['desired'] = bool(item['desired'])
            services.append(item)
        events, counts = [], {'recoveries_24h': 0, 'failures_24h': 0}
        if self.exists:
            with self.connect(readonly=True) as conn:
                events = [dict(row) for row in conn.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?', (max(1, min(500, limit)),))]
                for row in conn.execute("SELECT event,COUNT(*) n FROM events WHERE ts>=? AND event IN ('recovery_succeeded','recovery_failed') GROUP BY event", (time.time() - 86400,)):
                    counts['recoveries_24h' if row['event'] == 'recovery_succeeded' else 'failures_24h'] = row['n']
        return {'enabled': self.meta('enabled', False), 'last_check_at': self.meta('last_check_at'),
                'last_check_outcome': self.meta('last_check_outcome'), 'services': services,
                'events': events, 'counts': counts}


@contextmanager
def operation_lock(root: Path, *, wait_seconds=0):
    directory = Path(root) / 'runtime' / 'supervisor'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'operation.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        deadline = time.monotonic() + wait_seconds
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Another Harness lifecycle operation is active')
                time.sleep(.1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class WindowsServices:
    """Reuse the existing owned-process implementations and saved speech gates."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def raw(self, action, *, port=None, foreground=False):
        command = ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                   '-File', str(Path(__file__).parents[1] / 'scripts' / 'stack.ps1'), '-Root', str(self.root),
                   '-Action', action, '-Internal']
        if action == 'status':
            command.append('-Json')
        if port is not None:
            command += ['-Port', str(port)]
        if foreground:
            command.append('-Foreground')
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(command, cwd=self.root, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                process.wait(timeout=None if foreground else (38 if action != 'status' else 15))
            except subprocess.TimeoutExpired:
                # Kill only the orchestration shell; detached owned service processes
                # remain identifiable for the next check, rather than being duplicated.
                process.kill(); process.wait(timeout=3)
                raise RuntimeError(f'{action}: lifecycle command timed out') from None
            output.seek(0)
            text = output.read().decode('utf-8-sig', errors='replace')
        if process.returncode:
            raise RuntimeError(safe_detail(text) or f'{action}: exit={process.returncode}')
        return text

    def snapshot(self):
        raw = json.loads(self.raw('status'))
        runtime = raw.get('runtime') or {}
        result = {}
        for service in SERVICES:
            item = raw.get(service) or {}
            state = item.get('state', 'error')
            pids = item.get('pids') or []
            pid = item.get('pid') or (pids[0] if pids else None)
            if state in {'not_installed', 'port_in_use'}:
                state = 'blocked'
            if state in {'not_started', 'exited', 'disabled'}:
                state = 'stopped'
            healthy = state == 'running'
            if service == 'harness':
                healthy = healthy and bool(runtime) and not raw.get('http_error')
                if runtime.get('mode') == 'live':
                    healthy = healthy and bool((runtime.get('transport') or {}).get('connected'))
            elif service == 'core':
                healthy = healthy and bool(item.get('ready'))
                if runtime and self.gates()['core']['enabled']:
                    healthy = healthy and bool((runtime.get('core') or {}).get('connected'))
            elif service == 'snowluma':
                healthy = healthy and bool(item.get('webui_ready'))
                if runtime.get('mode') == 'live':
                    healthy = healthy and bool(item.get('connected'))
            elif service == 'speech':
                healthy = healthy and bool((runtime.get('speech') or {}).get('ready'))
            error = item.get('error') or (raw.get('http_error') if service == 'harness' else '') or (item.get('detail', '') if state == 'blocked' else '')
            if state == 'running' and not healthy and not error:
                error = f'{service}: process alive; connection or readiness is unavailable'
            result[service] = {'state': state, 'pid': pid, 'healthy': healthy, 'error': error}
        return result

    def gates(self):
        def read(path):
            return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        config = read(self.root / 'config' / 'settings.json')
        speech = read(self.root / 'config' / 'speech.json')
        core = (config.get('extra') or {}).get('core') or {}
        voice = (config.get('mode', 'observe') == 'live' and config.get('speech_enabled', False)
                 and speech.get('enabled', False) and not (self.root / 'runtime' / 'speech-runtime' / 'stopped.flag').exists())
        return {'harness': {'enabled': True, 'reason': ''},
                'core': {'enabled': bool(core.get('enabled', False)), 'reason': 'core_gate_off'},
                'snowluma': {'enabled': True, 'reason': ''},
                'speech': {'enabled': bool(voice), 'reason': 'speech_gate_off'}}

    def start(self, service, *, manual=False):
        if service == 'speech':
            from .speech_runtime import SpeechRuntimeManager
            manager = SpeechRuntimeManager(self.root)
            if manual:
                manager.start()
            else:
                manager._start(explicit=False)
        else:
            self.raw('start-' + service)
        return self.snapshot()[service]

    def stop(self, service, *, manual=False):
        if service == 'speech':
            from .speech_runtime import SpeechRuntimeManager
            return SpeechRuntimeManager(self.root).stop(disable=manual)
        self.raw('stop-' + service)
        return self.snapshot()[service]

    def wait_foreground(self, process, *, from_start=False):
        """Follow the confirmed process after startup has released the lifecycle lock."""
        import psutil

        logs = {self.root / 'logs' / 'stdout.log': sys.stdout,
                self.root / 'logs' / 'stderr.log': sys.stderr or sys.stdout}
        positions = {path: 0 if from_start or not path.exists() else path.stat().st_size for path in logs}

        def forward():
            for path, output in logs.items():
                if output is None or not path.exists():
                    continue
                if path.stat().st_size < positions[path]:
                    positions[path] = 0
                with path.open('r', encoding='utf-8', errors='replace') as stream:
                    stream.seek(positions[path])
                    text = stream.read(65536)
                    positions[path] = stream.tell()
                if text:
                    output.write(text)
                    output.flush()

        while True:
            forward()
            try:
                return_code = process.wait(timeout=.2)
            except psutil.TimeoutExpired:
                continue
            forward()
            return return_code


class Supervisor:
    def __init__(self, root, backend=None, clock=time.time):
        self.root = Path(root).resolve()
        self.store = SupervisorStore(self.root)
        self.backend = backend or WindowsServices(self.root)
        self.clock = clock

    def enable(self, adopt=True):
        self.store.initialize()
        with operation_lock(self.root, wait_seconds=40):
            if adopt:
                states, gates, saved = self.backend.snapshot(), self.backend.gates(), self.store.get_services()
                self.store.set_intents({s: states[s]['state'] == 'running' and gates[s]['enabled']
                                        for s in SERVICES if saved[s].get('desired') is None},
                                       reason='adopt_current_state')
            self.store.set_enabled(True)
        return self.store.view()

    def check(self):
        if not self.store.meta('enabled', False):
            return {'outcome': 'disabled', 'recovered': []}
        try:
            with operation_lock(self.root):
                return self._check()
        except RuntimeError as exc:
            if str(exc) == 'Another Harness lifecycle operation is active':
                return {'outcome': 'busy', 'recovered': []}
            self.store.record('watchdog', 'check_failed', outcome='failed', detail=exc, now=self.clock())
            self.store.set_meta(last_check_at=self.clock(), last_check_outcome='failed')
            return {'outcome': 'failed', 'recovered': []}

    def _check(self):
        now = self.clock()
        try:
            states, gates = self.backend.snapshot(), self.backend.gates()
        except Exception as exc:
            self.store.record('watchdog', 'check_failed', outcome='failed', detail=exc, now=now)
            self.store.set_meta(last_check_at=now, last_check_outcome='failed')
            return {'outcome': 'failed', 'recovered': []}
        saved = self.store.get_services()
        recovered = []
        for service in SERVICES:
            state = states[service]
            observed = 'degraded' if state['state'] == 'running' and not state.get('healthy', True) else state['state']
            self.store.observe(service, observed, state.get('pid'), state.get('error', ''), now)
        for service in SERVICES:
            # Re-read intent immediately before launch: an operator stop is
            # saved before waiting for this same lifecycle lock.
            saved = self.store.get_services()
            if not self.store.meta('enabled', False) or not saved[service].get('desired') or not gates[service]['enabled']:
                continue
            if service == 'speech' and (not saved['harness'].get('desired') or states['harness']['state'] != 'running'):
                continue
            if states[service]['state'] != 'stopped' or saved[service].get('next_retry_at', 0) > now:
                continue
            self.store.record(service, 'recovery_started', reason='owned_process_missing', outcome='accepted', now=now)
            try:
                result = self.backend.start(service)
                if result['state'] != 'running':
                    raise RuntimeError(result.get('error') or 'Owned service did not start')
                self.store.observe(service, 'running' if result.get('healthy', True) else 'degraded', result.get('pid'), now=now)
                self.store.recovery(service, success=True, pid=result.get('pid'), now=self.clock())
                recovered.append(service)
            except Exception as exc:
                self.store.recovery(service, success=False, error=exc, now=self.clock())
            # A complete scheduled run is bounded; remaining missing services
            # are considered next minute, and failure backoff prevents starvation.
            break
        self.store.set_meta(last_check_at=self.clock(), last_check_outcome='completed')
        return {'outcome': 'completed', 'recovered': recovered}

    def operate(self, action, source='operator', port=None, foreground=False):
        if action in {'start-all', 'stop-all'}:
            start = action == 'start-all'
            targets = ['core', 'harness', 'snowluma'] if start else ['harness', 'core', 'snowluma', 'speech']
            intents = {s: start for s in SERVICES}
            if start:
                intents['speech'] = self.backend.gates()['speech']['enabled']
        else:
            verb, service = action.split('-', 1)
            if service not in SERVICES or verb not in {'start', 'stop', 'restart'}:
                raise ValueError('Unknown lifecycle action')
            targets = [service]
            start = verb != 'stop'
            intents = {service: start}
            if service == 'harness' and start:
                intents['speech'] = self.backend.gates()['speech']['enabled']
        self.store.set_intents(intents, source, 'manual_stop' if not start else 'manual_start')
        if foreground and action in {'start-harness', 'restart-harness'}:
            from .harness_process import owned_process

            # Launch and confirm ownership under the same lock as manual stop
            # and recovery; only waiting for the app lifetime happens outside it.
            with operation_lock(self.root, wait_seconds=45):
                self.store.record('harness', 'operation_started', source, action, 'accepted')
                try:
                    prior = self.backend.snapshot()['harness']
                    if action == 'restart-harness':
                        self.backend.stop('harness')
                    text = self.backend.raw('start-harness', port=port)
                    result = self.backend.snapshot()['harness']
                    process = owned_process(self.root)
                    if result['state'] != 'running' or process is None or process.pid != result.get('pid'):
                        raise RuntimeError('Foreground Harness ownership or startup confirmation failed')
                    self.store.observe('harness', result['state'], result['pid'], result.get('error', ''))
                    self.store.record('harness', 'operation_succeeded', source, action, 'succeeded', result['pid'])
                except Exception as exc:
                    self.store.record('harness', 'operation_failed', source, action, 'failed', detail=exc)
                    raise
            if text and sys.stdout is not None:
                print(text, end='' if text.endswith('\n') else '\n', flush=True)
            try:
                return_code = self.backend.wait_foreground(process,
                    from_start=action == 'restart-harness' or prior['state'] != 'running')
            except KeyboardInterrupt:
                self.operate('stop-harness', source='foreground')
                return self.store.view()
            self.store.record('harness', 'foreground_exited', source='foreground', outcome='exited',
                              pid=process.pid, detail=f'exit={return_code}')
            return self.store.view()
        failures = []
        with operation_lock(self.root, wait_seconds=45):
            for service in targets:
                self.store.record(service, 'operation_started', source, action, 'accepted')
                try:
                    if action.startswith('restart-'):
                        self.backend.stop(service)
                    if start and service != 'speech' and (port is not None or foreground) and service == 'harness':
                        self.backend.raw('start-harness', port=port, foreground=foreground)
                        result = self.backend.snapshot()[service]
                    else:
                        if start:
                            if service == 'speech':
                                result = self.backend.start(service, manual=True)
                            else:
                                result = self.backend.start(service)
                        else:
                            if action == 'stop-speech':
                                try:
                                    result = self.backend.stop(service, manual=True)
                                except TypeError as exc:
                                    if 'manual' not in str(exc):
                                        raise
                                    result = self.backend.stop(service)
                            else:
                                result = self.backend.stop(service)
                    expected = 'running' if start else 'stopped'
                    if result['state'] not in ({'stopped', 'disabled', 'not_started', 'exited'} if not start else {'running'}):
                        raise RuntimeError(result.get('error') or f'Service did not reach {expected}')
                    self.store.observe(service, result['state'], result.get('pid'), result.get('error', ''))
                    self.store.record(service, 'operation_succeeded', source, action, 'succeeded', result.get('pid'))
                except Exception as exc:
                    failures.append(f'{service}: {safe_detail(exc)}')
                    self.store.record(service, 'operation_failed', source, action, 'failed', detail=exc)
        if failures:
            raise RuntimeError('\n'.join(failures))
        return self.store.view()


def main():
    parser = argparse.ArgumentParser(description='Harness local service watchdog')
    parser.add_argument('command', choices=('enable', 'disable', 'check', 'status', 'operate'))
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--action')
    parser.add_argument('--port', type=int)
    parser.add_argument('--foreground', action='store_true')
    args = parser.parse_args()
    supervisor = Supervisor(args.root)
    try:
        if args.command == 'enable':
            result = supervisor.enable()
        elif args.command == 'disable':
            supervisor.store.set_enabled(False)
            result = supervisor.store.view()
        elif args.command == 'check':
            result = supervisor.check()
        elif args.command == 'operate':
            result = supervisor.operate(args.action, port=args.port, foreground=args.foreground)
        else:
            result = supervisor.store.view()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result.get('outcome') == 'failed')
    except Exception as exc:
        print(json.dumps({'outcome': 'failed', 'error': safe_detail(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
