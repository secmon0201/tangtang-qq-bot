"""Harness owns event routing, task lifetimes and confirmed QQ delivery."""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from .business.avatars import AvatarService
from .chat import ChatService
from .log_context import append_cache_delivery, append_cache_delivery_by_request
from .chat_settings import parse_chat_settings, chat_settings_status
from .config import HarnessConfig, save_config
from .core_protocol import game_prefix
from .continuation_policy import ContinuationConfig, ContinuationStore, ConversationWindow
from .external import CoreBridge, SpeechClient
from .speech_runtime import SpeechRuntimeManager
from .onebot import MessageSegment, NativeBot, parse_event, wire_message
from .message_text import VOICE_MARKER, message_text, normalize_voice
from .proactive_policy import TrafficState, decide, ordinary_text
from .reply_media import ExpressionSelector, expression_candidates, voice_decision
from .router import Router
from .store import Store
from .tools import ToolExecutor
from .topics import PersonaTopics, collect_topics
from .types import InboundEvent, ToolCall, ToolResult


class Runtime:
    def __init__(self, config: HarnessConfig, *, bot=None, model_client=None):
        self.config = config
        self.store = Store(config.root)
        self.bot = bot or NativeBot()
        self.chat = ChatService(config, self.store, model_client=model_client)
        self.router = Router()
        self.tools = ToolExecutor(self.bot, config.root, self.store)
        if isinstance(self.bot, NativeBot):
            self.bot.can_write = lambda: self.config.mode == "live"
        self.core = CoreBridge(self)
        self.speech = SpeechClient(config.root)
        self.expressions = ExpressionSelector(config.root, self.store, config.persona)
        self.speech_runtime = SpeechRuntimeManager(config.root)
        self.topics = PersonaTopics(self.store)
        self.core_sources: dict[str, InboundEvent] = {}
        self.started_at = time.time()
        self.tasks: set[asyncio.Task] = set()
        self.subscribers: set[asyncio.Queue] = set()
        self.session_locks: dict[str, asyncio.Lock] = {}
        self.bursts: dict[tuple[str, int], dict] = {}
        self.windows: dict[str, ConversationWindow] = {}
        self.continuation = ContinuationStore(config.root / "data" / "continuation.db")
        self.traffic: dict[int, TrafficState] = {}
        self.last_group_event: dict[int, InboundEvent] = {}
        self._last_group_membership_sync = 0.0
        # Direct/offline service fixtures do not run transport lifecycle.
        # Production start and every OneBot connection close this gate before
        # any worker is spawned or the socket can dispatch group deliveries.
        self._group_membership_ready = True
        self._group_membership_generation = 0
        self._group_membership_lock = asyncio.Lock()
        self._current_group_membership: list[dict] = []
        self.closing = False

    def publish(self, kind: str, value: dict):
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait({"type": kind, "at": time.time(), **value})

    def spawn(self, coroutine, name="harness-task"):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.add(task)
        def completed(done):
            self.tasks.discard(done)
            if not done.cancelled() and done.exception():
                logger.error("{} failed: {}", done.get_name(), done.exception())
                self.publish("failure", {"task": done.get_name(), "error": str(done.exception())})
        task.add_done_callback(completed)
        return task

    async def start(self):
        self._pause_group_membership()
        self.store.finish_interrupted_work()
        self.continuation.recover_interrupted_refreshes()
        self.store.set_setting("runtime_live", self.config.mode == "live")
        self.core.start()
        self.spawn(self._scheduler(), "harness-scheduler")
        self.spawn(self._external_worker(), "harness-external")
        self.spawn(self._speech_worker(), "harness-speech")
        self.spawn(self._topics_worker(), "harness-topics")
        self.spawn(self._ai_worker(), "harness-ai-worker")

    async def close(self):
        self.closing = True
        self.store.set_setting("runtime_live", False)
        await self.core.close()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(self.speech_runtime.stop, disable=False)
        self.bot.detach()
        if hasattr(self.chat, "close"):
            await self.chat.close()
        if hasattr(self.tools, "close"):
            result = self.tools.close()
            if hasattr(result, "__await__"):
                await result

    def update_config(self, config: HarnessConfig):
        save_config(config)
        self.config = config
        self.chat.config = config
        self.store.set_setting("runtime_live", config.mode == "live")
        self.publish("settings", {"mode": config.mode})

    def transport_connected(self):
        self._pause_group_membership()
        with self.store.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS transport_incidents(id INTEGER PRIMARY KEY, started_at REAL, ended_at REAL, reason TEXT)")
            conn.execute("UPDATE transport_incidents SET ended_at=? WHERE ended_at IS NULL", (time.time(),))
        self.publish("transport", {"connected": True})
        if self.config.mode == "live":
            self.spawn(self._sync_group_membership(), "harness-group-membership")

    def transport_disconnected(self, reason="connection_closed"):
        self._pause_group_membership()
        with self.store.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS transport_incidents(id INTEGER PRIMARY KEY, started_at REAL, ended_at REAL, reason TEXT)")
            conn.execute("INSERT INTO transport_incidents(started_at,reason) VALUES(?,?)", (time.time(), reason))
        self.publish("transport", {"connected": False})

    def _pause_group_membership(self):
        self._last_group_membership_sync = 0.0
        self._group_membership_ready = False
        self._group_membership_generation += 1

    def group_delivery_allowed(self, group_id: int | None) -> bool:
        return group_id is None or (self._group_membership_ready and self.store.group_present(group_id))

    async def _sync_group_membership(self):
        async with self._group_membership_lock:
            if self._group_membership_ready and time.monotonic() - self._last_group_membership_sync < 60:
                return self._current_group_membership
            generation = self._group_membership_generation
            groups = await asyncio.wait_for(self.bot.call_api("get_group_list"), timeout=10)
            if generation != self._group_membership_generation or not self.bot.connected:
                raise RuntimeError("QQ 连接已变化；旧群列表未用于恢复群任务")
            self._reconcile_group_membership(groups)
            self._current_group_membership = groups
            self._last_group_membership_sync = time.monotonic()
            self._group_membership_ready = True
            self.publish("group_membership", {"ready": True})
            return groups

    def in_scope(self, event: InboundEvent):
        if self.config.mode != "live":
            return False
        if event.group_id is None:
            private_users = self.config.extra.get("private_user_ids", [])
            return bool(self.config.private_chat_enabled
                        and (not private_users or event.user_id in private_users))
        # A group that was explicitly archived after the bot left must stay
        # out of the event path.  Unknown groups remain eligible for the
        # configured test-prefix flow and can be registered by collect().
        historical = self.tools.db.managed_group(event.group_id, include_disabled=True)
        if historical is not None and not bool(historical["enabled"]):
            return False
        prefix = self.config.extra.get("test_prefix", "#harness")
        if prefix and event.text.lstrip().startswith(prefix):
            return True
        return bool(self.config.extra.get("isolated_scope_enabled", False)
                    and event.group_id in self.config.group_ids)

    def accepts_background(self, group_id: int):
        return (self.config.mode == "live" and self.config.extra.get("isolated_scope_enabled", False)
                and group_id in self.config.group_ids and self.bot.connected
                and self.group_delivery_allowed(group_id))

    async def receive(self, packet: dict):
        if "echo" in packet and "post_type" not in packet:
            self.bot.receive_response(packet)
            return
        if packet.get("post_type") not in {"message", "message_sent"}:
            self._handle_platform_notice(packet)
            self.publish("platform", {"event": packet.get("meta_event_type") or packet.get("notice_type", "event")})
            return
        event = parse_event(packet)
        if event.group_id is not None:
            mention_names = {
                str(segment.get("data", {}).get("qq", "")): self.tools.db.group_member_name(
                    event.group_id, int(segment["data"]["qq"])
                )
                for segment in event.segments
                if segment.get("type") == "at"
                and str(segment.get("data", {}).get("qq", "")).isdigit()
            }
            if any(mention_names.values()):
                event = replace(event, text=message_text(event.segments, mention_names))
        if not self.store.append_event(event):
            return {"status": "duplicate"}
        # A group-decrease notice archives the group.  Retained/stale packets
        # can still arrive after that notice; keep their raw event for audit,
        # but prevent collection, routing, passive actions, and model work.
        if event.group_id is not None:
            managed = self.tools.db.managed_group(event.group_id, include_disabled=True)
            if managed is not None and not bool(managed["enabled"]):
                self.store.set_setting("event_scope:" + event.key, {"chat_allowed": False, "kind": "group_not_present"})
                self.publish("event", {"session_key": event.session_key, "event_id": event.event_id, "ignored": True})
                return {"status": "ignored_group_not_present"}
        active = self.in_scope(event)
        self.store.set_setting("event_scope:" + event.key, {"chat_allowed": False, "kind": "observed"})
        # Statistics consume filtered messages too; passive writes are delivered only in scope.
        passive = self.tools.collect(event, passive=active)
        if hasattr(passive, "__await__"):
            passive = await passive
        self.publish("event", {"session_key": event.session_key, "event_id": event.event_id})
        if packet.get("post_type") == "message_sent" or event.user_id == event.self_id:
            return {"status": "recorded_outgoing"}
        if not active:
            return {"status": "observed", "model_calls": 0, "qq_writes": 0}
        prefix = self.config.extra.get("test_prefix", "#harness")
        if prefix and event.text.lstrip().startswith(prefix):
            text = event.text.lstrip()[len(prefix):].strip()
            segments = tuple(s for s in event.segments if s.get("type") != "text")
            event = replace(event, text=text, segments=({"type": "text", "data": {"text": text}}, *segments))
            explicit = True
        else:
            explicit = event.group_id is None or self.config.call_keyword in event.text or (
                self.config.mention_chat_enabled and any(s.get("type") == "at" and str(s.get("data", {}).get("qq")) == str(event.self_id) for s in event.segments))
        if self._filtered(event):
            self.store.set_setting("event_scope:" + event.key, {"chat_allowed": False, "kind": "filtered"})
            return {"status": "filtered"}
        route_started = time.perf_counter()
        decision = self.router.route(event)
        route_ms = round((time.perf_counter() - route_started) * 1000, 3)
        self.publish("route", {"session_key": event.session_key, "kind": decision.kind})
        command = event.text.lstrip().startswith('#') or bool(game_prefix(event.text)) or bool(re.match(r'^[\u4e00-\u9fff]{4}\s*#\s*丢给', event.text))
        if decision.kind in {"tool", "mixed", "clarification"} and (explicit or command):
            self.store.set_setting("event_scope:" + event.key, {"chat_allowed": False, "kind": "tool", "route_ms": route_ms})
            self.spawn(self.process(event, decision), "harness-tool:" + event.session_key)
            return {"status": "accepted", "route": decision.kind}
        if decision.kind in {"ignore", "none"}:
            return {"status": "ignored"}
        if not self.chat_allowed(event):
            if passive:
                self.spawn(self.deliver_tool(event, passive), "harness-passive")
            return {"status": "chat_disabled"}
        if not explicit and not self._continuation_eligible(event):
            if passive:
                self.spawn(self.deliver_tool(event, passive), "harness-passive")
            if event.group_id is not None:
                source = ordinary_text(event.text)
                if source:
                    state = self.traffic.setdefault(event.group_id, TrafficState())
                    state.observe(event.user_id, event.event_id, source, event.timestamp or time.time())
                    self.last_group_event[event.group_id] = event
                    self.store.set_setting("event_scope:" + event.key, {"chat_allowed": True, "kind": "group_context", "route_ms": route_ms})
            return {"status": "context_only"}
        self.store.set_setting("event_scope:" + event.key, {"chat_allowed": True, "kind": "chat", "route_ms": route_ms})
        self._offer_burst(event, explicit)
        return {"status": "accepted", "route": "chat"}

    def _handle_platform_notice(self, packet: dict) -> None:
        """Archive groups as soon as OneBot reports that this bot left them."""
        if packet.get("post_type") != "notice":
            return
        try:
            group_id = int(packet.get("group_id"))
            user_id = int(packet.get("user_id"))
            self_id = int(packet.get("self_id") or self.bot.self_id)
        except (TypeError, ValueError):
            return
        if group_id <= 0 or user_id != self_id:
            return
        notice = packet.get("notice_type")
        if notice == "group_decrease":
            if self.tools.db.managed_group(group_id, include_disabled=True) is None:
                return
            self.tools.domains.disable_group(group_id)
            self.publish("group_membership", {"group_id": group_id, "enabled": False, "reason": "bot_left"})
        elif notice == "group_increase":
            self.tools.domains.ensure_group(group_id, group_name=str(packet.get("group_name") or ""),
                                            joined_at=datetime.now().astimezone().isoformat(), reactivate=True)
            self.publish("group_membership", {"group_id": group_id, "enabled": True, "reason": "bot_joined"})

    def _filtered(self, event):
        return self.tools.blocked(event)

    def game_allowed(self, event):
        feature = game_prefix(event.text)
        return bool(feature and event.group_id is not None and not self._filtered(event)
                    and self.config.mode == "live" and self.config.extra.get("core", {}).get("enabled", False)
                    and self.store.get_setting("game_api_enabled", True)
                    and self.tools.domains.effective_feature_enabled(event.group_id, feature))

    def chat_allowed(self, event, purpose="chat"):
        kind = "proactive_chat" if purpose == "proactive" else "mention_chat"
        enabled = self.config.proactive_enabled if purpose == "proactive" else self.config.mention_chat_enabled
        saved = self.tools.db.passive_settings().get(kind + "_global_enabled", "true")
        global_enabled = str(saved).casefold() not in {"false", "0", "off"}
        if not enabled or not global_enabled:
            return False
        if event.group_id is None:
            return purpose != "proactive" and self.config.private_chat_enabled
        return self.tools.domains.feature_enabled(event.group_id, kind)

    def _continuation_eligible(self, event):
        if not self.config.continuation_enabled or event.group_id is None:
            return False
        if any(s.get("type") == "at" and str(s.get("data", {}).get("qq")) != str(event.self_id) for s in event.segments):
            return False
        window = self.windows.get(event.session_key)
        return bool(window and window.context == event.user_id and window.current(time.time(), self._continuation_config())
                    and window.attempts < self._continuation_config().max_attempts)

    def _continuation_config(self):
        values = self.config.extra.get("continuation", {})
        return ContinuationConfig(**{k: v for k, v in values.items() if k in ContinuationConfig.__dataclass_fields__})

    def _offer_burst(self, event, explicit):
        key, now = (event.session_key, event.user_id), time.time()
        current = self.bursts.get(key)
        if current:
            current["events"].append(event)
            current["last"] = now
            current["explicit"] |= explicit
            return
        burst = {"events": [event], "first": now, "last": now, "explicit": explicit}
        self.bursts[key] = burst
        self.spawn(self._flush_burst(key, burst), "harness-merge:" + event.session_key)

    async def _flush_burst(self, key, burst):
        limits = self._continuation_config()
        try:
            while True:
                wait = min(burst["last"] + limits.debounce_seconds, burst["first"] + limits.max_debounce_seconds) - time.time()
                if wait <= 0:
                    break
                await asyncio.sleep(wait)
            events = burst["events"]
            self.bursts.pop(key, None)
            event = replace(events[-1], text="\n".join(e.text for e in events),
                            segments=tuple(s for e in events for s in e.segments))
            self.store.set_setting("merge:" + event.key, {"source_ids": [e.event_id for e in events], "wait_ms": (time.time() - burst["first"]) * 1000})
            if not burst["explicit"]:
                if not self._continuation_eligible(event) or not self.continuation.claim(event.group_id, event.key, time.time()):
                    return
                self.windows[event.session_key].attempts += 1
            await self.process(event, self.router.route(event), continuation=not burst["explicit"])
        finally:
            if self.bursts.get(key) is burst:
                self.bursts.pop(key, None)

    async def process(self, event, decision, *, continuation=False, purpose="chat"):
        async with self.session_locks.setdefault(event.session_key, asyncio.Lock()):
            if not self.store.group_present(event.group_id):
                return
            if not self.in_scope(event):
                # Stripped test messages were already admitted; remember that explicitly.
                if not self.store.get_setting("event_scope:" + event.key, {}).get("kind") in {"chat", "tool"} or self.config.mode != "live":
                    return
            if self._filtered(event):
                return
            if decision.kind == "clarification":
                await self.deliver(event, decision.reason or "请补充工具的目标或参数。")
                return
            facts = []
            for call in decision.tools:
                try:
                    result = await self.execute_call(event, call)
                except Exception as exc:
                    result = ToolResult('error', '', {'error': str(exc)})
                self.store.add_tool_result(event, call, result)
                await self.deliver_tool(event, result)
                facts.append({"tool": call.name, "status": result.status, "data": result.data.get("facts", result.data)})
            if decision.tools and (decision.kind != "mixed" or any(
                    fact["status"] in {"error", "failed", "timeout"} for fact in facts)):
                return
            if not self.chat_allowed(event, purpose):
                return
            local_reply = self._local_chat_reply(event)
            if local_reply:
                if isinstance(local_reply, str):
                    candidates = expression_candidates(self.config.root, local_reply, self.config.persona)
                    safe = [row['id'] for row in candidates if row.get('group') in {'gentle_smile', 'greeting'}][:3]
                    ids, sent = await self._deliver_reply(event, [local_reply], {'voice': 'auto', 'expression_candidates': safe})
                else:
                    ids, sent = await self.deliver(event, local_reply), [str(local_reply)]
                self.store.confirm_turn(event, sent, message_ids=ids)
                return
            if decision.kind == 'mixed':
                scope = self.store.get_setting('event_scope:' + event.key, {})
                self.store.set_setting('event_scope:' + event.key, {**scope, 'chat_allowed': True})
            event = await self.resolve_quote(event)
            if not facts:
                facts = await self._knowledge_facts(event)
            response = None
            try:
                try:
                    response = await self.chat.respond(event, tool_facts=facts or None,
                        purpose='continuation' if continuation else purpose)
                except (ValueError, RuntimeError) as exc:
                    logger.error('Chat preparation failed: {}', exc)
                    self.publish('request', {'status': 'failed', 'event_key': event.key, 'error': str(exc)})
                    return
                if response.status == "failed":
                    append_cache_delivery(self.store, response, event, [], status='failed')
                    logger.error('Model request {} failed: {}', response.request_id, response.error)
                    self.publish('request', {'request_id': response.request_id, 'status': 'failed', 'error': response.error})
                    if continuation:
                        self.continuation.outcome(event.key, 'failed')
                    return
                if response.status == "silent" or not response.messages:
                    append_cache_delivery(self.store, response, event, [], status=response.status)
                    if continuation and event.session_key in self.windows:
                        self.windows[event.session_key].silences += 1
                        self.continuation.outcome(event.key, "silent")
                    self.publish("request", {"request_id": response.request_id, "status": response.status})
                    return
                if not self.chat_allowed(event, purpose) or continuation and not self.config.continuation_enabled:
                    append_cache_delivery(self.store, response, event, [], status='disabled')
                    self.publish('request', {'request_id': response.request_id, 'status': 'disabled'})
                    return
                try:
                    ids, sent = await self._deliver_reply(event, response.messages, response.metadata,
                        request_id=response.request_id, user_content=response.user_content)
                    response.messages = sent
                    self.store.confirm_response(response, event, ids)
                    append_cache_delivery(self.store, response, event, sent)
                    self.chat.complete_context_window(event.session_key, response.snapshot_revision)
                    now = time.time()
                    if event.group_id is not None and self.config.continuation_enabled:
                        window = self.windows.get(event.session_key) if continuation else None
                        if window:
                            window.delivered_at, window.silences = now, 0
                        else:
                            self.windows[event.session_key] = ConversationWindow(event.user_id, now, now)
                    if continuation:
                        self.continuation.outcome(event.key, "delivered")
                    if hasattr(self.chat, "after_delivery"):
                        await self.chat.after_delivery(event, response)
                except asyncio.CancelledError:
                    self._record_interrupted_reply(event, response.request_id,
                        response.user_content, status='cancelled')
                    raise
                except Exception as exc:
                    self._record_interrupted_reply(event, response.request_id,
                        response.user_content, status='failed')
                    logger.error('Reply delivery {} failed: {}', response.request_id, exc)
                    self.publish('delivery', {'request_id': response.request_id, 'status': 'failed', 'error': str(exc)})
                    raise
                self.publish("request", {"request_id": response.request_id, "status": "delivered"})
            finally:
                # Success, scope changes, transport errors and cancellation all
                # release the attempt. Confirmed QQ turns remain in Store.
                self.chat.cancel_context_window(event.session_key)

    def _record_interrupted_reply(self, event, request_id, user_content, *, status):
        """Recover actual receipts even if cancellation interrupts local bookkeeping."""
        if self.store.delivered(request_id, event.key):
            return
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT messages,message_ids FROM deliveries WHERE request_id=? AND event_key=? "
                "AND outcome IN ('delivered','partial') ORDER BY id", (request_id, event.key)).fetchall()
        sent, ids, seen = [], [], set()
        for row in rows:
            fresh = [str(value) for value in json.loads(row['message_ids']) if str(value) not in seen]
            if fresh:
                ids.extend(fresh)
                seen.update(fresh)
                sent.extend(json.loads(row['messages']))
        if ids:
            request = self.store.request(request_id)
            cursor = (request or {}).get('telemetry', {}).get('context_cursor')
            self.store.confirm_turn(event, sent, request_id, ids, user_content, context_cursor=cursor)
            status = 'partial'
        append_cache_delivery_by_request(self.store, request_id, event, sent, status=status)

    async def _deliver_reply(self, event, messages, metadata, *, request_id='', user_content=None):
        ids, sent = [], []
        now = time.time()
        cooldown = self.config.extra.get('speech_cooldown_seconds', 600)
        random_candidate = now - self.store.get_setting('last_random_voice:' + event.session_key, 0) >= cooldown
        random_candidate = random_candidate and random.random() < self.config.extra.get('speech_probability', .1)
        voice = voice_decision(event, messages, metadata,
            enabled=self.config.speech_enabled and (event.group_id is None or self.tools.domains.feature_enabled(event.group_id, 'persona_voice')),
            ready=self.speech.ready, random_candidate=random_candidate)
        voice_sent = False
        if voice.voice:
            try:
                path = await self.speech.synthesize(voice.text, self.config.extra.get('speech', {}))
                ids.extend(await self.deliver(event, MessageSegment.record(path), request_id=request_id))
                sent.append(VOICE_MARKER)
                voice_sent = True
                if not voice.explicit:
                    self.store.set_setting('last_random_voice:' + event.session_key, time.time())
                self.publish('speech', {'request_id': request_id, 'status': 'delivered'})
            except Exception as exc:
                logger.error('Speech for {} failed: {}', request_id, exc)
                self.publish('speech', {'request_id': request_id, 'status': 'text_fallback', 'error': str(exc)})
                fallback = metadata.get('text_fallback')
                if isinstance(fallback, list) and fallback and all(isinstance(text, str) and text.strip() for text in fallback):
                    messages = fallback
        if not voice_sent:
            for text in messages:
                try:
                    ids.extend(await self.deliver(event, text, request_id=request_id))
                    sent.append(text)
                except (Exception, asyncio.CancelledError) as exc:
                    status = 'partial' if ids else 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
                    self.store.record_delivery(event, request_id, outcome=status,
                        messages=sent, message_ids=ids, error=str(exc))
                    self._record_interrupted_reply(event, request_id, user_content, status=status)
                    raise
        expression = self.expressions.choose(event, metadata, voice=voice_sent,
            enabled=event.group_id is None or self.tools.domains.feature_enabled(event.group_id, 'persona_expressions'),
            probability=self.config.extra.get('expression_probability', .6))
        if expression:
            key, path = expression
            try:
                await self.deliver(event, MessageSegment.image(path), request_id=request_id)
                self.expressions.delivered(event, key)
            except Exception as exc:
                logger.error('Expression for {} failed: {}', request_id, exc)
                self.store.record_delivery(event, request_id, outcome='failed', messages=['[image]'], message_ids=[], error=str(exc))
                self.publish('expression', {'request_id': request_id, 'status': 'failed', 'error': str(exc)})
        return ids, sent

    async def resolve_quote(self, event):
        if event.quoted:
            return event
        reply = next((s for s in event.segments if s.get("type") == "reply"), None)
        if reply:
            data = await self.bot.call_api("get_msg", message_id=reply["data"]["id"])
            if isinstance(data, dict):
                quoted = parse_event({**data, "self_id": event.self_id, "message_type": "group" if event.group_id is not None else "private", "group_id": event.group_id})
                return replace(event, quoted=normalize_voice({**data, "text": quoted.text}))
        return event

    async def execute_call(self, event, call):
        if not self.store.group_present(event.group_id):
            return ToolResult("disabled", "机器人已离开该群；本地功能已暂停。")
        if not self.store.get_setting("tool_enabled:" + call.name, True):
            return ToolResult("disabled", "" if call.name == "external_game" else "该工具已关闭。")
        groups = self.store.get_setting("tool_groups:" + call.name, [])
        if groups and event.group_id not in groups:
            return ToolResult("disabled", "" if call.name == "external_game" else "该工具尚未在当前群启用。")
        if call.name == "qq_transport_status":
            if event.user_id not in self.store.get_setting("operator_ids", []):
                return ToolResult("denied", "该操作仅限机器人管理者。")
            with self.store.connect() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS transport_incidents(id INTEGER PRIMARY KEY, started_at REAL, ended_at REAL, reason TEXT)")
                incidents = [dict(row) for row in conn.execute("SELECT * FROM transport_incidents ORDER BY id DESC LIMIT ?", (int(call.arguments.get("limit", 20)),))]
            lines = ["新 OneBot 连接：" + ("已连接" if self.bot.connected else "未连接")]
            for row in incidents:
                start = datetime.fromtimestamp(row["started_at"]).isoformat(timespec="seconds")
                end = datetime.fromtimestamp(row["ended_at"]).isoformat(timespec="seconds") if row["ended_at"] else "尚未重连"
                lines.append(start + " → " + end)
            return ToolResult("ok", "\n".join(lines), {"connected": self.bot.connected, "incidents": incidents})
        if call.name == "external_game":
            if not self.game_allowed(event):
                return ToolResult("disabled")
            event = await self.resolve_quote(event)
            self.core_sources[event.event_id] = event
            try:
                await self.core.forward(event)
                return ToolResult("forwarded", "", {"protocol": "Core", "identity": "TangtangHarness"})
            except RuntimeError as exc:
                return ToolResult("error", str(exc))
        if call.name == "expression_send":
            path = self.expression(call.arguments.get("selector", event.text))
            return ToolResult("ok", "", {"expression": path.name}, wire_message(MessageSegment.image(path))) if path else ToolResult("empty", "没有找到对应表情。")
        if call.name == "tool_followup":
            text = self._local_chat_reply(replace(event, text=call.arguments.get("text", call.arguments.get("query", event.text))))
            return ToolResult("ok", str(text)) if text else ToolResult("empty", "请先查询排行，再指定要看的名次。")
        if call.name in {"chat_settings", "model_settings", "proactive_settings"}:
            args = dict(call.arguments)
            command = None
            if "text" in args:
                try:
                    command = parse_chat_settings(args.pop('text'), self.config, group_id=event.group_id,
                        managed_group_ids=tuple(self.tools.domains.all_group_ids()))
                except ValueError as exc:
                    return ToolResult('clarification', str(exc))
                if command.private_only and event.group_id is not None:
                    return ToolResult('denied', '系统设置请在私聊管理入口使用。')
                if command.requires_operator and event.user_id not in self.store.get_setting('operator_ids', []):
                    return ToolResult('denied', '仅机器人管理者可使用聊天管理指令。')
                args.update(command.changes)
            mutation = {k: v for k, v in args.items() if k not in {"action"}}
            if mutation and event.user_id not in self.store.get_setting("operator_ids", []):
                return ToolResult("denied", "仅机器人管理者可修改聊天设置")
            if call.name == "model_settings" and mutation:
                requested = args.get("active_model", args.get("profile_id", args.get("profile", args.get("name", args.get("value", "")))))
                profile = next((p for p in self.config.profiles if requested in {p.id, p.name}), None)
                if profile is None:
                    return ToolResult("clarification", "模型档案不存在")
                mutation = {"active_model": profile.id}
            elif call.name == "proactive_settings" and mutation:
                mutation = {k: v for k, v in mutation.items() if k in {"proactive_enabled", "extra"}}
                if "strategy" in args or "value" in args:
                    mutation["extra"] = {**self.config.extra, "proactive_strategy": args.get("strategy", args.get("value"))}
            if mutation:
                raw = asdict(self.config)
                raw.pop("root")
                raw.update(mutation)
                self.update_config(HarnessConfig.from_dict(raw, root=self.config.root))
            for field, gate in (("mention_chat_enabled", "mention_chat"), ("proactive_enabled", "proactive_chat")):
                if field in mutation:
                    self.tools.db.set_passive_setting(gate + "_global_enabled", "true" if mutation[field] else "false")
            text = chat_settings_status(command, self.config, managed_group_ids=tuple(self.tools.domains.all_group_ids())) if command else f"人格：达妮娅；模型：{self.config.active_model or '未配置'}；模式：{self.config.mode}"
            return ToolResult("ok", text, {"active_model": self.config.active_model})
        if call.name == "memory_manage":
            action = call.arguments.get("action", "list")
            if hasattr(self.chat, "memory_control"):
                return ToolResult("ok", self.chat.memory_control(event, action, call.arguments.get("query", "")))
            return ToolResult("error", "记忆接口尚未就绪")
        if call.name in {"growth_manage", "profile_generate", "persona_status", "persona_impression"}:
            if hasattr(self.chat, "manage"):
                result = self.chat.manage(event, call)
                return await result if hasattr(result, "__await__") else result
            return ToolResult("error", "对应后台能力未启用")
        return await self.tools.execute(event, call)

    def _local_chat_reply(self, event):
        source = re.sub(r"^(?:糖糖|达妮娅|娅娅|娅宝|小娅)[,，、：:\s]*", "", event.text).strip(" ?？!！。~～")
        if not source and any(s.get("type") == "image" for s in event.segments):
            return None
        if not source:
            path = self.config.root / "resources" / "personas" / "denia" / "lines.txt"
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else ["嗯？我在呢。"]
            previous = self.store.get_setting("canned:" + event.session_key, "")
            text = random.choice([line for line in lines if line and line != previous] or lines)
            self.store.set_setting("canned:" + event.session_key, text)
            return text
        if re.fullmatch(r"(?:这|我们|本)(?:个)?群(?:是|叫)(?:什么|啥)(?:名字|群)?", source) and event.group_id is not None:
            groups = self.tools.db.managed_groups()
            name = next((g["group_name"] for g in groups if g["group_id"] == event.group_id), "")
            return "这是" + name if name else "不知道这个群叫什么"
        ordinal = re.fullmatch(r"第([一二三四五六七八九十]|\d+)名(?:是|叫)?谁", source)
        if ordinal:
            rank = int(ordinal[1]) if ordinal[1].isdigit() else "一二三四五六七八九十".index(ordinal[1]) + 1
            rows = next((r["result"]["data"].get("rows", r["result"]["data"].get("ranking", [])) for r in self.store.tool_results(event.session_key)
                         if r["name"] == "ranking" and r["result"]["status"] == "ok"), [])
            if 0 < rank <= len(rows):
                row = rows[rank - 1]
                return f"第{rank}名是{row.get('nickname') or row.get('name') or row.get('user_id')}。"
        if re.fullmatch(r"(?:来|发|给我)(?:个|张)?表情(?:包)?(?:\s*.*)?", source):
            expression = self.expression(source)
            if expression:
                return MessageSegment.image(expression)
        return None

    def expression(self, selector):
        return self.expressions.resolve(str(selector or ''))

    async def _knowledge_facts(self, event):
        # Search stays local. Only relevant short extracts enter the dynamic tail.
        results = []
        for domain in ("zhijiang", "mingchao"):
            entries = self.tools.knowledge.approved_entries(domain)
            for row in entries:
                if len(row["title"]) >= 2 and row["title"] in event.text:
                    results.append({"title": row["title"], "summary": row["summary"][:600], "source": row.get("source_url", "")})
        return {"local_knowledge": results[:3]} if results else None

    async def deliver(self, event, messages, *, request_id=""):
        if self.config.mode != "live":
            return []
        if not self.store.group_present(event.group_id):
            raise RuntimeError("机器人已离开该群；结果未发送")
        if not self.group_delivery_allowed(event.group_id):
            raise RuntimeError("QQ 群列表尚未完成同步；群投递已暂停")
        if self._filtered(event):
            raise RuntimeError("发送前该用户已被过滤；本轮结果未发送")
        start = time.perf_counter()
        started_at = time.time()
        receipt = await self.bot.send(event, messages)
        ids = [str(receipt["message_id"])]
        recorded_text = message_text(messages)
        self.store.record_delivery(event, request_id, outcome="delivered", messages=[recorded_text], message_ids=ids,
                                  started_at=started_at, elapsed_ms=round((time.perf_counter() - start) * 1000, 3))
        if hasattr(self.tools, "collect_outgoing"):
            try:
                await self.tools.collect_outgoing(event, recorded_text, ids[0])
            except Exception as exc:
                logger.error('Outgoing collection after confirmed receipt failed: {}', exc)
                self.publish('collection', {'request_id': request_id, 'status': 'failed', 'error': str(exc)})
        self.publish("delivery", {"session_key": event.session_key, "request_id": request_id, "message_ids": ids,
                                  "latency_ms": (time.perf_counter() - start) * 1000})
        return ids

    async def deliver_tool(self, event, result):
        if not self.store.group_present(event.group_id):
            raise RuntimeError("机器人已离开该群；工具结果未发送")
        if not self.group_delivery_allowed(event.group_id):
            raise RuntimeError("QQ 群列表尚未完成同步；群工具投递已暂停")
        if result.status in {'error', 'failed', 'timeout'}:
            error = result.data.get('error') or result.text
            logger.error('Local tool failed: {}', error)
            self.publish('tool', {'status': result.status, 'event_key': event.key, 'error': error})
            return
        for action in result.data.get("actions", []):
            if self.config.mode == "live" and self.config.extra.get("isolated_scope_enabled", False):
                target_group = action["params"].get("group_id", event.group_id)
                if not self.group_delivery_allowed(target_group):
                    raise RuntimeError("目标群已离开或正在同步群列表；工具动作未执行")
                await self.bot.call_api(action["action"], **action["params"])
        nodes = result.data.get("forward_nodes")
        if nodes:
            await self.deliver_forward(event, nodes)
        elif result.messages or result.text:
            await self.deliver(event, result.messages or result.text)
        for message in result.data.get("additional_messages", []):
            await self.deliver(event, message)
        for delivery in result.data.get("deliveries", []):
            target = replace(event, group_id=int(delivery["group_id"]))
            try:
                ids = await self.deliver(target, delivery.get("messages") or delivery.get("message") or delivery.get("text", ""))
                delivery.update(status="delivered", message_ids=ids)
            except Exception as exc:
                delivery.update(status="failed", error=str(exc))

    async def deliver_forward(self, event, nodes, *, request_id=""):
        if self.config.mode != "live":
            return []
        if not self.store.group_present(event.group_id):
            raise RuntimeError("机器人已离开该群；转发结果未发送")
        if not self.group_delivery_allowed(event.group_id):
            raise RuntimeError("QQ 群列表尚未完成同步；群转发已暂停")
        if self._filtered(event):
            raise RuntimeError("发送前该用户已被过滤；转发结果未发送")
        params = {"messages": nodes, "group_id" if event.group_id is not None else "user_id": event.group_id if event.group_id is not None else event.user_id}
        start, started_at = time.perf_counter(), time.time()
        receipt = await self.bot.call_api("send_group_forward_msg" if event.group_id is not None else "send_private_forward_msg", **params)
        if not receipt or not receipt.get("message_id"):
            raise RuntimeError("QQ 未确认转发消息回执")
        ids = [str(receipt["message_id"])]
        recorded_text = message_text(nodes)
        self.store.record_delivery(event, request_id, outcome="delivered", messages=[recorded_text], message_ids=ids,
                                  started_at=started_at, elapsed_ms=round((time.perf_counter() - start) * 1000, 3))
        try:
            await self.tools.collect_outgoing(event, recorded_text, ids[0])
        except Exception as exc:
            logger.error('Forward collection after confirmed receipt failed: {}', exc)
            self.publish('collection', {'request_id': request_id, 'status': 'failed', 'error': str(exc)})
        self.publish("delivery", {"session_key": event.session_key, "request_id": request_id, "message_ids": ids})
        return ids

    async def _speak(self, event, text):
        try:
            path = await self.speech.synthesize(text, self.config.extra.get("speech", {}))
            await self.deliver(event, MessageSegment.record(path))
        except Exception as exc:
            self.publish("speech", {"status": "failed", "error": str(exc)})

    async def _scheduler(self):
        last_cleanup = 0
        summary_started = False
        while True:
            if self.config.mode == "live" and self.config.extra.get("isolated_scope_enabled", False) and self.bot.connected:
                try:
                    pending = await self.tools.tick(
                        group_ids=[group for group in self.config.group_ids if self.accepts_background(group)],
                        user_ids=self.config.extra.get("private_user_ids", []))
                except Exception as exc:
                    self.publish("job", {"status": "failed", "error": str(exc)})
                    pending = []
                for item in pending:
                    group = item.get("group_id")
                    if group is None:
                        user = int(item["user_id"])
                        private_users = self.config.extra.get("private_user_ids", [])
                        if not self.config.private_chat_enabled or private_users and user not in private_users:
                            continue
                    else:
                        group, user = int(group), self.bot.self_id
                        if not self.accepts_background(group):
                            continue
                    event = InboundEvent("job:" + uuid.uuid4().hex, self.bot.self_id, user,
                                         group, "", timestamp=time.time())
                    try:
                        await self.deliver_tool(event, item["result"])
                        self.tools.mark_delivered(item["result"], True)
                    except Exception as exc:
                        self.tools.mark_delivered(item["result"], False)
                        self.publish("job", {"status": "failed", "error": str(exc)})
                try:
                    await self._proactive_tick()
                except Exception as exc:
                    self.publish("proactive", {"status": "failed", "error": str(exc)})
                if self.config.summary_enabled and not self.chat.cache_first:
                    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
                    for group in self.config.group_ids:
                        if not self.accepts_background(group):
                            continue
                        key = f"summary_schedule:{group}:{day}"
                        if not summary_started or not self.store.get_setting(key, False):
                            self.chat.enqueue_summary("group:" + str(group))
                            self.store.set_setting(key, True)
                    summary_started = True
            if time.time() - last_cleanup > 21600:
                cleanup_generated(self.config.root, retention_hours=self.config.extra.get("retention_hours", 24))
                last_cleanup = time.time()
            # Private context windows are an in-memory cache.  Releasing an
            # idle one keeps permanent Store history intact and never changes
            # chat admission or continuation permissions.
            for transition in self.chat.windows.release_idle():
                self.store.set_setting('context_window:' + transition.session_key,
                                       self.chat.windows.export(transition.session_key))
            await asyncio.sleep(1)

    async def _external_worker(self):
        while True:
            if self.config.mode == "live":
                if self.bot.connected:
                    quota_refresh = self.continuation.begin_quota_refresh(time.time())
                    membership_due = (not self._last_group_membership_sync
                                      or time.monotonic() - self._last_group_membership_sync >= 60)
                    if quota_refresh or membership_due:
                        try:
                            groups = await self._sync_group_membership()
                            if quota_refresh:
                                self.continuation.finish_quota_refresh(
                                    time.time(),
                                    {int(g["group_id"]): int(g.get("member_count", 0)) for g in groups},
                                )
                        except Exception as exc:
                            self.publish("group_membership", {"status": "failed", "error": str(exc)})
                            if quota_refresh:
                                self.continuation.finish_quota_refresh(time.time(), None)
                                self.publish("quota", {"status": "failed", "error": str(exc)})
                try:
                    await self.tools.poll_external(
                        group_ids=[group for group in self.config.group_ids if self.accepts_background(group)])
                except Exception as exc:
                    self.publish("external", {"status": "failed", "error": str(exc)})
            await asyncio.sleep(15)

    def _reconcile_group_membership(self, groups) -> tuple[int, ...]:
        """Synchronize managed-group enablement with the authoritative QQ list.

        Group messages are not a membership signal: a delayed packet must not
        revive a group the bot already left.  The OneBot ``get_group_list``
        response is the only source used to archive missing groups and to
        reactivate groups that were joined again.
        """
        if not isinstance(groups, (list, tuple)):
            raise ValueError("QQ 群列表响应不是有效列表；保留当前群归档状态")
        active: dict[int, str] = {}
        for item in groups:
            try:
                group_id = int(item.get("group_id"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError("QQ 群列表含无效群号；保留当前群归档状态") from exc
            if group_id <= 0:
                raise ValueError("QQ 群列表含无效群号；保留当前群归档状态")
            active[group_id] = str(item.get("group_name") or item.get("group_remark") or "")[:80]
        active_ids = set(active)
        changed: list[int] = []
        for group_id, name in active.items():
            was_enabled = self.tools.db.is_managed_group(group_id)
            self.tools.domains.ensure_group(group_id, group_name=name, reactivate=True)
            if not was_enabled:
                changed.append(group_id)
        for row in self.tools.db.all_managed_groups():
            group_id = int(row["group_id"])
            if bool(row["enabled"]) and group_id not in active_ids:
                self.tools.domains.disable_group(group_id)
                changed.append(group_id)
        for group_id in changed:
            self.publish("group_membership", {"group_id": group_id, "enabled": group_id in active_ids})
        return tuple(changed)

    async def _speech_worker(self):
        while True:
            if self.config.mode == "live" and self.config.speech_enabled:
                from .supervisor import SupervisorStore
                supervisor = SupervisorStore(self.config.root)
                # Managed recovery has one owner so every attempt and result
                # reaches the shared history. Preserve saved manual-off intent
                # even while the external watchdog is temporarily disabled.
                desired = supervisor.get_services()['speech'].get('desired')
                if not supervisor.meta('enabled', False) and desired is not False and desired != 0:
                    await self.speech_runtime.ensure_running()
                await self.speech.check(self.config.extra.get("speech", {}))
            await asyncio.sleep(15)

    async def _topics_tick(self):
        if self.chat.cache_first or self.config.mode != "live" or not self.config.background_enabled:
            return None
        active = any(self.accepts_background(group) and self.tools.domains.feature_enabled(group, "persona_topics")
                     and (self.chat_allowed(InboundEvent("topics", self.bot.self_id, self.bot.self_id, group, ""))
                          or self.chat_allowed(InboundEvent("topics", self.bot.self_id, self.bot.self_id, group, ""), "proactive"))
                     for group in self.config.group_ids)
        if not active:
            return None
        self.topics.import_legacy_snapshot()
        if not self.topics.refresh_due():
            return None
        result = await collect_topics(self.topics, self.tools.asoul)
        self.publish("topics", result)
        return result

    async def _topics_worker(self):
        while True:
            try:
                await self._topics_tick()
            except Exception as exc:
                logger.warning("Public topic worker failed: {}", type(exc).__name__)
                self.publish("topics", {"status": "failed", "error": type(exc).__name__})
            await asyncio.sleep(60)

    async def _ai_worker(self):
        while True:
            # Background work has its own single-flight lock and persisted
            # budget.  Blocking it whenever *any* foreground session is busy
            # starves memory, summary and compaction jobs when proactive chat
            # keeps a live group active.  Let the worker claim one job while
            # foreground requests continue independently; the model client
            # and delivery path already keep those concerns separate.
            if self.config.mode == "live":
                try:
                    result = await self.chat.run_background_once(admit_job=self._background_job_admitted)
                    if result:
                        self.publish("background", result)
                except Exception as exc:
                    self.publish("background", {"status": "failed", "error": str(exc)})
            await asyncio.sleep(1)

    def _background_job_admitted(self, job: dict) -> bool:
        scope = job["source"].get("source_session", job["session_key"])
        group_id = int(scope.split(":")[1]) if scope.startswith("group:") else None
        return self.group_delivery_allowed(group_id)

    async def _proactive_tick(self):
        if not self.config.proactive_enabled:
            return
        now = time.time()
        for group, state in self.traffic.items():
            if not self.accepts_background(group) or self.session_locks.get("group:" + str(group), asyncio.Lock()).locked():
                continue
            settings = {**self.config.extra, **self.config.extra.get("proactive_by_group", {}).get(str(group), {})}
            strategy = settings.get('proactive_strategy', 'active_v1')
            if strategy == "legacy":
                fresh = state.last_tick < state.last_message
                state.last_tick = state.last_message
                candidate = fresh and now - state.last_attempt >= settings.get("proactive_cooldown_seconds", 900) and now - state.last_message < 60 and len(state.recent) >= settings.get("proactive_message_interval", 20) and random.random() < settings.get("proactive_probability", .02)
            else:
                candidate = decide(state, strategy, now, datetime.now(ZoneInfo("Asia/Shanghai")).hour, random.random()).reason == "candidate"
            if not candidate:
                continue
            day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            key = f"proactive_attempts:{group}:{day}"
            used = self.store.get_setting(key, 0)
            if used >= self.config.extra.get("proactive_daily_attempts", 8):
                continue
            self.store.set_setting(key, used + 1)
            state.last_attempt = now
            source = self.last_group_event[group]
            if not self.chat_allowed(source, "proactive"):
                continue
            state.consumed_message = source.event_id
            event = replace(source, event_id="proactive:" + uuid.uuid4().hex)
            self.store.append_event(event)
            self.store.set_setting("event_scope:" + event.key, {"kind": "chat", "chat_allowed": True})
            self.spawn(self.process(event, self.router.route(event), purpose="proactive"), "harness-proactive")


def cleanup_generated(root: Path, *, retention_hours=24, apply=True):
    cutoff, paths = time.time() - float(retention_hours) * 3600, []
    for folder in ("runtime/screenshots", "runtime/speech", "runtime/reports", "reports"):
        path = root / folder
        if path.exists():
            for item in path.rglob("*"):
                if item.is_file() and item.stat().st_mtime < cutoff:
                    paths.append(item.relative_to(root).as_posix())
                    if apply:
                        item.unlink()
    avatars: dict[int, list[Path]] = {}
    for item in (root / "runtime/avatars").glob("*.png"):
        if match := AvatarService._cache_name.fullmatch(item.name):
            avatars.setdefault(int(match.group("user_id")), []).append(item)
    for versions in avatars.values():
        current = max(versions, key=lambda item: (item.stat().st_mtime, item.name.lower()))
        for item in versions:
            if item != current:
                paths.append(item.relative_to(root).as_posix())
                if apply:
                    item.unlink()
    return paths
