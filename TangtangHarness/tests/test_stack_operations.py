"""Test operator orchestration without production QQ writes or model calls."""
import json
import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize('engine', ['powershell', 'pwsh'])
def test_stack_continues_independent_services_and_uses_current_console_port(tmp_path, engine):
    executable = shutil.which(engine)
    if not executable:
        pytest.skip(f'{engine} is not installed')
    source = Path(__file__).parents[1] / 'scripts' / 'stack.ps1'
    probe = tmp_path / 'stack-probe.ps1'
    probe.write_text(r'''
param([string]$Source, [string]$HarnessRoot)
. $Source
$script:events = @()
$script:failCore = $true
$script:failStop = $false
$script:local = [pscustomobject]@{
    port=8120; speech_gate=$true
    harness=[pscustomobject]@{state='stopped';pid=$null;port=8121;error=''}
    speech=[pscustomobject]@{state='stopped';enabled=$true;pid=$null;error=''}
}
function Get-HarnessLocalState { param($HarnessRoot) return $script:local }
function Invoke-HarnessScript {
    param($HarnessRoot, $Script, [int]$Port = 0)
    $script:events += ('child:{0}:{1}' -f $Script, $Port)
    if ($Script -eq 'stop.ps1') {
        if ($script:failStop) { throw 'synthetic stop failure' }
        $script:local.harness.state = 'stopped'
    } else { $script:local.harness.state = 'running' }
}
function Start-HarnessCore {
    param($HarnessRoot)
    $script:events += 'core-start'
    if ($script:failCore) { throw 'synthetic Core failure' }
}
function Stop-HarnessCore { param($HarnessRoot) $script:events += 'core-stop' }
function Start-HarnessSnowLuma { param($HarnessRoot) $script:events += 'snow-start' }
function Stop-HarnessSnowLuma { param($HarnessRoot) $script:events += 'snow-stop' }
function Show-HarnessStackStatus { param($HarnessRoot) $script:events += 'status' }
function Start-Process { param($FilePath) $script:events += ('open:' + $FilePath) }
$startFailed = $false
try { Invoke-HarnessStackAction -HarnessRoot $HarnessRoot -Action 'start-all' }
catch { $startFailed = $_.Exception.Message -match 'Core failure' }
$startEvents = $script:events
$script:events = @()
$script:failStop = $true
$stopFailed = $false
try { Invoke-HarnessStackAction -HarnessRoot $HarnessRoot -Action 'stop-all' }
catch { $stopFailed = $_.Exception.Message -match 'stop failure' }
$stopEvents = $script:events
$script:events = @()
$script:failStop = $false
Invoke-HarnessStackAction -HarnessRoot $HarnessRoot -Action 'restart-harness'
$restartEvents = $script:events
$script:events = @()
Start-HarnessService -HarnessRoot $HarnessRoot
$repeatEvents = $script:events
Invoke-HarnessStackAction -HarnessRoot $HarnessRoot -Action 'open-console'
$openRunning = $script:events[-1]
$script:local.harness.state = 'stopped'
Invoke-HarnessStackAction -HarnessRoot $HarnessRoot -Action 'open-console'
$openStopped = $script:events[-1]
[pscustomobject]@{
    start_failed=$startFailed;start_events=$startEvents
    stop_failed=$stopFailed;stop_events=$stopEvents
    restart_events=$restartEvents;repeat_events=@($repeatEvents)
    open_running=$openRunning;open_stopped=$openStopped
    unknown=(Format-HarnessBool $null)
} | ConvertTo-Json -Depth 5
''', encoding='utf-8-sig')
    completed = subprocess.run(
        [executable, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(probe),
         '-Source', str(source), '-HarnessRoot', str(tmp_path / 'Harness')],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout[completed.stdout.index('{'):])
    assert result['start_failed'] is True
    assert result['start_events'] == ['core-start', 'child:start.ps1:8120', 'snow-start', 'status']
    assert result['stop_failed'] is True
    assert result['stop_events'] == ['child:stop.ps1:0', 'core-stop', 'snow-stop', 'status']
    assert result['restart_events'] == ['child:stop.ps1:0', 'child:start.ps1:8120']
    assert result['repeat_events'] == []
    assert result['open_running'] == 'open:http://127.0.0.1:8121/'
    assert result['open_stopped'] == 'open:http://127.0.0.1:8120/'
    assert result['unknown'] == '未知'


def test_real_launcher_start_repeat_restart_stop_in_observe_root(tmp_path):
    """Run the shipped entry with a temporary DB, port and observation settings."""
    from tangtang_harness.harness_process import stop

    harness_source = Path(__file__).parents[1]
    root = tmp_path / 'isolated Harness'
    (root / 'config').mkdir(parents=True)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    settings = {'mode': 'observe', 'port': port, 'speech_enabled': False}
    (root / 'config' / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    # Share dependencies, while all state and writes stay in the isolated root.
    setup = tmp_path / 'dependencies.ps1'
    setup.write_text(
        'param([string]$Link, [string]$Target)\n'
        "$ErrorActionPreference = 'Stop'\n"
        'New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n', encoding='utf-8-sig',
    )
    base_args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File']
    junction = root / '.venv'
    subprocess.run(base_args + [str(setup), '-Link', str(junction), '-Target', str(harness_source / '.venv')],
                   check=True, capture_output=True, timeout=15)
    source = harness_source / 'scripts' / 'stack.ps1'

    def run(action, *extra):
        # A detached Windows process can inherit pipe handles. Capture to a file
        # so a successful launcher exit does not wait for the running app's EOF.
        log = tmp_path / 'launcher-output.log'
        with log.open('wb') as stream:
            result = subprocess.run(base_args + [str(source), '-Root', str(root), '-Action', action, *extra],
                                    stdout=stream, stderr=stream, timeout=40)
        output = log.read_text(encoding='utf-8', errors='replace')
        assert result.returncode == 0, output
        return output

    try:
        run('start-harness')
        runtime = httpx.get(f'http://127.0.0.1:{port}/api/status', timeout=5).json()
        assert runtime['mode'] == 'observe'
        assert runtime['transport']['connected'] is False
        run('start-harness')
        assert httpx.get(f'http://127.0.0.1:{port}/api/status', timeout=5).json()['pid'] == runtime['pid']
        run('restart-harness')
        restarted = httpx.get(f'http://127.0.0.1:{port}/api/status', timeout=5).json()
        assert restarted['pid'] != runtime['pid']
        run('stop-harness')
        state = json.loads(run('status', '-Json'))
        assert state['harness']['state'] == 'stopped'
        assert state['speech']['pid'] is None
        assert json.loads((root / 'config' / 'settings.json').read_text(encoding='utf-8')) == settings
    finally:
        stop(root)
        junction.rmdir()
