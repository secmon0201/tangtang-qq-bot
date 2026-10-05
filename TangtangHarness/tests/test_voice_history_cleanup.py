import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from tangtang_harness.message_text import VOICE_MARKER
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "normalize_voice_history.py"
spec = importlib.util.spec_from_file_location("normalize_voice_history", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
cleanup_voice_history = module.cleanup_voice_history

AUDIO = "base64://" + "synthetic-audio" * 1000
IMAGE = "base64://synthetic-image"


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def seed(root):
    store = Store(root)
    event = InboundEvent("1", 999, 101, 201, "")
    store.append_event(event)
    payload = event.to_dict()
    payload["segments"] = [{"type": "record", "data": {"file": AUDIO, "text": "旧识别文本"}}]
    image = str([{"type": "image", "data": {"file": IMAGE}}])
    with store.connect() as conn:
        conn.execute("UPDATE events SET payload=?", (encode(payload),))
        deliveries = [
            (1, "voice", "group:201", "trigger-voice", "delivered", ["71"], ["[record]"]),
            (2, "voice", "group:201", "trigger-voice", "delivered", ["71"], ["实际只用语音说出的旧生成内容"]),
            (3, "voice", "group:201", "trigger-fallback", "delivered", ["72"], ["另外实际发出的文字"]),
            (4, "failed", "group:201", "trigger-failed", "failed", [], ["[record]"]),
            (5, "failed", "group:201", "trigger-fallback", "delivered", ["73"], ["语音失败后的文字回退"]),
            (6, "image", "group:201", "trigger-image", "delivered", ["74"], [image]),
            (7, "voice", "group:202", "other-session", "delivered", ["71"], ["另一个群同请求号的文字"]),
            (8, "serialized", "group:201", "serialized-trigger", "delivered", ["75"],
             [str([{"type": "record", "data": {"file": AUDIO}}])]),
            (9, "no-receipt", "group:201", "no-receipt-trigger", "delivered", [], ["[record]"]),
        ]
        for row in deliveries:
            conn.execute("INSERT INTO deliveries(id,request_id,session_key,event_key,outcome,message_ids,messages,error,created_at) VALUES(?,?,?,?,?,?,?,'',1)",
                         (*row[:5], encode(row[5]), encode(row[6])))
        turns = [
            (1, "group:201", "trigger-voice", "voice", ["71"], ["实际只用语音说出的旧生成内容"],
             [{"type": "text", "text": f"前面[CQ:record,file={AUDIO}]后面"},
              {"type": "image", "data": {"file": IMAGE}}]),
            (2, "group:201", "trigger-fallback", "voice", ["72"], ["另外实际发出的文字"], "当前用户普通发言"),
            (3, "group:201", "trigger-failed", "failed", ["73"], ["语音失败后的文字回退"], "普通用户内容"),
            (4, "group:202", "other-session", "voice", ["71"], ["另一个群同请求号的文字"], "另一个群用户内容"),
            (5, "group:201", "serialized-trigger", "serialized", ["75"], ["序列化语音原来的文字"], "普通用户内容"),
            (6, "group:201", "no-receipt-trigger", "no-receipt", [], ["没有真实语音回执的文字"], "普通用户内容"),
        ]
        for row in turns:
            conn.execute("INSERT INTO turns(id,session_key,event_key,request_id,message_ids,messages,user_content,status,created_at) VALUES(?,?,?,?,?,?,?,'delivered',1)",
                         (*row[:4], encode(row[4]), encode(row[5]), encode(row[6])))
        conn.execute("INSERT INTO background_jobs VALUES('job','summary','group:201','queued',?,'{}',1,1)",
                     (encode({"events": [payload], "image": {"type": "image", "data": {"file": IMAGE}}}),))
        conn.execute("INSERT INTO snapshots VALUES(1,'group:201',1,1,?,1)",
                     (encode({"summary": f"前面[CQ:record,file={AUDIO}]后面"}),))
        conn.execute("INSERT INTO summary_versions VALUES('group:201',1,?,1,'[]','job',1)",
                     (encode({"summary": str([{"type": "audio", "data": {"file": AUDIO}}])}),))
        conn.execute("INSERT INTO settings VALUES('summary',?)", (encode({"text": "[record]"}),))
        conn.execute("INSERT INTO settings VALUES('ordinary',?)", ('{ "text": "record audio ordinary", "image": "base64://synthetic-image" }',))
        conn.execute("INSERT INTO requests(id,session_key,event_key,profile_id,provider,model,api_style,purpose,payload,outcome,started_at) VALUES('archived','group:201','trigger-voice','p','provider','model','chat','chat',?,'completed',1)",
                     (encode({"messages": [{"content": f"[CQ:record,file={AUDIO}]"}]}),))
    return store


def snapshot(root):
    with sqlite3.connect(root / "data" / "harness.db") as conn:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("events", "deliveries", "turns", "background_jobs", "snapshots", "summary_versions", "settings", "requests")}


def test_dry_run_preserves_database_and_creates_no_backup(tmp_path):
    seed(tmp_path)
    before = snapshot(tmp_path)
    database_bytes = (tmp_path / "data" / "harness.db").read_bytes()

    result = cleanup_voice_history(tmp_path)

    assert result["mode"] == "dry_run" and result["rows"] > 0 and result["chars_removed"] > len(AUDIO)
    assert "backup" not in result
    assert snapshot(tmp_path) == before
    assert (tmp_path / "data" / "harness.db").read_bytes() == database_bytes
    assert not list((tmp_path / "runtime" / "verification").glob("voice-history-*.jsonl"))


def test_apply_only_voice_histories_preserves_receipts_fallback_images_and_archives(tmp_path):
    seed(tmp_path)
    before = snapshot(tmp_path)

    result = cleanup_voice_history(tmp_path, apply=True)

    with sqlite3.connect(tmp_path / "data" / "harness.db") as conn:
        event = json.loads(conn.execute("SELECT payload FROM events").fetchone()[0])
        assert event["text"] == VOICE_MARKER and event["segments"] == [{"type": "record", "data": {}}]
        assert conn.execute("SELECT message_ids FROM deliveries WHERE id=1").fetchone()[0] == before["deliveries"][0][5]
        for table, voice_ids in (("turns", [1, 5]), ("deliveries", [1, 2, 8])):
            for row_id in voice_ids:
                assert json.loads(conn.execute(f"SELECT messages FROM {table} WHERE id=?", (row_id,)).fetchone()[0]) == [VOICE_MARKER]
        content = json.loads(conn.execute("SELECT user_content FROM turns WHERE id=1").fetchone()[0])
        assert content[0]["text"] == "前面[语音]后面" and content[1]["data"]["file"] == IMAGE
        for table, unchanged_ids, message_index in (("turns", [2, 3, 4, 6], 5), ("deliveries", [3, 5, 6, 7], 6)):
            for row_id in unchanged_ids:
                assert conn.execute(f"SELECT messages FROM {table} WHERE id=?", (row_id,)).fetchone()[0] == before[table][row_id - 1][message_index]
        ordinary_before = next(row for row in before["settings"] if row[0] == "ordinary")
        assert conn.execute("SELECT value FROM settings WHERE key='ordinary'").fetchone()[0] == ordinary_before[1]
        assert conn.execute("SELECT payload FROM requests").fetchone()[0] == before["requests"][0][9]
        for table, column in (("background_jobs", "source"), ("snapshots", "content"), ("summary_versions", "content"), ("settings", "value")):
            assert all(AUDIO not in row[0] for row in conn.execute(f"SELECT {column} FROM {table}"))

    after = snapshot(tmp_path)
    backup_path = tmp_path / result["backup"]
    records = [json.loads(line) for line in backup_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["format"] == "voice-history-fields-v1" and records[-1]["committed"] is True
    changes = records[1:-1]
    assert len(changes) == result["fields"]
    assert all(record["table"] != "requests" for record in changes)
    with sqlite3.connect(tmp_path / "data" / "harness.db") as conn:
        for record in changes:
            keys = record["where"]
            clause = " AND ".join(f"{key}=?" for key in keys)
            current = conn.execute(f"SELECT {record['field']} FROM {record['table']} WHERE {clause}", tuple(keys.values())).fetchone()[0]
            assert current == record["after"]
            conn.execute(f"UPDATE {record['table']} SET {record['field']}=? WHERE {clause}", (record["before"], *keys.values()))
    assert snapshot(tmp_path) == before
    assert after != before


def test_cleanup_is_idempotent(tmp_path):
    seed(tmp_path)
    cleanup_voice_history(tmp_path, apply=True)
    before = snapshot(tmp_path)

    result = cleanup_voice_history(tmp_path, apply=True)

    assert result["rows"] == result["fields"] == result["chars_removed"] == 0
    assert "backup" not in result
    assert snapshot(tmp_path) == before


def test_failure_rolls_back_all_changes(tmp_path):
    store = seed(tmp_path)
    with store.connect() as conn:
        conn.execute("INSERT INTO settings VALUES('invalid-json','invalid')")
    before = snapshot(tmp_path)

    with pytest.raises(json.JSONDecodeError):
        cleanup_voice_history(tmp_path, apply=True)

    assert snapshot(tmp_path) == before
    backup_paths = list((tmp_path / "runtime" / "verification").glob("voice-history-*.jsonl"))
    assert len(backup_paths) == 1
    assert not any(json.loads(line).get("committed") for line in backup_paths[0].read_text(encoding="utf-8").splitlines())
