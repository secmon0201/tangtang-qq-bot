# Repository Instructions

## Supported Environment

- This repository supports Windows-local deployment only: Python 3.13, NoneBot2, OneBot v11, NapCat/QQNT, PowerShell, and SQLite.
- Do not add alternative operating-system or virtual-machine deployment assets.
- QQ login, QR codes, CAPTCHA, slider, SMS, and device verification remain manual user operations.

## Source Boundaries

- `bot/plugins`: NoneBot event and command adapters. Plugins must not import other plugins.
- `bot/application`: cross-plugin use-case orchestration. It may depend on services, never on plugins.
- `bot/services`: business logic, rendering, persistence facades, and local data adapters. It must not depend on plugins or application modules.
- `bot/integrations`: project-owned compatibility for external runtimes.
- `bot/resources`: reproducible project-owned static inputs, never runtime caches.
- `scripts`: Windows operations and deterministic validators.
- `tests`: behavior and architecture protection.
- `docs`: current user and maintainer documentation.
- `config`: non-secret version pins and repository configuration.

`GsUID.Core`, its UID plugins, NapCat, Lagrange, `.env`, databases, logs, reports, downloads, backups, login state, and caches are independent local runtime data and must never be committed.

## NTE Ownership

- NTE is the only enabled game interface and uses `#nte`.
- Ranking, help rendering, command takeover, and compatibility code are project-owned and stay under `bot`.
- Never modify `GsUID.Core` or NTEUID source. Read `GsData.db` in read-only mode and keep upstream repositories clean.
- Update upstream pins through `config/upstream-lock.json` and validate compatibility after every upstream update.

## Required Checks

Run before every commit:

```powershell
.\.venv\Scripts\python.exe scripts\validate_repository.py
.\.venv\Scripts\python.exe scripts\validate_docs.py
.\.venv\Scripts\python.exe scripts\validate_architecture.py
.\.venv\Scripts\python.exe scripts\validate_upstream_lock.py
.\.venv\Scripts\python.exe scripts\validate_nte_mode.py
.\.venv\Scripts\python.exe scripts\validate_qq_config.py --env .env
.\.venv\Scripts\python.exe -m pytest
```

The architecture validator must remain free of plugin-to-plugin imports, reverse layer dependencies, cycles, and substantial duplicate function bodies.

## Operations

- Full start: `start_bot_and_napcat.bat`
- Full restart: `restart_bot_and_napcat.bat`
- Full stop: `stop_bot_and_napcat.bat`
- NoneBot-only restart: `restart_nonebot.bat`
- Generated-file cleanup preview: `scripts\clean-generated.ps1`
- Generated-file cleanup: `scripts\clean-generated.ps1 -Apply`

Do not restart NapCat or QQ for ordinary Python changes. Prefer `scripts\stop.ps1` followed by `scripts\start.ps1`, then verify the PID, port 8080, logs, and OneBot connection.

## Git Rules

- Commit the complete project-owned QQ bot in this repository; NTE is not a separate repository.
- Keep `main` buildable and use focused commits.
- Never bypass repository or architecture validators.
- Do not stash or preserve patches inside upstream repositories. Move required compatibility into `bot/integrations` or `bot/services`.
- Never commit credentials, runtime data, generated outputs, historical archives, or retired implementations.
