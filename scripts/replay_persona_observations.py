"""Preview/requeue quarantined observations after fixing the extraction cause."""
import argparse
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = args.root / 'data/tangtang/tangtang.db'
    uri = path.resolve().as_uri() + ('?mode=rw' if args.apply else '?mode=ro')
    with sqlite3.connect(uri, uri=True) as conn:
        if args.apply:
            conn.execute('BEGIN IMMEDIATE')
        ids = [r[0] for r in conn.execute("""SELECT id FROM persona_observation_inbox
            WHERE state='quarantined' ORDER BY id LIMIT ?""", (max(1, min(args.limit, 1000)),))]
        if args.apply:
            conn.executemany("""UPDATE persona_observation_inbox SET state='pending',revision=revision+1,
                owner='',epoch=epoch+1,lease_until=0,attempts=0,next_attempt=0,error=''
                WHERE id=? AND state='quarantined'""", [(i,) for i in ids])
    print(json.dumps({'selected': len(ids), 'applied': args.apply}))


if __name__ == '__main__':
    main()
