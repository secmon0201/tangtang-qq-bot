Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$candidates = @(
    [pscustomobject]@{
        Name = 'QQ transport watchdog'
        PidPath = Join-Path $root 'logs\qq-transport-watchdog.pid'
        ScriptPath = Join-Path $PSScriptRoot 'watch_qq_transport.ps1'
    },
    [pscustomobject]@{
        Name = 'legacy watchdog'
        PidPath = Join-Path $root 'logs\napcat-watchdog.pid'
        ScriptPath = Join-Path $PSScriptRoot 'watch_napcat.ps1'
    }
)

foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate.PidPath)) { continue }
    $rawPid = (Get-Content -LiteralPath $candidate.PidPath -Raw -ErrorAction SilentlyContinue).Trim()
    if ($rawPid -notmatch '^\d+$') {
        Remove-Item -LiteralPath $candidate.PidPath -Force
        Write-Output "Removed invalid $($candidate.Name) PID file."
        continue
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $rawPid" -ErrorAction SilentlyContinue
    $scriptPattern = [regex]::Escape([IO.Path]::GetFullPath($candidate.ScriptPath))
    if (
        $null -ne $process -and
        $process.Name -match '(?i)^(powershell|pwsh)\.exe$' -and
        [string]$process.CommandLine -match $scriptPattern
    ) {
        Stop-Process -Id ([int]$rawPid) -Force
        Write-Output "Stopped $($candidate.Name) PID $rawPid."
    } else {
        Write-Output "$($candidate.Name) PID file was stale; no unrelated process was stopped."
    }
    Remove-Item -LiteralPath $candidate.PidPath -Force -ErrorAction SilentlyContinue
}

if (-not ($candidates | Where-Object { Test-Path -LiteralPath $_.PidPath })) {
    Write-Output 'No tracked QQ transport watchdog remains.'
}
