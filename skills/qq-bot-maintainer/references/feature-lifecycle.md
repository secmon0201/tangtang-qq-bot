# Feature Lifecycle

## Locate a feature (查)

- Registry key in `bot/application/plugin_registry.py` gives the module path.
- `rg "<key>"` across `bot/`, `tests/`, and `docs/` lists the feature's files.

## Disable versus uninstall

- A group/runtime gate preserves code and stored settings; skill release controls gate invocation. Neither mechanism unloads a plugin or proves every background task is stopped.
- Registry selection happens at process startup. Runtime hot unloading is not currently a general feature. For chat/personas, use the [Agent engineering guide](../../../docs/开发-Agent工程导航.md) to identify shared services, task owners and data before proposing removal.
- Preserve runtime databases and instance resources when disabling or removing code. Destructive data cleanup is a separate operation, not an implied uninstall step.

## Add a feature (增)

1. Create `bot/plugins/<name>.py` with commands/matchers; import only application or services.
2. Add a `PluginSpec` to the registry: unique key, module, Chinese label/category, `transports` (normally `("onebot",)`), `setting` only for an env-gated flag, `after` only for a real load-order need.
3. Put business logic in `bot/services/<name>*.py`; static inputs in `bot/resources/<name>/`.
4. Add `tests/test_<name>.py`. Global registry coverage and ordering are already protected.
5. If the feature exposes `#` commands, add every command and alias to `docs/全部#指令清单.md`; `tests/test_command_catalog.py` enforces coverage.
6. Keep `config/skill-registry.json` coverage in sync. Exposing a conversational action additionally needs its parameter contract, existing handler registration, execution gates, tests and chat documentation; a plugin entry alone does not expose tools to the model.
7. Run the gates from `operations.md`, restart NoneBot only, and confirm `Feature plugin loaded: <label> [<key>]` in startup logs.

## Edit a feature (改)

- Stay inside the feature's plugin, service, resource, test, and doc files.
- Update tests and the command catalog in the same change when behavior changes.
- If a shared service must change, update its tests and all consumers; that is a deliberate contract change, not feature coupling.

## Remove a feature (删)

1. Identify shared consumers, startup/shutdown tasks, `after` constraints and persistent data. Define whether the requested outcome is reversible disablement or source removal.
2. For source removal, remove the plugin file, registry and skill inventory entries, exclusive services/resources, obsolete tests and doc rows. Update dependent order declarations and conversational action contracts/handlers; retain shared services and runtime data.
3. `rg` for remaining imports or references and remove or fix them. Verify remaining direct commands and shared consumers; architecture checks alone do not prove runtime independence.
4. Run the gates; the one-to-one registry test fails until files and registry match.
5. Restart NoneBot only if it was running the removed feature.

For the retired cross-group activity and survey features, keep only the current documentation pointer to their historical Git revision. They must not remain registered, routable, listed in help, included in image review packs, or writable through legacy `.env` scope mappings. Group activity counters are a separate live statistics capability and must remain intact.

## Keep changes orthogonal

- Do not touch unrelated plugins, registry entries, or docs inside a feature change.
- Prefer one focused commit per feature change.
