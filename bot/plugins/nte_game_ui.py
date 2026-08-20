"""Project-owned NTEUID help and ranking interception layer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.rule import Rule

from bot.config import ROOT, settings
from bot.services.avatars import AvatarService
from bot.services.media import local_image_segment
from bot.services.nte_help_render import NTEHelpRenderer
from bot.services.nte_rank_data import (
    NTERankDataError,
    RankRequest,
    default_rank_service,
    is_new_nte_help_command,
    is_nte_help_command,
    is_original_nte_help_command,
    parse_rank_command,
)
from bot.services.nte_rank_render import NTERankRenderer
from bot.services.runtime import passive_settings


ORIGINAL_HELP_PATH = ROOT / "data" / "nte_original_help.png"
rank_service = default_rank_service()
rank_renderer = NTERankRenderer()
help_renderer = NTEHelpRenderer()
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


def is_nte_ui_message(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    text = event.get_plaintext().strip()
    return is_nte_help_command(text) or parse_rank_command(text) is not None


nte_game_ui = on_message(rule=Rule(is_nte_ui_message), priority=-2, block=True)


@nte_game_ui.handle()
async def _(bot: Bot, event: GroupMessageEvent) -> None:
    del bot
    text = event.get_plaintext().strip()
    if is_nte_help_command(text):
        await _send_help(text)
        return

    request = parse_rank_command(text)
    if request is None:
        await nte_game_ui.finish()
        return
    try:
        result = await asyncio.to_thread(
            rank_service.build_strongest_rank if request.strongest else rank_service.build_role_rank,
            request,
            int(event.group_id),
            int(event.user_id),
        )
        character_ids = await asyncio.to_thread(rank_service.character_ids)
        art_refresh = asyncio.create_task(rank_renderer.refresh_character_art(character_ids))
        avatar_rows = list(result.rows)
        if result.self_overflow is not None:
            avatar_rows.append(result.self_overflow)
        avatar_items = [
            {"user_id": int(row.user_id), "avatar_url": ""}
            for row in avatar_rows
            if row.user_id.isdigit()
        ]
        avatar_paths = await avatar_service.prefetch(avatar_items)
        refreshed_art = await art_refresh
        if len(refreshed_art) != len(character_ids):
            logger.warning("NTE character art refresh incomplete: {}/{}", len(refreshed_art), len(character_ids))
        image_path = await asyncio.to_thread(rank_renderer.render, result, avatar_paths)
    except NTERankDataError as exc:
        logger.warning("NTE ranking unavailable: {}", exc)
        await nte_game_ui.finish(f"异环排行榜暂不可用：{exc}")
        return
    except Exception:
        logger.exception("NTE ranking render failed")
        await nte_game_ui.finish("异环排行榜生成失败，请稍后重试。")
        return
    await nte_game_ui.finish(local_image_segment(image_path))


async def _send_help(text: str) -> None:
    if is_original_nte_help_command(text):
        if not ORIGINAL_HELP_PATH.exists():
            await nte_game_ui.finish(
                "原版帮助快照尚未生成，请在项目目录运行："
                "python scripts/export_nte_original_help.py"
            )
            return
        await nte_game_ui.finish(local_image_segment(ORIGINAL_HELP_PATH))
    if is_new_nte_help_command(text):
        try:
            image_path = await asyncio.to_thread(help_renderer.render)
        except Exception:
            logger.exception("NTE help render failed")
            await nte_game_ui.finish("新版异环帮助图生成失败，请稍后重试。")
            return
        await nte_game_ui.finish(local_image_segment(image_path))
    await nte_game_ui.finish()


__all__ = ["nte_game_ui", "is_nte_ui_message"]
