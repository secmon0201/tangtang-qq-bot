import asyncio

from bot.services.qq_platform import QQPlatform, QQPlatformError, call_qq_action


class FakeBot:
    self_id = "999"

    def __init__(self):
        self.calls = []

    async def call_api(self, action, **params):
        self.calls.append((action, params))
        if action == "get_group_info":
            return {"data": {"group_id": params["group_id"], "group_name": "test"}}
        if action == "get_group_member_list":
            return {"data": [{"user_id": 999, "role": "owner"}]}
        return {"data": {"message_id": 1}}


def test_platform_owns_standard_onebot_actions():
    bot = FakeBot()
    platform = QQPlatform(bot)

    assert asyncio.run(platform.group_info(1001))["group_name"] == "test"
    assert asyncio.run(platform.member_list(1001))[0]["user_id"] == 999
    assert asyncio.run(platform.send_group_message(1001, "hello"))["message_id"] == 1
    assert [item[0] for item in bot.calls] == [
        "get_group_info",
        "get_group_member_list",
        "send_group_msg",
    ]


def test_platform_exposes_login_info_without_a_transport_specific_probe():
    class LoginBot(FakeBot):
        async def call_api(self, action, **params):
            if action == "get_login_info":
                return {"data": {"user_id": 999, "nickname": "test-bot"}}
            return await super().call_api(action, **params)

    assert asyncio.run(QQPlatform(LoginBot()).login_info()) == {
        "user_id": 999,
        "nickname": "test-bot",
    }


def test_platform_compatibility_bridge_still_uses_the_platform(monkeypatch):
    observed = []

    async def fake_request(self, action, **params):
        observed.append((action, params))
        return {"ok": True}

    monkeypatch.setattr(QQPlatform, "_request", fake_request)
    assert asyncio.run(call_qq_action(FakeBot(), "send_group_msg", group_id=1001)) == {"ok": True}
    assert observed == [("send_group_msg", {"group_id": 1001})]


def test_platform_normalizes_transport_failures():
    class BrokenBot:
        self_id = "999"

        async def call_api(self, _action, **_params):
            raise RuntimeError("offline")

    try:
        asyncio.run(QQPlatform(BrokenBot()).send_group_message(1001, "hello"))
    except QQPlatformError as exc:
        assert "send_group_msg failed" in str(exc)
    else:
        raise AssertionError("platform error was not raised")


def test_web_cookies_keeps_combined_cookie_and_bkn_response():
    class CombinedBot(FakeBot):
        async def call_api(self, action, **params):
            self.calls.append((action, params))
            assert action == "get_cookies"
            return {"data": {"cookies": "uin=o1; skey=test", "bkn": 123}}

    bot = CombinedBot()
    assert asyncio.run(QQPlatform(bot).web_cookies()) == {
        "cookies": "uin=o1; skey=test",
        "bkn": "123",
    }
    assert [action for action, _ in bot.calls] == ["get_cookies"]


def test_web_cookies_falls_back_to_separate_csrf_action():
    class SeparateBot(FakeBot):
        async def call_api(self, action, **params):
            self.calls.append((action, params))
            if action == "get_cookies":
                return {"data": {"cookies": "uin=o1; skey=test"}}
            if action == "get_csrf_token":
                return {"data": {"token": 456}}
            raise AssertionError(action)

    bot = SeparateBot()
    assert asyncio.run(QQPlatform(bot).web_cookies("qun.qq.com")) == {
        "cookies": "uin=o1; skey=test",
        "bkn": "456",
    }
    assert bot.calls == [
        ("get_cookies", {"domain": "qun.qq.com"}),
        ("get_csrf_token", {}),
    ]


def test_message_history_requests_older_messages_in_stable_order():
    class HistoryBot(FakeBot):
        async def call_api(self, action, **params):
            self.calls.append((action, params))
            return {"data": {"messages": [{"message_id": -42}]}}

    bot = HistoryBot()
    assert asyncio.run(QQPlatform(bot).message_history(1001, 50)) == [{"message_id": -42}]
    assert bot.calls == [
        (
            "get_group_msg_history",
            {"group_id": 1001, "count": 50, "reverse_order": True},
        )
    ]
