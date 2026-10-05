import json
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from tangtang_harness.app import create_app
from tangtang_harness.onebot import MessageSegment, parse_event, wire_message
from tangtang_harness.router import RouteDecision
from tangtang_harness.runtime import Runtime
from test_reply_media_runtime import runtime_fixture
from test_runtime_and_console import FakeBot, configured


@pytest.mark.asyncio
@pytest.mark.parametrize('api_style', ['chat_completions', 'responses'])
async def test_delivered_voice_is_only_a_marker_in_next_model_request(tmp_path, monkeypatch, api_style):
    runtime, model, first = runtime_fixture(tmp_path, monkeypatch,
        source='娅娅用语音讲个短故事', voice='accept', speech_text='这段只交给语音合成')
    profile = replace(runtime.config.profile(), api_style=api_style)
    runtime.update_config(replace(runtime.config, profiles=(profile,),
                                 extra={**runtime.config.extra, 'context_mode': 'legacy'}))
    await runtime.process(first, RouteDecision('chat'))
    assert runtime.speech.calls == ['这段只交给语音合成']
    assert runtime.store.history(first.session_key)[0]['messages'] == ['[语音]']

    runtime.speech.ready = False
    second = replace(first, event_id='2', text='娅娅继续聊')
    runtime.store.append_event(second)
    await runtime.process(second, RouteDecision('chat'))
    payload = json.dumps(model.calls[-1], ensure_ascii=False)
    assert '[语音]' in payload
    assert '这段只交给语音合成' not in payload
    assert '你好呀' not in payload and '今天也一起聊会儿吧' not in payload
    await runtime.close()


@pytest.mark.asyncio
async def test_old_voice_evidence_is_stripped_before_background_request(tmp_path):
    runtime = Runtime(configured(tmp_path), bot=FakeBot())
    encoded = 'base64://' + 'c3ludGhldGlj' * 10000
    packet = {'message_type': 'group', 'group_id': 102, 'user_id': 101,
              'self_id': 103, 'message_id': 1,
              'message': [{'type': 'record', 'data': {'file': encoded, 'text': '附件转写不可使用'}}]}
    event = parse_event(packet)
    raw = {**event.to_dict(), 'text': '', 'segments': packet['message'],
           'quoted': {'message': packet['message'], 'user_id': 104}}
    source, remaining, _ = runtime.chat._bounded_source(
        {'kind': 'memory', 'session_key': 'group:102:101', 'source': {'event': raw}},
        runtime.config.profile(), '只整理用户文字证据')
    frozen = json.dumps(source, ensure_ascii=False)
    assert '[语音]' in frozen
    assert encoded not in frozen and '附件转写不可使用' not in frozen
    assert len(frozen) < 1000 and remaining is None
    await runtime.close()


@pytest.mark.asyncio
async def test_voice_transport_is_sent_intact_but_console_only_receives_marker(tmp_path):
    runtime = Runtime(configured(tmp_path, 'live'), bot=FakeBot())
    event = parse_event({'message_type': 'group', 'group_id': 102, 'user_id': 101,
                         'self_id': 103, 'message_id': 1, 'message': '合成输入'})
    voice = MessageSegment.record(b'synthetic audio')
    original_wire = wire_message(voice)
    await runtime.deliver(event, [voice])
    assert wire_message(runtime.bot.sent[0][1]) == original_wire
    client = TestClient(create_app(tmp_path, runtime=runtime))
    items = client.get('/api/sessions/group:102/events').json()['items']
    assert [item['text'] for item in items if item['type'] == 'delivery'] == ['[语音]']
    assert 'base64://' not in json.dumps(items)
    await runtime.close()


@pytest.mark.asyncio
async def test_console_compacts_old_serialized_image_deliveries_without_changing_archive(tmp_path):
    runtime = Runtime(configured(tmp_path), bot=FakeBot())
    old = str([{'type': 'image', 'data': {'file': 'base64://' + 'synthetic-image' * 10000}}])
    with runtime.store.connect() as conn:
        conn.execute("INSERT INTO deliveries VALUES(1,'','group:102','old-image','delivered','[\"1\"]',?,'',1)",
                     (json.dumps([old]),))
    client = TestClient(create_app(tmp_path, runtime=runtime))
    items = client.get('/api/sessions/group:102/events').json()['items']
    assert items[0]['text'] == '[图片]'
    with runtime.store.connect() as conn:
        assert json.loads(conn.execute('SELECT messages FROM deliveries').fetchone()[0]) == [old]
    await runtime.close()
