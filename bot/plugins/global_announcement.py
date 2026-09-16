from __future__ import annotations

import asyncio
import hmac
import json
import random
import re
from pathlib import Path
from typing import Any

from fastapi import File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, Response
from nonebot import get_bots, get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment

from bot.config import RESOURCE_DIR, ROOT, settings
from bot.services.media import local_image_segment
from bot.services.qq_platform import call_qq_action
from bot.services.reports import ReportRenderer
from bot.services.roles import is_super_admin
from bot.services.runtime import database, group_domains
from bot.services.global_announcement_web import (
    AnnouncementTarget,
    UPLOAD_DIR,
    announcement_web_preview_bytes,
    announcement_web_base_url,
    web_sessions,
)


MAX_GLOBAL_ANNOUNCEMENT_CHARS = 1000
MAX_GRAPHIC_ANNOUNCEMENT_TITLE_CHARS = 80
MAX_GLOBAL_IMAGE_BYTES = 20 * 1024 * 1024
GLOBAL_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
GRAPHIC_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
STICKER_DIR = RESOURCE_DIR / "asoul_stickers"
STICKER_ALIASES = {"大哭": "哭哭"}
WEB_PAGE_PATH = RESOURCE_DIR / "global_announcement_web.html"


renderer = ReportRenderer(
    settings.report_dir,
    settings.report_font_path,
    settings.report_retention_hours,
    settings.timezone,
    settings.command_prefix,
)
driver = get_driver()
db = database()
domains = group_domains()


def announcement_targets() -> tuple[int, ...]:
    """Resolve the internal endpoint's default target from a named SQLite cluster."""

    cluster_name = settings.global_announcement_default_cluster
    if not cluster_name:
        raise ValueError("GLOBAL_ANNOUNCEMENT_DEFAULT_CLUSTER is not configured")
    try:
        domain = domains.cluster_by_name_or_alias(cluster_name)
    except ValueError as exc:
        raise ValueError("default announcement cluster name is ambiguous") from exc
    if domain is None:
        raise ValueError("default announcement cluster does not exist")
    targets = domains.domain_groups(domain.domain_id)
    if not targets:
        raise ValueError("default announcement cluster has no active groups")
    return targets


def announcement_target_options() -> tuple[AnnouncementTarget, ...]:
    """Snapshot selectable clusters, member groups and independent groups."""

    grouped: dict[int, list[int]] = {}
    domain_rows: dict[int, Any] = {}
    for row in db.managed_groups():
        group_id = int(row["group_id"])
        domain = domains.domain_for_group(group_id)
        if domain is None:
            continue
        grouped.setdefault(domain.domain_id, []).append(group_id)
        domain_rows[domain.domain_id] = domain

    options: list[AnnouncementTarget] = []
    solo_options: list[AnnouncementTarget] = []
    for domain_id in sorted(grouped):
        domain = domain_rows[domain_id]
        group_ids = tuple(sorted(grouped[domain_id]))
        if domain.mode == "cluster":
            parent_key = f"cluster:{domain_id}"
            options.append(
                AnnouncementTarget(
                    key=parent_key,
                    label=domain.alias or domain.name,
                    kind="cluster",
                    group_ids=group_ids,
                )
            )
            options.extend(
                AnnouncementTarget(
                    key=f"group:{group_id}",
                    label=domains.display_name(group_id),
                    kind="group",
                    group_ids=(group_id,),
                    parent_key=parent_key,
                )
                for group_id in group_ids
            )
        else:
            group_id = group_ids[0]
            solo_options.append(
                AnnouncementTarget(
                    key=f"group:{group_id}",
                    label=domains.display_name(group_id),
                    kind="group",
                    group_ids=(group_id,),
                )
            )
    return tuple((*options, *solo_options))


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
    extra_text: str = "",
    target_group_ids: tuple[int, ...] | None = None,
) -> tuple[int, int]:
    """Render one announcement poster and deliver it to the resolved snapshot."""
    targets = tuple(target_group_ids) if target_group_ids is not None else announcement_targets()
    sticker = resolve_announcement_sticker(member, sticker_name)
    try:
        image_path = renderer.render_global_announcement(text, sticker=sticker)
    except Exception:
        logger.exception("Global announcement poster rendering failed")
        return 0, len(targets)
    image = local_image_segment(image_path)
    message = global_announcement_message(image, at_all=at_all, extra_text=extra_text)
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


def global_announcement_message(
    image: MessageSegment, *, at_all: bool = False, extra_text: str = ""
) -> MessageSegment | Message:
    """Build one OneBot message, appending text beneath the announcement image when set."""
    normalized_extra_text = extra_text.strip()
    if not at_all and not normalized_extra_text:
        return image
    segments: list[MessageSegment] = []
    if at_all:
        segments.append(MessageSegment.at("all"))
    segments.append(image)
    if normalized_extra_text:
        segments.append(MessageSegment.text(f"\n{normalized_extra_text}"))
    return Message(segments)


async def send_global_image(
    bot: Any,
    image_path: Path,
    at_all: bool = False,
    extra_text: str = "",
    target_group_ids: tuple[int, ...] | None = None,
) -> tuple[int, int]:
    return await send_global_image_segment(
        bot,
        local_image_segment(image_path),
        at_all=at_all,
        extra_text=extra_text,
        target_group_ids=target_group_ids,
    )


async def send_global_image_segment(
    bot: Any,
    image: MessageSegment,
    at_all: bool = False,
    extra_text: str = "",
    target_group_ids: tuple[int, ...] | None = None,
) -> tuple[int, int]:
    message = global_announcement_message(image, at_all=at_all, extra_text=extra_text)
    sent = 0
    failed = 0
    targets = tuple(target_group_ids) if target_group_ids is not None else announcement_targets()
    for group_id in targets:
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


def _web_session(token: str):
    session = web_sessions.get(token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="链接已过期，请重新发送 #公告面板 获取新链接。",
        )
    return session


def _web_preview_response(preview: bytes) -> Response:
    """Return a transfer-light preview; the full local poster remains the send source."""
    return Response(
        content=preview,
        media_type="image/webp",
        headers={"Cache-Control": "no-store"},
    )


def _web_sticker(
    member_choice: str, sticker_choice: str, *, allow_none: bool = False
) -> tuple[str | None, str, Path | None]:
    if allow_none and member_choice == "__none__":
        if sticker_choice != "__random__":
            raise ValueError("无角色时不能选择表情")
        return None, "无角色", None
    members = available_sticker_members()
    member = None if member_choice == "__random__" else member_choice
    if member is not None and member not in members:
        raise ValueError("角色选择无效")
    sticker = None if sticker_choice == "__random__" else sticker_choice
    if member is None and sticker is not None:
        raise ValueError("随机角色只能搭配随机表情")
    path = resolve_announcement_sticker(member, sticker)
    if path is None:
        raise ValueError("当前没有可用的公告角色表情")
    for actual_member in members:
        for name, candidate in sticker_names(actual_member).items():
            if candidate == path:
                return actual_member, name, path
    raise ValueError("公告角色表情不可用")


def _web_at_all(value: str) -> bool:
    if value.lower() in {"true", "1", "on"}:
        return True
    if value.lower() in {"false", "0", "off"}:
        return False
    raise ValueError("全体提醒参数无效")


def _web_extra_text(value: object) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > MAX_GLOBAL_ANNOUNCEMENT_CHARS:
        raise ValueError(f"附加文字不能超过 {MAX_GLOBAL_ANNOUNCEMENT_CHARS} 个字符")
    return normalized


def _web_targets(session: Any, value: object) -> tuple[int, ...]:
    return web_sessions.resolve_targets(session, value)


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
    extra_text: str = "",
    target_group_ids: tuple[int, ...] | None = None,
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
        extra_text=extra_text,
        target_group_ids=target_group_ids,
    )


async def deliver_global_image(
    image_path: Path,
    at_all: bool = False,
    extra_text: str = "",
    target_group_ids: tuple[int, ...] | None = None,
) -> tuple[int, int]:
    target = _active_onebot()
    if target is None:
        raise RuntimeError("no active OneBot connection is available")
    return await send_global_image(
        target,
        image_path,
        at_all=at_all,
        extra_text=extra_text,
        target_group_ids=target_group_ids,
    )


def _authorized(request: Request) -> bool:
    token = settings.codex_completion_notify_token
    provided = request.headers.get("X-Codex-Completion-Token", "")
    return bool(token) and hmac.compare_digest(provided, token)


@driver.server_app.get("/announcement/{token}", response_class=HTMLResponse)
async def global_announcement_web_page(token: str) -> HTMLResponse:
    _web_session(token)
    if not WEB_PAGE_PATH.is_file():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="公告网页资源不可用")
    return HTMLResponse(WEB_PAGE_PATH.read_text(encoding="utf-8"))


@driver.server_app.get("/announcement/api/{token}/state")
async def global_announcement_web_state(token: str) -> dict[str, Any]:
    session = _web_session(token)
    members = available_sticker_members()
    return {
        "members": members,
        "stickers": {member: sorted(sticker_names(member)) for member in members},
        "target_options": [
            {
                "key": option.key,
                "label": option.label,
                "kind": option.kind,
                "group_ids": option.group_ids,
                "parent_key": option.parent_key,
            }
            for option in session.target_options
        ],
        "remaining_seconds": web_sessions.remaining_seconds(session),
    }


@driver.server_app.post("/announcement/api/{token}/preview")
async def global_announcement_web_preview(token: str, request: Request) -> Response:
    session = _web_session(token)
    if session.delivered or session.sending:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="此链接已提交，请重新创建公告。")
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("预览参数必须是对象")
        text = str(payload.get("text") or "").strip()
        member_choice = str(payload.get("member") or "__random__")
        sticker_choice = str(payload.get("sticker") or "__random__")
        at_all = bool(payload.get("at_all", False))
        extra_text = _web_extra_text(payload.get("extra_text"))
        target_group_ids = _web_targets(session, payload.get("targets"))
        if not text:
            raise ValueError("公告正文不能为空")
        if len(text) > MAX_GLOBAL_ANNOUNCEMENT_CHARS:
            raise ValueError(f"公告正文不能超过 {MAX_GLOBAL_ANNOUNCEMENT_CHARS} 个字符")
        member, sticker, sticker_path = _web_sticker(
            member_choice, sticker_choice, allow_none=True
        )
        poster = await asyncio.to_thread(
            renderer.render_global_announcement, text, sticker=sticker_path
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Global announcement web preview failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="海报预览生成失败") from exc
    session.draft_path = poster
    session.draft_text = text
    session.draft_extra_text = extra_text
    session.draft_at_all = at_all
    session.draft_target_group_ids = target_group_ids
    return _web_preview_response(
        await asyncio.to_thread(announcement_web_preview_bytes, poster)
    )


@driver.server_app.post("/announcement/api/{token}/graphic-preview")
async def global_announcement_web_graphic_preview(
    token: str,
    image: UploadFile = File(...),
    title: str = Form(""),
    text: str = Form(""),
    member: str = Form("__random__"),
    sticker: str = Form("__random__"),
    at_all: str = Form("false"),
    extra_text: str = Form(""),
    targets: str = Form("[]"),
) -> Response:
    session = _web_session(token)
    if session.delivered or session.sending:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="此链接已提交，请重新创建公告。")
    session.draft_path = None
    session.draft_text = ""
    session.draft_extra_text = ""
    session.draft_at_all = False
    session.draft_target_group_ids = ()
    if session.upload_path is not None:
        session.upload_path.unlink(missing_ok=True)
        session.upload_path = None
    suffix = Path(image.filename or "").suffix.lower()
    upload_path: Path | None = None
    try:
        if suffix not in GRAPHIC_IMAGE_SUFFIXES:
            raise ValueError("图文海报仅支持 PNG、JPG、JPEG、WebP 图片；动图请使用原图公告")
        normalized_title = title.strip()
        normalized_text = text.strip()
        if not normalized_title:
            raise ValueError("公告标题不能为空")
        if not normalized_text:
            raise ValueError("公告正文不能为空")
        if len(normalized_title) > MAX_GRAPHIC_ANNOUNCEMENT_TITLE_CHARS:
            raise ValueError(f"公告标题不能超过 {MAX_GRAPHIC_ANNOUNCEMENT_TITLE_CHARS} 个字符")
        if len(normalized_text) > MAX_GLOBAL_ANNOUNCEMENT_CHARS:
            raise ValueError(f"公告正文不能超过 {MAX_GLOBAL_ANNOUNCEMENT_CHARS} 个字符")
        notify_all = _web_at_all(at_all)
        normalized_extra_text = _web_extra_text(extra_text)
        target_group_ids = _web_targets(session, json.loads(targets))
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = UPLOAD_DIR / f"{token}.graphic{suffix}"
        size = 0
        with upload_path.open("wb") as destination:
            while chunk := await image.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_GLOBAL_IMAGE_BYTES:
                    raise ValueError("图片不能超过 20 MiB")
                destination.write(chunk)
        session.upload_path = resolve_global_image(str(upload_path))
        selected_member, selected_sticker, sticker_path = _web_sticker(
            member, sticker, allow_none=True
        )
        poster = await asyncio.to_thread(
            renderer.render_global_graphic_announcement,
            normalized_title,
            normalized_text,
            session.upload_path,
            sticker=sticker_path,
        )
    except (ValueError, OSError) as exc:
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
        logger.exception("Global announcement web graphic preview failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="图文海报预览生成失败") from exc
    finally:
        await image.close()
    session.draft_path = poster
    session.draft_text = normalized_text
    session.draft_extra_text = normalized_extra_text
    session.draft_at_all = notify_all
    session.draft_target_group_ids = target_group_ids
    return _web_preview_response(
        await asyncio.to_thread(announcement_web_preview_bytes, poster)
    )


@driver.server_app.post("/announcement/api/{token}/send-preview")
@driver.server_app.post("/announcement/api/{token}/send-text")
async def global_announcement_web_send_preview(token: str) -> dict[str, int]:
    session = _web_session(token)
    if session.draft_path is None or not session.draft_path.is_file():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请先生成海报预览")
    try:
        web_sessions.begin_send(token)
        sent, failed = await deliver_global_image(
            session.draft_path,
            at_all=session.draft_at_all,
            extra_text=session.draft_extra_text,
            target_group_ids=session.draft_target_group_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        web_sessions.fail_send(token)
        logger.exception("Global announcement web text delivery failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="公告发送失败") from exc
    web_sessions.finish_send(token)
    if session.upload_path is not None:
        try:
            session.upload_path.unlink(missing_ok=True)
        except OSError:
            pass
    return {"sent": sent, "failed": failed}


@driver.server_app.post("/announcement/api/{token}/send-image")
async def global_announcement_web_send_image(
    token: str,
    image: UploadFile = File(...),
    at_all: str = Form("false"),
    extra_text: str = Form(""),
    targets: str = Form("[]"),
) -> dict[str, int]:
    session = _web_session(token)
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in GLOBAL_IMAGE_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="仅支持 PNG、JPG、JPEG、GIF、WebP 图片")
    upload_path: Path | None = None
    try:
        notify_all = _web_at_all(at_all)
        normalized_extra_text = _web_extra_text(extra_text)
        target_group_ids = _web_targets(session, json.loads(targets))
        web_sessions.begin_send(token)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = UPLOAD_DIR / f"{token}{suffix}"
        size = 0
        with upload_path.open("wb") as destination:
            while chunk := await image.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_GLOBAL_IMAGE_BYTES:
                    raise ValueError("图片不能超过 20 MiB")
                destination.write(chunk)
        session.upload_path = resolve_global_image(str(upload_path))
        sent, failed = await deliver_global_image(
            session.upload_path,
            at_all=notify_all,
            extra_text=normalized_extra_text,
            target_group_ids=target_group_ids,
        )
    except ValueError as exc:
        web_sessions.fail_send(token)
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        web_sessions.fail_send(token)
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
        logger.exception("Global announcement web image delivery failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="图片公告发送失败") from exc
    finally:
        await image.close()
    web_sessions.finish_send(token)
    if session.upload_path is not None:
        try:
            session.upload_path.unlink(missing_ok=True)
        except OSError:
            pass
    return {"sent": sent, "failed": failed}


@driver.server_app.post("/internal/codex/global-announcement")
async def receive_global_announcement(request: Request) -> dict[str, Any]:
    """Deliver an authenticated local request to managed groups or the default cluster."""
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
    target_group_ids = None
    if "target_group_ids" in payload:
        requested = payload["target_group_ids"]
        managed = set(domains.all_group_ids())
        if (not isinstance(requested, list) or not requested
                or any(type(group_id) is not int or group_id not in managed for group_id in requested)):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="target_group_ids must be a non-empty list of active managed group IDs")
        target_group_ids = tuple(dict.fromkeys(requested))
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
                resolve_global_image(image_raw.strip()), at_all=at_all,
                target_group_ids=target_group_ids,
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
                target_group_ids=target_group_ids,
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


global_announcement_web = on_command(
    "公告面板", aliases={"公告网页"}, priority=5, block=True
)


@global_announcement_web.handle()
async def _(event: MessageEvent):
    if not is_super_admin(int(event.user_id)):
        await global_announcement_web.finish("只有超级管理员可以使用公告面板。")
    base_url = announcement_web_base_url()
    if base_url is None:
        await global_announcement_web.finish(
            "公告面板隧道尚未启动。"
        )
    target_options = announcement_target_options()
    if not target_options:
        await global_announcement_web.finish("当前没有可选的公告目标群。")
    session = web_sessions.create(int(event.user_id), target_options)
    await global_announcement_web.finish(
        "公告编辑页（链接仅限本次操作，15 分钟内有效）：\n"
        f"{base_url}/notice/{session.token}"
    )
