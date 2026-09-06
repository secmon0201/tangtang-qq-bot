from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from bot.services.avatars import AvatarService


GSUID_LOG_RETENTION_DAYS = 30
GSUID_LOG_MAX_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CleanupResult:
    scanned_files: int = 0
    removed_files: int = 0
    removed_bytes: int = 0

    def __add__(self, other: "CleanupResult") -> "CleanupResult":
        return CleanupResult(
            self.scanned_files + other.scanned_files,
            self.removed_files + other.removed_files,
            self.removed_bytes + other.removed_bytes,
        )


def cleanup_avatar_versions(cache_dirs: Iterable[Path]) -> CleanupResult:
    result = CleanupResult()
    for cache_dir in cache_dirs:
        scanned, removed, removed_bytes = AvatarService.cleanup_stale_versions(cache_dir)
        result += CleanupResult(scanned, removed, removed_bytes)
    return result


def cleanup_screenshot_outputs(
    report_dir: Path,
    *,
    retention_hours: int,
    now: datetime | None = None,
) -> CleanupResult:
    cutoff = (now or datetime.now()) - timedelta(hours=max(1, int(retention_hours)))
    result = CleanupResult()
    for name in ("asoul_html", "community_html"):
        result += _cleanup_older_than(report_dir / name, cutoff, keep_latest=True)
    return result


def cleanup_gsuid_logs(
    log_dir: Path,
    *,
    retention_days: int = GSUID_LOG_RETENTION_DAYS,
    max_bytes: int = GSUID_LOG_MAX_BYTES,
    now: datetime | None = None,
) -> CleanupResult:
    files = _files_oldest_first(log_dir)
    if not files:
        return CleanupResult()

    cutoff = (now or datetime.now()) - timedelta(days=max(1, int(retention_days)))
    protected = files[-1][1]
    removed_files = 0
    removed_bytes = 0
    survivors: list[tuple[float, Path, int]] = []
    for modified, path, size in files:
        if path != protected and datetime.fromtimestamp(modified) < cutoff and _unlink(path):
            removed_files += 1
            removed_bytes += size
        else:
            survivors.append((modified, path, size))

    total_bytes = sum(item[2] for item in survivors)
    limit = max(1, int(max_bytes))
    for _modified, path, size in survivors:
        if total_bytes <= limit:
            break
        if path == protected:
            continue
        if _unlink(path):
            removed_files += 1
            removed_bytes += size
            total_bytes -= size

    _remove_empty_directories(log_dir)
    return CleanupResult(len(files), removed_files, removed_bytes)


def _cleanup_older_than(root: Path, cutoff: datetime, *, keep_latest: bool) -> CleanupResult:
    files = _files_oldest_first(root)
    protected = files[-1][1] if files and keep_latest else None
    removed_files = 0
    removed_bytes = 0
    for modified, path, size in files:
        if path == protected or datetime.fromtimestamp(modified) >= cutoff:
            continue
        if _unlink(path):
            removed_files += 1
            removed_bytes += size
    _remove_empty_directories(root)
    return CleanupResult(len(files), removed_files, removed_bytes)


def _files_oldest_first(root: Path) -> list[tuple[float, Path, int]]:
    if not root.is_dir():
        return []
    files: list[tuple[float, Path, int]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((stat.st_mtime, path, stat.st_size))
    files.sort(key=lambda item: (item[0], str(item[1]).lower()))
    return files


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _remove_empty_directories(root: Path) -> None:
    if not root.is_dir():
        return
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in directories:
        try:
            path.rmdir()
        except OSError:
            continue
