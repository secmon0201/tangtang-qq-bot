# Operations and Commit Gates

## Before every commit

```powershell
.\.venv\Scripts\python.exe scripts\validate_repository.py
.\.venv\Scripts\python.exe scripts\validate_public_release.py
.\.venv\Scripts\python.exe scripts\validate_docs.py
.\.venv\Scripts\python.exe scripts\validate_architecture.py
.\.venv\Scripts\python.exe scripts\validate_upstream_lock.py
.\.venv\Scripts\python.exe scripts\validate_nte_mode.py
.\.venv\Scripts\python.exe scripts\validate_qq_config.py --env .env
.\.venv\Scripts\python.exe -m pytest
```

## NoneBot-only restart

- Use `scripts\stop.ps1` then `scripts\start.ps1` with process-only execution policy bypass. `启动工具\11-仅重启机器人.bat` is interactive because it ends in `pause`.
- `scripts\start.ps1` archives the previous run under `logs\history`, enables unbuffered output and Python fault handling, and appends lifecycle evidence to `logs\bot.lifecycle.log`.
- Verify: new PID in `logs\bot.pid`; `127.0.0.1:8080` listening; `logs\bot.err.log` empty; startup log shows `Feature plugin loaded` lines, the loaded adapter, and OneBot traffic; Core `8765` and proxy `18765` are unchanged.
- Never restart SnowLuma or QQ unless the user explicitly asks.

## Agent context rollout

- Run `scripts\replay_agent_context.py` before rollout. It must confirm both API styles have the same semantic order, the previous request is a strict prefix of the next request, and the local shadow candidate has all 44 tools without a second provider call. Reports stay under ignored `reports/` and contain only counts and hashes.
- `scripts\benchmark_agent_cache.py` is offline by default. `--live` is the only mode allowed to make paid calls; it uses synthetic context and reports actual cache support and ratios without prompt text, identities, endpoints, or keys. Never turn `unsupported` into zero.
- Roll out in order with `scripts\configure_agent_rollout.py --mode shadow|v2|rollback --apply`: `shadow/shadow/false`, fixed test group, small managed-group scope, then `v2/true/true`. The three values are `TANGTANG_CONTEXT_LAYOUT`, `TANGTANG_NATIVE_ACTION_TOOLS`, and `TANGTANG_CONTEXT_COMPACTION_ENABLED`; do not add a parallel rollout switch. The script changes only these keys, writes atomically, and keeps ignored `.env` backups under `data/backups`.
- Use `scripts\report_agent_usage.py` with explicit baseline/candidate timestamps. It deduplicates terminal request records and emits only aggregates, bounded labels, and hashes.
- Roll back only by setting `v1/false/false` and restarting NoneBot. Never delete context turns, snapshots, compaction jobs, raw chat, or memory evidence. Preserve the legacy `feature_calls` parser through the rollback observation window even after v2 stops injecting the old prompt protocol.
- Each shadow, v2, rollback, and restore restart must verify the new bot PID, listener ownership on 8080, established OneBot traffic, real ordinary chat plus a read-only native tool in the fixed test group, and an empty `logs\bot.err.log`. Use `scripts\notify_test_group.py`; it never accepts a group ID. Do not restart SnowLuma, QQ, or Core.

## Watchdog recovery

- `start_watchdog.ps1` installs a per-workspace, current-user scheduled check at logon and every minute. The action must use the project `pythonw.exe` with `run_watchdog_check.py` and `CREATE_NO_WINDOW`; directly scheduling PowerShell with `-WindowStyle Hidden` can flash a console before argument parsing. The check has a 45-second timeout and UTF-8 failure logging in `logs/watchdog-supervisor-check.log`; the scheduled action is bounded to 50 seconds. `ensure_watchdog.ps1` restores exited or stale (180-second completed heartbeat) watchdogs, allowing 180 seconds for startup. Launch via WMI outside the invoking terminal/task process tree. Keep lifecycle operations serialized with the shared file lock.
- Explicit watchdog/full-stack stop removes `data/watchdog_enabled.flag` before stopping the owned watcher; scheduled checks must stay inert until an explicit start enables the gate. When modifying lifecycle supervision, verify actual scheduled recovery after terminating the watchdog and verify manual stop survives a scheduled check. Never require credentials or elevated task execution.

- Opt-in local model gateway supervision uses `MODEL_GATEWAY_*` in the ignored `.env` and `scripts/model_gateway.py`. Probe only the loopback model list with a 3-second HTTP timeout, never periodic paid generation. Recover after 3 misses with a 120-second cooldown; controller deadlines are 15 seconds for inspection and 40 seconds for recovery. Verify Node executable, script entrypoint, listener ownership and process creation time before stopping anything. Foreign listeners and ambiguous owners must remain untouched.
- Bot, tunnel and model gateway checks isolate exceptions. Model gateway recovery is single-flight and must not restart QQ or Core. The independently installed gateway remains running when the watchdog/full stack is stopped; this avoids stopping a service shared with other clients. For gateway changes also fault-drill its exit and verify automatic recovery plus a real model response without sending QQ messages.

- Every external recovery process must have a finite timeout and return control to the main watchdog loop on success, failure, or timeout. Never use `Start-Process -Wait` for a script that launches long-lived descendants.
- Named Tunnel recovery uses `-SkipCoreRestart`; a fixed public URL does not require restarting Core.
- `data\qq-transport-watchdog-state.json` must update `last_check_started_at` and `last_check_completed_at` every loop. A completed heartbeat older than 180 seconds means the watchdog is unhealthy even if its PID still exists.
- Before claiming a watchdog repair, fault-drill a tunnel recovery and then a NoneBot-only outage. Confirm the same watchdog continues updating its heartbeat and restores a new NoneBot PID, port 8080, and OneBot traffic without restarting SnowLuma/QQ.

## Public and image postconditions

- Token ranking links use `/ranking/<token>/*`; verify `/community*`, bare `/ranking/`, legacy `/s`, `/activity*`, and `/operations*` return 404.
- Active generated images must use `AK-BOT FUNCTION` at the top and `AK bot` at the bottom. Run the image review pack and inspect representative outputs after branding or renderer changes.

## Operator shortcuts

Speech-only shortcuts are `12-启动语音.bat` (explicitly enable the global speech gate and start the runtime) and `13-关闭语音.bat` (disable that gate before stopping the owned launcher and server processes). Full startup respects the saved gate; full shutdown preserves it. The independent speech supervisor checks every 10 seconds, recovers exited runtimes with at least 120 seconds between launch attempts, and requires real synthesis warmup. Inspect `data/tts/readiness.json` with its timestamp and bot PID. Speech failure must not block unrelated startup or commands.

Daily shortcuts are `01-启动全部`, `02-重启全部`, and `03-关闭全部`. Use `11-仅重启机器人` for ordinary Python changes. `31-启动守护程序` and `32-关闭守护程序` are watchdog diagnostics. Shortcuts `21` through `27` are disaster-recovery Quick Tunnel controls only when the fixed Named Tunnel is unavailable; they are not part of normal operation.

The three full-stack shortcuts are thin wrappers over `scripts\start_all.ps1`, `scripts\restart_all.ps1`, and `scripts\stop_all.ps1`. Start and restart must finish with `scripts\verify_full_stack.ps1`: exactly one configured transport, the NoneBot listener, one correctly owned OneBot client, a valid watchdog process and timely healthy heartbeat, the GsUID Core listener, the SnowLuma WebUI when selected, and an empty `logs\bot.err.log`.

## Cleanup

`scripts\clean-generated.ps1` previews; `-Apply` removes generated caches. Never commit caches.

The `runtime_maintenance` plugin cleans avatar superseded versions, HTML screenshot outputs, and `GsUID.Core/data/logs` at startup and every 6 hours. Screenshots follow `REPORT_RETENTION_HOURS`; GsUID logs use 30 days plus a 256 MiB cap. Never include player data or `data/backups` in automatic cleanup. Audit backups with `scripts\audit_database_backups.py` before proposing a retention policy.

## Git

- Commit the complete project-owned QQ bot in one repository; NTE is not a separate repository.
- Keep `main` buildable and use focused commits. An operational remote may exist, but it must remain private and must not be pushed without an explicit request. Public release uses a separate fresh-history sanitized repository.
- Never commit `.env`, `data/`, `logs/`, `GsUID.Core`, SnowLuma, caches, generated outputs, or retired implementations.
- Stage only explicit project-owned paths. Upstream checkouts must remain clean, stash-free, on the locked branch, tracking `origin/<branch>`, and without local-only commits; update them only through `scripts\install_gsuid.ps1` fast-forward flow.
- After deleting anything material, report what was removed and whether it is recoverable.
