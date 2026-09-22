from datetime import datetime
from types import SimpleNamespace

import nonebot

nonebot.init()

from bot.plugins.scope import (
    disabled_feature_for,
    is_bot_mentioned,
    is_disabled_game_command,
    is_feature_group,
    is_managed_group,
    mention_chat_is_available,
)
from bot.services.roles import UserRole
from bot.services.zhijiang_live_guard import LiveSchedule

TEST_GROUP_ID = 1001


def test_legacy_a_coast_ranking_alias_uses_the_speech_ranking_switch(monkeypatch):
    import bot.plugins.scope as scope

    class Domains:
        @staticmethod
        def cluster_by_name_or_alias(name):
            assert name.casefold() in {"a海岸".casefold()}
            return object()

        @staticmethod
        def effective_feature_enabled(group_id, feature):
            assert group_id == 9001
            assert feature == "speech_ranking"
            return False

    monkeypatch.setattr(scope, "group_domains", lambda: Domains())

    assert disabled_feature_for("#A海岸发言排行 月", 9001) == "speech_ranking"
    assert disabled_feature_for("#a海岸统计", 9001) == "speech_ranking"


def test_external_game_commands_are_not_gated_by_the_mini_game_switch():
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = TEST_GROUP_ID
        user_id = 7

        def get_plaintext(self):
            return "#nte帮助"

    assert not scope.is_disabled_game_command(GroupEvent())


def test_enabled_game_commands_use_independent_group_feature_switches(monkeypatch):
    import bot.plugins.scope as scope

    class Domains:
        @staticmethod
        def effective_feature_enabled(group_id, feature):
            assert group_id == 9001
            return feature != "ww"

    monkeypatch.setattr(scope, "group_domains", lambda: Domains())

    assert disabled_feature_for("#nte帮助", 9001) is None
    assert disabled_feature_for("ww帮助", 9001) == "ww"


def test_game_menu_remains_available_while_gameplay_is_paused(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = TEST_GROUP_ID
        user_id = 7

        def get_plaintext(self):
            return "#游戏列表"

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    assert not scope.is_disabled_game_command(GroupEvent())


def test_domain_game_rankings_are_blocked_in_a_closed_game_group(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = TEST_GROUP_ID
        user_id = 7

        def get_plaintext(self):
            return "#转盘总榜"

    class Scopes:
        @staticmethod
        def is_game_globally_enabled():
            return True

    class Domains:
        @staticmethod
        def feature_enabled(group_id, feature):
            assert int(group_id) == TEST_GROUP_ID
            assert feature == "mini_games"
            return False

        @staticmethod
        def effective_feature_enabled(_group_id, feature):
            assert feature == "live_guard"
            return False

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    monkeypatch.setattr(scope, "passive_settings", lambda: Scopes())
    monkeypatch.setattr(scope, "group_domains", lambda: Domains())
    monkeypatch.setattr(scope, "database", lambda: SimpleNamespace(
        is_managed_group=lambda group_id: group_id == TEST_GROUP_ID))
    assert scope.is_disabled_game_command(GroupEvent())


def test_today_wife_commands_are_independent_from_the_game_switch(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = TEST_GROUP_ID
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
        group_id = TEST_GROUP_ID
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
    assert is_managed_group(920000001, (920000001, 910000101))
    assert not is_managed_group(999999999, (920000001, 910000101))


def test_scope_normalizes_numeric_group_ids():
    assert is_managed_group(920000001, ("920000001",))


def test_feature_scope_accepts_only_enabled_groups():
    assert is_feature_group(920000001, (920000001, 910000105))
    assert not is_feature_group(910000101, (920000001, 910000105))


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


def test_mention_chat_requires_global_and_group_switches(monkeypatch):
    import bot.plugins.scope as scope

    states = {"global": True, "group": True}
    monkeypatch.setattr(
        scope,
        "passive_settings",
        lambda: SimpleNamespace(
            is_chat_globally_enabled=lambda feature: (
                feature == "mention_chat" and states["global"]
            )
        ),
    )
    monkeypatch.setattr(
        scope,
        "group_domains",
        lambda: SimpleNamespace(
            feature_enabled=lambda _group_id, feature: (
                feature == "mention_chat" and states["group"]
            )
        ),
    )

    assert mention_chat_is_available(1001)
    states["global"] = False
    assert not mention_chat_is_available(1001)
    states["global"] = True
    states["group"] = False
    assert not mention_chat_is_available(1001)


def test_live_guard_reminder_mentions_a_normal_user_and_the_current_stream(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = TEST_GROUP_ID
        user_id = 7

        def get_plaintext(self):
            return "#装填"

    class Scopes:
        @staticmethod
        def is_game_globally_enabled():
            return True

    class Domains:
        @staticmethod
        def feature_enabled(_group_id, feature):
            return feature == "mini_games"

        @staticmethod
        def effective_feature_enabled(_group_id, feature):
            return feature == "live_guard"

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
    monkeypatch.setattr(scope, "group_domains", lambda: Domains())
    monkeypatch.setattr(scope, "zhijiang_live_guard", lambda: Guard())
    monkeypatch.setattr(scope, "user_role", lambda _user_id: UserRole.USER)

    message = scope.live_guard_mini_game_reminder(GroupEvent())

    assert message is not None
    assert "嘉然" in str(message)
    assert "qq=7" in str(message)


def test_live_guard_reminder_replies_to_operators_during_a_live_pause(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = TEST_GROUP_ID
        user_id = 7

        def get_plaintext(self):
            return "#装填"

    class Scopes:
        @staticmethod
        def is_game_globally_enabled():
            return True

    class Domains:
        @staticmethod
        def feature_enabled(_group_id, feature):
            return feature == "mini_games"

        @staticmethod
        def effective_feature_enabled(_group_id, feature):
            return feature == "live_guard"

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
    monkeypatch.setattr(scope, "group_domains", lambda: Domains())
    monkeypatch.setattr(scope, "zhijiang_live_guard", lambda: Guard())
    monkeypatch.setattr(scope, "user_role", lambda _user_id: UserRole.SUPER_ADMIN)
    message = scope.live_guard_mini_game_reminder(GroupEvent())

    assert message is not None
    assert "嘉然" in str(message)
    assert "qq=7" not in str(message)


def test_live_guard_reminder_stays_silent_for_manual_game_closures(monkeypatch):
    import bot.plugins.scope as scope

    class GroupEvent:
        group_id = TEST_GROUP_ID
        user_id = 7

        def get_plaintext(self):
            return "#骰子"

    class Scopes:
        @staticmethod
        def is_game_globally_enabled():
            return False

    class Domains:
        @staticmethod
        def feature_enabled(_group_id, feature):
            return feature == "mini_games"

        @staticmethod
        def effective_feature_enabled(_group_id, feature):
            return feature == "live_guard"

    class Guard:
        @staticmethod
        def active_entries():
            return ()

    monkeypatch.setattr(scope, "GroupMessageEvent", GroupEvent)
    monkeypatch.setattr(scope, "passive_settings", lambda: Scopes())
    monkeypatch.setattr(scope, "group_domains", lambda: Domains())
    monkeypatch.setattr(scope, "zhijiang_live_guard", lambda: Guard())
    monkeypatch.setattr(scope, "user_role", lambda _user_id: UserRole.SUPER_ADMIN)

    assert scope.live_guard_mini_game_reminder(GroupEvent()) is None
