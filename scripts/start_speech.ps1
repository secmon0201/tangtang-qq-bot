[CmdletBinding()]
param()
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$speechRoot = Split-Path -Parent $PSScriptRoot
$speechData = Join-Path $speechRoot 'data\tts'
$speechConfigPath = Join-Path $speechData 'service.json'
if (-not (Test-Path -LiteralPath $speechConfigPath)) { exit 0 }
$speechConfig = Get-Content -LiteralPath $speechConfigPath -Raw | ConvertFrom-Json
if (-not $speechConfig.enabled) { exit 0 }
$speechLock = [IO.File]::Open((Join-Path $speechData 'start.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $speechPidFile = Join-Path $speechData 'service.pid'
    if (Test-Path -LiteralPath $speechPidFile) {
        $speechProcessId = [int](Get-Content -LiteralPath $speechPidFile -Raw)
        $speechExisting = Get-CimInstance Win32_Process -Filter "ProcessId=$speechProcessId" -ErrorAction SilentlyContinue
        if ($speechExisting -and $speechExisting.CommandLine -and $speechExisting.CommandLine.Contains([string]$speechConfig.config)) {
            Write-Output 'Speech runtime already running.'
            exit 0
        }
    }
    $speechPython = (Resolve-Path -LiteralPath $speechConfig.python).Path
    $speechSource = (Resolve-Path -LiteralPath $speechConfig.source).Path
    $speechBootstrap = Join-Path $speechRoot 'bot\integrations\sovits_bootstrap.py'
    $speechInferConfig = (Resolve-Path -LiteralPath $speechConfig.config).Path
    $env:PYTHONUTF8 = '1'
    $env:OMP_NUM_THREADS = '8'
    $env:MKL_NUM_THREADS = '8'
    $env:NLTK_DATA = Join-Path $speechRoot 'data\tts\env\nltk_data'
    $speechArguments = @('-u', ('"' + $speechBootstrap + '"'), ('"' + $speechSource + '"'), '-c', ('"' + $speechInferConfig + '"'), '-a', '127.0.0.1', '-p', [string]$speechConfig.port)
    $speechProcess = Start-Process -FilePath $speechPython -ArgumentList $speechArguments -WorkingDirectory $speechSource -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $speechData 'service.out.log') -RedirectStandardError (Join-Path $speechData 'service.err.log')
    [IO.File]::WriteAllText($speechPidFile, [string]$speechProcess.Id)
    Write-Output 'Speech runtime launched in background; readiness requires the HTTP health check.'
}
finally { $speechLock.Dispose() }
