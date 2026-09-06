Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Write-Host 'Stopping the complete SnowLuma bot stack...'
& (Join-Path $PSScriptRoot 'stop_all.ps1')
Start-Sleep -Seconds 2

Write-Host 'Starting the complete SnowLuma bot stack...'
& (Join-Path $PSScriptRoot 'start_all.ps1')
Write-Output 'Full SnowLuma bot stack restart completed.'
