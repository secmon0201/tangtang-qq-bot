from __future__ import annotations

import asyncio
import re
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.params import CommandArg
from zoneinfo import ZoneInfo

from bot.config import settings
from bot.application.command_helpers import current_group, user_id
from bot.services.codex_tasks import CodexWorker, MAX_TASK_PROMPT_CHARS, task_title_from_prompt
from bot.services.forward import build_forward_nodes
from bot.services.qq_platform import call_qq_action
from bot.services.roles import is_super_admin
from bot.services.runtime import database


db = database()
worker = CodexWorker(db)
driver = get_driver()
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))

STATUS_LABELS = {
    "draft": "待启动",
    "queued": "已排队",
    "running": "执行中",
    "stopping": "停止中",
    "completed": "已完成",
    "failed": "失败，等待重试",
    "cancelled": "已取消",
}


def task_id_from_text(value: str) -> int | None:
    match = re.fullmatch(r"\s*(?:#|任务)?(\d{1,12})\s*", value)
    return int(match.group(1)) if match else None


def task_status_text(row: Any) -> str:
    updated = str(row["updated_at"]).replace("T", " ").replace("+00:00", " UTC")
    lines = [
        f"Codex 任务 #{row['task_id']}｜{STATUS_LABELS.get(str(row['status']), row['status'])}",
        f"标题：{row['title']}",
        f"续办轮次：{row['message_count']}，待执行：{row['queued_count']}",
        f"会话上下文：{'已建立，可续办' if row['codex_thread_id'] else '尚未建立'}",
        f"最近更新：{updated}",
    ]
    if row["last_error"]:
        lines.append(f"最近问题：{str(row['last_error'])[:500]}")
    elif row["last_result"]:
        lines.append("已有最终结果，使用 #Codex 结果 ID 查看。")
    return "\n".join(lines)


def task_help_text() -> str:
    return "\n".join(
        [
            "Codex 持续任务（仅超级管理员）",
            "#Codex <需求>：新建待启动任务。",
            "#Codex 新 <标题> | <需求>：带标题新建任务。",
            "#Codex 续 <ID> <补充需求>：给已有任务追加一轮，同一 Codex 会话会保留上下文。",
            "#启动Codex <ID>：正式启动待执行轮次；全机同一时间只运行一个任务。",
            "#暂停Codex <ID>：停止当前轮，未执行的续办内容保留，之后可再次启动。",
            "#取消Codex <ID>：丢弃未执行内容并关闭该任务，不能再续办。",
            "#Codex 重试 <ID>：复制最近失败/中断的一轮，随后用启动命令执行。",
            "#Codex 状态 [ID]、#Codex 列表、#Codex 结果 <ID>。",
            f"单次需求最多 {MAX_TASK_PROMPT_CHARS} 字；完成或失败均会向固定通知群发送折叠结果。",
        ]
    )


async def require_super_admin(matcher: Any, event: MessageEvent) -> None:
    if not is_super_admin(int(event.user_id)):
        await matcher.finish("只有超级管理员可以管理 Codex 持续任务。")


async def send_task_result(bot: Bot, matcher: Any, event: MessageEvent, row: Any) -> None:
    result = str(row["last_result"] or row["last_error"] or "").strip()
    if not result:
        await matcher.finish("该任务还没有可查看的执行结果。")
    title = f"Codex 任务 #{row['task_id']} 执行结果"
    try:
        nodes = build_forward_nodes([result], [None], bot.self_id, title=title)
        if isinstance(event, GroupMessageEvent):
            await call_qq_action(
                bot, "send_group_forward_msg", group_id=int(event.group_id), messages=nodes
            )
        else:
            await call_qq_action(
                bot, "send_private_forward_msg", user_id=int(event.user_id), messages=nodes
            )
    except Exception:
        logger.exception("Codex task result forward failed")
        await matcher.finish(result[:1800])
    await matcher.finish("执行结果已发送为合并转发。")


codex_task = on_command(
    "Codex", aliases={"codex", "Codex任务", "codex任务"}, priority=5, block=True
)


@codex_task.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await require_super_admin(codex_task, event)
    raw = args.extract_plain_text().strip()
    if not raw or raw.lower() in {"帮助", "help"}:
        await codex_task.finish(task_help_text())

    if raw.lower() in {"列表", "list"}:
        rows = db.codex_tasks()
        if not rows:
            await codex_task.finish("目前没有 Codex 持续任务。")
        lines = ["Codex 持续任务："]
        for row in rows:
            lines.append(
                f"#{row['task_id']} {STATUS_LABELS.get(str(row['status']), row['status'])}｜"
                f"待执行 {row['queued_count']}｜{row['title']}"
            )
        await codex_task.finish("\n".join(lines))

    status_match = re.fullmatch(r"(?:状态|status)\s*(.*)", raw, re.IGNORECASE | re.S)
    if status_match:
        target_raw = status_match.group(1).strip()
        if not target_raw:
            rows = db.codex_tasks(limit=1)
            if not rows:
                await codex_task.finish("目前没有 Codex 持续任务。")
            await codex_task.finish(task_status_text(rows[0]))
        task_id = task_id_from_text(target_raw)
        row = db.codex_task(task_id or 0)
        if task_id is None or row is None:
            await codex_task.finish("任务不存在。用法：#Codex 状态 ID")
        await codex_task.finish(task_status_text(row))

    result_match = re.fullmatch(r"(?:结果|result)\s*(.*)", raw, re.IGNORECASE | re.S)
    if result_match:
        task_id = task_id_from_text(result_match.group(1))
        row = db.codex_task(task_id or 0)
        if task_id is None or row is None:
            await codex_task.finish("任务不存在。用法：#Codex 结果 ID")
        await send_task_result(bot, codex_task, event, row)

    retry_match = re.fullmatch(r"(?:重试|retry)\s*(.*)", raw, re.IGNORECASE | re.S)
    if retry_match:
        task_id = task_id_from_text(retry_match.group(1))
        if task_id is None:
            await codex_task.finish("用法：#Codex 重试 ID")
        state = db.retry_codex_task(task_id, user_id(event))
        messages = {
            "queued": f"任务 #{task_id} 已加入重试轮次。请发送 #启动Codex {task_id} 正式执行。",
            "not_found": "任务不存在。",
            "cancelled": "该任务已取消，不能重试。",
            "running": "该任务正在执行，不能重试。",
            "nothing_to_retry": "该任务没有失败或中断的轮次可重试。",
        }
        db.audit(user_id(event), "codex_task_retry", current_group(event), f"task_id={task_id}")
        await codex_task.finish(messages[state])

    continuation_match = re.fullmatch(r"(?:续|继续|continue)\s+(\d{1,12})\s+(.+)", raw, re.IGNORECASE | re.S)
    if continuation_match:
        task_id = int(continuation_match.group(1))
        prompt = continuation_match.group(2).strip()
        if len(prompt) > MAX_TASK_PROMPT_CHARS:
            await codex_task.finish(f"单次需求不能超过 {MAX_TASK_PROMPT_CHARS} 字。")
        try:
            db.append_codex_task_message(task_id, prompt, user_id(event))
        except ValueError as exc:
            await codex_task.finish(str(exc))
        row = db.codex_task(task_id)
        db.audit(user_id(event), "codex_task_continue", current_group(event), f"task_id={task_id}")
        if row is not None and str(row["status"]) in {"running", "queued"}:
            await codex_task.finish(f"已追加到任务 #{task_id}，当前轮完成后会继续执行。")
        await codex_task.finish(f"已追加到任务 #{task_id}。请发送 #启动Codex {task_id} 正式执行。")

    new_match = re.fullmatch(r"(?:新|new)\s+(.+)", raw, re.IGNORECASE | re.S)
    if new_match:
        payload = new_match.group(1).strip()
        if "|" in payload:
            title, prompt = (part.strip() for part in payload.split("|", 1))
        else:
            prompt = payload
            title = task_title_from_prompt(prompt)
    else:
        prompt = raw
        title = task_title_from_prompt(prompt)
    if len(prompt) > MAX_TASK_PROMPT_CHARS:
        await codex_task.finish(f"单次需求不能超过 {MAX_TASK_PROMPT_CHARS} 字。")
    task_id = db.create_codex_task(title, prompt, user_id(event))
    db.audit(user_id(event), "codex_task_create", current_group(event), f"task_id={task_id}")
    await codex_task.finish(
        f"Codex 任务 #{task_id} 已创建为待启动。\n标题：{title}\n发送 #启动Codex {task_id} 正式执行。"
    )


start_codex = on_command("启动Codex", aliases={"启动codex"}, priority=5, block=True)


@start_codex.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_super_admin(start_codex, event)
    task_id = task_id_from_text(args.extract_plain_text())
    if task_id is None:
        await start_codex.finish("用法：#启动Codex ID")
    state = db.start_codex_task(task_id)
    if state == "queued":
        db.audit(user_id(event), "codex_task_start", current_group(event), f"task_id={task_id}")
        asyncio.create_task(worker.run_once(), name=f"codex-task-{task_id}")
        await start_codex.finish(f"Codex 任务 #{task_id} 已启动，将由本机 worker 串行执行。")
    if state.startswith("busy:"):
        await start_codex.finish(f"任务 #{state.split(':', 1)[1]} 正在占用 worker，请先等待完成或暂停它。")
    messages = {
        "not_found": "任务不存在。",
        "cancelled": "该任务已取消，不能启动。",
        "empty": "该任务没有待执行内容。请用 #Codex 续 ID <需求> 追加。",
    }
    await start_codex.finish(messages.get(state, "任务启动失败。"))


pause_codex = on_command("暂停Codex", aliases={"暂停codex", "停止Codex", "停止codex"}, priority=5, block=True)


@pause_codex.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_super_admin(pause_codex, event)
    task_id = task_id_from_text(args.extract_plain_text())
    if task_id is None:
        await pause_codex.finish("用法：#暂停Codex ID")
    state = await worker.request_stop(task_id)
    messages = {
        "stopping": f"正在停止任务 #{task_id} 的当前轮；未执行续办内容会保留。",
        "paused": f"任务 #{task_id} 已暂停；未执行内容会保留。",
        "not_found": "任务不存在。",
        "already_cancelled": "该任务已经取消。",
    }
    db.audit(user_id(event), "codex_task_pause", current_group(event), f"task_id={task_id}")
    await pause_codex.finish(messages[state])


cancel_codex = on_command("取消Codex", aliases={"取消codex"}, priority=5, block=True)


@cancel_codex.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_super_admin(cancel_codex, event)
    task_id = task_id_from_text(args.extract_plain_text())
    if task_id is None:
        await cancel_codex.finish("用法：#取消Codex ID")
    state = await worker.request_stop(task_id, cancel_all=True)
    messages = {
        "cancelling": f"正在取消任务 #{task_id}；当前轮停止后将关闭任务。",
        "cancelled": f"任务 #{task_id} 已取消，待执行内容已丢弃。",
        "not_found": "任务不存在。",
        "already_cancelled": "该任务已经取消。",
    }
    db.audit(user_id(event), "codex_task_cancel", current_group(event), f"task_id={task_id}")
    await cancel_codex.finish(messages[state])


codex_task_help = on_command("Codex帮助", aliases={"codex帮助"}, priority=5, block=True)


@codex_task_help.handle()
async def _(event: MessageEvent):
    await require_super_admin(codex_task_help, event)
    await codex_task_help.finish(task_help_text())


@driver.on_startup
async def _start_codex_worker() -> None:
    if not settings.codex_worker_enabled:
        logger.info("Codex persistent task worker is disabled")
        return
    interrupted = db.recover_codex_tasks_after_restart()
    if interrupted:
        logger.warning("Marked %s interrupted Codex task turn(s) after restart", interrupted)
    scheduler.add_job(
        worker.run_once,
        "interval",
        seconds=settings.codex_worker_poll_seconds,
        id="codex-persistent-task-worker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Codex persistent task worker started; interval=%ss timeout=%ss sandbox=%s",
        settings.codex_worker_poll_seconds,
        settings.codex_worker_timeout_seconds,
        settings.codex_worker_sandbox,
    )


@driver.on_shutdown
async def _stop_codex_worker() -> None:
    await worker.shutdown()
    if scheduler.running:
        scheduler.shutdown(wait=False)
