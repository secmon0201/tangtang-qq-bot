from __future__ import annotations

import hmac
import random
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from nonebot import get_bots, get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

from bot.config import A_COAST_GROUP_IDS, RESOURCE_DIR, ROOT, settings
from bot.services.media import local_image_segment
from bot.services.qq_platform import call_qq_action
from bot.services.reports import ReportRenderer
from bot.services.roles import is_super_admin


MAX_GLOBAL_ANNOUNCEMENT_CHARS = 1000
MAX_GLOBAL_IMAGE_BYTES = 20 * 1024 * 1024
GLOBAL_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
STICKER_DIR = RESOURCE_DIR / "asoul_stickers"
STICKER_ALIASES = {"大哭": "哭哭"}


renderer = ReportRenderer(
    settings.report_dir,
    settings.report_font_path,
    settings.report_retention_hours,
    settings.timezone,
    settings.command_prefix,
)
driver = get_driver()


def announcement_targets() -> tuple[int, ...]:
    """Return the fixed A-Coast groups that are still inside the managed scope."""
    managed = set(settings.managed_group_ids)
    skipped = [group_id for group_id in A_COAST_GROUP_IDS if group_id not in managed]
    if skipped:
        logger.warning(
            "Global announcement skips A-Coast groups outside MANAGED_GROUP_IDS: %s",
            ",".join(map(str, skipped)),
        )
    return tuple(group_id for group_id in A_COAST_GROUP_IDS if group_id in managed)


def sticker_map(sticker_dir: Path = STICKER_DIR) -> dict[str, list[Path]]:
    """Map sticker folder names to their PNG assets, mirroring the A-SOUL renderer."""
    result: dict[str, list[Path]] = {}
    if sticker_dir.is_dir():
        for directory in sticker_dir.iterdir():
            if directory.is_dir():
                result[directory.name] = sorted(
                    path for path in directory.glob("*.png") if path.is_file()
                )
    return result


def available_sticker_members(sticker_dir: Path = STICKER_DIR) -> tuple[str, ...]:
    return tuple(sorted(sticker_map(sticker_dir)))


def sticker_names(member: str, sticker_dir: Path = STICKER_DIR) -> dict[str, Path]:
    """Map display sticker names (file names without the static suffix) to paths."""
    result: dict[str, Path] = {}
    for path in sticker_map(sticker_dir).get(member, []):
        name = path.name.split("-0_", 1)[0]
        if name:
            result[name] = path
    return result


def resolve_announcement_sticker(
    member: str | None,
    sticker_name: str | None = None,
    sticker_dir: Path = STICKER_DIR,
) -> Path | None:
    """Pick one sticker: exact name, member pack, then random from everyone."""
    stickers = sticker_map(sticker_dir)
    if member and member in stickers:
        pack_names = sticker_names(member, sticker_dir)
        if sticker_name:
            if sticker_name in pack_names:
                return pack_names[sticker_name]
            alias_target = STICKER_ALIASES.get(sticker_name)
            if alias_target and alias_target in pack_names:
                return pack_names[alias_target]
            logger.warning(
                "Global announcement sticker {} not found in {}; falling back to random",
                sticker_name,
                member,
            )
        if stickers[member]:
            return random.choice(stickers[member])
    candidates = [path for paths in stickers.values() for path in paths]
    if not candidates:
        return None
    if member:
        logger.warning(
            "Global announcement sticker member {} has no assets; falling back to random",
            member,
        )
    return random.choice(candidates)


def parse_announcement_args(raw: str) -> tuple[str, str | None, str | None, bool]:
    """Split optional @全体 marker, member name, and sticker name from the text."""
    text = raw.strip()
    at_all = False

    def strip_at_all_markers(value: str) -> str:
        nonlocal at_all
        parts = re.split(r"([\s，,\n]+)", value)
        kept: list[str] = []
        for part in parts:
            if re.fullmatch(r"@全体|@all", part, re.IGNORECASE):
                at_all = True
            else:
                kept.append(part)
        return "".join(kept).strip()

    text = strip_at_all_markers(text)
    member = None
    sticker_name = None
    members = available_sticker_members()
    match = re.match(r"^([^\s，,\n]+)[\s，,\n]+(.+)$", text, re.DOTALL)
    if match and match.group(1) in members:
        member = match.group(1)
        text = match.group(2).strip()
        sticker_match = re.match(r"^([^\s，,\n]+)[\s，,\n]+(.+)$", text, re.DOTALL)
        pack_names = sticker_names(member)
        if sticker_match:
            token = sticker_match.group(1)
            if token in pack_names:
                sticker_name = token
                text = sticker_match.group(2).strip()
            elif token in STICKER_ALIASES and STICKER_ALIASES[token] in pack_names:
                sticker_name = STICKER_ALIASES[token]
                text = sticker_match.group(2).strip()
    text = strip_at_all_markers(text)
    return text, member, sticker_name, at_all


async def send_global_announcement(
    bot: Any,
    text: str,
    member: str | None = None,
    sticker_name: str | None = None,
    at_all: bool = False,
) -> tuple[int, int]:
    """Render one announcement poster and deliver it to every A-Coast group."""
    targets = announcement_targets()
    sticker = resolve_announcement_sticker(member, sticker_name)
    try:
        image_path = renderer.render_global_announcement(text, sticker=sticker)
    except Exception:
        logger.exception("Global announcement poster rendering failed")
        return 0, len(targets)
    image = local_image_segment(image_path)
    message: Any = (
        Message([MessageSegment.at("all"), image]) if at_all else image
    )
    sent = 0
    failed = 0
    for group_id in targets:
        try:
            await call_qq_action(
                bot,
                "send_group_msg",
                group_id=group_id,
                message=message,
            )
        except Exception:
            logger.exception("Global announcement delivery failed: group=%s", group_id)
            failed += 1
        else:
            sent += 1
    return sent, failed


def resolve_global_image(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("image path must be inside the bot workspace") from exc
    if path.suffix.lower() not in GLOBAL_IMAGE_SUFFIXES:
        raise ValueError("unsupported global image type")
    if not path.is_file():
        raise ValueError("global image file does not exist")
    if path.stat().st_size > MAX_GLOBAL_IMAGE_BYTES:
        raise ValueError("global image file is too large")
    return path


async def send_global_image(bot: Any, image_path: Path, at_all: bool = False) -> tuple[int, int]:
    return await send_global_image_segment(bot, local_image_segment(image_path), at_all=at_all)


async def send_global_image_segment(
    bot: Any, image: MessageSegment, at_all: bool = False
) -> tuple[int, int]:
    message: Any = Message([MessageSegment.at("all"), image]) if at_all else image
    sent = 0
    failed = 0
    for group_id in announcement_targets():
        try:
            await call_qq_action(bot, "send_group_msg", group_id=group_id, message=message)
        except Exception:
            logger.exception("Global image delivery failed: group=%s", group_id)
            failed += 1
        else:
            sent += 1
    return sent, failed


def extract_global_announcement_image(
    message: Message, reply_message: Message | None = None
) -> MessageSegment | None:
    for source in (message, reply_message or Message()):
        for segment in source:
            if segment.type != "image":
                continue
            image_source = str(segment.data.get("url") or segment.data.get("file") or "").strip()
            if image_source:
                return MessageSegment.image(file=image_source)
    return None


def image_announcement_mentions_all(message: Message) -> bool:
    if any(
        segment.type == "at" and str(segment.data.get("qq") or "").lower() == "all"
        for segment in message
    ):
        return True
    return bool(re.search(r"(?:^|\s)@(?:全体|all)(?=$|\s)", message.extract_plain_text(), re.I))


def _active_onebot() -> Any | None:
    return next(
        (candidate for candidate in get_bots().values() if hasattr(candidate, "call_api")),
        None,
    )


async def deliver_global_announcement(
    text: str,
    member: str | None = None,
    sticker_name: str | None = None,
    at_all: bool = False,
) -> tuple[int, int]:
    """Deliver a validated announcement through the currently connected bot."""
    target = _active_onebot()
    if target is None:
        raise RuntimeError("no active OneBot connection is available")
    return await send_global_announcement(
        target,
        text,
        member=member,
        sticker_name=sticker_name,
        at_all=at_all,
    )


async def deliver_global_image(image_path: Path, at_all: bool = False) -> tuple[int, int]:
    target = _active_onebot()
    if target is None:
        raise RuntimeError("no active OneBot connection is available")
    return await send_global_image(target, image_path, at_all=at_all)


def _authorized(request: Request) -> bool:
    token = settings.codex_completion_notify_token
    provided = request.headers.get("X-Codex-Completion-Token", "")
    return bool(token) and hmac.compare_digest(provided, token)


@driver.server_app.post("/internal/codex/global-announcement")
async def receive_global_announcement(request: Request) -> dict[str, Any]:
    """Receive a local text request and deliver it as an image to all A-Coast groups."""
    if not settings.codex_completion_notify_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="global announcement is disabled",
        )
    if not _authorized(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid notification token",
        )
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid JSON body",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="JSON body must be an object",
        )
    text = payload.get("text")
    image_raw = payload.get("image_path")
    if (text is None) == (image_raw is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="provide exactly one of text or image_path",
        )
    sticker = payload.get("sticker")
    if sticker is not None and not isinstance(sticker, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sticker must be a member name string",
        )
    sticker_name = payload.get("sticker_name")
    if sticker_name is not None and not isinstance(sticker_name, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sticker_name must be a string",
        )
    at_all = payload.get("at_all", False)
    if not isinstance(at_all, bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="at_all must be a boolean",
        )
    try:
        if image_raw is not None:
            if not isinstance(image_raw, str) or not image_raw.strip():
                raise ValueError("image_path must be a non-empty string")
            sent, failed = await deliver_global_image(
                resolve_global_image(image_raw.strip()), at_all=at_all
            )
        else:
            if not isinstance(text, str):
                raise ValueError("text must be a string")
            normalized = text.strip()
            if not normalized:
                raise ValueError("text must not be empty")
            if len(normalized) > MAX_GLOBAL_ANNOUNCEMENT_CHARS:
                raise ValueError(
                    f"text must not exceed {MAX_GLOBAL_ANNOUNCEMENT_CHARS} characters"
                )
            sent, failed = await deliver_global_announcement(
                normalized,
                member=sticker,
                sticker_name=sticker_name,
                at_all=at_all,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Global announcement delivery failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OneBot delivery failed",
        ) from exc
    return {"ok": True, "sent": sent, "failed": failed}


global_announcement = on_command("全局通告", priority=5, block=True)
global_image_announcement = on_command(
    "全局图片公告", aliases={"图片公告"}, priority=5, block=True
)


@global_image_announcement.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    if not is_super_admin(int(event.user_id)):
        await global_image_announcement.finish("只有超级管理员可以使用全局图片公告。")
    reply_message = event.reply.message if event.reply is not None else None
    image = extract_global_announcement_image(args, reply_message)
    if image is None:
        await global_image_announcement.finish(
            f"请回复一张图片后发送 {settings.command_prefix}全局图片公告，"
            "或把指令和图片放在同一条消息中。"
        )
    if not announcement_targets():
        await global_image_announcement.finish(
            "当前没有可发送的 A 海岸群，请检查 MANAGED_GROUP_IDS 配置。"
        )
    at_all = image_announcement_mentions_all(args)
    sent, failed = await send_global_image_segment(bot, image, at_all=at_all)
    at_all_text = "并@全体成员" if at_all else "（未@全体）"
    summary = f"全局图片公告已发送至 {sent} 个 A 海岸群{at_all_text}。"
    if failed:
        summary += f"  {failed} 个群发送失败，请稍后重试。"
    await global_image_announcement.finish(summary)


@global_announcement.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    if not is_super_admin(int(event.user_id)):
        await global_announcement.finish("只有超级管理员可以使用全局通告。")
    text, member, sticker_name, at_all = parse_announcement_args(args.extract_plain_text())
    if not text:
        await global_announcement.finish(
            f"用法：{settings.command_prefix}全局通告 [@全体] [人物] [表情包名] <内容>"
        )
    if len(text) > MAX_GLOBAL_ANNOUNCEMENT_CHARS:
        await global_announcement.finish(
            f"通告内容不能超过 {MAX_GLOBAL_ANNOUNCEMENT_CHARS} 个字符。"
        )
    if not announcement_targets():
        await global_announcement.finish(
            "当前没有可发送的 A 海岸群，请检查 MANAGED_GROUP_IDS 配置。"
        )
    sent, failed = await send_global_announcement(
        bot,
        text,
        member=member,
        sticker_name=sticker_name,
        at_all=at_all,
    )
    at_all_text = "并@全体成员" if at_all else "（未@全体）"
    summary = f"全局通告已以图片发送至 {sent} 个 A 海岸群{at_all_text}。"
    if failed:
        summary += f"  {failed} 个群发送失败，请稍后重试。"
    await global_announcement.finish(summary)
