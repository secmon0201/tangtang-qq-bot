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
