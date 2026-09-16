from __future__ import annotations

import re
import time
from typing import Any

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.message import event_postprocessor

from bot.config import settings
from bot.application.personas import persona_engine
from bot.application.local_features import (
    feature_label,
    request_from_decision,
    run_feature_call,
)
from bot.services.runtime import database, group_domains, passive_settings
from bot.services.chat_dispatch import dispatcher
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
from bot.services.tangtang_media import extract_image_references


PASSIVE_EVENT_MAX_AGE_SECONDS = 120
_GAME_CODE_RE = re.compile(r"^(?:gs|ys|ww|nte|yh)(?=$|\s|[\u4e00-\u9fff])", re.IGNORECASE)
_CODEX_COMMAND_RE = re.compile(r"^#\s*codex(?:\s|$)", re.IGNORECASE)


async def _feature_router(
    bot: Bot, event: MessageEvent, config: TangtangConfig, text: str
) -> tuple[bool, dict[str, Any]]:
    if not has_feature_hint(text):
        return False, {}
    current_domain = group_domains().domain_for_group(int(event.group_id))
    cluster_labels = (
        (current_domain.name, current_domain.alias)
        if current_domain is not None and current_domain.mode == "cluster"
        else ()
    )
    decision = classify_local_feature(text, cluster_labels=cluster_labels, call_keyword=config.call_keyword)
    if decision is None:
        profile = next(p for p in persona_engine().profiles.values() if p.call_keyword == config.call_keyword)
        decision, usage = await feature_classifier.classify(config, text, persona_name=profile.name)
    else:
        usage = {}
    if decision is None:
        return False, usage
    request = request_from_decision(decision)
    feature_key = {
        "ranking": "speech_ranking",
        "zhijiang_schedule": "zhijiang_calendar",
        "today_live": "zhijiang_calendar",
        "tomorrow_live": "zhijiang_calendar",
        "week_live": "zhijiang_calendar",
    }.get(request.action)
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
    return True


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
    dispatcher.submit(int(event.group_id), lambda: service.handle(bot, event, config, context=context),
        request_id=context.request_id, current=lambda: persona_engine().current(context))


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
    dispatcher.submit(int(event.group_id), lambda: service.handle_proactive(bot, event, config, context=context),
        proactive=True, request_id=context.request_id, current=lambda: persona_engine().current(context))


@event_postprocessor
async def _record_group_context(bot: Bot, event: MessageEvent):
    """Keep the latest group texts in memory for call-time atmosphere context."""

    if not isinstance(event, GroupMessageEvent):
        return
    config = runtime_config()
    if not config.enabled or int(event.group_id) not in group_domains().all_group_ids():
        return
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
        )
