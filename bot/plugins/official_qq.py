"""Minimal official QQ OpenAPI sandbox probe.

The official group API exposes application-scoped OpenIDs, not a member's QQ
number. This plugin deliberately validates the supported transport before any
legacy cross-group service is migrated to it.
"""

from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw, ImageFont
from nonebot import logger, on_command
from nonebot.adapters.qq import Bot, Event, Message, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule

from bot.config import settings
from bot.openapi import official_runtime
from bot.services.emoji_text import EmojiTextDraw
from bot.services.character_marks import draw_heart_tail, draw_stitched_mascot
from bot.services.image_style import paste_horizontal_gradient, transparent_rounded_corners


def _official_message(event: Event) -> bool:
    return isinstance(event, (GroupAtMessageCreateEvent, C2CMessageCreateEvent))


def _quote_message(event: Event, message: str | Message | MessageSegment) -> Message:
    """Use QQ OpenAPI's native message reference for one-response commands."""
    return MessageSegment.reference(event.id) + message


async def _send_reply(bot: Bot, event: Event, message: str | Message | MessageSegment) -> None:
    await bot.send(event, _quote_message(event, message))


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_corner_ribbons(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """Use the same quiet registration marks as the local report cards."""
    inset = 18
    draw.line((inset, 96, inset, inset, 96, inset), fill="#ff91b4", width=7, joint="curve")
    draw.line((width - inset - 42, inset, width - inset, inset, width - inset, inset + 26), fill="#f7c8d8", width=3, joint="curve")
    draw.line((inset, height - inset - 26, inset, height - inset, inset + 44, height - inset), fill="#f7c8d8", width=3, joint="curve")


def _draw_outer_frame(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """Let the registration marks own the upper and lower-left corners."""
    left, top, right, bottom = 18, 18, width - 18, height - 18
    radius, line_width = 26, 2
    outline = "#f5dce7"
    draw.rectangle((left, top, right, bottom), fill="#ffffff")
    draw.line((96, top, right - 42, top), fill=outline, width=line_width)
    draw.line((left, 96, left, bottom - 26), fill=outline, width=line_width)
    draw.line((left + 44, bottom, right - radius, bottom), fill=outline, width=line_width)
    draw.line((right, top + 26, right, bottom - radius), fill=outline, width=line_width)
    draw.arc((right - radius * 2, bottom - radius * 2, right, bottom), 0, 90, fill=outline, width=line_width)


def _draw_official_probe_card(path) -> object:
    width, height = 768, 620
    image = Image.new("RGB", (width, height), "#fffafd")
    draw = EmojiTextDraw(image)
    _draw_outer_frame(draw, width, height)
    _draw_corner_ribbons(draw, width, height)
    draw_heart_tail(draw, 78, height - 42)
    paste_horizontal_gradient(image, (52, 56, width - 52, 220), "#f3a2bd", "#fff5f9", radius=22)
    draw.rounded_rectangle((82, 107, 252, 139), radius=16, fill="#fffefe")
    draw.text((101, 113), "OFFICIAL QQ", font=_font(16, True), fill="#d65791")
    draw.text((82, 130), "官方 QQ 连通测试", font=_font(42, True), fill="#342a32")
    draw_stitched_mascot(draw, width - 118, 16, 50)
    draw.rounded_rectangle((52, 248, width - 52, 530), radius=18, fill="#fffefd", outline="#f0d9e4", width=1)
    draw.text((82, 284), "本图由本地 Pillow\n确定性绘制", font=_font(27, True), fill="#342a32", spacing=6)
    draw.text((82, 378), "未调用生成式 AI，\n也不会消耗模型 Token。", font=_font(21), fill="#907885", spacing=6)
    draw.rounded_rectangle((82, 466, width - 82, 512), radius=16, fill="#fce4f0")
    draw.text((108, 479), "收到图片即表示官方群消息接收和图片发送均正常。", font=_font(16), fill="#b43e78")
    transparent_rounded_corners(image).save(path, format="PNG")
    return path


def _probe_image() -> object:
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = settings.report_dir / f"official-qq-probe-{stamp}.png"
    return _draw_official_probe_card(path)
    image = Image.new("RGB", (1040, 520), "#fff4f8")
    draw = EmojiTextDraw(image)
    draw.rounded_rectangle((36, 32, 1004, 488), radius=28, fill="#fffdfd", outline="#efcbd8", width=3)
    draw.rounded_rectangle((36, 32, 1004, 154), radius=28, fill="#f6b4c9")
    draw.text((78, 70), "官方 QQ OpenAPI 沙箱连通性", font=_font(42, True), fill="#342b32")
    draw.text((80, 210), "本图由本地 Pillow 确定性绘制", font=_font(30), fill="#453741")
    draw.text((80, 270), "未调用生成式 AI，也不会消耗模型 Token", font=_font(26), fill="#8b6875")
    draw.rounded_rectangle((78, 350, 960, 430), radius=18, fill="#ffe7f0")
    draw.text((110, 374), "收到图片即表示官方群消息接收和图片发送均正常。", font=_font(24), fill="#9f315d")
    image.save(path, format="PNG")
    return path


async def _send_image(bot: Bot, event: Event) -> None:
    try:
        await _send_reply(bot, event, MessageSegment.file_image(_probe_image()))
    except Exception:
        logger.exception("Official QQ local image probe failed")
        await _send_reply(bot, event, "图片发送失败；请检查沙箱的群消息、文件上传和消息发送权限。")


official_status = on_command("官方状态", rule=Rule(_official_message), priority=5, block=True)


@official_status.handle()
async def _(bot: Bot, event: Event):
    runtime = official_runtime(settings)
    location = "群聊" if isinstance(event, GroupAtMessageCreateEvent) else "私聊"
    await _send_reply(
        bot,
        event,
        "官方 QQ OpenAPI 已连接\n"
        f"模式：{'沙箱' if runtime.sandbox else '正式'}\n"
        f"会话：{location}\n"
        "可测试：官方群 @ 消息、私聊消息、本地图片发送、群成员 OpenID 接口。\n"
        "尚未迁移：QQ 号查重、原 OneBot 统计、跨群活动。",
    )


official_image = on_command("官方图片测试", rule=Rule(_official_message), priority=5, block=True)


@official_image.handle()
async def _(bot: Bot, event: Event):
    await _send_image(bot, event)


official_identity = on_command("官方身份", rule=Rule(_official_message), priority=5, block=True)


@official_identity.handle()
async def _(bot: Bot, event: Event):
    if isinstance(event, GroupAtMessageCreateEvent):
        await _send_reply(bot, event, "为保护身份标识，请在与机器人的私聊中发送“官方身份”。")
        return
    await _send_reply(
        bot,
        event,
        "你的官方平台用户标识如下。它是 OpenID，不是 QQ 号；请勿把它用于公开名单：\n"
        f"{event.author.user_openid}",
    )


official_group = on_command("官方群身份", rule=Rule(_official_message), priority=5, block=True)


@official_group.handle()
async def _(bot: Bot, event: Event):
    if not isinstance(event, GroupAtMessageCreateEvent):
        await _send_reply(
            bot,
            event,
            "官方 QQ 群 transport 仅投递 @ 事件；请在 @机器人 后使用 #官方群身份。",
        )
        return
    await _send_reply(
        bot,
        event,
        "当前官方群标识（用于后续沙箱能力核验，不等同 QQ 群号）：\n"
        f"group_id={event.group_id}\n"
        f"group_openid={event.group_openid}",
    )


official_members = on_command("官方群成员测试", rule=Rule(_official_message), priority=5, block=True)


@official_members.handle()
async def _(bot: Bot, event: Event, args=CommandArg()):
    if not isinstance(event, GroupAtMessageCreateEvent):
        await _send_reply(bot, event, "该能力只能在官方群聊中测试。")
        return
    raw_limit = args.extract_plain_text().strip()
    limit = int(raw_limit) if raw_limit.isdigit() else 10
    limit = max(1, min(limit, 50))
    try:
        result = await bot.post_group_members(group_id=event.group_id, limit=limit)
    except Exception as exc:
        logger.warning("Official group member probe failed for group=%s: %s", event.group_id, exc)
        await _send_reply(bot, event, "官方群成员接口当前不可用或未获授权；已记录为迁移阻塞项。")
        return
    await _send_reply(
        bot,
        event,
        f"成员接口可调用：返回 {len(result.members)} 条，"
        f"下一页索引：{result.next_index or '无'}。\n"
        "该接口仅返回成员 OpenID 与入群时间，不返回 QQ 号、昵称或头像；"
        "因此原查重报告暂不迁移。",
    )
