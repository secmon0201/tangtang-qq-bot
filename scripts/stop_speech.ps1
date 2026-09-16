[CmdletBinding()]
param()
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$speechRoot = Split-Path -Parent $PSScriptRoot
$speechData = Join-Path $speechRoot 'data\tts'
$speechPidPath = Join-Path $speechData 'service.pid'
$speechConfigPath = Join-Path $speechData 'service.json'
if (-not (Test-Path -LiteralPath $speechPidPath) -or -not (Test-Path -LiteralPath $speechConfigPath)) { exit 0 }
$speechConfig = Get-Content -LiteralPath $speechConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
$speechProcessId = [int](Get-Content -LiteralPath $speechPidPath -Raw)
$speechExisting = Get-CimInstance Win32_Process -Filter "ProcessId=$speechProcessId" -ErrorAction SilentlyContinue
if (-not $speechExisting) { exit 0 }
if (-not $speechExisting.CommandLine -or -not $speechExisting.CommandLine.Contains([string]$speechConfig.config) -or $speechExisting.ExecutablePath -ne [string]$speechConfig.python) {
    throw 'Speech PID belongs to another process; refusing to stop it.'
}
Stop-Process -Id $speechProcessId -ErrorAction Stop
Write-Output 'Independent speech runtime stopped; QQ and NoneBot unchanged.'
