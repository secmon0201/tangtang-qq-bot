import asyncio
from types import SimpleNamespace

import pytest

import bot.services.codex_completion as completion


def test_completion_message_mentions_only_the_configured_super_admin(monkeypatch):
    monkeypatch.setattr(
        completion,
        "settings",
        SimpleNamespace(codex_completion_notify_super_admin_id=595861835),
    )

    message = completion.completion_message("Codex 已执行完成。")

    assert "595861835" in str(message)
    assert "Codex 已执行完成。" in str(message)


def test_completion_notification_uses_the_fixed_group_and_onebot_action(monkeypatch):
    monkeypatch.setattr(
        completion,
        "settings",
        SimpleNamespace(
            codex_completion_notify_enabled=True,
            codex_completion_notify_group_id=1067772451,
            codex_completion_notify_super_admin_id=595861835,
        ),
    )
    calls = []

    async def fake_paced_call(bot, action, **params):
        calls.append((bot, action, params))
        return {"message_id": 1}

    monkeypatch.setattr(completion, "call_qq_action", fake_paced_call)
    bot = SimpleNamespace()

    result = asyncio.run(completion.notify_codex_completion(bot=bot))

    assert result == {"message_id": 1}
    assert calls[0][0] is bot
    assert calls[0][1] == "send_group_msg"
    assert calls[0][2]["group_id"] == 1067772451
    assert "595861835" in str(calls[0][2]["message"])


def test_completion_details_are_sent_as_a_folded_forward_before_the_mention(monkeypatch):
    monkeypatch.setattr(
        completion,
        "settings",
        SimpleNamespace(
            codex_completion_notify_enabled=True,
            codex_completion_notify_group_id=1067772451,
            codex_completion_notify_super_admin_id=595861835,
        ),
    )
    calls = []

    async def fake_paced_call(bot, action, **params):
        calls.append((bot, action, params))
        return {"message_id": len(calls)}

    monkeypatch.setattr(completion, "call_qq_action", fake_paced_call)
    bot = SimpleNamespace(self_id=3987707335)

    asyncio.run(
        completion.notify_codex_completion(
            "Codex 已执行完成，完整结果请展开合并转发查看。",
            details="已完成直播提醒文案调整。\n验证：21 passed。",
            bot=bot,
        )
    )

    assert [call[1] for call in calls] == ["send_group_forward_msg", "send_group_msg"]
    forward = calls[0][2]
    assert forward["group_id"] == 1067772451
    assert forward["messages"][0]["data"]["name"] == "Codex 执行结果 第1页"
    assert "已完成直播提醒文案调整。" in forward["messages"][0]["data"]["content"][0]["data"]["text"]
    assert "595861835" in str(calls[1][2]["message"])


def test_completion_details_preserve_all_content_across_forward_pages():
    text = "甲" * (completion.COMPLETION_FORWARD_PAGE_CHARS + 25)

    pages = completion.completion_detail_pages(text)

    assert "".join(pages) == text
    assert len(pages) == 2


def test_completion_notification_refuses_disabled_configuration(monkeypatch):
    monkeypatch.setattr(
        completion,
        "settings",
        SimpleNamespace(codex_completion_notify_enabled=False),
    )

    with pytest.raises(RuntimeError, match="disabled"):
        asyncio.run(completion.notify_codex_completion(bot=SimpleNamespace()))
