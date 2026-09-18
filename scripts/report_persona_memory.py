"""Read-only aggregate diagnostics for personal memory; no private message output."""
import argparse
import json
from pathlib import Path
import sqlite3
import time


def report(root: Path) -> dict:
    result = {}
    now = time.time()
    for relative, tables in (
        ('data/tangtang/tangtang.db', ('persona_observation_inbox',)),
        ('data/personas/denia-history.db', ('persona_actions', 'persona_intents')),
    ):
        path = root / relative
        if not path.is_file():
            continue
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.execute('BEGIN')
            existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in tables:
                if table in existing:
                    result[table] = dict(conn.execute(f'SELECT state,count(*) FROM {table} GROUP BY state'))
            if 'persona_observation_inbox' in tables and 'persona_observation_inbox' in existing:
                row = conn.execute("""SELECT min(received_at),count(DISTINCT user_id),sum(length(text))
                    FROM persona_observation_inbox WHERE state<>'applied'""").fetchone()
                result.update(oldest_pending_seconds=round(max(0, now - row[0]), 2) if row[0] else 0,
                              pending_users=row[1], protected_characters=row[2] or 0)
                latencies = sorted(r[0] for r in conn.execute("""SELECT completed_at-received_at
                    FROM persona_observation_inbox WHERE completed_at>0 AND completed_at>?""", (now - 86400,)))
                if latencies:
                    result['latency_seconds'] = {f'p{p}': round(latencies[min(len(latencies)-1, int((len(latencies)-1)*p/100))], 2)
                                                 for p in (50, 95)}
            if 'persona_cognition_reviews' in existing:
                reviews = [json.loads(r[0]) for r in conn.execute('SELECT detail FROM persona_cognition_reviews WHERE created_at>?', (now - 86400,))]
                result['rejected_patches_24h'] = sum(len(r.get('rejected', [])) for r in reviews)
    central = root / 'data/personas/state.db'
    if central.is_file():
        with sqlite3.connect(central.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            jobs = [json.loads(r[0]) for r in conn.execute("SELECT usage FROM jobs WHERE kind='personal_memory' AND created_at>?", (now - 86400,))]
            result['background_calls_24h'] = len(jobs)
            result['background_tokens_24h'] = sum(int(r.get('total_tokens', 0)) for r in jobs)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    print(json.dumps(report(parser.parse_args().root), ensure_ascii=False, indent=2))
