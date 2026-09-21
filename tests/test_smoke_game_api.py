from scripts.smoke_game_api import _has_https_login_link


def test_login_link_accepts_configured_https_domain_without_exposing_it() -> None:
    texts = (
        "请打开 https://login.example.invalid/waves/i/token-123 完成登录",
        "备用说明",
    )

    assert _has_https_login_link(texts, "/waves/i/")
    assert not _has_https_login_link(texts, "/nte/i/")


def test_login_link_rejects_insecure_or_malformed_urls() -> None:
    texts = (
        "http://login.example.invalid/waves/i/token-123",
        "https:///waves/i/token-123",
        "https://login.example.invalid/other/token-123",
    )

    assert not _has_https_login_link(texts, "/waves/i/")
