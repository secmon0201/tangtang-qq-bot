import pytest

from bot.services.operator_web import OperatorWebSessions, operator_web_base_url, operator_web_url


def test_operator_web_sessions_are_scoped_and_expire():
    sessions = OperatorWebSessions()
    duplicate = sessions.create(100, "duplicate")
    assert sessions.get(duplicate.token, "duplicate") is duplicate
    assert sessions.get(duplicate.token, "operations") is None
    duplicate.expires_at = 0
    sessions.cleanup()
    assert sessions.get(duplicate.token) is None

    with pytest.raises(ValueError, match="unsupported operator web session"):
        sessions.create(100, "activity")


def test_operator_web_base_url_requires_https(monkeypatch, tmp_path):
    from bot.services import operator_web

    path = tmp_path / "operator_url.txt"
    monkeypatch.setattr(operator_web, "TUNNEL_URL_PATH", path)
    path.write_text("http://127.0.0.1:8080", encoding="utf-8")
    assert operator_web_base_url() is None
    path.write_text("https://example.trycloudflare.com/\n", encoding="utf-8")
    assert operator_web_base_url() == "https://example.trycloudflare.com"


def test_operator_web_urls_use_public_feature_paths():
    base = "https://tangtang.secmon.cn"

    assert operator_web_url(base, "duplicate", "token") == f"{base}/duplicate/token"
    with pytest.raises(ValueError, match="unsupported operator web session"):
        operator_web_url(base, "operations", "token")
