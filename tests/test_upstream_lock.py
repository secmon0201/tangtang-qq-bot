from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_upstream_lock import load_lock, repository_path, validate_lock


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
