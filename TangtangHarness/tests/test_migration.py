import sqlite3
import base64
import json

from tangtang_harness.business.db import Database
from tangtang_harness.migration import merge_business, online_snapshot, import_snapshot
from tangtang_harness.business.tangtang_db import TangtangDb
from tangtang_harness.store import Store


def seed(path, count):
    Database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT OR REPLACE INTO daily_counts VALUES(102,'2026-01-01',101,'测试',?,'2026-01-01')", (count,))


def test_online_backup_includes_uncheckpointed_wal(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "snapshot.db"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY,text TEXT)")
        conn.execute("INSERT INTO messages VALUES(1,'测试')")
        conn.commit()
        online_snapshot(source, target)
        with sqlite3.connect(target) as copied:
            assert copied.execute("SELECT text FROM messages").fetchone()[0] == "测试"


def test_version_delta_keeps_trial_counts_and_repeat_import_does_not_accumulate(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "business.db"
    seed(source, 5)
    seed(target, 2)
    merge_business(source, target, "data/bot.db")
    merge_business(source, target, "data/bot.db")
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT message_count FROM daily_counts").fetchone()[0] == 7
    seed(source, 6)
    merge_business(source, target, "data/bot.db")
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT message_count FROM daily_counts").fetchone()[0] == 8
    with sqlite3.connect(source) as conn:
        conn.execute("DELETE FROM daily_counts")
    merge_business(source, target, "data/bot.db")
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT message_count FROM daily_counts").fetchone()[0] == 2


def test_import_preserves_source_and_archives_active_games(tmp_path):
    old, new = tmp_path / "legacy", tmp_path / "harness"
    source = old / "data" / "bot.db"
    seed(source, 5)
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO mini_game_sessions(group_id,game_type,status,creator_id,started_at,ends_at) VALUES(102,'guess','active',101,'2026-01-01','2026-01-02')")
    assert import_snapshot(old, new)["status"] == "preview"
    assert not new.exists()
    result = import_snapshot(old, new, apply=True)
    assert result["pending_replayed"] == result["writes_to_legacy"] == 0
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT status FROM mini_game_sessions").fetchone()[0] == "active"
    with sqlite3.connect(new / "runtime" / "business.db") as conn:
        assert conn.execute("SELECT status FROM mini_game_sessions").fetchone()[0] == "cancelled"


def test_history_schema_initialization_and_frozen_legacy_image(tmp_path):
    old, new = tmp_path / "legacy", tmp_path / "harness"
    path = old / "data" / "personas" / "denia-history.db"
    db = TangtangDb(path)
    with db._connect() as conn:
        conn.execute("INSERT INTO tangtang_group_messages(group_id,user_id,nickname,text,message_id,created_at) VALUES(102,101,'样例','原文',700,'2026-01-01')")
        conn.execute("INSERT INTO chat_context_sessions(id,group_id,user_id,layout_version,persona_version,tool_version,stable_prefix_hash,created_at,updated_at) VALUES(1,102,101,'v','p','t','hash','2026-01-01','2026-01-01')")
        url = 'data:image/png;base64,' + base64.b64encode(b'example image').decode()
        conn.execute("INSERT INTO chat_context_images VALUES('legacy-digest',?)", (url,))
        for index, role, content in [(0,'user',[{'type':'text','text':'看图'},{'type':'image_url','image_url':{'url':'vision:legacy-digest'}}]), (1,'assistant','回复')]:
            conn.execute("INSERT INTO chat_context_turns(session_id,request_id,sequence,role,item_type,payload_json,delivery_status,source_hash,created_at) VALUES(1,'r',?,?, 'message',?,'confirmed','h','2026-01-01')", (index,role,json.dumps({'content':content})))
    result = import_snapshot(old, new, apply=True)
    assert result['sources'][0]['changed'] > 0
    with sqlite3.connect(new / 'runtime' / 'business-history.db') as conn:
        assert conn.execute('SELECT count(*) FROM tangtang_group_messages').fetchone()[0] == 1
    store = Store(new)
    assert store.history('group:102')[0]['user_content'][1]['image_url']['url'] == url
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM assets').fetchone()[0] == 1
