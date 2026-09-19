from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from nonebot import on_message, get_driver, get_bots, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.exception import FinishedException
from nonebot.message import event_preprocessor

from bot.config import settings
from bot.application.personas import persona_engine
from bot.application.proactive_chat import ProactiveCoordinator, proactive_store
from bot.application.chat_continuation import ContinuationCoordinator, continuation_config
from bot.application.local_features import (
    FeatureRequest,
    feature_label,
    request_from_decision,
    run_feature_call,
)
from bot.services.runtime import database, group_domains, passive_settings
from bot.services.chat_dispatch import dispatcher
from bot.services.continuation_policy import continuation_turn
from bot.services.game_api_gate import GAME_COMMAND_RE
from bot.services.tangtang_chat import (
    TangtangConfig,
    TangtangGroupIdentity,
    TangtangService,
    render_message_text,
    resolve_at_labels,
)
from bot.services.tangtang_runtime import config_loader as loader
from bot.services.tangtang_features import (
    TangtangFeatureClassifier,
    classify_local_feature,
    has_feature_hint,
)
from bot.services.skills import local_action_skill
from bot.services.skill_audit import ledger as skill_ledger
from bot.services.agent_plan import build_plan, needs_plan
from bot.services.tangtang_media import extract_image_references


PASSIVE_EVENT_MAX_AGE_SECONDS = 120
_GAME_CODE_RE = re.compile(r"^(?:gs|ys|ww|nte|yh)(?=$|\s|[\u4e00-\u9fff])", re.IGNORECASE)
_CODEX_COMMAND_RE = re.compile(r"^#\s*codex(?:\s|$)", re.IGNORECASE)


async def _feature_router(
    bot: Bot, event: MessageEvent, config: TangtangConfig, text: str
) -> tuple[bool, dict[str, Any]]:
    if not has_feature_hint(text):
        return False, {}
    if needs_plan(text):
        plan = build_plan(text)
        allowed_steps = [
            step
            for step in plan.steps
            if skill_ledger.enabled_for_group(step.skill_id, int(event.group_id))
        ]
        if len(allowed_steps) >= 2 and len(allowed_steps) == len(plan.steps):
            for step in allowed_steps:
                request = FeatureRequest(
                    action=step.action,
                    args=step.args,
                    cluster=step.cluster,
                )
                try:
                    await run_feature_call(tangtang_call, bot, event, request)
                except FinishedException:
                    continue
            return True, {}
    current_domain = group_domains().domain_for_group(int(event.group_id))
    cluster_labels = (
        (current_domain.name, current_domain.alias)
        if current_domain is not None and current_domain.mode == "cluster"
        else ()
    )
    decision = classify_local_feature(text, cluster_labels=cluster_labels, call_keyword=config.call_keyword)
    if decision is None:
        # Continuation's model attempt is charged by the shared chat path.
        # Keep deterministic tool requests working without starting a second,
        # unmetered classifier model before that admission point.
        if continuation_turn() is not None:
            return False, {}
        profile = next(p for p in persona_engine().profiles.values() if p.call_keyword == config.call_keyword)
        decision, usage = await feature_classifier.classify(config, text, persona_name=profile.name)
    else:
        usage = {}
    if decision is None:
        return False, usage
    request = request_from_decision(decision)
    skill = local_action_skill(request.action)
    if skill is None:
        # The proposer returned an action outside the closed skill registry.
        await tangtang_call.send("这个功能目前没有对应的本地技能，不能凭空执行。")
        return True, usage
    if not skill_ledger.enabled_for_group(skill.skill_id, int(event.group_id)):
        await tangtang_call.send("这个技能当前没有对本群启用，暂时不能执行。")
        return True, usage
    feature_key = skill.feature_key or None
    if feature_key and not group_domains().effective_feature_enabled(
        int(event.group_id), feature_key
    ):
        return True, usage
    if decision.line:
        await tangtang_call.send(decision.line)
    handled = await run_feature_call(tangtang_call, bot, event, request)
    if not handled:
        return False, usage
    service.record_feature(
        group_id=int(event.group_id),
        user_id=int(event.user_id),
        message_id=getattr(event, "message_id", "") or "",
        call_text=text,
        reply_text=decision.line or f"已执行本地功能：{feature_label(request)}",
    )
    return True, usage


db = database()


def _group_identity(group_id: int) -> TangtangGroupIdentity | None:
    row = db.managed_group(int(group_id), include_disabled=True)
    if row is None:
        return None
    domains = group_domains()
    domain = domains.domain_for_group(int(group_id))
    stored_name = str(row["group_name"] or "").strip()
    if stored_name == str(int(group_id)):
        stored_name = ""
    return TangtangGroupIdentity(
        group_name=stored_name,
        alias=str(row["alias"] or "").strip(),
        domain_mode=domain.mode if domain is not None else "",
        domain_name=(
            domains.domain_display_name(domain)
            if domain is not None and domain.mode == "cluster"
            else ""
        ),
    )


feature_classifier = TangtangFeatureClassifier()
service = TangtangService(
    loader=loader,
    feature_router=_feature_router,
    group_identity_provider=_group_identity,
    persona_engine=persona_engine(),
)


def runtime_config() -> TangtangConfig:
    return loader.load().with_group_ids(group_domains().all_group_ids())


def _is_stale(event: GroupMessageEvent) -> bool:
    timestamp = int(getattr(event, "time", 0) or 0)
    return timestamp > 0 and time.time() - timestamp > PASSIVE_EVENT_MAX_AGE_SECONDS


def automation_is_paused() -> bool:
    try:
        return db.passive_settings().get("automation_pause_active", "false") == "true"
    except (AttributeError, OSError):
        return False


def is_call_event(event: MessageEvent) -> bool:
    """Called messages only: @ bot, or text containing the call keyword."""

    if not isinstance(event, GroupMessageEvent):
        return False
    config = runtime_config()
    if (
        not config.enabled
        or not passive_settings().is_chat_globally_enabled("mention_chat")
        or not group_domains().feature_enabled(int(event.group_id), "mention_chat")
    ):
        return False
    if _is_stale(event):
        return False
    original = getattr(event, "original_message", event.message)
    text = original.extract_plain_text().strip()
    mentioned = any(segment.type == "at" and str(segment.data.get("qq")) == str(event.self_id) for segment in original)
    if not text:
        return mentioned
    if text.startswith(settings.command_prefix) or _CODEX_COMMAND_RE.match(text):
        return False
    if _GAME_CODE_RE.match(text) or GAME_COMMAND_RE.match(text):
        return False
    if mentioned:
        return True
    keyword = persona_engine().profile(int(event.group_id)).call_keyword
    return bool(keyword) and keyword in text


def is_proactive_event(event: MessageEvent) -> bool:
    """Ordinary non-command group messages that may get a proactive reply."""

    if not isinstance(event, GroupMessageEvent):
        return False
    if automation_is_paused():
        return False
    config = runtime_config()
    if not config.enabled or not config.proactive_enabled:
        return False
    if not passive_settings().is_chat_globally_enabled("proactive_chat"):
        return False
    if not group_domains().feature_enabled(int(event.group_id), "proactive_chat"):
        return False
    if _is_stale(event):
        return False
    text = event.get_plaintext().strip()
    if not text:
        return False
    if text.startswith(settings.command_prefix) or _CODEX_COMMAND_RE.match(text):
        return False
    if _GAME_CODE_RE.match(text) or GAME_COMMAND_RE.match(text):
        return False
    if is_call_event(event):
        return False
    if is_continuation_event(event):
        return False
    return True


def continuation_enabled(group_id: int) -> bool:
    engine = persona_engine()
    return (not automation_is_paused() and engine.store.option("continuation_enabled", True)
            and engine.chat_enabled(group_id, False) and engine.chat_enabled(group_id, True))


def is_continuation_event(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent) or _is_stale(event) or is_call_event(event):
        return False
    text = event.get_plaintext().strip()
    if (not text or text.startswith((settings.command_prefix, "/", "!", "！"))
            or _CODEX_COMMAND_RE.match(text) or _GAME_CODE_RE.match(text) or GAME_COMMAND_RE.match(text)):
        return False
    return continuation_coordinator.eligible(event)


# Runs before the passive matcher but never blocks, so random emoji, random
# repeat and triple repeat keep working in parallel on the same message.
tangtang_call = on_message(rule=is_call_event, priority=-1, block=False)


@tangtang_call.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    if str(event.user_id) == str(bot.self_id):
        return
    if not passive_settings().is_chat_globally_enabled("mention_chat"):
        return
    if db.passive_filter_contains(int(event.user_id)):
        return
    config = runtime_config()
    context = persona_engine().snapshot(event, config.model, False)
    if continuation_coordinator.offer(bot, event, config, context, explicit=True):
        proactive_coordinator.pending.pop(int(event.group_id), None)


tangtang_continuation = on_message(rule=is_continuation_event, priority=-1, block=False)


@tangtang_continuation.handle()
async def _continue_conversation(bot: Bot, event: GroupMessageEvent):
    if str(event.user_id) == str(bot.self_id) or db.passive_filter_contains(int(event.user_id)):
        return
    config = runtime_config()
    context = persona_engine().snapshot(event, config.model, False)
    if continuation_coordinator.offer(bot, event, config, context, explicit=False):
        proactive_coordinator.pending.pop(int(event.group_id), None)


# Proactive replies also run before the passive matcher and never block it, so
# random emoji/repeat and triple repeat can still fire on the same message.
tangtang_proactive = on_message(rule=is_proactive_event, priority=-1, block=False)


@tangtang_proactive.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    if str(event.user_id) == str(bot.self_id):
        return
    if not passive_settings().is_chat_globally_enabled("proactive_chat"):
        return
    if db.passive_filter_contains(int(event.user_id)):
        return
    config = runtime_config()
    context = persona_engine().snapshot(event, config.model, True)
    if proactive_store().selection(int(event.group_id))[0] != "legacy":
        original = getattr(event, "original_message", event.message)
        if context.persona.call_keyword in event.get_plaintext() or any(
            segment.type == "at" and str(segment.data.get("qq")) == str(bot.self_id) for segment in original
        ):
            return
        proactive_coordinator.observe(bot, event, config)
        return
    if continuation_coordinator.has_active_group(int(event.group_id)):
        return
    dispatcher.submit(int(event.group_id), lambda: service.handle_proactive(bot, event, config, context=context),
        proactive=True, request_id=context.request_id, current=lambda: persona_engine().current(context))


@event_preprocessor
async def _record_group_context(bot: Bot, event: MessageEvent):
    try:
        await _capture_group_context(bot, event)
    except Exception as exc:
        # Optional memory capture must never cancel game or management matchers.
        logger.warning('Persona observation capture failed: {}', type(exc).__name__)


async def _capture_group_context(bot: Bot, event: MessageEvent):
    """Keep the latest group texts in memory for call-time atmosphere context."""

    if not isinstance(event, GroupMessageEvent):
        return
    config = runtime_config()
    if not config.enabled or int(event.group_id) not in group_domains().all_group_ids():
        return
    if str(event.user_id) == str(bot.self_id):
        return
    engine = persona_engine()
    frozen = engine.snapshot(event, config.model, False)
    at_labels = await resolve_at_labels(bot, event, use_api=False)
    text = render_message_text(event.message, at_labels)
    media_references = extract_image_references(event, config.vision_max_images)
    if text or media_references:
        sender = getattr(event, "sender", None)
        nickname = str(
            getattr(sender, "card", "") or getattr(sender, "nickname", "") or "群友"
        )
        service.record_group_message(
            int(event.group_id),
            nickname,
            text or "[图片]",
            user_id=int(event.user_id),
            message_id=str(getattr(event, "message_id", "") or ""),
            media_references=media_references,
            observation={
                'persona': frozen.persona.key,
                'route_version': f'{frozen.selection_revision}:{frozen.persona.version}',
                'occurred_at': float(getattr(event, 'time', time.time())),
                'received_at': time.time(),
                'reply_to': str(getattr(getattr(event, 'reply', None), 'message_id', '') or ''),
                'attribution': 'direct' if is_call_event(event) else 'ambient',
            } if (config.memory_enabled and engine.v2_enabled(frozen.persona.key)
                  and not _is_stale(event) and not db.passive_filter_contains(int(event.user_id))
                  and not text.startswith((settings.command_prefix, '/', '!', '！'))
                  and not _GAME_CODE_RE.match(text) and not GAME_COMMAND_RE.match(text)
                  and (engine.chat_enabled(int(event.group_id), False) or engine.chat_enabled(int(event.group_id), True))) else None,
        )


def scheduled_chat_enabled(group_id: int) -> bool:
    return (not automation_is_paused()
            and not continuation_coordinator.has_active_group(group_id)
            and persona_engine().chat_enabled(group_id, True))


continuation_coordinator = ContinuationCoordinator(
    service, dispatcher, persona_engine(), enabled=continuation_enabled,
    connected=lambda bot: get_bots().get(str(bot.self_id)) is bot,
    config=lambda: continuation_config(persona_engine().store))
service.turn_observer = continuation_coordinator.outcome


proactive_coordinator = ProactiveCoordinator(
    proactive_store(), service, dispatcher, persona_engine(), enabled=scheduled_chat_enabled,
    connected=lambda bot: get_bots().get(str(bot.self_id)) is bot,
    groups=lambda: tuple(group_domains().all_group_ids()))
_proactive_task = None


@get_driver().on_startup
async def start_proactive_timer() -> None:
    global _proactive_task
    _proactive_task = asyncio.create_task(proactive_coordinator.run())


@get_driver().on_shutdown
async def stop_proactive_timer() -> None:
    await continuation_coordinator.close()
    if _proactive_task:
        _proactive_task.cancel()
        await asyncio.gather(_proactive_task, return_exceptions=True)
    proactive_coordinator.pending.clear()
