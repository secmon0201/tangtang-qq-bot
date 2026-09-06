# Game Interfaces and Upstream Isolation

Chain: `SnowLuma -> OneBot v11 -> NoneBot2 -> GenshinUID connector -> GsUID.Core:8765 -> NTEUID/XutheringWavesUID`.

- NTEUID and XutheringWavesUID are enabled; GenshinUID stays disabled. `scripts/validate_nte_mode.py` enforces the mode and both force prefixes.
- `bot/plugins/game_api.py` is the local gate. `bot/plugins/nte_game_ui.py` loads after it (registry `after=("game_api",)`) and intercepts rank/help at priority `-2`; other `#nte` commands continue upstream.
- Takeover is an ingress boundary: match and stop only project-owned commands in NoneBot, then read/transform upstream data or resources. Never replace functions inside Core or a UID plugin; unmatched messages continue through the official connector.
- `bot/integrations/genshinuid_connector_compat.py` keeps Core connect, reconnect, and ping work out of incoming-message handlers and application startup. When Core is offline, forwarding returns immediately while the connector's scheduled, single-flight reconnect continues in the background; incompatible connector APIs stop startup loudly.
- Ranking reads `GsUID.Core\data\GsData.db` read-only through `bot/services/nte_rank_data.py`. Validate the schema; never write or repair upstream tables.
- `#nte薄荷排行` and `#nte最强排行` are current-group views. Only the explicit `#nte薄荷总排行` and `#nte最强总排行` commands read the robot-wide view; closing NTE in one group blocks local invocation without deleting or filtering upstream records.
- Help and ranking rendering are project-owned in `bot/services/nte_help_render.py` and `bot/services/nte_rank_render.py`; original character art uses the general refresh path, not one-off asset patches.
- Source group is the latest `#nte刷新面板` group; fixed aliases from `NTE_RANK_GROUP_ALIASES` override live names.
- `scripts/nte_login_proxy.py` listens on `127.0.0.1:18765` and allows only `/nte/*`; cloudflared targets the proxy, never Core.

## Wuthering Waves takeover

- `bot/plugins/wuwa_game_ui.py` intercepts `#ww` help and ranking at priority `-2`; other commands continue upstream.
- `#ww帮助` is the compact common-user catalog, `#ww完整帮助` contains all upstream, extension, group-admin, and Bot-owner entries, and `#ww原版帮助` is the upstream snapshot. Regenerate checked-in catalogs with `scripts/sync_wuwa_help.py` after an upstream update.
- RoverSign, TodayEcho, ScoreEcho, and RoverReminder are installed as clean upstream repositories with the `ww` force prefix and exposed through `HelpExtraModules=["all"]`. RoverSign, TodayEcho, and ScoreEcho are enabled. RoverReminder stays disabled at both plugin-load and internal mail-switch levels; project command policy blocks all of its settings and replies that mail reminders are unavailable.
- ScoreEcho keeps its upstream API failure response, which already includes HTTP status and server detail for an expired token; do not replace upstream errors unless a future pinned version removes that feedback.
- Ranking interception is whitelist-based: project-rendered score, phantom, practice, and strongest formats only. Damage and activity rankings continue upstream.
- Rankings read `wavesbind`, `players/<uid>/charListData.json`, and compressed or plain `rawData.json` read-only. They expose current-group and robot-wide local views only, never an A-Coast view.
- QQ avatar, display name, group name, and group alias come from project `bot.db`; pages contain up to 100 ranking rows.
- `scripts/import_wuwa_data.py` is dry-run by default. `--apply` is permitted only after its A-Coast five-group boundary check and backups succeed. Never print cookies, tokens, or login payloads.
- `#ww登录` uses `WavesLoginUrl=https://tangtang.secmon.cn` with `WavesLoginUrlSelf=true`. The shared gateway forwards only the temporary `/waves/i/*` page and its approved login submission endpoints; panel editing, gacha pages, fonts, external-login service endpoints, `/ws/*`, and `/api/*` stay private.

## Upstream update flow

1. Stop Core: `scripts\stop_gsuid_core.ps1`
2. `scripts\install_gsuid.ps1` (reads the lock, repairs tracking metadata, rejects local state/history, and fast-forwards only)
3. `scripts\validate_upstream_lock.py` then `scripts\validate_nte_mode.py`
4. Start Core: `scripts\start_gsuid_core.ps1`
5. Commit `config/upstream-lock.json` together with required bot-side compatibility changes.

- Every locked checkout must have the declared origin URL and branch, track `origin/<branch>`, contain no tracked/untracked changes or stash, and have no commits outside the remote branch history. A remote-ahead checkout is valid and updateable.
- Never reset, clean, stash, merge, replay, or patch inside `GsUID.Core` or a UID plugin. Move compatibility into `bot/integrations` or `bot/services`; runtime adapters may change only in-process objects and must fail loudly when upstream APIs drift.
- Verify compatibility with the full gates plus `scripts/smoke_game_api.py`, `tests/test_nte_rank.py`, and `tests/test_nte_prefix_display.py`.
