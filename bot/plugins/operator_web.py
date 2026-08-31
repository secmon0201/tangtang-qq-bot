"""Capability-scoped web controls for activities and operator workflows."""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import HTMLResponse
from nonebot import get_bots, get_driver

from bot.config import RESOURCE_DIR, settings
from bot.services.activities import ActivityService, build_create_payload, build_update_payload
from bot.services.duplicate import DuplicateService
from bot.services.gateway import OneBotGateway
from bot.services.operator_web import operator_web_sessions
from bot.services.runtime import database, passive_settings


driver = get_driver()
db = database()
passive = passive_settings()
activities = ActivityService(db, lambda: passive.groups("activity"))
duplicates = DuplicateService(db)
PAGE_PATH = RESOURCE_DIR / "operator_web.html"
SCOPE_LABELS = {
    "duplicate": "查重", "game": "小游戏", "game_api": "游戏接口", "today_wife": "今日老婆",
    "activity": "活动", "passive": "被动互动", "hourly": "整点报时",
}


def _session(token: str, kind: str):
    session = operator_web_sessions.get(token, kind)
    if session is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="链接已过期或不属于此操作，请重新在 QQ 发送网页命令。")
    return session


def _payload(request_data: Any) -> dict[str, Any]:
    if not isinstance(request_data, dict):
        raise ValueError("请求参数必须是对象")
    return request_data


def _groups() -> list[dict[str, Any]]:
    return [{"id": int(row["group_id"]), "name": str(row["group_name"] or "未命名群")} for row in db.managed_groups()]


def _activity_event(actor_id: int) -> SimpleNamespace:
    group_ids = sorted(passive.groups("activity"))
    return SimpleNamespace(user_id=actor_id, group_id=(group_ids[0] if group_ids else 0), sender=SimpleNamespace(role="admin"))


def _activity_rows(session: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in db.activities(("scheduled", "active", "ended", "cancelled")):
        item = dict(row)
        item["groups"] = [dict(group) for group in db.activity_groups(int(row["activity_id"]))]
        item["prizes"] = [dict(prize) for prize in db.activity_prizes(int(row["activity_id"]))]
        item["can_manage"] = bool(session.is_super_admin or int(row["creator_id"]) == session.actor_id)
        rows.append(item)
    return rows


def _operations_state() -> dict[str, Any]:
    groups = _groups()
    return {
        "groups": groups,
        "scopes": {key: sorted(passive.groups(key)) for key in SCOPE_LABELS},
        "scope_labels": SCOPE_LABELS,
        "passive": [
            {"group_id": group_id, **asdict(value)}
            for group_id, value in passive.group_settings()
        ],
        "filters": {
            "active": [dict(row) for row in db.filter_members("active")],
            "passive": [dict(row) for row in db.filter_members("passive")],
        },
    }


@driver.server_app.get("/operator/{token}", response_class=HTMLResponse)
async def operator_web_page(token: str) -> HTMLResponse:
    if operator_web_sessions.get(token) is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="链接已过期，请重新在 QQ 获取。")
    if not PAGE_PATH.is_file():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="运营网页资源不可用")
    return HTMLResponse(PAGE_PATH.read_text(encoding="utf-8"))


@driver.server_app.get("/operator/api/{kind}/{token}/state")
async def operator_web_state(kind: str, token: str) -> dict[str, Any]:
    session = _session(token, kind)
    base = {"remaining_seconds": operator_web_sessions.remaining_seconds(session), "actor_id": session.actor_id}
    if kind == "activity":
        return {**base, "groups": [item for item in _groups() if item["id"] in passive.groups("activity")], "activities": _activity_rows(session)}
    if kind == "operations":
        return {**base, **_operations_state()}
    if kind == "duplicate":
        return {**base, "groups": [item for item in _groups() if item["id"] in passive.groups("duplicate")], "whitelist": db.whitelist_profiles()}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知网页功能")


@driver.server_app.post("/operator/api/activity/{token}/create")
async def operator_activity_create(token: str, request: Request) -> dict[str, Any]:
    session = _session(token, "activity")
    try:
        value = _payload(await request.json())
        payload = build_create_payload(str(value.get("title") or "").strip(), str(value.get("starts") or ""), str(value.get("ends") or ""), ",".join(map(str, value.get("groups") or ())), str(value.get("type") or ""), str(value.get("description") or ""), str(value.get("prizes") or ""), str(value.get("visibility") or ""))
        activity_id = activities.create(_activity_event(session.actor_id), payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"activity_id": activity_id}


@driver.server_app.post("/operator/api/activity/{token}/{activity_id}/update")
async def operator_activity_update(token: str, activity_id: int, request: Request) -> dict[str, bool]:
    session = _session(token, "activity")
    current = db.activity(activity_id)
    if current is None or not (session.is_super_admin or int(current["creator_id"]) == session.actor_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="没有管理此活动的权限")
    try:
        value = _payload(await request.json())
        _, payload = build_update_payload(activity_id, value.get("title"), value.get("starts"), value.get("ends"), value.get("description"), ",".join(map(str, value["groups"])) if "groups" in value else None, value.get("type"), value.get("prizes"), value.get("visibility"))
        updated = activities.update(_activity_event(session.actor_id), activity_id, payload)
        if updated is None:
            raise PermissionError("活动状态不允许修改")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"ok": True}


@driver.server_app.post("/operator/api/activity/{token}/{activity_id}/{action}")
async def operator_activity_action(token: str, activity_id: int, action: str, request: Request) -> dict[str, bool]:
    session = _session(token, "activity")
    current = db.activity(activity_id)
    if current is None or not (session.is_super_admin or int(current["creator_id"]) == session.actor_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="没有管理此活动的权限")
    if action == "finish":
        if str(current["status"]) not in {"scheduled", "active"} or not activities.finish_now(current):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动当前不能提前结束")
    elif action == "cancel":
        value = _payload(await request.json())
        if not activities.cancel(activity_id, str(value.get("reason") or "")):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="活动当前不能取消")
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知活动操作")
    return {"ok": True}


@driver.server_app.post("/operator/api/operations/{token}/scope")
async def operator_scope_update(token: str, request: Request) -> dict[str, Any]:
    _session(token, "operations")
    try:
        value = _payload(await request.json())
        feature = str(value.get("feature") or "")
        groups = {int(item) for item in value.get("groups") or ()}
        if feature not in SCOPE_LABELS or not groups.issubset(set(settings.managed_group_ids)):
            raise ValueError("功能范围或群号无效")
        current = set(passive.groups(feature))
        for group_id in current - groups: passive.remove_feature_group(feature, group_id)
        for group_id in groups - current: passive.add_feature_group(feature, group_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"groups": sorted(passive.groups(feature))}


@driver.server_app.post("/operator/api/operations/{token}/passive/{group_id}")
async def operator_passive_update(token: str, group_id: int, request: Request) -> dict[str, bool]:
    _session(token, "operations")
    try:
        value = _payload(await request.json())
        reaction_probability = float(value["reaction_probability"])
        reaction_cooldown = int(value["reaction_cooldown_seconds"])
        repeat_probability = float(value["repeat_probability"])
        repeat_cooldown = int(value["repeat_cooldown_seconds"])
        repeat_interval = int(value["repeat_message_interval"])
        triple_probability = float(value["triple_repeat_probability"])
        if not (0 <= reaction_probability <= 0.5 and 0 <= repeat_probability <= 0.1):
            raise ValueError("概率超出允许范围")
        if not (0 <= reaction_cooldown <= 3600 and 0 <= repeat_cooldown <= 86400 and 0 <= repeat_interval <= 10000 and 0 <= triple_probability <= 1):
            raise ValueError("冷却、间隔或三连概率超出允许范围")
        passive.set_reaction_probability(group_id, reaction_probability)
        passive.set_reaction_cooldown_seconds(group_id, reaction_cooldown)
        passive.set_repeat_probability(group_id, repeat_probability)
        passive.set_repeat_cooldown_seconds(group_id, repeat_cooldown)
        passive.set_repeat_message_interval(group_id, repeat_interval)
        passive.set_triple_repeat_enabled(group_id, bool(value["triple_repeat_enabled"]))
        passive.set_triple_repeat_probability(group_id, triple_probability)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="被动互动参数无效") from exc
    return {"ok": True}


@driver.server_app.post("/operator/api/operations/{token}/filter/{kind}/{action}")
async def operator_filter_update(token: str, kind: str, action: str, request: Request) -> dict[str, Any]:
    session = _session(token, "operations")
    try:
        value = _payload(await request.json())
        user_ids = [int(item) for item in value.get("user_ids") or ()]
        if kind not in {"active", "passive"} or not user_ids or any(item <= 0 for item in user_ids): raise ValueError("名单参数无效")
        changed = db.add_filter_members(kind, user_ids, session.actor_id) if action == "add" else db.remove_filter_members(kind, user_ids) if action == "remove" else ()
        if not changed: raise ValueError("未知名单操作")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"changed": list(changed)}


@driver.server_app.post("/operator/api/duplicate/{token}/whitelist/{action}")
async def operator_whitelist_update(token: str, action: str, request: Request) -> dict[str, bool]:
    session = _session(token, "duplicate")
    try:
        value = _payload(await request.json()); user_id = int(value.get("user_id"))
        if user_id <= 0: raise ValueError("QQ 号无效")
        if action == "add": db.add_whitelist(user_id, session.actor_id, str(value.get("note") or ""))
        elif action == "remove": db.remove_whitelist(user_id)
        else: raise ValueError("未知白名单操作")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"ok": True}


@driver.server_app.post("/operator/api/duplicate/{token}/scan")
async def operator_duplicate_scan(token: str, request: Request) -> dict[str, Any]:
    _session(token, "duplicate")
    try:
        value = _payload(await request.json()); group_ids = tuple(dict.fromkeys(int(item) for item in value.get("groups") or ()))
        mode = str(value.get("mode") or "source"); ignore = bool(value.get("ignore_whitelist", False))
        if mode not in {"source", "all"} or len(group_ids) < 2 or not set(group_ids).issubset(passive.groups("duplicate")): raise ValueError("请选择至少两个已开启查重的群")
        bot = next(iter(get_bots().values()), None)
        if bot is None: raise RuntimeError("OneBot 尚未连接")
        result, failed = await (duplicates.scan_all(OneBotGateway(bot), group_ids, ignore) if mode == "all" else duplicates.scan(OneBotGateway(bot), group_ids, ignore))
        if failed: raise RuntimeError("无法读取群成员：" + ", ".join(map(str, failed)))
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"count": len(result), "rows": result}
