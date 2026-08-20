"""Reject sensitive, generated, runtime, and oversized Git candidates."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath


FORBIDDEN_PREFIXES = (
    ".codex/",
    ".venv/",
    "backups/",
    "data/",
    "deploy/",
    "downloads/",
    "gsuid.core/",
    "lagrange.onebot/",
    "logs/",
    "napcat.shell/",
    "reports/",
    "scripts/vm/",
)
FORBIDDEN_ROOT_PREFIXES = ("linux_vm_",)
FORBIDDEN_SUFFIXES = (
    ".db",
    ".dll",
    ".env",
    ".exe",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".sqlite",
    ".sqlite3",
)
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{32,}\b"),
)
MAX_FILE_BYTES = 50 * 1024 * 1024


def git_candidates(project_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    return [entry.decode("utf-8") for entry in result.stdout.split(b"\0") if entry]


def git_history_paths(project_root: Path) -> list[str]:
    """Return every path reachable from local branches and tags."""

    revisions = subprocess.run(
        ["git", "rev-list", "--all"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    paths: set[str] = set()
    for revision in revisions:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "-z", revision],
            cwd=project_root,
            check=True,
            capture_output=True,
        )
        paths.update(
            entry.decode("utf-8")
            for entry in result.stdout.split(b"\0")
            if entry
        )
    return sorted(paths)


def path_policy_error(raw_path: str) -> str | None:
    normalised = PurePosixPath(raw_path.replace("\\", "/")).as_posix()
    lowered = normalised.lower()
    if lowered == ".env" or any(
        lowered.startswith(prefix) for prefix in FORBIDDEN_PREFIXES
    ) or ("/" not in lowered and lowered.startswith(FORBIDDEN_ROOT_PREFIXES)):
        return f"forbidden path: {normalised}"
    if lowered != ".env.example" and lowered.endswith(FORBIDDEN_SUFFIXES):
        return f"forbidden file type: {normalised}"
    return None


def validate_history_paths(paths: list[str]) -> list[str]:
    return [
        f"historical {error}"
        for raw_path in paths
        if (error := path_policy_error(raw_path)) is not None
    ]


def validate_candidates(project_root: Path, candidates: list[str]) -> list[str]:
    errors: list[str] = []
    for raw_path in candidates:
        normalised = PurePosixPath(raw_path.replace("\\", "/")).as_posix()
        if error := path_policy_error(normalised):
            errors.append(error)
            continue

        source = project_root / Path(*PurePosixPath(normalised).parts)
        if not source.is_file():
            errors.append(f"missing Git candidate: {normalised}")
            continue
        size = source.stat().st_size
        if size > MAX_FILE_BYTES:
            errors.append(f"oversized file ({size} bytes): {normalised}")
            continue
        if size <= 2 * 1024 * 1024:
            content = source.read_bytes()
            if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                errors.append(f"credential-like content: {normalised}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    errors = validate_candidates(root, git_candidates(root))
    errors.extend(validate_history_paths(git_history_paths(root)))
    if errors:
        print("Repository validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "Repository validation passed: current candidates and reachable history "
        "contain no forbidden artifacts."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
