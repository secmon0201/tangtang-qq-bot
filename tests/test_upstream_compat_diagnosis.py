"""Read-only upstream capability diagnosis tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.diagnose_upstream_compat import (
    _capability_report,
    build_report,
    inspect_repository,
)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
    )


def _initialize_repository(repository: Path, branch: str = "main") -> str:
    repository.mkdir(parents=True, exist_ok=True)
    _git(repository, "init", "-b", branch)
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "tester")
    (repository / "README.md").write_text("local fixture\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "fixture")
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_missing_git_metadata_is_reported_and_untrusted(tmp_path):
    target = tmp_path / "GsUID.Core"
    target.mkdir(parents=True)
    (target / "gsuid_core").mkdir()
    status = inspect_repository(
        tmp_path,
        {
            "name": "GsUID Core",
            "path": "GsUID.Core",
            "branch": "master",
            "commit": "a" * 40,
        },
    )

    assert status.actual_commit == ""
    assert status.branch == ""
    assert "missing git metadata" in status.issues
    assert not status.ok


def test_matching_repository_is_supported(tmp_path):
    target = tmp_path / "GsUID.Core"
    commit = _initialize_repository(target)
    status = inspect_repository(
        tmp_path,
        {
            "name": "GsUID Core",
            "path": "GsUID.Core",
            "branch": "main",
            "commit": commit,
        },
    )

    assert status.ok
    assert status.actual_commit == commit
    assert not status.issues


def test_dirty_repository_is_tolerant(tmp_path):
    target = tmp_path / "GsUID.Core"
    commit = _initialize_repository(target)
    (target / "untracked.txt").write_text("drift\n", encoding="utf-8")
    status = inspect_repository(
        tmp_path,
        {
            "name": "GsUID Core",
            "path": "GsUID.Core",
            "branch": "main",
            "commit": commit,
        },
    )

    assert status.actual_commit == commit
    assert "dirty working tree" in status.issues
    assert not status.ok
    capabilities = _capability_report(tmp_path, [status])
    assert capabilities["gsuid_core"] == "tolerant"


def test_commit_mismatch_is_unsupported(tmp_path):
    target = tmp_path / "GsUID.Core"
    _initialize_repository(target)
    status = inspect_repository(
        tmp_path,
        {
            "name": "GsUID Core",
            "path": "GsUID.Core",
            "branch": "main",
            "commit": "b" * 40,
        },
    )

    capabilities = _capability_report(tmp_path, [status])
    assert capabilities["gsuid_core"] == "unsupported"
    assert any("commit mismatch" in issue for issue in status.issues)


def test_build_report_reads_lock_and_backups(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "upstream-lock.json").write_text(
        '{"schema_version": 1, "repositories": []}', encoding="utf-8"
    )
    (tmp_path / "data" / "backups" / "denia-v2-test-001").mkdir(parents=True)
    report = build_report(tmp_path).to_dict()

    assert report["repositories"] == []
    assert report["latest_backup"] == "denia-v2-test-001"
    assert report["summary"] == {"missing": 4}
    assert set(report["capabilities"].values()) == {"missing"}
