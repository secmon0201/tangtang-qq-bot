import asyncio

from bot.db import Database
from bot.services.gateway import OneBotGateway


class FakeBot:
    self_id = "999"

    async def call_api(self, action, **params):
        if action == "get_group_info":
            return {"data": {"group_id": params["group_id"], "group_name": "test"}}
        if action == "get_group_member_list":
            return {"data": [{"user_id": 999, "role": "owner"}]}
        if action == "get_group_member_info":
            return {"data": {"user_id": params["user_id"], "role": "admin"}}
        raise AssertionError(action)


def test_gateway_normalizes_wrapped_onebot_responses(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001,))
    gateway = OneBotGateway(FakeBot())

    failed = asyncio.run(gateway.sync_members((1001,), db))
    assert failed == []
    assert db.managed_groups()[0]["group_name"] == "test"
    assert asyncio.run(gateway.refresh_stats_capabilities((1001,), db)) == []
    assert db.group_stats_enabled(1001)


def test_gateway_marks_missing_bot_as_not_member_without_member_info_call(tmp_path):
    class MissingBot(FakeBot):
        async def call_api(self, action, **params):
            if action == "get_group_member_list":
                return {"data": [{"user_id": 123, "role": "member"}]}
            return await super().call_api(action, **params)

    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001,))
    gateway = OneBotGateway(MissingBot())

    assert asyncio.run(gateway.refresh_stats_capabilities((1001,), db)) == []
    with db.connect() as connection:
        row = connection.execute(
            "SELECT stats_enabled,stats_role FROM managed_groups WHERE group_id=1001"
        ).fetchone()
    assert row["stats_enabled"] == 0
    assert row["stats_role"] == "not_member"
