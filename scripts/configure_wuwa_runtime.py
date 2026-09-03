"""Apply project-owned runtime policy to installed Wuthering Waves extensions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"runtime config must be a JSON object: {path}")
    return payload


def _write_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )


def disable_rover_reminder(core_dir: Path) -> tuple[Path, Path]:
    """Disable plugin loading and its internal mail scheduler switch."""

    data_dir = Path(core_dir) / "data"
    plugin_path = data_dir / "plugins_configs" / "RoverReminder.json"
    plugin = _read_object(plugin_path)
    plugin.setdefault("name", "RoverReminder")
    plugin.setdefault("pm", 6)
    plugin.setdefault("priority", 5)
    plugin["enabled"] = False
    plugin.setdefault("area", "SV")
    plugin.setdefault("black_list", [])
    plugin.setdefault("white_list", [])
    plugin.setdefault("prefix", [])
    prefixes = [str(value) for value in plugin.get("force_prefix", [])]
    if "ww" not in {value.casefold() for value in prefixes}:
        prefixes.append("ww")
    plugin["force_prefix"] = prefixes
    plugin.setdefault("disable_force_prefix", False)
    plugin.setdefault("allow_empty_prefix", False)
    plugin.setdefault("sv", {})
    plugin.setdefault("alias", [])
    _write_object(plugin_path, plugin)

    reminder_path = data_dir / "RoverReminder" / "config.json"
    reminder = _read_object(reminder_path)
    push = reminder.get("EnableStaminaPush")
    if not isinstance(push, dict):
        push = {
            "type": "GsBoolConfig",
            "title": "开启体力推送",
            "desc": "全局体力推送开关",
            "secret": False,
        }
        reminder["EnableStaminaPush"] = push
    push["data"] = False
    _write_object(reminder_path, reminder)
    return plugin_path, reminder_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--core-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "GsUID.Core",
    )
    args = parser.parse_args()
    plugin_path, reminder_path = disable_rover_reminder(args.core_dir.resolve())
    print(f"disabled RoverReminder plugin: {plugin_path}")
    print(f"disabled RoverReminder mail scheduler: {reminder_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
