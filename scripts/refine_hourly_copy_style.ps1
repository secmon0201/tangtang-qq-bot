$ErrorActionPreference = "Stop"

# The runtime source is now segmented. Edit the JSON fragments directly and
# use the catalog validator instead of rewriting already-composed messages.
$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $root ".venv\Scripts\python.exe") (Join-Path $root "scripts\generate_hourly_copy.py") --sample 4
if ($LASTEXITCODE -ne 0) {
    throw "hourly copy catalog validation failed"
}
