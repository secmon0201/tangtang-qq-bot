from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from scripts.validate_upstream_lock import (
    load_lock,
    refresh_lock,
    repository_path,
    validate_lock,
)


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _upstream_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    repository = tmp_path / "external" / "example"
    remote.mkdir()
    seed.mkdir()
    _git(remote, "init", "--bare")
    _git(seed, "init", "--initial-branch=main")
    (seed / "README.md").write_text("upstream\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(
        seed,
        "-c",
        "user.name=Upstream Test",
        "-c",
        "user.email=upstream@example.invalid",
        "commit",
        "-m",
        "initial",
    )
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")
    repository.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote), str(repository)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "repositories": [
            {
                "name": "example",
                "path": "external/example",
                "url": str(remote),
                "branch": "main",
                "commit": _git(repository, "rev-parse", "HEAD"),
            }
        ],
    }
    return tmp_path, repository, payload


def test_upstream_lock_schema_and_paths_are_valid():
    root = Path(__file__).resolve().parents[1]
    payload = load_lock(root / "config" / "upstream-lock.json")

    assert {entry["name"] for entry in payload["repositories"]} == {
        "GsUID Core",
        "GenshinUID connector",
        "NTEUID",
        "XutheringWavesUID",
        "RoverSign",
        "TodayEcho",
        "ScoreEcho",
        "RoverReminder",
    }
    assert validate_lock(root, payload) == []


def test_upstream_lock_can_be_schema_checked_without_local_repositories(tmp_path):
    payload = {
        "schema_version": 1,
        "repositories": [
            {
                "name": "example",
                "path": "external/example",
                "url": "https://example.invalid/repo.git",
                "branch": "main",
                "commit": "a" * 40,
            }
        ],
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_lock(lock_path) == payload
    assert validate_lock(tmp_path, payload, allow_missing=True) == []


def test_upstream_paths_cannot_escape_the_project(tmp_path):
    try:
        repository_path(tmp_path, "../outside")
    except ValueError as exc:
        assert "unsafe upstream path" in str(exc)
    else:
        raise AssertionError("unsafe path was accepted")


def test_upstream_lock_rejects_missing_tracking_branch(tmp_path):
    root, repository, payload = _upstream_fixture(tmp_path)
    _git(repository, "branch", "--unset-upstream")

    assert validate_lock(root, payload) == [
        "upstream repository has no tracking branch: external/example"
    ]


def test_upstream_lock_rejects_wrong_tracking_branch(tmp_path):
    root, repository, payload = _upstream_fixture(tmp_path)
    _git(repository, "branch", "other")
    _git(repository, "push", "origin", "other")
    _git(repository, "fetch", "origin", "other")
    _git(repository, "branch", "--set-upstream-to=origin/other", "main")

    assert validate_lock(root, payload) == [
        "upstream tracking mismatch for example: expected=origin/main actual=origin/other"
    ]


def test_upstream_lock_rejects_local_only_commits(tmp_path):
    root, repository, payload = _upstream_fixture(tmp_path)
    (repository / "local.txt").write_text("local patch\n", encoding="utf-8")
    _git(repository, "add", "local.txt")
    _git(
        repository,
        "-c",
        "user.name=Local Test",
        "-c",
        "user.email=local@example.invalid",
        "commit",
        "-m",
        "local patch",
    )
    payload["repositories"][0]["commit"] = _git(repository, "rev-parse", "HEAD")

    assert validate_lock(root, payload) == [
        "upstream repository has local-only commits or diverged history: external/example"
    ]


def test_upstream_lock_allows_remote_to_be_ahead(tmp_path):
    root, repository, payload = _upstream_fixture(tmp_path)
    publisher = tmp_path / "publisher"
    remote = Path(payload["repositories"][0]["url"])
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote), str(publisher)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    (publisher / "remote.txt").write_text("remote update\n", encoding="utf-8")
    _git(publisher, "add", "remote.txt")
    _git(
        publisher,
        "-c",
        "user.name=Remote Test",
        "-c",
        "user.email=remote@example.invalid",
        "commit",
        "-m",
        "remote update",
    )
    _git(publisher, "push", "origin", "main")
    _git(repository, "fetch", "origin", "main")

    assert validate_lock(root, payload) == []


def test_upstream_lock_rejects_diverged_history(tmp_path):
    root, repository, payload = _upstream_fixture(tmp_path)
    publisher = tmp_path / "publisher"
    remote = Path(payload["repositories"][0]["url"])
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote), str(publisher)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    (repository / "local.txt").write_text("local patch\n", encoding="utf-8")
    _git(repository, "add", "local.txt")
    _git(
        repository,
        "-c",
        "user.name=Local Test",
        "-c",
        "user.email=local@example.invalid",
        "commit",
        "-m",
        "local patch",
    )
    payload["repositories"][0]["commit"] = _git(repository, "rev-parse", "HEAD")
    (publisher / "remote.txt").write_text("remote update\n", encoding="utf-8")
    _git(publisher, "add", "remote.txt")
    _git(
        publisher,
        "-c",
        "user.name=Remote Test",
        "-c",
        "user.email=remote@example.invalid",
        "commit",
        "-m",
        "remote update",
    )
    _git(publisher, "push", "origin", "main")
    _git(repository, "fetch", "origin", "main")

    assert validate_lock(root, payload) == [
        "upstream repository has local-only commits or diverged history: external/example"
    ]


def test_upstream_lock_rejects_stashes(tmp_path):
    root, repository, payload = _upstream_fixture(tmp_path)
    (repository / "README.md").write_text("stashed patch\n", encoding="utf-8")
    _git(repository, "stash", "push", "-m", "local patch")

    assert validate_lock(root, payload) == [
        "upstream repository has stashes: external/example"
    ]


def test_upstream_lock_rejects_untracked_files(tmp_path):
    root, repository, payload = _upstream_fixture(tmp_path)
    (repository / "local_adapter.py").write_text("LOCAL = True\n", encoding="utf-8")

    assert validate_lock(root, payload) == [
        "upstream repository is dirty: external/example"
    ]


def test_refresh_lock_does_not_accept_a_different_origin(tmp_path):
    root, _, payload = _upstream_fixture(tmp_path)
    payload["repositories"][0]["url"] = "https://example.invalid/wrong.git"
    lock_path = tmp_path / "upstream-lock.json"

    try:
        refresh_lock(root, lock_path, payload)
    except ValueError as exc:
        assert "upstream url mismatch" in str(exc)
    else:
        raise AssertionError("refresh accepted a different origin URL")


def test_installer_uses_lock_and_only_fast_forward_sync():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "install_gsuid.ps1").read_text(encoding="utf-8")

    assert "ConvertFrom-Json" in script
    assert "foreach ($repository in $UpstreamLock.repositories)" in script
    assert "status --porcelain --untracked-files=all" in script
    assert "stash list" in script
    assert "branch --set-upstream-to=$expectedTracking" in script
    assert "merge-base --is-ancestor HEAD" in script
    assert "pull --ff-only origin $targetBranch" in script
    assert "https://github.com/" not in script
    assert not re.search(r"\bgit\b[^\r\n]*\breset\b", script, re.IGNORECASE)
    assert not re.search(r"\bgit\b[^\r\n]*\bclean\b", script, re.IGNORECASE)
    assert not re.search(
        r"\bgit\b[^\r\n]*\bstash\s+(?:push|pop|apply|drop|clear)\b",
        script,
        re.IGNORECASE,
    )
