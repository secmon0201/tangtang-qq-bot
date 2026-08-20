# NTE and Upstream Isolation

Chain: `NapCat -> OneBot v11 -> NoneBot2 -> GenshinUID connector -> GsUID.Core:8765 -> NTEUID`.

- Only NTE is enabled; GenshinUID and XutheringWavesUID stay disabled. `scripts/validate_nte_mode.py` enforces the mode.
- `bot/plugins/game_api.py` is the local gate. `bot/plugins/nte_game_ui.py` loads after it (registry `after=("game_api",)`) and intercepts rank/help at priority `-2`; other `#nte` commands continue upstream.
- Ranking reads `GsUID.Core\data\GsData.db` read-only through `bot/services/nte_rank_data.py`. Validate the schema; never write or repair upstream tables.
- Help and ranking rendering are project-owned in `bot/services/nte_help_render.py` and `bot/services/nte_rank_render.py`; original character art uses the general refresh path, not one-off asset patches.
- Source group is the latest `#nte刷新面板` group; fixed aliases from `NTE_RANK_GROUP_ALIASES` override live names.
- `scripts/nte_login_proxy.py` listens on `127.0.0.1:18765` and allows only `/nte/*`; cloudflared targets the proxy, never Core.

## Upstream update flow

1. Stop Core: `scripts\stop_gsuid_core.ps1`
2. `scripts\install_gsuid.ps1` (refuses when upstream repositories are dirty)
3. `scripts\validate_upstream_lock.py` then `scripts\validate_nte_mode.py`
4. Start Core: `scripts\start_gsuid_core.ps1`
5. Commit `config/upstream-lock.json` together with required bot-side compatibility changes.

- Never stash or patch inside `GsUID.Core`; move compatibility into `bot/integrations` or `bot/services` and keep upstream repositories clean.
- Verify compatibility with the full gates plus `scripts/smoke_game_api.py`, `tests/test_nte_rank.py`, and `tests/test_nte_prefix_display.py`.
