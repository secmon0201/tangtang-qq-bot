Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'napcat_process.ps1')

$settings = Get-BotLaunchSettings -Root $root
$roots = @(Get-QqRootProcesses)
$targets = @{}
$tracked = Get-VerifiedStateRoot -Roots $roots -StatePath $settings.StatePath -AccountId $settings.AccountId
if ($null -ne $tracked) { $targets[$tracked.Pid] = 'quick-launch record' }
foreach ($qqRoot in $roots | Where-Object { $_.AccountIds -contains $settings.AccountId }) {
    $targets[$qqRoot.Pid] = 'configured account hint'
}
foreach ($qqRoot in Get-QqRootsConnectedToPort -Port $settings.Port) {
    $targets[$qqRoot.Pid] = 'active OneBot connection'
}

if ($targets.Count -eq 0) {
    Write-Output 'No NapCat QQ process could be safely identified; no QQ process was closed.'
    exit 0
}
foreach ($target in $targets.GetEnumerator()) {
    Write-Output "Stopping verified NapCat QQ process tree PID $($target.Key) ($($target.Value))."
    if (-not (Stop-VerifiedProcessTree -ProcessId ([int]$target.Key))) {
        throw "Could not stop verified NapCat QQ process PID $($target.Key)."
    }
}
Start-Sleep -Seconds 1
if (Test-Path -LiteralPath $settings.StatePath) { Remove-Item -LiteralPath $settings.StatePath -Force }
$remainingConnections = @(Get-NetTCPConnection -LocalPort $settings.Port -State Established -ErrorAction SilentlyContinue | Where-Object {
    $_.LocalAddress -in @('127.0.0.1', '::1')
})
if ($remainingConnections.Count -gt 0) {
    throw "A OneBot client is still connected on port $($settings.Port); refusing to start a second QQ transport."
}
