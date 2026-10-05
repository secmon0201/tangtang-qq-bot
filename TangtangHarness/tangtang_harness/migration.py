"""Read-only online snapshots and repeatable, version-aware legacy imports."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .store import Store, encode


SOURCES = {
    "business": "data/bot.db", "history": "data/personas/denia-history.db",
    "tangtang": "data/tangtang/tangtang.db", "knowledge": "data/knowledge/knowledge.db",
    "persona": "data/personas/state.db", "gallery": "data/tangtang/denia-gallery.db",
    "continuation": "data/personas/continuation.db", "proactive": "data/tangtang/proactive.db",
    "skill_audit": "data/skills/audit.db", "skill_metrics": "data/skills/metrics.db",
}
TARGETS = {"business": "business.db", "history": "business-history.db", "tangtang": "business-history.db", "knowledge": "knowledge.db", "gallery": "gallery.db", "skill_audit": "business.db"}
ARCHIVE_INDEX_TABLES = {"person_facts", "person_restrictions", "person_relations", "person_semantic_memory",
    "persona_portraits", "persona_intents", "persona_state_factors", "growth", "growth_versions",
    "group_summary_topics", "group_summary_versions", "skill_controls", "skill_usage", "knowledge_entries", "options"}
ARCHIVE_INDEX_TABLES.update({"person_semantic_versions", "person_semantic_evidence", "persona_sources"})
ARCHIVE_INDEX_TABLES.add("skill_audit_entries")
COUNTERS = {"daily_counts": {"message_count"}, "member_totals": {"total_count"},
            "today_wife_activity_counts": {"message_count"}, "mini_game_stats": {
                "roulette_deaths", "roulette_games", "bomb_deaths", "bomb_passes", "bomb_games",
                "dice_highs", "dice_lows", "dice_games", "guess_wins", "guess_misses", "guess_games"}}


def json_value(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode()}
    return value


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def online_snapshot(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as old:
        with sqlite3.connect(target) as new:
            old.backup(new, pages=256, sleep=.01)
    return target


def table_names(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def inspect_sources(legacy_root: Path):
    result = []
    for kind, relative in SOURCES.items():
        path = legacy_root / relative
        if not path.exists() or path.stat().st_size == 0:
            continue
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            counts = {name: conn.execute(f"SELECT count(*) FROM {quote(name)}").fetchone()[0] for name in table_names(conn)}
        result.append({"kind": kind, "source": relative, "tables": counts, "bytes": path.stat().st_size})
    return result


def import_snapshot(legacy_root: Path, root: Path, *, apply: bool = False, snapshot_id: str | None = None):
    legacy_root, root = legacy_root.resolve(), root.resolve()
    if snapshot_id is not None:
        if len(snapshot_id) != 32 or any(c not in "0123456789abcdef" for c in snapshot_id):
            raise ValueError("snapshot-id 必须是已存在的快照编号")
        listing = [{"kind": kind, "source": relative} for kind, relative in SOURCES.items()
                   if (root / "data" / "imports" / snapshot_id / (kind + ".db")).exists()]
        if not listing:
            raise ValueError("该批一致性快照不存在")
    else:
        listing = inspect_sources(legacy_root)
    if not apply:
        return {"status": "preview", "sources": listing, "writes_to_legacy": 0}
    store = Store(root)
    if store.get_setting("runtime_live", False):
        raise ValueError("导入期间请使用观察模式，避免新业务写入竞争")
    now, run_id = time.time(), snapshot_id or uuid.uuid4().hex
    report = {"status": "imported", "id": run_id, "sources": [], "writes_to_legacy": 0, "pending_replayed": 0}
    with store.connect() as conn:
        conn.executescript("""CREATE TABLE IF NOT EXISTS legacy_rows(
          origin TEXT,table_name TEXT,row_key TEXT,source_hash TEXT,data TEXT,imported_at REAL,
          PRIMARY KEY(origin,table_name,row_key));
          CREATE INDEX IF NOT EXISTS legacy_rows_user ON legacy_rows(table_name,json_extract(data,'$.user_id'));
          CREATE INDEX IF NOT EXISTS legacy_rows_evidence ON legacy_rows(table_name,json_extract(data,'$.memory_id'));
          """)
    for item in listing:
        kind, origin = item["kind"], item["source"]
        snapshot = root / "data" / "imports" / run_id / (kind + ".db")
        if snapshot_id is None:
            online_snapshot(legacy_root / origin, snapshot)
        imported = 0
        with sqlite3.connect(snapshot) as source, store.connect() as output:
            source.row_factory = sqlite3.Row
            for table in table_names(source):
                # Large logs and media stay indexed in the consistent source snapshot.
                if table not in ARCHIVE_INDEX_TABLES:
                    continue
                info = source.execute(f"PRAGMA table_info({quote(table)})").fetchall()
                keys = [row["name"] for row in sorted(info, key=lambda r: r["pk"]) if row["pk"]]
                for index, row in enumerate(source.execute(f"SELECT * FROM {quote(table)}")):
                    value = {k: json_value(row[k]) for k in row.keys()}
                    key = encode([value[k] for k in keys]) if keys else str(index)
                    raw = encode(value)
                    digest = hashlib.sha256(raw.encode()).hexdigest()
                    output.execute("INSERT INTO legacy_rows VALUES(?,?,?,?,?,?) ON CONFLICT(origin,table_name,row_key) DO UPDATE SET source_hash=excluded.source_hash,data=excluded.data,imported_at=excluded.imported_at",
                        (origin, table, key, digest, raw, now))
                    imported += 1
                output.execute("DELETE FROM legacy_rows WHERE origin=? AND table_name=? AND imported_at<>?", (origin, table, now))
        if kind in TARGETS:
            changes = merge_business(snapshot, root / "runtime" / TARGETS[kind], origin)
        else:
            changes = {"archived": imported}
        with snapshot.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        store.record_import(origin + ":" + run_id, digest, {"snapshot": snapshot.relative_to(root).as_posix(), **changes})
        with store.connect() as conn:
            conn.execute("UPDATE imports SET source_hash=?,result=? WHERE origin=?", (digest, encode({"snapshot": snapshot.relative_to(root).as_posix(), **changes}), origin + ":" + run_id))
        report["sources"].append({"kind": kind, "rows": imported, **changes})
        if kind in {"history", "tangtang"}:
            report["sources"][-1]["chat"] = import_chat_history(store, snapshot, origin)
            import_personal_facts(store, snapshot, origin)
        if kind == "persona":
            import_persona_state(store, snapshot)
        store.set_setting("legacy_snapshot:" + kind, snapshot.relative_to(root).as_posix())
    store.set_setting("import_cutoff", now)
    store.set_setting("profile_worker_enabled", False)
    store.set_setting("speech_profile_ai_enabled", False)
    store.set_setting("last_import", report)
    return report


def merge_business(source_path: Path, target_path: Path, origin: str):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    # Target schemas are created by new business constructors, never legacy constructors.
    from .business.db import Database
    from .business.tangtang_db import TangtangDb
    from .business.knowledge_db import KnowledgeDb
    if target_path.name == "business.db":
        business = Database(target_path)
        from .corrections import SkillCorrectionLedger
        SkillCorrectionLedger(business)
    elif target_path.name == "business-history.db":
        history = TangtangDb(target_path)
        with history._connect():
            pass
    elif target_path.name == "knowledge.db":
        db = KnowledgeDb(target_path)
        with db._connect():
            pass
    with sqlite3.connect(source_path) as source, sqlite3.connect(target_path) as dest:
        source.row_factory = dest.row_factory = sqlite3.Row
        dest.execute("CREATE TABLE IF NOT EXISTS harness_import_rows(origin TEXT,table_name TEXT,row_key TEXT,data TEXT,PRIMARY KEY(origin,table_name,row_key))")
        overlap = reconcile_observed_counts(source, dest, target_path, origin)
        target_tables = table_names(dest)
        changed, skipped, collisions = 0, 0, 0
        collision_tables = defaultdict(int)
        for table in table_names(source):
            if table in {"schema_migrations", "chat_gate_revisions"}:
                continue
            if origin == SOURCES["skill_audit"] and table != "skill_audit_entries":
                continue
            if origin == SOURCES["tangtang"] and table != "tangtang_group_messages":
                continue
            if table not in target_tables:
                if target_path.name == "gallery.db":
                    schema = source.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                    dest.execute(schema)
                else:
                    continue
            if any(word in table for word in ("compaction_jobs", "proactive_state", "claims", "expression_events", "persona_profile_jobs")):
                continue
            info = dest.execute(f"PRAGMA table_info({quote(table)})").fetchall()
            fields = [r["name"] for r in info]
            keys = [r["name"] for r in sorted(info, key=lambda r: r["pk"]) if r["pk"]]
            if table == "tangtang_group_messages":
                keys = ["group_id", "message_id"]
            if not keys:
                continue
            seen = set()
            for row in source.execute(f"SELECT * FROM {quote(table)}"):
                data = {k: row[k] for k in row.keys() if k in fields}
                if table == "tangtang_group_messages":
                    data.pop("id", None)
                if table == "mini_game_sessions" and data.get("status") == "active":
                    data["status"] = "cancelled"
                if table in {"ranking_deliveries", "hourly_announcement_deliveries"} and data.get("status") != "sent":
                    data["status"] = "archived"
                key = encode([data[k] for k in keys])
                seen.add(key)
                previous = dest.execute("SELECT data FROM harness_import_rows WHERE origin=? AND table_name=? AND row_key=?", (origin, table, key)).fetchone()
                old = json.loads(previous[0]) if previous else None
                if old == data:
                    skipped += 1
                    continue
                condition = " AND ".join(quote(k) + "=?" for k in keys)
                current = dest.execute(f"SELECT * FROM {quote(table)} WHERE {condition}", tuple(data[k] for k in keys)).fetchone()
                shared_event = current is not None and old is None and table in {"event_dedup", "today_wife_activity_events", "tangtang_group_messages"}
                if current is not None and old is None and dict(current) != data and not shared_event and table not in COUNTERS and table not in {"group_domains", "managed_groups", "group_features", "passive_settings", "knowledge_entries"}:
                    collisions += 1
                    collision_tables[table] += 1
                    continue
                merged = dict(data)
                if shared_event:
                    merged = {k: current[k] for k in data}
                if current is not None and table in COUNTERS:
                    for column in COUNTERS[table]:
                        if column in data:
                            merged[column] = current[column] + data[column] - (old[column] if old else 0)
                columns = list(merged)
                update = ",".join(quote(k) + "=excluded." + quote(k) for k in columns if k not in keys)
                statement = f"INSERT INTO {quote(table)} ({','.join(quote(k) for k in columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT({','.join(quote(k) for k in keys)}) "
                statement += "DO UPDATE SET " + update if update else "DO NOTHING"
                dest.execute(statement, tuple(merged[k] for k in columns))
                dest.execute("INSERT INTO harness_import_rows VALUES(?,?,?,?) ON CONFLICT(origin,table_name,row_key) DO UPDATE SET data=excluded.data", (origin, table, key, encode(data)))
                changed += 1
            for oldrow in dest.execute("SELECT row_key,data FROM harness_import_rows WHERE origin=? AND table_name=?", (origin, table)).fetchall():
                if oldrow['row_key'] in seen:
                    continue
                prior = json.loads(oldrow['data'])
                condition = " AND ".join(quote(k) + "=?" for k in keys)
                args = tuple(prior[k] for k in keys)
                current = dest.execute(f"SELECT * FROM {quote(table)} WHERE {condition}", args).fetchone()
                if current is not None and all(current[k] == v for k, v in prior.items()):
                    dest.execute(f"DELETE FROM {quote(table)} WHERE {condition}", args)
                elif current is not None and table in COUNTERS:
                    values = {k: max(0, current[k] - prior[k]) for k in COUNTERS[table] if k in prior}
                    if values:
                        dest.execute(f"UPDATE {quote(table)} SET {','.join(quote(k)+'=?' for k in values)} WHERE {condition}", (*values.values(), *args))
                dest.execute("DELETE FROM harness_import_rows WHERE origin=? AND table_name=? AND row_key=?", (origin, table, oldrow['row_key']))
    return {"changed": changed, "unchanged": skipped, "trial_collisions": collisions, "collision_tables": dict(collision_tables), "observed_overlap": overlap}


def reconcile_observed_counts(source, dest, target_path: Path, origin: str) -> int:
    """A message captured by both connections contributes once after the final snapshot."""
    native = target_path.parent.parent / "data" / "harness.db"
    if target_path.name != "business.db" or origin != SOURCES["business"] or not native.exists():
        return 0
    if "event_dedup" not in table_names(source):
        return 0
    count = 0
    with sqlite3.connect(native.resolve().as_uri() + "?mode=ro", uri=True) as events:
        for raw, received in events.execute("SELECT payload,received_at FROM events WHERE json_extract(payload,'$.group_id') IS NOT NULL"):
            event = json.loads(raw)
            group, user = int(event['group_id']), int(event['user_id'])
            event_id = f"{group}:{event['event_id']}"
            key = encode([event_id])
            if dest.execute("SELECT 1 FROM harness_import_rows WHERE origin=? AND table_name='event_dedup' AND row_key=?", (origin, key)).fetchone():
                continue
            if not source.execute("SELECT 1 FROM event_dedup WHERE event_id=?", (event_id,)).fetchone() or not dest.execute("SELECT 1 FROM event_dedup WHERE event_id=?", (event_id,)).fetchone():
                continue
            day = datetime.fromtimestamp(event.get('timestamp') or received, ZoneInfo('Asia/Shanghai')).date().isoformat()
            dest.execute("UPDATE daily_counts SET message_count=message_count-1 WHERE group_id=? AND day=? AND user_id=? AND message_count>0", (group, day, user))
            if source.execute("SELECT 1 FROM today_wife_activity_events WHERE event_id=?", (event_id,)).fetchone() and dest.execute("SELECT 1 FROM today_wife_activity_events WHERE event_id=?", (event_id,)).fetchone():
                dest.execute("UPDATE today_wife_activity_counts SET message_count=message_count-1 WHERE group_id=? AND day=? AND user_id=? AND message_count>0", (group, day, user))
            count += 1
    return count


def import_chat_history(store: Store, snapshot: Path, origin: str):
    with sqlite3.connect(snapshot) as source, store.connect() as output:
        source.row_factory = sqlite3.Row
        names = table_names(source)
        if "chat_context_turns" not in names:
            return 0
        sessions = {row["id"]: dict(row) for row in source.execute("SELECT * FROM chat_context_sessions")}
        groups = defaultdict(list)
        for row in source.execute("SELECT * FROM chat_context_turns WHERE delivery_status='confirmed' ORDER BY id"):
            groups[(row["session_id"], row["request_id"])].append(dict(row))
        count = 0
        for (session_id, request_id), rows in groups.items():
            session = sessions[session_id]
            key = f"group:{session['group_id']}" if session["group_id"] else f"private:{session['user_id']}"
            event_key = "import:" + origin + ":" + request_id
            messages, user_contents = [], []
            for row in rows:
                payload = json.loads(row["payload_json"])
                content = payload.get("content", "") if isinstance(payload, dict) else payload
                if row["role"] == "user":
                    if isinstance(content, list):
                        for part in content:
                            image = part.get("image_url", {}) if part.get("type") == "image_url" else None
                            if image and str(image.get("url", "")).startswith("vision:"):
                                saved = source.execute("SELECT data_url FROM chat_context_images WHERE digest=?", (image["url"][7:],)).fetchone()
                                if saved is None:
                                    raise ValueError("历史轮次的图片引用缺少来源图片")
                                image["url"] = saved[0]
                            if image and str(image.get("url", "")).startswith("data:"):
                                header, encoded = image["url"].split(",", 1)
                                raw = base64.b64decode(encoded)
                                digest = hashlib.sha256(raw).hexdigest()
                                output.execute("INSERT OR IGNORE INTO assets VALUES(?,?,?,?)", (digest, header[5:].split(";", 1)[0], raw, time.time()))
                    user_contents.append(content)
                elif row["role"] == "assistant" and isinstance(content, str):
                    messages.append(content)
            if not messages:
                continue
            if len(user_contents) == 1:
                user_content = user_contents[0]
            elif any(isinstance(content, list) for content in user_contents):
                user_content = [part for content in user_contents for part in (content if isinstance(content, list) else [{"type": "text", "text": content}])]
            else:
                user_content = "\n\n".join(user_contents)
            try:
                at = datetime.fromisoformat(rows[-1]["created_at"]).timestamp()
            except ValueError:
                at = time.time()
            output.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,0)", (key, at))
            result = output.execute("INSERT INTO turns(session_key,event_key,request_id,user_content,messages,message_ids,status,created_at) VALUES(?,?,?,?,?,?,'delivered',?) ON CONFLICT(event_key) DO UPDATE SET user_content=excluded.user_content,messages=excluded.messages",
                                   (key, event_key, "legacy:" + request_id, encode(user_content), encode(messages), "[]", at))
            count += result.rowcount
    return count


def import_personal_facts(store, snapshot, origin):
    with sqlite3.connect(snapshot) as source:
        source.row_factory = sqlite3.Row
        names = table_names(source)
        for table in ("person_facts", "tangtang_memories"):
            if table not in names:
                continue
            for row in source.execute(f"SELECT * FROM {quote(table)}"):
                data = dict(row)
                store.import_legacy_fact(data, origin, table)


def import_persona_state(store, snapshot):
    with sqlite3.connect(snapshot) as source:
        source.row_factory = sqlite3.Row
        names = table_names(source)
        if "options" in names:
            values = {row["key"]: json.loads(row["value"]) for row in source.execute("SELECT * FROM options")}
            store.set_setting("legacy_persona_options", values)
        if "growth" in names:
            for row in source.execute("SELECT * FROM growth WHERE persona='denia'"):
                value = dict(row)
                store.set_setting("growth_entry:" + str(value["id"]), value)
    store.import_legacy_growth(snapshot)
