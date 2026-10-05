# Explicit legacy-layout compatibility contracts; new defaults are tested in test_cache_spine.py.
from dataclasses import replace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from tangtang_harness.app import create_app
from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.context import build_context
from tangtang_harness.runtime import Runtime
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


def configured(root):
    profile = ModelProfile('sample', 'Synthetic', 'custom', 'synthetic', 'https://example.invalid/v1',
                           currency='USD', input_price_per_million=1, output_price_per_million=2)
    return HarnessConfig(root=root, profiles=(profile,), active_model=profile.id, background_enabled=False, extra={'context_mode': 'legacy'})


def event():
    return InboundEvent('synthetic', 103, 101, 102, '我们继续讨论安排。')


def test_request_prices_stay_at_send_time_after_model_reconfiguration(tmp_path):
    cfg = configured(tmp_path)
    store = Store(tmp_path)
    rid = store.add_request(event(), cfg.profile(), {'messages': [{'role': 'user', 'content': 'test'}]})
    changed = replace(cfg.profile(), currency='CNY', input_price_per_million=20)
    store.set_setting('active_profile', changed.id)
    store.finish_request(rid, usage={'input_tokens': 100, 'cache_read_tokens': 0, 'output_tokens': 2, 'cost': .000104})
    saved = store.request(rid)
    assert saved['usage']['currency'] == 'USD'
    assert saved['usage']['pricing_snapshot']['input_price_per_million'] == 1
    assert saved['payload'] == {'messages': [{'role': 'user', 'content': 'test'}]}
    with store.connect() as conn:
        conn.execute("INSERT INTO requests(id,session_key,event_key,profile_id,provider,model,api_style,purpose,payload,outcome,started_at) VALUES('old','group:102','old','sample','custom','synthetic','chat_completions','chat','{}','running',1)")
    store.finish_request('old', usage={'cost': .5})
    assert store.request('old')['usage'] == {'cost': .5}


def test_summary_version_metadata_does_not_change_model_input(tmp_path):
    cfg, store = configured(tmp_path), Store(tmp_path)
    summary = {'topics': ['安排'], 'unresolved': []}
    store.set_setting('group_state:group:102', {'summary': summary, 'summary_cursor': 0, 'summary_source_users': []})
    before = build_context(cfg, store, event(), cfg.profile())
    store.publish_summary('group:102', summary, 0, [], 'summary-job')
    after = build_context(cfg, store, event(), cfg.profile())
    assert after.payload == before.payload
    assert after.telemetry['sources']['summary_revision'] == 1
    with store.connect() as conn:
        row = conn.execute('SELECT revision,job_id FROM summary_versions').fetchone()
    assert tuple(row) == (1, 'summary-job')


def test_job_timing_records_real_runs_and_retry_count(tmp_path):
    store = Store(tmp_path)
    job = store.enqueue_job('memory', 'group:102:101', {'event': event().to_dict()})
    store.job_wait_reason(job, 'rolling_hour_budget')
    with store.connect() as conn:
        assert conn.execute('SELECT started_at FROM job_timings').fetchone()[0] is None
    store.update_job(job, 'running')
    store.update_job(job, 'failed', {'error': 'synthetic-failure'})
    store.update_job(job, 'queued')
    store.update_job(job, 'running')
    store.update_job(job, 'completed', {'facts': []})
    with store.connect() as conn:
        row = dict(conn.execute('SELECT * FROM job_timings').fetchone())
    assert row['attempts'] == 2
    assert row['started_at'] <= row['ended_at']
    assert row['state'] == 'completed' and row['reason'] == ''


def test_read_only_console_routes_and_full_queue_count(tmp_path):
    cfg = configured(tmp_path)
    model = Mock()
    model.generate.side_effect = AssertionError('visualization must not call models')
    rt = Runtime(cfg, model_client=model)
    rt.store.append_event(event())
    for index in range(121):
        rt.store.enqueue_job('memory', f'group:{200 + index}:101', {'event': event().to_dict()})
    rid = rt.store.add_request(event(), cfg.profile(), {'messages': [{'role': 'user', 'content': 'large-image-placeholder'}]})
    rt.store.finish_request(rid, usage={'input_tokens': 100, 'output_tokens': 2, 'cache_read_tokens': 0})
    client = TestClient(create_app(runtime=rt))
    assert client.get('/api/status').json()['background_count'] == 121
    assert 'payload' not in client.get('/api/requests').json()['items'][0]
    assert client.get('/api/requests/' + rid).json()['payload']['messages'][0]['content'] == 'large-image-placeholder'
    for path in ('overview', 'series', 'breakdown', 'requests', 'sessions', 'users?session_key=group:102',
                 'sessions/group:102', 'records?session_key=group:102&user_id=101', 'jobs', 'tools',
                 'deliveries', 'business', 'speech', 'continuation', 'runtime', 'experiments', 'export'):
        response = client.get('/api/analytics/' + path)
        assert response.status_code == 200, (path, response.text)
    model.generate.assert_not_called()
    assert not rt.bot.connected


def test_request_detail_links_trigger_all_attempts_and_exact_model_diff(tmp_path):
    cfg = configured(tmp_path)
    rt = Runtime(cfg)
    incoming = event()
    rt.store.append_event(incoming)
    older = rt.store.add_request(incoming, cfg.profile(), {'messages': [{'role': 'user', 'content': 'first'}]})
    changed_model = rt.store.add_request(incoming, replace(cfg.profile(), model='other'), {'messages': []})
    selected = rt.store.add_request(incoming, cfg.profile(), {'messages': [{'role': 'user', 'content': 'third'}]})
    with rt.store.connect() as conn:
        for at, rid in enumerate((older, changed_model, selected), 1):
            conn.execute('UPDATE requests SET started_at=? WHERE id=?', (at, rid))
        for index in range(25):
            conn.execute("INSERT INTO tools(session_key,event_key,name,arguments,result,created_at) VALUES('group:102',?,'synthetic','{}','{}',?)", (incoming.key if index == 0 else str(index), index))
    client = TestClient(create_app(runtime=rt))
    detail = client.get('/api/requests/' + selected).json()
    assert detail['trigger']['payload']['text'] == incoming.text
    assert detail['user_id'] == incoming.user_id
    assert [attempt['id'] for attempt in detail['attempts']] == [older, changed_model, selected]
    assert all('payload' not in attempt for attempt in detail['attempts'])
    assert len(detail['tool_calls']) == 1
    assert client.get('/api/requests/' + selected + '/diff').json()['previous_request_id'] == older
