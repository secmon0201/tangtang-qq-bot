from datetime import datetime
from types import SimpleNamespace

from bot.plugins.scope import (
    is_bot_mentioned,
    is_disabled_game_command,
    is_feature_group,
    is_activity_command,
    is_managed_group,
    is_participation_command,
    is_unmentioned_activity_command,
)
from bot.config import settings
from bot.services.roles import UserRole
from bot.services.zhijiang_live_guard import LiveSchedule


def test_external_game_commands_are_not_gated_by_the_mini_game_switch():
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = settings.managed_group_ids[0]
        user_id = 7

        def get_plaintext(self):
            return "#nte帮助"

    assert not scope.is_disabled_game_command(GroupEvent())


def test_game_menu_remains_available_while_gameplay_is_paused(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = settings.managed_group_ids[0]
        user_id = 7

        def get_plaintext(self):
            return "#游戏列表"

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    assert not scope.is_disabled_game_command(GroupEvent())


def test_game_total_rankings_are_blocked_in_a_closed_game_group(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = settings.managed_group_ids[0]
        user_id = 7

        def get_plaintext(self):
            return "#转盘总榜"

    class Scopes:
        def groups(self, feature):
            assert feature == "game"
            return frozenset()

        @staticmethod
        def is_game_globally_enabled():
            return True

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    monkeypatch.setattr(scope, "passive_settings", lambda: Scopes())
    assert scope.is_disabled_game_command(GroupEvent())


def test_today_wife_commands_are_independent_from_the_game_switch(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = settings.managed_group_ids[0]
        user_id = 7

        def get_plaintext(self):
            return "#群老婆"

    class Scopes:
        def groups(self, feature):
            assert feature == "game"
            return frozenset()

        @staticmethod
        def is_game_globally_enabled():
            return True

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    monkeypatch.setattr(scope, "passive_settings", lambda: Scopes())
    assert not scope.is_disabled_game_command(GroupEvent())


def test_today_wife_clear_command_is_independent_from_the_game_switch(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = settings.managed_group_ids[0]
        user_id = 7

        def get_plaintext(self):
            return "#清缘"

    class Scopes:
        def groups(self, feature):
            assert feature == "game"
            return frozenset()

        @staticmethod
        def is_game_globally_enabled():
            return False

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    monkeypatch.setattr(scope, "passive_settings", lambda: Scopes())
    assert not scope.is_disabled_game_command(GroupEvent())


def test_scope_accepts_only_configured_groups():
    assert is_managed_group(1067772451, (1067772451, 1128870029))
    assert not is_managed_group(999999999, (1067772451, 1128870029))


def test_scope_normalizes_numeric_group_ids():
    assert is_managed_group(1067772451, ("1067772451",))


def test_feature_scope_accepts_only_enabled_groups():
    assert is_feature_group(1067772451, (1067772451, 278824712))
    assert not is_feature_group(1128870029, (1067772451, 278824712))


def test_private_mini_game_commands_are_silently_intercepted():
    class PrivateEvent:
        def get_plaintext(self):
            return "#骰子"

    assert is_disabled_game_command(PrivateEvent())


def test_scope_requires_an_explicit_bot_mention():
    class Event:
        def __init__(self, mentioned: bool):
            self.mentioned = mentioned

        def is_tome(self):
            return self.mentioned

    assert is_bot_mentioned(Event(True))
    assert not is_bot_mentioned(Event(False))


def test_participation_commands_keep_the_fixed_reaction():
    class Event:
        def __init__(self, text: str):
            self.text = text

        def get_plaintext(self):
            return self.text

    assert is_participation_command(Event("报名 500"))
    assert is_participation_command(Event("报名500"))
    assert is_participation_command(Event("报名：500"))
    assert is_participation_command(Event("取消报名#500"))
    assert is_participation_command(Event("取消报名 500"))
    assert not is_participation_command(Event("报名名单 500"))
    assert not is_participation_command(Event("活动详情 500"))


def test_activity_commands_allow_compact_forms_only_by_prefix():
    class Event:
        def __init__(self, text: str):
            self.text = text

        def get_plaintext(self):
            return self.text

    assert is_activity_command(Event("报名517"))
    assert is_activity_command(Event("活动详情：517"))
    assert is_activity_command(Event("修改活动 517 | 新标题"))
    assert not is_activity_command(Event("我要报名517"))
    assert not is_activity_command(Event("活动大厅很好看"))


def test_private_activity_commands_are_allowed_without_a_mention():
    class PrivateEvent:
        def get_plaintext(self):
            return "报名 517"

    assert is_unmentioned_activity_command(PrivateEvent(), ())


def test_live_guard_reminder_mentions_a_normal_user_and_the_current_stream(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = settings.managed_group_ids[0]
        user_id = 7

        def get_plaintext(self):
            return "#装填"

    class Scopes:
        def groups(self, feature):
            assert feature == "game"
            return frozenset(settings.managed_group_ids)

        @staticmethod
        def is_game_globally_enabled():
            return False

    class Guard:
        @staticmethod
        def active_entries():
            return (
                LiveSchedule(
                    "diana",
                    datetime(2026, 7, 25, 20, 0),
                    "嘉然",
                    "测试直播",
                    "https://live.bilibili.com/22637261",
                    "日常",
                ),
            )

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    monkeypatch.setattr(scope, "passive_settings", lambda: Scopes())
    monkeypatch.setattr(scope, "zhijiang_live_guard", lambda: Guard())
    monkeypatch.setattr(scope, "user_role", lambda _user_id: UserRole.USER)

    message = scope.live_guard_mini_game_reminder(GroupEvent())

    assert message is not None
    assert "嘉然" in str(message)
    assert "qq=7" in str(message)


def test_live_guard_reminder_replies_to_operators_during_a_live_pause(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = settings.managed_group_ids[0]
        user_id = 7

        def get_plaintext(self):
            return "#装填"

    class Scopes:
        def groups(self, _feature):
            return frozenset(settings.managed_group_ids)

        @staticmethod
        def is_game_globally_enabled():
            return False

    class Guard:
        @staticmethod
        def active_entries():
            return (
                LiveSchedule(
                    "diana",
                    datetime(2026, 7, 25, 20, 0),
                    "嘉然",
                    "测试直播",
                    "https://live.bilibili.com/22637261",
                    "日常",
                ),
            )

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    monkeypatch.setattr(scope, "passive_settings", lambda: Scopes())
    monkeypatch.setattr(scope, "zhijiang_live_guard", lambda: Guard())
    monkeypatch.setattr(scope, "user_role", lambda _user_id: UserRole.SUPER_ADMIN)
    message = scope.live_guard_mini_game_reminder(GroupEvent())

    assert message is not None
    assert "嘉然" in str(message)
    assert "qq=7" not in str(message)


def test_live_guard_reminder_stays_silent_for_manual_game_closures(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = settings.managed_group_ids[0]
        user_id = 7

        def get_plaintext(self):
            return "#骰子"

    class Scopes:
        def groups(self, _feature):
            return frozenset(settings.managed_group_ids)

        @staticmethod
        def is_game_globally_enabled():
            return False

    class Guard:
        @staticmethod
        def active_entries():
            return ()

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    monkeypatch.setattr(scope, "passive_settings", lambda: Scopes())
    monkeypatch.setattr(scope, "zhijiang_live_guard", lambda: Guard())
    monkeypatch.setattr(scope, "user_role", lambda _user_id: UserRole.SUPER_ADMIN)

    assert scope.live_guard_mini_game_reminder(GroupEvent()) is None


def test_active_filter_silently_blocks_hash_commands_but_not_admin_filter_management(monkeypatch):
    import bot.plugins.scope as scope

    class Event:
        user_id = 7

        @staticmethod
        def get_plaintext():
            return "#装填"

    monkeypatch.setattr(
        scope,
        "database",
        lambda: SimpleNamespace(active_filter_contains=lambda user_id: user_id == 7),
    )
    monkeypatch.setattr(scope, "is_super_admin", lambda _user_id: False)
    assert scope.is_active_filtered_command(Event())

    Event.get_plaintext = staticmethod(lambda: "#主动过滤 列表")
    monkeypatch.setattr(scope, "is_super_admin", lambda user_id: user_id == 7)
    assert not scope.is_active_filtered_command(Event())
