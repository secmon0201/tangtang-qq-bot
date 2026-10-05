"""Test Core operator lifecycle with synthetic process trees and port ownership."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("engine", ["powershell", "pwsh"])
def test_core_operations_owns_tree_waits_for_listener_and_reports_start_failure(tmp_path, engine):
    executable = shutil.which(engine)
    if not executable:
        pytest.skip(f"{engine} is not available")
    harness = tmp_path / "Harness"
    core = tmp_path / "External Core"
    (harness / "config").mkdir(parents=True)
    (core / "gsuid_core").mkdir(parents=True)
    entry = core / "gsuid_core" / "core.py"
    entry.write_text("# pristine synthetic vendor", encoding="utf-8")
    python = tmp_path / "Python Runtime" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"simulation")
    runner = tmp_path / "Project Runner" / "run_gsuid_core.py"
    runner.parent.mkdir()
    runner.write_text("# synthetic project runner", encoding="utf-8")
    (harness / "config" / "settings.json").write_text(json.dumps({
        "extra": {"core": {"url": "ws://127.0.0.1:9005"}, "operations": {
            "core_dir": str(core), "core_python": str(python), "core_runner": str(runner),
        }},
    }), encoding="utf-8")
    original = {path: path.read_bytes() for path in (entry, runner)}
    source = Path(__file__).parents[1] / "scripts" / "services-core.ps1"
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        r'''param([string]$HarnessRoot, [string]$Source)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
. $Source
$settings = Get-HarnessCoreSettings -HarnessRoot $HarnessRoot
$script:rootId = 1234
$script:alive = @(1234,1235,1236)
$script:launches = 0
$script:killed = @()
$script:hidden = $false
$script:ready = $true
$script:foreignListener = $false
$script:failLaunch = $false
$env:GSUID_CORE_DIR = 'synthetic-before'
function Get-CimInstance {
    param($ClassName, $ErrorAction)
    $rootId = $script:rootId
    if ($rootId -in $script:alive) {
        [pscustomobject]@{Name='python.exe';ProcessId=$rootId;ParentProcessId=99;CommandLine=('python.exe -u "{0}"' -f $settings.runner)}
    }
    if (($rootId + 1) -in $script:alive) {
        [pscustomobject]@{Name='python.exe';ProcessId=($rootId + 1);ParentProcessId=$rootId;CommandLine='python.exe -m runtime_child'}
    }
    if (($rootId + 2) -in $script:alive) {
        [pscustomobject]@{Name='worker.exe';ProcessId=($rootId + 2);ParentProcessId=($rootId + 1);CommandLine='worker.exe'}
    }
    [pscustomobject]@{Name='python.exe';ProcessId=5678;ParentProcessId=99;CommandLine='python.exe C:\Other\run_gsuid_core.py'}
}
function Get-NetTCPConnection {
    param($State, $LocalPort, $ErrorAction)
    if ($LocalPort -ne 9005) { throw 'Ignored configured Core port' }
    if ($script:foreignListener) { [pscustomobject]@{OwningProcess=5678} }
    elseif ($script:ready -and ($script:rootId + 1) -in $script:alive) {
        [pscustomobject]@{OwningProcess=($script:rootId + 1)}
    }
}
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru,
        $RedirectStandardOutput, $RedirectStandardError, $ErrorAction)
    if ($FilePath -ne $settings.python -or $ArgumentList -ne ('-u "{0}"' -f $settings.runner) -or $WorkingDirectory -ne $settings.directory) {
        throw 'Wrong installed runtime was launched'
    }
    if ($env:GSUID_CORE_DIR -ne $settings.directory) { throw 'Core runner did not inherit configured directory' }
    if (-not $RedirectStandardOutput.StartsWith($HarnessRoot) -or -not $RedirectStandardError.StartsWith($HarnessRoot)) {
        throw 'Logs escaped Harness'
    }
    $script:hidden = $WindowStyle -eq 'Hidden'
    $script:launches += 1
    $script:rootId = 4321
    if (-not $script:failLaunch) { $script:alive = @(4321,4322,4323) }
    return [pscustomobject]@{Id=4321}
}
function Stop-Process {
    param($Id, [switch]$Force, $ErrorAction)
    $script:killed += $Id
    $script:alive = @($script:alive | Where-Object { $_ -ne $Id })
}
function Wait-Process { param($Id, $Timeout, $ErrorAction) }
function Get-Process { param($Id, $ErrorAction); if ($Id -in $script:alive) { [pscustomobject]@{Id=$Id} } }
function Start-Sleep { param($Milliseconds); $script:ready = $true }
$initial = Get-HarnessCoreStatus -HarnessRoot $HarnessRoot
$existing = Start-HarnessCore -HarnessRoot $HarnessRoot
$existingLaunches = $script:launches
$script:ready = $false
$notReady = Get-HarnessCoreStatus -HarnessRoot $HarnessRoot
$waitExisting = Start-HarnessCore -HarnessRoot $HarnessRoot
$waitLaunches = $script:launches
$stopped = Stop-HarnessCore -HarnessRoot $HarnessRoot
$started = Start-HarnessCore -HarnessRoot $HarnessRoot
$restoredDirectory = $env:GSUID_CORE_DIR
$absolute = Test-HarnessCoreProcess -Settings $settings -Process ([pscustomobject]@{
    Name='python.exe';CommandLine=('python.exe "{0}"' -f $settings.entry.Replace('\', '/'))
})
$foreign = Test-HarnessCoreProcess -Settings $settings -Process ([pscustomobject]@{
    Name='python.exe';CommandLine='python.exe run_gsuid_core.py'
})
$script:alive = @()
$script:foreignListener = $true
$occupied = Get-HarnessCoreStatus -HarnessRoot $HarnessRoot
$occupiedError = ''
try { Start-HarnessCore -HarnessRoot $HarnessRoot | Out-Null } catch { $occupiedError = $_.Exception.Message }
$script:foreignListener = $false
$script:failLaunch = $true
$startupError = ''
try { Start-HarnessCore -HarnessRoot $HarnessRoot | Out-Null } catch { $startupError = $_.Exception.Message }
[pscustomobject]@{initial=$initial;existing=$existing;existing_launches=$existingLaunches;
    not_ready=$notReady;wait_existing=$waitExisting;wait_launches=$waitLaunches;
    stopped=$stopped;started=$started;killed=$script:killed;launches=$script:launches;
    hidden=$script:hidden;absolute=$absolute;foreign=$foreign;restored_directory=$restoredDirectory;
    occupied=$occupied;occupied_error=$occupiedError;startup_error=$startupError} | ConvertTo-Json -Depth 8
''',
        encoding="utf-8-sig",
    )
    completed = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(probe), "-HarnessRoot", str(harness), "-Source", str(source)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["initial"]["state"] == "running"
    assert result["initial"]["pids"] == [1234, 1235, 1236]
    assert result["initial"]["ready"] is True
    assert result["initial"]["url"] == "http://127.0.0.1:9005/"
    assert result["existing_launches"] == result["wait_launches"] == 0
    assert result["not_ready"]["state"] == "running"
    assert result["not_ready"]["ready"] is False
    assert result["wait_existing"]["ready"] is True
    assert result["stopped"]["state"] == "stopped"
    assert result["killed"] == [1236, 1235, 1234]
    assert result["started"]["pids"] == [4321, 4322, 4323]
    assert result["launches"] == 2
    assert result["hidden"] is True
    assert result["absolute"] is True
    assert result["foreign"] is False
    assert result["restored_directory"] == "synthetic-before"
    assert result["occupied"]["state"] == "port_in_use"
    assert "unrelated process" in result["occupied_error"]
    assert "exited during startup" in result["startup_error"]
    assert {path: path.read_bytes() for path in original} == original
