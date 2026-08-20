from __future__ import annotations

import pytest

from bot.services.tangtang_profile import (
    ProfileRunGate,
    TangtangProfileConfig,
    TangtangProfileConfigLoader,
    TangtangProfileProvider,
)


def _values(**overrides) -> dict[str, str]:
    values = {
        "TANGTANG_ENABLED": "true",
        "TANGTANG_API_URL": "https://api.deepseek.com",
        "TANGTANG_API_KEY": "test-only",
        "TANGTANG_API_STYLE": "responses",
        "TANGTANG_MODEL": "deepseek-v4-flash",
        "TANGTANG_REASONING_EFFORT": "max",
    }
    values.update(overrides)
    return values


def _install_mock_client(monkeypatch, handler) -> None:
    import httpx

    from bot.services import tangtang_profile as profile_module

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler), *args, **kwargs)

    monkeypatch.setattr(profile_module.httpx, "AsyncClient", factory)


def test_profile_defaults_are_generous_and_match_soft_hard_caps():
    config = TangtangProfileConfig.from_values(_values())
    assert config.enabled is True
    assert config.max_input_chars == 20000
    assert config.max_output_tokens == 16000
    assert config.max_response_chars == 16000
    assert config.chunk_chars == 16000
    assert config.max_records_per_run == 100000
    assert config.max_concurrent == 3
    assert config.retry_max_attempts == 3
    assert config.retry_base_seconds == 2.0
    assert config.merge_chunk_chars == 12000
    assert config.evidence_concurrency == 5
    assert config.evidence_reasoning_effort == "low"
    assert config.evidence_max_chars == 4000
    assert config.evidence_max_tokens == 8000
    assert config.evidence_response_chars == 16000
    assert config.final_max_chars == 4000
    assert config.final_max_tokens == 8000
    assert config.final_response_chars == 16000
    # soft limits must stay well inside hard limits
    assert config.evidence_max_chars < config.evidence_max_tokens
    assert config.evidence_max_chars < config.evidence_response_chars
    assert config.final_max_chars < config.final_max_tokens
    assert config.final_max_chars < config.final_response_chars


def test_profile_stage_knobs_are_configurable():
    config = TangtangProfileConfig.from_values(
        _values(
            PROFILE_CHUNK_CHARS="15000",
            PROFILE_MAX_RECORDS_PER_RUN="200000",
            PROFILE_MAX_CONCURRENT="5",
            PROFILE_RETRY_MAX_ATTEMPTS="5",
            PROFILE_RETRY_BASE_SECONDS="4",
            PROFILE_MERGE_CHUNK_CHARS="15000",
            PROFILE_EVIDENCE_CONCURRENCY="8",
            PROFILE_EVIDENCE_REASONING_EFFORT="none",
            PROFILE_EVIDENCE_MAX_CHARS="1200",
            PROFILE_EVIDENCE_MAX_TOKENS="3000",
            PROFILE_EVIDENCE_MAX_RESPONSE_CHARS="6000",
            PROFILE_FINAL_MAX_CHARS="3000",
            PROFILE_FINAL_MAX_TOKENS="6000",
            PROFILE_FINAL_MAX_RESPONSE_CHARS="8000",
        )
    )
    assert config.chunk_chars == 15000
    assert config.max_records_per_run == 200000
    assert config.max_concurrent == 5
    assert config.retry_max_attempts == 5
    assert config.retry_base_seconds == 4.0
    assert config.merge_chunk_chars == 15000
    assert config.evidence_concurrency == 8
    assert config.evidence_reasoning_effort == "none"
    assert config.evidence_max_chars == 1200
    assert config.evidence_max_tokens == 3000
    assert config.evidence_response_chars == 6000
    assert config.final_max_chars == 3000
    assert config.final_max_tokens == 6000
    assert config.final_response_chars == 8000


def test_profile_falls_back_to_tangtang_api_settings():
    config = TangtangProfileConfig.from_values(
        {
            "TANGTANG_ENABLED": "true",
            "TANGTANG_API_URL": "https://api.deepseek.com",
            "TANGTANG_API_KEY": "test-only",
            "TANGTANG_MODEL": "deepseek-v4-flash",
            "TANGTANG_REASONING_EFFORT": "high",
        }
    )
    assert config.api_url == "https://api.deepseek.com"
    assert config.api_key == "test-only"
    assert config.model == "deepseek-v4-flash"
    assert config.reasoning_effort == "high"


def test_profile_rejects_out_of_range_stage_values():
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(_values(PROFILE_CHUNK_CHARS="999"))
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(_values(PROFILE_EVIDENCE_MAX_TOKENS="10"))
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(_values(PROFILE_FINAL_MAX_CHARS="99999"))
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(_values(PROFILE_MAX_CONCURRENT="0"))
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(_values(PROFILE_MAX_INPUT_CHARS="65000"))
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(_values(PROFILE_EVIDENCE_MAX_CHARS="12001"))
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(_values(PROFILE_RETRY_MAX_ATTEMPTS="0"))
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(
            _values(PROFILE_MAX_INPUT_CHARS="12000", PROFILE_CHUNK_CHARS="12000")
        )
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(
            _values(PROFILE_MAX_INPUT_CHARS="12000", PROFILE_MERGE_CHUNK_CHARS="12000")
        )
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(_values(PROFILE_EVIDENCE_CONCURRENCY="0"))
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(
            _values(PROFILE_EVIDENCE_REASONING_EFFORT="medium")
        )
    with pytest.raises(ValueError):
        TangtangProfileConfig.from_values(_values(PROFILE_REASONING_EFFORT="medium"))


def test_profile_accepts_wider_limits_within_1m_context():
    config = TangtangProfileConfig.from_values(
        _values(
            PROFILE_MAX_INPUT_CHARS="64000",
            PROFILE_CHUNK_CHARS="48000",
            PROFILE_MERGE_CHUNK_CHARS="48000",
            PROFILE_EVIDENCE_MAX_CHARS="8000",
            PROFILE_EVIDENCE_MAX_TOKENS="16000",
            PROFILE_EVIDENCE_MAX_RESPONSE_CHARS="16000",
        )
    )
    assert config.max_input_chars == 64000
    assert config.chunk_chars == 48000
    assert config.merge_chunk_chars == 48000
    assert config.evidence_max_chars == 8000


def test_profile_generate_retries_429_then_succeeds(monkeypatch):
    import asyncio

    import httpx

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(
                429, json={"error": {"message": "busy"}}, headers={"retry-after": "0"}
            )
        return httpx.Response(
            200,
            json={
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "完成"}]}
                ]
            },
        )

    _install_mock_client(monkeypatch, handler)
    config = TangtangProfileConfig.from_values(
        _values(PROFILE_RETRY_MAX_ATTEMPTS="3", PROFILE_RETRY_BASE_SECONDS="0")
    )
    text = asyncio.run(TangtangProfileProvider().generate(config, "system", "user"))
    assert text == "完成"
    assert calls["count"] == 3


def test_profile_generate_gives_up_after_retries(monkeypatch):
    import asyncio

    import httpx

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            429, json={"error": {"message": "busy"}}, headers={"retry-after": "0"}
        )

    _install_mock_client(monkeypatch, handler)
    config = TangtangProfileConfig.from_values(
        _values(PROFILE_RETRY_MAX_ATTEMPTS="3", PROFILE_RETRY_BASE_SECONDS="0")
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(TangtangProfileProvider().generate(config, "system", "user"))
    assert calls["count"] == 3


def test_profile_generate_does_not_retry_non_retryable_status(monkeypatch):
    import asyncio

    import httpx

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    _install_mock_client(monkeypatch, handler)
    config = TangtangProfileConfig.from_values(
        _values(PROFILE_RETRY_MAX_ATTEMPTS="3", PROFILE_RETRY_BASE_SECONDS="0")
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(TangtangProfileProvider().generate(config, "system", "user"))
    assert calls["count"] == 1


def test_profile_payloads_carry_reasoning_and_token_caps():
    config = TangtangProfileConfig.from_values(
        _values(PROFILE_REASONING_EFFORT="max", PROFILE_MAX_OUTPUT_TOKENS="16000")
    )
    responses = TangtangProfileProvider._payload(config, "system", "user")
    assert responses["reasoning"] == {"effort": "max"}
    assert responses["max_output_tokens"] == 16000

    chat_config = TangtangProfileConfig.from_values(
        _values(PROFILE_API_STYLE="chat_completions", PROFILE_REASONING_EFFORT="low")
    )
    chat = TangtangProfileProvider._payload(chat_config, "system", "user")
    assert chat["thinking"] == {"type": "enabled"}
    assert chat["reasoning_effort"] == "low"
    assert chat["max_tokens"] == 16000

    evidence_payload = TangtangProfileProvider._payload(
        config, "system", "user", reasoning_effort="low"
    )
    assert evidence_payload["reasoning"] == {"effort": "low"}


def test_profile_loader_hot_reloads(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TANGTANG_ENABLED=true\n"
        "TANGTANG_API_URL=https://api.deepseek.com\n"
        "TANGTANG_API_KEY=test-only\n"
        "TANGTANG_MODEL=deepseek-v4-flash\n"
        "PROFILE_CHUNK_CHARS=12000\n",
        encoding="utf-8",
    )
    loader = TangtangProfileConfigLoader(env_path)
    assert loader.load().chunk_chars == 12000
    env_path.write_text(
        "TANGTANG_ENABLED=true\n"
        "TANGTANG_API_URL=https://api.deepseek.com\n"
        "TANGTANG_API_KEY=test-only\n"
        "TANGTANG_MODEL=deepseek-v4-flash\n"
        "PROFILE_CHUNK_CHARS=18000\n",
        encoding="utf-8",
    )
    assert loader.load().chunk_chars == 18000


def test_profile_run_gate_blocks_duplicate_user_and_queues_by_limit():
    import asyncio

    async def scenario():
        gate = ProfileRunGate()
        assert await gate.acquire(7, 3) is True
        assert await gate.acquire(7, 3) is False
        assert gate.is_user_pending(7) is True
        assert await gate.acquire(8, 3) is True
        assert await gate.acquire(9, 3) is True
        acquired: list[bool] = []
        task = asyncio.create_task(_acquire_into(gate, 10, 3, acquired))
        await asyncio.sleep(0)
        assert gate.queue_depth == 1
        gate.release(8)
        await asyncio.sleep(0)
        assert acquired == [True]
        assert gate.active_count == 3
        assert gate.is_user_pending(10) is True
        gate.release(7)
        gate.release(9)
        gate.release(10)
        await task
        assert gate.active_count == 0
        assert not gate.is_user_pending(7)

    asyncio.run(scenario())


def test_profile_run_gate_fifo_wakeup():
    import asyncio

    async def scenario():
        gate = ProfileRunGate()
        assert await gate.acquire(1, 1) is True
        order: list[int] = []
        task_2 = asyncio.create_task(_wait_and_record(gate, 2, order))
        task_3 = asyncio.create_task(_wait_and_record(gate, 3, order))
        await asyncio.sleep(0)
        gate.release(1)
        await asyncio.sleep(0)
        gate.release(2)
        await asyncio.sleep(0)
        gate.release(3)
        await task_2
        await task_3
        assert order == [2, 3]

    asyncio.run(scenario())


async def _acquire_into(
    gate: ProfileRunGate, user_id: int, limit: int, out: list[bool]
) -> None:
    out.append(await gate.acquire(user_id, limit))


async def _wait_and_record(gate: ProfileRunGate, user_id: int, order: list[int]) -> None:
    await gate.acquire(user_id, 1)
    order.append(user_id)
