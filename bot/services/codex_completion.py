from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonebot import get_bots
from nonebot.adapters.onebot.v11 import MessageSegment

from bot.config import ROOT, settings
from bot.services.forward import build_forward_nodes
from bot.services.qq_platform import call_qq_action


DEFAULT_COMPLETION_MESSAGE = "Codex 已执行完成。"
MAX_COMPLETION_MESSAGE_LENGTH = 500
MAX_COMPLETION_DETAILS_LENGTH = 24_000
COMPLETION_FORWARD_PAGE_CHARS = 2_000
COMPLETION_FORWARD_TITLE = "Codex 执行结果"
TEST_GROUP_FORWARD_TITLE = "测试群通知"
MAX_NOTIFICATION_TITLE_LENGTH = 120
MAX_NOTIFICATION_IMAGE_COUNT = 9
MAX_NOTIFICATION_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_NOTIFICATION_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
MAX_TEST_GROUP_MEMBER_COUNT = 5_000

NOTIFICATION_KIND_LABELS = {
    "test-case": "测试用例",
    "completion": "完成通知",
    "notice": "通知",
}


@dataclass(frozen=True, slots=True)
class TestGroupNotification:
    kind: str
    title: str
    body: str
    image_paths: tuple[Path, ...]


def completion_message(text: str | None = None) -> MessageSegment:
    """Build the fixed-target completion notification without exposing IDs to callers."""
    if settings.codex_completion_notify_super_admin_id is None:
        raise RuntimeError("Codex completion notification target is not configured")
    value = (text or DEFAULT_COMPLETION_MESSAGE).strip() or DEFAULT_COMPLETION_MESSAGE
    if len(value) > MAX_COMPLETION_MESSAGE_LENGTH:
        raise ValueError(f"completion message must not exceed {MAX_COMPLETION_MESSAGE_LENGTH} characters")
    return MessageSegment.at(settings.codex_completion_notify_super_admin_id) + f" {value}"


def completion_detail_pages(text: str) -> tuple[str, ...]:
    """Split the caller-provided final result into QQ forward-message pages."""
    value = text.strip()
    if not value:
        return ()
    if len(value) > MAX_COMPLETION_DETAILS_LENGTH:
        raise ValueError(f"completion details must not exceed {MAX_COMPLETION_DETAILS_LENGTH} characters")
    pages: list[str] = []
    remaining = value
    while len(remaining) > COMPLETION_FORWARD_PAGE_CHARS:
        window = remaining[: COMPLETION_FORWARD_PAGE_CHARS]
        split_at = max(window.rfind("\n"), window.rfind("。")) + 1
        if split_at <= COMPLETION_FORWARD_PAGE_CHARS // 2:
            split_at = COMPLETION_FORWARD_PAGE_CHARS
        pages.append(remaining[:split_at])
        remaining = remaining[split_at:]
    pages.append(remaining)
    return tuple(pages)


def completion_forward_nodes(text: str, bot_id: int | str) -> list[dict[str, Any]]:
    pages = completion_detail_pages(text)
    if not pages:
        return []
    return build_forward_nodes(
        pages,
        [None] * len(pages),
        bot_id,
        title=COMPLETION_FORWARD_TITLE,
    )


def parse_test_group_notification(payload: dict[str, Any]) -> TestGroupNotification:
    """Validate a fixed-target notification without accepting arbitrary destinations."""
    kind = payload.get("kind", "notice")
    if not isinstance(kind, str) or kind not in NOTIFICATION_KIND_LABELS:
        raise ValueError("kind must be test-case, completion, or notice")
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_NOTIFICATION_TITLE_LENGTH:
        raise ValueError(f"title must be text from 1 to {MAX_NOTIFICATION_TITLE_LENGTH} characters")
    body = payload.get("body", "")
    if not isinstance(body, str):
        raise ValueError(f"body must be text up to {MAX_COMPLETION_DETAILS_LENGTH} characters")
    normalized_body = body.strip()
    heading_length = len(NOTIFICATION_KIND_LABELS[kind]) + len(title.strip()) + 8
    if len(normalized_body) + heading_length > MAX_COMPLETION_DETAILS_LENGTH:
        raise ValueError(f"body must be text up to {MAX_COMPLETION_DETAILS_LENGTH} characters")
    raw_paths = payload.get("image_paths", [])
    if not isinstance(raw_paths, list) or len(raw_paths) > MAX_NOTIFICATION_IMAGE_COUNT:
        raise ValueError(f"image_paths must contain at most {MAX_NOTIFICATION_IMAGE_COUNT} items")

    image_paths: list[Path] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("image_paths must contain non-empty file paths")
        path = Path(raw_path).resolve()
        try:
            path.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise ValueError("image paths must be located inside the bot workspace") from exc
        if path.suffix.lower() not in ALLOWED_NOTIFICATION_IMAGE_SUFFIXES:
            allowed = ", ".join(sorted(ALLOWED_NOTIFICATION_IMAGE_SUFFIXES))
            raise ValueError(f"image path must use one of: {allowed}")
        if not path.is_file():
            raise ValueError(f"image file does not exist: {path}")
        if path.stat().st_size > MAX_NOTIFICATION_IMAGE_BYTES:
            raise ValueError(f"image file exceeds {MAX_NOTIFICATION_IMAGE_BYTES // (1024 * 1024)} MiB: {path}")
        image_paths.append(path)

    if not normalized_body and not image_paths:
        raise ValueError("body or image_paths is required")
    return TestGroupNotification(kind, title.strip(), normalized_body, tuple(image_paths))


def test_group_notification_nodes(
    notification: TestGroupNotification, bot_id: int | str
) -> list[dict[str, Any]]:
    heading = f"【{NOTIFICATION_KIND_LABELS[notification.kind]}】\n主题：{notification.title}"
    text = f"{heading}\n\n{notification.body}" if notification.body else heading
    pages = completion_detail_pages(text)
    messages = list(pages) + [notification.title] * len(notification.image_paths)
    paths: list[Path | None] = [None] * len(pages) + list(notification.image_paths)
    return build_forward_nodes(messages, paths, bot_id, title=TEST_GROUP_FORWARD_TITLE)


def _active_onebot() -> Any | None:
    return next(
        (candidate for candidate in get_bots().values() if hasattr(candidate, "call_api")),
        None,
    )


def normalize_test_group_members(response: Any) -> list[dict[str, int | str]]:
    """Keep only the display fields required for a one-off local graph render."""
    payload = response.get("data") if isinstance(response, dict) and "data" in response else response
    if not isinstance(payload, list):
        return []
    members: dict[int, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            user_id = int(item.get("user_id") or 0)
        except (TypeError, ValueError):
            continue
        if user_id <= 0:
            continue
        nickname = str(item.get("card") or item.get("nickname") or item.get("nick") or "群友").strip()
        members.setdefault(user_id, nickname[:40] or "群友")
    if len(members) > MAX_TEST_GROUP_MEMBER_COUNT:
        raise RuntimeError("test group member count exceeds the local rendering limit")
    return [{"user_id": user_id, "nickname": nickname} for user_id, nickname in sorted(members.items())]


async def fetch_test_group_members(*, bot: Any | None = None) -> list[dict[str, int | str]]:
    """Read the configured fixed test group without accepting an arbitrary group ID."""
    if not settings.codex_completion_notify_enabled:
        raise RuntimeError("Codex completion notifications are disabled")
    if settings.codex_completion_notify_group_id is None:
        raise RuntimeError("Codex completion notification group is not configured")
    target = bot or _active_onebot()
    if target is None:
        raise RuntimeError("no active OneBot connection is available")
    response = await call_qq_action(
        target,
        "get_group_member_list",
        group_id=settings.codex_completion_notify_group_id,
        no_cache=False,
    )
    return normalize_test_group_members(response)


async def notify_codex_completion(
    text: str | None = None,
    *,
    details: str | None = None,
    bot: Any | None = None,
) -> Any:
    """Send the final result as a forward, followed by a fixed-target mention."""
    if not settings.codex_completion_notify_enabled:
        raise RuntimeError("Codex completion notifications are disabled")
    if settings.codex_completion_notify_group_id is None:
        raise RuntimeError("Codex completion notification group is not configured")
    target = bot or _active_onebot()
    if target is None:
        raise RuntimeError("no active OneBot connection is available")
    if details and details.strip():
        nodes = completion_forward_nodes(details, getattr(target, "self_id", "Codex"))
        await call_qq_action(
            target,
            "send_group_forward_msg",
            group_id=settings.codex_completion_notify_group_id,
            messages=nodes,
        )
    return await call_qq_action(
        target,
        "send_group_msg",
        group_id=settings.codex_completion_notify_group_id,
        message=completion_message(text),
    )


async def notify_test_group(
    notification: TestGroupNotification,
    *,
    bot: Any | None = None,
) -> Any:
    """Deliver a validated text/image notification to the configured test group."""
    if not settings.codex_completion_notify_enabled:
        raise RuntimeError("Codex completion notifications are disabled")
    if settings.codex_completion_notify_group_id is None:
        raise RuntimeError("Codex completion notification group is not configured")
    target = bot or _active_onebot()
    if target is None:
        raise RuntimeError("no active OneBot connection is available")
    nodes = test_group_notification_nodes(notification, getattr(target, "self_id", "Codex"))
    await call_qq_action(
        target,
        "send_group_forward_msg",
        group_id=settings.codex_completion_notify_group_id,
        messages=nodes,
    )
    return await call_qq_action(
        target,
        "send_group_msg",
        group_id=settings.codex_completion_notify_group_id,
        message=completion_message(
            f"{NOTIFICATION_KIND_LABELS[notification.kind]}已发送：{notification.title}。"
        ),
    )
