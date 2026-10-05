"""Validate the local NTEUID and XutheringWavesUID runtime mode.

The NoneBot side is the effective command boundary and recognizes ``nte`` and
``ww`` with or without ``#`` (case-insensitive). GenshinUID stays disabled.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


EXPECTED = {
    "GenshinUID": False,
    "XutheringWavesUID": True,
    "NTEUID": True,
    "RoverSign": True,
    "TodayEcho": True,
    "ScoreEcho": True,
    "RoverReminder": False,
}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"\'')
    return values


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    env = read_env(root / ".env")
    if env.get("GSUID_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        errors.append("GSUID_ENABLED must be true")

    config_dir = root / "GsUID.Core" / "data" / "plugins_configs"
    for plugin, expected_enabled in EXPECTED.items():
        path = config_dir / f"{plugin}.json"
        if not path.exists():
            errors.append(f"missing plugin config: {path}")
            continue
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid {plugin}.json: {exc}")
            continue
        if bool(config.get("enabled")) != expected_enabled:
            errors.append(
                f"{plugin}.enabled={config.get('enabled')!r}; expected {expected_enabled}"
            )
        if plugin in {
            "NTEUID",
            "XutheringWavesUID",
            "RoverSign",
            "TodayEcho",
            "ScoreEcho",
            "RoverReminder",
        }:
            prefixes = {str(value).lower() for value in config.get("force_prefix", [])}
            expected_prefix = "nte" if plugin == "NTEUID" else "ww"
            if expected_prefix not in prefixes:
                errors.append(f"{plugin}.force_prefix must include {expected_prefix}")

    # Login, help extensions, and rendering options belong to the upstream
    # plugin. Only require that its runtime config is valid JSON here.
    waves_config_path = root / "GsUID.Core" / "data" / "XutheringWavesUID" / "config.json"
    try:
        json.loads(waves_config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid XutheringWavesUID config: {exc}")

    reminder_config_path = root / "GsUID.Core" / "data" / "RoverReminder" / "config.json"
    try:
        reminder_config = json.loads(reminder_config_path.read_text(encoding="utf-8-sig"))
        push_enabled = reminder_config.get("EnableStaminaPush", {}).get("data")
        if push_enabled is not False:
            errors.append("RoverReminder.EnableStaminaPush must be false")
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        errors.append(f"invalid RoverReminder config: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    errors = validate(args.root.resolve())
    if errors:
        for error in errors:
            print(f"invalid: {error}", file=sys.stderr)
        return 1
    print(
        "Game mode valid: Core=enabled, GenshinUID=disabled, "
        "NTEUID/XutheringWavesUID and user-facing WW extensions enabled, "
        "RoverReminder mail disabled, "
        "OneBot gate=NTE/WW with or without #"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
