# Explicit legacy-layout compatibility contracts; new defaults are tested in test_cache_spine.py.
import asyncio
import json
from dataclasses import replace

import httpx

from tangtang_harness.chat import ChatService
from tangtang_harness.config import HarnessConfig, ModelProfile, public_config
from tangtang_harness.context import build_context
from tangtang_harness.media import image_sources
from tangtang_harness.models import ModelClient
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent, ToolCall


def setup(tmp_path, handler=None, **options):
    profile = ModelProfile('test', 'Test', 'custom', 'synthetic', 'https://example.invalid/v1')
    options['extra'] = {**options.get('extra', {}), 'context_mode': 'legacy'}
    config = HarnessConfig(root=tmp_path, mode='live', profiles=(profile,), active_model='test', **options)
    store = Store(tmp_path)
    def empty(request):
        return answer({'facts': []})
    service = ChatService(config, store, ModelClient(transport=httpx.MockTransport(handler or empty)))
    event = InboundEvent('1', 999, 101, 201, '我喜欢画画，也愿意继续完善这个计划。', sender={'nickname': '合成群友'})
    return service, store, event


def answer(value, usage=None):
    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}],
                                   'usage': usage or {'prompt_tokens': 100, 'completion_tokens': 20}})


def test_same_foreground_result_learns_only_after_real_receipt_and_once(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return answer({'decision': 'reply', 'messages': ['记下你的表达了。'], 'voice': 'text',
            'memory_updates': [{'content': '喜欢画画', 'quote': '我喜欢画画', 'kind': 'preference'}],
            'impression_updates': [{'trait': 'persistent', 'direction': 1, 'quote': '愿意继续完善这个计划'}],
            'cognition_updates': [{'kind': 'intent', 'topic': '完善计划', 'content': '继续完善计划',
                                   'state': 'open', 'quote': '愿意继续完善这个计划'}]})
    service, store, event = setup(tmp_path, handler)
    response = asyncio.run(service.respond(event))
    assert response.metadata['voice'] == 'text'
    assert asyncio.run(service.after_delivery(event, response))['status'] == 'unconfirmed'
    assert not store.memories(event.session_key, event.user_id)
    store.confirm_response(response, event, ['123'])
    result = asyncio.run(service.after_delivery(event, response))
    assert result['memory_ids'] and result['impressions'] == 1 and result['cognition_ids']
    assert asyncio.run(service.after_delivery(event, response))['status'] == 'already_processed'
    assert store.memories(event.session_key, event.user_id)[0]['version'] == 1
    assert len(store.impressions(event.session_key, event.user_id)[0]['evidence']) == 1
    assert len(calls) == 1 and not store.jobs()


def test_invalid_learning_does_not_reject_valid_independent_evidence(tmp_path):
    service, store, event = setup(tmp_path)
    learned = service._learn(event, {
        'memory_updates': [{'content': '喜欢画画', 'quote': '我喜欢画画'}, {'content': '住在某地', 'quote': '未说过'}],
        'impression_updates': [{'trait': 'not_a_trait', 'direction': 1, 'quote': '我喜欢画画'}],
        'cognition_updates': [{'kind': 'commitment', 'topic': '计划', 'content': '继续完善计划', 'quote': '继续完善这个计划'}],
        'growth_updates': [{'content': '我喜欢画画', 'quote': '我喜欢画画'}]})
    assert len(learned['memory_ids']) == 1 and learned['cognition_ids']
    assert len(learned['rejected']) == 3 and not store.growth(event.session_key)


def test_learning_batch_keeps_earlier_pending_events_if_final_turn_has_updates(tmp_path):
    service, store, event = setup(tmp_path, lambda request: answer({'decision': 'reply', 'messages': ['合成答复']}))
    for index in range(5):
        current = replace(event, event_id=str(index), text=f'合成讨论第{index}项')
        response = asyncio.run(service.respond(current))
        if index == 4:
            response.metadata = {'memory_updates': [{'content': current.text, 'quote': current.text}]}
        store.confirm_response(response, current, [str(1000 + index)])
        asyncio.run(service.after_delivery(current, response))
    memory = next(job for job in store.jobs() if job['kind'] == 'memory')
    assert len(memory['source']['events']) == 4
    assert len(store.requests()) == 5  # Queueing learning did not make background model calls.


def test_new_event_while_memory_job_runs_survives_and_new_queued_events_merge(tmp_path):
    service, store, event = setup(tmp_path)
    first = service.enqueue_memory(event)
    store.update_job(first, 'running')
    second = service.enqueue_memory(replace(event, event_id='2'))
    assert first != second
    assert service.enqueue_memory(replace(event, event_id='3')) == second
    pending = next(job for job in store.jobs() if job['id'] == second)
    assert [item['event_id'] for item in pending['source']['events']] == ['2', '3']


def test_disabled_queued_memory_does_not_call_model(tmp_path):
    def forbidden(request):
        raise AssertionError('关闭后的排队任务不能消费模型')
    service, store, event = setup(tmp_path, forbidden)
    service.enqueue_memory(event)
    service.config = replace(service.config, memory_enabled=False)
    assert asyncio.run(service.run_background_once()) is None
    assert store.jobs()[0]['status'] == 'queued' and not store.requests()


def test_disable_while_request_runs_preserves_usage_without_publishing(tmp_path):
    service, store, event = setup(tmp_path)
    def handler(request):
        service.config = replace(service.config, memory_enabled=False)
        return answer({'facts': [{'content': '喜欢画画', 'quote': '我喜欢画画'}]})
    service.model_client = ModelClient(transport=httpx.MockTransport(handler))
    service.enqueue_memory(event)
    assert asyncio.run(service.run_background_once())['status'] == 'disabled'
    assert not store.memories(event.session_key, event.user_id)
    assert store.requests()[0]['usage']['input_tokens'] == 100


def test_rolling_background_budget_is_persistent_across_services(tmp_path):
    service, store, event = setup(tmp_path, background_requests_per_hour=1)
    service.enqueue_memory(event)
    assert asyncio.run(service.run_background_once())['status'] == 'completed'
    service.enqueue_memory(replace(event, event_id='2'))
    restarted = ChatService(service.config, Store(tmp_path), service.model_client)
    assert asyncio.run(restarted.run_background_once())['status'] == 'budget_paused'
    assert len(store.requests()) == 1 and len(store.jobs(status='queued')) == 1


def test_invalid_foreground_format_retains_actual_paid_usage(tmp_path):
    service, store, event = setup(tmp_path, lambda request: answer({'decision': 'reply', 'messages': 123}))
    response = asyncio.run(service.respond(event))
    assert response.status == 'failed' and response.usage['input_tokens'] == 100
    assert store.request(response.request_id)['usage']['output_tokens'] == 20
    assert not store.history(event.session_key)


def test_profile_is_published_only_after_independent_review(tmp_path):
    calls = []
    draft = {'impressions': [{'text': '表达创作兴趣', 'quote': '我喜欢画画'}]}
    def handler(request):
        calls.append(json.loads(request.content))
        return answer(draft if len(calls) == 1 else {'accepted': True, 'profile': draft})
    service, store, event = setup(tmp_path, handler)
    store.append_event(event)
    result = service.manage(event, ToolCall('profile_generate'))
    assert result.status == 'ok' and service.config.profile_enabled is False
    assert asyncio.run(service.run_background_once())['kind'] == 'profile'
    assert store.get_setting('profile:' + event.session_key + ':' + str(event.user_id)) is None
    assert asyncio.run(service.run_background_once())['kind'] == 'profile_review'
    published = store.get_setting('profile:' + event.session_key + ':' + str(event.user_id))
    assert published['version'] == 1 and len(calls) == 2
    assert {row['purpose'] for row in store.requests()} == {'profile', 'profile_review'}


def test_failed_profile_review_preserves_previous_published_version(tmp_path):
    service, store, event = setup(tmp_path, lambda request: answer({'accepted': False}))
    key = 'profile:' + event.session_key + ':' + str(event.user_id)
    store.set_setting(key, {'version': 2, 'profile': '旧有效画像'})
    store.enqueue_job('profile_review', event.session_key + ':' + str(event.user_id),
        {'events': [event.to_dict()], 'explicit': True, 'draft': {'impressions': []}})
    assert asyncio.run(service.run_background_once())['status'] == 'failed'
    assert store.get_setting(key)['version'] == 2
    assert store.requests()[0]['usage']['input_tokens'] == 100


def test_summary_uses_ordered_delta_excludes_tools_and_commits_cursor(tmp_path):
    service, store, event = setup(tmp_path, lambda request: answer({'topics': ['合成话题'], 'unresolved': []}))
    for index in range(3):
        current = replace(event, event_id=str(index), text=f'发言{index}')
        store.append_event(current)
        if index == 1:
            store.set_setting('event_scope:' + current.key, {'chat_allowed': False, 'kind': 'tool'})
    service.enqueue_summary(event.session_key)
    source = store.jobs()[0]['source']
    assert [item['event_id'] for item in source['events']] == ['0', '2']
    assert asyncio.run(service.run_background_once())['status'] == 'completed'
    assert store.get_setting('group_state:' + event.session_key)['summary_cursor'] == 3
    assert service.enqueue_summary(event.session_key) is None


def test_memory_correction_keeps_versions_and_is_bound_to_owner(tmp_path):
    service, store, event = setup(tmp_path)
    record_id = store.remember(event.session_key, event.user_id, '喜欢画画', quote='我喜欢画画', event_key=event.key)
    corrected = replace(event, event_id='2', text=f'#记忆 更正 {record_id} 我现在喜欢雕塑')
    assert '已更正' in service.memory_control(corrected, 'correct', f'{record_id} 我现在喜欢雕塑')
    assert store.memories(event.session_key, event.user_id)[0]['version'] == 2
    intruder = replace(corrected, user_id=102)
    assert '没有该编号' in service.memory_control(intruder, 'correct', f'{record_id} 我现在喜欢雕塑')
    assert '已停用 1' in service.memory_control(event, 'forget', '雕塑')
    assert '已恢复 1' in service.memory_control(event, 'restore', '雕塑')
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM memory_versions WHERE memory_id=?', (record_id,)).fetchone()[0] == 4


def test_group_growth_disable_does_not_change_global_record_or_other_groups(tmp_path):
    service, store, event = setup(tmp_path)
    record_id = store.grow(event, '先把事情讲清楚', '继续完善这个计划')
    administrator = replace(event, sender={'role': 'admin'})
    result = service.manage(administrator, ToolCall('growth_manage', {'text': f'成长 停用 {record_id}'}))
    assert result.status == 'ok' and store.growth('group:202')[0]['status'] == 'active'
    assert record_id in store.get_setting('growth_disabled:' + event.session_key)
    before = build_context(service.config, store, event, service.config.profile())
    other = build_context(service.config, store, replace(event, group_id=202), service.config.profile())
    assert '先把事情讲清楚' not in before.user_content
    assert '先把事情讲清楚' in other.user_content


def test_growth_management_retains_group_and_global_permissions(tmp_path):
    service, store, event = setup(tmp_path)
    store.set_setting('operator_ids', [9999])
    global_id = store.grow(event, '合成共享表达', '合成证据')
    local_id = store.grow(event, '合成本群表达', '合成证据', shared=False)
    admin = replace(event, sender={'role': 'admin'})
    operator = replace(event, user_id=9999)
    assert service.manage(event, ToolCall('growth_manage', {'text': '成长 列表'})).status == 'denied'
    assert service.manage(admin, ToolCall('growth_manage', {'text': '成长 全局 列表'})).status == 'denied'
    assert service.manage(admin, ToolCall('growth_manage', {'text': f'成长 回退 {local_id} 1'})).status == 'denied'
    rows = service.manage(operator, ToolCall('growth_manage', {'text': '成长 全局 列表'})).data['growth']
    assert [row['id'] for row in rows] == [global_id]
    assert service.manage(operator, ToolCall('growth_manage', {'text': f'成长 全局 停用 {local_id}'})).status == 'missing'


def test_growth_diagnostics_show_recent_task_states_without_private_evidence(tmp_path):
    service, store, event = setup(tmp_path)
    store.set_setting('operator_ids', [9999])
    job = store.enqueue_job('growth', event.session_key, {'quote': '私人证据不可进入诊断回复'})
    store.update_job(job, 'failed', {'error': '合成错误', 'evidence': '私人证据不可进入诊断回复'})
    store.enqueue_job('growth', 'group:202', {'quote': '另一群原文'})
    administrator = replace(event, sender={'role': 'admin'})
    assert service.manage(administrator, ToolCall('growth_manage', {'text': '成长 诊断'})).status == 'denied'
    operator = replace(event, user_id=9999)
    result = service.manage(operator, ToolCall('growth_manage', {'text': '成长 诊断'}))
    assert result.status == 'ok' and len(result.data['jobs']) == 1
    assert 'failed' in result.text and '私人证据' not in json.dumps(result.to_dict(), ensure_ascii=False)
    global_result = service.manage(operator, ToolCall('growth_manage', {'text': '成长 全局 诊断'}))
    assert len(global_result.data['jobs']) == 2 and '另一群原文' not in global_result.text


def test_cognitive_updates_preserve_old_versions_and_session_boundaries(tmp_path):
    service, store, event = setup(tmp_path)
    service._learn(event, {'cognition_updates': [{'kind': 'intent', 'topic': '计划', 'content': '继续完善',
                        'state': 'open', 'quote': '继续完善这个计划'}]})
    resolved = replace(event, event_id='2', text='这个计划已经完成了。')
    service._learn(resolved, {'cognition_updates': [{'kind': 'intent', 'topic': '计划', 'content': '计划完成',
                        'state': 'resolved', 'quote': '这个计划已经完成了'}]})
    assert store.cognition(event.session_key, event.user_id)[0]['version'] == 2
    assert not store.cognition('group:202', event.user_id)
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM cognition_versions').fetchone()[0] == 2


def test_public_config_does_not_expose_nested_profile_credentials(tmp_path):
    service, store, event = setup(tmp_path)
    profile = replace(service.config.profile(), api_key='synthetic-secret', extra_body={'client': {'api_key': 'nested-secret'}})
    public = public_config(replace(service.config, profiles=(profile,)))
    assert 'synthetic-secret' not in json.dumps(public)
    assert 'nested-secret' not in json.dumps(public)


def test_forgetting_filters_old_raw_turns_and_snapshot_without_deleting_sources(tmp_path):
    service, store, event = setup(tmp_path)
    store.append_event(event)
    store.remember(event.session_key, event.user_id, '喜欢画画', quote='我喜欢画画', event_key=event.key)
    turn_id = store.confirm_turn(event, ['你说喜欢画画'], message_ids=['123'], user_content=event.text)
    store.publish_snapshot(event.session_key, {'facts': ['喜欢画画'], 'commitments': [], 'unresolved': [], 'topic_progress': []}, turn_id)
    assert '已停用 1' in service.memory_control(event, 'forget', '画画')
    current = replace(event, event_id='2', text='有什么记忆？')
    context = build_context(service.config, store, current, service.config.profile())
    assert '喜欢画画' not in json.dumps(context.payload, ensure_ascii=False)
    assert '喜欢画画' in store.history(event.session_key)[0]['user_content']
    learned = service._learn(event, {'memory_updates': [{'content': '爱好绘画', 'quote': '我喜欢画画'}]})
    assert not learned['memory_ids']
    assert '已恢复 1' in service.memory_control(event, 'restore', '画画')
    restored = build_context(service.config, store, current, service.config.profile())
    assert '喜欢画画' in json.dumps(restored.payload, ensure_ascii=False)


def test_forget_all_requires_short_confirmation_scoped_to_person(tmp_path):
    service, store, event = setup(tmp_path)
    store.remember(event.session_key, event.user_id, '喜欢画画', quote='我喜欢画画', event_key=event.key)
    store.remember(event.session_key, 102, '喜欢读书', quote='我喜欢读书', event_key='other')
    assert '需确认' in service.memory_control(event, 'forget')
    assert store.memories(event.session_key, event.user_id)
    assert '已停用 1' in service.memory_control(event, 'forget', '确认全部')
    assert not store.memories(event.session_key, event.user_id)
    assert store.memories(event.session_key, 102)


def test_profile_views_do_not_call_models_and_unmentioned_target_is_rejected(tmp_path):
    service, store, event = setup(tmp_path)
    store.set_setting('profile:' + event.session_key + ':' + str(event.user_id), {'version': 1, 'profile': {'impressions': []}})
    assert service.manage(event, ToolCall('profile_generate', {'action': 'list'})).status == 'ok'
    assert service.manage(event, ToolCall('profile_generate', {'target_user_id': 102})).status == 'denied'
    assert not store.jobs() and not store.requests()


def test_excluded_previous_image_does_not_search_backwards_for_another_image(tmp_path):
    service, store, event = setup(tmp_path)
    older = replace(event, event_id='old', segments=({'type': 'image', 'data': {'url': 'https://example.invalid/old.png'}},))
    blocked = replace(event, event_id='blocked', segments=({'type': 'image', 'data': {'url': 'https://example.invalid/blocked.png'}},))
    current = replace(event, event_id='current', segments=())
    assert image_sources(current, [older, blocked], excluded_keys={blocked.key}) == []


def test_persona_facts_change_static_hash_but_gallery_changes_do_not(tmp_path):
    service, store, event = setup(tmp_path)
    folder = tmp_path / 'resources' / 'personas' / 'denia'
    folder.mkdir(parents=True)
    (folder / 'persona.md').write_text('合成人格', encoding='utf-8')
    (folder / 'canonical-facts.md').write_text('固定事实一', encoding='utf-8')
    (folder / 'canonical-events.md').write_text('固定事件一', encoding='utf-8')
    (folder / 'dialogue-corpus.md').write_text('| 1 | 画画可以放松。 | 平静 | 画画 | 其他 | 鼓励 | 5 | 日常 |', encoding='utf-8')
    first = build_context(service.config, store, event, service.config.profile())
    assert '固定事实一' in first.messages[0]['content'] and '固定事件一' in first.messages[0]['content']
    assert '画画可以放松' in first.user_content
    (folder / 'gallery').mkdir()
    (folder / 'gallery' / 'manifest.json').write_text('{}', encoding='utf-8')
    second = build_context(service.config, store, event, service.config.profile())
    assert first.telemetry['static_prefix_hash'] == second.telemetry['static_prefix_hash']
    (folder / 'canonical-facts.md').write_text('固定事实二', encoding='utf-8')
    third = build_context(service.config, store, event, service.config.profile())
    assert first.telemetry['static_prefix_hash'] != third.telemetry['static_prefix_hash']


def test_large_raw_atmosphere_trims_before_completed_history(tmp_path):
    service, store, event = setup(tmp_path)
    store.append_event(event)
    previous = build_context(service.config, store, event, service.config.profile())
    store.confirm_turn(event, ['完成轮次'], message_ids=['123'], user_content=previous.user_content,
                       context_cursor=previous.telemetry['context_cursor'])
    current = replace(event, event_id='3', text='接着讨论')
    baseline = build_context(service.config, store, current, service.config.profile())
    noise = replace(event, event_id='2', user_id=102, text='无关群聊气氛' * 1000)
    store.append_event(noise)
    context = build_context(service.config, store, current, service.config.profile(),
                            input_budget_tokens=baseline.telemetry['estimated_input_tokens'] + 200)
    assert context.telemetry['trimmed_event_keys'] == [noise.key]
    assert context.telemetry['trimmed_turn_ids'] == []
    assert context.messages[-2]['content'] == '完成轮次'


def test_legacy_semantic_memory_and_intents_use_imported_scope_and_provenance(tmp_path):
    service, store, event = setup(tmp_path)
    origin = 'data/personas/denia-history.db'
    values = [
        ('person_semantic_memory', 'memory', {'id': 1, 'user_id': event.user_id, 'scope_group': 0,
            'content': '合成绘画偏好', 'category': 'preference', 'status': 'active', 'version': 3}),
        ('person_semantic_versions', 'version', {'memory_id': 1, 'version': 3, 'source_group': event.group_id,
            'quote': '我喜欢画画', 'source_message_id': 'old'}),
        ('persona_sources', 'source', {'event_key': 'legacy:1', 'user_id': event.user_id,
            'group_id': event.group_id, 'text': '继续完善计划'}),
        ('persona_intents', 'intent', {'id': 'intent1', 'user_id': event.user_id, 'topic': '计划',
            'description': '继续完善计划', 'state': 'open', 'version': 2, 'source_key': 'legacy:1'}),
        ('persona_portraits', 'portrait', {'user_id': event.user_id, 'content': '旧版本人画像', 'dependencies': '{}'}),
    ]
    with store.connect() as conn:
        conn.execute('CREATE TABLE legacy_rows(origin TEXT,table_name TEXT,row_key TEXT,source_hash TEXT,data TEXT,imported_at REAL,PRIMARY KEY(origin,table_name,row_key))')
        conn.executemany('INSERT INTO legacy_rows VALUES(?,?,?,?,?,?)', [(origin, table, key, 'synthetic-hash', json.dumps(value), 100) for table, key, value in values])
    own = store.legacy_personal_context(event.session_key, event.user_id)
    assert own['memory'][0]['version'] == 3 and own['memory'][0]['evidence']['quote'] == '我喜欢画画'
    assert own['cognition'][0]['source']['origin'] == origin and not own['profile']
    other = store.legacy_personal_context('group:202', event.user_id)
    assert not other['memory'] and not other['cognition']
    private = store.legacy_personal_context('private:101', event.user_id)
    assert private['profile'][0]['content'] == '旧版本人画像'
    context = build_context(service.config, store, event, service.config.profile())
    assert '合成绘画偏好' in context.user_content and context.telemetry['sources']['legacy_sources']


def test_fallback_recompiles_vision_and_cache_key_for_actual_profile(tmp_path):
    import base64
    import io
    from PIL import Image
    received = []
    def handler(request):
        value = json.loads(request.content)
        received.append(value)
        return httpx.Response(503) if len(received) == 1 else answer({'decision': 'reply', 'messages': ['备用答复']})
    service, store, event = setup(tmp_path, handler)
    primary = replace(service.config.profile(), fallback_profile_id='backup', cache_key_enabled=True)
    backup = replace(primary, id='backup', model='backup', vision=False, context_limit=4096,
                     max_output_tokens=1000, fallback_profile_id='')
    service.config = replace(service.config, profiles=(primary, backup))
    buffer = io.BytesIO()
    Image.new('RGB', (32, 32)).save(buffer, format='PNG')
    url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
    store.confirm_turn(replace(event, event_id='old'), ['历史答复'], message_ids=['123'],
        user_content=[{'type': 'text', 'text': '历史图片描述'}, {'type': 'image_url', 'image_url': {'url': url}}])
    response = asyncio.run(service.respond(event))
    assert response.profile_id == 'backup'
    assert received[0]['prompt_cache_key'] != received[1]['prompt_cache_key']
    assert '[历史图片：当前模型不支持视觉]' in json.dumps(received[1], ensure_ascii=False)
    assert all(isinstance(message['content'], str) for message in received[1]['messages'])
    assert store.request(response.request_id)['telemetry']['input_budget_tokens'] == 2072
