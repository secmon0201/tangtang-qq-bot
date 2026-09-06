"""Shared operator-facing cards used by command plugins."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from bot.config import settings
from bot.services.avatars import AvatarService
from bot.services.media import local_image_segment
from bot.services.reports import ReportRenderer
from bot.services.runtime import database


db = database()
group_avatar_service = AvatarService(
    settings.avatar_cache_dir / "groups",
    "https://p.qlogo.cn/gh/{user_id}/{user_id}/100",
    settings.avatar_timeout,
    settings.avatar_cache_ttl,
    refresh_interval=settings.avatar_refresh_interval,
    refresh_cooldown=settings.avatar_refresh_cooldown,
    max_refresh_per_call=settings.avatar_refresh_max_per_call,
    concurrency=settings.avatar_refresh_concurrency,
)
report_renderer = ReportRenderer(
    settings.report_dir,
    settings.report_font_path,
    settings.report_retention_hours,
    settings.timezone,
)


async def finish_with_image_or_text(
    matcher: object,
    fallback: str,
    render: Callable[[], Path],
    prefix: MessageSegment | None = None,
) -> None:
    """Send a local report image, with text fallback for operational resilience."""

    message_prefix = Message(prefix) if prefix is not None else Message()
    if settings.report_output_mode != "local_image":
        await matcher.finish(message_prefix + fallback)  # type: ignore[attr-defined]
    try:
        path = render()
    except Exception:
        logger.exception("Local report rendering failed; using text output")
        await matcher.finish(message_prefix + fallback)  # type: ignore[attr-defined]
    try:
        await matcher.send(message_prefix + local_image_segment(path))  # type: ignore[attr-defined]
    except Exception:
        logger.exception("QQ transport rejected local report image; using text output")
        await matcher.finish(message_prefix + fallback)  # type: ignore[attr-defined]
    await matcher.finish()  # type: ignore[attr-defined]


async def finish_admin_feedback(
    matcher: object,
    title: str,
    subtitle: str,
    sections: list[tuple[str, str, str]],
) -> None:
    fallback = title + "\n" + "\n".join(
        f"{heading}\n{commands}\n{note}".strip() for heading, commands, note in sections
    )
    await finish_with_image_or_text(
        matcher,
        fallback,
        lambda: report_renderer.render_admin_panel(title, subtitle, sections),
    )


async def group_card_rows(
    group_ids: Iterable[int],
    details: dict[int, str] | None = None,
    tags: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    names = {
        int(row["group_id"]): str(row["group_name"] or "")
        for row in db.managed_groups()
    }
    return [
        {
            "group_id": int(group_id),
            "group_name": names.get(int(group_id), ""),
            "detail": (details or {}).get(int(group_id), ""),
            "tag": (tags or {}).get(int(group_id), "管理群"),
        }
        for group_id in sorted({int(value) for value in group_ids})
    ]


def cached_group_avatar_paths(
    rows: Sequence[Mapping[str, Any]],
) -> dict[int, Path]:
    lookup_rows = [{"user_id": int(row["group_id"])} for row in rows]
    return group_avatar_service.cached_paths(lookup_rows)


async def group_avatar_paths(
    rows: Sequence[Mapping[str, Any]],
) -> dict[int, Path]:
    lookup_rows = [{"user_id": int(row["group_id"])} for row in rows]
    cached = group_avatar_service.cached_paths(lookup_rows)
    due = [
        row
        for row in lookup_rows
        if int(row["user_id"]) not in cached or group_avatar_service.refresh_due(row)
    ]
    if due:
        try:
            await asyncio.wait_for(
                group_avatar_service.prefetch(due),
                timeout=min(2.0, float(settings.avatar_timeout)),
            )
        except TimeoutError:
            logger.info("Group avatar prefetch timed out; using local fallbacks")
        cached = group_avatar_service.cached_paths(lookup_rows)
    return cached


async def finish_group_overview(
    matcher: object,
    title: str,
    subtitle: str,
    rows: list[dict[str, Any]],
) -> None:
    fallback = title + "\n" + "\n".join(
        f"{row['group_name'] or '未命名群'}（{row['group_id']}）\n{row['detail']}\n{row['tag']}"
        for row in rows
    )
    paths = await group_avatar_paths(rows)
    await finish_with_image_or_text(
        matcher,
        fallback or f"{title}\n暂无群信息",
        lambda: report_renderer.render_group_overview(title, subtitle, rows, paths),
    )


__all__ = [
    "cached_group_avatar_paths",
    "finish_admin_feedback",
    "finish_group_overview",
    "finish_with_image_or_text",
    "group_avatar_paths",
    "group_card_rows",
    "report_renderer",
]
