"""Explicit synthetic comparisons; model usage exists only for paid=True runs."""
from __future__ import annotations

import copy
import asyncio
import time
import uuid
from dataclasses import replace

from .chat import ChatService
from .context import build_context, payload_diff, text_token_estimate
from .log_context import append_cache_response, observe_cache_usage, persist_cache_context
from .models import ModelRequestError, model_error_summary
import hashlib
from .store import Store, encode
from .types import InboundEvent
from .redaction import redact_payload


async def run_experiment(runtime, raw: dict):
    if raw.get("paid") is True and runtime.config.mode != "live":
        raise ValueError("观察和回放模式不调用模型；收费实验需要在 live 模式显式执行")
    run_id = uuid.uuid4().hex
    created_at = time.time()
    kind = raw.get("kind", "cache_append")
    cache_first = runtime.chat.cache_first
    if kind == "compaction" and cache_first:
        raise ValueError("缓存主路径已停用 AI 压缩快照；请使用同会话追加实验")
    profile = runtime.config.profile(raw.get("profile_id"))
    temp = Store(runtime.config.root, path=runtime.config.root / "runtime" / "experiments" / (run_id + ".db"))
    service = ChatService(replace(runtime.config, mode="observe", active_model=profile.id), temp)
    event = InboundEvent("synthetic:" + run_id, 103, 101, 102, raw.get("text", "我们下次继续讨论今天的安排。"), timestamp=0)
    before = service.preview(event)
    before_context = None
    label = "当前配置的合成冷请求"
    if kind in {"cache_append", "compaction"}:
        if cache_first:
            before_context = build_context(service.config, temp, event, profile)
            persist_cache_context(temp, before_context)
            append_cache_response(temp, before_context,
                                  encode({"decision": "reply", "messages": ["好的，下次继续。"]}))
        else:
            with temp.connect() as conn:
                conn.execute("INSERT INTO sessions VALUES(?,?,0)", (event.session_key, time.time()))
                conn.execute("INSERT INTO turns(session_key,event_key,request_id,user_content,messages,message_ids,status,created_at) VALUES(?,?,?,?,?,?,'delivered',?)",
                             (event.session_key, event.key, "synthetic", encode(before["payload"].get("messages", [{}])[-1].get("content", event.text)), encode(["好的，下次继续。"]), "[]", time.time()))
            if kind == "compaction":
                temp.publish_snapshot(event.session_key, {"facts": ["用户希望以后继续讨论安排（合成资料）"], "commitments": [], "unresolved": [], "topic_progress": []}, 1)
        after = service.preview(replace(event, event_id=event.event_id + ":append", text="接着刚才的话，先说第一项。"))
        label = "追加完整轮次" if kind == "cache_append" else "切换压缩快照"
    elif kind == "cold_warm":
        after = copy.deepcopy(before)
        label = "完全相同的第二次请求；本地无法判断实际缓存"
    elif kind == "prefix_change":
        after = copy.deepcopy(before)
        layer = after["layers"][0]
        old_tokens = layer["estimated_tokens"]
        layer["text"] += "\n[仅本次实验] 合成固定规则版本二。"
        layer["estimated_tokens"] = text_token_estimate(layer["text"], profile.tokenizer)[0]
        # This explicit experiment edits the actual fixed prefix, so its local
        # count must match that payload before observing provider input usage.
        token_delta = layer["estimated_tokens"] - old_tokens
        for field in ('estimated_input_tokens', 'local_input_tokens', 'input_tokens_before_trim',
                      'capacity_estimated_input_tokens', 'budget_input_tokens', 'budget_input_tokens_before_trim'):
            if field in after['telemetry']:
                after['telemetry'][field] += token_delta
        after["telemetry"]["static_prefix_hash"] = hashlib.sha256(layer["text"].encode()).hexdigest()
        if profile.api_style == "responses":
            after["payload"]["input"][0]["content"][0]["text"] = layer["text"]
        else:
            after["payload"]["messages"][0]["content"] = layer["text"]
        label = "固定前缀变化；未修改生产人格"
    elif kind == "model_switch":
        alternate = next((p for p in runtime.config.profiles if p.id != profile.id), None)
        if alternate is None:
            output = redact_payload({"id": run_id, "kind": kind, "created_at": created_at, "status": "needs_model", "model_calls": 0, "qq_writes": 0,
                    "result": {"reason": "需要至少两个模型档案才能比较模型切换", "before": before}})
            runtime.store.set_setting('experiment:' + run_id, output)
            return output
        after = service.preview(event, profile_id=alternate.id)
        label = "同业务输入的另一个模型"
    elif kind == "tool_isolation":
        after = service.preview(event, tool_facts={"synthetic": True, "ranking": [{"rank": 1, "name": "示例成员", "count": 5}]})
        label = "必要工具事实仅进入动态尾部；工具目录不进入固定前缀"
    else:
        raise ValueError("未知实验类型")
    delta = payload_diff({"id": "synthetic:before", "payload": before["payload"], "telemetry": {"layers": before["layers"]}}, after["payload"], after["layers"])
    steps = [{"label": "冷请求", "preview": before}, {"label": label, "preview": after}]
    results = {"steps": steps, "diff": delta, "actual_cache": None, "note": "合成输入独立保存；未写入真实业务历史"}
    calls = 0
    if raw.get("paid") is True:
        for index, step in enumerate(steps):
            if index == 0 and before_context is not None:
                # Replace the offline synthetic output with the actual first result.
                persist_cache_context(temp, before_context)
            selected = runtime.config.profile(step["preview"]["profile_id"])
            payload = step["preview"]["payload"]
            request = runtime.store.add_request(replace(event, event_id=f"experiment:{run_id}:{index}"), selected, payload,
                purpose="prewarm" if raw.get("prewarm") else "experiment", telemetry={"layers": step["preview"]["layers"], "experiment_id": run_id})
            calls += 1
            result = None
            try:
                result = await runtime.chat.model_client.generate(selected, payload)
                if cache_first:
                    observe_cache_usage(runtime.store, selected, step['preview'], result.usage)
                    observe_cache_usage(temp, selected, step['preview'], result.usage)
                runtime.store.finish_request(request, usage=result.usage, outcome="completed", messages=[result.text], account=result.account,
                                             diagnostics=getattr(result, 'diagnostics', None))
                step.update(request_id=request, usage=result.usage, reply=result.text)
                if index == 0 and before_context is not None:
                    append_cache_response(temp, before_context, result.text)
            except asyncio.CancelledError:
                runtime.store.finish_request(request, outcome="cancelled", error="CancelledError")
                raise
            except Exception as exc:
                attempt_result = result if result is not None else exc if isinstance(exc, ModelRequestError) else None
                error = model_error_summary(exc, selected)
                runtime.store.finish_request(request, outcome="failed", error=error,
                    usage=attempt_result.usage if attempt_result is not None else None,
                    account=attempt_result.account if attempt_result is not None else 'unknown',
                    diagnostics=getattr(attempt_result, 'diagnostics', None))
                step.update(request_id=request, status="failed", error=error)
            if index == 0 and before_context is not None:
                steps[1]["preview"] = service.preview(replace(
                    event, event_id=event.event_id + ":append", text="接着刚才的话，先说第一项。"))
                results["diff"] = payload_diff(
                    {"id": "synthetic:before", "payload": before["payload"],
                     "telemetry": {"layers": before["layers"]}},
                    steps[1]["preview"]["payload"], steps[1]["preview"]["layers"])
        results["actual_cache"] = [step.get("usage", {}).get("cache_read_tokens") for step in steps]
    output = {"id": run_id, "kind": kind, "created_at": created_at, "status": "completed" if calls else "offline", "model_calls": calls, "qq_writes": 0, "result": results}
    output = redact_payload(output)
    runtime.store.set_setting("experiment:" + run_id, output)
    return output
