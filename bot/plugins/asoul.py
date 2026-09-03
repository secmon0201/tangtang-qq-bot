from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import HTTPException, status
from fastapi.responses import HTMLResponse
from nonebot import get_bots, get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.params import CommandArg

from bot.application.local_features import FeatureRequest, register_local_feature
from bot.config import settings
from bot.application.command_helpers import text_arg
from bot.services.asoul import ASoulService
from bot.services.qq_platform import call_qq_action
from bot.services.asoul_render import ASoulImageRenderer
from bot.services.asoul_web_render import (
    ASoulWebRenderer,
    VALID_SCHEDULE_VIEWS,
    asoul_live_web_url,
    page_html,
    schedule_payload,
)
from bot.services.roles import is_super_admin
from bot.services.runtime import database, group_domains


service = ASoulService(database())
renderer = ASoulImageRenderer(settings.report_dir, settings.report_font_path)
web_renderer = ASoulWebRenderer(
    settings.report_dir,
    renderer._remote_image,
    renderer.select_schedule_sticker,
)
driver = get_driver()
scheduler = AsyncIOScheduler(timezone=settings.timezone)


def _admin(event: MessageEvent) -> bool:
    return is_super_admin(int(event.user_id))


async def _require_admin(matcher: Any, event: MessageEvent) -> None:
    if not _admin(event):
        await matcher.finish("只有超级管理员可以使用 A-SOUL 管理功能。")


async def _send_monitor_messages() -> None:
    target_groups = group_domains().enabled_groups("bilibili")
    if not settings.asoul_bili_enabled or not target_groups:
        return
    bots = get_bots()
    bot = next(iter(bots.values()), None)
    if bot is None:
        return
    messages = await service.poll_updates()
    if not messages:
        return
    for group_id in sorted(target_groups):
        for message in messages:
            try:
                payload: Any = message
                live_details = service.live_notification_details(message)
                dynamic_details = service.dynamic_notification_details(message)
                video_details = service.video_notification_details(message)
                comment_details = service.comment_notification_details(message)
                caption = (
                    str(dynamic_details.get("url") or message)
                    if dynamic_details is not None
                    else str(video_details.get("url") or message)
                    if video_details is not None
                    else message
                )
                if settings.asoul_bili_render_cards:
                    try:
                        card = await web_renderer.render_notification(
                            message,
                            live=live_details,
                            dynamic=dynamic_details,
                            video=video_details,
                            comment=comment_details,
                        )
                        payload = MessageSegment.image(file=card.resolve().as_uri()) + "\n" + caption
                    except Exception:
                        logger.exception("A-SOUL HTML card render failed; trying Pillow fallback")
                        try:
                            card = await renderer.render_bilibili_notification(
                                message,
                                live=live_details,
                                dynamic=dynamic_details,
                                video=video_details,
                            )
                            payload = MessageSegment.image(file=card.resolve().as_uri()) + "\n" + caption
                        except Exception:
                            logger.exception("A-SOUL Pillow card render failed; sending text fallback")
                            payload = caption
                elif dynamic_details is not None or video_details is not None:
                    payload = caption
                is_live = message.startswith("【开播】")
                if is_live and await _bot_can_at_all(bot, group_id):
                    payload = MessageSegment.at("all") + " " + payload
                await call_qq_action(bot, "send_group_msg", group_id=group_id, message=payload)
            except Exception:
                logger.exception("A-SOUL Bilibili push failed for group=%s", group_id)


async def _bot_can_at_all(bot: Bot, group_id: int) -> bool:
    try:
        response = await call_qq_action(
            bot,
            "get_group_member_info",
            group_id=int(group_id),
            user_id=int(bot.self_id),
            no_cache=False,
        )
    except Exception:
        logger.warning("A-SOUL @all skipped: unable to verify bot role for group=%s", group_id)
        return False
    member = response.get("data", response) if isinstance(response, dict) else {}
    role = str(member.get("role") or "member") if isinstance(member, dict) else "member"
    allowed = role in {"owner", "admin"}
    if not allowed:
        logger.info("A-SOUL @all skipped: bot role=%s for group=%s", role, group_id)
    return allowed


@driver.on_startup
async def _start_asoul_monitor() -> None:
    if settings.asoul_bili_render_cards:
        try:
            await web_renderer.warmup()
        except Exception:
            logger.exception("A-SOUL HTML renderer warmup failed; Pillow fallback remains available")
    if not settings.asoul_bili_enabled:
        logger.info("A-SOUL Bilibili monitor is disabled")
        return
    if not group_domains().enabled_groups("bilibili"):
        logger.warning("A-SOUL Bilibili monitor enabled without Bilibili push groups")
        return
    scheduler.add_job(
        _send_monitor_messages,
        "interval",
        seconds=settings.asoul_bili_poll_interval_seconds,
        id="asoul-bilibili-monitor",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    await _send_monitor_messages()
    logger.info("A-SOUL Bilibili monitor started")


@driver.on_shutdown
async def _stop_asoul_monitor() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await web_renderer.close()


asoul_help = on_command("A魂帮助", aliases={"bot帮助"}, priority=5, block=True)
today_live = on_command("今日直播", priority=5, block=True)
tomorrow_live = on_command("明日直播", priority=5, block=True)
week_live = on_command("本周直播", priority=5, block=True)


@asoul_help.handle()
async def _():
    await asoul_help.finish(
        "【A-SOUL 功能】\n"
        "#今日直播  #明日直播  #本周直播\n"
        "管理员：#日程高亮、#取消日程高亮、#日程高亮列表、#取消日程高亮记录、"
        "#bili_status、#bili_login、#bili_logout、#bili_test_*。"
    )


async def finish_schedule_reply(matcher: Any, title: str, target_day: datetime, args: str = "") -> None:
    items = await service.schedule_for_day(target_day.date())
    fallback = service.render_schedule(target_day.date(), title, items)
    view = "tomorrow" if title == "明日直播" else "today"
    try:
        card = await web_renderer.render_schedule(
            view,
            [(target_day.date(), items)],
            generated_at=datetime.now(service.timezone).strftime("%Y-%m-%d %H:%M"),
        )
    except Exception:
        logger.exception("A-SOUL HTML schedule render failed; trying Pillow fallback")
        try:
            card = await renderer.render_schedule(target_day.date(), title, items)
        except Exception:
            logger.exception("A-SOUL Pillow schedule render failed; sending text fallback")
            await matcher.finish(fallback)
    link = asoul_live_web_url(view)
    payload: Any = MessageSegment.image(file=card.resolve().as_uri())
    if link:
        payload += f"\n线上：{link}"
    await matcher.finish(payload)


async def finish_week_schedule_reply(matcher: Any, args: str = "") -> None:
    now = datetime.now(service.timezone)
    first, last = now.date(), (now + timedelta(days=6 - now.weekday())).date()
    schedules = await service.schedule_for_days(first, last)
    blocks = []
    card_days = []
    for target_day in (first + timedelta(days=offset) for offset in range((last - first).days + 1)):
        items = schedules.get(target_day, [])
        blocks.append(service.render_schedule(target_day, "本周直播", items))
        card_days.append((target_day, items))
    try:
        card = await web_renderer.render_schedule(
            "week",
            card_days,
            generated_at=now.strftime("%Y-%m-%d %H:%M"),
        )
    except Exception:
        logger.exception("A-SOUL HTML weekly schedule render failed; trying Pillow fallback")
        try:
            card = await renderer.render_week_schedule(card_days)
        except Exception:
            logger.exception("A-SOUL Pillow weekly schedule render failed; sending text fallback")
            await matcher.finish("\n\n".join(blocks))
    link = asoul_live_web_url("week")
    payload: Any = MessageSegment.image(file=card.resolve().as_uri())
    if link:
        payload += f"\n线上：{link}"
    await matcher.finish(payload)


@today_live.handle()
async def _(args=CommandArg()):
    await finish_schedule_reply(today_live, "今日直播", datetime.now(service.timezone), text_arg(args))


@tomorrow_live.handle()
async def _(args=CommandArg()):
    now = datetime.now(service.timezone)
    await finish_schedule_reply(tomorrow_live, "明日直播", now + timedelta(days=1), text_arg(args))


@week_live.handle()
async def _(args=CommandArg()):
    await finish_week_schedule_reply(week_live, text_arg(args))


schedule_highlight = on_command("日程高亮", priority=5, block=True)
schedule_unhighlight = on_command("取消日程高亮", priority=5, block=True)
schedule_highlight_list = on_command("日程高亮列表", priority=5, block=True)
schedule_highlight_remove = on_command("取消日程高亮记录", priority=5, block=True)


async def _date_items(raw_day: str) -> tuple[datetime, list[Any]]:
    target = datetime.strptime(raw_day, "%Y-%m-%d").replace(tzinfo=service.timezone)
    return target, await service.schedule_for_day(target.date())


@schedule_highlight.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(schedule_highlight, event)
    tokens = text_arg(args).split()
    if not tokens:
        await schedule_highlight.finish("用法：#日程高亮 YYYY-MM-DD [序号] [粉色|红色|白金色]")
    try:
        target, items = await _date_items(tokens[0])
    except ValueError:
        await schedule_highlight.finish("日期格式错误，请使用 YYYY-MM-DD。")
    if len(tokens) == 1:
        if not items:
            await schedule_highlight.finish(f"{target:%Y-%m-%d} 没有可标记的直播日程。")
        lines = [f"{target:%Y-%m-%d} 可标记日程："]
        lines.extend(
            f"{index}. {item.starts_at:%H:%M}｜{' / '.join(item.hosts) or '待确认'}｜{item.content}"
            for index, item in enumerate(items, start=1)
        )
        await schedule_highlight.finish("\n".join(lines))
    if not tokens[1].isdigit() or not 1 <= int(tokens[1]) <= len(items):
        await schedule_highlight.finish(f"序号超出范围，当天共有 {len(items)} 条日程。")
    style = tokens[2] if len(tokens) > 2 else "白金色"
    if style not in {"粉色", "红色", "白金色"}:
        await schedule_highlight.finish("高亮颜色仅支持：粉色、红色、白金色。")
    item = items[int(tokens[1]) - 1]
    service.set_highlight(item, style)
    await schedule_highlight.finish(f"已设为特别关注（{style}）：{target:%Y-%m-%d} {item.starts_at:%H:%M} {' / '.join(item.hosts)}《{item.content}》")


@schedule_unhighlight.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(schedule_unhighlight, event)
    tokens = text_arg(args).split()
    if len(tokens) != 2 or not tokens[1].isdigit():
        await schedule_unhighlight.finish("用法：#取消日程高亮 YYYY-MM-DD 序号")
    try:
        target, items = await _date_items(tokens[0])
    except ValueError:
        await schedule_unhighlight.finish("日期格式错误，请使用 YYYY-MM-DD。")
    index = int(tokens[1])
    if not 1 <= index <= len(items):
        await schedule_unhighlight.finish(f"序号超出范围，当天共有 {len(items)} 条日程。")
    item = items[index - 1]
    if not service.remove_highlight(item):
        await schedule_unhighlight.finish("该日程当前没有设置特别关注。")
    await schedule_unhighlight.finish(f"已取消特别关注：{target:%Y-%m-%d} {item.starts_at:%H:%M} {' / '.join(item.hosts)}《{item.content}》")


@schedule_highlight_list.handle()
async def _(event: MessageEvent):
    await _require_admin(schedule_highlight_list, event)
    records = service.highlight_records()
    if not records:
        await schedule_highlight_list.finish("当前没有特别关注日程。")
    await schedule_highlight_list.finish("【特别关注日程】\n" + "\n".join(f"{index}. {style}｜{key}" for index, (key, style) in enumerate(records.items(), 1)) + "\n使用 #取消日程高亮记录 序号 移除。")


@schedule_highlight_remove.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(schedule_highlight_remove, event)
    records = list(service.highlight_records())
    raw = text_arg(args).strip()
    if not raw.isdigit() or not 1 <= int(raw) <= len(records):
        await schedule_highlight_remove.finish(f"用法：#取消日程高亮记录 序号（当前共 {len(records)} 条）")
    service.remove_highlight_key(records[int(raw) - 1])
    await schedule_highlight_remove.finish("已取消特别关注记录。")


bili_status = on_command("bili_status", priority=5, block=True)
bili_login = on_command("bili_login", priority=5, block=True)
bili_logout = on_command("bili_logout", priority=5, block=True)
bili_test_dynamic = on_command("bili_test_dynamic", priority=5, block=True)
bili_dump_dynamic = on_command("bili_dump_dynamic", priority=5, block=True)
bili_test_video = on_command("bili_test_video", priority=5, block=True)
bili_test_live = on_command("bili_test_live", priority=5, block=True)
bili_dump_live = on_command("bili_dump_live", priority=5, block=True)
bili_test_atall = on_command("bili_test_atall", priority=5, block=True)
bili_test_comment = on_command("bili_test_comment", priority=5, block=True)
bili_test_all = on_command("bili_test_all", priority=5, block=True)


def _uid_or_usage(raw: str, usage: str) -> str | None:
    value = raw.strip()
    return value if value.isdigit() and int(value) > 0 else None


async def _require_private_bili(matcher: Any, event: MessageEvent) -> None:
    if isinstance(event, GroupMessageEvent):
        await matcher.finish("请在私聊中使用 B 站测试和调试指令，避免将接口结果发送到群聊。")


async def _test_bili(matcher: Any, kind: str, uid: str) -> None:
    fetchers = {"dynamic": service.fetch_dynamic, "video": service.fetch_video, "live": service.fetch_live}
    try:
        item = await fetchers[kind](uid)
    except Exception as exc:
        logger.warning("Bilibili {} test failed for uid {}: {}", kind, uid, type(exc).__name__)
        await matcher.finish(f"B站{ {'dynamic': '动态', 'video': '视频', 'live': '直播'}[kind]}接口暂时不可用，请稍后重试。")
    if item is None:
        await matcher.finish(f"UID {uid} 当前没有抓到可用{ {'dynamic': '动态', 'video': '视频', 'live': '直播间信息'}[kind]}。")
    label = {"dynamic": "B站最新动态", "video": "B站最新视频", "live": "B站直播状态"}[kind]
    suffix = "直播中" if kind == "live" and item.get("live") == "1" else "当前未开播" if kind == "live" else ""
    message = f"【{label}】{item['author']}\n{suffix}\n{item['text']}\n{item['url']}"
    video_details = None
    if kind == "video" or (kind == "dynamic" and item.get("notification_kind") == "video"):
        try:
            video_details = await service.enrich_video_notification(item)
        except Exception:
            video_details = item
    caption = item["url"] if kind in {"dynamic", "video"} else message
    if settings.asoul_bili_render_cards:
        try:
            card = await web_renderer.render_notification(
                message,
                dynamic=item if kind == "dynamic" and video_details is None else None,
                video=video_details,
            )
        except Exception:
            logger.exception("A-SOUL HTML test card render failed; trying Pillow fallback")
            try:
                card = await renderer.render_bilibili_notification(
                    message,
                    dynamic=item if kind == "dynamic" and video_details is None else None,
                    video=video_details,
                )
            except Exception:
                logger.exception("A-SOUL Pillow test card render failed; sending text fallback")
            else:
                await matcher.finish(MessageSegment.image(file=card.resolve().as_uri()) + "\n" + caption)
        else:
            await matcher.finish(MessageSegment.image(file=card.resolve().as_uri()) + "\n" + caption)
    await matcher.finish(caption)


async def _recent_comment_rows(uid: str) -> list[dict[str, str]]:
    dynamics = await service.fetch_dynamics(uid)
    resource = service.latest_comment_resource(dynamics)
    if resource is None:
        return []
    return await service.fetch_hot_comments(resource)


async def _bilibili_card_payload(message: str) -> Any:
    if not settings.asoul_bili_render_cards:
        return message
    try:
        card = await web_renderer.render_notification(message)
    except Exception:
        logger.exception("A-SOUL HTML card render failed; trying Pillow fallback")
        try:
            card = await renderer.render_bilibili_notification(message)
        except Exception:
            logger.exception("A-SOUL Pillow card render failed; sending text fallback")
            return message
    return MessageSegment.image(file=card.resolve().as_uri()) + "\n" + message


async def _schedule_web_payload(view: str) -> dict[str, Any]:
    if view not in VALID_SCHEDULE_VIEWS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="view 仅支持 today、tomorrow、week")
    now = datetime.now(service.timezone)
    today = now.date()
    tomorrow = (now + timedelta(days=1)).date()
    week_end = (now + timedelta(days=6 - now.weekday())).date()
    last = max(tomorrow, week_end)
    schedules = await service.schedule_for_days(today, last)
    if view == "today":
        targets = [today]
    elif view == "tomorrow":
        targets = [tomorrow]
    else:
        targets = [today + timedelta(days=offset) for offset in range((week_end - today).days + 1)]
    return schedule_payload(
        view,
        [(target, schedules.get(target, [])) for target in targets],
        generated_at=now.strftime("%Y-%m-%d %H:%M"),
    )


@driver.server_app.get("/asoul-live/", response_class=HTMLResponse)
async def asoul_live_web_page() -> HTMLResponse:
    return HTMLResponse(page_html())


@driver.server_app.get("/asoul-live/api/schedule")
async def asoul_live_web_schedule(view: str = "today") -> dict[str, Any]:
    payload = await _schedule_web_payload(view)
    return web_renderer.localize_schedule_stickers(payload)


@bili_status.handle()
async def _(event: MessageEvent):
    await _require_admin(bili_status, event)
    await bili_status.finish(service.monitor_status())


@bili_login.handle()
async def _(event: MessageEvent):
    await _require_admin(bili_login, event)
    if isinstance(event, GroupMessageEvent):
        await bili_login.finish("请在私聊中使用 #bili_login。")
    login = await service.create_qr_login()
    path = Path("data") / "asoul_bili_login_qrcode.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    login.get_qrcode_picture().to_file(str(path))
    await bili_login.send("请使用哔哩哔哩 App 扫描二维码登录。\n" + MessageSegment.image(file=path.resolve().as_uri()))
    if await service.wait_for_qr_login(login):
        await bili_login.finish("B 站登录成功，登录态已保存在本机数据库。")
    await bili_login.finish("二维码已过期或登录未完成，请重新执行 #bili_login。")


@bili_logout.handle()
async def _(event: MessageEvent):
    await _require_admin(bili_logout, event)
    service.clear_credential()
    await bili_logout.finish("已清除本机保存的 B 站登录态。")


@bili_test_dynamic.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(bili_test_dynamic, event)
    await _require_private_bili(bili_test_dynamic, event)
    await bili_test_dynamic.finish("B站动态查询已停用，避免高频请求触发风控。")


@bili_dump_dynamic.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(bili_dump_dynamic, event)
    await _require_private_bili(bili_dump_dynamic, event)
    await bili_dump_dynamic.finish("B站动态原始数据导出已停用，避免高频请求触发风控。")


@bili_test_video.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(bili_test_video, event)
    await _require_private_bili(bili_test_video, event)
    uid = _uid_or_usage(text_arg(args), "#bili_test_video UID")
    if uid is None:
        await bili_test_video.finish("用法：#bili_test_video UID")
    await _test_bili(bili_test_video, "video", uid)


@bili_test_live.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(bili_test_live, event)
    await _require_private_bili(bili_test_live, event)
    uid = _uid_or_usage(text_arg(args), "#bili_test_live UID")
    if uid is None:
        await bili_test_live.finish("用法：#bili_test_live UID")
    await _test_bili(bili_test_live, "live", uid)


@bili_dump_live.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(bili_dump_live, event)
    await _require_private_bili(bili_dump_live, event)
    uid = _uid_or_usage(text_arg(args), "#bili_dump_live UID")
    if uid is None:
        await bili_dump_live.finish("用法：#bili_dump_live UID")
    try:
        path = await service.dump_live_payload(uid)
    except Exception as exc:
        logger.warning("Bilibili live payload dump failed for uid %s: %s", uid, type(exc).__name__)
        await bili_dump_live.finish("B站直播原始数据导出失败，请稍后重试。")
    await bili_dump_live.finish(f"已导出直播原始数据：{path}")


@bili_test_atall.handle()
async def _(event: MessageEvent, bot: Bot):
    await _require_admin(bili_test_atall, event)
    if not isinstance(event, GroupMessageEvent):
        await bili_test_atall.finish("请在需要验证的群聊中使用 #bili_test_atall。")
    try:
        await call_qq_action(
            bot,
            "send_group_msg",
            group_id=event.group_id,
            message=MessageSegment.at("all") + " B站开播全体提醒测试",
        )
    except Exception as exc:
        logger.warning("Bilibili @all test failed for group %s: %s", event.group_id, type(exc).__name__)
        await bili_test_atall.finish("全体提醒发送失败。请确认机器人拥有本群管理员权限。")
    await bili_test_atall.finish("已发送 B站开播全体提醒测试。")


@bili_test_comment.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(bili_test_comment, event)
    await _require_private_bili(bili_test_comment, event)
    uid = _uid_or_usage(text_arg(args), "#bili_test_comment UID")
    if uid is None:
        await bili_test_comment.finish("用法：#bili_test_comment UID")
    if uid not in settings.asoul_bili_comment_target_uids:
        await bili_test_comment.finish("该 UID 不在评论推送的 5 个目标账号中。")
    try:
        rows = await _recent_comment_rows(uid)
    except Exception as exc:
        logger.warning("Bilibili comment test failed for uid %s: %s", uid, type(exc).__name__)
        await bili_test_comment.finish("评论区测试失败，请稍后重试。")
    if not rows:
        await bili_test_comment.finish("最新 6 小时动态的热门第一页中没有目标账号回复。")
    lines = ["【B站评论区测试】"]
    lines.extend(f"{row['author']}：{row['text']}" for row in rows[:5])
    await bili_test_comment.finish("\n".join(lines))


@bili_test_all.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await _require_admin(bili_test_all, event)
    await _require_private_bili(bili_test_all, event)
    uid = _uid_or_usage(text_arg(args), "#bili_test_all UID")
    if uid is None:
        await bili_test_all.finish("用法：#bili_test_all UID")
    replies = []
    replies.append("dynamic：已停用（不请求动态接口）")
    for kind in ("video", "live"):
        try:
            item = await {"dynamic": service.fetch_dynamic, "video": service.fetch_video, "live": service.fetch_live}[kind](uid)
        except Exception as exc:
            replies.append(f"{kind}：查询失败（{type(exc).__name__}）")
            continue
        replies.append(f"{kind}：{'可用，' + item['url'] if item else '未找到可用内容'}")
    if uid in settings.asoul_bili_comment_target_uids:
        try:
            comment_rows = await _recent_comment_rows(uid)
        except Exception as exc:
            replies.append(f"comment：查询失败（{type(exc).__name__}）")
        else:
            replies.append(f"comment：可用，热门第一页命中 {len(comment_rows)} 条目标账号回复")
    else:
        replies.append("comment：该 UID 不在评论目标范围")
    await bili_test_all.finish("【B站综合测试】\n" + "\n".join(replies))


@register_local_feature("today_live", "tomorrow_live", "week_live")
async def _run_local_live_feature(
    matcher: Any,
    bot: Bot,
    event: Any,
    request: FeatureRequest,
) -> None:
    del bot, event
    if request.action == "week_live":
        await finish_week_schedule_reply(matcher, request.args)
        return
    now = datetime.now(service.timezone)
    if request.action == "tomorrow_live":
        if now.weekday() == 6:
            await matcher.finish("还没有下周的直播排表哦")
        await finish_schedule_reply(matcher, "明日直播", now + timedelta(days=1), request.args)
        return
    await finish_schedule_reply(matcher, "今日直播", now, request.args)
