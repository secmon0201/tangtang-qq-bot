import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tangtang_harness.app import create_app
from tangtang_harness.config import (HarnessConfig, ModelProfile, effective_cache_input_budget,
                                    load_config, public_config)
from tangtang_harness.runtime import Runtime


def profile_data():
    return {'id': 'synthetic', 'name': 'Synthetic', 'provider': 'custom',
            'model': 'synthetic', 'base_url': 'https://example.invalid/v1'}


@pytest.mark.parametrize('capacity', [None, 32768, 1048576])
def test_dictionary_profiles_preserve_explicit_capacity(capacity):
    raw = {**profile_data(), 'context_limit': capacity}
    assert ModelProfile.from_dict(raw).context_limit == capacity


def test_missing_dictionary_capacity_stays_unknown_without_changing_constructor_default():
    raw = profile_data()
    assert ModelProfile.from_dict(raw).context_limit is None
    assert 'context_limit' not in raw
    assert ModelProfile(**raw).context_limit == 131072
    assert HarnessConfig.from_dict({'profiles': [raw]}).profiles[0].context_limit is None


def test_models_save_missing_capacity_remains_unknown_and_preview_requires_real_value(tmp_path):
    profile = ModelProfile(**profile_data(), context_limit=32768, api_key='synthetic-secret')
    config = HarnessConfig(root=tmp_path, profiles=(profile,), active_model=profile.id,
                           mode='observe', background_enabled=False)
    model = SimpleNamespace(generate=AsyncMock(side_effect=AssertionError('preview must not call models')))
    runtime = Runtime(config, model_client=model)
    runtime.bot.call_api = AsyncMock(side_effect=AssertionError('model settings must not call QQ'))
    runtime.bot.send = AsyncMock(side_effect=AssertionError('preview must not send QQ'))
    client = TestClient(create_app(runtime=runtime))
    try:
        draft = client.get('/api/models').json()
        draft['items'][0].pop('context_limit')
        saved = client.put('/api/models', json=draft)
        assert saved.status_code == 200
        assert saved.json()['items'][0]['context_limit'] is None
        assert client.get('/api/models').json()['items'][0]['context_limit'] is None
        assert load_config(tmp_path).profile().context_limit is None
        assert runtime.chat.config.profile().context_limit is None
        assert runtime.config.profile().api_key == 'synthetic-secret'
        unknown = client.post('/api/preview', json={'profile_id': profile.id,
            'session_key': 'private:101', 'text': '合成容量预览'})
        assert unknown.status_code == 400
        assert '真实 context_limit' in unknown.json()['detail']

        confirmed = client.get('/api/models').json()
        confirmed['items'][0]['context_limit'] = 65536
        response = client.put('/api/models', json=confirmed)
        assert response.status_code == 200
        assert response.json()['items'][0]['context_limit'] == 65536
        assert load_config(tmp_path).profile().context_limit == 65536
        preview = client.post('/api/preview', json={'profile_id': profile.id,
            'session_key': 'private:101', 'text': '合成容量预览'})
        assert preview.status_code == 200
        assert preview.json()['telemetry']['input_budget_tokens'] == effective_cache_input_budget(
            runtime.config, runtime.config.profile()) == 65536 - profile.max_output_tokens - 1024
        assert preview.json()['model_calls'] == preview.json()['qq_writes'] == 0
        assert not runtime.store.requests()
        model.generate.assert_not_called()
        runtime.bot.call_api.assert_not_called()
        runtime.bot.send.assert_not_called()
    finally:
        client.close()
        asyncio.run(runtime.close())


@pytest.mark.parametrize('capacity', [32768, 131072, 1048576])
def test_automatic_input_budget_follows_configured_model_capacity(capacity):
    profile = ModelProfile(**profile_data(), context_limit=capacity, max_output_tokens=4096)
    config = HarnessConfig(profiles=(profile,), active_model=profile.id)
    assert config.cache_input_budget_tokens is None
    assert effective_cache_input_budget(config, profile) == capacity - 4096 - 1024
    policy = public_config(config)['context_policy']
    assert policy['configured_input_budget_tokens'] is None
    assert policy['effective_input_budget_tokens'] == capacity - 4096 - 1024
    assert policy['budget_source'] == 'model_capacity'
    assert policy['model_context_limit'] == capacity
    assert policy['capacity_verification'] == 'not_verified_by_harness'
    assert policy['rebuild_trigger_tokens'] == int((capacity - 5120) * .9)
    assert policy['rebuild_target_tokens'] == int((capacity - 5120) * .5)


def test_manual_budget_and_per_request_override_stay_within_model_capacity():
    profile = ModelProfile(**profile_data(), context_limit=65536)
    config = HarnessConfig(cache_input_budget_tokens=32768)
    assert effective_cache_input_budget(config, profile) == 32768
    assert effective_cache_input_budget(config, profile, override=8192) == 8192
    assert effective_cache_input_budget(config, profile, override=131072) == 60416


def test_unknown_model_capacity_stays_unknown_even_with_manual_budget():
    profile = ModelProfile.from_dict(profile_data())
    config = HarnessConfig(profiles=(profile,), active_model=profile.id, cache_input_budget_tokens=32768)
    assert effective_cache_input_budget(config, profile) is None
    assert effective_cache_input_budget(config, profile, override=8192) is None
    policy = public_config(config)['context_policy']
    assert policy['effective_input_budget_tokens'] is None
    assert policy['model_context_limit'] is None
    assert policy['budget_source'] == 'unknown_capacity'
    assert policy['rebuild_trigger_tokens'] is None
    assert policy['rebuild_target_tokens'] is None


@pytest.mark.parametrize('invalid', [0, -1, True, False, 1.5, '32768'])
def test_cache_budget_rejects_nonpositive_or_noninteger_values(invalid):
    with pytest.raises(ValueError, match='null.*正整数'):
        HarnessConfig.from_dict({'cache_input_budget_tokens': invalid})
    profile = ModelProfile(**profile_data())
    with pytest.raises(ValueError, match='null.*正整数'):
        effective_cache_input_budget(HarnessConfig(), profile, override=invalid)


def test_settings_api_reports_effective_budget_after_model_switch_and_ignores_derived_fields(tmp_path):
    first = ModelProfile(**profile_data(), context_limit=65536)
    second = ModelProfile(**{**profile_data(), 'id': 'larger'}, context_limit=262144)
    config = HarnessConfig(root=tmp_path, profiles=(first, second), active_model=first.id)
    model = SimpleNamespace(generate=AsyncMock(side_effect=AssertionError('settings must not call models')))
    runtime = Runtime(config, model_client=model)
    runtime.bot.call_api = AsyncMock(side_effect=AssertionError('settings must not call QQ'))
    runtime.bot.send = AsyncMock(side_effect=AssertionError('settings must not send QQ'))
    client = TestClient(create_app(runtime=runtime))
    try:
        current = client.get('/api/settings').json()
        assert current['cache_input_budget_tokens'] is None
        assert current['context_policy']['effective_input_budget_tokens'] == 60416
        assert current['context']['effective_input_budget_tokens'] == 60416
        current['context']['effective_input_budget_tokens'] = -1
        current['context']['model_context_limit'] = -1
        saved = client.put('/api/settings', json=current)
        assert saved.status_code == 200
        assert saved.json()['context_policy']['effective_input_budget_tokens'] == 60416
        assert 'effective_input_budget_tokens' not in load_config(tmp_path).extra
        assert 'model_context_limit' not in load_config(tmp_path).extra
        models = client.get('/api/models').json()
        models['active_model'] = second.id
        assert client.put('/api/models', json=models).status_code == 200
        assert client.get('/api/settings').json()['context_policy']['effective_input_budget_tokens'] == 257024
        manual = client.put('/api/settings', json={'context': {'cache_input_budget_tokens': 8192}})
        assert manual.status_code == 200
        assert manual.json()['context_policy']['effective_input_budget_tokens'] == 8192
        assert manual.json()['context_policy']['budget_source'] == 'configured_limit'
        reset = client.put('/api/settings', json={'context': {'cache_input_budget_tokens': None}})
        assert reset.status_code == 200
        assert reset.json()['context_policy']['effective_input_budget_tokens'] == 257024
        assert load_config(tmp_path).cache_input_budget_tokens is None
        invalid = client.put('/api/settings', json={'context': {'cache_input_budget_tokens': 0}})
        assert invalid.status_code == 400
        assert runtime.config.cache_input_budget_tokens is None
        assert not runtime.store.requests()
        model.generate.assert_not_called()
        runtime.bot.call_api.assert_not_called()
        runtime.bot.send.assert_not_called()
    finally:
        client.close()
        asyncio.run(runtime.close())
