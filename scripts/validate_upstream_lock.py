"""Validate or refresh independently versioned upstream repository pins."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def load_lock(lock_path: Path) -> dict[str, Any]:
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("repositories"), list):
        raise ValueError("unsupported or malformed upstream lock file")
    return payload


def repository_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe upstream path: {value}")
    return root.joinpath(*relative.parts)


def inspect_repository(root: Path, entry: dict[str, Any]) -> dict[str, str]:
    path = repository_path(root, str(entry["path"]))
    if not (path / ".git").exists():
        raise FileNotFoundError(path)
    dirty = git(path, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise ValueError(f"upstream repository is dirty: {entry['path']}")
    return {
        "url": git(path, "remote", "get-url", "origin"),
        "branch": git(path, "branch", "--show-current"),
        "commit": git(path, "rev-parse", "HEAD"),
    }


def validate_lock(root: Path, payload: dict[str, Any], allow_missing: bool = False) -> list[str]:
    errors: list[str] = []
    for entry in payload["repositories"]:
        try:
            actual = inspect_repository(root, entry)
        except FileNotFoundError:
            if allow_missing:
                continue
            errors.append(f"missing upstream repository: {entry.get('path')}")
            continue
        except (KeyError, ValueError, subprocess.CalledProcessError) as exc:
            errors.append(str(exc))
            continue
        for field, value in actual.items():
            if str(entry.get(field, "")) != value:
                errors.append(
                    f"upstream {field} mismatch for {entry['name']}: "
                    f"lock={entry.get(field)} actual={value}"
                )
    return errors


def refresh_lock(root: Path, lock_path: Path, payload: dict[str, Any]) -> None:
    for entry in payload["repositories"]:
        entry.update(inspect_repository(root, entry))
    lock_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    lock_path = root / "config" / "upstream-lock.json"
    payload = load_lock(lock_path)
    if args.write:
        refresh_lock(root, lock_path, payload)
        print("Upstream lock refreshed from clean local repositories.")
        return 0
    errors = validate_lock(root, payload, allow_missing=args.allow_missing)
    if errors:
        print("Upstream lock validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Upstream lock validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
