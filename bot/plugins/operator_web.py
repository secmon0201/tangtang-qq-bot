"""Capability-scoped web controls for current operator workflows."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import HTMLResponse
from nonebot import get_bots, get_driver

from bot.config import RESOURCE_DIR
from bot.services.duplicate import DuplicateService
from bot.services.gateway import OneBotGateway
from bot.services.operator_web import operator_web_sessions
from bot.services.runtime import database, group_domains


driver = get_driver()
db = database()
domains = group_domains()
duplicates = DuplicateService(db)
PAGE_PATH = RESOURCE_DIR / "operator_web.html"


def _session(token: str, kind: str):
    session = operator_web_sessions.get(token, kind)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="链接已过期或不属于此操作，请重新在 QQ 获取。",
        )
    return session


def _payload(request_data: Any) -> dict[str, Any]:
    if not isinstance(request_data, dict):
        raise ValueError("请求参数必须是对象")
    return request_data


def _groups() -> list[dict[str, Any]]:
    return [
        {
            "id": int(row["group_id"]),
            "name": str(row["alias"] or row["group_name"] or "未命名群"),
        }
        for row in db.managed_groups()
    ]


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
    base = {
        "remaining_seconds": operator_web_sessions.remaining_seconds(session),
        "actor_id": session.actor_id,
    }
    if kind == "duplicate":
        return {
            **base,
            "groups": [item for item in _groups() if item["id"] in domains.enabled_groups("duplicate")],
            "whitelist": db.whitelist_profiles(),
        }
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知网页功能")


@driver.server_app.post("/operator/api/duplicate/{token}/whitelist/{action}")
async def operator_whitelist_update(token: str, action: str, request: Request) -> dict[str, bool]:
    session = _session(token, "duplicate")
    try:
        value = _payload(await request.json())
        user_id = int(value.get("user_id"))
        if user_id <= 0:
            raise ValueError("QQ 号无效")
        if action == "add":
            db.add_whitelist(user_id, session.actor_id, str(value.get("note") or ""))
        elif action == "remove":
            db.remove_whitelist(user_id)
        else:
            raise ValueError("未知白名单操作")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"ok": True}


@driver.server_app.post("/operator/api/duplicate/{token}/scan")
async def operator_duplicate_scan(token: str, request: Request) -> dict[str, Any]:
    _session(token, "duplicate")
    try:
        value = _payload(await request.json())
        group_ids = tuple(dict.fromkeys(int(item) for item in value.get("groups") or ()))
        mode = str(value.get("mode") or "source")
        ignore = bool(value.get("ignore_whitelist", False))
        if mode not in {"source", "all"} or len(group_ids) < 2 or not set(group_ids).issubset(domains.enabled_groups("duplicate")):
            raise ValueError("请选择至少两个已开启查重的群")
        bot = next(iter(get_bots().values()), None)
        if bot is None:
            raise RuntimeError("OneBot 尚未连接")
        if mode == "all":
            result, failed = await duplicates.scan_all(OneBotGateway(bot), group_ids, ignore)
        else:
            result, failed = await duplicates.scan(OneBotGateway(bot), group_ids, ignore)
        if failed:
            raise RuntimeError("无法读取群成员：" + ", ".join(map(str, failed)))
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"count": len(result), "rows": result}


__all__ = ["operator_web_page", "operator_web_state"]
