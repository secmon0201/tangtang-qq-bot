from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SQLITE_HEADER = b"SQLite format 3\x00"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return source.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def _audit_sqlite(path: Path, base: Path) -> dict[str, Any]:
    stat = path.stat()
    result: dict[str, Any] = {
        "path": path.relative_to(base).as_posix(),
        "bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "sha256": _sha256(path),
        "quick_check": "unavailable",
        "tables": [],
    }
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            result["quick_check"] = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for table in tables:
                escaped = table.replace('"', '""')
                try:
                    count = int(connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0])
                except sqlite3.DatabaseError:
                    count = None
                result["tables"].append({"name": table, "rows": count})
    except sqlite3.DatabaseError as exc:
        result["quick_check"] = f"error: {exc}"
    return result


def build_audit(backup_root: Path) -> dict[str, Any]:
    files = sorted(path for path in backup_root.rglob("*") if path.is_file())
    sqlite_files = [path for path in files if _is_sqlite(path)]
    return {
        "backup_root": str(backup_root.resolve()),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_files": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "sqlite_files": [_audit_sqlite(path, backup_root) for path in sqlite_files],
        "non_sqlite_files": len(files) - len(sqlite_files),
        "note": "Only metadata, integrity results, table names, and row counts are included; no user records are exported.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit database backups without exporting record contents")
    parser.add_argument("--backup-root", type=Path, default=ROOT / "data" / "backups")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    backup_root = args.backup_root.resolve()
    if not backup_root.is_dir():
        raise SystemExit(f"Backup directory does not exist: {backup_root}")
    audit = build_audit(backup_root)
    payload = json.dumps(audit, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
        print(output)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
