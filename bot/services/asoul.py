from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import httpx
except ImportError as exc:
    httpx = None  # type: ignore[assignment]
    HTTPX_ERROR = exc
else:
    HTTPX_ERROR = None

try:
    from bilibili_api import Credential, comment, login_v2, user
except ImportError as exc:  # The schedule functions remain usable without the optional client.
    Credential = None  # type: ignore[assignment,misc]
    comment = None  # type: ignore[assignment,misc]
    login_v2 = None  # type: ignore[assignment,misc]
    user = None  # type: ignore[assignment,misc]
    BILIBILI_API_ERROR = exc
else:
    BILIBILI_API_ERROR = None

from bot.config import ROOT, settings
from bot.db import Database


CALENDAR_URL = "https://asoul.love/calendar.ics"
CALENDAR_CACHE_KEY = "calendar_cache"
HIGHLIGHTS_KEY = "schedule_highlights"
MONITOR_KEY = "bilibili_monitor"
CREDENTIAL_KEY = "bilibili_credential"
BILIBILI_LIVE_STATUS_URL = "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids"
MEMBERS = ("向晚", "贝拉", "珈乐", "嘉然", "乃琳", "心宜", "思诺")
COMMENT_MAX_AGE_SECONDS = 6 * 60 * 60
COMMENT_SCAN_TARGETS_PER_POLL = 2
COMMENT_SEEN_LIMIT = 300


@dataclass(frozen=True, slots=True)
class ScheduleItem:
    starts_at: datetime
    hosts: tuple[str, ...]
    content: str
    label: str
    highlighted: bool = False
    highlight_style: str = ""

    @property
    def key(self) -> str:
        payload = f"{self.starts_at:%Y-%m-%dT%H:%M}|{self.content}|{self.label}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class ASoulService:
    """NoneBot-facing A-SOUL schedule and read-only Bilibili integration."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.timezone = ZoneInfo(settings.timezone)
        self._calendar_lock = asyncio.Lock()
        self._live_notification_details: dict[str, dict[str, str]] = {}
        self._dynamic_notification_details: dict[str, dict[str, str]] = {}
        self._video_notification_details: dict[str, dict[str, str]] = {}
        self._comment_notification_details: dict[str, dict[str, str]] = {}

    async def schedule_for_days(self, first_day: date, last_day: date) -> dict[date, list[ScheduleItem]]:
        events = await self._calendar_events()
        highlights = self._highlight_records()
        grouped: dict[date, dict[tuple[datetime, str, str], ScheduleItem]] = {}
        for event in events:
            starts_at = event["starts_at"]
            if not first_day <= starts_at.date() <= last_day:
                continue
            hosts = self._extract_hosts(event)
            content = self._extract_content(event, hosts)
            label = self._classify(event, content)
            item = ScheduleItem(starts_at, tuple(hosts), content, label)
            key = (starts_at, content, label)
            day_items = grouped.setdefault(starts_at.date(), {})
            existing = day_items.get(key)
            if existing is not None:
                item = ScheduleItem(
                    starts_at,
                    tuple(dict.fromkeys((*existing.hosts, *item.hosts))),
                    content,
                    label,
                )
            style = highlights.get(item.key, "")
            day_items[key] = ScheduleItem(
                item.starts_at, item.hosts, item.content, item.label, bool(style), style
            )
        return {
            target: sorted(items.values(), key=lambda item: item.starts_at)
            for target, items in grouped.items()
        }

    async def schedule_for_day(self, target_day: date) -> list[ScheduleItem]:
        return (await self.schedule_for_days(target_day, target_day)).get(target_day, [])

    def render_schedule(self, target_day: date, title: str, items: list[ScheduleItem]) -> str:
        if not items:
            return f"{target_day:%Y-%m-%d} 暂无{title}安排。"
        lines = [f"【{title}】{target_day:%Y-%m-%d}"]
        for item in items:
            marker = f" ⭐特别关注·{item.highlight_style}" if item.highlighted else ""
            hosts = " / ".join(item.hosts) if item.hosts else "待确认"
            lines.append(f"{item.starts_at:%H:%M}｜{hosts}｜{item.content}（{item.label}）{marker}")
        return "\n".join(lines)

    def _highlight_records(self) -> dict[str, str]:
        raw = self.db.asoul_state(HIGHLIGHTS_KEY, {})
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): str(style)
            for key, style in raw.items()
            if str(style) in {"粉色", "红色", "白金色"}
        }

    def set_highlight(self, item: ScheduleItem, style: str) -> None:
        records = self._highlight_records()
        records[item.key] = style
        self.db.set_asoul_state(HIGHLIGHTS_KEY, records)

    def remove_highlight(self, item: ScheduleItem) -> bool:
        records = self._highlight_records()
        if records.pop(item.key, None) is None:
            return False
        self.db.set_asoul_state(HIGHLIGHTS_KEY, records)
        return True

    def highlight_records(self) -> dict[str, str]:
        return self._highlight_records()

    def remove_highlight_key(self, key: str) -> bool:
        records = self._highlight_records()
        if records.pop(key, None) is None:
            return False
        self.db.set_asoul_state(HIGHLIGHTS_KEY, records)
        return True

    async def _calendar_events(self) -> list[dict[str, Any]]:
        async with self._calendar_lock:
            cached = self.db.asoul_state(CALENDAR_CACHE_KEY, {})
            if isinstance(cached, dict) and isinstance(cached.get("text"), str):
                if time.time() - float(cached.get("fetched_at", 0) or 0) < 1800:
                    return self._parse_ics(cached["text"])
            try:
                if httpx is None:
                    raise RuntimeError("httpx is unavailable") from HTTPX_ERROR
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    response = await client.get(CALENDAR_URL, headers={"User-Agent": "asasfans"})
                    response.raise_for_status()
                text = response.text
                self.db.set_asoul_state(CALENDAR_CACHE_KEY, {"fetched_at": time.time(), "text": text})
                return self._parse_ics(text)
            except Exception:
                if isinstance(cached, dict) and isinstance(cached.get("text"), str):
                    return self._parse_ics(cached["text"])
                raise

    def _parse_ics(self, source: str) -> list[dict[str, Any]]:
        lines: list[str] = []
        for raw in source.splitlines():
            if raw.startswith((" ", "\t")) and lines:
                lines[-1] += raw[1:]
            else:
                lines.append(raw.rstrip("\r"))
        events: list[dict[str, Any]] = []
        current: dict[str, str] | None = None
        for line in lines:
            if line == "BEGIN:VEVENT":
                current = {}
                continue
            if line == "END:VEVENT" and current is not None:
                event = self._event_from_ics(current)
                if event is not None:
                    events.append(event)
                current = None
                continue
            if current is None or ":" not in line:
                continue
            key, value = line.split(":", 1)
            current[key.split(";", 1)[0].upper()] = value
        return events

    def _event_from_ics(self, raw: dict[str, str]) -> dict[str, Any] | None:
        value = raw.get("DTSTART", "")
        if len(value) == 8 or not value:
            return None
        try:
            fmt = "%Y%m%dT%H%M%S" if len(value.rstrip("Z")) == 15 else "%Y%m%dT%H%M"
            starts_at = datetime.strptime(value.rstrip("Z"), fmt)
            starts_at = starts_at.replace(tzinfo=ZoneInfo("UTC") if value.endswith("Z") else self.timezone)
            starts_at = starts_at.astimezone(self.timezone)
        except ValueError:
            return None
        if raw.get("STATUS", "").upper() == "CANCELLED":
            return None
        event = {
            "starts_at": starts_at,
            "summary": self._decode_ics(raw.get("SUMMARY", "")),
            "description": self._decode_ics(raw.get("DESCRIPTION", "")),
            "location": self._decode_ics(raw.get("LOCATION", "")),
            "categories": self._decode_ics(raw.get("CATEGORIES", "")),
            "url": raw.get("URL", ""),
        }
        haystack = " ".join(str(event[name]).lower() for name in ("summary", "description", "location", "categories"))
        if "live.bilibili.com" not in event["url"].lower() and not any(
            token in haystack for token in ("直播", "开播", "突击", "歌会", "综艺", "杂谈", "联动", "游戏", "演唱")
        ):
            return None
        return event

    @staticmethod
    def _decode_ics(value: str) -> str:
        return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")

    def _extract_hosts(self, event: dict[str, Any]) -> list[str]:
        text = " ".join(str(event[key]).lower() for key in ("summary", "description", "location"))
        if any(alias in text for alias in ("a-soul", "asoul", "一个魂")):
            return ["嘉然", "乃琳", "贝拉"]
        return [member for member in MEMBERS if member.lower() in text]

    @staticmethod
    def _extract_content(event: dict[str, Any], hosts: list[str]) -> str:
        candidate = str(event["summary"] or event["description"].split("\n", 1)[0] or "直播内容待定")
        if "：" in candidate:
            candidate = candidate.split("：", 1)[1]
        elif ":" in candidate:
            candidate = candidate.split(":", 1)[1]
        for token in (*hosts, "直播", "开播", "A-SOUL", "ASOUL"):
            candidate = candidate.replace(token, "")
        return " ".join(candidate.replace("【", " ").replace("】", " ").split()).strip(" -|：:") or "直播内容待定"

    @staticmethod
    def _classify(event: dict[str, Any], content: str) -> str:
        text = f"{event['summary']} {content}".lower()
        for token, label in (("突击", "突击"), ("演唱", "演出"), ("歌会", "歌会"), ("游戏", "游戏"), ("联动", "联动"), ("杂谈", "杂谈"), ("2d", "2D")):
            if token in text:
                return label
        return "直播"

    def _credential(self) -> Credential | None:
        self._require_bilibili_api()
        raw = self.db.asoul_state(CREDENTIAL_KEY, {})
        if not isinstance(raw, dict) or not raw.get("sessdata"):
            return None
        values = {key: str(value) for key, value in raw.items() if key in {"sessdata", "bili_jct", "buvid3", "buvid4", "dedeuserid", "ac_time_value"} and value}
        return Credential(**values)

    def credential_available(self) -> bool:
        try:
            credential = self._credential()
        except RuntimeError:
            return False
        return credential is not None and credential.has_sessdata()

    @staticmethod
    def _require_bilibili_api() -> None:
        if BILIBILI_API_ERROR is not None:
            raise RuntimeError("bilibili-api-python is unavailable") from BILIBILI_API_ERROR

    def _user(self, uid: str) -> user.User:
        self._require_bilibili_api()
        credential = self._credential()
        return user.User(int(uid), credential=credential) if credential is not None else user.User(int(uid))

    def save_credential(self, cookies: dict[str, Any]) -> None:
        allowed = {key: str(value) for key, value in cookies.items() if key in {"SESSDATA", "bili_jct", "buvid3", "buvid4", "DedeUserID", "ac_time_value"} and value}
        normalized = {
            "sessdata": allowed.get("SESSDATA", ""), "bili_jct": allowed.get("bili_jct", ""),
            "buvid3": allowed.get("buvid3", ""), "buvid4": allowed.get("buvid4", ""),
            "dedeuserid": allowed.get("DedeUserID", ""), "ac_time_value": allowed.get("ac_time_value", ""),
        }
        self.db.set_asoul_state(CREDENTIAL_KEY, normalized)

    def clear_credential(self) -> None:
        self.db.set_asoul_state(CREDENTIAL_KEY, {})

    async def create_qr_login(self) -> login_v2.QrCodeLogin:
        self._require_bilibili_api()
        login = login_v2.QrCodeLogin(platform=login_v2.QrCodeLoginChannel.WEB)
        await login.generate_qrcode()
        return login

    async def wait_for_qr_login(self, login: login_v2.QrCodeLogin, timeout_seconds: int = 180) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            state = await login.check_state()
            if state == login_v2.QrCodeLoginEvents.DONE:
                self.save_credential(login.get_credential().get_cookies())
                return True
            if state == login_v2.QrCodeLoginEvents.TIMEOUT:
                return False
            await asyncio.sleep(2)
        return False

    async def fetch_dynamics(self, uid: str) -> list[dict[str, str]]:
        source_user = self._user(uid)
        payload = await source_user.get_dynamics_new()
        try:
            profile_payload = await source_user.get_user_info()
        except Exception:
            profile_payload = {}
        profile = profile_payload if isinstance(profile_payload, dict) else {}
        items = payload.get("items") or []
        ranked: list[tuple[int, int, dict[str, str]]] = []
        for item in items:
            modules = item.get("modules") or {}
            author = modules.get("module_author") or {}
            dynamic = modules.get("module_dynamic") or {}
            if not isinstance(author, dict) or not isinstance(dynamic, dict):
                continue
            text, emoji_urls = self._dynamic_summary(dynamic)
            rich_nodes = self._dynamic_rich_nodes(dynamic)
            major = dynamic.get("major") or {}
            archive = major.get("archive") if isinstance(major, dict) else {}
            if not isinstance(archive, dict):
                archive = {}
            identifier = str(item.get("id_str") or "")
            if not identifier:
                continue
            quoted_author = ""
            quoted_text = ""
            quoted_rich_nodes: list[dict[str, str]] = []
            quoted_images: list[str] = []
            original = item.get("orig")
            if isinstance(original, dict):
                original_modules = original.get("modules") or {}
                original_author = original_modules.get("module_author") or {}
                original_dynamic = original_modules.get("module_dynamic") or {}
                if isinstance(original_author, dict) and isinstance(original_dynamic, dict):
                    quoted_author = str(original_author.get("name") or "")
                    quoted_text, _ = self._dynamic_summary(original_dynamic)
                    quoted_rich_nodes = self._dynamic_rich_nodes(original_dynamic)
                    quoted_images = self._dynamic_image_urls(original_dynamic)
            notification_kind = self._dynamic_notification_kind(item, dynamic, major, archive)
            is_video_submission = notification_kind == "video"
            jump = (
                archive.get("jump_url")
                or (f"//www.bilibili.com/video/{archive['bvid']}" if archive.get("bvid") else "")
                or self._dynamic_jump_url(dynamic)
                or item.get("basic", {}).get("jump_url")
                or f"//t.bilibili.com/{identifier}"
            )
            record = {
                "id": identifier,
                "uid": str(author.get("mid") or profile.get("mid") or uid),
                "author": str(author.get("name") or profile.get("name") or uid),
                "avatar_url": str(author.get("face") or profile.get("face") or ""),
                "profile": str(profile.get("sign") or ""),
                "text": self._normalize_multiline_text(archive.get("title") or text),
                "emoji_labels": "|".join(label for label, _ in emoji_urls),
                "emoji_urls": "|".join(url for _, url in emoji_urls),
                "quote_author": quoted_author,
                "quote_text": self._normalize_multiline_text(quoted_text),
                "url": self._absolute_url(str(jump)),
                "notification_kind": notification_kind,
                "cover_url": self._absolute_url(str(archive.get("cover") or "")),
            }
            published_at = self._dynamic_pub_ts(item, author)
            basic = item.get("basic") or {}
            comment_oid = str(basic.get("comment_id_str") or basic.get("comment_id") or "")
            comment_type = str(basic.get("comment_type") or "")
            if published_at:
                record["published_at"] = str(published_at)
            if comment_oid and comment_type:
                record["comment_oid"] = comment_oid
                record["comment_type"] = comment_type
            if rich_nodes:
                record["rich_nodes"] = json.dumps(rich_nodes, ensure_ascii=False, separators=(",", ":"))
            image_urls = self._dynamic_image_urls(dynamic)
            if image_urls:
                record["image_urls"] = json.dumps(image_urls, ensure_ascii=False, separators=(",", ":"))
            if quoted_rich_nodes:
                record["quote_rich_nodes"] = json.dumps(quoted_rich_nodes, ensure_ascii=False, separators=(",", ":"))
            if quoted_images:
                record["quote_image_urls"] = json.dumps(quoted_images, ensure_ascii=False, separators=(",", ":"))
            record.update(self._dynamic_reservation(dynamic))
            if is_video_submission:
                record["description"] = self._normalize_multiline_text(archive.get("desc"))
            ranked.append((self._dynamic_pub_ts(item, author), len(ranked), record))
        ranked.sort(key=lambda entry: (-entry[0], entry[1]))
        return [record for _, _, record in ranked]

    @staticmethod
    def _dynamic_notification_kind(
        item: dict[str, Any],
        dynamic: dict[str, Any],
        major: object,
        archive: dict[str, Any],
    ) -> str:
        """Map Bilibili feed variants to the push behavior used by the monitor."""
        if archive.get("bvid") or archive.get("jump_url"):
            return "video"

        additional = dynamic.get("additional") or {}
        if isinstance(additional, dict) and isinstance(additional.get("reserve"), dict):
            return "reservation"

        major_map = major if isinstance(major, dict) else {}
        type_values = (
            item.get("type"),
            item.get("dynamic_type"),
            dynamic.get("type"),
            major_map.get("type"),
            major_map.get("type_name"),
        )
        type_tokens = {str(value or "").upper() for value in type_values}
        if (
            bool(major_map.get("live_rcmd"))
            or bool(major_map.get("live"))
            or any("LIVE_RCMD" in value or value in {"LIVE", "DYNAMIC_TYPE_LIVE"} for value in type_tokens)
        ):
            return "live"

        if isinstance(item.get("orig"), dict) or isinstance(major_map.get("forward"), dict):
            return "forward"
        return "dynamic"

    @staticmethod
    def _dynamic_pub_ts(item: dict[str, Any], author: dict[str, Any]) -> int:
        for value in (author.get("pub_ts"), item.get("pub_ts")):
            try:
                return max(int(value), 0)
            except (TypeError, ValueError):
                continue
        return 0

    @staticmethod
    def _normalize_multiline_text(value: object) -> str:
        source = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(" ".join(line.split()) for line in source.split("\n")).strip()

    @staticmethod
    def _dynamic_summary(dynamic: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
        rich_nodes = ASoulService._dynamic_rich_nodes(dynamic)
        text = "".join(str(node.get("text") or "") for node in rich_nodes)
        emoji_urls = [
            (str(node.get("text") or ""), str(node.get("url") or ""))
            for node in rich_nodes
            if node.get("type") == "emoji" and node.get("text") and node.get("url")
        ]
        if text:
            return text, emoji_urls
        desc = dynamic.get("desc") or {}
        major = dynamic.get("major") or {}
        if not isinstance(desc, dict):
            desc = {}
        if not isinstance(major, dict):
            major = {}
        opus = major.get("opus") or {}
        archive = major.get("archive") or {}
        if not isinstance(opus, dict):
            opus = {}
        if not isinstance(archive, dict):
            archive = {}
        summary = opus.get("summary") or {}
        if not isinstance(summary, dict):
            summary = {}
        details = desc if desc.get("text") else summary
        text = str(details.get("text") or archive.get("title") or "（无文字内容）")
        nodes = details.get("rich_text_nodes") or []
        emoji_urls: list[tuple[str, str]] = []
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                emoji = node.get("emoji") or {}
                if not isinstance(emoji, dict):
                    continue
                url = str(emoji.get("webp_url") or emoji.get("icon_url") or emoji.get("gif_url") or "")
                label = str(emoji.get("text") or node.get("text") or "")
                if url and label and (label, url) not in emoji_urls:
                    emoji_urls.append((label, url))
        return text, emoji_urls

    @staticmethod
    def _dynamic_rich_nodes(dynamic: dict[str, Any]) -> list[dict[str, str]]:
        desc = dynamic.get("desc") or {}
        major = dynamic.get("major") or {}
        if not isinstance(desc, dict):
            desc = {}
        if not isinstance(major, dict):
            major = {}
        opus = major.get("opus") or {}
        if not isinstance(opus, dict):
            opus = {}
        summary = opus.get("summary") or {}
        if not isinstance(summary, dict):
            summary = {}
        raw_nodes = summary.get("rich_text_nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raw_nodes = desc.get("rich_text_nodes")
        if not isinstance(raw_nodes, list):
            return []
        result: list[dict[str, str]] = []
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                continue
            emoji = raw_node.get("emoji") or {}
            if not isinstance(emoji, dict):
                emoji = {}
            text = str(raw_node.get("text") or emoji.get("text") or "")
            emoji_url = str(emoji.get("webp_url") or emoji.get("icon_url") or emoji.get("gif_url") or "")
            jump_url = ASoulService._absolute_url(str(raw_node.get("jump_url") or ""))
            node_type = str(raw_node.get("type") or "").upper()
            if not jump_url and node_type.endswith("_AT"):
                mention_uid = str(raw_node.get("rid") or "").strip()
                if mention_uid.isdigit():
                    jump_url = f"https://space.bilibili.com/{mention_uid}"
            if emoji_url:
                result.append({"type": "emoji", "text": text, "url": ASoulService._absolute_url(emoji_url)})
            elif text and jump_url.startswith(("https://", "http://")):
                result.append({"type": "link", "text": text, "url": jump_url})
            elif text:
                result.append({"type": "text", "text": text})
        source_text = str(summary.get("text") or desc.get("text") or "")
        if source_text and "".join(str(node.get("text") or "") for node in result) != source_text:
            return []
        return result

    @staticmethod
    def _dynamic_image_urls(dynamic: dict[str, Any]) -> list[str]:
        major = dynamic.get("major") or {}
        if not isinstance(major, dict):
            return []
        urls: list[str] = []
        seen: set[str] = set()

        def add(value: object) -> None:
            url = ASoulService._absolute_url(str(value or "").strip())
            if url.startswith(("https://", "http://")) and url not in seen:
                seen.add(url)
                urls.append(url)

        opus = major.get("opus") or {}
        if isinstance(opus, dict):
            for picture in opus.get("pics") or []:
                if isinstance(picture, dict):
                    add(picture.get("url") or picture.get("orig_url") or picture.get("img_src"))
        draw = major.get("draw") or {}
        if isinstance(draw, dict):
            for picture in draw.get("items") or []:
                if isinstance(picture, dict):
                    add(picture.get("src") or picture.get("url") or picture.get("img_src"))
        return urls

    @staticmethod
    def _additional_text(value: object) -> str:
        return str(value.get("text") or "") if isinstance(value, dict) else ""

    def _dynamic_reservation(self, dynamic: dict[str, Any]) -> dict[str, str]:
        additional = dynamic.get("additional") or {}
        if not isinstance(additional, dict):
            return {}
        reserve = additional.get("reserve") or {}
        if not isinstance(reserve, dict) or not reserve:
            return {}
        subtitles = [
            self._additional_text(reserve.get("desc1")),
            self._additional_text(reserve.get("desc2")),
        ]
        if not any(subtitles):
            total = self._nonnegative_int(reserve.get("reserve_total"))
            if total is not None:
                subtitles.append(f"{total}人预约")
        button = reserve.get("button") or {}
        action = ""
        if isinstance(button, dict):
            action = self._additional_text(button.get("uncheck")) or self._additional_text(button)
        return {
            "reserve_title": self._normalize_multiline_text(reserve.get("title")),
            "reserve_subtitle": " · ".join(part for part in subtitles if part),
            "reserve_action": action or "预约",
        }

    @staticmethod
    def _dynamic_jump_url(dynamic: dict[str, Any]) -> str:
        major = dynamic.get("major") or {}
        if not isinstance(major, dict):
            return ""
        opus = major.get("opus") or {}
        return str(opus.get("jump_url") or "") if isinstance(opus, dict) else ""

    async def fetch_dynamic(self, uid: str) -> dict[str, str] | None:
        items = await self.fetch_dynamics(uid)
        return items[0] if items else None

    async def fetch_videos(self, uid: str) -> list[dict[str, str]]:
        payload = await self._user(uid).get_videos(ps=10)
        items = (payload.get("list") or {}).get("vlist") or []
        result: list[dict[str, str]] = []
        for item in items:
            bvid = str(item.get("bvid") or "")
            if not bvid:
                continue
            result.append(
                {
                    "id": bvid,
                    "uid": str(item.get("mid") or uid),
                    "author": str(item.get("author") or uid),
                    "text": str(item.get("title") or "（无标题）"),
                    "description": self._normalize_multiline_text(item.get("description")),
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "cover_url": self._absolute_url(str(item.get("pic") or "")),
                }
            )
        return result

    async def fetch_video(self, uid: str) -> dict[str, str] | None:
        items = await self.fetch_videos(uid)
        return items[0] if items else None

    async def fetch_live(self, uid: str) -> dict[str, str] | None:
        return (await self.fetch_live_statuses((uid,))).get(str(uid))

    async def fetch_live_statuses(self, uids: tuple[str, ...]) -> dict[str, dict[str, str]]:
        """Fetch all monitored live rooms in one public, read-only request."""
        if not uids:
            return {}
        if httpx is None:
            raise RuntimeError("httpx is unavailable") from HTTPX_ERROR
        params = [("uids[]", str(uid)) for uid in uids]
        headers = {"User-Agent": "Mozilla/5.0"}
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            response = await client.get(BILIBILI_LIVE_STATUS_URL, params=params)
            response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("Bilibili live-status payload is invalid")
        statuses: dict[str, dict[str, str]] = {}
        for uid in uids:
            raw = data.get(str(uid))
            if not isinstance(raw, dict):
                continue
            room_id = str(raw.get("room_id") or raw.get("roomid") or "")
            if not room_id:
                continue
            statuses[str(uid)] = {
                "id": room_id,
                "uid": str(raw.get("uid") or uid),
                "author": str(raw.get("uname") or uid),
                "text": str(raw.get("title") or "直播间"),
                "url": f"https://live.bilibili.com/{room_id}",
                "live": "1" if str(raw.get("live_status") or "0") == "1" else "0",
                "avatar_url": str(raw.get("face") or ""),
                "cover_url": str(raw.get("cover_from_user") or raw.get("cover") or raw.get("user_cover") or ""),
                "online": str(raw.get("online") or "0"),
                "live_time": str(raw.get("live_time") or ""),
                "area": str(raw.get("area_v2_name") or raw.get("area_name") or ""),
            }
        return statuses

    async def fetch_profile_stats(self, uid: str) -> dict[str, str]:
        relation, up_stat = await asyncio.gather(
            self._user(uid).get_relation_info(),
            self._user(uid).get_up_stat(),
            return_exceptions=True,
        )
        archive = up_stat.get("archive") if isinstance(up_stat, dict) else {}
        stats: dict[str, str] = {}
        values = (
            ("likes", up_stat.get("likes") if isinstance(up_stat, dict) else None),
            ("following", relation.get("following") if isinstance(relation, dict) else None),
            ("followers", relation.get("follower") if isinstance(relation, dict) else None),
            ("views", archive.get("view") if isinstance(archive, dict) else None),
        )
        for key, value in values:
            number = self._nonnegative_int(value)
            if number is not None:
                stats[key] = str(number)
        return stats

    async def _enrich_creator_notification(self, item: dict[str, str]) -> dict[str, str]:
        """Add independently available creator data without discarding the notification."""
        uid = str(item.get("uid") or "")
        if not uid:
            return dict(item)
        profile_payload, stats_payload = await asyncio.gather(
            self._user(uid).get_user_info(),
            self.fetch_profile_stats(uid),
            return_exceptions=True,
        )
        profile = profile_payload if isinstance(profile_payload, dict) else {}
        stats = stats_payload if isinstance(stats_payload, dict) else {}
        return {
            **item,
            "uid": str(profile.get("mid") or uid),
            "author": str(item.get("author") or profile.get("name") or uid),
            "avatar_url": str(item.get("avatar_url") or profile.get("face") or ""),
            "profile": str(profile.get("sign") or item.get("profile") or ""),
            **stats,
        }

    async def enrich_live_notification(self, live: dict[str, str]) -> dict[str, str]:
        return await self._enrich_creator_notification(live)

    def live_notification_details(self, message: str) -> dict[str, str] | None:
        details = self._live_notification_details.get(message)
        return dict(details) if details is not None else None

    def _remember_live_notification(self, message: str, details: dict[str, str], phase: str) -> None:
        self._live_notification_details[message] = {**details, "phase": phase}
        while len(self._live_notification_details) > 100:
            self._live_notification_details.pop(next(iter(self._live_notification_details)))

    def dynamic_notification_details(self, message: str) -> dict[str, str] | None:
        details = self._dynamic_notification_details.get(message)
        return dict(details) if details is not None else None

    def _remember_dynamic_notification(self, message: str, details: dict[str, str]) -> None:
        self._dynamic_notification_details[message] = dict(details)
        while len(self._dynamic_notification_details) > 100:
            self._dynamic_notification_details.pop(next(iter(self._dynamic_notification_details)))

    async def enrich_video_notification(self, video: dict[str, str]) -> dict[str, str]:
        return await self._enrich_creator_notification(video)

    async def enrich_dynamic_notification(self, dynamic: dict[str, str]) -> dict[str, str]:
        return await self._enrich_creator_notification(dynamic)

    def video_notification_details(self, message: str) -> dict[str, str] | None:
        details = self._video_notification_details.get(message)
        return dict(details) if details is not None else None

    def _remember_video_notification(self, message: str, details: dict[str, str]) -> None:
        self._video_notification_details[message] = dict(details)
        while len(self._video_notification_details) > 100:
            self._video_notification_details.pop(next(iter(self._video_notification_details)))

    def comment_notification_details(self, message: str) -> dict[str, str] | None:
        details = self._comment_notification_details.get(message)
        return dict(details) if details is not None else None

    def _remember_comment_notification(self, message: str, details: dict[str, str]) -> None:
        self._comment_notification_details[message] = dict(details)
        while len(self._comment_notification_details) > 100:
            self._comment_notification_details.pop(next(iter(self._comment_notification_details)))

    @staticmethod
    def latest_comment_resource(
        dynamics: list[dict[str, str]], observed_at: int | None = None
    ) -> dict[str, str] | None:
        now = int(time.time()) if observed_at is None else int(observed_at)
        for item in dynamics:
            try:
                published_at = int(item.get("published_at") or 0)
                comment_oid = int(item.get("comment_oid") or 0)
                comment_type = int(item.get("comment_type") or 0)
            except (TypeError, ValueError):
                continue
            age = now - published_at
            if (
                item.get("notification_kind") != "live"
                and 0 <= age < COMMENT_MAX_AGE_SECONDS
                and comment_oid > 0
                and comment_type > 0
            ):
                return item
        return None

    async def fetch_latest_comments(self, resource: dict[str, str]) -> list[dict[str, str]]:
        """Read one recent comment page and filter authors by the comment target set."""
        self._require_bilibili_api()
        watched = set(settings.asoul_bili_comment_target_uids)
        if not watched:
            return []
        oid = int(resource["comment_oid"])
        try:
            resource_type = comment.CommentResourceType(int(resource["comment_type"]))
        except ValueError:
            return []
        payload = await comment.get_comments(
            oid,
            resource_type,
            page_index=1,
            order=comment.OrderType.TIME,
            credential=self._credential(),
        )
        observed: list[dict[str, str]] = []
        seen_reply_ids: set[str] = set()
        roots = [*(payload.get("top_replies") or []), *(payload.get("replies") or [])]
        for root in roots:
            for row in (root, *(root.get("replies") or [])):
                member = row.get("member") or {}
                author_uid = str(member.get("mid") or "")
                if author_uid not in watched:
                    continue
                reply_id = str(row.get("rpid") or "")
                if not reply_id or reply_id in seen_reply_ids:
                    continue
                content = row.get("content") or {}
                text, rich_nodes = self._comment_content(
                    str(content.get("message") or ""),
                    content.get("emote"),
                )
                if text:
                    seen_reply_ids.add(reply_id)
                    observed.append(
                        {
                            "id": reply_id,
                            "author_uid": author_uid,
                            "author": str(member.get("uname") or author_uid),
                            "avatar_url": str(member.get("avatar") or ""),
                            "profile": str(member.get("sign") or ""),
                            "text": text,
                            "rich_nodes": rich_nodes,
                        }
                    )
        return [row for row in observed if row["id"]]

    @classmethod
    def _comment_content(
        cls,
        message: str,
        raw_emotes: object,
    ) -> tuple[str, str]:
        source = cls._normalize_multiline_text(message)
        emotes = raw_emotes if isinstance(raw_emotes, dict) else {}
        nodes: list[dict[str, str]] = []
        plain_parts: list[str] = []
        cursor = 0
        while cursor < len(source):
            matches = []
            for raw_label, value in emotes.items():
                label = str(raw_label)
                index = source.find(label, cursor) if label else -1
                if index >= 0:
                    matches.append((index, label, value))
            if not matches:
                tail = source[cursor:]
                if tail:
                    nodes.append({"type": "text", "text": tail})
                    plain_parts.append(tail)
                break
            index, label, value = min(matches, key=lambda item: item[0])
            if index > cursor:
                text = source[cursor:index]
                nodes.append({"type": "text", "text": text})
                plain_parts.append(text)
            emote = value if isinstance(value, dict) else {}
            url = cls._absolute_url(str(emote.get("url") or ""))
            short_label = str(emote.get("jump_title") or "").strip()
            if not short_label:
                short_label = label.strip("[]").rsplit("_", 1)[-1] or "表情"
            if url:
                nodes.append({"type": "emoji", "text": label, "url": url})
            else:
                nodes.append({"type": "text", "text": f"[{short_label}]"})
            plain_parts.append(f"[{short_label}]")
            cursor = index + len(label)
        return "".join(plain_parts).strip(), json.dumps(
            nodes,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _comment_context(resource: dict[str, str]) -> str:
        owner = str(resource.get("author") or resource.get("uid") or "UP主")
        if resource.get("notification_kind") == "video":
            return f"在{owner}的《{resource.get('text') or '视频'}》视频底下的回复"
        return f"在{owner}的动态底下的回复"

    @staticmethod
    def _comment_message(resource: dict[str, str], reply: dict[str, str]) -> str:
        return "\n".join(
            (
                f"【B站评论区回复】{reply['author']}",
                ASoulService._comment_context(resource),
                reply["text"],
                str(resource.get("url") or ""),
            )
        )

    async def _poll_latest_comment_updates(
        self,
        dynamics: list[dict[str, str]],
        entry: dict[str, Any],
        observed_at: int,
    ) -> list[str]:
        resource = self.latest_comment_resource(dynamics, observed_at)
        if resource is None:
            entry.pop("comment_monitor", None)
            return []
        replies = await self.fetch_latest_comments(resource)
        current_ids = [row["id"] for row in replies]
        monitor = entry.get("comment_monitor")
        if not isinstance(monitor, dict) or monitor.get("resource_id") != resource["id"]:
            entry["comment_monitor"] = {
                "resource_id": resource["id"],
                "reply_ids": current_ids[:COMMENT_SEEN_LIMIT],
                "initialized": True,
                "last_scanned_at": observed_at,
            }
            return []
        known_ids = {str(value) for value in monitor.get("reply_ids", []) if str(value)}
        unseen = [row for row in replies if row["id"] not in known_ids]
        monitor["reply_ids"] = list(dict.fromkeys(current_ids + list(known_ids)))[:COMMENT_SEEN_LIMIT]
        monitor["last_scanned_at"] = observed_at
        messages: list[str] = []
        context = self._comment_context(resource)
        for row in reversed(unseen):
            message = self._comment_message(resource, row)
            self._remember_comment_notification(
                message,
                {
                    "author": row["author"],
                    "uid": row["author_uid"],
                    "avatar_url": row.get("avatar_url", ""),
                    "profile": row.get("profile", ""),
                    "text": row["text"],
                    "rich_nodes": row.get("rich_nodes", "[]"),
                    "context": context,
                    "url": str(resource.get("url") or ""),
                },
            )
            messages.append(message)
        return messages

    async def dump_dynamic_payload(self, uid: str) -> Path:
        """Persist a credential-free diagnostic response for a super-admin."""
        self._require_bilibili_api()
        payload = await self._user(uid).get_dynamics_new()
        return self._write_debug_payload("dynamic", uid, payload)

    async def dump_live_payload(self, uid: str) -> Path:
        self._require_bilibili_api()
        payload = await self._user(uid).get_live_info()
        return self._write_debug_payload("live", uid, payload)

    @staticmethod
    def _write_debug_payload(kind: str, uid: str, payload: object) -> Path:
        directory = ROOT / "data" / "asoul_debug"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = directory / f"{kind}_{uid}_{timestamp}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _absolute_url(value: str) -> str:
        return "https:" + value if value.startswith("//") else value

    @staticmethod
    def _nonnegative_int(value: object) -> int | None:
        try:
            return max(int(str(value)), 0)
        except (TypeError, ValueError):
            return None

    def _start_live_session(
        self, live: dict[str, str], details: dict[str, str], observed_at: int
    ) -> dict[str, object]:
        return {
            "observed_started_at": observed_at,
            "source_live_time": str(live.get("live_time") or ""),
            "author": str(details.get("author") or live.get("author") or ""),
            "title": str(live.get("text") or "直播间"),
            "url": str(live.get("url") or ""),
            "cover_url": str(live.get("cover_url") or ""),
            "room_id": str(live.get("id") or ""),
            "area": str(live.get("area") or ""),
            "profile_start": {
                key: str(details[key])
                for key in ("likes", "following", "followers")
                if key in details
            },
            "online_samples": [],
        }

    def _record_live_sample(
        self, session: dict[str, object], live: dict[str, str], observed_at: int
    ) -> None:
        online = self._nonnegative_int(live.get("online"))
        if online is not None:
            samples = session.setdefault("online_samples", [])
            if isinstance(samples, list):
                samples.append([observed_at, online])
                del samples[:-720]
        session["last_seen_at"] = observed_at

    @staticmethod
    def _format_duration(seconds: int) -> str:
        minutes = max(0, seconds) // 60
        hours, minutes = divmod(minutes, 60)
        return f"{hours}小时{minutes:02d}分" if hours else f"{minutes}分"

    def _live_session_metrics(
        self, session: object, observed_ended_at: int, end_stats: dict[str, str]
    ) -> dict[str, str]:
        if not isinstance(session, dict):
            return {
                "live_duration": "",
                "popularity_peak": "",
                "popularity_average": "",
                "popularity_sample_count": "0",
                "likes_delta": "",
                "following_delta": "",
                "followers_delta": "",
            }

        # Prefer Bilibili's stream-start timestamp; the local observation time
        # is only a fallback when the room interface did not provide one.
        started_at = self._nonnegative_int(session.get("source_live_time"))
        if started_at is None:
            started_at = self._nonnegative_int(session.get("observed_started_at"))
        duration = self._format_duration(observed_ended_at - started_at) if started_at is not None else ""
        samples = session.get("online_samples")
        online_values: list[int] = []
        if isinstance(samples, list):
            for sample in samples:
                if not isinstance(sample, list) or len(sample) != 2:
                    continue
                value = self._nonnegative_int(sample[1])
                if value is not None:
                    online_values.append(value)
        start_stats = session.get("profile_start")
        changes: dict[str, str] = {}
        if isinstance(start_stats, dict):
            for label, key in (("获赞", "likes"), ("关注", "following"), ("粉丝", "followers")):
                start_value = self._nonnegative_int(start_stats.get(key))
                end_value = self._nonnegative_int(end_stats.get(key))
                if start_value is not None and end_value is not None:
                    changes[key] = f"{end_value - start_value:+,}"
        return {
            "live_duration": duration,
            "popularity_peak": f"{max(online_values):,}" if online_values else "",
            "popularity_average": f"{sum(online_values) // len(online_values):,}" if online_values else "",
            "popularity_sample_count": str(len(online_values)),
            "likes_delta": changes.get("likes", ""),
            "following_delta": changes.get("following", ""),
            "followers_delta": changes.get("followers", ""),
        }

    @staticmethod
    def _live_session_report(metrics: dict[str, str]) -> str:
        lines = ["【本场直播观测】"]
        duration = metrics.get("live_duration")
        if duration:
            lines.append(f"直播时长：{duration}")
        else:
            lines.append("直播时长：未完整观测")
        peak = metrics.get("popularity_peak")
        average = metrics.get("popularity_average")
        sample_count = metrics.get("popularity_sample_count") or "0"
        if peak and average:
            lines.append(f"人气值：峰值 {peak}，均值 {average}（{sample_count} 次采样）")
        else:
            lines.append("人气值：未取得有效采样")
        changes = (
            ("获赞", metrics.get("likes_delta")),
            ("关注", metrics.get("following_delta")),
            ("粉丝", metrics.get("followers_delta")),
        )
        if all(value for _, value in changes):
            lines.append("累计新增：" + "｜".join(f"{label} {value}" for label, value in changes))
        else:
            lines.append("累计新增：未取得完整快照")
        lines.append("注：数据为本机器人每 5 分钟观测，非 B 站官方结算。")
        return "\n".join(lines)

    async def poll_updates(self) -> list[str]:
        """Return unseen notifications in chronological order; first poll is baseline only."""
        state = self.db.asoul_state(MONITOR_KEY, {})
        if not isinstance(state, dict):
            state = {}
        initialized = bool(state.get("initialized"))
        sent: list[str] = []
        observed_at = int(time.time())
        comment_targets = tuple(
            uid
            for uid in settings.asoul_bili_comment_target_uids
            if uid in settings.asoul_bili_target_uids
        )
        comment_scan_uids: frozenset[str] = frozenset()
        if settings.asoul_bili_push_comment and comment_targets:
            start = int(state.get("comment_scan_index") or 0) % len(comment_targets)
            count = min(COMMENT_SCAN_TARGETS_PER_POLL, len(comment_targets))
            comment_scan_uids = frozenset(
                comment_targets[(start + offset) % len(comment_targets)]
                for offset in range(count)
            )
            state["comment_scan_index"] = (start + count) % len(comment_targets)
        try:
            live_statuses = await self.fetch_live_statuses(settings.asoul_bili_target_uids)
        except Exception:
            live_statuses = {}
        for uid in settings.asoul_bili_target_uids:
            entry = state.setdefault(uid, {})
            # Video-list polling was retired. Drop obsolete cursors left by older releases.
            entry.pop("video_ids", None)
            entry.pop("video", None)
            dynamics: list[dict[str, str]] = []
            dynamic_fetch_succeeded = False
            # Video submissions are read from the dynamic feed; no separate video-list polling.
            if (
                settings.asoul_bili_push_dynamic
                or settings.asoul_bili_push_video
                or uid in comment_scan_uids
            ):
                try:
                    dynamics = await self.fetch_dynamics(uid)
                    dynamic_fetch_succeeded = True
                except Exception:
                    dynamics = []
            live_status = live_statuses.get(uid)
            if dynamic_fetch_succeeded:
                old_dynamic_ids = entry.get("dynamic_ids", [])
                known_dynamic_ids = {str(value) for value in old_dynamic_ids if str(value)}
                if not known_dynamic_ids and entry.get("dynamic"):
                    known_dynamic_ids.add(str(entry["dynamic"]))
                current_dynamic_ids = [str(item["id"]) for item in dynamics if item.get("id")]
                entry["dynamic_ids"] = list(dict.fromkeys(current_dynamic_ids + list(known_dynamic_ids)))[:100]
                entry["dynamic"] = current_dynamic_ids[0] if current_dynamic_ids else str(entry.get("dynamic", ""))
                if initialized and entry.get("dynamic_initialized"):
                    for item in reversed(dynamics):
                        if item.get("id") and item["id"] not in known_dynamic_ids:
                            if item.get("notification_kind") == "video":
                                if settings.asoul_bili_push_video:
                                    message = f"【B站新视频】{item['author']}\n{item['text']}\n{item['url']}"
                                    try:
                                        details = await self.enrich_video_notification(item)
                                    except Exception:
                                        details = item
                                    self._remember_video_notification(message, details)
                                    sent.append(message)
                            elif item.get("notification_kind") == "live":
                                # Live-room feed cards are not authoritative live status events.
                                continue
                            else:
                                if settings.asoul_bili_push_dynamic:
                                    message = f"【B站新动态】{item['author']}\n{item['text']}\n{item['url']}"
                                    try:
                                        details = await self.enrich_dynamic_notification(item)
                                    except Exception:
                                        details = item
                                    self._remember_dynamic_notification(message, details)
                                    sent.append(message)
                entry["dynamic_initialized"] = True
            if dynamic_fetch_succeeded and uid in comment_scan_uids:
                try:
                    sent.extend(
                        await self._poll_latest_comment_updates(
                            dynamics,
                            entry,
                            observed_at,
                        )
                    )
                except Exception:
                    pass
            if live_status is not None:
                was_live = str(entry.get("live", "0"))
                entry["live"] = live_status["live"]
                start_details: dict[str, str] | None = None
                if live_status["live"] == "1":
                    session = entry.get("live_session")
                    if not isinstance(session, dict):
                        try:
                            start_details = await self.enrich_live_notification(live_status)
                        except Exception:
                            start_details = dict(live_status)
                        session = self._start_live_session(live_status, start_details, observed_at)
                        entry["live_session"] = session
                    self._record_live_sample(session, live_status, observed_at)
                if initialized and settings.asoul_bili_push_live and was_live != "1" and live_status["live"] == "1":
                    message = f"【开播】{live_status['author']}\n{live_status['text']}\n{live_status['url']}"
                    try:
                        details = start_details or await self.enrich_live_notification(live_status)
                    except Exception:
                        details = live_status
                    self._remember_live_notification(message, details, "start")
                    sent.append(message)
                elif initialized and settings.asoul_bili_push_live and was_live == "1" and live_status["live"] != "1":
                    session = entry.pop("live_session", None)
                    try:
                        details = await self.enrich_live_notification(live_status)
                    except Exception:
                        details = live_status
                    if isinstance(session, dict):
                        details = {
                            **details,
                            "author": str(session.get("author") or details.get("author") or ""),
                            "text": str(session.get("title") or details.get("text") or ""),
                            "url": str(session.get("url") or details.get("url") or ""),
                            "cover_url": str(session.get("cover_url") or details.get("cover_url") or ""),
                        }
                    metrics = self._live_session_metrics(session, observed_at, details)
                    details = {**details, **metrics}
                    message = f"【已下播】{details['author']}"
                    self._remember_live_notification(message, details, "end")
                    sent.append(message)
        state["initialized"] = True
        self.db.set_asoul_state(MONITOR_KEY, state)
        return sent

    def monitor_status(self) -> str:
        records = self.db.asoul_state(MONITOR_KEY, {})
        initialized = isinstance(records, dict) and bool(records.get("initialized"))
        return "\n".join((
            "【A-SOUL B站监控状态】",
            f"自动播报：{'开启' if settings.asoul_bili_enabled else '关闭'}",
            f"目标 UID：{len(settings.asoul_bili_target_uids)} 个",
            f"评论目标 UID：{len(settings.asoul_bili_comment_target_uids)} 个",
            f"推送群：{', '.join(map(str, settings.asoul_bili_effective_group_ids)) or '未配置'}",
            f"轮询：每 {settings.asoul_bili_poll_interval_seconds} 秒",
            f"普通/转发/预约动态 / 动态视频 / 开播下播 / 评论：{'开' if settings.asoul_bili_push_dynamic else '关'} / {'开' if settings.asoul_bili_push_video else '关'} / {'开' if settings.asoul_bili_push_live else '关'} / {'开' if settings.asoul_bili_push_comment else '关'}",
            "独立视频轮询：已停用（投稿视频由动态接口识别）",
            f"评论范围：每轮最多 {COMMENT_SCAN_TARGETS_PER_POLL} 个账号，仅最新且发布未满 6 小时的动态",
            f"图片卡片：{'开' if settings.asoul_bili_render_cards else '关'}",
            f"登录状态：{'已登录' if self.credential_available() else '未登录'}",
            f"游标状态：{'已建立，不会回放历史内容' if initialized else '首次成功轮询时建立基线'}",
        ))
