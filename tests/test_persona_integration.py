from __future__ import annotations

import asyncio
import io
import json
import wave
from dataclasses import replace
from types import SimpleNamespace
from functools import wraps

import pytest
from nonebot.adapters.onebot.v11 import Message

from bot.services.chat_dispatch import ChatDispatcher
from bot.services.persona_engine import PersonaEngine
from bot.services.persona_profiles import VoiceProfile
from bot.services.persona_store import PersonaStore
from bot.services.speech import SpeechService
from bot.services.speech_policy import choose_delivery
from bot.services.tangtang_chat import AgentResult, TangtangConfig, TangtangService
from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_reply import ReplyPlan, parse_reply_plan


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 1600)
    return output.getvalue()


class Backend:
    def __init__(self):
        self.calls = []
        self.wait = None
        self.fail = False

    async def health(self, voice):
        return True

    async def synthesize(self, text, voice):
        self.calls.append((text, voice.key))
        if self.wait:
            await self.wait.wait()
        if self.fail:
            raise RuntimeError("synthetic TTS failure")
        return wav_bytes()


def make_runtime(tmp_path):
    store = PersonaStore(tmp_path / "state.db")
    backend = Backend()
    speech = SpeechService(store, backend, tmp_path / "audio", timeout=0.08)
    speech.profiles["dania"] = VoiceProfile("dania", "http://127.0.0.1:9880", tmp_path / "ref.wav", "示例", "model1", "ref1")
    speech.bindings["denia"] = "dania"
    speech.ready.add("dania")
    engine = PersonaEngine(store, speech, feature_enabled=lambda *_: True, chat_enabled=lambda *_: True)
    return engine, backend


def event(text="娅娅，今天过得怎么样", *, group=1001, message=1):
    return SimpleNamespace(group_id=group, user_id=2001, message_id=message, self_id=3001,
        message=Message(text), original_message=Message(text), get_plaintext=lambda: text,
        is_tome=lambda: False, sender=SimpleNamespace(card="", nickname="群友"), reply=None)


class Provider:
    def __init__(self, response, after=None):
        self.response, self.after, self.seen = response, after, []

    async def generate_agent(self, config, persona, prompt, *args):
        self.seen.append((persona, prompt))
        if self.after:
            self.after()
        return AgentResult(json.dumps(self.response, ensure_ascii=False), (), {})


def service_for(tmp_path, engine, provider):
    config = TangtangConfig.from_values({
        "TANGTANG_ENABLED": "true", "TANGTANG_MODE": "d", "TANGTANG_GROUP_IDS": "1001,1002",
        "TANGTANG_API_URL": "https://example.invalid/responses", "TANGTANG_API_KEY": "test-only",
        "TANGTANG_MODEL": "synthetic", "TANGTANG_IGNORE_PROBABILITY": "0",
        "TANGTANG_VISION_ENABLED": "false", "TANGTANG_REPLY_DELAY_MIN_MS": "0",
        "TANGTANG_REPLY_DELAY_MAX_MS": "0",
    }, (1001, 1002))
    service = TangtangService(loader=SimpleNamespace(load=lambda: config),
        db=TangtangDb(tmp_path / "tangtang.db"), provider=provider,
        persona_engine=engine, usage_dir=tmp_path / "usage")
    return service, config


@pytest.mark.parametrize("user_request,choice,body,available,candidate,expected", [
    ("发语音吧", "decline", "今天不想发语音", True, True, False),
    ("发语音吧", "accept", "今天不想发语音", True, True, False),
    ("发语音吧", "accept", "今天不想说话", True, True, False),
    ("发语音吧", "accept", "天气真好", True, False, True),
    ("不要语音，文字就好", "accept", "天气真好", True, True, False),
    ("今天怎么样", "auto", "天气真好", True, True, True),
    ("今天怎么样", "auto", "天气真好", True, False, False),
    ("发语音吧", "accept", "天气真好", False, True, False),
    ("发语音吧", "accept", "语音已经发好了", True, True, False),
    ("今天怎么样", "auto", "看这个 https://example.invalid", True, True, False),
    ("今天怎么样", "text", "天气真好", True, True, False),
])
def test_delivery_priorities(user_request, choice, body, available, candidate, expected):
    plan = ReplyPlan(True, (body,), choice, ("今天挺好的。",), structured=True)
    result = choose_delivery(plan, user_request, available=available, random_candidate=candidate)
    assert result.voice is expected


def test_malformed_control_is_not_published():
    assert not parse_reply_plan('{"decision":"reply",broken}').decided
    assert not choose_delivery(ReplyPlan(False, ()), "发语音", available=True, random_candidate=True).voice
    assert not choose_delivery(ReplyPlan(True, ("今天挺好",)), "今天如何", available=True, random_candidate=True).voice
    assert not parse_reply_plan(json.dumps({"decision":"reply", "messages":["你好"], "text_fallback":["[CQ:at,qq=all]"]})).decided
    oversized = parse_reply_plan(json.dumps({"decision":"reply", "messages":["好" * 400], "voice":"accept"}), max_chars=30)
    assert not choose_delivery(oversized, "发语音吧", available=True, random_candidate=True).voice


def test_selection_revisions_and_storage_isolate_personas(tmp_path):
    engine, _ = make_runtime(tmp_path)
    initial = engine.snapshot(event(), "model", False)
    assert initial.persona.key == "tangtang"
    engine.store.switch(1001, "denia")
    assert not engine.current(initial)
    assert engine.profile(1002).key == "tangtang"
    base = TangtangDb(tmp_path / "old.db")
    assert engine.history("denia", base).path != base.path
    engine.store.switch(1001, "tangtang")
    assert not engine.current(initial)
    assert engine.history("tangtang", base) is base


def test_chat_configuration_change_invalidates_unsent_turn(tmp_path):
    engine, _ = make_runtime(tmp_path)
    version = "config-one"
    engine.configuration_version = lambda:version
    frozen = engine.snapshot(event(), "model", False)
    assert engine.current(frozen)
    version = "config-two"
    assert not engine.current(frozen)


def test_voice_available_by_binding_not_persona_name(tmp_path):
    engine, _ = make_runtime(tmp_path)
    speech = engine.speech
    assert speech.status("tangtang", 1001) == "未绑定声线"
    speech.bindings["tangtang"] = "dania"
    assert speech.status("tangtang", 1001) == "可用"
    engine.store.set_option("speech_enabled", False)
    assert speech.status("denia", 1001) == "已关闭"


@pytest.mark.parametrize("ignore_probability", [0.0, 1.0])
@async_test
async def test_accepted_voice_replaces_text_and_records_only_delivered(tmp_path, monkeypatch, ignore_probability):
    engine, backend = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    provider = Provider({"decision": "reply", "messages": ["今天也有好好休息呢。"], "voice": "accept", "text_fallback": ["今天也有好好休息呢。"], "expression": "smile"})
    service, config = service_for(tmp_path, engine, provider)
    config = replace(config, call_ignore_probability_by_group={1001: ignore_probability})
    sent = []
    async def send(bot, action, **params):
        sent.append(params["message"])
        return {"message_id": 77}
    monkeypatch.setattr("bot.services.speech.call_qq_action", send)
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await service.handle(None, event("娅娅，发语音聊聊今天吧"), config)
    assert len(sent) == 1 and sent[0].type == "record"
    assert backend.calls[0][0] == "今天也有好好休息呢。"
    denia_db = engine.history("denia", service.db)
    assert denia_db.list_calls(2001, 1001)[0]["reply_text"] == "今天也有好好休息呢。"
    assert service.db.list_calls(2001, 1001) == []
    assert len(engine.store.interactions("denia", 1001)) == 1
    await engine.speech.close()


@async_test
async def test_switch_during_generation_discards_old_persona(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    provider = Provider({"decision": "reply", "messages": ["我在呢"], "voice": "text"}, after=lambda: engine.store.switch(1001, "tangtang"))
    service, config = service_for(tmp_path, engine, provider)
    async def must_not_send(*args, **kwargs):
        pytest.fail("stale persona sent")
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", must_not_send)
    await service.handle(None, event(), config)
    assert not service.db.list_calls(2001, 1001)
    assert not engine.store.interactions("denia", 1001)


@async_test
async def test_synthesis_timeout_keeps_worker_single_and_never_sends_late(tmp_path, monkeypatch):
    engine, backend = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    backend.wait = asyncio.Event()
    sent = []
    async def send(*args, **kwargs):
        sent.append(kwargs)
        return {"message_id": 99}
    monkeypatch.setattr("bot.services.speech.call_qq_action", send)
    first = await engine.speech.deliver(None, engine.snapshot(event(), "model", False), "早上好", explicit=True, current=lambda: True)
    second_task = asyncio.create_task(engine.speech.deliver(None, engine.snapshot(event(message=2), "model", False), "晚上好", explicit=True, current=lambda: True))
    await asyncio.sleep(0.01)
    assert len(backend.calls) == 1
    second = await second_task
    backend.wait.set()
    await asyncio.sleep(0.02)
    assert first.status == second.status == "failed"
    assert not sent and len(backend.calls) == 1
    await engine.speech.close()


@async_test
async def test_unknown_send_is_journaled_without_retry(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    context = engine.snapshot(event(), "model", False)
    calls = []
    async def ambiguous(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("no acknowledgement")
    monkeypatch.setattr("bot.services.speech.call_qq_action", ambiguous)
    result = await engine.speech.deliver(None, context, "你好", explicit=True, current=lambda: True)
    assert result.status == "uncertain"
    again = await engine.speech.deliver(None, context, "你好", explicit=True, current=lambda: True)
    assert again.status == "duplicate" and len(calls) == 1
    assert engine.store.delivery(context.request_id)["status"] == "uncertain"
    await engine.speech.close()


@pytest.mark.parametrize("mode", ["decline", "synthesis_failure", "unknown_text", "explicit_text"])
@async_test
async def test_fallback_and_refusal_never_create_phantom_voice(tmp_path, monkeypatch, mode):
    engine, backend = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    engine.store.set_option("speech_probability", 1)
    backend.fail = mode == "synthesis_failure"
    provider = Provider({"decision":"reply", "messages":["今天不想说话" if mode == "decline" else "今天挺好的"],
        "voice":"decline" if mode == "decline" else "accept", "text_fallback":["今天挺好的"]})
    service, config = service_for(tmp_path, engine, provider)
    sent = []
    async def send(*args, **kwargs):
        sent.append(kwargs["message"])
        return None if mode == "unknown_text" else {"message_id":88}
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    monkeypatch.setattr("bot.services.speech.call_qq_action", send)
    text = "娅娅，不要语音，文字就好" if mode in {"explicit_text", "unknown_text"} else "娅娅，发语音吧"
    await service.handle(None, event(text), config)
    assert sent and all(not getattr(part, "type", "") == "record" for part in sent)
    assert len(backend.calls) == (1 if mode == "synthesis_failure" else 0)
    assert len(engine.store.interactions("denia", 1001)) == (0 if mode == "unknown_text" else 1)
    if mode == "decline":
        assert "不想说话" in str(sent[0])
    if mode == "synthesis_failure":
        assert "没合成好" in str(sent[0])
    await engine.speech.close()


@async_test
async def test_event_claim_survives_persona_switch_and_store_reopen(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    provider = Provider({"decision":"reply", "messages":["你好呀"], "voice":"text"})
    service, config = service_for(tmp_path, engine, provider)
    sent = []
    async def send(*args, **kwargs):
        sent.append(kwargs)
        return {"message_id":81}
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await service.handle(None, event("糖糖，你好吗"), config)
    engine.store.switch(1001, "denia")
    engine.store = PersonaStore(engine.store.path)
    await service.handle(None, event("娅娅，你好吗"), config)
    assert len(sent) == 1


@async_test
async def test_simultaneous_groups_keep_frozen_personas(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    entered = asyncio.Event()
    class ConcurrentProvider:
        count = 0
        async def generate_agent(self, config, persona, prompt, *args):
            self.count += 1
            if self.count == 2:
                entered.set()
            await entered.wait()
            return AgentResult(json.dumps({"decision":"reply", "messages":[config.call_keyword + "在这里"], "voice":"text"}), (), {})
    service, config = service_for(tmp_path, engine, ConcurrentProvider())
    sent = {}
    async def send(*args, **kwargs):
        sent[kwargs["group_id"]] = str(kwargs["message"])
        return {"message_id":82}
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await asyncio.wait_for(asyncio.gather(service.handle(None, event(), config),
        service.handle(None, event("糖糖，聊聊今天", group=1002), config)), 5)
    assert "娅娅" in sent[1001] and "糖糖" in sent[1002]
    assert not engine.store.interactions("tangtang", 1001)
    assert not engine.store.interactions("denia", 1002)


@async_test
async def test_dispatch_does_not_hold_event_path_and_limits_groups():
    dispatcher = ChatDispatcher(limit=2)
    wait = asyncio.Event()
    async def slow():
        await wait.wait()
    assert dispatcher.submit(1, slow)
    assert not dispatcher.submit(1, slow)
    assert dispatcher.submit(2, slow)
    assert not dispatcher.submit(3, slow)
    await dispatcher.close()
