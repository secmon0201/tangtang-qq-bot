"""Read-only upstream capability diagnosis for skill routing."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "config" / "upstream-lock.json"
DEFAULT_BACKUP_DIR = ROOT / "data" / "backups"


def _git(repository: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return result.stdout.strip()


def _tracking(repository: Path) -> str | None:
    return _git(repository, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")


@dataclass(frozen=True, slots=True)
class RepositoryStatus:
    name: str
    path: str
    expected_commit: str
    actual_commit: str
    branch: str
    tracking: str
    pristine: bool
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues and self.actual_commit == self.expected_commit


def _latest_backup(root: Path) -> str:
    backup_root = root / "data" / "backups"
    if not backup_root.is_dir():
        return ""
    candidates = sorted(
        (
            item
            for item in backup_root.iterdir()
            if item.is_dir() and item.name.startswith("denia-v2-")
        ),
        key=lambda item: item.name,
    )
    return candidates[-1].name if candidates else ""


def inspect_repository(root: Path, entry: dict) -> RepositoryStatus:
    repository = root.joinpath(*Path(str(entry["path"])).parts)
    issues: list[str] = []
    if not repository.is_dir():
        return RepositoryStatus(
            str(entry.get("name") or entry["path"]),
            str(entry["path"]),
            str(entry.get("commit") or ""),
            "",
            "",
            "",
            False,
            ("missing directory",),
        )
    if not (repository / ".git").exists():
        # Without local metadata git walks up to the parent repository and
        # reports the wrong commit; never trust git output in this state.
        return RepositoryStatus(
            str(entry.get("name") or entry["path"]),
            str(entry["path"]),
            str(entry.get("commit") or ""),
            "",
            "",
            "",
            False,
            ("missing git metadata",),
        )
    actual = _git(repository, "rev-parse", "HEAD") or ""
    branch = _git(repository, "branch", "--show-current") or ""
    tracking = _tracking(repository) or ""
    dirty = _git(repository, "status", "--porcelain", "--untracked-files=all") or ""
    stashes = _git(repository, "stash", "list") or ""
    if dirty:
        issues.append("dirty working tree")
    if stashes:
        issues.append("stash present")
    expected_branch = str(entry.get("branch") or "")
    if branch != expected_branch:
        issues.append(f"branch mismatch: actual={branch or '(none)'} expected={expected_branch}")
    if actual and actual != str(entry.get("commit") or ""):
        issues.append(
            f"commit mismatch: actual={actual} expected={entry.get('commit')}"
        )
    return RepositoryStatus(
        str(entry.get("name") or entry["path"]),
        str(entry["path"]),
        str(entry.get("commit") or ""),
        actual,
        branch,
        tracking,
        not issues,
        tuple(issues),
    )


@dataclass(slots=True)
class CompatibilityReport:
    generated_at: str
    repositories: list[RepositoryStatus] = field(default_factory=list)
    capabilities: dict[str, str] = field(default_factory=dict)
    latest_backup: str = ""
    summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "repositories": [
                {
                    "name": item.name,
                    "path": item.path,
                    "expected_commit": item.expected_commit,
                    "actual_commit": item.actual_commit,
                    "branch": item.branch,
                    "tracking": item.tracking,
                    "pristine": item.pristine,
                    "status": "ok" if item.ok else "attention",
                    "issues": list(item.issues),
                }
                for item in self.repositories
            ],
            "capabilities": self.capabilities,
            "latest_backup": self.latest_backup,
            "summary": self.summary,
        }


def _module_present(root: Path, relative: str) -> bool:
    return (root / relative).is_dir()


def _capability_report(root: Path, statuses: list[RepositoryStatus]) -> dict[str, str]:
    by_path = {item.path: item for item in statuses}
    core = by_path.get("GsUID.Core")
    capabilities: dict[str, str] = {}
    if core is None:
        capabilities["gsuid_core"] = "missing"
    elif core.actual_commit == core.expected_commit and not core.issues:
        capabilities["gsuid_core"] = "supported"
    elif core.actual_commit == core.expected_commit:
        capabilities["gsuid_core"] = "tolerant"
    else:
        capabilities["gsuid_core"] = "unsupported"
    plugins = {
        "genshinuid": "GsUID.Core/gsuid_core/plugins/GenshinUID",
        "nteuid": "GsUID.Core/gsuid_core/plugins/NTEUID",
        "wuwa_uid": "GsUID.Core/gsuid_core/plugins/XutheringWavesUID",
    }
    for name, relative in plugins.items():
        if not _module_present(root, relative):
            capabilities[name] = "missing"
            continue
        entry = next(
            (item for item in statuses if item.path == relative),
            None,
        )
        if entry is None:
            capabilities[name] = "unsupported"
        elif entry.actual_commit == entry.expected_commit and not entry.issues:
            capabilities[name] = "supported"
        elif entry.actual_commit == entry.expected_commit:
            capabilities[name] = "tolerant"
        else:
            capabilities[name] = "unsupported"
    return capabilities


def build_report(root: Path = ROOT) -> CompatibilityReport:
    from datetime import datetime

    lock_path = root / "config" / "upstream-lock.json"
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    statuses = [inspect_repository(root, entry) for entry in payload["repositories"]]
    capabilities = _capability_report(root, statuses)
    counts: dict[str, int] = {}
    for value in capabilities.values():
        counts[value] = counts.get(value, 0) + 1
    return CompatibilityReport(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        repositories=statuses,
        capabilities=capabilities,
        latest_backup=_latest_backup(root),
        summary=counts,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(args.root).to_dict()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    if args.json:
        print(text)
    else:
        print("Upstream compatibility report")
        for item in report["repositories"]:
            print(f"- {item['name']}: {item['status']} {item['issues']}")
        print("Capabilities:", report["capabilities"])
        print("Latest backup:", report["latest_backup"] or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
