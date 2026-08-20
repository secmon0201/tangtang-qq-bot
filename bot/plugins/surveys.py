from __future__ import annotations

from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import Rule

from bot.config import settings
from bot.services.forward import build_forward_nodes
from bot.services.media import local_image_segment
from bot.services.qq_platform import call_qq_action
from bot.services.reports import ReportRenderer
from bot.services.runtime import database
from bot.services.surveys import (
    parse_survey_create_payload,
    parse_survey_id,
    survey_status_label,
)


db = database()
driver = get_driver()
scheduler = AsyncIOScheduler(timezone=settings.timezone)
renderer = ReportRenderer(
    settings.report_dir,
    settings.report_font_path,
    settings.report_retention_hours,
    settings.timezone,
    settings.command_prefix,
)


def is_super_admin(event: MessageEvent) -> bool:
    return int(event.user_id) in settings.operator_ids


async def require_super_admin(matcher: Any, event: MessageEvent) -> None:
    if not is_super_admin(event):
        await matcher.finish("只有超级管理员可以管理问卷调查。")


def target_groups_text(survey_id: int) -> str:
    return ", ".join(str(row["group_id"]) for row in db.survey_groups(survey_id))


def survey_poster_path(survey: Any):
    return renderer.render_survey_poster(
        int(survey["survey_id"]),
        str(survey["question"]),
    )


async def send_survey_broadcasts(bot: Bot, survey: Any) -> tuple[int, int]:
    survey_id = int(survey["survey_id"])
    sent = 0
    failed = 0
    try:
        image_path = survey_poster_path(survey)
    except Exception:
        logger.exception("Survey poster rendering failed: survey=%s", survey_id)
        return sent, len(db.pending_survey_broadcasts(survey_id))
    message = Message(
        [
            local_image_segment(image_path),
            MessageSegment.text(
                "\n直接在群里发送 ###+内容 即可，\n糖糖会记录你说的话并转达。"
            ),
        ]
    )
    for row in db.pending_survey_broadcasts(survey_id):
        group_id = int(row["group_id"])
        try:
            await call_qq_action(
                bot,
                "send_group_msg",
                group_id=group_id,
                message=message,
            )
        except Exception as exc:
            logger.exception("Survey broadcast failed: survey=%s group=%s", survey_id, group_id)
            db.mark_survey_broadcast_error(survey_id, group_id, str(exc))
            failed += 1
        else:
            db.mark_survey_broadcast_sent(survey_id, group_id)
            sent += 1
    return sent, failed


def feedback_content(event: MessageEvent) -> str | None:
    text = event.get_plaintext().strip()
    if not text.startswith("###"):
        return None
    return text[3:].strip()


def accepts_feedback(event: MessageEvent) -> bool:
    if feedback_content(event) is None:
        return False
    return not isinstance(event, GroupMessageEvent) or db.is_managed_group(int(event.group_id))


def feedback_entry_text(row: Any) -> str:
    source = f"群 {row['source_group_id']}" if row["source_group_id"] else "私聊"
    survey = f"问卷：#{row['survey_id']}" if row["survey_id"] else "问卷：历史未关联"
    return (
        f"反馈 #{row['feedback_id']}\n"
        f"{survey}  QQ：{row['user_id']}  来源：{source}\n"
        f"时间：{row['created_at']}\n"
        f"内容：{row['content']}"
    )


FEEDBACKS_PER_DIALOG = 20
DIALOGS_PER_RECORD = 10
FEEDBACKS_PER_PAGE = FEEDBACKS_PER_DIALOG * DIALOGS_PER_RECORD


def feedback_dialogs(rows: list[Any]) -> list[list[Any]]:
    return [rows[index : index + FEEDBACKS_PER_DIALOG] for index in range(0, len(rows), FEEDBACKS_PER_DIALOG)]


def feedback_dialog_text(rows: list[Any]) -> str:
    return "\n\n".join(feedback_entry_text(row) for row in rows)


def feedback_records(rows: list[Any]) -> list[list[list[Any]]]:
    dialogs = feedback_dialogs(rows)
    return [dialogs[index : index + DIALOGS_PER_RECORD] for index in range(0, len(dialogs), DIALOGS_PER_RECORD)]


async def send_feedback_records(
    bot: Bot, rows: list[Any], title: str, group_id: int | None = None, user_id: int | None = None
) -> None:
    if (group_id is None) == (user_id is None):
        raise ValueError("feedback delivery needs exactly one group or private recipient")
    for record in feedback_records(rows):
        messages = [feedback_dialog_text(dialog) for dialog in record]
        nodes = build_forward_nodes(
            messages,
            [None] * len(messages),
            getattr(bot, "self_id", "机器人"),
            title=title,
        )
        if group_id is not None:
            await call_qq_action(bot, "send_group_forward_msg", group_id=group_id, messages=nodes)
        else:
            await call_qq_action(bot, "send_private_forward_msg", user_id=user_id, messages=nodes)


async def deliver_feedback_notifications(bot: Bot | None = None) -> None:
    target = bot or next(iter(get_bots().values()), None)
    if target is None or not settings.operator_ids:
        return
    entries = db.feedback_entries()
    db.ensure_feedback_deliveries(
        (int(row["feedback_id"]) for row in entries), settings.operator_ids
    )
    for operator_id in sorted(settings.operator_ids):
        rows = db.pending_feedback_deliveries(operator_id)
        for record in feedback_records(rows):
            batch = [row for dialog in record for row in dialog]
            try:
                messages = [feedback_dialog_text(dialog) for dialog in record]
                nodes = build_forward_nodes(
                    messages,
                    [None] * len(messages),
                    getattr(target, "self_id", "机器人"),
                    title="新的用户反馈",
                )
                await call_qq_action(
                    target, "send_private_forward_msg", user_id=operator_id, messages=nodes
                )
            except Exception as exc:
                logger.exception("Feedback notification failed: operator=%s", operator_id)
                for row in batch:
                    db.mark_feedback_delivery_error(int(row["feedback_id"]), operator_id, str(exc))
            else:
                for row in batch:
                    db.mark_feedback_delivery_sent(int(row["feedback_id"]), operator_id)


async def scheduled_feedback_notifications() -> None:
    await deliver_feedback_notifications()


@driver.on_startup
async def _start_feedback_scheduler() -> None:
    scheduler.add_job(
        scheduled_feedback_notifications,
        "interval",
        seconds=settings.feedback_notification_interval_seconds,
        id="feedback-notification-delivery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Feedback notification scheduler started; interval=%ss",
        settings.feedback_notification_interval_seconds,
    )


@driver.on_shutdown
async def _stop_feedback_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


async def create_survey_handler(matcher: Any, event: MessageEvent, args: Any) -> None:
    await require_super_admin(matcher, event)
    try:
        payload = parse_survey_create_payload(args.extract_plain_text())
        survey_id = db.create_survey(
            payload.question,
            int(event.user_id),
            payload.group_ids,
        )
    except ValueError as exc:
        await matcher.finish(
            "创建格式：\n"
            "问卷内容：...\n"
            "调查群：群号1,群号2\n"
            f"{exc}"
        )
    await matcher.finish(
        f"问卷调查已创建，ID：{survey_id}\n"
        f"目标群：{target_groups_text(survey_id)}\n"
        f"执行 {settings.command_prefix}发送问券调查 {survey_id} 后群发。"
    )


create_survey = on_command("创建问卷调查", aliases={"创建问券调查"}, priority=5, block=True)


@create_survey.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await create_survey_handler(create_survey, event, args)


end_survey = on_command("结束问券调查", aliases={"结束问卷调查"}, priority=5, block=True)


@end_survey.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_super_admin(end_survey, event)
    survey_id = parse_survey_id(args.extract_plain_text())
    if survey_id is None:
        await end_survey.finish(f"用法：{settings.command_prefix}结束问券调查 ID")
    survey = db.survey(survey_id)
    if survey is None:
        await end_survey.finish("问卷调查不存在。")
    if not db.end_survey(survey_id):
        await end_survey.finish("该问卷调查已经结束。")
    await end_survey.finish(f"问卷调查 #{survey_id} 已结束，不再接收新的反馈。")


list_surveys = on_command("问券调查列表", aliases={"问卷调查列表"}, priority=5, block=True)


@list_surveys.handle()
async def _(event: MessageEvent):
    await require_super_admin(list_surveys, event)
    rows = db.surveys()
    if not rows:
        await list_surveys.finish("暂无问卷调查。")
    text = "问卷调查列表：\n" + "\n".join(
        f"#{row['survey_id']} [{survey_status_label(str(row['status']))}] "
        f"{row['question'][:48]}（{row['feedback_count']} 条反馈）"
        for row in rows
    )
    await list_surveys.finish(text)


query_survey = on_command("查询问券调查", aliases={"查询问卷调查"}, priority=5, block=True)


@query_survey.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_super_admin(query_survey, event)
    survey_id = parse_survey_id(args.extract_plain_text())
    if survey_id is None:
        await query_survey.finish(f"用法：{settings.command_prefix}查询问券调查 ID")
    survey = db.survey(survey_id)
    if survey is None:
        await query_survey.finish("问卷调查不存在。")
    await query_survey.finish(
        f"问卷调查 #{survey_id} 当前{survey_status_label(str(survey['status']))}，"
        f"共有 {survey['feedback_count']} 条反馈。"
    )


send_survey = on_command("发送问券调查", aliases={"发送问卷调查"}, priority=5, block=True)


@send_survey.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await require_super_admin(send_survey, event)
    survey_id = parse_survey_id(args.extract_plain_text())
    if survey_id is None:
        await send_survey.finish(f"用法：{settings.command_prefix}发送问券调查 ID")
    survey = db.survey(survey_id)
    if survey is None:
        await send_survey.finish("问卷调查不存在。")
    if str(survey["status"]) != "active":
        await send_survey.finish("问卷调查已经结束，不能再发送。")
    if not db.publish_survey(survey_id):
        await send_survey.finish("问卷调查已经结束，不能再发送。")
    sent, failed = await send_survey_broadcasts(bot, survey)
    if not sent and not failed:
        await send_survey.finish("该问卷调查已发送至全部目标群，不会重复群发。")
    summary = f"问卷调查 #{survey_id} 已发送到 {sent} 个目标群。"
    if failed:
        summary += f"{failed} 个群发送失败，再执行一次会重试失败的群。"
    await send_survey.finish(summary)


feedback = on_message(rule=Rule(accepts_feedback), priority=3, block=True)


@feedback.handle()
async def _(event: MessageEvent):
    content = feedback_content(event) or ""
    if not content:
        await feedback.finish("反馈内容不能为空，请在 ### 后写下具体意见。")
    if len(content) > 1000:
        await feedback.finish("反馈内容不能超过 1000 个字符。")
    survey = db.current_feedback_survey()
    if survey is None:
        await feedback.finish("当前没有已发布的问卷公告，暂时无法提交反馈。")
    source_group_id = int(event.group_id) if isinstance(event, GroupMessageEvent) else None
    feedback_id = db.record_feedback(int(survey["survey_id"]), int(event.user_id), content, source_group_id)
    db.ensure_feedback_deliveries((feedback_id,), settings.operator_ids)
    await feedback.finish(f"已收到针对问卷 #{survey['survey_id']} 的反馈，制作人员会定时查看。")


def parse_feedback_lookup(raw: str) -> tuple[int | None, int] | None:
    tokens = raw.strip().split()
    if not tokens:
        return None, 1
    if len(tokens) == 1 and tokens[0].startswith("-") and tokens[0][1:].isdigit():
        return None, int(tokens[0][1:])
    if tokens[0].isdigit():
        page = 1
        if len(tokens) == 2 and tokens[1].startswith("-") and tokens[1][1:].isdigit():
            page = int(tokens[1][1:])
        elif len(tokens) != 1:
            return None
        return int(tokens[0]), page
    return None


feedback_list = on_command("查看反馈", aliases={"反馈意见清单", "反馈列表"}, priority=5, block=True)


@feedback_list.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await require_super_admin(feedback_list, event)
    lookup = parse_feedback_lookup(args.extract_plain_text())
    if lookup is None or lookup[1] < 1:
        await feedback_list.finish("用法：#查看反馈 [问卷ID] [-页码]；例如 #查看反馈、#查看反馈 -2、#查看反馈 12 或 #查看反馈 12 -2。")
    survey_id, page = lookup
    if survey_id is not None and db.survey(survey_id) is None:
        await feedback_list.finish("问卷调查不存在。")
    rows = db.feedback_entries(
        survey_id=survey_id,
        limit=FEEDBACKS_PER_PAGE,
        offset=(page - 1) * FEEDBACKS_PER_PAGE,
    )
    if not rows:
        await feedback_list.finish(f"第 {page} 页没有反馈意见。")
    title = f"问卷 #{survey_id} 的反馈" if survey_id is not None else "全部反馈"
    group_id = int(event.group_id) if isinstance(event, GroupMessageEvent) else None
    await send_feedback_records(
        bot,
        rows,
        title=f"{title} 第 {page} 页",
        group_id=group_id,
        user_id=None if group_id is not None else int(event.user_id),
    )
    await feedback_list.finish()
