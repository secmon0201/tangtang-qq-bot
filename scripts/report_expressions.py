"""Read-only daily expression ranking and decision diagnostics; never contacts QQ."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo


def report(database: Path, catalog: Path, day: str, persona: str, group: int | None) -> dict:
    start = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    end = start + timedelta(days=1)
    conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM expression_events WHERE persona=? AND (created_at>=? AND created_at<? OR completed_at>=? AND completed_at<?)"
        args = [persona, start.timestamp(), end.timestamp(), start.timestamp(), end.timestamp()]
        if group is not None:
            query += " AND group_id=?"
            args.append(group)
        events = [dict(row) for row in conn.execute(query, args)]
    finally:
        conn.close()
    stats = defaultdict(Counter)
    reasons = Counter()
    for event in events:
        key = event["selected_id"]
        when = event["completed_at"]
        if key and when is not None and start.timestamp() <= when < end.timestamp():
            stats[key][event["status"]] += 1
        detail = json.loads(event["decision_json"])
        if start.timestamp() <= event["created_at"] < end.timestamp():
            reasons[event["reason"]] += 1
            for candidate in detail.get("candidates", []):
                stats[candidate]["nominated"] += 1
            for candidate, reason in detail.get("rejected", {}).items():
                stats[candidate][reason] += 1
    rows = json.loads(catalog.read_text(encoding="utf-8"))
    ranking = [dict(id=r["id"], name=r["name"], delivered=stats[r["id"]].get("delivered", 0),
                    details=dict(stats[r["id"]])) for r in rows]
    ranking.sort(key=lambda row: (-row["delivered"], row["id"]))
    return dict(day=day, persona=persona, group=group, total=sum(r["delivered"] for r in ranking),
                reasons=dict(reasons), ranking=ranking,
                note="delivered=平台回执确认；sending/uncertain不算成功。导入旧发送记录不含模型候选。")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    parser.add_argument("--group", type=int)
    parser.add_argument("--persona", default="denia")
    parser.add_argument("--database", type=Path, default=root / "data/personas/state.db")
    parser.add_argument("--catalog", type=Path, default=root / "bot/resources/personas/denia/expression_catalog.json")
    args = parser.parse_args()
    print(json.dumps(report(args.database, args.catalog, args.day, args.persona, args.group), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
