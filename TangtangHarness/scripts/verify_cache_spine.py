"""Compare real Harness context behavior offline, using synthetic stores only.

Run this same file with --source BASELINE, MODIFIED_FILE or a restored checkout.
The report describes local input reuse; it never claims upstream cache hits.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib
import json
import socket
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


def load_target(source: Path):
    source = source.resolve()
    if not (source / "tangtang_harness" / "context.py").is_file():
        raise ValueError("--source must contain tangtang_harness/context.py")
    sys.path.insert(0, str(source))
    modules = {
        name: importlib.import_module("tangtang_harness." + name)
        for name in ("chat", "config", "context", "models", "store", "types")
    }
    if any(not Path(module.__file__).resolve().is_relative_to(source / "tangtang_harness")
           for module in modules.values()):
        raise RuntimeError("Run each --source in a fresh Python process; another Harness is already imported")
    return modules


class OfflineFixture:
    def __init__(self, api, root: Path, *, budget=100_000, response="reply", fallback=False):
        import httpx

        self.api, self.root, self.calls = api, root, []
        profile = api["config"].ModelProfile(
            "legacy-3", "Synthetic model 3", "custom", "synthetic-model-3",
            "https://example.invalid/v1", vision=False, max_output_tokens=512,
            context_limit=200_000, cache_key_enabled=True,
            fallback_profile_id="backup" if fallback else "",
        )
        profiles = (profile, replace(profile, id="backup", model="synthetic-backup",
                                     fallback_profile_id="")) if fallback else (profile,)
        cache_budget = ({"cache_input_budget_tokens": budget}
                        if "cache_input_budget_tokens" in api["config"].HarnessConfig.__dataclass_fields__ else {})
        self.config = api["config"].HarnessConfig(
            root=root, mode="live", profiles=profiles, active_model="legacy-3",
            input_budget_tokens=budget, recent_rounds=3, background_batch_size=2,
            extra={"cache_input_budget_tokens": budget},
            **cache_budget,
        )
        self.store = api["store"].Store(root)
        self.response = response

        def handler(request):
            wire = json.loads(request.content)
            self.calls.append(copy.deepcopy(wire))
            if self.response == "failed" and wire["model"] == profile.model:
                return httpx.Response(503, json={"error": {"message": "synthetic failure"}})
            silent = self.response == "silent"
            raw = json.dumps({"decision": "silent" if silent else "reply",
                              "messages": [] if silent else ["合成答复 " + str(len(self.calls))]},
                             ensure_ascii=False)
            return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})

        self.client = api["models"].ModelClient(transport=httpx.MockTransport(handler))
        self.service = api["chat"].ChatService(self.config, self.store, self.client)

    def event(self, n, *, user=101, text=None):
        return self.api["types"].InboundEvent(
            str(n), 999, user, 201, text or ("合成聊天第 " + str(n) + " 轮：" + "继续讨论音乐和今天的安排。" * 24),
            sender={"nickname": "合成群友"}, timestamp=1_700_000_000 + int(n),
        )

    async def turn(self, event):
        result = await self.service.respond(event)
        if result.status == "generated":
            self.store.confirm_response(result, event, ["synthetic-receipt-" + event.event_id])
            self.service.complete_context_window(event.session_key, result.snapshot_revision)
            await self.service.after_delivery(event, result)
        else:
            # The public caller may explicitly release too. Record before this
            # call in lifecycle tests so a missing automatic release is visible.
            self.service.cancel_context_window(event.session_key)
        return result

    def mutate_low_value_sources(self, n):
        text = "LOW_VALUE_MEMORY " + str(n) + " " + "重复个人参考。" * 80
        self.store.set_setting("memory:group:201:101", [{"id": 1, "version": n + 1,
            "kind": "preference", "content": text}])
        self.store.set_setting("profile:group:201:101", {"version": n + 1,
            "profile": "LOW_VALUE_PROFILE " + str(n) + " " + "旧画像。" * 80})
        self.store.set_setting("group_state:group:201", {"summary_revision": n + 1,
            "summary": "LOW_VALUE_GROUP " + str(n) + " " + "旧群摘要。" * 80})
        ev = self.event(500 + n)
        self.store.grow(ev, "LOW_VALUE_GROWTH " + str(n) + " " + "公共成长参考。" * 40,
                        "合成证据", shared=True)
        with self.store.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS legacy_rows(
                origin TEXT,table_name TEXT,row_key TEXT,source_hash TEXT,
                imported_at REAL,data TEXT)""")
            conn.execute("DELETE FROM legacy_rows")
            conn.execute("INSERT INTO legacy_rows VALUES(?,?,?,?,?,?)", (
                "synthetic/history.db", "person_semantic_memory", "one", "hash", 1,
                json.dumps({"id": 3, "user_id": 101, "scope_group": 201,
                            "status": "active", "version": n + 1,
                            "content": "LOW_VALUE_LEGACY " + str(n) + " " + "旧导入参考。" * 80},
                           ensure_ascii=False),
            ))


def _messages(payload):
    return payload.get("messages", payload.get("input", []))


async def _sequence(api, root, *, budget, rounds, mutate):
    fixture = OfflineFixture(api, root, budget=budget)
    records, last, strict, same_epoch, same_strict = [], None, 0, 0, 0
    for n in range(1, rounds + 1):
        if mutate:
            fixture.mutate_low_value_sources(n)
        event = fixture.event(n)
        result = await fixture.turn(event)
        row = fixture.store.request(result.request_id)
        messages = _messages(result.payload)
        telemetry = row["telemetry"]
        epoch = telemetry.get("cache_spine_epoch")
        append = last is not None and messages[:len(last["messages"])] == last["messages"]
        if last:
            strict += int(append)
            if epoch == last["epoch"]:
                same_epoch += 1
                same_strict += int(append)
        encoded = json.dumps(messages, ensure_ascii=False)
        complete_pairs = all(index > 0 and messages[index - 1]["role"] == "user"
                             for index, item in enumerate(messages) if item["role"] == "assistant")
        records.append({"round": n, "epoch": epoch, "strict_append": append,
            "status": result.status, "message_count": len(messages),
            "estimated_tokens": api["context"].message_tokens(messages, fixture.config.profile()),
            "trimmed_turns": len(telemetry.get("trimmed_turn_ids", [])),
            "trimmed_events": len(telemetry.get("trimmed_event_keys", [])),
            "reset_reason": telemetry.get("cache_spine_reset_reason", ""),
            "action": telemetry.get("cache_spine_action", "legacy"),
            "complete_pairs": complete_pairs,
            "low_value_occurrences": sum(encoded.count(marker) for marker in (
                "LOW_VALUE_MEMORY", "LOW_VALUE_PROFILE", "LOW_VALUE_GROUP",
                "LOW_VALUE_GROWTH", "LOW_VALUE_LEGACY")),
        })
        last = {"messages": copy.deepcopy(messages), "epoch": epoch}
    next_event = fixture.event(rounds + 1)
    before = fixture.store.settings()
    first = api["context"].build_context(fixture.config, fixture.store, next_event,
                                         fixture.config.profile()).payload
    reopened = api["store"].Store(root)
    second = api["context"].build_context(fixture.config, reopened, next_event,
                                          fixture.config.profile()).payload
    return {"rounds": rounds, "strict_append_pairs": strict, "adjacent_pairs": rounds - 1,
            "strict_append_ratio": strict / (rounds - 1), "same_epoch_pairs": same_epoch,
            "same_epoch_strict_pairs": same_strict,
            "same_epoch_strict_ratio": same_strict / same_epoch if same_epoch else None,
            "sliding_rounds": sum(bool(row["trimmed_turns"] or row["trimmed_events"]) for row in records),
            "low_value_occurrences": sum(row["low_value_occurrences"] for row in records),
            "capacity_resets": sum(row["reset_reason"] == "capacity" for row in records),
            "all_complete_pairs": all(row["complete_pairs"] for row in records),
            "max_estimated_tokens": max(row["estimated_tokens"] for row in records),
            "budget": budget, "confirmed_turns": len(fixture.store.history("group:201")),
            "restart_same_input": first == second,
            "preview_did_not_mutate_settings": before == fixture.store.settings(),
            "mock_calls": len(fixture.calls), "records": records}


async def _visibility(api, root):
    fixture = OfflineFixture(api, root)
    secret = "SYNTHETIC_FORGET_MARKER"
    ev = fixture.event(1, text="本人资料 " + secret)
    fixture.store.remember(ev.session_key, ev.user_id, secret, event_key=ev.key, quote=secret)
    await fixture.turn(ev)
    fixture.store.forget(ev.session_key, ev.user_id, secret)
    forgotten = api["context"].build_context(fixture.config, fixture.store, fixture.event(2),
                                              fixture.config.profile())
    await fixture.turn(fixture.event(2))
    hidden = fixture.event(3, user=102, text="SYNTHETIC_FILTER_MARKER")
    await fixture.turn(hidden)
    database = importlib.import_module("tangtang_harness.business.db").Database
    business = database(root / "runtime" / "business.db")
    business.seed_groups((201,))
    business.add_filter_members("active", [102], 101)
    filtered = api["context"].build_context(fixture.config, fixture.store, fixture.event(4),
                                             fixture.config.profile())
    return {"forgotten_removed": secret not in json.dumps(forgotten.payload, ensure_ascii=False),
            "filtered_removed": "SYNTHETIC_FILTER_MARKER" not in json.dumps(filtered.payload, ensure_ascii=False),
            "forget_reset_reason": forgotten.telemetry.get("cache_spine_reset_reason", "legacy"),
            "filter_reset_reason": filtered.telemetry.get("cache_spine_reset_reason", "legacy"),
            "originals_preserved": len(fixture.store.history("group:201")) == 3}


async def _lifecycle(api, root):
    results = {}
    for outcome in ("silent", "failed"):
        fixture = OfflineFixture(api, root / outcome, response=outcome, fallback=outcome == "failed")
        event = fixture.event(1)
        response = await fixture.service.respond(event)
        results[outcome] = {"status": response.status,
            "pending": fixture.service.windows.session(event.session_key).turn_in_progress,
            "confirmed_turns": len(fixture.store.history(event.session_key)),
            "models": [wire["model"] for wire in fixture.calls],
            "usage_cache_read": response.usage.get("cache_read_tokens")}
        fixture.service.cancel_context_window(event.session_key)
    fixture = OfflineFixture(api, root / "cancel")

    class CancelClient:
        async def generate(self, *args, **kwargs):
            raise asyncio.CancelledError("synthetic cancellation")

    fixture.service.model_client = CancelClient()
    event = fixture.event(1)
    try:
        await fixture.service.respond(event)
    except asyncio.CancelledError:
        pass
    results["cancel"] = {"pending": fixture.service.windows.session(event.session_key).turn_in_progress,
                         "confirmed_turns": len(fixture.store.history(event.session_key)),
                         "outcome": fixture.store.requests()[0]["outcome"]}
    fixture.service.cancel_context_window(event.session_key)
    fixture = OfflineFixture(api, root / "background")
    event = fixture.event(1)
    await fixture.turn(event)
    before = len(fixture.calls)
    fixture.service.enqueue_memory(event)
    fixture.service.enqueue_summary(event.session_key)
    fixture.service.enqueue_compaction(event.session_key)
    fixture.store.enqueue_job("memory", event.session_key + ":101",
                              {"event": event.to_dict(), "source_session": event.session_key})
    for _ in range(8):
        if await fixture.service.run_background_once() is None:
            break
    results["background"] = {"mock_model_calls": len(fixture.calls) - before,
                             "jobs": [row["status"] for row in fixture.store.jobs()]}
    return results


async def _runtime_delivery(api, root):
    """Exercise the real runtime receipt/finally path with an in-memory QQ peer."""
    import httpx

    Runtime = importlib.import_module("tangtang_harness.runtime").Runtime
    RouteDecision = importlib.import_module("tangtang_harness.router").RouteDecision
    report = {}
    plain_raw = '{"decision":"reply","messages":["第一条合成答复","第二条合成答复"]}'
    for outcome in ("delivered", "partial", "failed", "cancelled", "disabled",
                    "collector_failed", "collector_cancelled",
                    "voice_collector_failed", "voice_collector_cancelled"):
        fixture = OfflineFixture(api, root / outcome)
        voice = outcome.startswith("voice_")
        raw = (json.dumps({"decision": "reply", "messages": ["合成语音文字答复"],
                           "voice": "accept", "speech_text": "这是合成语音。",
                           "text_fallback": ["合成语音文字回退"]}, ensure_ascii=False)
               if voice else plain_raw)
        config = replace(fixture.config, group_ids=(201,), continuation_enabled=False,
                         speech_enabled=voice,
                         extra={**fixture.config.extra, "test_prefix": "",
                                "isolated_scope_enabled": True})

        class ReceiptBot:
            self_id = 999
            connected = True

            def __init__(self):
                self.sent = []

            async def send(self, event, message):
                if outcome == "cancelled":
                    raise asyncio.CancelledError("synthetic receipt cancellation")
                if outcome == "failed" or outcome == "partial" and self.sent:
                    raise RuntimeError("synthetic receipt failure")
                self.sent.append(message)
                return {"message_id": "synthetic-" + str(len(self.sent))}

            def detach(self):
                self.connected = False

        bot = ReceiptBot()
        runtime = None

        def handler(request):
            if outcome == "disabled":
                runtime.config = replace(runtime.config, mention_chat_enabled=False)
            return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})

        runtime = Runtime(config, bot=bot,
                          model_client=api["models"].ModelClient(transport=httpx.MockTransport(handler)))
        runtime.tools.domains.ensure_group(201)
        collector_calls = []
        if "collector" in outcome:
            async def broken_collector(event, recorded_text, message_id):
                collector_calls.append(message_id)
                if outcome.endswith("cancelled"):
                    raise asyncio.CancelledError("synthetic outgoing collector cancellation")
                raise RuntimeError("synthetic outgoing collector failure")
            runtime.tools.collect_outgoing = broken_collector
        if voice:
            async def synthesize(text, options):
                path = config.root / "synthetic.wav"
                path.write_bytes(b"RIFFsynthetic-acceptance-only")
                return path
            runtime.speech.ready = True
            runtime.speech.synthesize = synthesize
        event = fixture.event(1, text="娅娅用语音回答" if voice else "娅娅请解释这两个问题")
        error = ""
        try:
            await runtime.process(event, RouteDecision("chat"))
        except (RuntimeError, asyncio.CancelledError) as exc:
            error = type(exc).__name__
        request = runtime.store.requests()[0]
        key = request["telemetry"].get("cache_state_key")
        state = runtime.store.get_setting(key, {}) if key else {}
        items = state.get("items", [])
        receipts = [json.loads(item["content"].split("\n", 1)[1])
                    for item in items if item.get("kind") == "delivery"]
        history = runtime.store.history(event.session_key)
        report[outcome] = {
            "pending": runtime.chat.windows.session(event.session_key).turn_in_progress,
            "confirmed_turns": len(history),
            "confirmed_messages": history[0]["messages"] if history else [],
            "fake_peer_sent": len(bot.sent), "error": error,
            "collector_calls": len(collector_calls),
            "fake_peer_message_types": [getattr(item, "type", "text") for item in bot.sent],
            "raw_output_preserved": any(item.get("kind") == "generated" and item["content"] == raw
                                        for item in items),
            "receipt_facts": receipts,
        }
        # Never start transports or workers. close only releases this temporary runtime.
        await runtime.close()
    return report


async def observe(api, root):
    return {"contract": "cache-spine-offline-v1", "real_network_calls": 0,
        "provider_cache_hit_ratio": None,
        "note": "Local strict-prefix reuse only; upstream cache usage stays unknown.",
        "stable": await _sequence(api, root / "stable", budget=100_000, rounds=12, mutate=True),
        "rollover": await _sequence(api, root / "rollover", budget=3_500, rounds=20, mutate=False),
        "visibility": await _visibility(api, root / "visibility"),
        "lifecycle": await _lifecycle(api, root / "lifecycle"),
        "runtime_delivery": await _runtime_delivery(api, root / "runtime-delivery")}


def run_observation(source: Path, root: Path | None = None):
    api = load_target(source)

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline acceptance attempted a real network connection")

    # Windows' event loop creates a local socketpair during initialization.
    # Initialize it first; prohibit connections while application code runs.
    with asyncio.Runner() as runner:
        runner.get_loop()
        with patch.object(socket, "create_connection", forbidden), patch.object(socket.socket, "connect", forbidden):
            if root is not None:
                return runner.run(observe(api, root))
            with tempfile.TemporaryDirectory(prefix="harness-cache-acceptance-") as temporary:
                return runner.run(observe(api, Path(temporary)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_observation(args.source)
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
