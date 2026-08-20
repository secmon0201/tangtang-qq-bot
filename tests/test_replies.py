import asyncio
from types import SimpleNamespace

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.matcher import Matcher, current_bot, current_event

from bot.services.replies import (
    _is_excluded_matcher,
    has_reply_segment,
    install_matcher_quote_replies,
    quote_message,
)


def test_quote_message_prefixes_a_native_onebot_reply_segment():
    quoted = quote_message(SimpleNamespace(message_id=12345), "收到")

    assert isinstance(quoted, Message)
    assert quoted[0].type == "reply"
    assert quoted[0].data["id"] == "12345"
    assert quoted.extract_plain_text() == "收到"


def test_quote_message_does_not_duplicate_an_existing_reply_segment():
    original = MessageSegment.reply(12345) + "已引用"

    quoted = quote_message(SimpleNamespace(message_id=67890), original)

    assert has_reply_segment(quoted)
    assert len([segment for segment in quoted if segment.type == "reply"]) == 1
    assert quoted[0].data["id"] == "12345"


def test_quote_exclusions_cover_statistics_and_genshinuid():
    class StatisticsMatcher:
        _tangtang_skip_quote = True

    class GenshinMatcher:
        plugin_name = "GenshinUID"

    assert _is_excluded_matcher(StatisticsMatcher)
    assert _is_excluded_matcher(GenshinMatcher)


def test_matcher_send_quotes_the_triggering_onebot_message():
    class CapturingBot:
        def __init__(self) -> None:
            self.sent: list[tuple[object, Message]] = []

        async def send(self, event: object, message: Message, **_kwargs: object) -> None:
            self.sent.append((event, message))

    event = GroupMessageEvent.model_validate(
        {
            "time": 0,
            "self_id": 1,
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 12345,
            "group_id": 1001,
            "user_id": 7,
            "message": "测试",
            "raw_message": "测试",
            "font": 0,
            "sender": {"user_id": 7, "nickname": "小夏", "card": ""},
        }
    )
    bot = CapturingBot()
    install_matcher_quote_replies()
    bot_token = current_bot.set(bot)  # type: ignore[arg-type]
    event_token = current_event.set(event)
    try:
        asyncio.run(Matcher.send("收到"))
    finally:
        current_event.reset(event_token)
        current_bot.reset(bot_token)

    sent_event, sent_message = bot.sent[0]
    assert sent_event is event
    assert sent_message[0].type == "reply"
    assert sent_message[0].data["id"] == "12345"
    assert sent_message.extract_plain_text() == "收到"
