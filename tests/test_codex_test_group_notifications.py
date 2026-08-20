import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.services.codex_completion as completion


def test_notification_parser_rejects_empty_delivery():
    with pytest.raises(ValueError, match="body or image_paths"):
        completion.parse_test_group_notification(
            {"kind": "notice", "title": "空通知", "body": "", "image_paths": []}
        )


def test_notification_parser_accounts_for_its_heading_in_the_text_limit():
    with pytest.raises(ValueError, match="body must be text"):
        completion.parse_test_group_notification(
            {
                "kind": "notice",
                "title": "标题",
                "body": "甲" * completion.MAX_COMPLETION_DETAILS_LENGTH,
            }
        )


def test_notification_parser_keeps_workspace_images(monkeypatch, tmp_path):
    image = tmp_path / "report.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(completion, "ROOT", tmp_path)

    notification = completion.parse_test_group_notification(
        {"kind": "test-case", "title": "登录冒烟", "body": "验证登录页。", "image_paths": [str(image)]}
    )

    assert notification.kind == "test-case"
    assert notification.image_paths == (image.resolve(),)
    nodes = completion.test_group_notification_nodes(notification, 10001)
    assert nodes[0]["data"]["content"][0]["data"]["text"].startswith("【测试用例】")
    assert nodes[1]["data"]["content"][0]["data"]["file"] == image.resolve().as_uri()


def test_notification_parser_rejects_images_outside_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external_image = tmp_path / "external.png"
    external_image.write_bytes(b"png")
    monkeypatch.setattr(completion, "ROOT", workspace)

    with pytest.raises(ValueError, match="inside the bot workspace"):
        completion.parse_test_group_notification(
            {"kind": "notice", "title": "图片", "image_paths": [str(external_image)]}
        )


def test_test_group_notification_forwards_content_then_mentions_operator(monkeypatch):
    monkeypatch.setattr(
        completion,
        "settings",
        SimpleNamespace(
            codex_completion_notify_enabled=True,
            codex_completion_notify_group_id=1001,
            codex_completion_notify_super_admin_id=99,
        ),
    )
    calls = []

    async def fake_paced_call(bot, action, **params):
        calls.append((action, params))
        return {"message_id": len(calls)}

    monkeypatch.setattr(completion, "call_qq_action", fake_paced_call)
    notification = completion.TestGroupNotification("completion", "登录模块", "24 passed", ())

    result = asyncio.run(completion.notify_test_group(notification, bot=SimpleNamespace(self_id=77)))

    assert result == {"message_id": 2}
    assert [action for action, _ in calls] == ["send_group_forward_msg", "send_group_msg"]
    assert calls[0][1]["group_id"] == 1001
    assert "【完成通知】" in calls[0][1]["messages"][0]["data"]["content"][0]["data"]["text"]
    assert "99" in str(calls[1][1]["message"])


def test_test_group_member_lookup_is_fixed_target_and_normalized(monkeypatch):
    monkeypatch.setattr(
        completion,
        "settings",
        SimpleNamespace(codex_completion_notify_enabled=True, codex_completion_notify_group_id=1001),
    )
    calls = []

    async def fake_paced_call(bot, action, **params):
        calls.append((action, params))
        return {
            "data": [
                {"user_id": 2, "nickname": "普通昵称", "card": "群名片"},
                {"user_id": "1", "nickname": "小明"},
                {"user_id": 2, "nickname": "重复成员"},
                {"user_id": "bad", "nickname": "无效成员"},
            ]
        }

    monkeypatch.setattr(completion, "call_qq_action", fake_paced_call)

    members = asyncio.run(completion.fetch_test_group_members(bot=SimpleNamespace()))

    assert members == [{"user_id": 1, "nickname": "小明"}, {"user_id": 2, "nickname": "群名片"}]
    assert calls == [("get_group_member_list", {"group_id": 1001, "no_cache": False})]
