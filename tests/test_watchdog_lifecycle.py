import json
from pathlib import Path
import shutil
import subprocess

from scripts.run_watchdog_check import run_check


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


def test_scheduled_check_has_no_console_and_preserves_unicode_failure(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    probe = scripts / 'ensure_watchdog.ps1'
    probe.write_text('''
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type 'using System; using System.Runtime.InteropServices; public class ConsoleProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
if ([ConsoleProbe]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 99 }
Write-Output '中文错误保留'
exit 7
''', encoding='utf-8-sig')
    assert run_check(tmp_path) == 7
    log = (tmp_path / 'logs/watchdog-supervisor-check.log').read_text(encoding='utf-8')
    assert '中文错误保留' in log
    assert 'exit=7' in log


def test_scheduled_check_bounds_hangs_and_reports_success(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    probe = scripts / 'ensure_watchdog.ps1'
    probe.write_text('Start-Sleep -Seconds 30', encoding='ascii')
    assert run_check(tmp_path, timeout=1) == 124
    log = tmp_path / 'logs/watchdog-supervisor-check.log'
    assert 'check timed out' in log.read_text(encoding='utf-8')
    before = log.read_bytes()
    probe.write_text('exit 0', encoding='ascii')
    assert run_check(tmp_path) == 0
    assert log.read_bytes() == before
