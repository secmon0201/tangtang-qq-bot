import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from bot.services.persona_background import PersonaBackground
from bot.services.persona_background_retry import BackgroundRetry, classify_background_failure, parse_growth_output
from bot.services.persona_engine import PersonaEngine
from bot.services.persona_growth_diagnostics import diagnostic_text
from bot.services.persona_store import PersonaStore
from bot.services.tangtang_chat import TangtangConfig, TangtangProvider


def seeded_worker_parts(tmp_path, monkeypatch):
    store = PersonaStore(tmp_path / 'state.db')
    engine = PersonaEngine(store, None, feature_enabled=lambda *_: True, chat_enabled=lambda *_: True)
    engine.evidence_allowed = lambda *_: True
    for group in (1001, 1002):
        for index in range(5):
            store.observe(persona='denia', group_id=group, user_id=2001,
                          request_id=f'{group}:{index}', source='休息很重要，可以慢慢来', reply='嗯',
                          now=1789488000 + index % 2 * 86400)
    clock = [1789617600.]
    monkeypatch.setattr('bot.services.persona_background.time.time', lambda: clock[0])
    config = [replace(TangtangConfig.disabled(), enabled=True, api_style='chat_completions',
                      api_url='https://example.invalid/v1', api_key='test-key', model='test-model')]
    return store, engine, clock, config, SimpleNamespace(load=lambda: config[0])


def test_real_provider_outage_backoff_and_recovery_preserve_evidence_and_budget(tmp_path, monkeypatch):
    store, engine, clock, config, loader = seeded_worker_parts(tmp_path, monkeypatch)
    calls = []
    def request_handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(503, json={'error': {'code': 'service_unavailable', 'message': 'PRIVATE PROMPT'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"proposals":[]}'}}],
                                        'usage': {'total_tokens': 25}})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original_client(transport=httpx.MockTransport(request_handler), **kw))
    worker = PersonaBackground(engine, TangtangProvider(), loader)
    pending_ids = {row['id'] for row in store.pending_interactions('denia', 1001)}
    async def run():
        await worker.tick({1001, 1002})
        assert len(calls) == 1
        assert pending_ids == {row['id'] for row in store.pending_interactions('denia', 1001)}
        assert store.budget_used('background', 'global', clock[0]) == 1
        review = store.growth_diagnostics('denia', 1001)[0]
        detail = json.loads(review['decisions'])[0]
        assert detail['http_status'] == 503 and detail['retryable']
        assert 'PRIVATE' not in json.dumps(review)
        assert 'HTTP 503' in diagnostic_text(store, 'denia', 1001)
        # A restarted worker shares the provider circuit with every group.
        restarted = PersonaBackground(engine, TangtangProvider(), loader)
        clock[0] += 299
        await restarted.tick({1001, 1002})
        assert len(calls) == 1
        clock[0] += 1
        await restarted.tick({1001, 1002})
        assert len(calls) == 2
        assert not store.pending_interactions('denia', 1002)
        assert store.pending_interactions('denia', 1001)
        # Successful group 2 did not waive failed group 1's six-hour spacing.
        await restarted.tick({1001, 1002})
        assert len(calls) == 2
        clock[0] += 21600
        await restarted.tick({1001, 1002})
        assert len(calls) == 3
        assert not store.pending_interactions('denia', 1001)
        assert store.budget_used('background', 'global', clock[0]) == 3
        assert store.option('background_provider_retry') == {}
    asyncio.run(run())


def test_permanent_auth_failure_waits_for_configuration_change(tmp_path, monkeypatch):
    store, engine, clock, config, loader = seeded_worker_parts(tmp_path, monkeypatch)
    class Provider:
        calls = 0
        async def generate(self, *args):
            self.calls += 1
            if self.calls == 1:
                response = httpx.Response(401, request=httpx.Request('POST', 'https://example.invalid'),
                                          json={'error': {'code': 'invalid_api_key'}})
                response.raise_for_status()
            return '{"proposals":[]}', {}
    provider = Provider()
    async def run():
        await PersonaBackground(engine, provider, loader).tick({1001, 1002})
        clock[0] += 86399
        await PersonaBackground(engine, provider, loader).tick({1001, 1002})
        assert provider.calls == 1
        assert store.budget_used('background', 'global', clock[0]) == 0
        assert '等待配置修复' in diagnostic_text(store, 'denia', 1001)
        config[0] = replace(config[0], api_key='replacement-test-key')
        await PersonaBackground(engine, provider, loader).tick({1001, 1002})
        assert provider.calls == 2
        assert store.option('background_provider_retry') == {}
    asyncio.run(run())


def test_unchanged_provider_is_rechecked_after_one_day(tmp_path):
    store = PersonaStore(tmp_path / 'state.db')
    circuit = BackgroundRetry(store)
    config = TangtangConfig.disabled()
    response = httpx.Response(401, request=httpx.Request('POST', 'https://example.invalid'))
    error = httpx.HTTPStatusError('private message', request=response.request, response=response)
    circuit.failed(config, error, 100)
    assert circuit.blocked_status(config, 86499)
    assert not circuit.blocked_status(config, 86500)


@pytest.mark.parametrize('status,code,retryable', [(400, 'unsupported_parameter', False),
    (403, 'permission_denied', False), (429, 'rate_limit_exceeded', True),
    (429, 'insufficient_quota', False), (502, 'server_error', True)])
def test_status_classification_and_header_bounds(status, code, retryable):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://example.invalid'),
                              json={'error': {'code': code}}, headers={'Retry-After': '86400'})
    error = httpx.HTTPStatusError('do not persist this secret', request=response.request, response=response)
    failure = classify_background_failure(error, 100)
    assert (failure.status, failure.code, failure.retryable, failure.retry_after) == (status, code, retryable, 21600)


def test_untrusted_error_codes_and_body_are_never_saved(tmp_path):
    store = PersonaStore(tmp_path / 'state.db')
    circuit = BackgroundRetry(store)
    config = TangtangConfig.disabled()
    response = httpx.Response(500, request=httpx.Request('POST', 'https://example.invalid/PRIVATE'),
                              json={'error': {'code': 'PRIVATE_SECRET', 'message': 'PRIVATE MESSAGE'}})
    error = httpx.HTTPStatusError('PRIVATE KEY', request=response.request, response=response)
    for attempt in range(10):
        detail = circuit.failed(config, error, 100)
    assert detail['error_code'] == 'unclassified'
    assert detail['next_attempt_at'] == 21700
    assert 'PRIVATE' not in json.dumps(store.option('background_provider_retry'))


def test_model_output_json_fences_supported_but_prose_is_rejected():
    assert parse_growth_output('```json\n{"proposals":[]}\n```') == []
    with pytest.raises(ValueError):
        parse_growth_output('I saved everything! {"proposals":[]}')


def test_disabled_during_generation_keeps_pending_evidence(tmp_path, monkeypatch):
    store, engine, clock, config, loader = seeded_worker_parts(tmp_path, monkeypatch)
    class Provider:
        async def generate(self, *args):
            store.set_option('background_enabled', False)
            return '{"proposals":[]}', {}
    asyncio.run(PersonaBackground(engine, Provider(), loader).tick({1001}))
    assert len(store.pending_interactions('denia', 1001)) == 5


def test_cancelled_job_preserves_six_hour_pacing_and_pending_evidence(tmp_path, monkeypatch):
    store, engine, clock, config, loader = seeded_worker_parts(tmp_path, monkeypatch)
    class Provider:
        async def generate(self, *args):
            raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(PersonaBackground(engine, Provider(), loader).tick({1001}))
    assert store.last_growth_attempt(1001) == clock[0]
    assert len(store.pending_interactions('denia', 1001)) == 5
    assert not store.claim_budget('background', 1001, clock[0] + 60, 12, 2, paced=True)
