param([int]$Port = 8090, [string]$Root = '', [switch]$Foreground)
$ErrorActionPreference = 'Stop'
$harnessRoot = if ($Root) { [IO.Path]::GetFullPath($Root) } else { Split-Path -Parent $PSScriptRoot }
$stackArgs = @{ Root = $harnessRoot; Action = 'restart-harness'; Port = $Port; Foreground = $Foreground }
& (Join-Path $PSScriptRoot 'stack.ps1') @stackArgs
exit $LASTEXITCODE
