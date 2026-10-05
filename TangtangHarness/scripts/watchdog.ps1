param(
    [ValidateSet('install', 'enable', 'disable', 'check', 'status', 'uninstall')]
    [string]$Action = 'status',
    [string]$Root = ''
)

$ErrorActionPreference = 'Stop'
$harnessRoot = if ($Root) { [IO.Path]::GetFullPath($Root) } else { Split-Path -Parent $PSScriptRoot }
$python = Join-Path $harnessRoot '.venv\Scripts\python.exe'
$pythonw = Join-Path $harnessRoot '.venv\Scripts\pythonw.exe'
$runner = Join-Path $PSScriptRoot 'watchdog_runner.py'
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($harnessRoot.ToLowerInvariant()))).Replace('-', '').Substring(0, 12)
} finally { $sha.Dispose() }
$taskName = "TangtangHarness-Watchdog-$hash"

function Invoke-HarnessSupervisor {
    param([string]$Command)
    Push-Location -LiteralPath $harnessRoot
    try {
        $output = & $python -X utf8 -m tangtang_harness.supervisor $Command --root $harnessRoot
        $code = $LASTEXITCODE
        if ($code -ne 0) { throw ($output -join [Environment]::NewLine) }
        return ($output -join [Environment]::NewLine) | ConvertFrom-Json
    } finally { Pop-Location }
}

function Register-HarnessWatchdog {
    foreach ($required in @($python, $pythonw, $runner)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing Harness watchdog entry: $required" }
    }
    $taskAction = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}" --root "{1}"' -f $runner, $harnessRoot) -WorkingDirectory $harnessRoot
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $triggers = @(
        New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
        New-ScheduledTaskTrigger -AtLogOn -User $user
    )
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 50) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $triggers -Principal $principal -Settings $settings -Description 'Recover only missing owned Harness services with saved on intent and enabled feature gates.' -Force | Out-Null
}

switch ($Action) {
    'install' {
        Register-HarnessWatchdog
        [pscustomobject]@{task_name=$taskName;installed=$true} | ConvertTo-Json
    }
    'enable' {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($null -eq $task) { throw 'Install the Harness watchdog task before enabling it.' }
        $result = Invoke-HarnessSupervisor 'enable'
        Enable-ScheduledTask -TaskName $taskName | Out-Null
        $result | ConvertTo-Json -Depth 10
    }
    'disable' { Invoke-HarnessSupervisor 'disable' | ConvertTo-Json -Depth 10 }
    'uninstall' {
        Invoke-HarnessSupervisor 'disable' | Out-Null
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($null -ne $task) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
        [pscustomobject]@{task_name=$taskName;installed=$false} | ConvertTo-Json
    }
    'check' {
        & $python -X utf8 $runner --root $harnessRoot
        if ($LASTEXITCODE -ne 0) { throw 'Harness watchdog check failed; review logs\watchdog-supervisor-check.log.' }
        Invoke-HarnessSupervisor 'status' | ConvertTo-Json -Depth 10
    }
    'status' {
        $result = Invoke-HarnessSupervisor 'status'
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $info = if ($null -ne $task) { Get-ScheduledTaskInfo -TaskName $taskName } else { $null }
        $result | Add-Member -NotePropertyName scheduled_task -NotePropertyValue ([pscustomobject]@{
            name=$taskName; installed=($null -ne $task); state=if($task){[string]$task.State}else{'missing'}
            last_run_at=if($info){$info.LastRunTime.ToString('o')}else{$null}
            last_result=if($info){$info.LastTaskResult}else{$null}
        })
        $result | ConvertTo-Json -Depth 10
    }
}
