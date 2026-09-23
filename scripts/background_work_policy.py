"""Inspect background request accounting or change one non-secret budget setting."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot.services.background_work import DEFAULT_POLICY


def report(path, since):
    result = {'since': datetime.fromtimestamp(since).astimezone().isoformat(), 'policy': dict(DEFAULT_POLICY), 'requests': []}
    if not path.is_file():
        return result
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        saved = conn.execute("SELECT value FROM options WHERE key='background_work_policy'").fetchone()
        if saved:
            result['policy'].update(json.loads(saved[0]))
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='background_work_calls'").fetchone():
            return result
        rows = [dict(r) for r in conn.execute('SELECT * FROM background_work_calls WHERE started_at>=?', (since,))]
        for kind in ('summary', 'memory', 'profile'):
            selected = [r for r in rows if r['kind'] == kind]
            usage = [json.loads(r['usage']) for r in selected]
            result['requests'].append({'kind': kind, 'attempts': len(selected),
                'historical_attempts': sum(r['historical'] for r in selected),
                'statuses': dict(Counter(r['status'] for r in selected)),
                'charged_or_reserved_tokens': sum(r['charged_tokens'] for r in selected),
                'reported_tokens': sum(u.get('total_tokens', u.get('prompt_tokens', 0) + u.get('completion_tokens', 0)) for u in usage),
                'distinct_batches': len({r['batch'] for r in selected})})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours', type=float, default=1)
    parser.add_argument('--since', help='ISO timestamp with timezone')
    parser.add_argument('--set', nargs=2, metavar=('KEY', 'INTEGER'))
    args = parser.parse_args()
    if not 0 < args.hours <= 168:
        parser.error('--hours must be between 0 and 168')
    path = ROOT / 'data/personas/state.db'
    if args.set:
        key, raw = args.set
        if key not in DEFAULT_POLICY:
            parser.error('unknown key: ' + key)
        try:
            value = int(raw)
        except ValueError:
            parser.error('value must be an integer')
        if not 0 <= value <= 10000000:
            parser.error('value must be between 0 and 10000000')
        from bot.services.persona_store import PersonaStore
        store = PersonaStore(path)
        saved = store.option('background_work_policy', {})
        store.set_option('background_work_policy', {**saved, key: value}, invalidate=False)
    since = datetime.fromisoformat(args.since).timestamp() if args.since else time.time() - args.hours * 3600
    print(json.dumps(report(path, since), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
