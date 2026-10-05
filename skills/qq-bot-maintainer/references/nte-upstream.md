# Game Interfaces and Upstream Isolation

The production chain is `SnowLuma -> OneBot v11 -> NoneBot2 -> GenshinUID connector -> GsUID.Core:8765 -> NTEUID/XutheringWavesUID`.

- NTEUID and XutheringWavesUID are enabled; GenshinUID stays disabled. `scripts/validate_nte_mode.py` enforces the mode and both force prefixes.
- `bot/plugins/game_api.py` is the only project-owned game boundary. It applies the managed-group, blacklist, and feature-switch gates, then lets `#nte`/`#ww` messages pass unchanged to the official connector.
- The project does not load local game ranking/help matchers, does not expose game actions to the chat Agent, and does not patch the connector or NTEUID display helpers. Upstream command parsing, help, ranking, rendering, errors, and login flows remain upstream-owned.
- `GsUID.Core`, NTEUID, XutheringWavesUID, their databases, player caches, and login state are disposable upstream runtime data. Never edit their source or configuration from project scripts. The old URL rewrite helpers intentionally fail with an explanatory message.
- `scripts/nte_login_proxy.py` remains transport infrastructure for the login tunnel; it does not modify Core configuration. The named Tangtang tunnel forwards the upstream login paths without restarting Core for a project-side rewrite.

## Upstream update flow

1. Stop Core: `scripts\stop_gsuid_core.ps1`
2. Run `scripts\install_gsuid.ps1` (fast-forward only, using `config/upstream-lock.json`)
3. Run `scripts\validate_upstream_lock.py` and `scripts\validate_nte_mode.py`
4. Start Core: `scripts\start_gsuid_core.ps1`

Every locked checkout must have the declared origin URL and branch, track `origin/<branch>`, and contain no tracked or untracked changes, stash, local-only commits, or history divergence. Move any future project compatibility into `bot/integrations` or `bot/services`; never patch upstream source or runtime configuration.

Verify the full stack with the repository gates and use `scripts/smoke_game_api.py` only as an isolated end-to-end check of upstream passthrough plus the project gates. Do not use the retired local ranking/help tests as evidence that upstream behavior is supported.
