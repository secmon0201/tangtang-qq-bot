import asyncio
from dataclasses import replace
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from tangtang_harness.app import create_app
from tangtang_harness.config import HarnessConfig, save_config
from tangtang_harness.onebot import wire_message
from tangtang_harness.runtime import Runtime


class NotificationBot:
    self_id = 999
    connected = True

    def __init__(self):
        self.calls = []
        self.fail_forward = False

    async def call_api(self, action, **params):
        self.calls.append((action, params))
        if action == "get_group_member_list":
            return [{"user_id": 103, "nickname": "昵称", "card": " 群名片 "},
                    {"user_id": 101, "nickname": "甲"}, {"user_id": 103, "nickname": "重复"},
                    {"user_id": 102, "nickname": "", "nick": "乙"}]
        if action == "send_group_forward_msg" and self.fail_forward:
            raise RuntimeError("synthetic forward failure")
        if action in {"send_group_forward_msg", "send_group_msg"}:
            return {"message_id": 700 + len(self.calls)}
        raise AssertionError("unexpected external API " + action)

    async def send(self, event, messages):
        return await self.call_api("send_group_msg", group_id=event.group_id, message=wire_message(messages))

    def detach(self):
        self.connected = False


@pytest.fixture
def notification_app(tmp_path, monkeypatch):
    bot = NotificationBot()
    runtime = Runtime(HarnessConfig(root=tmp_path, mode="live", group_ids=(201,),
        background_enabled=False, extra={"isolated_scope_enabled": True}), bot=bot)
    runtime.store.set_setting("notifications_enabled", True)
    runtime.store.set_setting("notification_group_id", 201)
    runtime.store.set_setting("notification_operator_id", 101)
    runtime.store.set_setting("notification_token_env", "HARNESS_NOTIFICATION_TOKEN")
    monkeypatch.setenv("HARNESS_NOTIFICATION_TOKEN", "synthetic-notice-token")
    client = TestClient(create_app(runtime=runtime))
    yield runtime, bot, client, {"X-Codex-Completion-Token": "synthetic-notice-token"}
    client.close()
    asyncio.run(runtime.close())


def test_completion_folded_pages_fixed_operator_and_platform_receipts(notification_app):
    runtime, bot, client, headers = notification_app
    details = "测试结果。\n" * 1001
    response = client.post("/internal/codex/completion", headers=headers,
                           json={"message": "任务完成", "details": details, "group_id": 888, "user_id": 888})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert [action for action, _ in bot.calls] == ["send_group_forward_msg", "send_group_msg"]
    assert all(params["group_id"] == 201 for _, params in bot.calls)
    pages = bot.calls[0][1]["messages"]
    text_pages = [node["data"]["content"][0]["data"]["text"] for node in pages]
    assert "".join(text_pages) == details.strip()
    assert all(len(text) <= 2000 for text in text_pages)
    assert all(node["data"]["uin"] == "999" for node in pages)
    assert bot.calls[1][1]["message"] == [{"type": "at", "data": {"qq": "101"}},
                                        {"type": "text", "data": {"text": " 任务完成"}}]
    with runtime.store.connect() as connection:
        deliveries = [dict(row) for row in connection.execute("SELECT * FROM deliveries")]
    assert len(deliveries) == 2 and all(row["outcome"] == "delivered" for row in deliveries)
    assert not runtime.store.requests()


@pytest.mark.parametrize("kind,label", [("test-case", "测试用例"), ("completion", "完成通知"), ("notice", "通知")])
def test_text_and_image_notice_preserves_folded_nodes_and_order(notification_app, kind, label):
    runtime, bot, client, headers = notification_app
    image = runtime.config.root / "notice.png"
    Image.new("RGB", (4, 4), "red").save(image)
    response = client.post("/internal/codex/test-group-notice", headers=headers,
                           json={"kind": kind, "title": "合成标题", "body": "正文", "image_paths": [str(image)]})
    assert response.status_code == 200
    nodes = bot.calls[0][1]["messages"]
    assert nodes[0]["data"]["content"] == [{"type": "text", "data": {"text": f"【{label}】\n主题：合成标题\n\n正文"}}]
    assert nodes[1]["data"]["content"] == [{"type": "image", "data": {"file": image.as_uri()}}]
    assert bot.calls[1][1]["message"][0] == {"type": "at", "data": {"qq": "101"}}
    assert f"{label}已发送：合成标题。" in bot.calls[1][1]["message"][1]["data"]["text"]
    assert not runtime.store.requests()


def test_fixed_group_member_lookup_uses_live_card_and_returns_display_fields(notification_app):
    _, bot, client, headers = notification_app
    response = client.post("/internal/codex/test-group-members", headers=headers, json={"group_id": 888})
    assert response.status_code == 200
    assert response.json() == {"members": [{"user_id": 101, "nickname": "甲"},
                                          {"user_id": 102, "nickname": "乙"},
                                          {"user_id": 103, "nickname": "群名片"}]}
    assert bot.calls == [("get_group_member_list", {"group_id": 201, "no_cache": True})]


def test_disabled_notifications_and_invalid_token_do_not_call_qq(notification_app):
    runtime, bot, client, headers = notification_app
    paths_and_payloads = (("/internal/codex/completion", {}),
        ("/internal/codex/test-group-notice", {"title": "合成通知", "body": "正文"}),
        ("/internal/codex/test-group-members", {}))
    for path, payload in paths_and_payloads:
        assert client.post(path, json=payload).status_code == 401
    runtime.store.set_setting("notifications_enabled", False)
    for path, payload in (*paths_and_payloads, ("/api/notifications", {"text": "旧入口"})):
        assert client.post(path, json=payload, headers=headers).status_code == 404
    assert bot.calls == []


@pytest.mark.parametrize("path,payload", [
    ("completion", {"message": "长" * 501}),
    ("completion", {"details": "长" * 24001}),
    ("test-group-notice", {"title": "长" * 121, "body": "正文"}),
    ("test-group-notice", {"title": "标题", "body": "长" * 24000}),
    ("test-group-notice", {"title": "标题", "body": "正文", "image_paths": ["none.png"] * 10}),
    ("test-group-notice", {"title": "标题", "body": "正文", "image_paths": ["../outside.png"]}),
    ("test-group-notice", {"kind": "other", "title": "标题", "body": "正文"}),
])
def test_old_notification_payload_limits_prevent_partial_delivery(notification_app, path, payload):
    _, bot, client, headers = notification_app
    assert client.post("/internal/codex/" + path, headers=headers, json=payload).status_code == 422
    assert bot.calls == []


def test_notice_delivery_failure_stays_in_backend_without_success_mention(notification_app):
    runtime, bot, client, headers = notification_app
    bot.fail_forward = True
    publications = []
    runtime.publish = lambda kind, value: publications.append((kind, value))
    response = client.post("/internal/codex/completion", headers=headers,
                           json={"message": "完成", "details": "折叠结果"})
    assert response.status_code == 503
    assert [action for action, _ in bot.calls] == ["send_group_forward_msg"]
    assert publications == [("notification", {"status": "failed", "error": "RuntimeError: synthetic forward failure"})]


def test_existing_notification_api_remains_fixed_target_and_observe_mode_writes_nothing(notification_app):
    runtime, bot, client, _ = notification_app
    response = client.post("/api/notifications", json={"text": "本地通知", "group_id": 888})
    assert response.status_code == 200
    assert bot.calls[0][1]["group_id"] == 201
    runtime.config = replace(runtime.config, mode="observe")
    bot.calls.clear()
    assert client.post("/api/notifications", json={"text": "观察模式"}).status_code == 503
    assert bot.calls == []


def test_harness_notice_client_uses_its_own_port_store_and_env(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "notify.py"
    specification = importlib.util.spec_from_file_location("harness_notice_script", script)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    config = HarnessConfig(root=tmp_path, port=8090)
    save_config(config)
    store = module.Store(tmp_path)
    store.set_setting("notifications_enabled", True)
    (tmp_path / ".env").write_text("HARNESS_NOTIFICATION_TOKEN=synthetic-client-token\n", encoding="utf-8")
    monkeypatch.delenv("HARNESS_NOTIFICATION_TOKEN", raising=False)
    calls = []
    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {"ok": True}
    def post(url, **params):
        calls.append((url, params))
        return Response()
    monkeypatch.setattr(module.httpx, "post", post)
    assert module.main(["--root", str(tmp_path), "completion", "合成完成", "--details", "细节"]) == 0
    assert calls[0][0] == "http://127.0.0.1:8090/internal/codex/completion"
    assert calls[0][1]["headers"] == {"X-Codex-Completion-Token": "synthetic-client-token"}
    assert calls[0][1]["json"] == {"message": "合成完成", "details": "细节"}
    calls.clear()
    assert module.main(["--root", str(tmp_path), "notice", "--title", "标题", "--body", "正文", "--dry-run"]) == 0
    assert calls == []
