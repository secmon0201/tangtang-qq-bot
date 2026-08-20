from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment

nonebot.init()

from bot.plugins import today_wife


class _Event:
    def __init__(self, message: Message) -> None:
        self._message = message

    def get_plaintext(self) -> str:
        return self._message.extract_plain_text()

    def get_message(self) -> Message:
        return self._message


def _event(*segments: MessageSegment) -> _Event:
    return _Event(Message(list(segments)))


def test_force_command_parser_accepts_compact_and_spaced_mentions() -> None:
    compact = _event(MessageSegment.text("#强取"), MessageSegment.at("123"))
    spaced = _event(MessageSegment.text("#强取 "), MessageSegment.at("123"))

    assert today_wife._command(compact) == "#强取"
    assert today_wife._command(spaced) == "#强取"
    assert today_wife._mentioned_users(compact) == (123,)
    assert today_wife._command(_event(MessageSegment.text("#强取手册"))) == "#强取手册"


def test_force_command_rejects_non_numeric_or_multiple_targets_at_parser_boundary() -> None:
    all_target = _event(MessageSegment.text("#强取"), MessageSegment.at("all"))
    multiple = _event(
        MessageSegment.text("#强取"),
        MessageSegment.at("123"),
        MessageSegment.at("456"),
    )

    assert today_wife._mentioned_users(all_target) == ()
    assert len(today_wife._mentioned_users(multiple)) == 2
    assert "用法" in today_wife._force_message("missing")
    assert "一次只能" in today_wife._force_message("multiple")


def test_force_target_rejects_mixed_all_and_member_mentions() -> None:
    mixed = _event(
        MessageSegment.text("#强取"),
        MessageSegment.at("all"),
        MessageSegment.at("123"),
    )

    assert today_wife._force_target_id(mixed) is None


def test_interaction_target_rejects_all_self_and_multiple_mentions() -> None:
    all_target = _event(MessageSegment.text("#互动 靠近"), MessageSegment.at("all"))
    self_target = _event(MessageSegment.text("#互动 靠近"), MessageSegment.at("123"))
    multiple = _event(
        MessageSegment.text("#互动 助攻"),
        MessageSegment.at("123"),
        MessageSegment.at("456"),
    )

    assert today_wife._interaction_target(all_target, 999) == (None, "invalid")
    assert today_wife._interaction_target(self_target, 123) == (None, "self")
    assert today_wife._interaction_target(multiple, 999) == (None, "multiple")
