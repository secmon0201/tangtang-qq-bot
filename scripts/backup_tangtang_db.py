from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from bot.config import ROOT


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a consistent Tangtang SQLite backup")
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "data" / "tangtang" / "tangtang.db",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "data" / "backups",
    )
    args = parser.parse_args()
    source = args.source.resolve()
    backup_dir = args.backup_dir.resolve()
    if not source.is_file():
        raise SystemExit(f"Tangtang database does not exist: {source}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"tangtang-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    source_db = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    target_db = sqlite3.connect(target)
    try:
        with source_db, target_db:
            source_db.backup(target_db)
            check = str(target_db.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        target_db.close()
        source_db.close()
    if check != "ok":
        target.unlink(missing_ok=True)
        raise SystemExit(f"Backup quick_check failed: {check}")
    print(
        json.dumps(
            {
                "ok": True,
                "source": str(source),
                "target": str(target),
                "bytes": target.stat().st_size,
                "sha256": _sha256(target),
                "quick_check": check,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
