"""Exercise the Windows launch interface without starting QQ or SnowLuma."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("engine", ["powershell", "pwsh"])
def test_snowluma_lifecycle_preserves_config_and_only_stops_owned_node(tmp_path, engine):
    executable = shutil.which(engine)
    if not executable:
        pytest.skip(f"{engine} is not available")
    harness = tmp_path / "Harness"
    runtime = tmp_path / "SnowLuma"
    (harness / "config").mkdir(parents=True)
    (runtime / "config").mkdir(parents=True)
    (runtime / "node.exe").write_bytes(b"simulation")
    (runtime / "index.mjs").write_bytes(b"simulation")
    (harness / "config" / "settings.json").write_text('{"port":8090}', encoding="utf-8")
    configuration = runtime / "config" / "runtime.json"
    configuration.write_text('{"webuiPort":5099,"hookAutoLoad":true}', encoding="utf-8")
    onebot = runtime / "config" / "onebot_synthetic.json"
    onebot.write_text('{"networks":{"wsClients":[{"url":"ws://127.0.0.1:8090/onebot/v11/ws"}]}}', encoding="utf-8")
    original = {path: path.read_bytes() for path in (configuration, onebot)}
    source = Path(__file__).parents[1] / "scripts" / "services-snowluma.ps1"
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        r'''param([string]$HarnessRoot, [string]$Source)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
. $Source
$settings = Get-HarnessSnowLumaSettings -HarnessRoot $HarnessRoot
$script:running = $true
$script:ownedId = 1234
$script:launches = 0
$script:killed = @()
$script:hidden = $false
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    if ($script:running) {
        [pscustomobject]@{ProcessId=$script:ownedId;ExecutablePath=$settings.node;CommandLine='node.exe index.mjs'}
    }
    [pscustomobject]@{ProcessId=5678;ExecutablePath='C:\Other\node.exe';CommandLine='node.exe other-server.mjs'}
}
function Get-NetTCPConnection {
    param($State, $LocalPort, $RemotePort, $ErrorAction)
    if ($script:running) {
        [pscustomobject]@{OwningProcess=$script:ownedId;RemoteAddress='127.0.0.1'}
    }
}
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru,
        $RedirectStandardOutput, $RedirectStandardError, $ErrorAction)
    if ($FilePath -ne $settings.node -or $ArgumentList -ne 'index.mjs' -or $WorkingDirectory -ne $settings.directory) {
        throw 'Wrong installed runtime was launched'
    }
    if (-not $RedirectStandardOutput.StartsWith($HarnessRoot) -or -not $RedirectStandardError.StartsWith($HarnessRoot)) {
        throw 'Logs escaped Harness'
    }
    $script:hidden = $WindowStyle -eq 'Hidden'
    $script:launches += 1
    $script:running = $true
    $script:ownedId = 4321
    $process = [pscustomobject]@{Id=4321;HasExited=$false}
    $process | Add-Member ScriptMethod Refresh {}
    return $process
}
function Stop-Process {
    param($Id, [switch]$Force, $ErrorAction)
    $script:killed += $Id
    $script:running = $false
}
function Wait-Process { param($Id, $Timeout, $ErrorAction) }
function Get-Process { param($Id, $ErrorAction) }
function Start-Sleep { param($Milliseconds) }
$initial = Get-HarnessSnowLumaStatus -HarnessRoot $HarnessRoot
$existing = Start-HarnessSnowLuma -HarnessRoot $HarnessRoot
$existingLaunches = $script:launches
$stopped = Stop-HarnessSnowLuma -HarnessRoot $HarnessRoot
$started = Start-HarnessSnowLuma -HarnessRoot $HarnessRoot
$absolute = Test-HarnessSnowLumaProcess -Settings $settings -Process ([pscustomobject]@{
    ExecutablePath='C:\Global\node.exe';CommandLine=('node.exe "{0}"' -f $settings.entry)
})
$foreign = Test-HarnessSnowLumaProcess -Settings $settings -Process ([pscustomobject]@{
    ExecutablePath='C:\Global\node.exe';CommandLine='node.exe index.mjs'
})
[pscustomobject]@{initial=$initial;existing=$existing;existing_launches=$existingLaunches;
    stopped=$stopped;started=$started;killed=$script:killed;launches=$script:launches;
    hidden=$script:hidden;absolute=$absolute;foreign=$foreign} | ConvertTo-Json -Depth 8
''',
        encoding="utf-8-sig",
    )
    completed = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(probe),
         "-HarnessRoot", str(harness), "-Source", str(source)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["initial"]["state"] == "running"
    assert result["initial"]["pids"] == [1234]
    assert result["initial"]["connected"] is True
    assert result["initial"]["webui_ready"] is True
    assert result["existing_launches"] == 0
    assert result["stopped"]["state"] == "stopped"
    assert result["killed"] == [1234]
    assert result["started"]["pids"] == [4321]
    assert result["launches"] == 1
    assert result["hidden"] is True
    assert result["absolute"] is True
    assert result["foreign"] is False
    assert (harness / "data" / "snowluma-process.json").is_file()
    assert {path: path.read_bytes() for path in original} == original
