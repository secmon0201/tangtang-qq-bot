Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$settings = Get-QqTransportSettings -Root $root
switch ($settings.Transport) {
    'snowluma' { & (Join-Path $PSScriptRoot 'stop_snowluma.ps1') }
    'lagrange' { & (Join-Path $PSScriptRoot 'stop_lagrange_onebot.ps1') }
    'napcat' { & (Join-Path $PSScriptRoot 'stop_napcat_transport.ps1') }
}
