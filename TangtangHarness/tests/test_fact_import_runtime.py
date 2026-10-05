import sqlite3
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


def legacy(tmp_path):
    path = tmp_path / 'synthetic-legacy.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''CREATE TABLE person_facts(
            id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,scope_group INTEGER NOT NULL,
            content TEXT NOT NULL,normalized TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,
            importance REAL NOT NULL,confidence REAL NOT NULL,updated_at TEXT NOT NULL,
            UNIQUE(user_id,scope_group,normalized));
            CREATE TABLE tangtang_memories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,group_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,content TEXT NOT NULL,normalized_content TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('candidate','active','archived','deleted')),
            importance REAL NOT NULL DEFAULT .5,confidence REAL NOT NULL DEFAULT .5,evidence_count INTEGER NOT NULL DEFAULT 1,
            source_message_id TEXT NOT NULL DEFAULT '',source_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL,expires_at TEXT,deleted_at TEXT,
            UNIQUE(group_id,user_id,kind,source_hash));''')
        conn.execute('INSERT INTO person_facts VALUES(1,101,201,?,?,?,?,?,?,?)',
            ('喜欢画画', '喜欢画画', 'preference', 'active', .8, .9, '2026-01-01'))
        conn.execute('''INSERT INTO tangtang_memories(group_id,user_id,kind,content,normalized_content,status,source_message_id,source_hash,created_at,updated_at)
            VALUES(201,101,'preference','喜欢音乐','喜欢音乐','active','message-1','source-1','2026-01-01','2026-01-01')''')
    return path


def row(path, table):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute('SELECT * FROM ' + table + ' WHERE id=1').fetchone())


def versions(store, memory_id):
    with store.connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM memory_versions WHERE memory_id=? ORDER BY version', (memory_id,))]


def test_same_source_updates_one_record_without_adding_versions_on_repeat(tmp_path):
    source = legacy(tmp_path)
    store = Store(tmp_path / 'TangtangHarness')
    initial = store.import_legacy_fact(row(source, 'person_facts'), 'data/personas/history.db', 'person_facts')
    assert initial['status'] == 'imported' and initial['version'] == 1
    assert store.import_legacy_fact(row(source, 'person_facts'), 'data/personas/history.db', 'person_facts')['status'] == 'unchanged'
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE person_facts SET content='现在喜欢摄影',normalized='现在喜欢摄影',updated_at='2026-01-02' WHERE id=1")
    updated = store.import_legacy_fact(row(source, 'person_facts'), 'data/personas/history.db', 'person_facts')
    assert updated == {'status': 'updated', 'memory_id': initial['memory_id'], 'version': 2}
    memories = store.memories('group:201', 101, include_forgotten=True)
    assert len(memories) == 1 and memories[0]['content'] == '现在喜欢摄影'
    assert [item['content'] for item in versions(store, initial['memory_id'])] == ['喜欢画画', '现在喜欢摄影']


def test_native_correction_and_forgetting_survive_source_changes_and_reimport(tmp_path):
    source = legacy(tmp_path)
    store = Store(tmp_path / 'TangtangHarness')
    data = row(source, 'person_facts')
    result = store.import_legacy_fact(data, 'old/history.db', 'person_facts')
    event = InboundEvent('correction', 999, 101, 201, '已经改成喜欢写作了')
    store.correct_memory(event, result['memory_id'], '喜欢写作')
    assert store.import_legacy_fact(data, 'old/history.db', 'person_facts')['status'] == 'preserved'
    assert store.import_legacy_fact({**data, 'content': '旧来源的新内容'}, 'old/history.db', 'person_facts')['status'] == 'preserved'
    store.forget('group:201', 101, '喜欢写作')
    assert store.import_legacy_fact(data, 'old/history.db', 'person_facts')['status'] == 'preserved'
    memories = store.memories('group:201', 101, include_forgotten=True)
    assert len(memories) == 1 and memories[0]['status'] == 'forgotten' and memories[0]['content'] == '喜欢写作'
    assert len(versions(store, result['memory_id'])) == 3


def test_source_inactive_closes_memory_and_evidence_versions_are_retained(tmp_path):
    source = legacy(tmp_path)
    store = Store(tmp_path / 'TangtangHarness')
    data = row(source, 'tangtang_memories')
    first = store.import_legacy_fact(data, 'old/tangtang.db', 'tangtang_memories')
    new_evidence = {**data, 'source_message_id': 'message-2', 'source_hash': 'source-2'}
    second = store.import_legacy_fact(new_evidence, 'old/tangtang.db', 'tangtang_memories')
    assert second['version'] == 2 and second['memory_id'] == first['memory_id']
    forgotten = store.import_legacy_fact({**new_evidence, 'status': 'deleted'}, 'old/tangtang.db', 'tangtang_memories')
    assert forgotten['version'] == 3 and not store.memories('group:201', 101)
    assert store.get_setting(f"legacy_fact_version:{first['memory_id']}:1")['data']['source_message_id'] == 'message-1'
    assert store.get_setting(f"legacy_fact_version:{first['memory_id']}:2")['data']['source_message_id'] == 'message-2'
    assert versions(store, first['memory_id'])[-1]['status'] == 'forgotten'
    restored = store.import_legacy_fact(new_evidence, 'old/tangtang.db', 'tangtang_memories')
    assert restored['version'] == 4 and store.memories('group:201', 101)


def test_pre_mapping_corrected_or_forgotten_records_are_adopted_without_recreating_old_fact(tmp_path):
    source = legacy(tmp_path)
    data = row(source, 'person_facts')
    for action in ('correct', 'forget'):
        store = Store(tmp_path / action / 'TangtangHarness')
        memory_id = store.remember('group:201', 101, data['content'], event_key='import:old/history.db:person_facts:1', quote=data['content'])
        if action == 'correct':
            current = InboundEvent('edit', 999, 101, 201, '如今喜欢写作')
            store.correct_memory(current, memory_id, '喜欢写作')
        else:
            store.forget('group:201', 101, '画画')
        assert store.import_legacy_fact(data, 'old/history.db', 'person_facts')['status'] == 'preserved'
        assert len(store.memories('group:201', 101, include_forgotten=True)) == 1
        assert len(versions(store, memory_id)) == 2


def test_adoption_retires_duplicates_left_by_first_importer(tmp_path):
    source = legacy(tmp_path)
    data = row(source, 'person_facts')
    store = Store(tmp_path / 'TangtangHarness')
    event_key = 'import:old/history.db:person_facts:1'
    stale_id = store.remember('group:201', 101, '过时喜好', event_key=event_key, quote='过时喜好')
    current_id = store.remember('group:201', 101, data['content'], event_key=event_key, quote=data['content'], kind=data['kind'])
    adopted = store.import_legacy_fact(data, 'old/history.db', 'person_facts')
    assert adopted['memory_id'] == current_id and adopted['version'] == 1
    assert [item['id'] for item in store.memories('group:201', 101)] == [current_id]
    assert versions(store, stale_id)[-1]['status'] == 'forgotten'


def test_scope_and_origin_are_part_of_source_identity(tmp_path):
    source = legacy(tmp_path)
    data = row(source, 'person_facts')
    store = Store(tmp_path / 'TangtangHarness')
    first = store.import_legacy_fact(data, 'old/denia.db', 'person_facts')
    second = store.import_legacy_fact({**data, 'scope_group': 0, 'content': '私聊资料'}, 'old/tangtang.db', 'person_facts')
    assert first['memory_id'] != second['memory_id']
    assert store.memories('private:101', 101)[0]['content'] == '私聊资料'
