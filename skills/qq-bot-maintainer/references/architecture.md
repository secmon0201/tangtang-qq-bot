# Architecture and Independence Contract

## Layers

- `bot/plugins`: NoneBot event, command, and schedule entry points. May import application and services. Never imports another plugin.
- `bot/application`: cross-plugin orchestration and the plugin registry. May import services. Never imports plugins.
- `bot/services`: business logic, persistence facades, rendering, local data adapters, and platform facades. Never imports plugins or application.
- `bot/integrations`: project-owned compatibility around external runtimes such as GsUID Core. May translate between bot code and upstream.
- `bot/resources`: reproducible project-owned static inputs, never runtime caches.
- `scripts`, `tests`, `docs`, `config`, `启动工具`: operations, protection, documentation, pins, and operator shortcuts. Implementations stay in `scripts`.

## Registry contract

`bot/application/plugin_registry.py` is the only load list. Each `PluginSpec` has a stable `key`, `module`, Chinese `label_zh` and `category_zh`, `transports`, an optional feature flag `setting`, and optional `after` ordering. `_validate_registry()` rejects duplicate keys/modules and unsatisfied ordering at import time; `tests/test_plugin_registry.py` keeps registry entries and plugin files one-to-one; `bot/__main__.py` only calls `plugin_specs_for(...)`.

Feature flags such as `stats_realtime_enabled` are resolved in the registry without importing plugins.

## What feature independence means here

Required architecture: no plugin-to-plugin imports, one-way layer dependencies, no cycles, no substantial duplicated function bodies, unique registry entries, separable per-feature files, and fail-fast event paths when an external dependency is unavailable. Tests and validators cover parts of this contract, not arbitrary runtime removal. The current architecture validator does not detect every `services -> application` import; see the [engineering guide](../../../docs/开发-Agent工程导航.md) for the current group-summary composition gap and lifecycle ownership.

Intentional sharing: settings, SQLite, permission/role checks, QQ platform calls, avatar/media helpers, and report renderers are shared contracts with tests. Changing them is a deliberate cross-cutting change, not accidental coupling.

Runtime independence means a matcher may inspect already-known dependency state but must not connect, reconnect, retry, sleep, or perform an unbounded health probe before unrelated matchers can run. Put that work in a bounded background task, keep retries single-flight with a retry interval or backoff, and return an unavailable result immediately. This is event-path fault isolation inside NoneBot; it does not claim one operating-system process per feature.

## Group domains

- `bot/services/group_domains.py` owns unlimited managed-group registration, aliases, solo domains, private clusters, feature defaults, and public ranking-token rotation. SQLite is the runtime authority; `.env` group lists are migration seeds or machine-level settings only.
- A newly observed group becomes an independent solo domain. Named clusters are created and maintained only through SQLite-backed super-admin operations; none are built into source code.
- Group owners and QQ group administrators can edit only the current group's alias and switches. Only super administrators can create, inspect, invite to, remove from, or dissolve private clusters.
- Per-group switches control local invocation and push behavior. Cluster membership alone controls cluster statistics, so disabling a feature in one member group never removes that group's records from the cluster aggregate.
- Token-consuming mention chat and proactive chat additionally require their SQLite robot-wide runtime gates. A robot-wide gate is a reversible cost-control condition and must never overwrite the saved per-group intent.

## Locating ownership

- From a command or event, find its plugin in `bot/plugins`, read its registry key, then `rg` that key across `bot/services`, `bot/resources`, `tests`, and `docs`.
- A shared-contract change updates the shared module, its direct tests, and every declared consumer in the same commit, then runs the full suite.

## Guardrails against design drift

- Extend `bot/services/reports.py` or the domain renderer instead of creating a parallel image pipeline.
- Do not inline avatar, QQ API, or permission logic in a plugin; use the existing services.
- Do not put business logic in `scripts/`; scripts are operations and validators.
- Do not hard-code group IDs, operator IDs, or prefixes; use settings and managed configuration.
- Keep stable English code paths; Chinese belongs to `启动工具/`, `docs/`, and registry labels.
