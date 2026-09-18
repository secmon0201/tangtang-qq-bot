import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_watchdog_supervisor_respects_gate_freshness_and_ownership(tmp_path):
    completed = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', str(ROOT / 'tests/fixtures/watchdog_lifecycle_probe.ps1'),
        '-LibraryPath', str(ROOT / 'scripts/watchdog_lifecycle.ps1'), '-ScratchRoot', str(tmp_path),
    ], capture_output=True, text=True, timeout=15, check=True)
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        'starts': 2, 'stops': 2, 'disabled': True, 'ambiguous': True,
    }
