"""Preview or atomically apply the three Agent rollout switches."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
BACKUP_DIR = ROOT / "data" / "backups"
KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_]+)\s*=(?P<value>.*)$")
SWITCH_KEYS = (
    "TANGTANG_CONTEXT_LAYOUT",
    "TANGTANG_NATIVE_ACTION_TOOLS",
    "TANGTANG_CONTEXT_COMPACTION_ENABLED",
)
MODES = {
    "shadow": {
        "TANGTANG_CONTEXT_LAYOUT": "shadow",
        "TANGTANG_NATIVE_ACTION_TOOLS": "shadow",
        "TANGTANG_CONTEXT_COMPACTION_ENABLED": "false",
    },
    "v2": {
        "TANGTANG_CONTEXT_LAYOUT": "v2",
        "TANGTANG_NATIVE_ACTION_TOOLS": "true",
        "TANGTANG_CONTEXT_COMPACTION_ENABLED": "true",
    },
    "rollback": {
        "TANGTANG_CONTEXT_LAYOUT": "v1",
        "TANGTANG_NATIVE_ACTION_TOOLS": "false",
        "TANGTANG_CONTEXT_COMPACTION_ENABLED": "false",
    },
}


def updated_lines(lines: list[str], mode: str) -> tuple[list[str], dict[str, tuple[str, str]]]:
    if mode not in MODES:
        raise ValueError("unknown rollout mode")
    desired = MODES[mode]
    positions: dict[str, int] = {}
    current: dict[str, str] = {}
    for index, line in enumerate(lines):
        match = KEY_RE.match(line)
        if not match:
            continue
        key = match.group("key").upper()
        if key not in desired:
            continue
        if key in positions:
            raise ValueError(f"duplicate rollout key in .env: {key}")
        positions[key] = index
        current[key] = match.group("value").strip()

    output = list(lines)
    changes: dict[str, tuple[str, str]] = {}
    missing: list[str] = []
    for key in SWITCH_KEYS:
        before = current.get(key, "<unset>")
        after = desired[key]
        if before != after:
            changes[key] = (before, after)
        if key in positions:
            output[positions[key]] = f"{key}={after}"
        else:
            missing.append(key)
    if missing:
        if output and output[-1].strip():
            output.append("")
        output.append("# Agent context rollout")
        output.extend(f"{key}={desired[key]}" for key in missing)
    return output, changes


def apply_mode(path: Path, mode: str, *, backup_dir: Path = BACKUP_DIR) -> tuple[Path | None, dict[str, tuple[str, str]]]:
    if not path.is_file():
        raise ValueError(".env does not exist")
    original = path.read_text(encoding="utf-8").splitlines()
    output, changes = updated_lines(original, mode)
    if not changes:
        return None, changes

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = backup_dir / f".env.agent-rollout-{mode}-{stamp}.bak"
    shutil.copy2(path, backup)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.agent-rollout-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            for line in output:
                handle.write(line.rstrip("\r\n") + "\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return backup, changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=tuple(MODES), required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not ENV_PATH.is_file():
        print("Agent rollout configuration failed: .env does not exist")
        return 1
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        _output, changes = updated_lines(lines, args.mode)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Agent rollout configuration failed: {exc}")
        return 1
    for key in SWITCH_KEYS:
        if key in changes:
            before, after = changes[key]
            print(f"{key}: {before} -> {after}")
        else:
            print(f"{key}: unchanged ({MODES[args.mode][key]})")
    if not args.apply:
        print("dry-run only; use --apply to write")
        return 0
    try:
        backup, _changes = apply_mode(ENV_PATH, args.mode)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Agent rollout configuration failed: {exc}")
        return 1
    print(
        "Agent rollout configuration applied; "
        + (f"backup={backup.relative_to(ROOT)}" if backup else "no changes")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
