"""Read-only, aggregate reply-style observation; no message text or identities."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.services.reply_style import expression_counts


TARGETS = {"wu_opening": 15.0, "dash": 8.0, "ellipsis": 25.0}


def summarize(texts: list[str]) -> dict:
    counts = expression_counts(texts)
    total = counts["reply_count"]
    return {"reply_count": total, "patterns": {
        key: {"count": value, "percent": round(100 * value / total, 2) if total else None}
        for key, value in counts.items() if key != "reply_count"
    }}


def build_report(db_path: Path, *, since: datetime, until: datetime) -> dict:
    if since.utcoffset() is None or until.utcoffset() is None or since >= until:
        raise ValueError("use timezone-aware bounds with since < until")
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            "SELECT c.reply_text, c.mode, c.reply_kind FROM tangtang_calls c "
            "WHERE c.reply_kind IN ('model','proactive') AND trim(c.reply_text) <> '' "
            "AND julianday(c.created_at) >= julianday(?) AND julianday(c.created_at) < julianday(?) "
            "AND (NOT EXISTS (SELECT 1 FROM tangtang_reply_parts p WHERE p.call_id=c.id) "
            "OR EXISTS (SELECT 1 FROM tangtang_reply_parts p WHERE p.call_id=c.id AND p.delivered=1)) "
            "ORDER BY c.id", (since.isoformat(), until.isoformat()),
        ).fetchall()
    grouped: dict[str, list[str]] = {"call": [], "continuation": [], "proactive": [], "other": []}
    for text, mode, kind in rows:
        category = ("proactive" if kind == "proactive" else "continuation" if mode == "continuation"
                    else "call" if mode in {"c", "d"} else "other")
        grouped[category].append(text)
    overall = summarize([r[0] for r in rows])
    hours = (until - since).total_seconds() / 3600
    eligible = hours >= 24 and len(rows) >= 200
    return {
        "report_version": "reply-style-v1", "privacy": "aggregates_only",
        "since": since.isoformat(), "until": until.isoformat(),
        "unit": "one persisted delivered model reply turn; categories may overlap",
        "overall": overall, "by_mode": {key: summarize(texts) for key, texts in grouped.items()},
        "observation": {
            "hours": round(hours, 2), "minimum_hours": 24, "minimum_replies": 200,
            "eligible": eligible, "target_percent": TARGETS,
            "frequency_targets_met": (
                all(100 * overall['patterns'][key]['count'] / len(rows) <= target
                    for key, target in TARGETS.items()) if eligible else None
            ),
            "qualitative_review": "pending_manual_review_of_30_replies_with_context",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT/'data/personas/denia-history.db')
    parser.add_argument('--since', required=True, help='inclusive ISO timestamp with timezone')
    parser.add_argument('--until', default='', help='exclusive ISO timestamp, default now')
    parser.add_argument('--output', type=Path, help='optional aggregate JSON inside reports/')
    args = parser.parse_args()
    try:
        output = args.output.resolve() if args.output else None
        if output is not None and not output.is_relative_to((ROOT/'reports').resolve()):
            raise ValueError('output must be inside reports/')
        report = build_report(args.db, since=datetime.fromisoformat(args.since),
                              until=datetime.fromisoformat(args.until) if args.until else datetime.now().astimezone())
        text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding='utf-8')
        else:
            print(text, end='')
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f'Reply style report failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
