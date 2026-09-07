from nonebot.adapters.onebot.v11 import GroupMessageEvent

from types import SimpleNamespace
from time import time

from bot.plugins.random_reactions import (
    is_passive_reaction_event,
    is_reaction_filtered,
    is_stale_passive_event,
    repeatable_text,
    should_triple_repeat,
)


def group_message(
    *, group_id: int, to_me: bool = False, text: str = "test", timestamp: int | None = None
) -> GroupMessageEvent:
    return GroupMessageEvent.model_validate(
        {
            "time": int(time()) if timestamp is None else timestamp,
            "self_id": 2,
            "post_type": "message",
            "sub_type": "normal",
            "user_id": 3,
            "message_type": "group",
            "message_id": 4,
            "message": text,
            "original_message": text,
            "raw_message": text,
            "font": 14,
            "sender": {
                "user_id": 3,
                "nickname": "tester",
                "card": "",
                "sex": "unknown",
                "age": 0,
                "area": "",
                "level": "",
                "role": "member",
                "title": "",
            },
            "to_me": to_me,
            "group_id": group_id,
        }
    )


def test_passive_reaction_accepts_only_non_mention_messages_in_configured_groups(monkeypatch):
    monkeypatch.setattr("bot.plugins.random_reactions.automation_is_paused", lambda: False)
    monkeypatch.setattr(
        "bot.plugins.random_reactions.passive",
        SimpleNamespace(is_group_enabled=lambda group_id: group_id == 910000104),
    )

    assert is_passive_reaction_event(group_message(group_id=910000104))
    assert not is_passive_reaction_event(group_message(group_id=910000104, to_me=True))
    assert not is_passive_reaction_event(group_message(group_id=910000105))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#装填"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#枝江直播"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#白名单"))
    assert is_passive_reaction_event(group_message(group_id=910000104, text="报名 517"))
    assert is_passive_reaction_event(group_message(group_id=910000104, text="取消报名 517"))
    assert is_passive_reaction_event(group_message(group_id=910000104, text="活动详情 517"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#猜数"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#猜 123"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#游戏开"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#清游"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#确认"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#取消"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="# 开枪"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#丢给@群友"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="# 重投"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="#不投"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="nte帮助"))
    assert not is_passive_reaction_event(group_message(group_id=910000104, text="NTE角色列表"))


def test_filtered_member_is_detected_without_changing_the_blocking_matcher(monkeypatch):
    monkeypatch.setattr("bot.plugins.random_reactions.automation_is_paused", lambda: False)
    monkeypatch.setattr(
        "bot.plugins.random_reactions.passive",
        SimpleNamespace(is_group_enabled=lambda group_id: group_id == 910000104),
    )
    monkeypatch.setattr(
        "bot.plugins.random_reactions.db",
        SimpleNamespace(passive_filter_contains=lambda user_id: user_id == 3),
    )

    event = group_message(group_id=910000104)

    assert is_passive_reaction_event(event)
    assert is_reaction_filtered(event)


def test_passive_reactions_are_rejected_while_automation_is_paused(monkeypatch):
    monkeypatch.setattr("bot.plugins.random_reactions.automation_is_paused", lambda: True)
    monkeypatch.setattr(
        "bot.plugins.random_reactions.passive",
        SimpleNamespace(is_group_enabled=lambda group_id: group_id == 910000104),
    )

    assert not is_passive_reaction_event(group_message(group_id=910000104))


def test_repeatable_text_requires_a_short_plain_text_message():
    assert repeatable_text(group_message(group_id=910000104)) == "test"


def test_triple_repeat_probability_gate_uses_strict_less_than():
    assert should_triple_repeat(0.30, 0.0)
    assert should_triple_repeat(0.30, 0.299)
    assert not should_triple_repeat(0.30, 0.30)
    assert not should_triple_repeat(0.30, 0.99)


def test_stale_group_messages_are_not_eligible_for_passive_interactions():
    event = group_message(group_id=910000104, timestamp=1)

    assert is_stale_passive_event(event, now=122)


def test_codex_messages_are_not_eligible_for_passive_interactions(monkeypatch):
    monkeypatch.setattr(
        "bot.plugins.random_reactions.passive",
        SimpleNamespace(is_group_enabled=lambda group_id: group_id == 910000104),
    )
