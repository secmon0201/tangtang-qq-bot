param([string]$Root = '')
$ErrorActionPreference = 'Stop'
$harnessRoot = if ($Root) { [IO.Path]::GetFullPath($Root) } else { Split-Path -Parent $PSScriptRoot }
$python = Join-Path $harnessRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Harness 虚拟环境不存在。' }
Push-Location -LiteralPath $harnessRoot
try {
    & $python -X utf8 -c "import json; from pathlib import Path; p=Path('config/settings.json'); d=json.loads(p.read_text(encoding='utf-8')); d['mode']='observe'; d['extra']['isolated_scope_enabled']=False; p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')"
    if ($LASTEXITCODE -ne 0) { throw '无法切换 Harness 配置到观察模式。' }
    & (Join-Path $PSScriptRoot 'stop.ps1') -Root $harnessRoot
    if ($LASTEXITCODE -ne 0) { throw 'Harness 停止失败。' }
} finally { Pop-Location }
