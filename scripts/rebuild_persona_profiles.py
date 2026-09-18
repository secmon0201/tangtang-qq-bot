"""Prepare automatic evidence-based profile rebuild; preview by default."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.services.persona_cognition import CognitionStore
from bot.services.persona_profile_history import rebuild_from_history
from bot.services.persona_store import PersonaStore
from bot.services.tangtang_db import TangtangDb
from scripts.migrate_persona_v2 import FILES, sqlite_snapshot


def prepare(root):
    return rebuild_from_history(CognitionStore(TangtangDb(root / FILES[0])),
                                TangtangDb(root / FILES[2]), PersonaStore(root / FILES[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.apply and ((root / 'logs/bot.pid').exists() or not args.backup_dir or args.backup_dir.exists()):
        parser.error('Apply requires stopped NoneBot and a new --backup-dir; suspend watchdog first.')
    with tempfile.TemporaryDirectory(prefix='profile-rebuild-') as tmp:
        workspace = args.backup_dir.resolve() if args.apply else Path(tmp)
        if args.apply:
            workspace.mkdir(parents=True)
        manifest = {relative: sqlite_snapshot(root / relative, workspace / relative) for relative in FILES}
        # Rehearse on copies; never modify the original backup used for recovery.
        rehearsal = Path(tmp) / 'rehearsal'
        for relative in FILES:
            sqlite_snapshot(workspace / relative, rehearsal / relative)
        preview = prepare(rehearsal)
        if prepare(rehearsal) != {'already_prepared': True}:
            raise ValueError('profile_rebuild_not_idempotent')
        result = {'preview': preview, 'applied': args.apply}
        if args.apply:
            (workspace / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            result['result'] = prepare(root)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
