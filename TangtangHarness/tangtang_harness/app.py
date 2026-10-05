"""Local REST/SSE console and SnowLuma reverse WebSocket endpoint."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

from .config import DEFAULT_ROOT, HarnessConfig, ModelProfile, load_config, public_config
from .context import payload_diff
from .models import ModelClient
from .onebot import parse_event
from .message_text import message_text
from .runtime import Runtime
from .store import decode_row
from .redaction import redact_payload, restore_redacted
from .types import InboundEvent, ToolCall
from .tools import TOOL_LABELS
from .analytics import Analytics, REQUEST_FROM, REQUEST_PROJECTION
from .analytics_business import BusinessAnalytics
from .console_business import ConsoleBusiness
from .notifications import CompletionPayload, NotificationService, TestGroupPayload
from .public_gateway import public_gateway_lifespan
from .supervisor import SupervisorStore


def request_view(row):
    usage = row.get("usage", {})
    return {**row, "created_at": row["started_at"], "status": row["outcome"],
            "latency_ms": usage.get("latency_ms"),
            'record_elapsed_ms': (row['ended_at'] - row['started_at']) * 1000 if row.get('ended_at') else None,
            **{key: usage.get(key) for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cache_ratio", "cost")},
            "layers": row.get("telemetry", {}).get("layers", []), "reply": row.get("response", [])}


def usage_metrics(rows):
    known = [r for r in rows if r.get("usage", {}).get("cache_read_tokens") is not None]
    weighted = [r for r in known if r["usage"].get("input_tokens") is not None]
    inputs = sum(r["usage"]["input_tokens"] for r in weighted)
    cache = sum(r["usage"]["cache_read_tokens"] for r in weighted)
    def total(field):
        values = [r.get("usage", {}).get(field) for r in rows if r.get("usage", {}).get(field) is not None]
        return sum(values) if values else None
    cost_values = [r.get("usage", {}).get("cost") for r in rows]
    elapsed = [(r["ended_at"] - r["started_at"]) * 1000 for r in rows if r.get("ended_at")]
    return {"request_count": len(rows), "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
            "cache_read_tokens": total("cache_read_tokens"), "cache_write_tokens": total("cache_write_tokens"),
            "cache_ratio": cache / inputs if inputs else None,
            "request_hit_ratio": sum(r["usage"]["cache_read_tokens"] > 0 for r in known) / len(known) if known else None,
            "coverage_ratio": len(known) / len(rows) if rows else None, "cache_known_requests": len(known),
            "cost": sum(cost_values) if rows and all(v is not None for v in cost_values) else None,
            "known_cost": sum(v for v in cost_values if v is not None) if any(v is not None for v in cost_values) else None,
            "latency_ms": sum(elapsed) / len(elapsed) if elapsed else None}


def create_app(root: Path = DEFAULT_ROOT, *, runtime: Runtime | None = None):
    rt = runtime or Runtime(load_config(root))
    analytics = Analytics(rt)
    business_analytics = BusinessAnalytics(rt)
    console_business = ConsoleBusiness(rt)
    notifications = NotificationService(rt)
    supervisor = SupervisorStore(rt.config.root)

    @asynccontextmanager
    async def lifespan(app):
        await rt.start()
        try:
            async with public_gateway_lifespan(app, rt.store):
                yield
        finally:
            await rt.close()

    app = FastAPI(title="TangtangHarness", version="0.1.0", lifespan=lifespan)
    app.state.runtime = rt

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return Response(json.dumps({"detail": str(exc)}, ensure_ascii=False), status_code=400, media_type="application/json")

    @app.get("/api/status")
    def status():
        with rt.store.connect() as conn:
            queued = conn.execute("SELECT COUNT(*) FROM background_jobs WHERE status='queued'").fetchone()[0]
        return {"mode": rt.config.mode, "version": "0.1.0", "pid": os.getpid(), "uptime_seconds": time.time() - rt.started_at,
                "transport": {"connected": rt.bot.connected, "group_membership_ready": rt._group_membership_ready,
                              "endpoint": f"ws://127.0.0.1:{rt.config.port}/onebot/v11/ws"},
                "core": {"connected": rt.core.socket is not None, "error": rt.core.error,
                         "subscription_connections": {"configured": len(rt.core.receive_connected),
                                                      "connected": sum(rt.core.receive_connected.values())}},
                "speech": {"enabled": rt.config.speech_enabled, "ready": rt.speech.ready, "error": rt.speech.error,
                           "runtime": rt.speech_runtime.status()},
                "background_count": queued, "active_tasks": len(rt.tasks)}

    @app.get("/api/watchdog")
    def watchdog(limit: int = Query(default=100, ge=1, le=500)):
        return supervisor.view(limit=limit)

    def analytics_filters(request: Request):
        values = dict(request.query_params)
        for name in ('after', 'before'):
            if values.get(name):
                values[name] = float(values[name])
        for name in ('user_id', 'group_id'):
            if values.get(name):
                values[name] = int(values[name])
        if 'actual_only' in values:
            values['actual_only'] = values['actual_only'].lower() not in {'false', '0'}
        return values

    @app.get('/api/analytics/overview')
    def analytics_overview(request: Request):
        return analytics.overview(analytics_filters(request))

    @app.get('/api/analytics/series')
    def analytics_series(request: Request):
        result = analytics.overview(analytics_filters(request))
        return {'items': result['series'], 'range': result['range'], 'as_of': result['as_of']}

    @app.get('/api/analytics/breakdown')
    def analytics_breakdown(request: Request):
        result = analytics.overview(analytics_filters(request))
        return {key: result[key] for key in ('by_model', 'by_purpose', 'by_session', 'cache_distribution', 'states', 'as_of')}

    @app.get('/api/analytics/requests')
    def analytics_requests(request: Request, offset: int = 0, page_size: int = 50,
                           sort: str = 'started_at', descending: bool = True, direction: str = ''):
        if direction:
            descending = direction != 'asc'
        return analytics.requests(analytics_filters(request), offset, page_size, sort, descending)

    @app.get('/api/analytics/sessions')
    def analytics_sessions(request: Request):
        return analytics.sessions(analytics_filters(request))

    @app.get('/api/analytics/windows')
    def analytics_windows(request: Request):
        """Cost and cache totals grouped by the actual context epoch."""
        return analytics.windows(analytics_filters(request))

    @app.get('/api/analytics/sessions/{session_key}')
    def analytics_session(session_key: str, request: Request):
        return analytics.session(session_key, analytics_filters(request))

    @app.get('/api/analytics/users')
    def analytics_users(session_key: str = ''):
        return analytics.users(session_key)

    @app.get('/api/analytics/records')
    def analytics_records(session_key: str, user_id: int, offset: int = 0, page_size: int = 50):
        return analytics.records(session_key, user_id, offset, page_size)

    @app.get('/api/analytics/jobs')
    def analytics_jobs(request: Request, offset: int = 0, page_size: int = 50):
        return analytics.jobs(analytics_filters(request), offset, page_size)

    @app.get('/api/analytics/tools')
    def analytics_tools(request: Request):
        return analytics.tools(analytics_filters(request))

    @app.get('/api/analytics/deliveries')
    def analytics_deliveries(request: Request):
        return analytics.deliveries(analytics_filters(request))

    @app.get('/api/analytics/business')
    def analytics_business(request: Request):
        return business_analytics.overview(analytics_filters(request))

    @app.get('/api/analytics/speech')
    def analytics_speech(request: Request):
        return business_analytics.speech_stats(analytics_filters(request))

    @app.get('/api/console/groups')
    def console_groups():
        return console_business.groups()

    @app.get('/api/console/ranking')
    def console_ranking(group_id: int, period: str = 'day', cluster: bool = False,
                        offset: int = 0, page_size: int = 100):
        return console_business.ranking(group_id, period, cluster, offset, page_size)

    @app.put('/api/console/groups/{group_id}')
    def console_group_settings(group_id: int, raw: dict):
        return console_business.save_group(group_id, raw)

    @app.post('/api/console/clusters')
    def console_cluster_create(raw: dict):
        return console_business.create_cluster(raw.get('name', ''))

    @app.get('/api/analytics/continuation')
    def analytics_continuation(request: Request):
        return business_analytics.continuation(analytics_filters(request))

    @app.get('/api/analytics/runtime')
    def analytics_runtime():
        return business_analytics.runtime()

    @app.get('/api/analytics/experiments')
    def analytics_experiments(request: Request):
        return business_analytics.experiments(analytics_filters(request))

    @app.get('/api/analytics/export')
    def analytics_export(request: Request, kind: str = 'requests', format: str = 'csv'):
        result = analytics.exports(kind, analytics_filters(request), format=format)
        return result

    @app.websocket("/onebot/v11/ws")
    async def onebot(socket: WebSocket):
        token = rt.config.onebot_access_token
        if token and socket.headers.get("authorization", "").removeprefix("Bearer ") != token:
            await socket.close(code=1008)
            return
        if rt.bot.connected:
            await socket.close(code=1013)
            return
        await socket.accept()
        await rt.bot.attach(socket, int(socket.headers.get("x-self-id", "0")))
        rt.transport_connected()
        try:
            while True:
                packet = await socket.receive_json()
                if packet.get("self_id"):
                    rt.bot.self_id = int(packet["self_id"])
                # RPC receipts must always be read, even during a slow tool/model request.
                if "echo" in packet and "post_type" not in packet:
                    rt.bot.receive_response(packet)
                else:
                    rt.spawn(rt.receive(packet), "harness-inbound")
        except WebSocketDisconnect:
            pass
        finally:
            rt.bot.detach(socket)
            rt.transport_disconnected()

    @app.get("/api/events/stream")
    @app.get("/api/stream")
    async def stream(request: Request):
        queue = asyncio.Queue(maxsize=200)
        rt.subscribers.add(queue)
        async def records():
            try:
                yield 'event: status\ndata: ' + json.dumps(status(), ensure_ascii=False) + '\n\n'
                while not await request.is_disconnected():
                    try:
                        value = await asyncio.wait_for(queue.get(), 15)
                        yield 'data: ' + json.dumps(value, ensure_ascii=False) + '\n\n'
                    except TimeoutError:
                        yield ': heartbeat\n\n'
            finally:
                rt.subscribers.discard(queue)
        return StreamingResponse(records(), media_type="text/event-stream")

    @app.get("/api/sessions")
    def sessions():
        # Keep the console's session list human-readable.  The session key is
        # still returned for API navigation, while the title/scope fields use
        # the full QQ group name and explicitly distinguish group chat from
        # private chat.
        metadata = analytics._group_names()
        items = []
        with rt.store.connect() as conn:
            for row in rt.store.sessions():
                key = row["session_key"]
                count = conn.execute("SELECT count(*) FROM events WHERE session_key=?", (key,)).fetchone()[0]
                view = analytics._scope_identity(conn, key, names=metadata)
                items.append({
                    **row,
                    "key": key,
                    "title": view.get("title") or key,
                    "kind": view.get("kind") or key.split(":")[0],
                    "kind_label": view.get("kind_label") or ("群" if key.startswith("group:") else "私聊" if key.startswith("private:") else "未知"),
                    "scope_label": view.get("scope_label") or key,
                    "group_name": view.get("group_name"),
                    "group_id": view.get("group_id"),
                    "user_id": view.get("user_id"),
                    "user_nickname": view.get("user_nickname"),
                    "nickname": view.get("user_nickname"),
                    "alias": view.get("alias"),
                    "enabled": view.get("enabled"),
                    "group": metadata.get(key, {}),
                    "event_count": count,
                    "last_event_at": row["updated_at"],
                })
        return {"items": items}

    @app.get("/api/sessions/{session_key}/events")
    def events(session_key: str, limit: int = 100):
        metadata = analytics._group_names()
        with rt.store.connect() as conn:
            identity = analytics._scope_identity(conn, session_key, names=metadata)
            session_view = {**identity, "kind_label": identity["scope_kind"], "group": metadata.get(session_key, {})}
            receipts = [decode_row(row) for row in conn.execute(
                "SELECT * FROM deliveries WHERE session_key=? ORDER BY id DESC LIMIT ?", (session_key, limit))]
            request_ids = tuple(dict.fromkeys(row['request_id'] for row in receipts if row.get('request_id')))
            cache_views = {row['id']: dict(row) for row in conn.execute(
                "SELECT id,json_extract(usage,'$.input_tokens') AS input_tokens,"
                "json_extract(usage,'$.cache_read_tokens') AS cache_read_tokens,"
                "json_extract(usage,'$.cache_ratio') AS cache_ratio,"
                "json_extract(usage,'$.output_tokens') AS output_tokens,snapshot_revision "
                "FROM requests WHERE id IN (" + ','.join('?' for _ in request_ids) + ')', request_ids)} if request_ids else {}
        confirmed_outgoing = {str(identity) for receipt in receipts for identity in receipt.get('message_ids', [])}
        values = []
        for row in rt.store.events(session_key, limit=limit):
            payload = row.get("payload") or {}
            sender = payload.get("sender") or {}
            nickname = sender.get("card") or sender.get("nickname") or row.get("user_id")
            outgoing = str(row.get("user_id")) == str(rt.bot.self_id)
            if outgoing and str(row.get('event_id')) in confirmed_outgoing:
                continue
            values.append({
                "id": str(row["id"]),
                "at": row["timestamp"] or row["received_at"],
                "role": "assistant" if outgoing else "user",
                "text": payload.get("text") or "",
                "type": "message",
                "source": row["event_key"],
                "speaker": sender,
                "speaker_nickname": nickname,
                "nickname": nickname,
                "outgoing": outgoing,
                "speaker_user_id": row.get("user_id"),
                "segments": payload.get("segments") or [],
                "scope": rt.store.get_setting("event_scope:" + row["event_key"], {}),
                "scope_kind": session_view.get("kind_label") or "会话",
                "scope_label": session_view.get("scope_label") or session_key,
                "group_name": session_view.get("group_name"),
                "group_id": session_view.get("group", {}).get("group_id") if isinstance(session_view.get("group"), dict) else None,
            })
        for row in rt.store.tool_results(session_key, limit=limit):
            values.append({"id": "tool:" + str(row["id"]), "at": row["created_at"], "role": "tool", "type": "tool",
                           "text": row["result"]["text"], "source": TOOL_LABELS.get(row["name"], row["name"]),
                           "tool_name": row["name"], "tool_label": TOOL_LABELS.get(row["name"], row["name"]),
                           "speaker_nickname": "本地工具：" + TOOL_LABELS.get(row["name"], row["name"]),
                           "nickname": "本地工具：" + TOOL_LABELS.get(row["name"], row["name"]), "result": row["result"],
                           "scope_kind": session_view.get("kind_label") or "会话", "scope_label": session_view.get("scope_label") or session_key,
                           "group_name": session_view.get("group_name")})
        for row in receipts:
            values.append({"id": "delivery:" + str(row["id"]), "at": row["created_at"], "role": "assistant", "type": "delivery",
                           "text": "\n".join(message_text(item) for item in row["messages"]), "source": row["request_id"], "status": row["outcome"], "message_ids": row["message_ids"],
                           "outgoing": True, "speaker_user_id": rt.bot.self_id, "speaker_nickname": "机器人", "nickname": "机器人",
                           "request_id": row.get('request_id'), "cache": cache_views.get(row.get('request_id')),
                           "scope_kind": session_view.get("kind_label") or "会话", "scope_label": session_view.get("scope_label") or session_key,
                           "group_name": session_view.get("group_name")})
        # Records are a reading log: newest first so the current conversation
        # is immediately visible without scrolling through its entire history.
        return {"items": sorted(values, key=lambda r: (r.get("at") or 0, str(r.get("id", ""))), reverse=True)}

    @app.get("/api/requests")
    def requests(session_key: str | None = None, profile_id: str | None = None, purpose: str | None = None,
                 provider: str | None = None, account: str | None = None, after: float = 0, before: float = 0, limit: int = 100):
        result = analytics.requests({'session_key': session_key, 'profile_id': profile_id, 'purpose': purpose,
            'provider': provider, 'account': account, 'after': after, 'before': before, 'actual_only': False}, page_size=limit)
        result['items'] = [{**row, 'created_at': row['started_at'], 'status': row['outcome']} for row in result['items']]
        return result

    @app.get("/api/requests/{request_id}")
    def request_detail(request_id: str):
        row = rt.store.request(request_id)
        if not row:
            raise HTTPException(404, "请求不存在")
        with rt.store.connect() as conn:
            receipts = [decode_row(r) for r in conn.execute("SELECT * FROM deliveries WHERE request_id=? ORDER BY id", (request_id,))]
            for receipt in receipts:
                timing = conn.execute('SELECT * FROM delivery_timings WHERE delivery_id=?', (receipt['id'],)).fetchone()
                receipt['timing'] = dict(timing) if timing else None
            tools = [decode_row(r) for r in conn.execute('SELECT * FROM tools WHERE event_key=? AND session_key=? ORDER BY id', (row['event_key'], row['session_key']))]
            attempts = [dict(r) for r in conn.execute(f'SELECT {REQUEST_PROJECTION} {REQUEST_FROM} WHERE r.event_key=? AND r.session_key=? ORDER BY r.started_at', (row['event_key'], row['session_key']))]
            group_names = analytics._group_names()
            for attempt in attempts:
                attempt.update(analytics._scope_identity(conn, attempt['session_key'], attempt.get('user_id'), names=group_names))
        trigger = rt.store.event(row['event_key'])
        sender = (trigger or {}).get('payload', {}).get('sender', {})
        with rt.store.connect() as conn:
            scope = analytics._scope_identity(conn, row['session_key'], (trigger or {}).get('user_id'), names=group_names)
        return redact_payload({**request_view(row), **scope, "tool_calls": tools,
                'trigger': trigger, 'user_id': (trigger or {}).get('user_id'),
                'nickname': sender.get('card') or sender.get('nickname'), 'attempts': attempts,
                "timeline": receipts, "merge": rt.store.get_setting("merge:" + row["event_key"], {})})

    @app.get("/api/requests/{request_id}/diff")
    def diff(request_id: str):
        row = rt.store.request(request_id)
        if not row:
            raise HTTPException(404, "请求不存在")
        with rt.store.connect() as conn:
            previous = decode_row(conn.execute('''SELECT * FROM requests WHERE session_key=? AND profile_id=?
                AND started_at<? AND model=? AND api_style=? AND provider=? AND account=? ORDER BY started_at DESC LIMIT 1''',
                (row['session_key'], row['profile_id'], row['started_at'], row['model'], row['api_style'], row['provider'], row['account'])).fetchone())
        return payload_diff(previous, row["payload"], row["telemetry"].get("layers", []))

    @app.get("/api/metrics")
    def metrics(profile_id: str | None = None, purpose: str | None = None, provider: str | None = None,
                account: str | None = None, after: float = 0, before: float = 0):
        result = analytics.overview({'profile_id': profile_id, 'purpose': purpose, 'provider': provider,
            'account': account, 'after': after, 'before': before})
        return {**result['metrics'], 'by_model': result['by_model'], 'as_of': result['as_of'], 'range': result['range']}

    @app.get("/api/settings")
    def settings():
        cfg = public_config(rt.config)
        cache_first = cfg['context_policy']['mode'] == 'cache_first'
        capacity_view = {key: cfg['context_policy'][key] for key in (
            'configured_input_budget_tokens', 'effective_input_budget_tokens', 'budget_source',
            'model_context_limit', 'capacity_source', 'capacity_verification',
            'reserved_output_tokens', 'safety_reserve_tokens',
            'rebuild_trigger_tokens', 'rebuild_target_tokens')}
        return {**cfg, "context": {"input_budget_tokens": cfg["input_budget_tokens"],
                                 "cache_input_budget_tokens": cfg['cache_input_budget_tokens'], "recent_rounds": cfg["recent_rounds"],
                                 "memory_enabled": cfg["memory_enabled"], "compaction_enabled": cfg["compaction_enabled"],
                                 "soft_budget_ratio": cfg["soft_budget_ratio"], "hard_budget_ratio": cfg["hard_budget_ratio"],
                                 **capacity_view},
                "routing": {"test_prefix": rt.config.extra.get("test_prefix", "#harness"), "call_keyword": cfg["call_keyword"],
                            "isolated_scope_enabled": rt.config.extra.get("isolated_scope_enabled", False)},
                "background": {"summary_enabled": cfg["summary_enabled"], "proactive_enabled": cfg["proactive_enabled"],
                               "speech_enabled": cfg["speech_enabled"], "cache_warmer_enabled": False},
                'effective_chat_features': {
                    name: False if cache_first else bool(cfg[name])
                    for name in ('memory_enabled', 'cognition_enabled', 'growth_enabled',
                                 'profile_enabled', 'summary_enabled', 'compaction_enabled', 'background_enabled')
                }}

    @app.put("/api/settings")
    async def update_settings(raw: dict):
        raw.pop('context_policy', None)
        raw.pop('effective_chat_features', None)
        current = asdict(rt.config)
        current.pop("root")
        for part in ("context", "background"):
            values = raw.pop(part, {})
            for key in ('configured_input_budget_tokens', 'effective_input_budget_tokens', 'budget_source',
                        'model_context_limit', 'capacity_source', 'capacity_verification',
                        'reserved_output_tokens', 'safety_reserve_tokens',
                        'rebuild_trigger_tokens', 'rebuild_target_tokens'):
                values.pop(key, None)
            current.update(values)
        routing = raw.pop("routing", {})
        for key in ("test_prefix", "isolated_scope_enabled"):
            if key in routing:
                current["extra"][key] = routing.pop(key)
        current.update(routing)
        raw.pop("profiles", None)
        raw.pop("onebot_access_token_configured", None)
        incoming_extra = restore_redacted(raw.pop("extra", {}), current["extra"])
        for key, value in incoming_extra.items():
            if isinstance(value, dict) and isinstance(current["extra"].get(key), dict):
                current["extra"][key].update(value)
            else:
                current["extra"][key] = value
        current.update(raw)
        rt.update_config(HarnessConfig.from_dict(current, root=rt.config.root))
        return settings()

    @app.get("/api/models")
    def models():
        return {"items": public_config(rt.config)["profiles"], "active_model": rt.config.active_model}

    @app.put("/api/models")
    async def update_models(raw: dict):
        old = {p.id: p for p in rt.config.profiles}
        profiles = []
        for item in raw.get("items", []):
            value = dict(item)
            if "api_key" not in value and value.get("id") in old:
                value["api_key"] = old[value["id"]].api_key
            if value.get("id") in old:
                saved_body = old[value["id"]].extra_body
                value["extra_body"] = restore_redacted(value.get("extra_body", saved_body), saved_body)
            profiles.append(ModelProfile.from_dict(value))
        cfg = replace(rt.config, profiles=tuple(profiles), active_model=raw.get("active_model", rt.config.active_model))
        rt.update_config(cfg)
        return models()

    def preview_event(raw):
        if "event" in raw:
            value = raw["event"]
            return InboundEvent.from_dict(value) if "event_id" in value else parse_event(value)
        session = raw.get("session_key", "private:101")
        kind, identity = session.split(":", 1)
        return InboundEvent("preview:" + uuid.uuid4().hex, rt.bot.self_id, int(raw.get("user_id", 101)),
                            int(identity) if kind == "group" else None, str(raw.get("text", "")), timestamp=time.time())

    @app.post("/api/preview")
    async def preview(raw: dict):
        event = preview_event(raw)
        decision = rt.router.route(event)
        result = rt.chat.preview(event, tool_facts=raw.get("tool_facts"), profile_id=raw.get("profile_id"))
        if hasattr(result, "__await__"):
            result = await result
        return redact_payload({**result, "route": asdict(decision), "model_calls": 0, "qq_writes": 0})

    @app.post("/api/replay")
    async def replay(raw: dict):
        event = preview_event(raw)
        if rt.store.append_event(event):
            rt.store.set_setting("event_scope:" + event.key, {"chat_allowed": False, "kind": "replay"})
        synthetic = replace(event, event_id="replay-preview:" + uuid.uuid4().hex)
        result = await preview({"event": synthetic.to_dict(), "tool_facts": raw.get("tool_facts"),
                                "profile_id": raw.get("profile_id")})
        return {**result, "replay_event_key": event.key}

    @app.get("/api/tools")
    def tools():
        return {"console_user_id": next(iter(rt.store.get_setting("operator_ids", [])), None),
                "items": [{**item, "enabled": rt.store.get_setting("tool_enabled:" + item["name"], True),
                            "group_ids": rt.store.get_setting("tool_groups:" + item["name"], [])}
                           for item in rt.tools.catalog()]}

    @app.put("/api/tools/{name}/enabled")
    def tool_enabled(name: str, raw: dict):
        if name not in {item["name"] for item in rt.tools.catalog()}:
            raise HTTPException(404, "工具不存在")
        rt.store.set_setting("tool_enabled:" + name, bool(raw["enabled"]))
        if "group_ids" in raw:
            rt.store.set_setting("tool_groups:" + name, [int(value) for value in raw["group_ids"]])
        rt.publish("tool_settings", {"name": name, "enabled": bool(raw["enabled"])})
        return tools()

    @app.post("/api/tools/run")
    async def execute_tool(raw: dict):
        local = dict(raw)
        if 'user_id' not in local:
            local['user_id'] = next(iter(rt.store.get_setting('operator_ids', [])), 101)
        if not local.get('session_key'):
            local['session_key'] = 'private:' + str(local['user_id'])
        if local['session_key'].startswith('private:') and 'user_id' not in raw:
            local['user_id'] = int(local['session_key'].split(':', 1)[1])
        event = preview_event(local)
        call = ToolCall(raw["name"], raw.get("arguments", {}))
        result = await rt.execute_call(event, call)
        rt.store.add_tool_result(event, call, result)
        if raw.get("deliver", False) and rt.in_scope(event):
            await rt.deliver_tool(event, result)
        return result.to_dict()

    @app.get("/api/jobs")
    def jobs():
        return analytics.jobs(page_size=100)

    @app.post("/api/jobs/{job_id}/{action}")
    def job_action(job_id: str, action: str):
        if action not in {"pause", "resume"}:
            raise HTTPException(400, "action 必须是 pause 或 resume")
        rt.store.update_job(job_id, "paused" if action == "pause" else "queued")
        return jobs()

    @app.get("/api/sessions/{session_key}/snapshots")
    def snapshots(session_key: str):
        return {"items": rt.store.snapshots(session_key)}

    @app.get("/api/assets/{digest}")
    def asset(digest: str):
        value = rt.store.asset(digest)
        if not value:
            raise HTTPException(404, "媒体不存在")
        return Response(value[1], media_type=value[0])

    @app.get("/api/experiments/{experiment_id}")
    def experiment_detail(experiment_id: str):
        record = rt.store.get_setting("experiment:" + experiment_id)
        if record is None:
            raise HTTPException(404, "实验记录不存在")
        return redact_payload(record)

    @app.post("/api/experiments")
    async def experiment(raw: dict):
        from .experiments import run_experiment
        return await run_experiment(rt, raw)

    @app.get("/api/imports")
    def imports():
        return {"items": rt.store.imports()}

    @app.get("/api/sessions/{session_key}/memories/{user_id}")
    def memories(session_key: str, user_id: int):
        return {"items": rt.store.memories(session_key, user_id, include_forgotten=True),
                "legacy": rt.store.legacy_personal_context(session_key, user_id)}

    @app.get("/api/sessions/{session_key}/profiles/{user_id}")
    def profile_history(session_key: str, user_id: int):
        return {"items": rt.store.profile_versions(session_key, user_id)}

    @app.get("/api/sessions/{session_key}/cognition/{user_id}")
    def cognition(session_key: str, user_id: int):
        return {"items": rt.store.cognition(session_key, user_id), "growth": rt.store.growth(session_key, include_disabled=True)}

    def notification_enabled():
        if not notifications.enabled:
            raise HTTPException(404, "notification is disabled")

    def notification_authorized(request: Request):
        notification_enabled()
        if not notifications.authorized(request.headers.get("X-Codex-Completion-Token", "")):
            raise HTTPException(401, "invalid notification token")

    async def notification_result(operation):
        try:
            return await notifications.run(operation)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "OneBot notification action failed") from exc

    @app.post("/api/notifications")
    async def notify(raw: dict):
        notification_enabled()
        return await notification_result(notifications.plain(raw))

    @app.post("/internal/codex/completion")
    async def codex_completion(request: Request, raw: CompletionPayload):
        notification_authorized(request)
        return await notification_result(notifications.completion(raw))

    @app.post("/internal/codex/test-group-notice")
    async def test_group_notice(request: Request, raw: TestGroupPayload):
        notification_authorized(request)
        return await notification_result(notifications.notice(raw))

    @app.post("/internal/codex/test-group-members")
    async def test_group_members(request: Request):
        notification_authorized(request)
        return await notification_result(notifications.members())

    @app.get("/business/assets/{path:path}")
    def business_asset(path: str):
        if path == "tangtang-avatar.jpg":
            path = "tangtang_avatar.jpg"
        target = (rt.tools.resources / path).resolve()
        if not target.is_relative_to(rt.tools.resources.resolve()) or not target.is_file():
            raise HTTPException(404, "素材不存在")
        return FileResponse(target)

    @app.get("/business/help/api")
    async def help_data():
        return await rt.tools.web.read("help")

    @app.get("/business/ranking/api")
    async def ranking_data(group_id: int = 0, scope: str = "", group: str = "",
                           period: str = "", cluster: bool = False):
        return await rt.tools.web.read("ranking", group_id=group_id, scope=scope, group=group,
                                       period=period, cluster=cluster)

    @app.get("/business/schedule/api/schedule")
    async def schedule_data(view: str = "today"):
        return await rt.tools.web.read("schedule", view=view)

    @app.get("/business/{name}/api/{token}/state")
    def business_state(name: str, token: str):
        return rt.tools.web.state(name, token)

    @app.post("/business/{name}/api/{token}/{action:path}")
    async def business_action(name: str, token: str, action: str, request: Request):
        rt.tools.web._session(name, token)
        if action in {"send-preview", "send-image"} and rt.config.mode != "live":
            raise HTTPException(400, "观察与回放模式不发送 QQ；预览仍可使用")
        if "multipart/form-data" in request.headers.get("content-type", ""):
            form = await request.form()
            raw = dict(form)
            image = raw.pop("image", None)
            if image is not None:
                raw["image_path"] = str(rt.tools.web.upload(await image.read(), image.filename))
        else:
            raw = await request.json()
        result = await rt.tools.web.action(name, token, action, raw)
        if isinstance(result, dict):
            return result
        if action in {"preview", "graphic-preview"}:
            return FileResponse(result.data["image_path"])
        if action in {"send-preview", "send-image"}:
            if any(not rt.accepts_background(int(target["group_id"])) for target in result.data.get("deliveries", [])):
                raise HTTPException(400, "并行联调仅允许向明确配置的独立测试范围发送公告")
            await rt.deliver_tool(rt.tools.web.event(name, token), result)
            targets = result.data.get("deliveries", [])
            return {"sent": sum(t.get("status") == "delivered" for t in targets),
                    "failed": sum(t.get("status") != "delivered" for t in targets), "targets": targets}
        return result.to_dict()

    @app.get("/business/{name}")
    async def business_page(name: str, group_id: int = 0, user_id: int = 0,
                            scope: str = "", group: str = "", period: str = "", cluster: bool = False,
                            token: str = ""):
        if name in {"duplicate", "whitelist", "announcement"}:
            try:
                return HTMLResponse(await rt.tools.page(name, token=token))
            except ValueError as exc:
                raise HTTPException(404, "管理链接无效或已过期，请重新获取。") from exc
        if name == "ranking":
            return HTMLResponse(await rt.tools.web.page(name, group_id=group_id, scope=scope,
                                                        group=group, period=period, cluster=cluster))
        return HTMLResponse(await rt.tools.page(name, group_id=group_id, user_id=user_id, token=token))

    @app.get("/{path:path}")
    def frontend(path: str):
        build = rt.config.root / "frontend" / "dist"
        target = (build / path).resolve()
        if target.is_relative_to(build.resolve()) and target.is_file():
            return FileResponse(target)
        if (build / "index.html").exists():
            return FileResponse(build / "index.html")
        return HTMLResponse("<h1>TangtangHarness 已启动</h1><p>控制台尚未构建。请在 frontend 中运行 npm install 和 npm run build。</p>")

    return app
