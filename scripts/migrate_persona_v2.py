"""Preview/adopt Denia v2 on verified SQLite snapshots; default is dry-run."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.services.persona_cognition import CognitionStore
from bot.services.persona_cognition_migration import migrate_legacy
from bot.services.persona_store import PersonaStore
from bot.services.tangtang_db import TangtangDb


FILES = ('data/personas/denia-history.db', 'data/personas/state.db', 'data/tangtang/tangtang.db')


def sqlite_snapshot(source: Path, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            integrity = dst.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity != 'ok':
                raise ValueError('snapshot_integrity_failed')
            names = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            counts = {name: dst.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in names}
    return {'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'tables': counts}


def exercise(root: Path, workspace: Path) -> dict:
    snapshots = {}
    for relative in FILES:
        if (root / relative).is_file():
            snapshots[relative] = sqlite_snapshot(root / relative, workspace / relative)
    cognition = CognitionStore(TangtangDb(workspace / FILES[0]))
    central = PersonaStore(workspace / FILES[1])
    adopted = migrate_legacy(cognition, central)
    second = migrate_legacy(cognition, central)
    if any(second.values()):
        raise ValueError('migration_not_idempotent')
    with cognition.connect() as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('migrated_integrity_failed')
    return {'source_snapshots': snapshots, 'adopted': adopted, 'repeat_adopted': second}


def apply(root: Path, backup: Path) -> dict:
    if (root / 'logs/bot.pid').exists():
        raise ValueError('stop_nonebot_and_suspend_watchdog_before_migration')
    if backup.exists():
        raise ValueError('backup_directory_must_be_new')
    backup.mkdir(parents=True)
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'files': {}}
    for relative in FILES:
        source = root / relative
        if source.is_file():
            manifest['files'][relative] = sqlite_snapshot(source, backup / relative)
    # Restore into a different directory and compare table counts and hashes.
    with tempfile.TemporaryDirectory(prefix='persona-restore-') as tmp:
        restored = Path(tmp)
        for relative, expected in manifest['files'].items():
            target = restored / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / relative, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected['sha256']:
                raise ValueError('restore_hash_mismatch')
        preview = exercise(restored, restored / 'preview')
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (backup / 'migration-preview.json').write_text(json.dumps(preview, indent=2), encoding='utf-8')
    cognition = CognitionStore(TangtangDb(root / FILES[0]))
    central = PersonaStore(root / FILES[1])
    adopted = migrate_legacy(cognition, central)
    # The activation flag is the final write, after evidence adoption succeeds.
    central.set_option('denia_v2_enabled', True)
    return {'activated': True, 'adopted': adopted, 'backup_verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.apply:
        if not args.backup_dir:
            parser.error('--apply requires --backup-dir')
        result = apply(root, args.backup_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='persona-preview-') as tmp:
            result = exercise(root, Path(tmp))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'source_snapshots'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
