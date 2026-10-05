# Explicit legacy-layout compatibility contracts; new defaults are tested in test_cache_spine.py.
import json
from dataclasses import replace

from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.context import build_context
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


FORGOTTEN = '我住A地'
REDACTED = '[已停用的个人记忆]'


def setup(tmp_path, *, private=False):
    profile = ModelProfile('synthetic', 'Synthetic', 'custom', 'model', 'https://example.invalid/v1')
    config = HarnessConfig(root=tmp_path, profiles=(profile,), active_model=profile.id, extra={'context_mode': 'legacy'})
    store = Store(tmp_path)
    event = InboundEvent('current', 999, 101, None if private else 201, '聊聊近况', timestamp=100)
    store.remember(event.session_key, 102, FORGOTTEN, quote=FORGOTTEN)
    store.forget(event.session_key, 102, FORGOTTEN)
    return config, store, event


def layer_json(context, name):
    layer = next(row for row in context.layers if row['name'] == name)
    assert layer['text'] in context.user_content
    return json.loads(layer['text'].split('\n', 1)[1])


def test_forgotten_quote_keeps_other_memories_and_exact_adopted_sources(tmp_path):
    config, store, event = setup(tmp_path)
    changed_id = store.remember(event.session_key, event.user_id, '我现在住B地',
                               quote='我以前说我住A地，现在住B地')
    other_id = store.remember(event.session_key, event.user_id, '我喜欢散步', quote='我喜欢散步')
    original = store.memories(event.session_key, event.user_id)

    context = build_context(config, store, event, config.profile())
    visible = layer_json(context, 'memory')
    assert {row['id'] for row in visible} == {changed_id, other_id}
    assert next(row for row in visible if row['id'] == changed_id)['quote'] == REDACTED
    assert {row['content'] for row in visible} == {'我现在住B地', '我喜欢散步'}
    assert context.telemetry['sources']['records']['memory'] == [
        {'id': row['id'], 'version': row['version'], 'scope': event.session_key, 'kind': row['kind']}
        for row in visible
    ]
    assert FORGOTTEN not in json.dumps(context.payload, ensure_ascii=False)
    assert context.payload['messages'][-1]['content'] == context.user_content
    assert context.telemetry['estimated_input_tokens'] > 0
    assert store.memories(event.session_key, event.user_id) == original
    assert not store.requests()


def test_fully_hidden_main_content_is_not_indexed_as_adopted(tmp_path):
    config, store, event = setup(tmp_path)
    hidden_memory = store.remember(event.session_key, event.user_id, FORGOTTEN + '，旧自述', quote='旧自述')
    visible_memory = store.remember(event.session_key, event.user_id, '我现在住B地', quote=FORGOTTEN)
    hidden_cognition = store.cognitive_update(event, 'state', '旧状态', FORGOTTEN, 'open', '旧证据')
    visible_cognition = store.cognitive_update(event, 'intent', '旅行', '计划去散步', 'open', FORGOTTEN)
    hidden_growth = store.grow(event, FORGOTTEN + '是旧表达', '旧证据')
    visible_growth = store.grow(event, '先把事情讲清楚', FORGOTTEN)

    context = build_context(config, store, event, config.profile())
    for kind, expected, hidden in (
        ('memory', visible_memory, hidden_memory),
        ('cognition', visible_cognition, hidden_cognition),
        ('growth', visible_growth, hidden_growth),
    ):
        visible = layer_json(context, kind)
        assert [row['id'] for row in visible] == [expected]
        adopted = context.telemetry['sources']['records'][kind]
        assert [row['id'] for row in adopted] == [expected]
        assert [row['version'] for row in adopted] == [row['version'] for row in visible]
        assert hidden not in {row['id'] for row in adopted}
    assert layer_json(context, 'cognition')[0]['quote'] == REDACTED
    assert len(store.memories(event.session_key, event.user_id)) == 2
    assert len(store.cognition(event.session_key, event.user_id)) == 2
    assert len(store.growth(event.session_key)) == 2


def test_partially_hidden_main_content_preserves_visible_lines_and_source(tmp_path):
    config, store, event = setup(tmp_path)
    record_id = store.remember(event.session_key, event.user_id, FORGOTTEN + '\n我现在住B地', quote='新自述')

    context = build_context(config, store, event, config.profile())
    visible = layer_json(context, 'memory')
    assert visible[0]['content'] == REDACTED + '\n我现在住B地'
    assert context.telemetry['sources']['records']['memory'][0]['id'] == record_id
    assert store.memories(event.session_key, event.user_id)[0]['content'].startswith(FORGOTTEN)


def test_profile_quote_redaction_retains_visible_profile_and_skips_empty_profile(tmp_path):
    config, store, event = setup(tmp_path)
    key = 'profile:' + event.session_key + ':' + str(event.user_id)
    original = {'version': 3, 'profile': {'impressions': [
        {'text': '愿意分享新近况', 'quote': FORGOTTEN},
        {'text': FORGOTTEN, 'quote': '曾经的自述'},
    ]}, 'published_at': 100}
    store.set_setting(key, original)

    context = build_context(config, store, event, config.profile())
    visible = layer_json(context, 'profile')
    assert visible['profile']['impressions'][0] == {'text': '愿意分享新近况', 'quote': REDACTED}
    assert visible['profile']['impressions'][1]['text'] == REDACTED
    assert context.telemetry['sources']['records']['profile'] == [
        {'version': 3, 'scope': event.session_key, 'user_id': event.user_id}
    ]
    assert store.get_setting(key) == original

    store.set_setting(key, {'version': 4, 'profile': {'impressions': [
        {'text': FORGOTTEN, 'quote': '安全的证据文本'}
    ]}})
    empty = build_context(config, store, event, config.profile())
    assert empty.telemetry['sources']['records']['profile'] == []
    assert all(row['name'] != 'profile' for row in empty.layers)


def test_structured_group_state_and_tools_preserve_unrelated_fields(tmp_path):
    config, store, event = setup(tmp_path)
    state = {'summary': {'facts': [FORGOTTEN, '群里正在讨论散步']}, 'summary_revision': 7}
    store.set_setting('group_state:' + event.session_key, state)
    facts = [{'content': FORGOTTEN, 'rank': 1}, {'content': '第二名是合成群友', 'rank': 2}]

    context = build_context(config, store, event, config.profile(), tool_facts=facts)
    assert layer_json(context, 'group_state')['summary']['facts'] == [REDACTED, '群里正在讨论散步']
    assert layer_json(context, 'tools') == [
        {'content': REDACTED, 'rank': 1}, {'content': '第二名是合成群友', 'rank': 2}
    ]
    assert context.telemetry['sources']['summary_revision'] == 7
    assert context.telemetry['sources']['tools_included'] is True
    assert facts[0]['content'] == FORGOTTEN
    assert store.get_setting('group_state:' + event.session_key) == state


def test_legacy_source_index_contains_only_visible_enabled_inputs(tmp_path):
    config, store, event = setup(tmp_path, private=True)
    origin = 'data/personas/denia-history.db'
    values = [
        ('person_semantic_memory', 'memory', {'id': 1, 'user_id': event.user_id, 'scope_group': 0,
            'content': '新近况是住B地', 'category': 'fact', 'status': 'active', 'version': 3}),
        ('person_semantic_versions', 'version', {'memory_id': 1, 'version': 3, 'quote': FORGOTTEN}),
        ('persona_portraits', 'portrait', {'user_id': event.user_id, 'content': '愿意分享近况'}),
        ('persona_intents', 'intent-visible', {'user_id': event.user_id, 'topic': '散步',
            'description': '计划散步', 'version': 2}),
        ('persona_intents', 'intent-hidden', {'user_id': event.user_id, 'topic': '旧自述',
            'description': FORGOTTEN, 'version': 1}),
    ]
    with store.connect() as conn:
        conn.execute('CREATE TABLE legacy_rows(origin TEXT,table_name TEXT,row_key TEXT,source_hash TEXT,data TEXT,imported_at REAL,PRIMARY KEY(origin,table_name,row_key))')
        conn.executemany('INSERT INTO legacy_rows VALUES(?,?,?,?,?,?)', [
            (origin, table, key, 'synthetic-hash', json.dumps(value), 100) for table, key, value in values
        ])

    context = build_context(config, store, event, config.profile())
    legacy = layer_json(context, 'legacy_memory')
    assert legacy['memory'][0]['evidence']['quote'] == REDACTED
    cognition = layer_json(context, 'legacy_cognition')
    assert [row['content'] for row in cognition] == ['计划散步']
    assert {row['row_key'] for row in context.telemetry['sources']['legacy_sources']} == {
        'memory', 'portrait', 'intent-visible'
    }
    disabled = build_context(replace(config, memory_enabled=False, cognition_enabled=False),
                             store, event, config.profile())
    assert disabled.telemetry['sources']['legacy_sources'] == []
    assert all(row['name'] not in {'legacy_memory', 'legacy_cognition'} for row in disabled.layers)
