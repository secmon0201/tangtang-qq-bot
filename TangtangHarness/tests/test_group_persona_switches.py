import asyncio
import time
from dataclasses import replace

import pytest

from tangtang_harness.business.db import Database
from tangtang_harness.business.group_domains import GroupDomainService
from tangtang_harness.context import build_context
from tangtang_harness.types import ToolCall
from tangtang_harness.topics import PersonaTopics
from test_learning_runtime import answer, setup


def group_settings(tmp_path):
    db = Database(tmp_path / 'runtime' / 'business.db')
    db.seed_groups((201, 202))
    for group in (201, 202):
        for key in ('persona_growth', 'persona_topics', 'persona_voice', 'persona_expressions'):
            db.set_group_feature(group, key, True)
    return db


def test_disabled_group_growth_omits_public_context_without_changing_other_groups_or_prefix(tmp_path):
    service, store, event = setup(tmp_path)
    db = group_settings(tmp_path)
    record = store.grow(event, '表达可以有不同视角', '不同视角', shared=True)
    before = build_context(service.config, store, event, service.config.profile())
    assert before.telemetry['sources']['records']['growth'][0]['id'] == record
    db.set_group_feature(201, 'persona_growth', False)
    after = build_context(service.config, store, event, service.config.profile())
    other = build_context(service.config, store, replace(event, group_id=202), service.config.profile())
    assert not after.telemetry['sources']['records']['growth']
    assert other.telemetry['sources']['records']['growth'][0]['id'] == record
    assert before.messages[0] == after.messages[0] == other.messages[0]
    assert store.growth(event.session_key)[0]['status'] == 'active'


def test_disabled_group_growth_still_learns_independent_memory(tmp_path):
    service, store, event = setup(tmp_path)
    db = group_settings(tmp_path)
    db.set_group_feature(201, 'persona_growth', False)
    event = replace(event, text='表达可以有不同视角，我喜欢画画')
    result = service._learn(event, {
        'growth_updates': [{'content': '表达可以有不同视角', 'quote': '表达可以有不同视角'}],
        'memory_updates': [{'content': '喜欢画画', 'quote': '我喜欢画画'}]})
    assert not result['growth_ids'] and not store.growth(event.session_key)
    assert result['memory_ids']


def test_disabled_group_does_not_queue_growth_or_reprocess_off_period_when_enabled(tmp_path):
    service, store, event = setup(tmp_path, lambda request: answer({'decision': 'reply', 'messages': ['收到']}),
                                  background_batch_size=2)
    db = group_settings(tmp_path)
    db.set_group_feature(201, 'persona_growth', False)
    response = asyncio.run(service.respond(event))
    store.confirm_response(response, event, ['100'])
    asyncio.run(service.after_delivery(event, response))
    pending = store.get_setting('learning_batch:' + event.session_key + ':' + str(event.user_id))
    assert 'growth' not in pending[0]['_pending']
    db.set_group_feature(201, 'persona_growth', True)
    second = replace(event, event_id='2')
    response = asyncio.run(service.respond(second))
    store.confirm_response(response, second, ['101'])
    asyncio.run(service.after_delivery(second, response))
    job = next(item for item in store.jobs() if item['kind'] == 'growth')
    assert [item['event_id'] for item in job['source']['events']] == ['2']


def test_group_off_skips_already_queued_growth_without_model_call(tmp_path):
    def forbidden(request):
        raise AssertionError('本群关闭后的排队成长不能消费模型')
    service, store, event = setup(tmp_path, forbidden)
    db = group_settings(tmp_path)
    store.enqueue_job('growth', event.session_key + ':101',
                      {'events': [event.to_dict()], 'source_session': event.session_key})
    db.set_group_feature(201, 'persona_growth', False)
    assert asyncio.run(service.run_background_once()) is None
    assert not store.requests() and store.jobs()[0]['status'] == 'queued'


@pytest.mark.parametrize('kind', ('memory', 'cognition', 'growth', 'summary', 'compaction', 'profile', 'profile_review'))
def test_archived_group_skips_every_queued_background_kind(tmp_path, kind):
    def forbidden(request):
        raise AssertionError('退群后不能继续消费后台模型')
    service, store, event = setup(tmp_path, forbidden)
    domains = GroupDomainService(group_settings(tmp_path))
    domains.ensure_group(201)
    store.enqueue_job(kind, event.session_key + ':101',
                      {'events': [event.to_dict()], 'source_session': event.session_key, 'explicit': True})
    domains.disable_group(201)
    assert asyncio.run(service.run_background_once()) is None
    assert service.enqueue_memory(event) is None
    assert service.enqueue_summary(event.session_key) is None
    assert service.enqueue_compaction(event.session_key) is None
    assert service.manage(event, ToolCall('profile_generate')).status == 'disabled'
    assert not store.requests() and store.jobs()[0]['status'] == 'queued'


def test_group_leave_during_memory_request_preserves_usage_without_publishing(tmp_path):
    domains = GroupDomainService(group_settings(tmp_path))
    domains.ensure_group(201)
    def handler(request):
        domains.disable_group(201)
        return answer({'facts': [{'content': '喜欢画画', 'quote': '我喜欢画画'}]})
    service, store, event = setup(tmp_path, handler)
    service.enqueue_memory(event)
    assert asyncio.run(service.run_background_once())['status'] == 'disabled'
    assert not store.memories(event.session_key, event.user_id)
    assert store.requests()[0]['outcome'] == 'disabled'
    assert store.requests()[0]['usage']['input_tokens'] == 100


@pytest.mark.parametrize('disabled_count', (20, 45))
def test_disabled_growth_backlog_does_not_starve_enabled_memory(tmp_path, disabled_count):
    calls = []
    def handler(request):
        calls.append(request)
        return answer({'facts': [{'content': '喜欢画画', 'quote': '我喜欢画画', 'kind': 'preference'}]})
    service, store, event = setup(tmp_path, handler)
    db = group_settings(tmp_path)
    db.set_group_feature(201, 'persona_growth', False)
    disabled_ids = {
        store.enqueue_job('growth', f'group:201:{index + 1000}',
                          {'events': [event.to_dict()], 'source_session': event.session_key})
        for index in range(disabled_count)
    }
    memory_id = store.enqueue_job('memory', event.session_key + ':101',
                                  {'events': [event.to_dict()], 'source_session': event.session_key})
    result = asyncio.run(service.run_background_once())
    assert result == {'job_id': memory_id, 'status': 'completed', 'kind': 'memory'}
    jobs = {job['id']: job for job in store.jobs()}
    assert all(jobs[job_id]['status'] == 'queued' for job_id in disabled_ids)
    assert jobs[memory_id]['status'] == 'completed'
    assert store.memories(event.session_key, event.user_id)[0]['content'] == '喜欢画画'
    assert not store.growth(event.session_key)
    assert len(calls) == 1 and [request['purpose'] for request in store.requests()] == ['memory']
    assert asyncio.run(service.run_background_once()) is None
    assert len(calls) == 1


def test_group_off_during_growth_request_keeps_usage_without_publishing(tmp_path):
    db = group_settings(tmp_path)
    def handler(request):
        db.set_group_feature(201, 'persona_growth', False)
        return answer({'updates': [{'content': '表达可以有不同视角', 'quote': '表达可以有不同视角'}]})
    service, store, event = setup(tmp_path, handler)
    event = replace(event, text='表达可以有不同视角')
    store.enqueue_job('growth', event.session_key,
                      {'events': [event.to_dict()], 'source_session': event.session_key})
    result = asyncio.run(service.run_background_once())
    assert result['status'] == 'disabled' and not store.growth(event.session_key)
    request = store.requests()[0]
    assert request['outcome'] == 'disabled' and request['usage']['input_tokens'] == 100


def test_persona_status_and_dynamic_voice_follow_current_group_switch(tmp_path):
    service, store, event = setup(tmp_path, speech_enabled=True)
    db = group_settings(tmp_path)
    on = build_context(service.config, store, event, service.config.profile())
    assert '语音已开启' in on.user_content
    db.set_group_feature(201, 'persona_voice', False)
    off = build_context(service.config, store, event, service.config.profile())
    assert '语音已关闭' in off.user_content and on.messages[0] == off.messages[0]
    status = service.manage(event, ToolCall('persona_status', {})).text
    assert '达妮娅' in status and '娅娅 或 @机器人' in status and '语音：已关闭' in status
    private = service.manage(replace(event, group_id=None), ToolCall('persona_status', {})).text
    assert '语音：已开启' in private


def public_topic(store):
    topics = PersonaTopics(store)
    topics.ingest('鸣潮官网', [{'url': 'https://mc.kurogames.com/main/news/detail/123',
        'title': '公开测试更新', 'body': '公开测试版本内容', 'published_at': '2026-10-05'}], time.time())
    return topics


def test_topic_group_switch_changes_dynamic_sources_only(tmp_path):
    service, store, event = setup(tmp_path)
    db = group_settings(tmp_path)
    public_topic(store)
    event = replace(event, text='鸣潮今天有什么更新')
    on = build_context(service.config, store, event, service.config.profile())
    assert '公开测试版本内容' in on.user_content and on.telemetry['public_topic']['topic_id']
    assert any(layer['name'] == 'public_topics' for layer in on.layers)
    db.set_group_feature(201, 'persona_topics', False)
    off = build_context(service.config, store, event, service.config.profile())
    assert '公开测试版本内容' not in off.user_content and off.telemetry['public_topic'] is None
    assert on.messages[0] == off.messages[0]
    other = build_context(service.config, store, replace(event, group_id=202), service.config.profile())
    assert '公开测试版本内容' in other.user_content


def test_topic_provenance_follows_confirmed_answer_and_current_session(tmp_path):
    service, store, event = setup(tmp_path, lambda request: answer({'decision': 'reply',
                                                                  'messages': ['公开测试版本内容']}))
    group_settings(tmp_path)
    topics = public_topic(store)
    event = replace(event, text='鸣潮今天有什么更新')
    response = asyncio.run(service.respond(event))
    assert asyncio.run(service.after_delivery(event, response))['status'] == 'unconfirmed'
    assert not topics.select(event, '来源链接').topic_id
    store.confirm_response(response, event, ['100'])
    asyncio.run(service.after_delivery(event, response))
    source = topics.select(event, '来源链接')
    assert source.url.endswith('/123') and source.topic_id == store.request(response.request_id)['telemetry']['public_topic']['topic_id']
    assert not topics.select(replace(event, group_id=202), '来源链接').topic_id
