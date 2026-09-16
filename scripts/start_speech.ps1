[CmdletBinding()]
param([switch]$Enable)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$speechRoot = Split-Path -Parent $PSScriptRoot
$speechData = Join-Path $speechRoot 'data\tts'
$speechConfigPath = Join-Path $speechData 'service.json'
if (-not (Test-Path -LiteralPath $speechConfigPath)) {
    if ($Enable) { throw 'Speech is not installed. Configure it before enabling.' }
    Write-Output 'Speech is not configured; skipped.'
    return
}
$speechConfig = Get-Content -LiteralPath $speechConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
if (-not $speechConfig.enabled) { Write-Output 'Speech runtime is disabled in service.json; skipped.'; return }
$speechBotPython = Join-Path $speechRoot '.venv\Scripts\python.exe'
$speechSwitch = Join-Path $speechRoot 'scripts\speech_switch.py'
$speechAction = if ($Enable) { 'enable' } else { 'status' }
$speechGateJson = & $speechBotPython $speechSwitch $speechAction
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the speech gate.' }
$speechGate = $speechGateJson | ConvertFrom-Json
if (-not $speechGate.enabled) { Write-Output 'Speech is closed by the operator; skipped.'; return }
$speechLock = [IO.File]::Open((Join-Path $speechData 'start.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $speechGateJson = & $speechBotPython $speechSwitch status
    if ($LASTEXITCODE -ne 0) { throw 'Cannot recheck the speech gate.' }
    if (-not ($speechGateJson | ConvertFrom-Json).enabled) { return }
    $speechPidFile = Join-Path $speechData 'service.pid'
    if (Test-Path -LiteralPath $speechPidFile) {
        $speechProcessId = [int](Get-Content -LiteralPath $speechPidFile -Raw)
        $speechExisting = Get-CimInstance Win32_Process -Filter "ProcessId=$speechProcessId" -ErrorAction SilentlyContinue
        if ($speechExisting -and $speechExisting.CommandLine -and $speechExisting.CommandLine.Contains([string]$speechConfig.config)) {
            Write-Output 'Speech runtime already running.'
            return
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
    Write-Output 'Speech runtime launched in background; the bot verifies API health and synthesis warmup.'
}
finally { $speechLock.Dispose() }
