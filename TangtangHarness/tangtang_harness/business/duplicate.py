from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from tangtang_harness.business.db import Database
from tangtang_harness.business.gateway import OneBotGateway


# Local images combine every matched member into one tall card. Text fallbacks
# split long output by message length instead of interactive pages.
DUPLICATE_PAGE_SIZE = 4
DUPLICATE_MESSAGE_LIMIT = 3500
DUPLICATE_MODE_ALL = "all"
DUPLICATE_MODE_SOURCE = "source"
DUPLICATE_MODE = Literal["all", "source"]


class DuplicateService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def _sync(self, gateway: OneBotGateway, group_ids: tuple[int, ...]) -> list[int]:
        if len(group_ids) < 2:
            return list(group_ids)
        failed = await gateway.sync_members(group_ids, self.database)
        return failed

    async def scan(
        self,
        gateway: OneBotGateway,
        group_ids: tuple[int, ...],
        ignore_whitelist: bool = False,
    ) -> tuple[list[dict[str, Any]], list[int]]:
        failed = await self._sync(gateway, group_ids)
        if failed:
            return [], failed
        return self.database.duplicate_members_from_source(
            group_ids[0], group_ids[1:], ignore_whitelist=ignore_whitelist
        ), []

    async def scan_all(
        self,
        gateway: OneBotGateway,
        group_ids: tuple[int, ...],
        ignore_whitelist: bool = False,
    ) -> tuple[list[dict[str, Any]], list[int]]:
        failed = await self._sync(gateway, group_ids)
        if failed:
            return [], failed
        return self.database.duplicate_members(group_ids, ignore_whitelist=ignore_whitelist), []


def group_marker(index: int) -> str:
    return f"{index}\ufe0f\u20e3" if 1 <= index <= 10 else f"[{index}]"


def scope_lines(
    group_labels: Sequence[tuple[int, str]], mode: DUPLICATE_MODE = DUPLICATE_MODE_SOURCE
) -> list[str]:
    if mode == DUPLICATE_MODE_ALL:
        lines = ["查重范围：所有指定群互相查重"]
    else:
        lines = ["查重范围：第一个为起点群，后续为目标群"]
    for index, (group_id, group_name) in enumerate(group_labels, 1):
        role = "参与查重" if mode == DUPLICATE_MODE_ALL else ("起点" if index == 1 else "目标")
        label = group_name or str(group_id)
        lines.append(f"{group_marker(index)} {label}({group_id}) [{role}]")
    return lines


def result_group_markers(item: dict[str, Any], group_labels: Sequence[tuple[int, str]]) -> str:
    present = {int(row["group_id"]) for row in item.get("groups", ())}
    return "、".join(
        group_marker(index)
        for index, (group_id, _name) in enumerate(group_labels, 1)
        if group_id in present
    ) or "未知"


def render_duplicate_page(
    result: list[dict[str, Any]],
    page: int,
    command_prefix: str,
    group_labels: Sequence[tuple[int, str]] | None = None,
    scope_mode: DUPLICATE_MODE = DUPLICATE_MODE_SOURCE,
    command_name: str = "查重",
    page_size: int | None = DUPLICATE_PAGE_SIZE,
) -> tuple[str, int]:
    if page_size is None:
        total_pages = 1
        chunk = result
    else:
        total_pages = max(1, (len(result) + page_size - 1) // page_size)
        if page > total_pages:
            raise ValueError(f"页码超出范围，共 {total_pages} 页。")
        chunk = result[(page - 1) * page_size : page * page_size]
    title = f"查重结果：{len(result)} 名重复成员"
    if total_pages > 1:
        title += f"，第 {page}/{total_pages} 页"
    lines = scope_lines(group_labels, scope_mode) if group_labels else []
    lines.append(title)
    for item in chunk:
        groups = (
            result_group_markers(item, group_labels)
            if group_labels
            else "、".join(
                f"{row['group_name'] or row['group_id']}({row['group_id']})" for row in item["groups"]
            )
        )
        nickname = str(item.get("nickname") or "")
        user_label = f"{item['user_id']}（{nickname}）" if nickname else str(item["user_id"])
        lines.append(f"{user_label}：{groups}")
    if not result:
        lines.append("未发现重复成员。")
    elif total_pages > 1 and page < total_pages:
        lines.append(f"使用 {command_prefix}{command_name} ... 页{page + 1} 查看下一页。")
    return "\n".join(lines), total_pages


def split_duplicate_message(message: str, limit: int = DUPLICATE_MESSAGE_LIMIT) -> list[str]:
    """Split long output by complete lines without creating interactive pages."""
    lines = message.splitlines() or [message]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        extra = len(line) + (1 if current else 0)
        if current and current_length + extra > limit:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += len(line) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks
