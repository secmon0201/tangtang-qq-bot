Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$base = Get-QqTransportSettings -Root $root
$settings = [pscustomobject]@{
    Root = $base.Root; Transport = 'snowluma'; AccountId = $base.AccountId
    StatePath = Join-Path $base.Root 'data\qq-transport-snowluma-state.json'
}
$process = Get-TrackedTransportProcess -Settings $settings
if ($null -eq $process) {
    Write-Output 'No verified SnowLuma process is tracked; no unrelated process was stopped.'
    exit 0
}
Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
Wait-Process -Id ([int]$process.ProcessId) -Timeout 15 -ErrorAction SilentlyContinue
if (Get-Process -Id ([int]$process.ProcessId) -ErrorAction SilentlyContinue) {
    throw "SnowLuma PID $($process.ProcessId) did not stop within 15 seconds."
}
Remove-Item -LiteralPath $settings.StatePath -Force -ErrorAction SilentlyContinue
Write-Output "Stopped verified SnowLuma process PID $($process.ProcessId). QQ login state was not modified."
