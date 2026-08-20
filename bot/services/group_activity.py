from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

import httpx

from bot.config import settings
from bot.db import Database
from bot.services.gateway import GatewayError, OneBotGateway


class ActivityResponseError(ValueError):
    """The configured QQ page response did not contain recognized activity data."""


@dataclass(frozen=True, slots=True)
class ActivityWindow:
    name: str
    active_member_count: int
    members: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ActivityCollectionResult:
    group_id: int
    report_day: date
    status: str
    windows: tuple[ActivityWindow, ...] = ()
    error: str = ""


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _first_value(value: Any, keys: set[str]) -> Any:
    for node in _walk(value):
        if isinstance(node, dict):
            for key, item in node.items():
                if str(key).lower() in keys and item not in (None, ""):
                    return item
    return None


def _named_node(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys:
                return item
        for child in value.values():
            result = _named_node(child, keys)
            if result is not None:
                return result
    elif isinstance(value, list):
        for child in value:
            result = _named_node(child, keys)
            if result is not None:
                return result
    return None


def _member_list(node: Any) -> list[Any]:
    if isinstance(node, list):
        return node
    if not isinstance(node, dict):
        return []
    keys = {"members", "member_list", "memberlist", "top_members", "top100", "rank_list", "rows", "items", "list"}
    for key, value in node.items():
        if str(key).lower() in keys and isinstance(value, list):
            return value
    return []


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _parse_window(name: str, node: Any, root: Any) -> ActivityWindow:
    members = []
    for item in _member_list(node):
        if not isinstance(item, dict):
            continue
        user_id = item.get("user_id", item.get("uin", item.get("qq", item.get("qqid"))))
        if user_id is None:
            continue
        members.append(
            {
                "user_id": _int_value(user_id),
                "nickname": str(item.get("nickname") or item.get("nick") or item.get("name") or ""),
                "activity_count": _int_value(
                    item.get(
                        "activity_count",
                        item.get(
                            "message_count",
                            item.get("msg_count", item.get("active_count", item.get("count", item.get("value", 0)))),
                        ),
                    )
                ),
            }
        )
    members = [item for item in members if item["user_id"] > 0]
    members.sort(key=lambda item: (-item["activity_count"], item["user_id"]))
    count_keys = {
        "active_member_count", "active_count", "activecount", "member_count", "membercount", "total_active", "total"
    }
    active_count = _first_value(node, count_keys)
    if active_count is None:
        active_count = _first_value(root, {f"{name}_active_count", f"{name}activecount"})
    if active_count is None:
        active_count = len(members)
    return ActivityWindow(name, _int_value(active_count), tuple(members[:100]))


def parse_activity_payload(payload: Any) -> tuple[ActivityWindow, ...]:
    """Normalize common QQ group activity response shapes.

    The endpoint is intentionally not guessed here. QQ has changed this private
    page API several times, so only a configured and inspected response is accepted.
    """
    yesterday_keys = {
        "yesterday", "daily", "day", "yesterday_active", "yesterdayactive", "yesterday_overview", "昨日", "昨日活跃"
    }
    seven_keys = {
        "seven_days", "sevendays", "last_7_days", "last7days", "weekly", "seven_day", "近七日", "近7天", "近七天"
    }
    yesterday = _named_node(payload, yesterday_keys)
    seven_days = _named_node(payload, seven_keys)
    if yesterday is None and _member_list(payload):
        yesterday = payload
    if yesterday is None or seven_days is None:
        raise ActivityResponseError("response does not contain yesterday and seven-day activity windows")
    windows = (
        _parse_window("yesterday", yesterday, payload),
        _parse_window("seven_days", seven_days, payload),
    )
    if not any(window.members or window.active_member_count for window in windows):
        raise ActivityResponseError("activity response contains no usable data")
    return windows


def _format_template(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        try:
            return value.format(**values)
        except (KeyError, ValueError):
            return value
    if isinstance(value, dict):
        return {key: _format_template(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [_format_template(item, values) for item in value]
    return value


class QQGroupActivityCollector:
    source = "qq_group_activity_http"

    def __init__(self, database: Database) -> None:
        self.database = database

    async def collect_group(
        self, gateway: OneBotGateway, group_id: int, report_day: date
    ) -> ActivityCollectionResult:
        if not settings.activity_enabled:
            self.database.record_activity_run(group_id, report_day, "disabled", self.source)
            return ActivityCollectionResult(group_id, report_day, "disabled")
        if not settings.activity_url:
            self.database.record_activity_run(group_id, report_day, "unconfigured", self.source)
            return ActivityCollectionResult(group_id, report_day, "unconfigured")
        if not self.database.group_stats_enabled(group_id):
            self.database.record_activity_run(group_id, report_day, "permission_disabled", self.source)
            return ActivityCollectionResult(group_id, report_day, "permission_disabled")

        try:
            web = await gateway.web_cookies("qun.qq.com")
            values = {
                "group_id": str(group_id),
                "gc": str(group_id),
                "day": report_day.isoformat(),
                "bkn": web["bkn"],
            }
            url = _format_template(settings.activity_url, values)
            request_data = _format_template(settings.activity_payload, values)
            headers = {
                "Accept": "application/json",
                "Referer": "https://qun.qq.com/",
                "Cookie": web["cookies"],
                "User-Agent": "QQGroupActivityCollector/1.0",
            }
            async with httpx.AsyncClient(timeout=settings.activity_timeout, follow_redirects=True) as client:
                response: httpx.Response | None = None
                for attempt in range(2):
                    try:
                        if settings.activity_method == "GET":
                            response = await client.get(url, params=request_data, headers=headers)
                        else:
                            response = await client.post(url, json=request_data, headers=headers)
                        response.raise_for_status()
                        break
                    except (httpx.TimeoutException, httpx.TransportError):
                        if attempt == 1:
                            raise
                        await asyncio.sleep(0.25)
                if response is None:
                    raise ActivityResponseError("empty QQ activity response")
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ActivityResponseError("QQ activity response is not JSON") from exc

            windows = parse_activity_payload(payload)
            for window in windows:
                self.database.save_activity_snapshot(
                    group_id,
                    report_day,
                    window.name,
                    window.active_member_count,
                    window.members,
                    self.source,
                )
            self.database.record_activity_run(group_id, report_day, "completed", self.source)
            return ActivityCollectionResult(group_id, report_day, "completed", windows)
        except (GatewayError, httpx.HTTPError, ActivityResponseError, ValueError) as exc:
            error = str(exc)[:1000]
            self.database.record_activity_run(group_id, report_day, "error", self.source, error)
            return ActivityCollectionResult(group_id, report_day, "error", error=error)

    async def collect_all(
        self, gateway: OneBotGateway, group_ids: Iterable[int], report_day: date
    ) -> list[ActivityCollectionResult]:
        return [
            await self.collect_group(gateway, int(group_id), report_day)
            for group_id in group_ids
        ]
