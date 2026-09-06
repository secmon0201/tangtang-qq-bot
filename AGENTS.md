# Repository Instructions

## Supported Environment

- This repository supports Windows-local deployment only: Python 3.13, NoneBot2, OneBot v11, SnowLuma/QQNT, PowerShell, and SQLite.
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
- `启动工具`: Chinese operator-facing batch shortcuts; implementations stay in `scripts`.
- `skills`: repository-owned Codex maintenance skill and its local link helper.

`GsUID.Core`, its UID plugins, SnowLuma, Lagrange, `.env`, databases, logs, reports, downloads, backups, login state, and caches are independent local runtime data and must never be committed.

## Game Interface Ownership

- NTE and Wuthering Waves are the only enabled game interfaces and use `#nte` and `#ww`.
- Ranking, help rendering, command takeover, and compatibility code are project-owned and stay under `bot`.
- Never modify `GsUID.Core`, NTEUID, or XutheringWavesUID source. Read `GsData.db` and game caches in read-only mode and keep upstream repositories clean.
- Wuthering Waves rankings expose only current-group and robot-wide local views. Never add an A-Coast Wuthering Waves board. Render 100 rows per page.
- Existing Wuthering Waves data may be imported only through the scoped dry-run-first tool and only for the fixed A-Coast five groups.
- Update upstream pins through `config/upstream-lock.json` and validate compatibility after every upstream update.
- Treat every locked upstream checkout as disposable, pristine vendor code: no tracked or untracked source changes, stashes, local-only commits, or diverged history. Each checkout must track its declared `origin/<branch>`; `scripts/install_gsuid.ps1` may repair tracking metadata and fast-forward, but must never reset, clean, stash, merge, or replay patches.
- Project takeover may inspect/read upstream data and resources, transform responses, and stop owned commands at the NoneBot message boundary. Commands not explicitly intercepted continue upstream. Runtime adapters in `bot/integrations` must be process-local, fail loudly when upstream APIs change, and never write upstream source.
- Features must be independent during installation, removal, invocation, and execution. A missing, slow, or failed external dependency must fail fast on the event path and must not delay unrelated matchers; connection, retry, and health work belongs in bounded single-flight background tasks with a retry interval or backoff.

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

- Full start: `启动工具\01-启动全部.bat`
- Full restart: `启动工具\02-重启全部.bat`
- Full stop: `启动工具\03-关闭全部.bat`
- NoneBot-only restart: `启动工具\11-仅重启机器人.bat`
- Full-stack implementations are `scripts\start_all.ps1`, `scripts\restart_all.ps1`, and `scripts\stop_all.ps1`; startup success requires `scripts\verify_full_stack.ps1` to pass.
- Generated-file cleanup preview: `scripts\clean-generated.ps1`
- Generated-file cleanup: `scripts\clean-generated.ps1 -Apply`

Do not restart SnowLuma or QQ for ordinary Python changes. Prefer `scripts\stop.ps1` followed by `scripts\start.ps1`, then verify the PID, port 8080, logs, and OneBot connection.

## Git Rules

- Commit the complete project-owned QQ bot in this repository; NTE is not a separate repository.
- Register every `bot/plugins/*.py` feature exactly once in `bot/application/plugin_registry.py`.
- Keep `skills/qq-bot-maintainer` in sync with every rule change; link it locally once with `skills\link-local-skill.ps1` and never edit a copied skill directory outside the repository.
- Keep `main` buildable and use focused commits.
- Never bypass repository or architecture validators.
- Do not stash or preserve patches inside upstream repositories. Move required compatibility into `bot/integrations` or `bot/services`.
- Never commit credentials, runtime data, generated outputs, historical archives, or retired implementations.
