# Feature Lifecycle

## Locate a feature (查)

- Registry key in `bot/application/plugin_registry.py` gives the module path.
- `rg "<key>"` across `bot/`, `tests/`, and `docs/` lists the feature's files.

## Add a feature (增)

1. Create `bot/plugins/<name>.py` with commands/matchers; import only application or services.
2. Add a `PluginSpec` to the registry: unique key, module, Chinese label/category, `transports` (normally `("onebot",)`), `setting` only for an env-gated flag, `after` only for a real load-order need.
3. Put business logic in `bot/services/<name>*.py`; static inputs in `bot/resources/<name>/`.
4. Add `tests/test_<name>.py`. Global registry coverage and ordering are already protected.
5. If the feature exposes `#` commands, add every command and alias to `docs/全部#指令清单.md`; `tests/test_command_catalog.py` enforces coverage.
6. Run the gates from `operations.md`, restart NoneBot only, and confirm `Feature plugin loaded: <label> [<key>]` in startup logs.

## Edit a feature (改)

- Stay inside the feature's plugin, service, resource, test, and doc files.
- Update tests and the command catalog in the same change when behavior changes.
- If a shared service must change, update its tests and all consumers; that is a deliberate contract change, not feature coupling.

## Remove a feature (删)

1. Remove the plugin file, its registry entry, exclusive services/resources, tests, and doc rows.
2. `rg` for remaining imports or references and remove or fix them.
3. Run the gates; the one-to-one registry test fails until files and registry match.
4. Restart NoneBot only if it was running the removed feature.

## Keep changes orthogonal

- Do not touch unrelated plugins, registry entries, or docs inside a feature change.
- Prefer one focused commit per feature change.
