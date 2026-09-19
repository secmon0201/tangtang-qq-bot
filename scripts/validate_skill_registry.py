"""Validate the skill registry against the live plugin command surface."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "config" / "skill-registry.json"
PLUGIN_DIR = ROOT / "bot" / "plugins"
KINDS = {"skill", "admin", "mixed", "infrastructure", "disabled"}
SKILL_ID = re.compile(r"^[a-z0-9_]+$")
COMMAND_RE = re.compile(
    r"on_command\(\s*[\"']([^\"']+)[\"'](?:\s*,\s*aliases=\{([^}]*)\})?"
)
ALIAS_RE = re.compile(r"[\"']([^\"']+)[\"']")

# Rules implemented as a module-level on_message assignment, where the rule
# function is created inline and is not importable for inspection.
PLUGIN_RULES = {
    "commands.py": ("compact_ranking", "named_cluster_ranking"),
    "mini_games.py": ("mini_games",),
    "nte_game_ui.py": ("nte_game_ui",),
    "today_wife.py": ("today_wife_activity", "today_wife"),
    "wuwa_game_ui.py": ("wuwa_game_ui",),
    "a_coast_archive.py": ("archive_search_router",),
}

# Deterministic local-feature handlers registered by the plugins. These are
# the only actions the registry may route without going through the model.
FEATURE_ACTIONS = {
    "asoul.py": ("today_live", "tomorrow_live", "week_live"),
    "commands.py": ("ranking",),
    "zhijiang.py": ("zhijiang_schedule",),
}

def discover_plugins(plugin_dir: Path = PLUGIN_DIR) -> dict[str, dict]:
    """Return plugin module name -> commands and aliases found in source."""

    plugins: dict[str, dict] = {}
    for path in sorted(plugin_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        text = path.read_text(encoding="utf-8")
        commands: list[dict] = []
        for match in COMMAND_RE.finditer(text):
            commands.append(
                {
                    "command": match.group(1),
                    "aliases": ALIAS_RE.findall(match.group(2) or ""),
                }
            )
        plugins[path.name] = {"path": path, "commands": commands}
    return plugins


def discover_rules(plugin_dir: Path = PLUGIN_DIR) -> tuple[set[tuple[str, str]], int]:
    """Return named rule functions and the total on_message matcher count."""

    found: set[tuple[str, str]] = set()
    total = 0
    for path in sorted(plugin_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"on_message\(", text):
            total += 1
            chunk = text[match.start(): match.start() + 300]
            rule = re.search(r"rule=([A-Za-z_][\w\.]*)", chunk)
            if rule and rule.group(1) != "Rule":
                found.add((path.name, rule.group(1)))
    return found, total


def discover_feature_actions(plugin_dir: Path = PLUGIN_DIR) -> dict[str, tuple[str, ...]]:
    """Return plugin file -> local feature action names from the decorators."""

    result: dict[str, tuple[str, ...]] = {}
    pattern = re.compile(r"@register_local_feature\(([^)]*)\)")
    for path in sorted(plugin_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        names = tuple(
            match.group(1)
            for call in pattern.findall(path.read_text(encoding="utf-8"))
            for match in re.finditer(r"[\"']([^\"']+)[\"']", call)
        )
        if names:
            result[path.name] = names
    return result


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


FEATURE_ACTIONS_AS_SETS = {
    name: set(actions) for name, actions in FEATURE_ACTIONS.items()
}


def validate(registry: dict, plugin_dir: Path = PLUGIN_DIR) -> list[str]:
    errors: list[str] = []
    skills = registry.get("skills")
    if registry.get("schema_version") != 1 or not isinstance(skills, list):
        return ["skill registry must have schema_version=1 and a skills list"]

    plugins = discover_plugins(plugin_dir)
    discovered_declared, discovered_total = discover_rules(plugin_dir)
    seen_skill_ids: set[str] = set()
    seen_commands: dict[str, str] = {}
    registered_plugins: set[str] = set()
    registered_rules: set[tuple[str, str]] = set()
    registered_plugin_rules: set[tuple[str, str]] = set()
    seen_local_actions: dict[str, str] = {}
    registered_local_actions: dict[str, set[str]] = {}

    for entry in skills:
        if not isinstance(entry, dict):
            errors.append("skill entry must be an object")
            continue
        skill_id = str(entry.get("skill_id") or "")
        if not SKILL_ID.match(skill_id):
            errors.append(f"invalid skill_id: {skill_id!r}")
        if skill_id in seen_skill_ids:
            errors.append(f"duplicate skill_id: {skill_id}")
        seen_skill_ids.add(skill_id)
        if entry.get("kind") not in KINDS:
            errors.append(f"{skill_id}: invalid kind {entry.get('kind')!r}")
        for key in ("version", "name", "plugin", "owner", "deprecated"):
            if key not in entry:
                errors.append(f"{skill_id}: missing field {key}")

        plugin = Path(str(entry.get("plugin") or "")).name
        if plugin not in plugins:
            errors.append(f"{skill_id}: unknown plugin file {plugin!r}")
            continue
        registered_plugins.add(plugin)
        if plugins[plugin]["path"].name != plugin:
            errors.append(f"{skill_id}: plugin path must reference bot/plugins/{plugin}")

        commands = entry.get("commands")
        if not isinstance(commands, list):
            errors.append(f"{skill_id}: commands must be a list")
            commands = []
        for item in commands:
            if not isinstance(item, dict) or not item.get("command"):
                errors.append(f"{skill_id}: invalid command entry {item!r}")
                continue
            command = str(item["command"])
            if command in seen_commands:
                errors.append(
                    f"command {command!r} registered in both {seen_commands[command]} and {skill_id}"
                )
            seen_commands[command] = skill_id
            aliases = item.get("aliases")
            if not isinstance(aliases, list):
                errors.append(f"{skill_id}.{command}: aliases must be a list")
                continue
            for alias in aliases:
                if alias in seen_commands:
                    errors.append(
                        f"alias {alias!r} conflicts with {seen_commands[alias]!r} in {skill_id}"
                    )
                seen_commands[str(alias)] = skill_id

        for rule in entry.get("rules") or []:
            registered_rules.add((plugin, str(rule)))
        for rule in entry.get("plugin_rules") or []:
            registered_plugin_rules.add((plugin, str(rule)))
        for action in entry.get("local_actions") or []:
            action = str(action)
            if action in seen_local_actions:
                errors.append(
                    f"local action {action!r} registered in both "
                    f"{seen_local_actions[action]} and {skill_id}"
                )
            seen_local_actions[action] = skill_id
            registered_local_actions.setdefault(plugin, set()).add(action)

    missing_plugins = sorted(set(plugins) - registered_plugins)
    if missing_plugins:
        errors.append("plugins missing from skill registry: " + ", ".join(missing_plugins))
    extra_plugins = sorted(registered_plugins - set(plugins))
    if extra_plugins:
        errors.append("skill registry references unknown plugins: " + ", ".join(extra_plugins))

    for plugin, discovered in plugins.items():
        expected_commands = {
            str(item["command"]) for item in discovered["commands"]
        }
        registered_commands = {
            str(item["command"])
            for entry in skills
            if Path(str(entry.get("plugin") or "")).name == plugin
            for item in entry.get("commands", [])
        }
        if expected_commands != registered_commands:
            missing = sorted(expected_commands - registered_commands)
            extra = sorted(registered_commands - expected_commands)
            if missing:
                errors.append(f"{plugin}: commands missing from registry: {missing}")
            if extra:
                errors.append(f"{plugin}: commands not found in source: {extra}")

    expected_plugin_rules = {
        (plugin, rule) for plugin, rules in PLUGIN_RULES.items() for rule in rules
    }
    if registered_plugin_rules != expected_plugin_rules:
        missing = sorted(expected_plugin_rules - registered_plugin_rules)
        extra = sorted(registered_plugin_rules - expected_plugin_rules)
        if missing:
            errors.append(f"plugin rules missing from registry: {missing}")
        if extra:
            errors.append(f"plugin rules not found in source: {extra}")

    if registered_rules != discovered_declared:
        missing = sorted(discovered_declared - registered_rules)
        extra = sorted(registered_rules - discovered_declared)
        if missing:
            errors.append(f"declared rules missing from registry: {missing}")
        if extra:
            errors.append(f"declared rules not found in source: {extra}")

    covered_matchers = len(registered_rules) + sum(
        len(entry.get("plugin_rules") or []) for entry in skills
    )
    if covered_matchers != discovered_total:
        errors.append(
            f"on_message matcher coverage mismatch: source={discovered_total} "
            f"registry={covered_matchers}"
        )

    discovered_actions = discover_feature_actions(plugin_dir)
    expected_actions = {name: set(actions) for name, actions in discovered_actions.items()}
    if expected_actions != FEATURE_ACTIONS_AS_SETS:
        errors.append(
            "local feature action table is out of sync: "
            f"source={expected_actions} declared={FEATURE_ACTIONS_AS_SETS}"
        )
    for plugin_name, actions in expected_actions.items():
        if registered_local_actions.get(plugin_name, set()) != actions:
            errors.append(
                f"{plugin_name}: local actions missing from registry: "
                f"expected={sorted(actions)} "
                f"registered={sorted(registered_local_actions.get(plugin_name, set()))}"
            )
    extra_action_plugins = sorted(set(registered_local_actions) - set(expected_actions))
    if extra_action_plugins:
        errors.append(
            "registry declares local actions for plugins without handlers: "
            + ", ".join(extra_action_plugins)
        )

    return errors


def counts(registry: dict) -> dict[str, int]:
    skills = registry.get("skills", [])
    return {
        "plugins": len(skills),
        "commands": sum(len(entry.get("commands", [])) for entry in skills),
        "aliases": sum(
            len(item.get("aliases", []))
            for entry in skills
            for item in entry.get("commands", [])
        ),
        "rules": sum(len(entry.get("rules", [])) for entry in skills)
        + sum(len(entry.get("plugin_rules", [])) for entry in skills),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--plugin-dir", type=Path, default=PLUGIN_DIR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    registry = load_registry(args.registry)
    errors = validate(registry, args.plugin_dir)
    report = {"counts": counts(registry), "errors": errors}
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    elif errors:
        print("Skill registry validation failed:")
        for error in errors:
            print("-", error)
    else:
        print(
            "Skill registry validation passed: "
            f"{report['counts']['plugins']} skills, "
            f"{report['counts']['commands']} commands, "
            f"{report['counts']['aliases']} aliases, "
            f"{report['counts']['rules']} rule entries"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
