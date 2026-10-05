import json
import sqlite3
from types import SimpleNamespace

import pytest

from tangtang_harness.analytics import Analytics
from tangtang_harness.business.db import Database
from tangtang_harness.business.group_domains import GroupDomainService
from tangtang_harness.config import HarnessConfig
from tangtang_harness.store import Store, encode
from tangtang_harness.types import InboundEvent


@pytest.fixture
def analytics(tmp_path):
    config = HarnessConfig(root=tmp_path)
    store = Store(tmp_path)
    return Analytics(SimpleNamespace(config=config, store=store))


def insert_requests(analytics, rows):
    with analytics.runtime.store.connect() as connection:
        for number, row in enumerate(rows):
            event = InboundEvent(str(row.get('id', number)), 999, row.get('user', 101), row.get('group', 201),
                                 'synthetic event', sender={'nickname': '示例成员'}, timestamp=row.get('at', number + 100))
            connection.execute('INSERT OR IGNORE INTO events(event_key,session_key,event_id,user_id,timestamp,received_at,payload) VALUES(?,?,?,?,?,?,?)',
                               (event.key, event.session_key, event.event_id, event.user_id, event.timestamp, event.timestamp, encode(event.to_dict())))
            connection.execute('INSERT OR IGNORE INTO sessions VALUES(?,?,0)', (event.session_key, event.timestamp))
            connection.execute("""INSERT INTO requests(id,session_key,event_key,profile_id,provider,model,api_style,purpose,account,payload,usage,
                outcome,started_at,ended_at,telemetry) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(row.get('id', number)), event.session_key, event.key, row.get('profile', 'one'), row.get('provider', 'sample'),
                 row.get('model', 'sample-model'), row.get('api_style', 'responses'), row.get('purpose', 'chat'), row.get('account', 'unknown'),
                 encode({'input': 'private prompt', 'image': 'data:image/png;base64,' + 'x' * 2048}), encode(row.get('usage', {})),
                 row.get('outcome', 'generated'), event.timestamp, None if row.get('running') else event.timestamp + .2,
                 encode(row.get('telemetry', {'estimated_input_tokens': 10, 'input_budget_tokens': 32768}))))


def test_all_history_is_aggregated_and_filters_precede_pagination(analytics):
    insert_requests(analytics, [{'id': number, 'profile': 'older' if number < 5 else 'newer', 'at': number + 100,
                                 'usage': {'input_tokens': 10, 'output_tokens': 1, 'cache_read_tokens': 0}}
                                for number in range(10025)])
    whole = analytics.overview({})['metrics']
    assert whole['request_count'] == 10025
    assert whole['input_tokens'] == 100250
    page = analytics.requests({'profile_id': 'older', 'before': 105}, offset=1, page_size=2)
    assert page['total'] == 5
    assert [row['id'] for row in page['items']] == ['3', '2']
    assert analytics.overview({'profile_id': 'older'})['metrics']['request_count'] == 5


def test_unknown_zero_and_weighted_cache_have_different_meaning(analytics):
    insert_requests(analytics, [
        {'id': 'hit', 'usage': {'input_tokens': 100, 'output_tokens': 3, 'cache_read_tokens': 80, 'cache_write_tokens': 0}},
        {'id': 'zero', 'usage': {'input_tokens': 900, 'output_tokens': 7, 'cache_read_tokens': 0}},
        {'id': 'unknown', 'usage': {'input_tokens': 200, 'output_tokens': 2}},
        {'id': 'running', 'running': True},
    ])
    overview = analytics.overview({})
    metrics = overview['metrics']
    assert metrics['cache_ratio'] == .08
    assert metrics['request_hit_ratio'] == .5
    assert metrics['coverage_ratio'] == .5
    assert metrics['cache_token_known_requests'] == 2
    assert metrics['token_coverage_ratio'] == .75
    assert metrics['cache_write_tokens'] == 0
    assert metrics['cache_write_known_requests'] == 1
    assert metrics['first_token_latency_ms'] is None
    assert {row['name']: row['count'] for row in overview['cache_distribution']} == {'hit': 1, 'miss': 1, 'unknown': 2}
    assert analytics.overview({'status': 'missing'})['metrics']['cache_read_tokens'] is None


def test_currency_is_not_combined_or_inferred_from_current_config(analytics):
    insert_requests(analytics, [
        {'usage': {'cost': 1.25, 'currency': 'CNY'}},
        {'usage': {'cost': .5, 'currency': 'USD'}},
        {'usage': {'cost': 2}},
        {'running': True},
    ])
    metrics = analytics.overview({})['metrics']
    assert metrics['known_cost'] is None and metrics['cost'] is None
    assert metrics['cost_coverage_ratio'] == .75
    prices = {row['currency']: row for row in metrics['cost_by_currency']}
    assert prices['CNY']['known_cost'] == 1.25
    assert prices['USD']['known_cost'] == .5
    assert prices['unknown']['known_cost'] == 2
    assert prices['unknown']['coverage_ratio'] == .5


def test_observed_is_excluded_and_lightweight_views_never_return_payload(analytics):
    insert_requests(analytics, [
        {'id': 'observe', 'outcome': 'observed'},
        {'id': 'sent', 'usage': {'input_tokens': 10, 'cache_read_tokens': 0},
         'telemetry': {'layers': [{'name': 'history', 'text': 'data:image/png;base64,secret', 'estimated_tokens': 9, 'stable': True}]}}
    ])
    assert analytics.overview({})['metrics']['request_count'] == 1
    assert analytics.overview({'actual_only': False})['metrics']['request_count'] == 2
    requests = analytics.requests({})
    serialized = json.dumps(requests)
    assert 'payload' not in serialized and 'base64' not in serialized and 'private prompt' not in serialized
    session = analytics.session('group:201', {})
    assert 'base64' not in json.dumps(session)
    assert session['last_context_layers'][0]['name'] == 'history'
    assert 'base64' not in analytics.exports('requests', {})['content']


def test_session_user_provider_status_and_time_filters_are_exact(analytics):
    insert_requests(analytics, [
        {'id': 'one', 'group': 201, 'user': 101, 'at': 10, 'provider': 'p1', 'outcome': 'failed'},
        {'id': 'two', 'group': 202, 'user': 101, 'at': 20, 'provider': 'p1'},
        {'id': 'three', 'group': 201, 'user': 102, 'at': 30, 'provider': 'p2'},
    ])
    assert [row['id'] for row in analytics.requests({'session_key': 'group:201', 'user_id': 101, 'provider': 'p1', 'status': 'failed', 'after': 5, 'before': 15})['items']] == ['one']
    assert analytics.overview({'session_key': 'group:201'})['metrics']['request_count'] == 2
    assert {row['user_id'] for row in analytics.users('group:201')['items']} == {101, 102}
    assert analytics.requests({'model': 'sample-model', 'group_id': 201, 'session_type': 'group', 'source': 'harness'})['total'] == 2
    assert analytics.requests({'source': 'legacy'})['total'] == 0


def test_record_elapsed_does_not_masquerade_as_model_latency(analytics):
    insert_requests(analytics, [{'id': 'failed', 'outcome': 'failed'}])
    row = analytics.requests({})['items'][0]
    assert row['latency_ms'] is None
    assert row['record_elapsed_ms'] == pytest.approx(200)
    assert analytics.overview({})['metrics']['latency_p95_ms'] is None


def test_half_open_interval_and_group_scope_are_shared_by_all_views(analytics):
    insert_requests(analytics, [{'id': 'at-start', 'at': 10, 'group': 201}, {'id': 'at-end', 'at': 20, 'group': 201}, {'id': 'other', 'at': 15, 'group': 202}])
    selected = {'after': 10, 'before': 20, 'group_id': 201}
    assert [row['id'] for row in analytics.requests(selected)['items']] == ['at-start']
    assert analytics.overview(selected)['metrics']['request_count'] == 1
    assert [row['session_key'] for row in analytics.sessions(selected)['items']] == ['group:201']
    store = analytics.runtime.store
    with store.connect() as connection:
        connection.executemany('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)', [
            ('j1', 'memory', 'group:201:101', 'queued', '{}', '{}', 10, 10),
            ('j2', 'memory', 'group:201:101', 'queued', '{}', '{}', 20, 20),
            ('j3', 'memory', 'group:202:101', 'queued', '{}', '{}', 15, 15)])
        connection.executemany("INSERT INTO deliveries(request_id,session_key,event_key,outcome,message_ids,messages,error,created_at) VALUES('',?,?,'delivered','[]','[]','',?)",
                               [('group:201', 'one', 10), ('group:201', 'two', 20), ('group:202', 'three', 15)])
    assert analytics.jobs(selected)['total'] == 1
    assert analytics.deliveries(selected)['total'] == 1


def test_csv_and_json_exports_are_anonymous_and_lightweight(analytics):
    insert_requests(analytics, [{'id': 'request-real', 'user': 910345678, 'group': 920456789}])
    for format in ('csv', 'json'):
        exported = analytics.exports('requests', {}, format=format)
        assert exported['filename'].endswith('.' + format)
        for private in ('910345678', '920456789', 'request-real', '示例成员', 'base64', 'private prompt'):
            assert private not in exported['content']
    data = json.loads(analytics.exports('requests', {}, format='json')['content'])
    assert data['items'][0]['input_tokens'] is None


def test_old_completed_summary_remains_visible_after_version_table_added(analytics):
    with analytics.runtime.store.connect() as connection:
        connection.execute('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)',
                           ('old', 'summary', 'group:201', 'completed', '{}', encode({'topics': ['old']}), 100, 103))
    view = analytics.session('group:201')
    assert view['summary_history_total'] == 1
    assert view['summary_history'][0]['source'] == 'reconstructed'
    assert view['summary_history'][0]['published_at'] is None
    with analytics.runtime.store.connect() as connection:
        connection.execute('INSERT INTO summary_versions VALUES(?,?,?,?,?,?,?)',
                           ('group:201', 1, encode({'topics': ['published']}), 0, '[]', 'old', 104))
    view = analytics.session('group:201')
    assert view['summary_history_total'] == 1
    assert view['summary_history'][0]['source'] == 'published'
    assert view['summary_history'][0]['published_at'] == 104


def test_records_and_legacy_versions_cannot_cross_scope(analytics):
    store = analytics.runtime.store
    store.remember('group:201', 101, 'current group fact', quote='synthetic', event_key='one')
    store.remember('group:202', 101, 'another group fact', quote='synthetic', event_key='two')
    store.remember('group:201', 102, 'another user fact', quote='synthetic', event_key='three')
    store.remember('private:101', 101, 'private fact', quote='synthetic', event_key='four')
    with store.connect() as connection:
        connection.executescript('CREATE TABLE legacy_rows(origin TEXT,table_name TEXT,row_key TEXT,source_hash TEXT,data TEXT,imported_at REAL);')
        for identity, group in ((1, 201), (2, 202)):
            connection.execute('INSERT INTO legacy_rows VALUES(?,?,?,?,?,?)',
                ('archive', 'person_semantic_memory', str(identity), 'hash', encode({'id': identity, 'user_id': 101, 'scope_group': group, 'content': 'archived'}), 10))
            connection.execute('INSERT INTO legacy_rows VALUES(?,?,?,?,?,?)',
                ('archive', 'person_semantic_versions', str(identity), 'hash', encode({'memory_id': identity, 'version': 1, 'content': 'version'}), 10))
    detail = analytics.records('group:201', 101, 0, 1)
    assert [row['content'] for row in detail['memories']] == ['current group fact']
    assert detail['totals']['memories'] == 1
    assert detail['legacy']['person_semantic_memory']['total'] == 1
    assert detail['legacy']['person_semantic_versions']['total'] == 1
    assert detail['legacy']['person_semantic_memory']['items'][0]['data']['scope_group'] == 201
    with pytest.raises(ValueError):
        analytics.records('private:101', 102)


def test_queue_counts_and_global_budget_are_complete(analytics):
    store = analytics.runtime.store
    with store.connect() as connection:
        connection.executemany('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)',
            [(str(n), 'memory', 'group:201:101', 'queued', encode({'source_session': 'group:201', 'user_id': 101}), '{}', n + 1, n + 1) for n in range(125)])
    page = analytics.jobs({'session_key': 'group:201'}, offset=120, page_size=5)
    assert page['total'] == 125
    assert sum(row['count'] for row in page['counts']) == 125
    assert len(page['items']) == 5
    assert analytics.jobs({'session_key': 'group:202'})['total'] == 0
    assert page['budget']['tokens'] == 0
    assert page['budget']['request_limit'] == 20


def test_session_queue_counts_include_user_suffix_and_source_session(analytics):
    insert_requests(analytics, [{'id': 'one', 'group': 201}, {'id': 'other', 'group': 202}])
    with analytics.runtime.store.connect() as connection:
        connection.executemany('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)', [
            ('suffix', 'memory', 'group:201:101', 'queued', '{}', '{}', 100, 100),
            ('source', 'growth', 'source-key', 'queued', encode({'source_session': 'group:201'}), '{}', 100, 100),
            ('completed', 'memory', 'group:201:101', 'completed', '{}', '{}', 100, 100),
            ('lookalike', 'memory', 'group:2011:101', 'queued', '{}', '{}', 100, 100)])
    sessions = {row['session_key']: row for row in analytics.sessions()['items']}
    assert sessions['group:201']['queued_jobs'] == 2
    assert sessions['group:202']['queued_jobs'] == 0


def test_known_empty_user_counters_are_zero_and_absent_legacy_is_unknown(analytics):
    insert_requests(analytics, [{'id': 'observe', 'outcome': 'observed'}])
    member = analytics.users('group:201')['items'][0]
    assert member['memory_count'] == 0
    assert member['request_count'] == 0
    assert member['legacy_memory_count'] is None
    with analytics.runtime.store.connect() as connection:
        connection.execute('CREATE TABLE legacy_rows(origin TEXT,table_name TEXT,row_key TEXT,source_hash TEXT,data TEXT,imported_at REAL)')
    view = analytics.users('group:201')
    assert view['legacy_available'] is True
    assert view['items'][0]['legacy_memory_count'] == 0


def test_latency_percentiles_summary_versions_and_timings(analytics):
    insert_requests(analytics, [{'id': str(n), 'usage': {'latency_ms': n * 100, 'first_token_latency_ms': n * 10},
                                 'telemetry': {'job_id': 'job'}} for n in range(1, 5)])
    with analytics.runtime.store.connect() as connection:
        connection.executescript("""CREATE TABLE IF NOT EXISTS summary_versions(session_key TEXT,revision INTEGER,content TEXT,cutoff_event_id INTEGER,source_users TEXT,job_id TEXT,created_at REAL);
            CREATE TABLE IF NOT EXISTS job_timings(job_id TEXT PRIMARY KEY,started_at REAL,ended_at REAL,state TEXT,reason TEXT,attempts INTEGER);
            CREATE TABLE IF NOT EXISTS delivery_timings(delivery_id INTEGER PRIMARY KEY,started_at REAL,ended_at REAL,elapsed_ms REAL);""")
        connection.execute('INSERT INTO summary_versions VALUES(?,?,?,?,?,?,?)', ('group:201', 1, encode({'topics': ['synthetic']}), 2, '[101]', 'job', 100))
        connection.execute('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)', ('job', 'memory', 'group:201:101', 'completed', encode({'user_id': 101}), '{}', 100, 103))
        connection.execute('INSERT INTO job_timings VALUES(?,?,?,?,?,?)', ('job', 101, 103, 'completed', '', 2))
        connection.execute("INSERT INTO deliveries(request_id,session_key,event_key,outcome,message_ids,messages,error,created_at) VALUES('1','group:201','event','delivered','[1]','[]','',200)")
        delivery = connection.execute('SELECT max(id) FROM deliveries').fetchone()[0]
        connection.execute('INSERT INTO delivery_timings VALUES(?,?,?,?)', (delivery, 199, 200, 1000))
    metrics = analytics.overview({})['metrics']
    assert metrics['latency_p50_ms'] == 250
    assert metrics['latency_p95_ms'] == pytest.approx(385)
    assert metrics['first_token_latency_ms'] == 25
    assert analytics.session('group:201')['summary_history_source'] == 'summary_versions'
    assert analytics.session('group:201')['summary_history'][0]['revision'] == 1
    job = analytics.jobs({})['items'][0]
    assert job['execution_ms'] == 2000 and job['attempts'] == 2
    assert analytics.deliveries({})['items'][0]['elapsed_ms'] == 1000


def test_readonly_methods_do_not_change_database(analytics):
    insert_requests(analytics, [{'id': 'one'}])
    with sqlite3.connect(analytics.path) as connection:
        before = connection.execute('SELECT total_changes()').fetchone()[0]
        before_counts = connection.execute('SELECT count(*) FROM requests').fetchone()[0]
    analytics.overview({}); analytics.requests({}); analytics.sessions({}); analytics.users('group:201')
    analytics.session('group:201'); analytics.records('group:201', 101); analytics.jobs({})
    analytics.tools({}); analytics.deliveries({}); analytics.exports('requests', {})
    with sqlite3.connect(analytics.path) as connection:
        assert connection.execute('SELECT count(*) FROM requests').fetchone()[0] == before_counts
        assert connection.execute('SELECT total_changes()').fetchone()[0] == before


def test_console_identity_fields_prefer_full_group_names_and_private_nicknames(analytics):
    database = Database(analytics.root / 'runtime/business.db')
    domains = GroupDomainService(database)
    domains.ensure_group(201, group_name='合成群的完整名称')
    domains.set_alias(201, '短名')
    insert_requests(analytics, [{'id': 'group-request', 'group': 201}, {'id': 'private-request', 'group': None}])

    requests = {row['id']: row for row in analytics.requests({})['items']}
    group = requests['group-request']
    assert group['group_name'] == '合成群的完整名称' and group['alias'] == '短名'
    assert group['scope_label'] == '群：合成群的完整名称（201） · 成员：示例成员（101）'
    assert requests['private-request']['scope_label'] == '私聊：示例成员（101）'
    for row in analytics.overview({})['by_session']:
        assert row['scope_kind'] in {'群', '私聊'} and row['scope_label']
    for row in analytics.windows({})['items']:
        assert row['scope_kind'] in {'群', '私聊'} and row['scope_label']
    sessions = {row['session_key']: row for row in analytics.sessions()['items']}
    assert sessions['group:201']['title'] == '合成群的完整名称'
    assert sessions['group:201']['group_id'] == '201' and sessions['group:201']['enabled'] == 1
    assert sessions['private:101']['user_id'] == 101 and sessions['private:101']['user_nickname'] == '示例成员'
    member = analytics.users('group:201')['items'][0]
    assert member['user_nickname'] == '示例成员' and member['group_name'] == '合成群的完整名称'

    with database.connect() as connection:
        connection.execute('INSERT INTO daily_counts VALUES(?,?,?,?,?,?)', (201, '2026-10-05', 101, '示例成员', 2, '2026-10-05'))
    with sqlite3.connect(analytics.root / 'runtime/business-history.db') as connection:
        connection.execute('CREATE TABLE tangtang_group_messages(group_id INTEGER,user_id INTEGER,text TEXT,created_at TEXT)')
        connection.execute('INSERT INTO tangtang_group_messages VALUES(?,?,?,?)', (201, 101, '合成内容', '2026-10-05T12:00:00+08:00'))
    assert analytics.records('group:201', 101)['speech']['groups'][0]['group_name'] == '合成群的完整名称'

    with analytics.runtime.store.connect() as connection:
        connection.execute('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)',
                           ('job', 'memory', 'group:201:101', 'completed', encode({'user_id': 101}), '{}', 100, 101))
    job = analytics.jobs({})['items'][0]
    assert job['kind'] == 'memory' and job['kind_label'] == '整理长期记忆'
    assert job['scope_label'].endswith('成员：示例成员（101）')

    domains.disable_group(201)
    with analytics.runtime.store.connect() as connection:
        connection.execute('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)',
                           ('archived-job', 'memory', 'group:201:101', 'queued', encode({'user_id': 101}), '{}', 110, 110))
        connection.execute('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)',
                           ('older-running-job', 'memory', 'private:101', 'running', encode({'user_id': 101}), '{}', 90, 90))
    latest = analytics.jobs({})['items']
    assert [item['id'] for item in latest] == ['archived-job', 'job', 'older-running-job']
    assert latest[0]['status'] == 'queued' and latest[0]['status_label'] == '已退群，暂停执行'
    assert latest[0]['reason_label'] == '机器人已退出该群，自动任务已暂停'


def test_background_identity_can_use_saved_member_names_without_chat_events(analytics):
    database = Database(analytics.root / 'runtime/business.db')
    domains = GroupDomainService(database)
    domains.ensure_group(201, group_name='合成群')
    domains.ensure_group(202, group_name='另一合成群')
    database.replace_members(201, [{'user_id': 102, 'nickname': '成员昵称', 'card': '当前群名片'}])
    database.replace_members(202, [{'user_id': 102, 'nickname': '其他群昵称', 'card': '其他群名片'}])
    with analytics.runtime.store.connect() as connection:
        connection.execute('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)',
                           ('saved-member-job', 'memory', 'group:201:102', 'queued', '{}', '{}', 100, 100))
    job = analytics.jobs({})['items'][0]
    assert job['user_nickname'] == '当前群名片'
    assert job['scope_label'] == '群：合成群（201） · 成员：当前群名片（102）'
