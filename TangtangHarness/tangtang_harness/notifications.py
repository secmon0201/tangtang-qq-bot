"""Fixed-test-group engineering notices using Harness delivery and receipts."""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import time
from typing import Literal
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from .help_content import build_forward_nodes
from .onebot import Message, MessageSegment
from .types import InboundEvent


DEFAULT_COMPLETION_MESSAGE = "Codex 已执行完成。"
KIND_LABELS = {"test-case": "测试用例", "completion": "完成通知", "notice": "通知"}
MAX_DETAILS_LENGTH = 24_000
PAGE_CHARS = 2_000
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class CompletionPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    message: str = Field(default=DEFAULT_COMPLETION_MESSAGE, max_length=500)
    details: str | None = Field(default=None, max_length=MAX_DETAILS_LENGTH)


class TestGroupPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    kind: Literal["test-case", "completion", "notice"] = "notice"
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=MAX_DETAILS_LENGTH)
    image_paths: list[str] = Field(default_factory=list, max_length=9)


def detail_pages(text: str) -> list[str]:
    remaining = text.strip()
    pages = []
    while len(remaining) > PAGE_CHARS:
        window = remaining[:PAGE_CHARS]
        split_at = max(window.rfind("\n"), window.rfind("。")) + 1
        if split_at <= PAGE_CHARS // 2:
            split_at = PAGE_CHARS
        pages.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        pages.append(remaining)
    return pages


def normalize_members(response) -> list[dict]:
    rows = response["data"] if isinstance(response, dict) else response
    members = {}
    for row in rows:
        user_id = int(row["user_id"])
        if user_id > 0:
            nickname = str(row.get("card") or row.get("nickname") or row.get("nick") or "群友").strip()
            members.setdefault(user_id, nickname[:40] or "群友")
    return [{"user_id": user_id, "nickname": nickname} for user_id, nickname in sorted(members.items())]


class NotificationService:
    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def enabled(self) -> bool:
        return bool(self.runtime.store.get_setting("notifications_enabled", False))

    def authorized(self, provided: str) -> bool:
        env_name = self.runtime.store.get_setting("notification_token_env", "HARNESS_NOTIFICATION_TOKEN")
        token = os.environ.get(env_name, "")
        return bool(token) and hmac.compare_digest(provided, token)

    def event(self) -> InboundEvent:
        runtime = self.runtime
        group_id = int(runtime.store.get_setting("notification_group_id", 0))
        if not group_id or not runtime.accepts_background(group_id):
            raise RuntimeError("Harness 通知群尚未接入当前运行范围或 QQ 未连接")
        return InboundEvent("notification:" + uuid4().hex, runtime.bot.self_id,
                            runtime.bot.self_id, group_id, "工程通知", timestamp=time.time())

    def mention(self, text: str) -> Message:
        operator = int(self.runtime.store.get_setting("notification_operator_id", 0))
        if not operator:
            raise RuntimeError("Harness 工程通知的固定管理员尚未配置")
        return MessageSegment.at(operator) + f" {text}"

    def image(self, raw_path: str) -> Path:
        root = self.runtime.config.root.resolve()
        path = (root / raw_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("通知图片必须位于 Harness 新目录")
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            raise ValueError("通知图片必须是已存在的 PNG、JPG、JPEG、GIF 或 WebP 文件")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("通知图片最大 20 MiB")
        return path

    async def completion(self, payload: CompletionPayload) -> dict:
        event = self.event()
        mention = self.mention(payload.message or DEFAULT_COMPLETION_MESSAGE)
        if payload.details:
            pages = detail_pages(payload.details)
            nodes = build_forward_nodes(pages, [None] * len(pages), event.self_id, title="Codex 执行结果")
            await self.runtime.deliver_forward(event, nodes)
        await self.runtime.deliver(event, mention)
        return {"ok": True}

    async def notice(self, payload: TestGroupPayload) -> dict:
        heading = f"【{KIND_LABELS[payload.kind]}】\n主题：{payload.title}"
        text = heading + ("\n\n" + payload.body if payload.body else "")
        if len(text) > MAX_DETAILS_LENGTH:
            raise ValueError(f"body must be text up to {MAX_DETAILS_LENGTH} characters")
        paths = [self.image(value) for value in payload.image_paths]
        if not payload.body and not paths:
            raise ValueError("body or image_paths is required")
        event = self.event()
        mention = self.mention(f"{KIND_LABELS[payload.kind]}已发送：{payload.title}。")
        pages = detail_pages(text)
        nodes = build_forward_nodes(pages + [payload.title] * len(paths),
                                    [None] * len(pages) + paths, event.self_id, title="测试群通知")
        await self.runtime.deliver_forward(event, nodes)
        await self.runtime.deliver(event, mention)
        return {"ok": True}

    async def members(self) -> dict:
        event = self.event()
        response = await self.runtime.bot.call_api("get_group_member_list", group_id=event.group_id, no_cache=True)
        return {"members": normalize_members(response)}

    async def plain(self, raw: dict) -> dict:
        event = self.event()
        message = Message(str(raw.get("text", "")))
        if raw.get("image_path"):
            message += MessageSegment.image(self.image(raw["image_path"]))
        ids = await self.runtime.deliver(event, message)
        return {"status": "delivered", "message_ids": ids}

    async def run(self, operation):
        try:
            return await operation
        except ValueError:
            raise
        except Exception as exc:
            logger.error("Harness engineering notification failed: {}: {}", type(exc).__name__, exc)
            self.runtime.publish("notification", {"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            raise
