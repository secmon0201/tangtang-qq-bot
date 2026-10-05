"""Interactive local business pages backed by the migrated business services."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
import secrets
import time
from typing import Any
from uuid import uuid4

from PIL import Image

from .business.community_web import help_payload, page_html as community_html, ranking_payload
from .business.asoul_web_render import page_html as schedule_html, schedule_payload
from .types import InboundEvent, ToolCall, ToolResult


class BusinessPages:
    def __init__(self, tools):
        self.tools = tools
        self.sessions: dict[str, dict[str, Any]] = {}

    def _session(self, name: str, token: str) -> dict[str, Any]:
        session = self.sessions.get(token)
        if not session or session["name"] != name or session["expires"] <= time.monotonic():
            if session and session["expires"] <= time.monotonic():
                self.sessions.pop(token, None)
            raise ValueError("页面操作已过期，请重新打开。")
        if session["actor"] not in self._operators(name):
            raise ValueError("当前账号没有这个页面的管理权限。")
        return session

    def _operators(self, name: str) -> list[int]:
        operators = self.tools.store.get_setting("operator_ids", [])
        if name == "announcement":
            operators = [*operators, *self.tools.store.get_setting("announcement_ids", [])]
        return operators

    def create_session(self, name: str, *, user_id: int) -> str:
        if name not in {"duplicate", "whitelist", "announcement"}:
            raise ValueError("没有这个业务页面。")
        if user_id not in self._operators(name):
            raise ValueError("当前账号没有这个页面的管理权限。")
        current = time.monotonic()
        for key, session in tuple(self.sessions.items()):
            if session["expires"] <= current:
                self.sessions.pop(key)
        token = secrets.token_urlsafe(24)
        self.sessions[token] = {"name": name, "actor": user_id, "expires": current + 900, "draft": None}
        return token

    def event(self, name: str, token: str) -> InboundEvent:
        session = self._session(name, token)
        return InboundEvent("web-" + uuid4().hex, int(self.tools.gateway.self_id), session["actor"], None, "本地管理面板")

    async def page(self, name: str, *, group_id: int = 0, user_id: int = 0,
                   scope: str = "", group: str = "", period: str = "", cluster: bool = False,
                   token: str = "") -> str:
        if name == "help":
            source = community_html("help", help_payload())
            return self._community_paths(source)
        if name == "ranking":
            payload = await self.read(name, group_id=group_id, scope=scope, group=group,
                                      period=period, cluster=cluster)
            source = self._community_paths(community_html("ranking", payload))
            return source.replace(
                "const query = () => new URLSearchParams(location.search);",
                "const query = () => new URLSearchParams(location.search);\n"
                "    const rankingGroupId = embedded.anchor_group_id;"
            ).replace(
                "&group=${encodeURIComponent(group)}`",
                "&group=${encodeURIComponent(group)}&group_id=${encodeURIComponent(rankingGroupId)}`"
            ).replace(
                "next.searchParams.set('group', data.group);",
                "next.searchParams.set('group', data.group);\n"
                "        next.searchParams.set('group_id', data.anchor_group_id);\n"
                "        next.searchParams.delete('period');\n"
                "        next.searchParams.delete('cluster');"
            )
        if name in {"schedule", "asoul"}:
            return schedule_html().replace('const apiBase = location.pathname.startsWith("/live") ? "/live/api" : "/asoul-live/api";', 'const apiBase = "/business/schedule/api";')
        if name not in {"duplicate", "whitelist", "announcement"}:
            raise ValueError("没有这个业务页面。")
        self._session(name, token)
        filename = "global_announcement_web.html" if name == "announcement" else "operator_web.html"
        source = (self.tools.resources / filename).read_text(encoding="utf-8")
        prefix = f"/business/{name}/api/{token}"
        if name == "announcement":
            return re.sub(r"const segments=location\.pathname.*?; const state=", f"const api='{prefix}'; const state=", source, count=1)
        return re.sub(r"const token = location\.pathname.*?;\s*const \$", f"const api = '{prefix}';\n  const $", source, count=1, flags=re.S)

    @staticmethod
    def _community_paths(source: str) -> str:
        return source.replace("return `/${kind}/api`;", "return `/business/${kind}/api`;").replace('src="/assets/tangtang-avatar.jpg"', 'src="/business/assets/tangtang-avatar.jpg"')

    async def read(self, name: str, *, group_id: int = 0, scope: str = "", group: str = "",
                   view: str = "today", period: str = "", cluster: bool = False) -> dict:
        tools = self.tools
        if name == "help":
            return help_payload()
        if name in {"schedule", "asoul"}:
            from datetime import timedelta
            now = tools._now()
            first = now.date() + timedelta(days=view == "tomorrow")
            last = first + timedelta(days=6 - first.weekday()) if view == "week" else first
            days = await tools.asoul.schedule_for_days(first, last)
            day_items = [(first + timedelta(days=offset), days.get(first + timedelta(days=offset), [])) for offset in range((last - first).days + 1)]
            payload = schedule_payload(view, day_items, generated_at=now.isoformat())
            return tools.asoul_web.localize_schedule_stickers(payload)
        if name != "ranking":
            raise ValueError("没有这个业务数据。")
        groups = tools.domains.all_group_ids()
        selected_group = int(group) if group not in {"", "current", "domain"} else None
        group_id = group_id or selected_group or (groups[0] if groups else 0)
        if group_id not in groups:
            raise ValueError("请先登记需要查看排行的群。")
        domain = tools.domains.domain_for_group(group_id)
        members = tools.domains.domain_groups(domain.domain_id)
        if selected_group is not None and selected_group not in members:
            raise ValueError("请选择当前群域内的群。")
        options = [{"key": "domain", "label": tools.domains.domain_display_name(domain)},
                   *[{"key": str(gid), "label": tools.domains.display_name(gid)} for gid in members]]
        payload = await self.ranking_data(selected_group or group_id, scope or period or "day",
            cluster=group == "domain" or (cluster and selected_group is None), group_options=options)
        payload["anchor_group_id"] = group_id
        return payload

    async def ranking_data(self, group_id: int, scope: str, *, cluster: bool = False,
                           rows: list[dict] | None = None, day: date | None = None,
                           group_options: list[dict] | None = None) -> dict:
        tools = self.tools
        domain = tools.domains.domain_for_group(group_id)
        groups = tools.domains.ranking_group_ids(group_id, cluster=cluster)
        label = tools.domains.domain_display_name(domain) if cluster else tools.domains.display_name(group_id)
        rows = (
            tools.stats.ranking_rows_for_groups(scope, groups, today=day)[:100]
            if rows is None else rows
        )
        key = "domain" if cluster else str(group_id)
        payload = ranking_payload(tools.stats, scope, key, rows=rows,
            selected_group_id=None if cluster else group_id, group_label_override=label,
            group_labels={gid: tools.domains.display_name(gid) for gid in groups},
            group_options=group_options or [{"key": key, "label": label}],
            avatar_paths=await tools._avatar_paths(rows),
            group_totals=(
                tools.stats.group_totals_for_groups(scope, groups, today=day)
                if cluster else []
            ),
            daily_totals=[] if cluster else tools.stats.recent_group_daily_totals(group_id, today=day))
        if day is not None:
            payload.update(generated_date=day.strftime("%Y.%m.%d"), generated_month=day.strftime("%m"),
                           generated_day=day.strftime("%d"))
            if day != tools._now().date():
                payload["scope_title"] = f"{day:%m.%d}发言榜"
                payload["title"] = label + payload["scope_title"]
        return payload

    def state(self, name: str, token: str) -> dict:
        session = self._session(name, token)
        tools = self.tools
        common = {"remaining_seconds": max(0, int(session["expires"] - time.monotonic())), "actor_id": session["actor"]}
        groups = []
        for group in tools.domains.all_group_ids():
            profile = tools.db.managed_group(group)
            groups.append({"id": group, "name": str(profile["group_name"] or group) if profile else str(group)})
        if name != "announcement":
            return {**common, "groups": groups, "whitelist": tools.db.whitelist_profiles()}
        options, clusters = [], {}
        for group in groups:
            domain = tools.domains.domain_for_group(group["id"])
            parent = f"cluster:{domain.domain_id}" if domain.mode == "cluster" else ""
            if parent and parent not in clusters:
                clusters[parent] = {"key": parent, "label": tools.domains.domain_display_name(domain), "kind": "cluster", "group_ids": tools.domains.domain_groups(domain.domain_id), "parent_key": ""}
            options.append({"key": f"group:{group['id']}", "label": group["name"], "kind": "group", "group_ids": [group["id"]], "parent_key": parent})
        sticker_root = tools.resources / "asoul_stickers"
        stickers = {folder.name: sorted(path.stem for path in folder.glob("*.png")) for folder in sticker_root.iterdir() if folder.is_dir()}
        return {**common, "members": list(stickers), "stickers": stickers, "target_options": [*clusters.values(), *options]}

    def upload(self, content: bytes, filename: str) -> Path:
        path = self.tools.root / "runtime" / "announcement-uploads" / (uuid4().hex + Path(filename).suffix.lower())
        if len(content) > 20 * 1024 * 1024:
            raise ValueError("图片最大 20 MiB。")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        with Image.open(path) as image:
            if image.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
                raise ValueError("图片格式需要 PNG、JPEG、GIF 或 WebP。")
            image.verify()
        return path

    def _targets(self, token: str, requested: list[str]) -> list[int]:
        options = {item["key"]: item["group_ids"] for item in self.state("announcement", token)["target_options"]}
        if not requested or any(key not in options for key in requested):
            raise ValueError("请选择有效的公告群。")
        return list(dict.fromkeys(group for key in requested for group in options[key]))

    async def action(self, name: str, token: str, action: str, raw: dict) -> ToolResult | dict:
        session = self._session(name, token)
        event = self.event(name, token)
        tools = self.tools
        if name in {"duplicate", "whitelist"}:
            if action.startswith("whitelist/"):
                result = await tools.execute(event, ToolCall("whitelist", {"action": action.split("/")[1], "user_id": int(raw["user_id"]), "note": raw.get("note", "")}))
                if result.status != "ok":
                    raise ValueError(result.text)
                return {"ok": True}
            if action == "scan":
                result = await tools.execute(event, ToolCall("duplicate_scan", {"group_ids": raw["groups"], "mode": raw.get("mode", "source"), "ignore_whitelist": raw.get("ignore_whitelist", False)}))
                if result.status != "ok":
                    raise ValueError(result.text)
                return {"count": len(result.data["members"]), "rows": result.data["members"]}
            raise ValueError("没有这个网页操作。")
        if action == "send-preview":
            if session["draft"] is None:
                raise ValueError("请先生成公告预览。")
            result = ToolResult(**session["draft"])
            session["draft"] = None
            return result
        if action not in {"preview", "graphic-preview", "send-image"}:
            raise ValueError("没有这个公告操作。")
        targets = raw.get("targets", [])
        if isinstance(targets, str):
            targets = json.loads(targets)
        args = {**raw, "group_ids": self._targets(token, targets)}
        if isinstance(args.get("at_all"), str):
            args["at_all"] = args["at_all"] == "true"
        if action == "graphic-preview":
            args["graphic"] = True
        result = await tools.execute(event, ToolCall("global_announcement", args))
        if result.status != "ok":
            raise ValueError(result.text)
        if action != "send-image":
            session["draft"] = result.to_dict()
        return result
