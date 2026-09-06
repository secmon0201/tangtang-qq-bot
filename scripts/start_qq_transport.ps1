Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$settings = Get-QqTransportSettings -Root $root
switch ($settings.Transport) {
    'snowluma' { & (Join-Path $PSScriptRoot 'start_snowluma.ps1') -EnableOneBot:$true }
    'lagrange' { & (Join-Path $PSScriptRoot 'start_lagrange_onebot.ps1') }
    'napcat' {
        & (Join-Path $PSScriptRoot 'start_napcat_transport.ps1')
    }
}
