"""Preview or reversibly normalize recognized voice transport in saved context.

Run while Harness is stopped before using --apply. Historical request payloads
remain evidence of what was actually sent and are never rewritten.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tangtang_harness.message_text import VOICE_MARKER, message_text, normalize_voice
from tangtang_harness.types import InboundEvent


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_payload(value: dict[str, Any]) -> dict[str, Any]:
    # Keep extra historical metadata and normalize only message-bearing fields.
    normalized = InboundEvent.from_dict(value).to_dict()
    return {**value, **{key: normalized[key] for key in ("text", "segments", "quoted")
                        if key in value or normalized[key]}}


def message_ids(raw: str) -> set[str]:
    values = json.loads(raw)
    if not isinstance(values, list) or not values or any(not str(value).strip() for value in values):
        return set()
    return {str(value) for value in values}


def cleanup_voice_history(root: Path, *, apply: bool = False) -> dict[str, Any]:
    """Return aggregate counts; --apply saves exact fields before one transaction."""
    root = Path(root).resolve()
    database = root / "data" / "harness.db"
    conn = sqlite3.connect(database.as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    backup = None
    backup_path = None
    totals = {"rows": 0, "fields": 0, "chars_removed": 0}
    table_totals: dict[str, dict[str, int]] = {}
    modified_rows: dict[str, set[tuple[Any, ...]]] = defaultdict(set)

    def change(table: str, keys: dict[str, Any], field: str, before: str, value: Any) -> None:
        nonlocal backup, backup_path
        after = encode(value)
        # Preserve original byte representation when the semantic value is equal.
        if json.loads(before) == json.loads(after):
            return
        counts = table_totals.setdefault(table, {"rows": 0, "fields": 0, "chars_removed": 0})
        identity = tuple(keys.values())
        if identity not in modified_rows[table]:
            modified_rows[table].add(identity)
            counts["rows"] += 1
            totals["rows"] += 1
        removed = max(0, len(before) - len(after))
        counts["fields"] += 1
        counts["chars_removed"] += removed
        totals["fields"] += 1
        totals["chars_removed"] += removed
        if not apply:
            return
        if backup is None:
            directory = root / "runtime" / "verification"
            directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = directory / f"voice-history-{timestamp}-{uuid.uuid4().hex[:8]}.jsonl"
            backup = backup_path.open("x", encoding="utf-8", newline="\n")
            backup.write(encode({"format": "voice-history-fields-v1", "database": "data/harness.db"}) + "\n")
        backup.write(encode({"table": table, "where": keys, "field": field,
                             "before": before, "after": after}) + "\n")
        predicate = " AND ".join(f"{key}=?" for key in keys)
        conn.execute(f"UPDATE {table} SET {field}=? WHERE {predicate}", (after, *keys.values()))

    try:
        if apply:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")

        # A delivered standalone recording plus its actual QQ receipts identifies
        # the historical confirm rows that incorrectly retained the spoken text.
        voice_receipts: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in conn.execute("SELECT session_key,request_id,messages,message_ids,outcome FROM deliveries"):
            values = json.loads(row["messages"])
            ids = message_ids(row["message_ids"])
            if row["outcome"] == "delivered" and ids and message_text(values) == VOICE_MARKER:
                voice_receipts[(row["session_key"], row["request_id"])].update(ids)

        def successful_voice(row: sqlite3.Row) -> bool:
            ids = message_ids(row["message_ids"])
            receipts = voice_receipts.get((row["session_key"], row["request_id"]), set())
            return bool(ids and ids <= receipts)

        for row in conn.execute("SELECT id,payload FROM events"):
            change("events", {"id": row["id"]}, "payload", row["payload"], event_payload(json.loads(row["payload"])))
        for row in conn.execute("SELECT id,session_key,request_id,message_ids,messages,outcome FROM deliveries"):
            values = json.loads(row["messages"])
            normalized = [VOICE_MARKER] if row["outcome"] == "delivered" and successful_voice(row) else normalize_voice(values)
            change("deliveries", {"id": row["id"]}, "messages", row["messages"], normalized)
        for row in conn.execute("SELECT id,session_key,request_id,message_ids,messages,user_content,status FROM turns"):
            values = json.loads(row["messages"])
            normalized = [VOICE_MARKER] if row["status"] == "delivered" and successful_voice(row) else normalize_voice(values)
            change("turns", {"id": row["id"]}, "messages", row["messages"], normalized)
            change("turns", {"id": row["id"]}, "user_content", row["user_content"], normalize_voice(json.loads(row["user_content"])))

        for table, field, keys in (("background_jobs", "source", ("id",)),
                                   ("snapshots", "content", ("id",)),
                                   ("summary_versions", "content", ("session_key", "revision")),
                                   ("settings", "value", ("key",))):
            for row in conn.execute(f"SELECT {','.join(keys)},{field} FROM {table}"):
                change(table, {key: row[key] for key in keys}, field, row[field], normalize_voice(json.loads(row[field])))

        if backup is not None:
            # Persist all exact before values before making the transaction visible.
            backup.flush()
            os.fsync(backup.fileno())
        if apply:
            conn.commit()
        else:
            conn.rollback()
        if backup is not None:
            backup.write(encode({"committed": True, **totals}) + "\n")
            backup.flush()
            os.fsync(backup.fileno())
        result = {"mode": "apply" if apply else "dry_run", **totals, "tables": table_totals}
        if backup_path is not None:
            result["backup"] = backup_path.relative_to(root).as_posix()
        return result
    except BaseException:
        conn.rollback()
        raise
    finally:
        if backup is not None:
            backup.close()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="只标记语音；默认只读预览，--apply 保存逐字段备份后原子更新")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(cleanup_voice_history(args.root, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
