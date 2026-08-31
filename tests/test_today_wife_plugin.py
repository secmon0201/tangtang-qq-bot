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


def test_removed_interaction_and_web_commands_are_not_registered() -> None:
    assert "#互动" not in today_wife.COMMANDS
    assert "#老婆网页" not in today_wife.COMMANDS
    assert "#缘分网页" not in today_wife.COMMANDS
    assert today_wife._command(_event(MessageSegment.text("#我的老婆 2"))) == "#我的老婆"


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
