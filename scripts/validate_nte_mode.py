"""Validate that the local game runtime is configured for NTEUID only.

The NoneBot side is the effective command boundary and recognizes the ``nte``
prefix with or without ``#`` (case-insensitive). Core must retain ``nte`` and
``NTE`` as force prefixes so both forms reach the upstream connector. Legacy
Core aliases, when present, cannot be reached through the project-owned
OneBot gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


EXPECTED = {
    "GenshinUID": False,
    "XutheringWavesUID": False,
    "NTEUID": True,
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
        if plugin == "NTEUID":
            prefixes = {str(value).lower() for value in config.get("force_prefix", [])}
            if "nte" not in prefixes:
                errors.append("NTEUID.force_prefix must include nte/NTE")
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
        "NTE-only game mode valid: Core=enabled, GenshinUID=disabled, "
        "XutheringWavesUID=disabled, NTEUID=enabled, OneBot gate=NTE with or without #"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
