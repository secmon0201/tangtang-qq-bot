"""Read-only audit that every active command/alias/matcher entry still resolves."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from bot.application.plugin_registry import plugin_specs_for


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "docs" / "全部#指令清单.md"


def _plugin_sources(transport: str = "onebot") -> list[tuple[str, Path]]:
    specs = plugin_specs_for(transport, stats_realtime_enabled=True)
    return [
        (spec.key, ROOT / (spec.module.replace(".", "/") + ".py")) for spec in specs
    ]


def _names(tree: ast.AST) -> tuple[list[str], int, int]:
    commands: list[str] = []
    matchers = 0
    preprocessors = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "on_command" and node.args:
            name = node.args[0]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                commands.append(name.value)
            for keyword in node.keywords:
                if keyword.arg == "aliases" and isinstance(keyword.value, ast.Set):
                    for alias in keyword.value.elts:
                        if isinstance(alias, ast.Constant) and isinstance(alias.value, str):
                            commands.append(alias.value)
        elif node.func.id == "on_message":
            matchers += 1
        elif node.func.id == "event_preprocessor":
            preprocessors += 1
    return commands, matchers, preprocessors


def audit(root: Path = ROOT) -> dict:
    catalog = (root / "docs" / "全部#指令清单.md").read_text(encoding="utf-8")
    owners: dict[str, list[str]] = defaultdict(list)
    files: dict[str, bool] = {}
    matchers: dict[str, int] = {}
    preprocessors: dict[str, int] = {}
    for key, path in _plugin_sources():
        files[key] = path.is_file()
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        commands, matcher_count, preprocessor_count = _names(tree)
        for name in commands:
            owners[name].append(key)
        matchers[key] = matcher_count
        preprocessors[key] = preprocessor_count

    collisions = {
        name: sorted(set(keys)) for name, keys in owners.items() if len(set(keys)) > 1
    }
    missing_catalog = sorted(name for name in owners if f"#{name}" not in catalog)
    missing_files = sorted(key for key, exists in files.items() if not exists)
    return {
        "plugins": len(files),
        "commands": len(owners),
        "collisions": collisions,
        "missing_catalog": missing_catalog,
        "missing_files": missing_files,
        "matchers": {key: count for key, count in matchers.items() if count},
        "preprocessors": {
            key: count for key, count in preprocessors.items() if count
        },
        "ok": not (collisions or missing_catalog or missing_files),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = audit()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"Command surface audit: plugins={report['plugins']} "
            f"commands+aliases={report['commands']} "
            f"matcher_plugins={len(report['matchers'])} "
            f"collisions={len(report['collisions'])} "
            f"missing_catalog={len(report['missing_catalog'])}"
        )
        if not report["ok"]:
            print("Issues:", json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
