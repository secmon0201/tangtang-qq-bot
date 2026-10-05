"""Windowless scheduled entry; bound a single Harness service check to 45 seconds."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def run_check(root: Path) -> int:
    root = root.resolve()
    python = root / '.venv' / 'Scripts' / 'python.exe'
    command = [str(python), '-X', 'utf8', '-m', 'tangtang_harness.supervisor', 'check', '--root', str(root)]
    error = ''
    with tempfile.TemporaryFile() as output:
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            child.wait(timeout=45)
        except subprocess.TimeoutExpired:
            # Stop orchestration only. An already launched owned service remains
            # identifiable on the next check; never kill it as part of a timeout.
            import psutil
            try:
                shells = [member for member in psutil.Process(child.pid).children(recursive=True)
                          if member.name().casefold() in {'powershell.exe', 'pwsh.exe'}]
            except psutil.NoSuchProcess:
                shells = []
            child.kill()
            child.wait(timeout=3)
            for member in shells:
                try:
                    member.kill()
                except psutil.NoSuchProcess:
                    pass
            error = 'Watchdog check exceeded 45 seconds'
        output.seek(0)
        raw = output.read().decode('utf-8-sig', errors='replace')
    if not error and child.returncode:
        error = raw or f'Watchdog check exited with code {child.returncode}'
    if error:
        sys.path.insert(0, str(root))
        from tangtang_harness.supervisor import SupervisorStore, safe_detail
        detail = safe_detail(error)
        store = SupervisorStore(root)
        store.record('watchdog', 'check_failed', source='scheduled_runner', outcome='failed', detail=detail)
        import time
        store.set_meta(last_check_at=time.time(), last_check_outcome='failed')
        log = root / 'logs' / 'watchdog-supervisor-check.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'error': detail}, ensure_ascii=False) + '\n')
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        return run_check(args.root)
    except Exception as exc:
        # pythonw has no visible stderr; preserve launcher failures locally too.
        root = args.root.resolve()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from tangtang_harness.supervisor import SupervisorStore, safe_detail
        detail = safe_detail(exc)
        log = root / 'logs' / 'watchdog-supervisor-check.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'error': detail}, ensure_ascii=False) + '\n')
        try:
            SupervisorStore(root).record('watchdog', 'check_failed', source='scheduled_runner', outcome='failed', detail=detail)
        except Exception:
            # The file log remains usable when the sidecar itself is unavailable.
            pass
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
