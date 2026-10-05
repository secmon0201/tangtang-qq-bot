import asyncio
import copy
from dataclasses import replace
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from tangtang_harness.app import create_app
from tangtang_harness.chat import ChatService
from tangtang_harness.config import HarnessConfig, ModelProfile, load_config
from tangtang_harness.context import payload_diff
from tangtang_harness.models import ModelClient
from tangtang_harness.runtime import Runtime
from tangtang_harness.store import Store, encode
from tangtang_harness.types import InboundEvent


EXTRA_BODY = {
    'api_key': 'synthetic-body-key', 'token': 'synthetic-body-token',
    'access_key': 'synthetic-access-key', 'password': 'synthetic-password',
    'secret': 'synthetic-secret', 'temperature': .2,
    'vendor': {'headers': {'Authorization': 'synthetic-body-authorization'},
               'options': [{'ACCESS_TOKEN': 'synthetic-nested-token', 'normal': 'keep'}]},
}
SECRET_VALUES = [EXTRA_BODY[key] for key in ('api_key', 'token', 'access_key', 'password', 'secret')]
SECRET_VALUES += ['synthetic-body-authorization', 'synthetic-nested-token', 'synthetic-header-key']


def configured(root, *, mode='observe', api_style='chat_completions'):
    profile = ModelProfile('synthetic', '合成模型', 'custom', 'synthetic', 'https://example.invalid/v1',
                           api_key='synthetic-header-key', api_style=api_style, extra_body=copy.deepcopy(EXTRA_BODY))
    return HarnessConfig(root=root, mode=mode, profiles=(profile,), active_model=profile.id,
                         background_enabled=False, summary_enabled=False, compaction_enabled=False)


def event(identity='synthetic:1'):
    return InboundEvent(identity, 103, 101, None, '合成消息')


def no_secrets(value):
    text = json.dumps(value, ensure_ascii=False)
    assert all(secret not in text for secret in SECRET_VALUES), text


def mock_model():
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path.endswith('/responses'):
            result = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"messages":["合成回复"]}'}]}],
                      'usage': {'input_tokens': 10, 'output_tokens': 3}}
        else:
            result = {'choices': [{'message': {'content': '{"messages":["合成回复"]}'}}],
                      'usage': {'prompt_tokens': 10, 'completion_tokens': 3}}
        return httpx.Response(200, json=result)
    return ModelClient(transport=httpx.MockTransport(handle)), requests


class BlockBot:
    self_id = 103
    connected = False
    def __init__(self):
        self.calls = []
    async def send(self, *args, **kwargs):
        self.calls.append('send')
        raise AssertionError('unexpected QQ send')
    async def call_api(self, *args, **kwargs):
        self.calls.append('api')
        raise AssertionError('unexpected QQ API')
    def detach(self, *args):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize('api_style', ['chat_completions', 'responses'])
async def test_wire_body_and_header_unchanged_while_request_records_are_redacted(tmp_path, api_style):
    config = configured(tmp_path, mode='live', api_style=api_style)
    store = Store(tmp_path)
    client, sent = mock_model()
    service = ChatService(config, store, model_client=client)
    reply = await service.respond(event())
    assert len(sent) == 1
    wire = json.loads(sent[0].content)
    assert {key: wire[key] for key in EXTRA_BODY} == EXTRA_BODY
    assert sent[0].headers['Authorization'] == 'Bearer synthetic-header-key'
    assert reply.payload == wire
    assert config.active_profile.extra_body == EXTRA_BODY
    row = store.request(reply.request_id)
    no_secrets(row)
    assert row['payload']['token'] == '[redacted]'
    assert row['payload']['access_key'] == '[redacted]'
    assert row['payload']['vendor']['options'][0]['normal'] == 'keep'
    with store.connect() as connection:
        durable = dict(connection.execute('SELECT payload,telemetry,usage FROM requests WHERE id=?', (reply.request_id,)).fetchone())
    no_secrets(durable)


def test_free_preview_replay_and_experiments_expose_only_redacted_copies(tmp_path):
    model, sent = mock_model()
    bot = BlockBot()
    runtime = Runtime(configured(tmp_path), bot=bot, model_client=model)
    with TestClient(create_app(runtime=runtime)) as client:
        for path in ('/api/preview', '/api/replay'):
            response = client.post(path, json={'session_key': 'private:101', 'text': '合成消息'})
            assert response.status_code == 200
            no_secrets(response.json())
            assert response.json()['payload']['token'] == '[redacted]'
            assert response.json()['model_calls'] == 0
        for kind in ('cold_warm', 'model_switch'):
            response = client.post('/api/experiments', json={'kind': kind, 'paid': False})
            assert response.status_code == 200
            result = response.json()
            no_secrets(result)
            assert result['model_calls'] == 0
            if result['status'] == 'offline':
                no_secrets(runtime.store.get_setting('experiment:' + result['id']))
                with runtime.store.connect() as connection:
                    durable = connection.execute('SELECT value FROM settings WHERE key=?', ('experiment:' + result['id'],)).fetchone()[0]
                no_secrets(durable)
        assert not sent and not bot.calls
        assert runtime.config.active_profile.extra_body == EXTRA_BODY


def test_paid_synthetic_experiment_uses_original_body_and_saves_visible_projection(tmp_path):
    model, sent = mock_model()
    bot = BlockBot()
    runtime = Runtime(configured(tmp_path, mode='live'), bot=bot, model_client=model)
    async def no_external(**kwargs):
        pass
    runtime.tools.poll_external = no_external
    with TestClient(create_app(runtime=runtime)) as client:
        response = client.post('/api/experiments', json={'kind': 'cold_warm', 'paid': True})
        assert response.status_code == 200
        result = response.json()
        assert result['model_calls'] == 2
        assert len(sent) == 2
        for request in sent:
            body = json.loads(request.content)
            assert {key: body[key] for key in EXTRA_BODY} == EXTRA_BODY
            assert request.headers['Authorization'] == 'Bearer synthetic-header-key'
        no_secrets(result)
        no_secrets(runtime.store.get_setting('experiment:' + result['id']))
        no_secrets(client.get('/api/requests').json())
        with runtime.store.connect() as connection:
            durable = [dict(row) for row in connection.execute('SELECT payload,telemetry FROM requests')]
        no_secrets(durable)
        assert not bot.calls


def test_older_request_rows_are_redacted_on_read_and_diff(tmp_path):
    config = configured(tmp_path)
    runtime = Runtime(config, bot=BlockBot())
    old_payload = {'model': 'synthetic', **copy.deepcopy(EXTRA_BODY), 'messages': []}
    request_id = runtime.store.add_request(event(), config.active_profile, old_payload)
    # Simulate a record produced before token/access_key redaction was fixed.
    with runtime.store.connect() as connection:
        connection.execute('UPDATE requests SET payload=? WHERE id=?', (encode(old_payload), request_id))
    no_secrets(runtime.store.request(request_id))
    no_secrets(runtime.store.requests())
    no_secrets(runtime.store.previous_request('private:101', 'synthetic'))
    no_secrets(payload_diff({'id': 'previous', 'payload': old_payload}, old_payload))
    with TestClient(create_app(runtime=runtime)) as client:
        for path in ('/api/requests', '/api/requests/' + request_id, '/api/requests/' + request_id + '/diff'):
            response = client.get(path)
            assert response.status_code == 200
            no_secrets(response.json())


def test_model_editor_roundtrip_preserves_placeholders_and_explicit_clear_is_sent(tmp_path):
    model, sent = mock_model()
    runtime = Runtime(configured(tmp_path), bot=BlockBot(), model_client=model)
    with TestClient(create_app(runtime=runtime)) as client:
        settings = client.get('/api/models').json()
        no_secrets(settings)
        assert settings['items'][0]['extra_body']['token'] == '[redacted]'
        settings['items'][0]['extra_body']['temperature'] = .3
        response = client.put('/api/models', json=settings)
        assert response.status_code == 200
        no_secrets(response.json())
        expected = {**copy.deepcopy(EXTRA_BODY), 'temperature': .3}
        assert runtime.config.active_profile.extra_body == expected
        assert load_config(tmp_path).active_profile.extra_body == expected
        without_body = copy.deepcopy(response.json())
        del without_body['items'][0]['extra_body']
        assert client.put('/api/models', json=without_body).status_code == 200
        assert runtime.config.active_profile.extra_body == expected
        service = ChatService(replace(runtime.config, mode='live'), runtime.store, model_client=model)
        asyncio.run(service.respond(event('synthetic:roundtrip')))
        assert {key: json.loads(sent[0].content)[key] for key in expected} == expected
        assert sent[0].headers['Authorization'] == 'Bearer synthetic-header-key'
        settings = response.json()
        settings['items'][0]['extra_body']['token'] = ''
        del settings['items'][0]['extra_body']['access_key']
        response = client.put('/api/models', json=settings)
        assert response.status_code == 200
        expected['token'] = ''
        del expected['access_key']
        assert runtime.config.active_profile.extra_body == expected
        service = ChatService(replace(runtime.config, mode='live'), runtime.store, model_client=model)
        asyncio.run(service.respond(event('synthetic:clear')))
        body = json.loads(sent[1].content)
        assert body['token'] == '' and 'access_key' not in body
        assert body['api_key'] == EXTRA_BODY['api_key']
        no_secrets(client.get('/api/requests').json())


def test_settings_editor_roundtrip_preserves_core_secrets_and_explicit_empty_clears(tmp_path):
    extra = {'core': {'enabled': False, 'url': 'ws://127.0.0.1:8765', 'token': 'synthetic-core-token'},
             'custom': {'entries': [{'access_key': 'synthetic-settings-key', 'label': 'keep'}]}}
    config = replace(configured(tmp_path), extra=extra, onebot_access_token='synthetic-qq-token')
    runtime = Runtime(config, bot=BlockBot())
    with TestClient(create_app(runtime=runtime)) as client:
        settings = client.get('/api/settings').json()
        encoded = json.dumps(settings)
        assert all(secret not in encoded for secret in ('synthetic-core-token', 'synthetic-settings-key', 'synthetic-qq-token'))
        assert settings['extra']['core']['token'] == '[redacted]'
        assert settings['extra']['custom']['entries'][0]['access_key'] == '[redacted]'
        # Match the console's editable settings shape.
        for name in ('context', 'background', 'routing', 'profiles', 'active_model', 'onebot_access_token_configured'):
            settings.pop(name, None)
        response = client.put('/api/settings', json=settings)
        assert response.status_code == 200
        assert runtime.config.extra == extra
        assert runtime.config.onebot_access_token == 'synthetic-qq-token'
        assert load_config(tmp_path).extra == extra
        assert 'synthetic-core-token' not in json.dumps(response.json())
        settings['extra']['core']['token'] = ''
        response = client.put('/api/settings', json=settings)
        assert response.status_code == 200
        assert runtime.config.extra['core']['token'] == ''
        assert load_config(tmp_path).extra['core']['token'] == ''
        assert runtime.config.extra['custom']['entries'][0]['access_key'] == 'synthetic-settings-key'
        assert runtime.config.onebot_access_token == 'synthetic-qq-token'
