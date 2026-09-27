from __future__ import annotations

import asyncio
import re
import time
from dataclasses import replace
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import on_message, get_driver, get_bots, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.message import event_preprocessor

from bot.config import settings
from bot.application.personas import persona_engine
from bot.application.proactive_chat import ProactiveCoordinator, proactive_store
from bot.application.chat_continuation import (
    ContinuationCoordinator, continuation_config, refresh_continuation_quotas,
)
from bot.application.local_features import (
    FeatureRequest,
    FeatureDelivery,
    StaleFeatureState,
    request_from_decision,
    run_feature_call,
    registered_local_features,
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
    classify_local_feature,
    has_feature_hint,
    persona_rejection,
)
from bot.services.skills import local_action_skill
from bot.services.skill_audit import ledger as skill_ledger
from bot.services.skill_metrics import metrics as skill_metrics
from bot.services.skill_capability import capabilities
from bot.services.skill_security import role_allows
from bot.services.roles import is_super_admin
from bot.services.agent_plan import build_plan, needs_plan
from bot.services.local_skill_contract import ACTION_CONTRACTS, MAX_SKILL_CALLS, valid_request
from bot.services.tangtang_media import extract_image_references
from bot.services.agent_tools import ToolExecutionResult
from bot.services.action_state import STATE_ACTIONS, action_state_versions


PASSIVE_EVENT_MAX_AGE_SECONDS = 120
_GAME_CODE_RE = re.compile(r"^(?:gs|ys|ww|nte|yh)(?=$|\s|[\u4e00-\u9fff])", re.IGNORECASE)
_CODEX_COMMAND_RE = re.compile(r"^#\s*codex(?:\s|$)", re.IGNORECASE)
_TARGETED_READ_ACTIONS = frozenset({"archive_records", "archive_search", "archive_profile"})
_TARGETED_WRITE_ACTIONS = frozenset({"wife_take", "bomb_pass", "idiom_bomb_pass"})
_TARGETED_ACTIONS = _TARGETED_READ_ACTIONS | _TARGETED_WRITE_ACTIONS


def _resolve_request_target(event: Any, request: FeatureRequest) -> FeatureRequest:
    if request.action not in _TARGETED_ACTIONS:
        return request
    mode = request.parameter("target")
    if mode == "self":
        return replace(request, target_user_id=int(event.user_id))
    if mode != "mentioned":
        return request
    original = getattr(event, "original_message", getattr(event, "message", ()))
    targets: list[int] = []
    for segment in original:
        if segment.type != "at":
            continue
        value = str(segment.data.get("qq") or "")
        if value == str(getattr(event, "self_id", "")) or not value.isdigit():
            continue
        targets.append(int(value))
    if len(targets) != 1:
        return request
    return replace(request, target_user_id=targets[0])


def _feature_denial(event, request: FeatureRequest) -> str:
    if request.action not in ACTION_CONTRACTS:
        return "unknown_skill"
    if not valid_request(request):
        return "invalid_args"
    if getattr(event, "message_type", "") != "group" or not getattr(event, "group_id", None):
        return "group_required"
    if request.action in _TARGETED_ACTIONS and request.target_user_id is None:
        return "invalid_args"
    skill = local_action_skill(request.action)
    if skill is None or skill.deprecated or request.action not in registered_local_features():
        return "unknown_skill"
    group_id = int(event.group_id)
    if not skill_ledger.enabled_for_group(skill.skill_id, group_id):
        return "group_disabled"
    if (
        skill.feature_key
        and request.action != "user_help"
        and not group_domains().effective_feature_enabled(group_id, skill.feature_key)
    ):
        return "group_disabled"
    if request.action.startswith(("nte_", "wuwa_")) and (
        not settings.game_api_enabled or not passive_settings().is_game_api_enabled()
    ):
        return "group_disabled"
    if not role_allows(skill.required_role,
        is_admin=getattr(getattr(event, "sender", None), "role", "") in {"owner", "admin"},
        is_super_admin=is_super_admin(int(event.user_id))):
        return "permission"
    if request.cluster:
        domain = group_domains().domain_for_group(group_id)
        if domain is None or domain.mode != "cluster":
            return "cluster_required"
    if not capabilities.skill_available(skill.skill_id):
        return "upstream_unavailable"
    return ""


def _available_model_skills(event) -> tuple[str, ...]:
    def sample(action: str, args: str) -> FeatureRequest:
        parameters: dict[str, str | int] = {}
        if action in {"archive_records", "archive_profile"}:
            parameters = {"target": "self"}
            if action == "archive_records":
                parameters["page"] = 1
        elif action == "archive_search":
            parameters = {"target": "self", "keyword": "测试", "page": 1}
        elif action in {"nte_mint_rank", "wuwa_progress_rank"}:
            parameters = {"page": 1}
        elif action in {"wuwa_character_rank", "wuwa_echo_rank"}:
            parameters = {"character": "角色", "page": 1}
        elif action in {"wife_take", "bomb_pass"}:
            parameters = {"target": "mentioned"}
        elif action == "idiom_bomb_pass":
            parameters = {"target": "mentioned", "idiom": "画蛇添足"}
        elif action == "idiom_bomb_load":
            parameters = {"mode": "entertainment", "duration_seconds": 120}
        elif action == "guess_submit":
            parameters = {"value": 500}
        request = FeatureRequest.with_parameters(action, parameters, args=args)
        return _resolve_request_target(event, request)

    return tuple(
        action
        for action, contract in ACTION_CONTRACTS.items()
        if not _feature_denial(event, sample(action, contract.args[0]))
    )


async def _run_skill_requests(bot, event, config, requests, *, text="", opening="",
                              usage=None, source="feature_router", current=None,
                              execution_results=None, state_versions=None,
                              request_id="") -> bool:
    """One authorization and delivery path for local, planned and model calls."""
    current = current or service._turn_current
    state_versions = state_versions or {}
    requests = tuple(
        replace(
            _resolve_request_target(event, request),
            state_version=str(state_versions.get(request.action) or ""),
            execution_key=(
                f"{request_id}:{request.action}:{index}"
                if request.action in STATE_ACTIONS and request_id else ""
            ),
        )
        for index, request in enumerate(requests)
    )
    delivery = FeatureDelivery(bot, event, current=current)
    usage = usage or {}
    if not current():
        if execution_results is not None:
            execution_results.extend(
                ToolExecutionResult("stale", request.action, error_code="stale")
                for request in requests
            )
        return True
    # Validate the whole batch before emitting an opener or executing any step.
    if not 1 <= len(requests) <= MAX_SKILL_CALLS:
        await delivery.send(persona_rejection("invalid_args", call_keyword=config.call_keyword))
        if execution_results is not None:
            execution_results.extend(
                ToolExecutionResult("denied", request.action,
                                    tuple(delivery.message_ids), "direct_qq", "invalid_args")
                for request in requests
            )
        return True
    if sum(request.action in STATE_ACTIONS for request in requests) > 1:
        await delivery.send(persona_rejection("invalid_args", call_keyword=config.call_keyword))
        if execution_results is not None:
            execution_results.extend(
                ToolExecutionResult(
                    "denied", request.action, tuple(delivery.message_ids),
                    "direct_qq", "batch_state_conflict",
                )
                for request in requests
            )
        return True
    for request in requests:
        reason = _feature_denial(event, request)
        if request.action in STATE_ACTIONS:
            if source != "native_tool" or not _explicit_state_request(request.action, text):
                reason = "invalid_args"
            elif not request.execution_key or not request.state_version:
                reason = "stale_state"
            elif action_state_versions(db, int(event.group_id)).get(request.action) != request.state_version:
                reason = "stale_state"
        if not reason and request.args == "总" and not re.search(r"总|累计|历史|机器人|所有群|bot", text, re.I):
            reason = "invalid_args"
        if not reason and request.cluster:
            domain = group_domains().domain_for_group(int(event.group_id))
            labels = (domain.name, domain.alias) if domain else ()
            if "集群" not in text and not any(label and label in text for label in labels):
                reason = "invalid_args"
        if not reason and source == "native_tool" and not has_feature_hint(text):
            reason = "invalid_args"
        if reason:
            logger.info("Local skill rejected source={} action={} reason={}", source, request.action, reason)
            await delivery.send(persona_rejection(reason, call_keyword=config.call_keyword))
            if execution_results is not None:
                execution_results.extend(
                ToolExecutionResult(
                        "stale" if reason == "stale_state" else "denied",
                        item.action, tuple(delivery.message_ids), "direct_qq",
                        reason if item is request else "batch_denied",
                    )
                    for item in requests
                )
            return True
    if opening:
        await delivery.send(opening)
    for index, request in enumerate(requests):
        if not current():
            break
        # Switches/permissions may change while a previous image is rendering.
        reason = _feature_denial(event, request)
        if request.action in STATE_ACTIONS and (
            action_state_versions(db, int(event.group_id)).get(request.action)
            != request.state_version
        ):
            reason = "stale_state"
        if reason:
            await delivery.send(persona_rejection(reason, call_keyword=config.call_keyword))
            break
        skill = local_action_skill(request.action)
        started_at, before, before_ids, ok = (
            time.monotonic(), len(delivery.receipts), len(delivery.message_ids), False
        )
        error_code = ""
        claimed = False
        try:
            if request.action in STATE_ACTIONS:
                claimed = service.db.claim_action_execution(
                    request.execution_key,
                    action=request.action,
                    state_version=request.state_version,
                    now=service._now(),
                )
                if not claimed:
                    error_code = "duplicate_request"
                    await delivery.send(persona_rejection("duplicate_request", call_keyword=config.call_keyword))
                else:
                    handled = await run_feature_call(delivery, bot, event, request)
                    ok = handled and len(delivery.receipts) > before
                    if not ok and current():
                        await delivery.send(persona_rejection("failed", call_keyword=config.call_keyword))
                        error_code = "failed"
            else:
                handled = await run_feature_call(delivery, bot, event, request)
                ok = handled and len(delivery.receipts) > before
                if not ok and current():
                    await delivery.send(persona_rejection("failed", call_keyword=config.call_keyword))
                    error_code = "failed"
        except StaleFeatureState:
            error_code = "stale_state"
            if current():
                await delivery.send(persona_rejection("stale_state", call_keyword=config.call_keyword))
        except Exception as exc:
            error_code = type(exc).__name__
            skill_ledger.record_failure(skill_id=skill.skill_id,
                error=f"{type(exc).__name__}: {exc}", group_id=int(event.group_id),
                user_id=int(event.user_id), source=source)
            if current():
                await delivery.send(persona_rejection("failed", call_keyword=config.call_keyword))
        finally:
            if claimed:
                service.db.finish_action_execution(
                    request.execution_key,
                    status="delivered" if ok else ("stale" if error_code == "stale_state" else "failed"),
                    now=service._now(),
                )
            logger.info("Local skill finished source={} action={} group={} message={} delivered={}",
                source, request.action, event.group_id, getattr(event, "message_id", ""), ok)
            skill_metrics.record(skill_id=skill.skill_id, group_id=int(event.group_id),
                user_id=int(event.user_id), action=request.action, ok=ok,
                latency_ms=int((time.monotonic() - started_at) * 1000),
                prompt_tokens=int(usage.get("prompt_tokens") or 0) if index == 0 else 0,
                completion_tokens=int(usage.get("completion_tokens") or 0) if index == 0 else 0,
                reasoning_tokens=int(usage.get("reasoning_tokens") or 0) if index == 0 else 0,
                cost=float(usage.get("cost") or 0) if index == 0 else 0, source=source)
        if execution_results is not None:
            status = (
                "delivered" if ok else
                "stale" if error_code == "stale_state" or not current() else
                "denied" if error_code == "duplicate_request" else "failed"
            )
            execution_results.append(ToolExecutionResult(
                status,
                request.action,
                tuple(delivery.message_ids[before_ids:]),
                "direct_qq",
                error_code or ("stale" if status == "stale" else ""),
                {"delivered": ok},
            ))
        if len(delivery.receipts) > before:
            service.record_feature(group_id=int(event.group_id), user_id=int(event.user_id),
                message_id=getattr(event, "message_id", "") or "", call_text=text,
                reply_text="\n".join(delivery.receipts[before:]),
                provenance={"skill_id": skill.skill_id, "action": request.action,
                            "args": request.args,
                            "parameter_names": [key for key, _ in request.parameters],
                            "source": source})
    return True


def _explicit_state_request(action: str, text: str) -> bool:
    normalized = str(text)
    action_words = r"(?:抽|强取|离婚|解缘|装填|开枪|装弹|丢给|传给|开局|掷|摇|猜|提交)"
    if re.search(action_words, normalized) and re.search(r"(?:不要|别|不用|取消)", normalized):
        return False
    if re.search(action_words, normalized) and re.search(
        r"(?:历史|摘要|引用|转述|有人说|他说|她说|原话|这句话|那句话|消息里|[\"“”「」『』])",
        normalized,
    ):
        return False
    patterns = {
        "wife_draw": r"(?:抽|来一?个).*(?:今日)?(?:缘分|老婆)",
        "wife_take": r"强取",
        "wife_divorce": r"离婚|解缘",
        "roulette_load": r"(?:转盘|俄罗斯转盘|轮盘)?.*装填|装填.*(?:转盘|俄罗斯转盘|轮盘)",
        "roulette_fire": r"开枪",
        "bomb_load": r"装弹(?!.*成语)",
        "bomb_pass": r"丢给|传给",
        "idiom_bomb_load": r"装弹.*成语|成语.*装弹",
        "idiom_bomb_pass": r"丢给|传给",
        "dice_start": r"(?:骰子|骰局).*(?:开局|开始|来一?局|掷|摇)|(?:开局|开始|来一?局).*(?:骰子|骰局)",
        "guess_start": r"(?:猜数|猜数字).*(?:开局|开始|来一?局)|(?:开局|开始|来一?局).*(?:猜数|猜数字)",
        "guess_submit": r"(?:猜|提交).*(?:\d)",
    }
    return bool(re.search(patterns[action], normalized, re.IGNORECASE))


def _feature_state_versions(event: Any) -> dict[str, str]:
    if getattr(event, "message_type", "") != "group":
        return {}
    return action_state_versions(db, int(event.group_id))


async def _feature_router(
    bot: Bot, event: MessageEvent, config: TangtangConfig, text: str
) -> tuple[bool, dict[str, Any]]:
    if not has_feature_hint(text):
        return False, {}
    domain = group_domains().domain_for_group(int(event.group_id))
    labels = (domain.name, domain.alias) if domain and domain.mode == "cluster" else ()
    if needs_plan(text):
        plan = build_plan(text, cluster_labels=labels, call_keyword=config.call_keyword)
        # A partial deterministic plan must reach the full model, so unknown
        # segments are explained rather than silently dropped.
        if plan.unresolved or len(plan.steps) < 2:
            return False, {}
        requests = tuple(FeatureRequest(s.action, s.args, s.cluster) for s in plan.steps)
        return await _run_skill_requests(bot, event, config, requests, text=text,
            opening="唔，我按顺序看看。" if config.call_keyword != "糖糖" else "好呀，一个个来看。",
            source="agent_plan"), {}
    decision = classify_local_feature(text, cluster_labels=labels, call_keyword=config.call_keyword)
    if decision is None:
        # The main chat model sees the executable contracts too. Avoid a second
        # inference request whose failure previously lost all skill awareness.
        return False, {}
    return await _run_skill_requests(bot, event, config, (request_from_decision(decision),),
                                     text=text, opening=decision.line), {}


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


service = TangtangService(
    loader=loader,
    feature_router=_feature_router,
    group_identity_provider=_group_identity,
    persona_engine=persona_engine(),
    feature_runner=_run_skill_requests,
    feature_catalog=_available_model_skills,
    feature_state_provider=_feature_state_versions,
    blocked_users=db.blocked_user_ids,
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
    if db.interaction_blocked(int(event.group_id), int(event.user_id)):
        service.record_group_message(int(event.group_id), "", "",
            user_id=int(event.user_id), message_id=str(event.message_id))
        return
    engine = persona_engine()
    frozen = engine.snapshot(event, config.model, False)
    at_labels = await resolve_at_labels(bot, event, use_api=False)
    text = render_message_text(event.message, at_labels)
    media_references = extract_image_references(event, config.vision_max_images, include_reply=False)
    # Empty/emoji-only messages still occupy the immediately preceding position.
    if event.message is not None:
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
_continuation_quota_scheduler = AsyncIOScheduler(timezone=settings.timezone)


async def refresh_daily_continuation_quotas() -> None:
    bot = next(iter(get_bots().values()), None)
    if bot is not None:
        await refresh_continuation_quotas(bot)


@get_driver().on_bot_connect
async def schedule_continuation_quota_catchup(bot: Bot) -> None:
    # Date job keeps transport/API work off the connection and message handlers.
    _continuation_quota_scheduler.add_job(
        refresh_daily_continuation_quotas, "date", id="continuation-quota-catchup",
        replace_existing=True, max_instances=1, misfire_grace_time=60)


@get_driver().on_startup
async def start_proactive_timer() -> None:
    global _proactive_task
    _proactive_task = asyncio.create_task(proactive_coordinator.run())
    _continuation_quota_scheduler.add_job(
        refresh_daily_continuation_quotas, "cron", hour=0, minute=0,
        id="continuation-daily-quotas", replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=3600)
    _continuation_quota_scheduler.start()


@get_driver().on_shutdown
async def stop_proactive_timer() -> None:
    if _continuation_quota_scheduler.running:
        _continuation_quota_scheduler.shutdown(wait=False)
    await continuation_coordinator.close()
    if _proactive_task:
        _proactive_task.cancel()
        await asyncio.gather(_proactive_task, return_exceptions=True)
    proactive_coordinator.pending.clear()
