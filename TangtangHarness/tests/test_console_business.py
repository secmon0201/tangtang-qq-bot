from fastapi.testclient import TestClient

from tangtang_harness.app import create_app
from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.runtime import Runtime
from tangtang_harness.types import InboundEvent


def test_console_rankings_show_complete_names_and_aggregate_active_cluster(tmp_path):
    runtime = Runtime(HarnessConfig(root=tmp_path))
    domains, db = runtime.tools.domains, runtime.tools.db
    cluster = domains.create_cluster('样例集群')
    for group, name, alias in ((201, '样例群完整名称一', '一群'), (202, '样例群完整名称二', '二群'), (203, '已离开的样例群', '旧群')):
        domains.ensure_group(group, group_name=name)
        domains.set_alias(group, alias)
        domains.add_group_to_cluster(group, cluster.domain_id)
    domains.disable_group(203)
    now = runtime.tools.stats.local_now()
    for key, group, user, name in (('one', 201, 101, '甲'), ('two', 202, 101, '甲'), ('three', 201, 102, '乙'), ('old', 203, 103, '旧成员')):
        db.record_message(key, group, user, name, now)
    client = TestClient(create_app(runtime=runtime))
    groups = client.get('/api/console/groups').json()
    assert groups['items'][0]['group_name'] == '样例群完整名称一'
    assert groups['items'][0]['alias'] == '一群'
    assert groups['items'][0]['cluster_name'] == '样例集群'
    assert groups['items'][2]['enabled'] is False
    result = client.get('/api/console/ranking?group_id=201&period=day&cluster=true').json()
    assert result['scope_label'] == '集群：样例集群'
    assert result['message_total'] == 3
    assert [(row['nickname'], row['message_count']) for row in result['items']] == [('甲', 2), ('乙', 1)]
    assert [group['group_name'] for group in result['groups']] == ['样例群完整名称一', '样例群完整名称二']
    assert client.get('/api/console/ranking?group_id=203').status_code == 400
    assert not runtime.store.requests()


def test_console_group_settings_preserve_other_features_and_archive_gate(tmp_path):
    runtime = Runtime(HarnessConfig(root=tmp_path))
    domains = runtime.tools.domains
    domains.ensure_group(201, group_name='样例群完整名称')
    before = runtime.tools.db.group_features(201)
    client = TestClient(create_app(runtime=runtime))
    groups = client.post('/api/console/clusters', json={'name': '新集群'}).json()
    cluster_id = groups['clusters'][0]['domain_id']
    result = client.put('/api/console/groups/201', json={
        'alias': '样例', 'cluster_id': cluster_id,
        'features': {'speech_ranking_push': True}, 'filter_user_ids': [101, 102],
    })
    assert result.status_code == 200
    after = runtime.tools.db.group_features(201)
    assert after == {**before, 'speech_ranking_push': True}
    assert runtime.tools.db.group_filter_members(201) == (101, 102)
    assert domains.domain_for_group(201).domain_id == cluster_id
    assert client.put('/api/console/groups/201', json={'cluster_id': None, 'filter_user_ids': [102]}).status_code == 200
    assert domains.domain_for_group(201).mode == 'solo'
    assert runtime.tools.db.group_filter_members(201) == (102,)
    domains.disable_group(201)
    assert client.put('/api/console/groups/201', json={'features': {'mention_chat': True}}).status_code == 400
    assert not runtime.tools.db.is_managed_group(201)


def test_console_group_settings_validate_before_modifying(tmp_path):
    runtime = Runtime(HarnessConfig(root=tmp_path))
    runtime.tools.domains.ensure_group(201, group_name='样例群')
    client = TestClient(create_app(runtime=runtime))
    result = client.put('/api/console/groups/201', json={'alias': '不应写入', 'cluster_id': 999})
    assert result.status_code == 400
    assert runtime.tools.db.managed_group(201)['alias'] != '不应写入'
    assert client.post('/api/console/clusters', json={'name': ''}).status_code == 400


def test_console_reply_links_cache_and_deduplicates_onebot_sent_copy(tmp_path):
    runtime = Runtime(HarnessConfig(root=tmp_path))
    runtime.tools.domains.ensure_group(201, group_name='样例群完整名称')
    incoming = InboundEvent('one', runtime.bot.self_id, 101, 201, '问题', timestamp=100)
    outgoing = InboundEvent('sent-id', runtime.bot.self_id, runtime.bot.self_id, 201, '答复', timestamp=200)
    runtime.store.append_event(incoming)
    runtime.store.append_event(outgoing)
    profile = ModelProfile('sample', '样例模型', 'custom', 'sample', 'https://example.invalid/v1')
    request_id = runtime.store.add_request(incoming, profile, {})
    runtime.store.finish_request(request_id, usage={'input_tokens': 100, 'cache_read_tokens': 80, 'cache_ratio': .8, 'output_tokens': 20})
    runtime.store.record_delivery(incoming, request_id, outcome='delivered', message_ids=['sent-id'], messages=['答复'])
    result = TestClient(create_app(runtime=runtime)).get('/api/sessions/group:201/events').json()['items']
    assert sum(row['text'] == '答复' for row in result) == 1
    reply = next(row for row in result if row['type'] == 'delivery')
    assert reply['request_id'] == request_id
    assert reply['cache']['cache_read_tokens'] == 80
    assert reply['cache']['input_tokens'] == 100
    assert reply['scope_label'] == '群：样例群完整名称（201）'
    assert result[0]['id'] == reply['id']
