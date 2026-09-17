"""Preview/apply additive shared-person migration, or roll back its read path."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.services.persona_memory_migration import migrate_people
from bot.services.persona_memory_store import PersonMemoryStore
from bot.services.tangtang_db import TangtangDb


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, action='append')
    parser.add_argument('--mode', choices=('preview', 'apply', 'rollback', 'enable'), default='preview')
    args = parser.parse_args()
    paths = args.db or [ROOT/'data/tangtang/tangtang.db', ROOT/'data/personas/denia-history.db']
    result = []
    for path in paths:
        path = path.resolve()
        if not path.is_file():
            result.append({'database': path.name, 'status': 'missing, skipped'})
            continue
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as conn:
            counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                      for table in ('tangtang_memories', 'tangtang_relationship_state', 'tangtang_calls')}
            if args.mode != 'preview':
                backup = ROOT/'data/backups'/('persona-memory-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))/path.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(backup) as destination:
                    conn.backup(destination)
        row = {'database': path.name, 'mode': args.mode, 'legacy_counts': counts}
        if args.mode == 'apply':
            row['migration'] = migrate_people(TangtangDb(path))
        elif args.mode != 'preview':
            PersonMemoryStore(TangtangDb(path)).set_enabled(args.mode == 'enable')
        result.append(row)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
