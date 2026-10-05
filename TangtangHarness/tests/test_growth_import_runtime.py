# Explicit legacy-layout compatibility contracts; new defaults are tested in test_cache_spine.py.
import hashlib
import json
import sqlite3

from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.context import build_context
from tangtang_harness.store import Store, encode
from tangtang_harness.types import InboundEvent
from tangtang_harness.business.db import Database


def snapshot(tmp_path):
    path = tmp_path / 'legacy-state.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''CREATE TABLE growth(id INTEGER PRIMARY KEY,persona TEXT,group_id INTEGER,topic TEXT,content TEXT,version INTEGER,enabled INTEGER);
            CREATE TABLE growth_versions(entry_id INTEGER,version INTEGER,content TEXT,evidence_ids TEXT,created_at REAL);
            CREATE TABLE evidence(id INTEGER PRIMARY KEY,user_id INTEGER,group_id INTEGER,source TEXT,reply TEXT);
            CREATE TABLE growth_hidden(group_id INTEGER,entry_id INTEGER);''')
        conn.executemany('INSERT INTO growth VALUES(?,?,?,?,?,?,?)', [(1, 'denia', 0, '表达', '新表达', 2, 0),
            (2, 'denia', 201, '话题', '本群话题', 1, 1), (3, 'tangtang', 0, '旧人格', '不迁移内容', 1, 1)])
        conn.executemany('INSERT INTO growth_versions VALUES(?,?,?,?,?)', [(1, 1, '旧表达', '[11]', 100),
            (1, 2, '新表达', '[11]', 101), (2, 1, '本群话题', '[12]', 102)])
        conn.executemany('INSERT INTO evidence VALUES(?,?,?,?,?)', [(11, 101, 301, '别群原始私人证据', '原回答'),
            (12, 102, 201, '本群原始证据', '原回答')])
        conn.execute('INSERT INTO growth_hidden VALUES(201,1)')
    return path


def test_growth_import_preserves_versions_evidence_disablement_and_new_edits(tmp_path):
    source = snapshot(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    store = Store(tmp_path / 'TangtangHarness')
    result = store.import_legacy_growth(source)
    assert result == {'imported': 2, 'existing': 0, 'versions': 3}
    rows = store.growth('group:201', include_disabled=True)
    global_row = next(row for row in rows if row['id'] == 1)
    assert global_row['status'] == 'disabled' and global_row['version'] == 2
    assert global_row['provenance']['evidence'][0]['source'] == '别群原始私人证据'
    assert store.get_setting('growth_disabled:group:201') == [1]
    rolled = store.change_growth(1, rollback_version=1)
    assert rolled['content'] == '旧表达' and rolled['version'] == 3
    assert store.import_legacy_growth(source) == {'imported': 0, 'existing': 2, 'versions': 0}
    assert next(row for row in store.growth('group:201') if row['id'] == 1)['version'] == 3
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_growth_context_uses_only_public_text_and_checks_imported_evidence_authors(tmp_path):
    store = Store(tmp_path / 'TangtangHarness')
    store.import_legacy_growth(snapshot(tmp_path))
    profile = ModelProfile('test', 'Test', 'custom', 'synthetic', 'https://example.invalid/v1')
    config = HarnessConfig(root=store.root, profiles=(profile,), active_model='test', extra={'context_mode': 'legacy'})
    current = InboundEvent('now', 999, 103, 201, '聊聊这个话题')
    built = build_context(config, store, current, profile)
    assert '本群话题' in encode(built.messages) and '本群原始证据' not in encode(built.messages)
    db = Database(store.root / 'runtime' / 'business.db')
    db.seed_groups((201,))
    db.set_group_feature(201, 'persona_growth', True)
    db.add_filter_members('active', (102,), 101)
    built = build_context(config, store, current, profile)
    assert '本群话题' not in encode(built.messages)


def test_changed_source_refreshes_same_record_and_keeps_each_native_version(tmp_path):
    source = snapshot(tmp_path)
    store = Store(tmp_path / 'TangtangHarness')
    store.import_legacy_growth(source)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE growth SET content='第三版表达',version=3,enabled=1 WHERE id=1")
        conn.execute("INSERT INTO evidence VALUES(13,103,201,'第三版原始证据','第三版回答')")
        conn.execute("INSERT INTO growth_versions VALUES(1,3,'第三版表达','[13]',103)")
    updated = store.import_legacy_growth(source)
    assert updated == {'imported': 0, 'existing': 2, 'versions': 1}
    current = next(row for row in store.growth('group:201') if row['id'] == 1)
    assert current['content'] == '第三版表达' and current['version'] == 3
    assert current['provenance']['native_versions']['2']['evidence'][0]['source'] == '别群原始私人证据'
    assert current['provenance']['native_versions']['3']['evidence'][0]['source'] == '第三版原始证据'
    assert store.import_legacy_growth(source)['versions'] == 0
    with sqlite3.connect(source) as conn:
        conn.execute('UPDATE growth SET enabled=0 WHERE id=1')
    assert store.import_legacy_growth(source)['versions'] == 1
    current = next(row for row in store.growth('group:201', include_disabled=True) if row['id'] == 1)
    assert current['content'] == '第三版表达' and current['version'] == 4 and current['status'] == 'disabled'
    assert store.import_legacy_growth(source)['versions'] == 0


def test_native_growth_edit_survives_later_source_content_and_status_changes(tmp_path):
    source = snapshot(tmp_path)
    store = Store(tmp_path / 'TangtangHarness')
    store.import_legacy_growth(source)
    store.change_growth(1, rollback_version=1)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE growth SET content='旧框架第三版',version=3,enabled=0 WHERE id=1")
        conn.execute("INSERT INTO growth_versions VALUES(1,3,'旧框架第三版','[11]',103)")
    assert store.import_legacy_growth(source)['versions'] == 0
    current = next(row for row in store.growth('group:201', include_disabled=True) if row['id'] == 1)
    assert current['content'] == '旧表达' and current['status'] == 'active' and current['version'] == 3
    assert '3' not in current['provenance']['native_versions']


def test_source_hidden_changes_sync_without_overwriting_trial_group_choices(tmp_path):
    source = snapshot(tmp_path)
    store = Store(tmp_path / 'TangtangHarness')
    store.import_legacy_growth(source)
    store.set_setting('growth_disabled:group:301', [1])
    store.set_setting('growth_disabled:group:401', [1])
    store.set_setting('growth_disabled:group:401', [])
    with sqlite3.connect(source) as conn:
        conn.execute('DELETE FROM growth_hidden WHERE group_id=201')
        conn.executemany('INSERT INTO growth_hidden VALUES(?,1)', [(202,), (301,), (401,)])
    assert store.import_legacy_growth(source)['versions'] == 0
    assert store.get_setting('growth_disabled:group:201') == []
    assert store.get_setting('growth_disabled:group:202') == [1]
    assert store.get_setting('growth_disabled:group:301') == [1]
    assert store.get_setting('growth_disabled:group:401') == []
    with sqlite3.connect(source) as conn:
        conn.execute('DELETE FROM growth_hidden')
    assert store.import_legacy_growth(source)['versions'] == 0
    assert store.get_setting('growth_disabled:group:202') == []
    assert store.get_setting('growth_disabled:group:301') == [1]
    assert store.get_setting('growth_disabled:group:401') == []


def test_row_hash_prevents_unrelated_file_changes_from_creating_versions(tmp_path):
    source = snapshot(tmp_path)
    store = Store(tmp_path / 'TangtangHarness')
    store.import_legacy_growth(source)
    mappings = {key: value for key, value in store.settings().items() if key.startswith('legacy_growth_id:')}
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE growth SET content='非达妮娅更新' WHERE id=3")
    assert store.import_legacy_growth(source)['versions'] == 0
    assert mappings == {key: value for key, value in store.settings().items() if key.startswith('legacy_growth_id:')}
