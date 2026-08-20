import json
import os

from bot.config import Settings
from bot.openapi import configure_official_environment, official_runtime


def _official_settings(monkeypatch) -> Settings:
    monkeypatch.setenv("BOT_TRANSPORT", "qq_openapi")
    monkeypatch.setenv("CODEX_COMPLETION_NOTIFY_ENABLED", "false")
    monkeypatch.setenv("CODEX_WORKER_ENABLED", "false")
    monkeypatch.setenv("QQ_OPENAPI_APP_ID", "1903484661")
    monkeypatch.setenv("QQ_OPENAPI_TOKEN", "test-token")
    monkeypatch.setenv("QQ_OPENAPI_APP_SECRET", "test-secret")
    monkeypatch.setenv("QQ_OPENAPI_SANDBOX", "true")
    monkeypatch.setenv("QQ_OPENAPI_PORT", "8081")
    return Settings.from_env()


def test_official_runtime_exposes_no_secret(monkeypatch):
    runtime = official_runtime(_official_settings(monkeypatch))
    assert runtime.app_id == "1903484661"
    assert runtime.sandbox is True
    assert not hasattr(runtime, "token")
    assert not hasattr(runtime, "secret")


def test_official_environment_builds_required_adapter_config(monkeypatch):
    settings = _official_settings(monkeypatch)
    runtime = configure_official_environment(settings)
    payload = json.loads(os.environ["QQ_BOTS"])
    assert runtime.port == 8081
    assert os.environ["DRIVER"] == "~fastapi+~httpx+~websockets"
    assert os.environ["QQ_IS_SANDBOX"] == "true"
    assert payload[0]["id"] == "1903484661"
    assert payload[0]["intent"]["c2c_group_at_messages"] is True
