"""Shared HTML presentation and screenshot rendering for A-SOUL/Bilibili cards."""

from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from bot.config import RESOURCE_DIR, ROOT
from bot.services.web_screenshot import LocalWebScreenshotRenderer


WEB_PAGE_PATH = RESOURCE_DIR / "asoul_live_web.html"
SHORT_LINK_CONFIG_PATH = ROOT / "config" / "public-short-links.json"
VALID_SCHEDULE_VIEWS = frozenset({"today", "tomorrow", "week"})
VIEW_LABELS = {"today": "今日直播", "tomorrow": "明日直播", "week": "本周直播"}


def asoul_live_web_url(view: str) -> str | None:
    try:
        payload = json.loads(SHORT_LINK_CONFIG_PATH.read_text(encoding="utf-8"))
        host = str(payload["host"])
        code = str(payload["qq_schedule"])
        if code not in payload["links"]:
            return None
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    return f"{host}/{code}"


def serialize_schedule_day(target_day: date, items: Iterable[Any]) -> dict[str, Any]:
    rows = []
    for item in items:
        rows.append(
            {
                "time": item.starts_at.strftime("%H:%M"),
                "hosts": list(item.hosts),
                "content": str(item.content),
                "label": str(item.label),
                "highlighted": bool(item.highlighted),
                "highlight_style": str(item.highlight_style),
            }
        )
    return {
        "date": target_day.isoformat(),
        "date_label": target_day.strftime("%m月%d日"),
        "weekday": ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[target_day.weekday()],
        "items": rows,
    }


def schedule_payload(
    view: str,
    days: Iterable[tuple[date, Iterable[Any]]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    normalized = view if view in VALID_SCHEDULE_VIEWS else "today"
    serialized = [serialize_schedule_day(target_day, items) for target_day, items in days]
    return {
        "mode": "schedule",
        "view": normalized,
        "title": VIEW_LABELS[normalized],
        "generated_at": generated_at,
        "days": serialized,
        "event_count": sum(len(day["items"]) for day in serialized),
    }


def notification_payload(
    message: str,
    *,
    live: Mapping[str, Any] | None = None,
    dynamic: Mapping[str, Any] | None = None,
    video: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    lines = [line.strip() for line in str(message).splitlines() if line.strip()]
    if live is not None:
        details = dict(live)
        phase = "start" if str(details.get("phase") or "start") == "start" else "end"
        mode = f"live-{phase}"
    elif dynamic is not None:
        details = dict(dynamic)
        mode = "dynamic"
    elif video is not None:
        details = dict(video)
        mode = "video"
    else:
        url = lines[-1] if lines and lines[-1].startswith("http") else ""
        content_lines = lines[1:-1] if url else lines[1:]
        is_comment = bool(lines and lines[0].startswith("【B站评论区回复】"))
        details = {
            "author": lines[0].split("】", 1)[-1] if lines else "B站UP主",
            "text": "\n".join(content_lines[1:] if is_comment else content_lines),
            "url": url,
        }
        if is_comment:
            details["context"] = content_lines[0] if content_lines else "在评论区发布了回复"
        mode = "comment" if is_comment else "dynamic"
    details = {str(key): value for key, value in details.items()}
    return {"mode": mode, "message": message, "details": details}


def page_html(payload: Mapping[str, Any] | None = None, *, capture: bool = False) -> str:
    source = WEB_PAGE_PATH.read_text(encoding="utf-8")
    if payload is None:
        serialized = "null"
    else:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        serialized = serialized.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return source.replace("__ASOUL_PAYLOAD_JSON__", serialized).replace(
        "__ASOUL_CAPTURE_CLASS__", "capture-mode" if capture else ""
    )


class ASoulWebRenderer(LocalWebScreenshotRenderer):
    """Render the shared HTML surface through one serialized, warm browser page."""

    WIDTH = 1080

    def __init__(
        self,
        output_dir: Path,
        media_loader: Callable[[str], Any] | None = None,
        sticker_selector: Callable[[Iterable[str]], Path | None] | None = None,
    ) -> None:
        super().__init__(output_dir, "asoul_html")
        self.media_loader = media_loader
        self.sticker_selector = sticker_selector

    async def render_schedule(
        self,
        view: str,
        days: Iterable[tuple[date, Iterable[Any]]],
        *,
        generated_at: str,
    ) -> Path:
        payload = schedule_payload(view, days, generated_at=generated_at)
        return await self.render_payload(self.localize_schedule_stickers(payload), "schedule")

    async def render_notification(
        self,
        message: str,
        *,
        live: Mapping[str, Any] | None = None,
        dynamic: Mapping[str, Any] | None = None,
        video: Mapping[str, Any] | None = None,
    ) -> Path:
        payload = notification_payload(message, live=live, dynamic=dynamic, video=video)
        return await self.render_payload(payload, str(payload["mode"]))

    async def render_payload(self, payload: Mapping[str, Any], prefix: str) -> Path:
        localized = await asyncio.to_thread(self._localize_media, payload)
        return await self.render_html(page_html(localized, capture=True), prefix)

    def _localize_media(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        localized = dict(payload)
        details = localized.get("details")
        if not isinstance(details, Mapping) or self.media_loader is None:
            return localized
        values = dict(details)
        for key in ("avatar_url", "cover_url"):
            values[key] = self._data_url(str(values.get(key) or ""))
        for key in ("image_urls", "quote_image_urls"):
            raw = values.get(key)
            try:
                urls = json.loads(str(raw or "[]"))
            except json.JSONDecodeError:
                urls = str(raw or "").split("|")
            if isinstance(urls, list):
                values[key] = json.dumps(
                    [data for url in urls if (data := self._data_url(str(url)))],
                    ensure_ascii=False,
                )
        for key in ("rich_nodes", "quote_rich_nodes"):
            values[key] = self._localize_rich_nodes(values.get(key))
        localized["details"] = values
        return localized

    def _localize_rich_nodes(self, value: Any) -> str:
        try:
            nodes = json.loads(str(value or "[]"))
        except json.JSONDecodeError:
            nodes = []
        if not isinstance(nodes, list):
            return "[]"
        localized: list[dict[str, str]] = []
        for raw_node in nodes:
            if not isinstance(raw_node, Mapping):
                continue
            node = {str(key): str(item) for key, item in raw_node.items() if item is not None}
            if node.get("type") == "emoji":
                node["url"] = self._data_url(node.get("url", ""))
            localized.append(node)
        return json.dumps(localized, ensure_ascii=False, separators=(",", ":"))

    def localize_schedule_stickers(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Embed one random host-matched PNG per schedule item for web and QQ."""
        localized = dict(payload)
        days = []
        for raw_day in payload.get("days", []):
            day = dict(raw_day)
            items = []
            for raw_item in raw_day.get("items", []):
                item = dict(raw_item)
                sticker = self.sticker_selector(item.get("hosts", ())) if self.sticker_selector else None
                item["sticker_url"] = self._local_file_data_url(sticker)
                items.append(item)
            day["items"] = items
            days.append(day)
        localized["days"] = days
        return localized

    def _localize_schedule_stickers(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Compatibility alias for callers predating the public web payload contract."""
        return self.localize_schedule_stickers(payload)

    @staticmethod
    def _local_file_data_url(path: Path | None) -> str:
        if path is None or path.suffix.lower() != ".png":
            return ""
        try:
            content = path.read_bytes()
        except OSError:
            return ""
        return "data:image/png;base64," + base64.b64encode(content).decode("ascii")

    def _data_url(self, url: str) -> str:
        if not url.startswith(("https://", "http://")) or self.media_loader is None:
            return ""
        source = self.media_loader(url)
        if source is None:
            return ""
        buffer = BytesIO()
        source.convert("RGBA").save(buffer, format="PNG", optimize=False)
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
