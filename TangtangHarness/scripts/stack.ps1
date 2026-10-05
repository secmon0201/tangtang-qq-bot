param(
    [ValidateSet('', 'start-all', 'stop-all', 'start-harness', 'stop-harness', 'restart-harness',
        'start-core', 'stop-core', 'start-snowluma', 'stop-snowluma', 'status', 'open-console', 'open-snowluma')]
    [string]$Action = '',
    [string]$Root = '',
    [switch]$Json,
    [switch]$Internal,
    [int]$Port = 0,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$harnessRoot = if ($Root) { [IO.Path]::GetFullPath($Root) } else { Split-Path -Parent $PSScriptRoot }
. (Join-Path $PSScriptRoot 'services-core.ps1')
. (Join-Path $PSScriptRoot 'services-snowluma.ps1')

function Get-HarnessLocalState {
    param([string]$HarnessRoot)
    $python = Join-Path $HarnessRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Harness 独立环境不存在，请运行 scripts\setup.ps1。' }
    Push-Location -LiteralPath $HarnessRoot
    try {
        $source = Join-Path $PSScriptRoot 'service_state.py'
        $pythonArgs = @('-X', 'utf8', $source, '--root', $HarnessRoot)
        $output = & $python @pythonArgs
        $code = $LASTEXITCODE
        if ($code -ne 0) { throw "读取 Harness 状态失败，退出码 $code。" }
        return $output | ConvertFrom-Json
    }
    finally { Pop-Location }
}

function Invoke-HarnessScript {
    param([string]$HarnessRoot, [string]$Script, [int]$Port = 0)
    $scriptArgs = @{ Root = $HarnessRoot; Internal = $true }
    if ($Port -gt 0) { $scriptArgs.Port = $Port }
    if ($Foreground -and $Script -eq 'start.ps1') { $scriptArgs.Foreground = $true }
    & (Join-Path $PSScriptRoot $Script) @scriptArgs
}

function Start-HarnessService {
    param([string]$HarnessRoot)
    $local = Get-HarnessLocalState -HarnessRoot $HarnessRoot
    if ($local.harness.state -eq 'running') {
        Write-Host "Harness 已运行：PID=$($local.harness.pid)，控制台=http://127.0.0.1:$($local.harness.port)/"
        return
    }
    if ($local.harness.state -eq 'error') { throw $local.harness.error }
    $requestedPort = if ($script:Port -gt 0) { $script:Port } else { $local.port }
    Invoke-HarnessScript -HarnessRoot $HarnessRoot -Script 'start.ps1' -Port $requestedPort
}

function Get-HarnessStackStatus {
    param([string]$HarnessRoot)
    $local = Get-HarnessLocalState -HarnessRoot $HarnessRoot
    $actualPort = $local.port
    if ($local.harness.state -eq 'running') { $actualPort = $local.harness.port }
    $runtime = $null
    $httpError = ''
    if ($local.harness.state -eq 'running') {
        try { $runtime = Invoke-RestMethod -Uri "http://127.0.0.1:$actualPort/api/status" -TimeoutSec 3 }
        catch { $httpError = $_.Exception.Message }
    }
    return [pscustomobject]@{
        harness = $local.harness
        console_url = "http://127.0.0.1:$actualPort/"
        runtime = $runtime
        http_error = $httpError
        core = Get-HarnessCoreStatus -HarnessRoot $HarnessRoot
        snowluma = Get-HarnessSnowLumaStatus -HarnessRoot $HarnessRoot
        speech = $local.speech
        speech_gate = $local.speech_gate
    }
}

function Format-HarnessBool {
    param($Value)
    if ($null -eq $Value) { return '未知' }
    if ($Value) { return '是' }
    return '否'
}

function Show-HarnessStackStatus {
    param([string]$HarnessRoot)
    $state = Get-HarnessStackStatus -HarnessRoot $HarnessRoot
    $onebot = $null
    $coreConnected = $null
    $speechReady = $null
    $mode = '未运行'
    if ($null -ne $state.runtime) {
        $onebot = $state.runtime.transport.connected
        $coreConnected = $state.runtime.core.connected
        $speechReady = $state.runtime.speech.ready
        $mode = $state.runtime.mode
    }
    @(
        [pscustomobject]@{ '服务'='Harness'; '进程状态'=$state.harness.state; 'PID'=$state.harness.pid;
            '连接与就绪'="控制台=$(Format-HarnessBool ($null -ne $state.runtime))；模式=$mode；QQ连接=$(Format-HarnessBool $onebot)" }
        [pscustomobject]@{ '服务'='Core'; '进程状态'=$state.core.state; 'PID'=($state.core.pids -join ',');
            '连接与就绪'="端口就绪=$(Format-HarnessBool $state.core.ready)；Harness连接=$(Format-HarnessBool $coreConnected)" }
        [pscustomobject]@{ '服务'='SnowLuma'; '进程状态'=$state.snowluma.state; 'PID'=($state.snowluma.pids -join ',');
            '连接与就绪'="管理页=$(Format-HarnessBool $state.snowluma.webui_ready)；Harness连接=$(Format-HarnessBool $state.snowluma.connected)" }
        [pscustomobject]@{ '服务'='独立语音'; '进程状态'=$state.speech.state; 'PID'=$state.speech.pid;
            '连接与就绪'="聊天语音开关=$(Format-HarnessBool $state.speech_gate)；运行开关=$(Format-HarnessBool $state.speech.enabled)；API就绪=$(Format-HarnessBool $speechReady)" }
    ) | Format-Table -AutoSize -Wrap | Out-Host
    Write-Host "Harness 控制台：$($state.console_url)"
    Write-Host "SnowLuma 管理页：$($state.snowluma.webui_url)"
    foreach ($message in @($state.harness.error, $state.http_error, $state.speech.error)) {
        if ($message) { Write-Host $message -ForegroundColor Yellow }
    }
    if ($null -ne $state.runtime) {
        foreach ($message in @($state.runtime.core.error, $state.runtime.speech.error)) {
            if ($message) { Write-Host $message -ForegroundColor Yellow }
        }
    }
}

function Invoke-HarnessStackAction {
    param([string]$HarnessRoot, [string]$Action)
    switch ($Action) {
        'start-harness' { Start-HarnessService -HarnessRoot $HarnessRoot }
        'stop-harness' { Invoke-HarnessScript -HarnessRoot $HarnessRoot -Script 'stop.ps1' }
        'restart-harness' {
            Invoke-HarnessScript -HarnessRoot $HarnessRoot -Script 'stop.ps1'
            Start-HarnessService -HarnessRoot $HarnessRoot
        }
        'start-core' { Start-HarnessCore -HarnessRoot $HarnessRoot | Out-Null; Write-Host 'Core 已就绪。' }
        'stop-core' { Stop-HarnessCore -HarnessRoot $HarnessRoot | Out-Null; Write-Host 'Core 已停止。' }
        'start-snowluma' { Start-HarnessSnowLuma -HarnessRoot $HarnessRoot | Out-Null; Write-Host 'SnowLuma 已启动；QQ 登录状态请查看管理页。' }
        'stop-snowluma' { Stop-HarnessSnowLuma -HarnessRoot $HarnessRoot | Out-Null; Write-Host 'SnowLuma 已停止。' }
        'status' { Show-HarnessStackStatus -HarnessRoot $HarnessRoot }
        'open-console' {
            $local = Get-HarnessLocalState -HarnessRoot $HarnessRoot
            $port = $local.port
            if ($local.harness.state -eq 'running') { $port = $local.harness.port }
            Start-Process -FilePath "http://127.0.0.1:$port/"
        }
        'open-snowluma' {
            $settings = Get-HarnessSnowLumaSettings -HarnessRoot $HarnessRoot
            Start-Process -FilePath $settings.webui_url
        }
        { $_ -in @('start-all', 'stop-all') } {
            $steps = if ($Action -eq 'start-all') { @('start-core', 'start-harness', 'start-snowluma') }
                     else { @('stop-harness', 'stop-core', 'stop-snowluma') }
            $failures = @()
            foreach ($step in $steps) {
                try { Invoke-HarnessStackAction -HarnessRoot $HarnessRoot -Action $step }
                catch { $failures += "${step}：$($_.Exception.Message)"; Write-Host $failures[-1] -ForegroundColor Red }
            }
            Show-HarnessStackStatus -HarnessRoot $HarnessRoot
            if ($failures.Count -gt 0) { throw ($failures -join [Environment]::NewLine) }
            if ($Action -eq 'start-all') { Write-Host '全部启动完成。' }
            else { Write-Host '全部关闭完成。' }
        }
    }
}

if ($Action) {
    try {
        if (-not $Internal -and $Action -match '^(start|stop|restart)-') {
            $python = Join-Path $harnessRoot '.venv\Scripts\python.exe'
            $arguments = @('-X', 'utf8', '-m', 'tangtang_harness.supervisor', 'operate', '--root', $harnessRoot, '--action', $Action)
            if ($Port -gt 0) { $arguments += @('--port', [string]$Port) }
            if ($Foreground) { $arguments += '--foreground' }
            Push-Location -LiteralPath $harnessRoot
            try { & $python @arguments; $operationCode = $LASTEXITCODE }
            finally { Pop-Location }
            if ($operationCode -ne 0) { throw "服务操作失败，退出码 $operationCode。" }
            exit 0
        }
        if ($Json -and $Action -eq 'status') { Get-HarnessStackStatus -HarnessRoot $harnessRoot | ConvertTo-Json -Depth 10 }
        else { Invoke-HarnessStackAction -HarnessRoot $harnessRoot -Action $Action }
        exit 0
    }
    catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
}
