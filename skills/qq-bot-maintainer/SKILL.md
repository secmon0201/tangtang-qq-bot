---
name: qq-bot-maintainer
description: Maintain the 通讯程序集成管理机器人 workspace by adding, editing, or removing QQ bot features through the plugin registry while preserving plugin/application/services boundaries, NTE and Wuthering Waves compatibility, clean upstream GsUID.Core, startup tools, validators, and focused local Git commits. Use automatically for feature changes, game compatibility work, architecture protection, restart verification, or repository maintenance in that workspace.
---

# QQ Bot Maintainer

Maintenance contract for `C:\Users\59586\Documents\通讯程序集成管理机器人`. These files are the canonical instructions for future sessions; `AGENTS.md` is the human-readable summary of the same rules.

## Invariants

- Layer flow: `bot/plugins -> bot/application -> bot/services`; `bot/integrations` wraps external runtimes. Plugins never import plugins; services and application never import plugins.
- Every `bot/plugins/*.py` entry, except `__init__.py`, is registered exactly once in `bot/application/plugin_registry.py`. Do not rebuild a plugin list in `bot/__main__.py`.
- NTE (`#nte`) and Wuthering Waves (`#ww`) are the only enabled game interfaces. Ranking, help, and command takeover stay under `bot/`; never modify `GsUID.Core`, NTEUID, or XutheringWavesUID; read upstream databases and caches read-only.
- Wuthering Waves has only current-group and robot-wide local rankings, with 100 rows per page. Never add an A-Coast Wuthering Waves ranking.
- SQLite is authoritative for unlimited managed groups, per-group aliases, feature intent, solo domains, and private clusters. A new QQ group is registered as a solo domain; the fixed A-Coast five-group set is the first private cluster.
- Group owners and QQ group administrators manage only their current group's feature switches. Private-cluster creation, membership, dissolution, and inspection are super-admin-only; joining a cluster enables every group feature initially without coupling later per-group switches to cluster statistics.
- Token-consuming mention chat and proactive chat require both their SQLite robot-wide runtime gate and the current group's saved switch. Closing a robot-wide gate preserves every group setting so reopening restores only groups that remain enabled.
- Current-group ranking is the default. Cluster speech ranking requires an explicit cluster command, and game robot-wide ranking requires an explicit `总排行`. Token ranking routes are `/ranking/<token>/*`; `/community*`, bare `/ranking/`, and legacy `/s` remain unavailable.
- Active image headers use `AK-BOT FUNCTION` and footers use `AK bot`. Do not expose `A海岸 bot` or `a-coast community` branding on active outputs.
- Cross-group activities and surveys are retired from runtime registration, routes, help, review packs, and docs. Preserve only the documented Git-history reference; do not confuse them with group activity counters used by rankings and games.
- A feature lives in its own plugin, service, resources, tests, and docs. Touching another feature is only legitimate as an intentional shared-contract change that updates the shared module, its tests, and all consumers together.
- Windows-local deployment only. Never commit `.env`, databases, logs, caches, or upstream repositories.
- Locked upstream repositories are pristine vendor checkouts: no tracked/untracked changes, stashes, local-only commits, or diverged history. They track the declared `origin/<branch>` and update only by fast-forward from `config/upstream-lock.json`; never reset, clean, stash, merge, or replay patches there.
- Local takeover reads and transforms upstream data/resources and stops only owned commands at the NoneBot boundary. Unmatched messages continue upstream. `bot/integrations` adapters are process-local and fail loudly on incompatible upstream APIs; they never write upstream source.
- Features remain independent through install, removal, invocation, and execution. External dependency checks on a message/event path must be fail-fast; reconnects, retries, and health probes run as bounded single-flight background work with a retry interval or backoff so a failed integration cannot delay unrelated matchers.
- QQ/SnowLuma login is user-operated. Never ask for or handle credentials.
- Runtime cleanup keeps one current avatar per QQ, screenshot outputs for `REPORT_RETENTION_HOURS`, and GsUID logs for 30 days under a 256 MiB cap. It never touches player data or `data/backups`; backup deletion requires a read-only audit and an explicit retention decision.

## Workflow

1. Identify the change: read [references/feature-lifecycle.md](references/feature-lifecycle.md) for feature add/edit/remove; [references/architecture.md](references/architecture.md) for boundaries, ownership, and shared contracts; [references/nte-upstream.md](references/nte-upstream.md) for game interfaces or upstream work; [references/operations.md](references/operations.md) for validation, restart, and Git.
2. Change code, registry, tests, and user docs in one pass.
3. Run the repository gates from `operations.md`. Do not bypass them.
4. Restart NoneBot only when the live bot must pick up the change. Verify postconditions, never script exit codes alone.
5. Commit the complete project-owned change in one focused commit.

## Common failure modes

- New `bot/plugins/x.py` without a registry entry or `docs/全部#指令清单.md` rows: registry and command-catalog tests fail.
- Writing to `GsData.db` outside the reviewed import tool or editing upstream assets: breaks game isolation and future upstream updates.
- Reimplementing shared avatar/roles/media/report logic inside a plugin: the architecture validator rejects substantial duplicate bodies.
- Restarting SnowLuma/QQ for a Python-only change: unnecessary login risk.

## Keep this skill current

When repository rules change, update these files and commit them with the code change. The local skill link (see `skills/link-local-skill.ps1`) must point at this directory; do not edit a copy outside the repository.
