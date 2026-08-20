Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $root 'data\quick-lagrange-state.json'
if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Output 'No tracked Lagrange.OneBot process exists.'
    exit 0
}
$state = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
if (-not $state.pid -or -not $state.executable) { throw "Invalid Lagrange state file: $statePath" }
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.pid)" -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item -LiteralPath $statePath -Force
    Write-Output 'Tracked Lagrange.OneBot process is no longer running.'
    exit 0
}
if ($state.PSObject.Properties.Name -contains 'launcher') {
    if (-not $process.ExecutablePath -or [IO.Path]::GetFullPath($process.ExecutablePath) -ne [IO.Path]::GetFullPath([string]$state.launcher)) {
        throw "Tracked PID $($state.pid) does not match the recorded Lagrange launcher; refusing to stop it."
    }
    if ([string]$process.CommandLine -notmatch [regex]::Escape([string]$state.executable)) {
        throw "Tracked launcher PID $($state.pid) no longer references Lagrange.OneBot; refusing to stop it."
    }
} elseif (-not $process.ExecutablePath -or [IO.Path]::GetFullPath($process.ExecutablePath) -ne [IO.Path]::GetFullPath([string]$state.executable)) {
    throw "Tracked PID $($state.pid) does not match the recorded Lagrange executable; refusing to stop it."
}
& "$env:SystemRoot\System32\taskkill.exe" /PID ([int]$state.pid) /T /F | Out-Host
if ($LASTEXITCODE -ne 0) { throw "Could not stop tracked Lagrange launcher PID $($state.pid)." }
Remove-Item -LiteralPath $statePath -Force
Write-Output "Stopped tracked Lagrange.OneBot launcher PID $($state.pid)."
