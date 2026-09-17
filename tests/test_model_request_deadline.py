import asyncio

import httpx
import pytest

from bot.services.tangtang_chat import TangtangConfig, TangtangProvider
from bot.services.tangtang_profile import TangtangProfileConfig, TangtangProfileProvider


@pytest.mark.parametrize("kind", ["chat", "agent", "profile"])
@pytest.mark.parametrize("timeout", [10, 120])
def test_model_requests_bound_proxy_work_to_client_timeout_and_thirty_seconds(monkeypatch, kind, timeout):
    values = {
        "TANGTANG_ENABLED": "true", "TANGTANG_MODE": "d",
        "TANGTANG_GROUP_IDS": "1001", "TANGTANG_API_URL": "http://127.0.0.1:3123/v1",
        "TANGTANG_API_KEY": "test-only", "TANGTANG_MODEL": "test-model",
        "TANGTANG_API_STYLE": "chat_completions",
        "TANGTANG_TIMEOUT_SECONDS": str(timeout), "PROFILE_TIMEOUT_SECONDS": str(timeout),
    }
    observed = []

    def handle(request):
        observed.append(request.headers.get("X-Request-Timeout-Ms"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    if kind == "profile":
        asyncio.run(TangtangProfileProvider().generate(TangtangProfileConfig.from_values(values), "system", "hello"))
    else:
        config = TangtangConfig.from_values(values, (1001,))
        method = TangtangProvider().generate_agent if kind == "agent" else TangtangProvider().generate
        asyncio.run(method(config, "system", "hello"))
    assert observed == [str(min(timeout, 30) * 1000)]
