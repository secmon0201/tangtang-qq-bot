---
name: qq-bot-maintainer
description: Understand and maintain the 通讯程序集成管理机器人 workspace, including plugin registration, conversational local skills, persona memory, background lifecycles, and feature removal. Use for onboarding, feature changes, architecture and dependency checks, game compatibility, or operations in this repository while preserving source boundaries and clean upstreams.
---

# QQ Bot Maintainer

Maintenance contract for the current repository root. Resolve it from the active workspace instead of embedding a developer's absolute path. These files are the canonical instructions for future sessions; `AGENTS.md` is the human-readable summary of the same rules.

## Invariants

- Layer flow: `bot/plugins -> bot/application -> bot/services`; `bot/integrations` wraps external runtimes. Plugins never import plugins; services and application never import plugins.
- Every `bot/plugins/*.py` entry, except `__init__.py`, is registered exactly once in `bot/application/plugin_registry.py`. Do not rebuild a plugin list in `bot/__main__.py`.
- NTE (`#nte`) and Wuthering Waves (`#ww`) are the only enabled game interfaces. Their commands, ranking, help, rendering, and login flows pass through unchanged to GsUID Core; the project must not take over game commands or modify `GsUID.Core`, NTEUID, or XutheringWavesUID.
- SQLite is authoritative for unlimited managed groups, per-group aliases, feature intent, solo domains, and private clusters. A new QQ group is registered as a solo domain; source code does not create, protect, or reattach any named cluster.
- Group owners and QQ group administrators manage only their current group's feature switches. Private-cluster creation, membership, dissolution, and inspection are super-admin-only; joining a cluster enables every group feature initially without coupling later per-group switches to cluster statistics.
- Token-consuming group mention chat and proactive chat require both their SQLite robot-wide runtime gate and the current group's saved switch. Private chat requires the robot-wide mention-chat gate but no managed-group membership or per-group switch. Closing a robot-wide gate preserves every group setting so reopening restores only groups that remain enabled.
- Identity blacklist is the union of global active/passive filters and the current group's filter. Block all interaction and model text/image input, including quoted authors and retained context; preserve daily message counts. Recheck queued work and outbound delivery. A changed blacklist invalidates the active context prefix without deleting historical records.
- Passive vision selects current/quoted images plus exactly the previous current-group message and the caller's previous message in that group, deduplicated; never search backward for another image. Proactive vision selects only the triggering message. Use high detail, retain original bytes below 8192px (4096px at 15 or more total request images), persist historical images with their turns, and never evict them merely to admit new images. Check the full request's image count, bytes and base64-expanded body.
- Current-group ranking is the default for project-owned speech and games. Cluster speech ranking requires `#集群发言排行` or `#<集群名>发言排行`, and the caller must belong to that cluster. Game ranking scope and syntax are owned by the upstream plugins.
- Active image headers use `AK-BOT FUNCTION` and footers use `AK bot`. Do not expose `A海岸 bot` or `a-coast community` branding on active outputs.
- Cross-group activities and surveys are retired from runtime registration, routes, help, review packs, and docs. Preserve only the documented Git-history reference; do not confuse them with group activity counters used by rankings and games.
- A feature lives in its own plugin, service, resources, tests, and docs. Touching another feature is only legitimate as an intentional shared-contract change that updates the shared module, its tests, and all consumers together.
- Windows-local deployment only. Never commit `.env`, databases, logs, caches, or upstream repositories.
- Public source contains no instance identities: real QQ/group IDs, private domains, personal credits, and machine paths stay in ignored `.env` or SQLite. Keep `.env.example` private fields empty, use synthetic fixtures, and run `scripts/validate_public_release.py`. Publish only a fresh-history sanitized snapshot, never the operational history.
- Locked upstream repositories are pristine vendor checkouts: no tracked/untracked changes, stashes, local-only commits, or diverged history. They track the declared `origin/<branch>` and update only by fast-forward from `config/upstream-lock.json`; never reset, clean, stash, merge, or replay patches there.
- The NoneBot game boundary only applies the project group/blacklist gates, then forwards `#nte`/`#ww` messages unchanged. Do not add local ranking/help matchers, natural-language game actions, or connector monkey patches. `bot/integrations` adapters must never write upstream source or runtime configuration.
- Features remain independent through install, removal, invocation, and execution. External dependency checks on a message/event path must be fail-fast; reconnects, retries, and health probes run as bounded single-flight background work with a retry interval or backoff so a failed integration cannot delay unrelated matchers.
- QQ/SnowLuma login is user-operated. Never ask for or handle credentials.
- Speech shortcuts `12`/`13` explicitly enable or disable the speech gate; daily startup preserves a saved off choice, and full shutdown preserves the gate. Stop both owned Python launcher and server processes. Keep speech recovery and warmup independent of topic/growth tasks and unrelated startup.
- Runtime cleanup keeps one current avatar per QQ, screenshot outputs for `REPORT_RETENTION_HOURS`, and GsUID logs for 30 days under a 256 MiB cap. It never touches player data or `data/backups`; backup deletion requires a read-only audit and an explicit retention decision.

## Workflow

1. Identify the change: for onboarding, chat, local skills, memory, or dependency questions, read the repository's [Agent engineering guide](../../docs/开发-Agent工程导航.md). Read [references/feature-lifecycle.md](references/feature-lifecycle.md) for feature add/edit/remove; [references/architecture.md](references/architecture.md) for boundaries, ownership, and shared contracts; [references/nte-upstream.md](references/nte-upstream.md) for game interfaces or upstream work; [references/operations.md](references/operations.md) for validation, restart, and Git.
2. Change code, registry, tests, and user docs in one pass.
3. Run the repository gates from `operations.md`. Do not bypass them.
4. Restart NoneBot only when the live bot must pick up the change. Verify postconditions, never script exit codes alone.
5. Commit the complete project-owned change in one focused commit.

## Chat and lifecycle distinctions

- Plugin loading, the skill inventory, registered callable actions, and this coding-agent skill are separate mechanisms. Check the action contract, registered handler, and execution gates before claiming that the chat model can use a capability.
- Group chat context is always the current group's shared ordered context and never stitches private, personal-session, or other-group context. Private chat uses an isolated per-user session plus only that user's own private and group utterances; it never imports other speakers, group summaries, or group metadata.
- The stable native-tool registry remains available to explicit command adapters and compatibility tests, but ordinary natural-language turns receive no local tool Schema and never execute `feature_calls`, `feature_call`, or native local actions. Game commands are not Agent tools; explicit `#nte`/`#ww` messages go unchanged to the upstream connector after the local group, blacklist, and feature gates. Agent context rollout and rollback follow `references/operations.md`; keep legacy parser compatibility without allowing it to execute from natural chat.
- Denia is the only effective live-chat persona and is locked in the application composition layer, not an independently registered plugin. Tangtang profiles, resources, selections, and history remain for compatibility and audit but cannot become the effective chat persona. Map chat, persona management, model/proactive configuration, group settings, and background owners before removing code. Closing chat gates preserves saved data/settings but does not prove all background work has stopped.
- Automatic continuation defaults to 30 seconds idle after delivery, 300 seconds total, and four attempts per window. Its daily per-group quota is twenty per started hundred members (minimum twenty), with no robot-wide daily cap. Snapshot group-list member counts once at local midnight; connection catchup is background-only and deduplicated persistently per day, including failures. Chat admission only reads saved quotas; missing counts retain the last quota or twenty. Never reset attempt history during refresh or restore the obsolete fixed/global quota options.
- Humanizer cleanup applies to both personas, including speech fallback text. Keep uncertainty and quotations/code intact. Denia's spoken em dashes become commas or are removed at boundaries/adjacent punctuation; preserve literal quotes, code, URLs, ordinary hyphens and numeric ranges. Apply this before generic cleanup to both messages and speech fallback. `TANGTANG_HUMANIZE_ENABLED` also gates the optional recent-expression reminder: only current-persona/current-group confirmed replies in 24 hours, at most eight, excluding blocked users; read once and inject fixed labels only into dynamic context. Never put changing style statistics in the static prefix, retry generation for style, or claim live improvement from unit tests. Use `report_reply_style.py` and a separate post-deployment observation window.
- The current registry loads at startup; shutdown hooks clean up on process exit. Do not claim arbitrary hot unloading, zero remaining model calls, or lossless completion of in-flight replies without implementation and behavior tests for those exact properties.
- Use the engineering guide's source map and known gaps instead of reconstructing architecture from older conversation claims. Update it when action contracts, lifecycle ownership, or removal capabilities change; keep live IDs, queue counts, model configuration and test totals out of the maintained guide.

## Common failure modes

- Background memory/profile/summary work uses persisted rolling-hour request and token admission in `background_work.py`; historical work has an additional shared cap. Preserve raw evidence and versions. Group summaries use one Low request per complete batch with atomic cursor commit. Separate queue time from execution time, retain failed usage reservations, rebuild invalid profile drafts, and never classify local SQL errors as provider authentication failures. See `docs/后台整理成本控制.md` for defaults and the read-only report/configuration command.

- New `bot/plugins/x.py` without a registry entry or `docs/全部#指令清单.md` rows: registry and command-catalog tests fail.
- Writing to `GsData.db` outside the reviewed import tool or editing upstream assets: breaks game isolation and future upstream updates.
- Reimplementing shared avatar/roles/media/report logic inside a plugin: the architecture validator rejects substantial duplicate bodies.
- Restarting SnowLuma/QQ for a Python-only change: unnecessary login risk.

## Keep this skill current

When repository rules change, update these files and commit them with the code change. The local skill link (see `skills/link-local-skill.ps1`) must point at this directory; do not edit a copy outside the repository.
