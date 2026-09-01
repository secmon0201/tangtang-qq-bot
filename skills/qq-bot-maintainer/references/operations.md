# Operations and Commit Gates

## Before every commit

```powershell
.\.venv\Scripts\python.exe scripts\validate_repository.py
.\.venv\Scripts\python.exe scripts\validate_docs.py
.\.venv\Scripts\python.exe scripts\validate_architecture.py
.\.venv\Scripts\python.exe scripts\validate_upstream_lock.py
.\.venv\Scripts\python.exe scripts\validate_nte_mode.py
.\.venv\Scripts\python.exe scripts\validate_qq_config.py --env .env
.\.venv\Scripts\python.exe -m pytest
```

## NoneBot-only restart

- Use `scripts\stop.ps1` then `scripts\start.ps1` with process-only execution policy bypass. `启动工具\11-仅重启机器人.bat` is interactive because it ends in `pause`.
- Verify: new PID in `logs\bot.pid`; `127.0.0.1:8080` listening; `logs\bot.err.log` empty; startup log shows `Feature plugin loaded` lines, the loaded adapter, and OneBot traffic; Core `8765` and proxy `18765` are unchanged.
- Never restart NapCat or QQ unless the user explicitly asks.

## Public and image postconditions

- Token ranking links use `/ranking/<token>/*`; verify `/community*`, bare `/ranking/`, legacy `/s`, `/activity*`, and `/operations*` return 404.
- Active generated images must use `AK-BOT FUNCTION` at the top and `AK bot` at the bottom. Run the image review pack and inspect representative outputs after branding or renderer changes.

## Operator shortcuts

`01-启动全部` start all; `02-重启全部` restart all; `03-关闭全部` stop all; `11-仅重启机器人` restart NoneBot only; `21-启动异环登录隧道` start tunnel; `22-关闭异环登录隧道` stop tunnel; `23-设置异环登录地址` set tunnel URL; `31-启动守护程序` start watchdog; `32-关闭守护程序` stop watchdog.

## Cleanup

`scripts\clean-generated.ps1` previews; `-Apply` removes generated caches. Never commit caches.

## Git

- Commit the complete project-owned QQ bot in one repository; NTE is not a separate repository.
- Keep `main` buildable and use focused commits. No remote is configured yet: remain local-only until the user supplies a private GitHub URL.
- Never commit `.env`, `data/`, `logs/`, `GsUID.Core`, NapCat, caches, generated outputs, or retired implementations.
- After deleting anything material, report what was removed and whether it is recoverable.
