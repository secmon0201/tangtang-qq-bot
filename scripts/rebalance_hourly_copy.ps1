$ErrorActionPreference = "Stop"

# Alias balance is controlled in bot\resources\zhijiang_character_aliases.json and
# segment availability is controlled by name_categories in the copy source.
$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $root ".venv\Scripts\python.exe") (Join-Path $root "scripts\generate_hourly_copy.py") --sample 8
if ($LASTEXITCODE -ne 0) {
    throw "hourly copy catalog validation failed"
}
