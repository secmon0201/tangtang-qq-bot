"""Project-owned XutheringWavesUID help and local ranking interception layer."""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.rule import Rule

from bot.config import ROOT, settings
from bot.services.avatars import AvatarService
from bot.services.media import local_image_segment
from bot.services.wuwa_command_policy import (
    ROVER_REMINDER_DISABLED_MESSAGE,
    is_disabled_rover_reminder_command,
)
from bot.services.wuwa_help_render import WuwaFullHelpRenderer, WuwaHelpRenderer
from bot.services.wuwa_rank_data import (
    WuwaRankDataError,
    WuwaRankRequest,
    default_wuwa_rank_service,
    is_full_wuwa_help_command,
    is_new_wuwa_help_command,
    is_original_wuwa_help_command,
    is_wuwa_help_command,
    parse_wuwa_rank_command,
)
from bot.services.wuwa_rank_render import WuwaRankRenderer
from bot.application.local_features import FeatureRequest, register_local_feature


ORIGINAL_HELP_PATH = ROOT / "data" / "wuwa_original_help.png"
rank_service = default_wuwa_rank_service()
rank_renderer = WuwaRankRenderer()
help_renderer = WuwaHelpRenderer()
full_help_renderer = WuwaFullHelpRenderer()
avatar_service = AvatarService(
    settings.avatar_cache_dir,
    settings.avatar_base_url,
    settings.avatar_timeout,
    settings.avatar_cache_ttl,
    refresh_interval=settings.avatar_refresh_interval,
    refresh_cooldown=settings.avatar_refresh_cooldown,
    max_refresh_per_call=settings.avatar_refresh_max_per_call,
    concurrency=settings.avatar_refresh_concurrency,
)


def is_wuwa_ui_message(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    text = event.get_plaintext().strip()
    return (
        is_wuwa_help_command(text)
        or parse_wuwa_rank_command(text) is not None
        or is_disabled_rover_reminder_command(text)
    )


wuwa_game_ui = on_message(rule=Rule(is_wuwa_ui_message), priority=-2, block=True)


@wuwa_game_ui.handle()
async def _(event: GroupMessageEvent) -> None:
    text = event.get_plaintext().strip()
    if is_disabled_rover_reminder_command(text):
        await wuwa_game_ui.finish(ROVER_REMINDER_DISABLED_MESSAGE)
        return
    if is_wuwa_help_command(text):
        await _send_help(wuwa_game_ui, text)
        return
    await _send_rank(wuwa_game_ui, event, text)


@register_local_feature("wuwa_rank")
async def _chat_wuwa_rank(
    matcher: Any,
    bot: Bot,
    event: GroupMessageEvent,
    request: FeatureRequest,
) -> None:
    del bot
    await _send_rank_request(
        matcher,
        event,
        WuwaRankRequest("strongest", None, "bot" if request.args == "总" else "group"),
    )


@register_local_feature(
    "wuwa_help", "wuwa_character_rank", "wuwa_echo_rank", "wuwa_progress_rank"
)
async def _chat_wuwa_read(
    matcher: Any,
    bot: Bot,
    event: GroupMessageEvent,
    request: FeatureRequest,
) -> None:
    del bot
    if request.action == "wuwa_help":
        await _send_help(matcher, "#ww帮助")
        return
    kind = {
        "wuwa_character_rank": "role",
        "wuwa_echo_rank": "phantom",
        "wuwa_progress_rank": "practice",
    }[request.action]
    character = (
        str(request.parameter("character", "") or "").strip()
        if kind in {"role", "phantom"}
        else None
    )
    await _send_rank_request(
        matcher,
        event,
        WuwaRankRequest(
            kind,
            character,
            "bot" if request.args == "总" else "group",
            int(request.parameter("page", 1) or 1),
        ),
    )


async def _send_rank(matcher: Any, event: GroupMessageEvent, text: str) -> None:
    request = parse_wuwa_rank_command(text)
    if request is None:
        await matcher.finish()
        return
    await _send_rank_request(matcher, event, request)


async def _send_rank_request(
    matcher: Any, event: GroupMessageEvent, request: WuwaRankRequest
) -> None:
    try:
        result = await asyncio.to_thread(rank_service.build, request, int(event.group_id), int(event.user_id))
        avatar_rows = [*result.rows, *([result.self_overflow] if result.self_overflow else [])]
        avatar_paths = await avatar_service.prefetch(
            [{"user_id": int(row.user_id), "avatar_url": ""} for row in avatar_rows if row.user_id.isdigit()]
        )
        image_path = await asyncio.to_thread(rank_renderer.render, result, avatar_paths)
    except WuwaRankDataError as exc:
        logger.warning("Wuthering Waves ranking unavailable: {}", exc)
        await matcher.finish(f"鸣潮排行榜暂不可用：{exc}")
        return
    except Exception:
        logger.exception("Wuthering Waves ranking render failed")
        await matcher.finish("鸣潮排行榜生成失败，请稍后重试。")
        return
    await matcher.finish(local_image_segment(image_path))


async def _send_help(matcher: Any, text: str) -> None:
    if is_original_wuwa_help_command(text):
        if not ORIGINAL_HELP_PATH.exists():
            await matcher.finish(
                "原版帮助快照尚未生成，请在项目目录运行："
                "python scripts/export_wuwa_original_help.py"
            )
            return
        await matcher.finish(local_image_segment(ORIGINAL_HELP_PATH))
    if is_full_wuwa_help_command(text):
        try:
            image_path = await asyncio.to_thread(full_help_renderer.render)
        except Exception:
            logger.exception("Wuthering Waves full help render failed")
            await matcher.finish("鸣潮完整帮助图生成失败，请稍后重试。")
            return
        await matcher.finish(local_image_segment(image_path))
    if is_new_wuwa_help_command(text):
        try:
            image_path = await asyncio.to_thread(help_renderer.render)
        except Exception:
            logger.exception("Wuthering Waves help render failed")
            await matcher.finish("新版鸣潮帮助图生成失败，请稍后重试。")
            return
        await matcher.finish(local_image_segment(image_path))
    await matcher.finish()


__all__ = ["is_wuwa_ui_message", "wuwa_game_ui"]
