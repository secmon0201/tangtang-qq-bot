"""Group-local persona commands and bounded background lifecycle."""
from __future__ import annotations

import asyncio
import time

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.params import CommandArg

from bot.application.personas import persona_engine
from bot.application.local_features import FeatureRequest, register_local_feature
from bot.application.speech_background import SpeechSupervisor
from bot.config import ROOT
from bot.integrations.speech_runtime import SpeechRuntime
from bot.services.qq_platform import QQPlatform, QQPlatformError
from bot.services.roles import is_super_admin
from bot.services.runtime import database
from bot.services.chat_dispatch import dispatcher


persona_command = on_command("人格", priority=3, block=True)


async def can_manage_persona(bot, event) -> bool:
    if is_super_admin(int(event.user_id)):
        return True
    try:
        member = await QQPlatform(bot).member_info(int(event.group_id), int(event.user_id))
    except QQPlatformError:
        return False
    return member.get("role") in {"owner", "admin"}


@persona_command.handle()
async def handle_persona(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    engine = persona_engine()
    group_id = int(event.group_id)
    profile = engine.profile(group_id)
    tokens = args.extract_plain_text().split()
    if tokens in (["印象"], ["我的印象"]):
        await persona_command.finish(engine.personal_impression(group_id, int(event.user_id)))
    if not tokens or tokens == ["状态"]:
        voice = engine.speech.status(profile.key, group_id, group_enabled=engine.feature_enabled(group_id, "persona_voice"))
        await persona_command.finish(f"当前人格：{profile.name}\n呼叫：{profile.call_keyword} 或 @机器人\n语音：{voice}")
    if not await can_manage_persona(bot, event):
        await persona_command.finish("只有本群群主、群管理员或超级管理员可以管理人格。")
    scope_group = group_id
    if tokens[:2] == ['成长', '全局']:
        if not is_super_admin(int(event.user_id)):
            await persona_command.finish('全局成长管理仅限超级管理员。')
        scope_group = 0
        tokens = ['成长', *tokens[2:]]
    if len(tokens) == 2 and tokens[0] == "切换":
        if engine.locked_persona is not None:
            await persona_command.finish("聊天人格已固定为达妮娅，糖糖不再参与对话。")
        target = next((p for p in engine.profiles.values() if p.name == tokens[1]), None)
        if target is None:
            await persona_command.finish("聊天人格已固定为达妮娅。")
        engine.store.switch(group_id, target.key)
        database().audit(int(event.user_id), "persona_switch", group_id, target.key)
        await persona_command.finish(f"本群已切换为{target.name}。叫“{target.call_keyword}”或 @机器人即可。")
    if tokens == ["成长", "列表"]:
        entries = engine.growth.entries(profile.key, scope_group)
        rows = [f"{r['id']} · {'跨群' if r.get('shared') else '本群'} · v{r['version']} · {'启用' if r['enabled'] else '停用'} · {r['topic']}：{r['content']}" for r in entries]
        await persona_command.finish("本群公开成长记录：\n" + ("\n".join(rows) if rows else "暂无"))
    if tokens == ["成长", "诊断"]:
        if not is_super_admin(int(event.user_id)):
            await persona_command.finish("成长诊断仅限超级管理员。")
        from bot.services.persona_growth_diagnostics import diagnostic_text
        await persona_command.finish(diagnostic_text(engine.store, profile.key, scope_group))
    if len(tokens) == 3 and tokens[:2] == ["成长", "停用"] and tokens[2].isdigit():
        changed = engine.growth.disable(profile.key, scope_group, int(tokens[2]))
        database().audit(int(event.user_id), "persona_growth_disable", group_id, tokens[2])
        await persona_command.finish("已停用。" if changed else "没有本群当前人格的这条记录。")
    if len(tokens) == 4 and tokens[:2] == ["成长", "回退"] and all(t.isdigit() for t in tokens[2:]):
        if not is_super_admin(int(event.user_id)):
            await persona_command.finish("成长回退仅限超级管理员。")
        changed = engine.growth.rollback(profile.key, scope_group, int(tokens[2]), int(tokens[3]), time.time())
        database().audit(int(event.user_id), "persona_growth_rollback", group_id, " ".join(tokens[2:]))
        await persona_command.finish("已回退并保留版本历史。" if changed else "没有本群当前人格的这个版本。")
    await persona_command.finish("用法：#人格 状态 / #人格 印象 / #人格 成长 列表|停用 <编号>|回退 <编号> <版本>")


@register_local_feature("persona_status", "persona_impression")
async def _run_local_persona_read(
    matcher: object,
    bot: Bot,
    event: GroupMessageEvent,
    request: FeatureRequest,
) -> None:
    del bot
    engine = persona_engine()
    group_id = int(event.group_id)
    if request.action == "persona_impression":
        await matcher.finish(engine.personal_impression(group_id, int(event.user_id)))
    profile = engine.profile(group_id)
    voice = engine.speech.status(
        profile.key,
        group_id,
        group_enabled=engine.feature_enabled(group_id, "persona_voice"),
    )
    await matcher.finish(
        f"当前人格：{profile.name}\n呼叫：{profile.call_keyword} 或 @机器人\n语音：{voice}"
    )


_background = None
_speech_background = None
_memory_background = None
_group_summary_background = None


@get_driver().on_startup
async def start_persona_background() -> None:
    from bot.application.persona_background import start_background
    from bot.application.persona_observer import run_personal_memory
    from bot.application.group_summary_worker import run_group_summaries
    global _background, _speech_background, _memory_background
    global _group_summary_background
    _background = asyncio.create_task(start_background())
    _memory_background = asyncio.create_task(run_personal_memory())
    _group_summary_background = asyncio.create_task(run_group_summaries())
    _speech_background = asyncio.create_task(SpeechSupervisor(ROOT, persona_engine().speech, SpeechRuntime(ROOT)).run())


@get_driver().on_shutdown
async def stop_persona_background() -> None:
    await dispatcher.close()
    if _memory_background:
        _memory_background.cancel()
        await asyncio.gather(_memory_background, return_exceptions=True)
    if _background:
        _background.cancel()
        await asyncio.gather(_background, return_exceptions=True)
    if _group_summary_background:
        _group_summary_background.cancel()
        await asyncio.gather(_group_summary_background, return_exceptions=True)
    if _speech_background:
        _speech_background.cancel()
        await asyncio.gather(_speech_background, return_exceptions=True)
    await persona_engine().speech.close()
