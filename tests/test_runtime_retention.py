from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from bot.services.runtime_retention import (
    cleanup_avatar_versions,
    cleanup_gsuid_logs,
    cleanup_screenshot_outputs,
)


def _write(path: Path, size: int, modified: datetime) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    timestamp = modified.timestamp()
    os.utime(path, (timestamp, timestamp))
    return path


def test_cleanup_avatar_versions_keeps_only_current_file_per_id(tmp_path: Path) -> None:
    now = datetime.now()
    root = tmp_path / "avatars"
    legacy = _write(root / "7.png", 3, now - timedelta(days=3))
    old = _write(root / "7.aaaaaaaaaaaaaaaa.png", 5, now - timedelta(days=2))
    current = _write(root / "7.bbbbbbbbbbbbbbbb.png", 7, now - timedelta(days=1))
    unrelated = _write(root / "placeholder.png", 11, now)

    result = cleanup_avatar_versions((root,))

    assert result.scanned_files == 3
    assert result.removed_files == 2
    assert result.removed_bytes == 8
    assert not legacy.exists()
    assert not old.exists()
    assert current.exists()
    assert unrelated.exists()


def test_cleanup_screenshot_outputs_applies_retention_and_keeps_latest(tmp_path: Path) -> None:
    now = datetime.now()
    report_dir = tmp_path / "reports"
    expired = _write(report_dir / "asoul_html" / "old.png", 3, now - timedelta(hours=30))
    latest = _write(report_dir / "asoul_html" / "latest.png", 5, now - timedelta(hours=29))
    recent = _write(report_dir / "community_html" / "recent.png", 7, now - timedelta(hours=1))

    result = cleanup_screenshot_outputs(report_dir, retention_hours=24, now=now)

    assert result.scanned_files == 3
    assert result.removed_files == 1
    assert not expired.exists()
    assert latest.exists()
    assert recent.exists()


def test_cleanup_gsuid_logs_enforces_age_and_total_size_without_touching_data(tmp_path: Path) -> None:
    now = datetime.now()
    log_dir = tmp_path / "GsUID.Core" / "data" / "logs"
    player_data = _write(tmp_path / "GsUID.Core" / "data" / "GsData.db", 19, now - timedelta(days=90))
    too_old = _write(log_dir / "old.log", 4, now - timedelta(days=31))
    first = _write(log_dir / "traces" / "first.jsonl", 7, now - timedelta(days=2))
    second = _write(log_dir / "traces" / "second.jsonl", 7, now - timedelta(days=1))
    newest = _write(log_dir / "today.log", 7, now)

    result = cleanup_gsuid_logs(log_dir, retention_days=30, max_bytes=14, now=now)

    assert result.scanned_files == 4
    assert result.removed_files == 2
    assert not too_old.exists()
    assert not first.exists()
    assert second.exists()
    assert newest.exists()
    assert player_data.exists()
