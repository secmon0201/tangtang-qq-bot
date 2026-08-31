param(
    [switch]$Background
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$CoreDir = Join-Path $Root "GsUID.Core"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$CoreRunner = Join-Path $Root "scripts\run_gsuid_core.py"
$LogDir = Join-Path $Root "logs"

if (-not (Test-Path -LiteralPath (Join-Path $CoreDir "gsuid_core\core.py"))) {
    throw "GsUID Core is not installed. Run .\scripts\install_gsuid.ps1 first."
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "The project virtual environment was not found: $Python"
}

function Test-CoreUp {
    try {
        $request = Invoke-WebRequest -Uri "http://127.0.0.1:8765/nte/i/__nte_health_check__" `
            -UseBasicParsing -TimeoutSec 3
        return $request.StatusCode -eq 404
    } catch {
        $responseProperty = $_.Exception.PSObject.Properties['Response']
        if ($null -ne $responseProperty -and $null -ne $responseProperty.Value) {
            return ([int]$responseProperty.Value.StatusCode -eq 404)
        }
        return $false
    }
}

$processes = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "python*.exe" -and
    $_.CommandLine -match 'gsuid_core[\\/]core\.py|gsuid_core\.core|run_gsuid_core\.py'
})

if (-not $Background) {
    if ($processes.Count -gt 0) {
        Write-Output "GsUID Core is already running with PID $($processes[0].ProcessId)."
        exit 0
    }
    Set-Location -LiteralPath $CoreDir
    Write-Host "Starting GsUID Core on http://127.0.0.1:8765"
    & $Python -u $CoreRunner
    exit $LASTEXITCODE
}

if ($processes.Count -eq 0) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $stdout = Join-Path $LogDir "gsuid_core.out.log"
    $stderr = Join-Path $LogDir "gsuid_core.err.log"
    $process = Start-Process -FilePath $Python -ArgumentList @("-u", $CoreRunner) `
        -WorkingDirectory $CoreDir -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    Write-Output "GsUID Core start requested with PID $($process.Id)."
} else {
    Write-Output "GsUID Core process already exists with PID $($processes[0].ProcessId); waiting for readiness."
}

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if (Test-CoreUp) {
        Write-Output "GsUID Core is ready on 127.0.0.1:8765."
        exit 0
    }
    Start-Sleep -Seconds 2
}
throw "GsUID Core did not become ready on 127.0.0.1:8765."
