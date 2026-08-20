from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import settings
from bot.db import Database, utc_now
from bot.services.qq_platform import call_qq_action
from bot.services.roles import is_activity_admin, is_activity_admin_blacklisted, is_super_admin


ACTIVITY_ANNOUNCEMENT = "announcement"
ACTIVITY_LOTTERY = "lottery"
ACTIVE_STATUSES = {"scheduled", "active"}
ACTIVITY_VISIBILITY_MASKED = "masked"
ACTIVITY_VISIBILITY_PUBLIC = "public"
ACTIVITY_ID_RE = re.compile(
    r"^[\s\u3000:：#]*(?:(?:id|编号)[\s\u3000:：#]*)?([0-9]{1,20})[\s\u3000]*$",
    re.IGNORECASE,
)
ACTIVITY_ID_PREFIX_RE = re.compile(
    r"^[\s\u3000:：#]*(?:(?:id|编号)[\s\u3000:：#]*)?",
    re.IGNORECASE,
)
ACTIVITY_FIELD_RE = re.compile(r"^\s*([^:：\s]+)\s*[:：]\s*(.*)\s*$")
ACTIVITY_FIELD_ALIASES = {
    "活动id": "activity_id",
    "活动编号": "activity_id",
    "编号": "activity_id",
    "id": "activity_id",
    "活动名": "title",
    "活动名称": "title",
    "标题": "title",
    "开始时间": "starts",
    "结束时间": "ends",
    "类型": "activity_type",
    "活动类型": "activity_type",
    "说明": "description",
    "活动说明": "description",
    "奖项": "prizes",
    "参与群": "groups",
    "群号": "groups",
    "群": "groups",
    "隐私": "visibility",
    "展示方式": "visibility",
    "参与展示": "visibility",
}


def parse_labeled_activity_fields(raw: str) -> dict[str, str] | None:
    """Parse the readable ``字段名：内容`` activity command format."""
    lines = raw.strip().splitlines()
    if not lines or not ACTIVITY_FIELD_RE.match(lines[0]):
        return None

    fields: dict[str, str] = {}
    for line in lines:
        match = ACTIVITY_FIELD_RE.match(line)
        if not match:
            raise ValueError("每一项请单独换行，并使用“字段名：内容”的格式")
        label = match.group(1).lower()
        field = ACTIVITY_FIELD_ALIASES.get(label)
        if field is None:
            raise ValueError(f"不支持的活动字段：{match.group(1)}")
        if field in fields:
            raise ValueError(f"活动字段不能重复：{match.group(1)}")
        fields[field] = match.group(2).strip()
    return fields


def parse_local_time(value: str) -> str:
    parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    local = parsed.replace(tzinfo=ZoneInfo(settings.timezone))
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


def format_local_time(value: str) -> str:
    parsed = datetime.fromisoformat(value).astimezone(ZoneInfo(settings.timezone))
    return parsed.strftime("%Y-%m-%d %H:%M")


def current_utc() -> str:
    return utc_now()


def parse_activity_id(raw: str) -> int | None:
    match = ACTIVITY_ID_RE.fullmatch(str(raw).strip())
    return int(match.group(1)) if match else None


def parse_group_ids(raw: str, allowed: tuple[int, ...]) -> tuple[int, ...]:
    values = tuple(dict.fromkeys(int(value) for value in re.findall(r"\d{4,20}", raw)))
    if not values:
        return settings.managed_order(allowed)
    invalid = sorted(set(values) - set(allowed))
    if invalid:
        raise ValueError("这些群不在活动开放范围内：" + ", ".join(map(str, invalid)))
    return values


def parse_prizes(raw: str) -> tuple[tuple[str, int], ...]:
    if not raw.strip() or raw.strip() in {"-", "无"}:
        return ()
    prizes: list[tuple[str, int]] = []
    for item in re.split(r"[;；]", raw):
        item = item.strip()
        if not item:
            continue
        match = re.match(r"^(.+?)[=:：](\d+)$", item)
        if not match or int(match.group(2)) <= 0:
            raise ValueError("奖项格式应为：一等奖=1;二等奖=2")
        prizes.append((match.group(1).strip()[:40], int(match.group(2))))
    if not prizes:
        raise ValueError("至少需要配置一个有效奖项")
    return tuple(prizes)


def parse_visibility(raw: str) -> str:
    value = raw.strip().lower()
    if not value or value in {"脱敏", "隐私", "匿名", "masked", "private"}:
        return ACTIVITY_VISIBILITY_MASKED
    if value in {"公开", "公开参与", "显式", "显式参与", "public", "open"}:
        return ACTIVITY_VISIBILITY_PUBLIC
    raise ValueError("参与展示方式只能是“公开”或“脱敏”")


def parse_activity_type(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"通报", "公告", "announcement", "basic"}:
        return ACTIVITY_ANNOUNCEMENT
    if value in {"抽奖", "lottery", "draw"}:
        return ACTIVITY_LOTTERY
    raise ValueError("活动类型只能是“通报”或“抽奖”")


def build_create_payload(
    title: str,
    starts: str,
    ends: str,
    groups: str,
    kind: str,
    description: str = "",
    prizes_raw: str = "",
    visibility_raw: str = "",
) -> dict[str, Any]:
    if not title or len(title) > 80:
        raise ValueError("活动标题不能为空且不能超过 80 个字")
    activity_type = parse_activity_type(kind)
    starts_at = parse_local_time(starts)
    ends_at = parse_local_time(ends)
    if ends_at <= starts_at:
        raise ValueError("结束时间必须晚于开始时间")
    description = description.strip()[:500]
    prizes = parse_prizes(prizes_raw)
    visibility = parse_visibility(visibility_raw)
    if activity_type == ACTIVITY_LOTTERY and not prizes:
        raise ValueError("抽奖活动必须配置奖项，例如：一等奖=1;二等奖=2")
    if activity_type == ACTIVITY_ANNOUNCEMENT and prizes:
        raise ValueError("通报活动不需要配置奖项")
    return {
        "title": title,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "groups_raw": groups,
        "activity_type": activity_type,
        "description": description,
        "prizes": prizes,
        "visibility": visibility,
    }


def parse_create_payload(raw: str) -> dict[str, Any]:
    fields = parse_labeled_activity_fields(raw)
    if fields is not None:
        required = {
            "title": "活动名",
            "starts": "开始时间",
            "ends": "结束时间",
            "activity_type": "类型",
        }
        missing = [label for field, label in required.items() if not fields.get(field)]
        if missing:
            raise ValueError("创建活动必须填写：" + "、".join(missing))
        return build_create_payload(
            fields["title"],
            fields["starts"],
            fields["ends"],
            fields.get("groups", ""),
            fields["activity_type"],
            fields.get("description", ""),
            fields.get("prizes", ""),
            fields.get("visibility", ""),
        )

    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 5:
        raise ValueError(
            "请按行填写“活动名：内容”等字段；也可使用旧格式：标题 | 开始时间 | 结束时间 | 群号 | 通报/抽奖"
        )
    return build_create_payload(
        *parts[:5],
        parts[5] if len(parts) > 5 else "",
        parts[6] if len(parts) > 6 else "",
        parts[7] if len(parts) > 7 else "",
    )


def build_update_payload(
    activity_id: int,
    title_raw: str | None,
    starts_raw: str | None,
    ends_raw: str | None,
    description: str | None,
    groups_raw: str | None,
    activity_type_raw: str | None,
    prizes_raw: str | None,
    visibility_raw: str | None,
) -> tuple[int, dict[str, Any]]:
    if not any(
        value is not None
        for value in (
            title_raw,
            starts_raw,
            ends_raw,
            description,
            groups_raw,
            activity_type_raw,
            prizes_raw,
            visibility_raw,
        )
    ):
        raise ValueError("至少需要修改一个项目")
    if title_raw is not None and len(title_raw) > 80:
        raise ValueError("活动标题不能为空且不能超过 80 个字")
    starts_at = parse_local_time(starts_raw) if starts_raw is not None else None
    ends_at = parse_local_time(ends_raw) if ends_raw is not None else None
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise ValueError("结束时间必须晚于开始时间")

    return activity_id, {
        "title": title_raw,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "description": description,
        "groups_raw": groups_raw,
        "activity_type": parse_activity_type(activity_type_raw) if activity_type_raw is not None else None,
        "prizes": parse_prizes(prizes_raw) if prizes_raw is not None else None,
        "visibility": parse_visibility(visibility_raw) if visibility_raw is not None else None,
    }


def parse_update_payload(raw: str) -> tuple[int, dict[str, Any]]:
    """Parse an update; blank labelled fields and ``0`` positional fields keep stored values."""
    fields = parse_labeled_activity_fields(raw)
    if fields is not None:
        activity_id = parse_activity_id(fields.get("activity_id", ""))
        if activity_id is None:
            raise ValueError("修改活动必须填写有效的活动ID，例如：活动ID：517")
        return build_update_payload(
            activity_id,
            fields.get("title") or None,
            fields.get("starts") or None,
            fields.get("ends") or None,
            fields.get("description") or None,
            fields.get("groups") or None,
            fields.get("activity_type") or None,
            fields.get("prizes") or None,
            fields.get("visibility") or None,
        )

    parts = [part.strip() for part in raw.split("|")]
    parts[0] = ACTIVITY_ID_PREFIX_RE.sub("", parts[0], count=1)
    if len(parts) < 2 or not parts[0].isdigit():
        raise ValueError(
            "请按行填写“活动ID：517”等字段；也可使用旧格式："
            f"{settings.command_prefix}修改活动 ID | 标题 | 开始时间 | 结束时间 | 说明 | 参与群 | 类型 | 奖项 | 隐私"
        )

    def optional(index: int) -> str | None:
        if len(parts) <= index or not parts[index] or parts[index] == "0":
            return None
        return parts[index]

    return build_update_payload(
        int(parts[0]),
        optional(1),
        optional(2),
        optional(3),
        optional(4),
        optional(5),
        optional(6),
        optional(7),
        optional(8),
    )


def masked_user_id(user_id: int) -> str:
    value = str(int(user_id))
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}****{value[-3:]}"


class ActivityService:
    def __init__(
        self,
        database: Database,
        allowed_group_ids: Callable[[], Iterable[int]] | None = None,
    ) -> None:
        self.database = database
        self.allowed_group_ids = allowed_group_ids or (lambda: settings.activity_group_ids)

    def allowed_group(self, group_id: int) -> bool:
        return int(group_id) in {int(item) for item in self.allowed_group_ids()}

    def is_activity_admin(self, user_id: int) -> bool:
        return is_activity_admin(user_id)

    def is_event_activity_admin(self, event: Any) -> bool:
        user_id = int(event.user_id)
        if self.is_activity_admin(user_id):
            return True
        if is_activity_admin_blacklisted(user_id):
            return False
        try:
            event_group_id = int(getattr(event, "group_id", 0) or 0)
        except (TypeError, ValueError):
            return False
        sender = getattr(event, "sender", None)
        sender_role = str(getattr(sender, "role", "") or "").lower()
        return event_group_id > 0 and self.allowed_group(event_group_id) and sender_role in {
            "owner",
            "admin",
        }

    def can_create(self, event: Any) -> bool:
        return self.is_event_activity_admin(event)

    def can_manage(self, event: Any, activity: Any) -> bool:
        user_id = int(event.user_id)
        return user_id in settings.operator_ids or (
            user_id == int(activity["creator_id"])
            and self.is_event_activity_admin(event)
        )

    def can_update(self, event: Any, activity: Any) -> bool:
        """Only super admins may update an activity that is already active."""
        status = str(activity["status"])
        if status == "active":
            return is_super_admin(int(event.user_id))
        return status == "scheduled" and self.can_manage(event, activity)

    def create(
        self,
        event: Any,
        payload: dict[str, Any],
        creator_group_id: int | None = None,
    ) -> int:
        groups = parse_group_ids(payload["groups_raw"], tuple(self.allowed_group_ids()))
        if not groups:
            raise ValueError("还没有配置活动开放群")
        source_group_id = int(creator_group_id) if creator_group_id else None
        message = (
            f"活动已发布：{payload['title']}（ID {{id}}）\n"
            f"时间：{format_local_time(payload['starts_at'])} - {format_local_time(payload['ends_at'])}\n"
            f"发送 {settings.command_prefix}活动详情 {{id}} 查看详情，"
            f"发送 {settings.command_prefix}报名 {{id}} 参与。"
        )
        activity_id = self.database.create_activity(
            payload["title"],
            payload["description"],
            payload["activity_type"],
            payload["starts_at"],
            payload["ends_at"],
            int(event.user_id),
            source_group_id or 0,
            groups,
            payload["prizes"],
            message.format(id="{activity_id}"),
            payload["visibility"],
            exclude_group_id=source_group_id,
        )
        return activity_id

    async def deliver_broadcasts(
        self,
        bot: Any,
        message_factory: Callable[[Any, Any], Any] | None = None,
    ) -> None:
        for row in self.database.pending_activity_broadcasts():
            try:
                message = message_factory(row, bot) if message_factory else str(row["message"])
                if message is None:
                    raise RuntimeError("activity broadcast image is unavailable")
                if isinstance(message, dict) and "_activity_forward_messages" in message:
                    await call_qq_action(
                        bot,
                        "send_group_forward_msg",
                        group_id=int(row["group_id"]),
                        messages=message["_activity_forward_messages"],
                    )
                else:
                    await call_qq_action(
                        bot,
                        "send_group_msg",
                        group_id=int(row["group_id"]),
                        message=message,
                    )
            except Exception as exc:
                self.database.mark_activity_broadcast_error(
                    int(row["activity_id"]),
                    int(row["group_id"]),
                    str(row["kind"]),
                    str(exc),
                    settings.activity_broadcast_max_attempts,
                )
            else:
                self.database.mark_activity_broadcast_sent(
                    int(row["activity_id"]), int(row["group_id"]), str(row["kind"])
                )

    def winner_text(self, activity_id: int) -> str:
        winners = self.database.activity_winners(activity_id)
        if not winners:
            return "暂无中奖者（报名人数不足或没有有效报名）。"
        return "；".join(
            f"{row['prize_name']}：{row['nickname'] or '未获取昵称'}（{masked_user_id(row['user_id'])}）"
            for row in winners
        )

    def finish_now(self, activity: Any) -> bool:
        activity_id = int(activity["activity_id"])
        if activity["activity_type"] == ACTIVITY_LOTTERY:
            self.database.draw_activity(activity_id)
            result = self.winner_text(activity_id)
            message = f"抽奖活动已结束：{activity['title']}（ID {activity_id}）\n中奖结果：{result}"
        else:
            message = f"活动已结束：{activity['title']}（ID {activity_id}）。报名已关闭。"
        return self.database.transition_activity(
            activity_id,
            str(activity["status"]),
            "ended",
            message,
        )

    def update(
        self, event: Any, activity_id: int, payload: dict[str, Any]
    ) -> dict[str, tuple[int, ...]] | None:
        current = self.database.activity(activity_id)
        if current is None or not self.can_update(event, current):
            return None

        old_groups = tuple(int(row["group_id"]) for row in self.database.activity_groups(activity_id))
        new_groups = old_groups
        if payload.get("groups_raw") is not None:
            if not re.search(r"\d{4,20}", str(payload["groups_raw"])):
                raise ValueError("参与群必须填写一个或多个群号；不修改请填 0")
            new_groups = parse_group_ids(payload["groups_raw"], tuple(self.allowed_group_ids()))
            if not new_groups:
                raise ValueError("活动至少需要保留一个参与群")

        activity_type = payload.get("activity_type") or str(current["activity_type"])
        title = payload.get("title") or str(current["title"])
        description = (
            str(payload["description"])
            if payload.get("description") is not None
            else str(current["description"])
        )
        starts_at = payload.get("starts_at") or str(current["starts_at"])
        ends_at = payload.get("ends_at") or str(current["ends_at"])
        if ends_at <= starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        visibility = payload.get("visibility") or str(current["visibility"] or ACTIVITY_VISIBILITY_MASKED)

        current_prizes = tuple(
            (str(row["prize_name"]), int(row["quantity"]))
            for row in self.database.activity_prizes(activity_id)
        )
        if activity_type == ACTIVITY_LOTTERY:
            prizes = payload.get("prizes") if payload.get("prizes") is not None else current_prizes
            if not prizes:
                raise ValueError("抽奖活动必须配置奖项，例如：一等奖=1;二等奖=2")
        else:
            if payload.get("prizes") not in {None, ()}:
                raise ValueError("通报活动不需要配置奖项")
            prizes = ()

        return self.database.update_activity(
            activity_id,
            title,
            description,
            activity_type,
            visibility,
            starts_at,
            ends_at,
            new_groups,
            prizes,
            f"活动已更新：{title}（ID {activity_id}）。"
            f"发送 {settings.command_prefix}活动详情 {activity_id} 查看最新信息。",
            f"活动「{title}」已不支持本群",
            allow_active=str(current["status"]) == "active",
        )

    def refresh_due(self) -> list[tuple[int, str, str]]:
        changed: list[tuple[int, str, str]] = []
        now = current_utc()
        for activity in self.database.due_activities(now):
            activity_id = int(activity["activity_id"])
            if activity["status"] == "scheduled" and activity["starts_at"] <= now < activity["ends_at"]:
                message = (
                    f"活动已开始：{activity['title']}（ID {activity_id}）。"
                    f"发送 {settings.command_prefix}报名 {activity_id} 参与。"
                )
                if self.database.transition_activity(activity_id, "scheduled", "active", message):
                    changed.append((activity_id, "scheduled", "active"))
                continue
            if activity["status"] not in {"scheduled", "active"}:
                continue
            if activity["activity_type"] == ACTIVITY_LOTTERY:
                self.database.draw_activity(activity_id)
                result = self.winner_text(activity_id)
                message = f"抽奖活动已结束：{activity['title']}（ID {activity_id}）\n中奖结果：{result}"
            else:
                message = f"活动已结束：{activity['title']}（ID {activity_id}）。报名已关闭。"
            if self.database.transition_activity(
                activity_id, str(activity["status"]), "ended", message
            ):
                changed.append((activity_id, str(activity["status"]), "ended"))
        return changed

    def cancel(self, activity_id: int, reason: str) -> bool:
        return self.database.transition_activity(
            activity_id,
            "scheduled",
            "cancelled",
            f"活动已取消：活动 ID {activity_id}。报名数据已冻结。原因：{reason or '创建者取消'}",
            reason,
        ) or self.database.transition_activity(
            activity_id,
            "active",
            "cancelled",
            f"活动已取消：活动 ID {activity_id}。报名数据已冻结。原因：{reason or '创建者取消'}",
            reason,
        )

    def state_label(self, status: str) -> str:
        return {
            "scheduled": "未开始",
            "active": "进行中",
            "ended": "已结束",
            "cancelled": "已取消",
        }.get(status, status)
