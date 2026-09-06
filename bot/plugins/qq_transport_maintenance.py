from __future__ import annotations

import json
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import CommandArg

from bot.config import settings
from bot.services.qq_transport_maintenance import QQTransportMaintenanceMonitor
from bot.services.roles import is_super_admin
from bot.services.runtime import database


driver = get_driver()
scheduler = AsyncIOScheduler(timezone=settings.timezone)
monitor = QQTransportMaintenanceMonitor(database())


def _current_bot_ids() -> tuple[str, ...]:
    return tuple(str(bot_id) for bot_id in get_bots())


def _text_arg(args: Any) -> str:
    return args.extract_plain_text().strip()


def _format_time(value: object) -> str:
    return str(value or "未知").replace("T", " ").replace("+00:00", " UTC")


def _snapshot_summary(raw: object) -> str:
    try:
        snapshot = json.loads(str(raw or "{}"))
    except (TypeError, json.JSONDecodeError):
        return "断线现场快照损坏，无法解析"
    process_count = snapshot.get("qq_process_count", "未知")
    probe = snapshot.get("qq_process_probe", "未知")
    return f"QQ 进程：{process_count}；进程探测：{probe}；OneBot 连接：{snapshot.get('onebot_connection_count', 0)}"


def _incident_text(row: Any) -> str:
    duration = row["duration_seconds"]
    duration_text = f"{duration} 秒" if duration is not None else "尚未恢复"
    return "\n".join(
        (
            f"#{row['incident_id']}｜{'已恢复' if row['status'] == 'recovered' else '未恢复'}｜{duration_text}",
            f"发现：{_format_time(row['detected_at'])}",
            f"断线前最后连接：{_format_time(row['last_connected_at'])}",
            f"触发：{row['trigger']}",
            f"判断：{row['diagnosis']}",
            f"现场：{_snapshot_summary(row['snapshot_json'])}",
        )
    )


async def _require_admin(matcher: Any, event: MessageEvent) -> bool:
    if is_super_admin(int(event.user_id)):
        return True
    await matcher.finish("只有超级管理员可以查看 QQ 传输维护记录。")
    return False


async def _tick() -> None:
    monitor.reconcile(_current_bot_ids(), "scheduler")


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    monitor.reconcile(_current_bot_ids(), "connect_hook")


@driver.on_bot_disconnect
async def _on_bot_disconnect(bot: Bot) -> None:
    monitor.reconcile(_current_bot_ids(), "disconnect_hook")


@driver.on_startup
async def _start_qq_transport_maintenance() -> None:
    if not settings.qq_transport_maintenance_enabled:
        logger.info("QQ transport local maintenance is disabled")
        return
    scheduler.add_job(
        _tick,
        "interval",
        seconds=settings.qq_transport_maintenance_interval_seconds,
        id="qq-transport-local-maintenance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    await _tick()
    logger.info(
        "QQ transport local maintenance started; transport=%s interval=%ss",
        settings.qq_platform_transport,
        settings.qq_transport_maintenance_interval_seconds,
    )


@driver.on_shutdown
async def _stop_qq_transport_maintenance() -> None:
    monitor.stop()
    if scheduler.running:
        scheduler.shutdown(wait=False)


maintenance_status = on_command("QQ传输维护状态", priority=5, block=True)


@maintenance_status.handle()
async def _(event: MessageEvent):
    if not await _require_admin(maintenance_status, event):
        return
    state = monitor.status()
    lines = [
        f"QQ 传输：{settings.qq_platform_transport}",
        "本地维护：" + ("开启" if settings.qq_transport_maintenance_enabled else "关闭"),
        f"检查间隔：{settings.qq_transport_maintenance_interval_seconds} 秒",
        "OneBot 连接：" + ("在线" if state["connected"] else "离线"),
        f"本次运行最后在线：{_format_time(state['last_connected_at'])}",
    ]
    incident = state["open_incident"]
    if incident is not None:
        lines.extend(("当前断线事件：", _incident_text(incident)))
    else:
        lines.append("当前没有未恢复的断线事件。")
    await maintenance_status.finish("\n".join(lines))


offline_records = on_command("QQ传输离线记录", priority=5, block=True)


@offline_records.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if not await _require_admin(offline_records, event):
        return
    raw = _text_arg(args)
    if raw and (not raw.isdigit() or not 1 <= int(raw) <= 20):
        await offline_records.finish("用法：#QQ传输离线记录 [1-20]")
    rows = database().qq_transport_connection_incidents(int(raw or 10))
    if not rows:
        await offline_records.finish("暂无 QQ 传输被动离线记录。")
    await offline_records.finish("【QQ 传输离线记录】\n\n" + "\n\n".join(_incident_text(row) for row in rows))
