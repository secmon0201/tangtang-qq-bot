from __future__ import annotations

import asyncio
import io
import json
import time
import wave
from dataclasses import replace
from types import SimpleNamespace
from functools import wraps

import pytest
from nonebot.adapters.onebot.v11 import ActionFailed, Message
from PIL import Image

from bot.services.chat_dispatch import ChatDispatcher
from bot.services.persona_engine import PersonaEngine
from bot.services.persona_expressions import expression_key, expression_request
from bot.services.persona_profiles import VoiceProfile
from bot.services.persona_store import PersonaStore
from bot.services.speech import SpeechService
from bot.services.speech_policy import choose_delivery
from bot.services.tangtang_chat import (
    AgentResult,
    PRIVATE_CONTINUATION_REPLY,
    TangtangConfig,
    TangtangService,
)
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


def make_locked_runtime(tmp_path):
    engine, backend = make_runtime(tmp_path)
    engine.locked_persona = "denia"
    return engine, backend


def event(text="娅娅，今天过得怎么样", *, group=1001, message=1):
    return SimpleNamespace(group_id=group, user_id=2001, message_id=message, self_id=3001,
        message=Message(text), original_message=Message(text), get_plaintext=lambda: text,
        is_tome=lambda: False, sender=SimpleNamespace(card="", nickname="群友"), reply=None)


def private_event(
    text="今天过得怎么样",
    *,
    message=1,
    sub_type="friend",
    source_group=0,
    sender_group_only=False,
):
    value = event(text, group=0 if sender_group_only else source_group, message=message)
    value.message_type = "private"
    value.sub_type = sub_type
    if sender_group_only:
        value.sender.group_id = source_group
    return value


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


@pytest.mark.parametrize("memory_enabled", [True, False])
@async_test
async def test_memory_confirmation_follows_actual_persistence_before_send(tmp_path, monkeypatch, memory_enabled):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    statement = "我上周参加了绘画展"
    provider = Provider({"decision": "reply", "messages": ["我已经记住了！"], "voice": "text",
        "memory_updates": [{"category": "experience", "quote": statement, "summary": statement, "tags": ["创作"]}]})
    service, config = service_for(tmp_path, engine, provider)
    config = replace(config, memory_enabled=memory_enabled, call_ignore_probability_by_group={1001: 1.0})
    memory = engine.memory("denia", service._base_db, service._now)
    sent = []
    async def send(bot, action, **params):
        rows = memory.semantic.recall(1001, 2001, "创作经历")
        assert bool(rows) is memory_enabled
        sent.append(str(params["message"]))
        return {"message_id": 91}
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await service.handle(None, event("娅娅，请记住" + statement), config)
    assert len(provider.seen) == 1 and sent
    assert "我已经记住了" not in "".join(sent)
    assert ("已经保存" if memory_enabled else "没有开启") in "".join(sent)


@pytest.mark.parametrize("acknowledged", [True, False])
@async_test
async def test_automatic_personal_episode_requires_delivery_ack(tmp_path, monkeypatch, acknowledged):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    statement = "我上周参加了绘画展"
    provider = Provider({"decision": "reply", "messages": ["展出了什么呀？"], "voice": "text",
        "memory_updates": [{"category": "experience", "quote": statement, "summary": statement, "tags": ["创作"]}]})
    service, config = service_for(tmp_path, engine, provider)
    memory = engine.memory("denia", service._base_db, service._now)
    async def send(bot, action, **params):
        assert not memory.semantic.recall(1001, 2001, "创作经历")
        return {"message_id": 92} if acknowledged else {}
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await service.handle(None, event("娅娅，" + statement), config)
    assert bool(memory.semantic.recall(1001, 2001, "创作经历")) is acknowledged


@async_test
async def test_false_memory_promise_never_reaches_tts_or_text_fallback(tmp_path, monkeypatch):
    engine, backend = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    provider = Provider({"decision": "reply", "messages": ["我已经记住了。"],
        "voice": "accept", "text_fallback": ["永远记得。"]})
    service, config = service_for(tmp_path, engine, provider)
    sent = []
    async def send(bot, action, **params):
        sent.append(params["message"])
        return {"message_id": 93}
    monkeypatch.setattr("bot.services.speech.call_qq_action", send)
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await service.handle(None, event("娅娅，记住这件事，用语音回答"), config)
    contents = [text for text, _voice in backend.calls] + [str(message) for message in sent]
    assert not any("我已经记住了" in text or "永远记得" in text for text in contents)
    assert any("没有保存" in text for text in contents)
    await engine.speech.close()


@pytest.mark.parametrize("body", ["好，我记着这件事。", "咖啡那条翻篇了，以后只认茶。", "已经记牢啦。"])
def test_explicit_memory_receipt_cannot_contradict_unseen_acknowledgment(body):
    from bot.services.persona_memory_contract import MemoryWriteResult, enforce_memory_confirmation
    failed = MemoryWriteResult(True, "failed")
    assert enforce_memory_confirmation((body,), failed) == (failed.receipt,)


def test_merged_claims_are_atomic_and_cannot_partially_consume_new_messages(tmp_path):
    store = PersonaStore(tmp_path / "state.db")
    assert store.claim_requests(("1001:1", "1001:2"), 100)
    assert not store.claim_requests(("1001:3", "1001:2"), 101)
    assert store.claim_request("1001:3", 102)


@pytest.mark.parametrize("text,expected", [
    ("娅娅发个表情包", "explicit"), ("娅娅来张思考表情", "explicit"),
    ("给我一个表情包", "explicit"), ("表情包来一个", "explicit"),
    ("不要发个表情包就敷衍我", "none"), ("别发那么多表情", "none"),
    ("表情包的概率太高了", "ordinary"), ("为什么发这么多表情", "ordinary"),
])
def test_expression_intent(text, expected):
    assert expression_request(text) == expected


def test_ordinary_expression_probability_is_sixty_percent():
    assert sum(bool(expression_key("今天好", "smile", n / 100)) for n in range(100)) == 60
    assert expression_key("别发表情", "smile", 0) == ""
    assert expression_key("今天好", "", 0) == ""
    assert expression_key("发个探头表情包", "", 0.999) == "peek"


@pytest.mark.parametrize("suffix", [".gif", ".webp", ".png", ".jpg", ".jpeg"])
def test_character_expression_sends_original_image(tmp_path, suffix):
    engine, _ = make_runtime(tmp_path)
    engine.profiles["denia"] = replace(engine.profiles["denia"], resource_dir=tmp_path)
    engine.store.switch(1001, "denia")
    context = engine.snapshot(event(), "model", False)
    folder = tmp_path / "expressions"
    folder.mkdir()
    path = folder / f"smile{suffix}"
    frame = Image.new("RGB", (8, 8), "red")
    options = {"save_all": True, "append_images": [Image.new("RGB", (8, 8), "blue")],
               "duration": 120, "loop": 0} if suffix == ".gif" else {}
    frame.save(path, **options)
    original = path.read_bytes()
    segment = engine.expression(context, "smile")
    assert segment.type == "image"
    assert segment.data["file"] == path.resolve().as_uri()
    assert path.read_bytes() == original
    if suffix == ".gif":
        with Image.open(path) as image:
            assert image.n_frames == 2
            assert image.info["duration"] == 120
    assert engine.expression(context, "think") is None
    assert engine.expression(context, "../smile") is None
    engine.feature_enabled = lambda *_: False
    assert engine.expression(context, "smile") is None


def test_character_expression_prefers_animation_over_static_asset(tmp_path):
    engine, _ = make_runtime(tmp_path)
    engine.profiles["denia"] = replace(engine.profiles["denia"], resource_dir=tmp_path)
    engine.store.switch(1001, "denia")
    context = engine.snapshot(event(), "model", False)
    folder = tmp_path / "expressions"
    folder.mkdir()
    for suffix in (".jpeg", ".jpg", ".png", ".webp", ".gif"):
        path = folder / f"smile{suffix}"
        Image.new("RGB", (8, 8), "red").save(path)
    assert engine.expression(context, "smile").data["file"] == (folder / "smile.gif").resolve().as_uri()
    engine.store.switch(1001, "tangtang")
    tangtang = engine.snapshot(event(), "model", False)
    assert engine.expression(tangtang, "smile").type == "face"


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("roll", [0.0, 0.5, 0.999])
@async_test
async def test_requested_expression_bypasses_random_ignore_and_model(tmp_path, monkeypatch, enabled, roll):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    engine.feature_enabled = lambda group, feature: enabled if feature == "persona_expressions" else True
    provider = Provider({"decision": "silent", "messages": []})
    service, config = service_for(tmp_path, engine, provider)
    config = replace(config, call_ignore_probability_by_group={1001: 1.0})
    sent = []
    async def send(bot, action, **params):
        sent.append(params["message"])
        return {"message_id": 77}
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    monkeypatch.setattr("bot.services.tangtang_chat.random.random", lambda: roll)
    await service.handle(None, event("娅娅发个探头表情包"), config)
    assert not provider.seen
    assert len(sent) == 1
    if enabled:
        assert [segment.type for segment in sent[0]] == ["reply", "text", "image"]
        assert sent[0].extract_plain_text() == "给你。"
        assert "peek.jpg" in str(sent[0])
    else:
        assert "没有可用" in str(sent[0])
    assert engine.history("denia", service.db).list_calls(2001, 1001)[0]["reply_text"] == (
        "给你。" if enabled else "现在没有可用的角色表情。")


@async_test
async def test_private_requested_expression_uses_private_delivery(tmp_path, monkeypatch):
    engine, _ = make_locked_runtime(tmp_path)
    provider = Provider({"decision": "silent", "messages": []})
    service, config = service_for(tmp_path, engine, provider)
    sent = []

    async def send(_bot, action, **params):
        sent.append((action, params))
        return {"message_id": 77}

    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await service.handle(None, private_event("发一个大笑表情包"), config)

    assert not provider.seen
    assert len(sent) == 1
    assert sent[0][0] == "send_private_msg"
    assert sent[0][1]["user_id"] == 2001
    assert "group_id" not in sent[0][1]
    assert [segment.type for segment in sent[0][1]["message"]] == ["text", "image"]


@async_test
async def test_group_temporary_private_delivery_includes_source_group(tmp_path, monkeypatch):
    engine, _ = make_locked_runtime(tmp_path)
    provider = Provider({"decision": "silent", "messages": []})
    service, config = service_for(tmp_path, engine, provider)
    sent = []

    async def send(_bot, action, **params):
        sent.append((action, params))
        return {"message_id": 77}

    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await service.handle(
        None,
        private_event(
            "发一个大笑表情包",
            sub_type="group",
            source_group=1001,
            sender_group_only=True,
        ),
        config,
    )

    assert not provider.seen
    assert len(sent) == 1
    assert sent[0][0] == "send_private_msg"
    assert sent[0][1]["user_id"] == 2001
    assert sent[0][1]["group_id"] == 1001
    assert [segment.type for segment in sent[0][1]["message"]] == ["text", "image"]


@async_test
async def test_private_invalid_model_structure_gets_safe_fallback(tmp_path, monkeypatch):
    engine, _ = make_locked_runtime(tmp_path)

    class InvalidProvider:
        calls = 0

        async def generate_agent(self, *_args, **_kwargs):
            self.calls += 1
            return AgentResult("这不是结构化回复", (), {})

    provider = InvalidProvider()
    service, config = service_for(tmp_path, engine, provider)
    monkeypatch.setattr(service, "_turn_current", lambda: True)
    sent = []

    async def send(_bot, action, **params):
        sent.append((action, params))
        return {"message_id": 78}

    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await service.handle(None, private_event("来一张美图"), config)

    assert provider.calls == 1
    assert len(sent) == 1
    assert sent[0][0] == "send_private_msg"
    assert sent[0][1]["message"] == "刚刚没组织好，再说一次吧。"


@async_test
async def test_private_persona_model_silence_gets_text_fallback(tmp_path, monkeypatch):
    engine, _ = make_locked_runtime(tmp_path)
    provider = Provider({"decision": "silent", "messages": []})
    service, config = service_for(tmp_path, engine, provider)
    sent = []

    async def send(_bot, action, **params):
        sent.append((action, params))
        return {"message_id": 79}

    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    await service.handle(None, private_event("陪我聊聊"), config)

    assert len(provider.seen) == 1
    assert len(sent) == 1
    assert sent[0][0] == "send_private_msg"
    assert sent[0][1]["message"] == PRIVATE_CONTINUATION_REPLY


@async_test
async def test_uncertain_combined_expression_is_not_sent_again(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    service, config = service_for(tmp_path, engine, Provider({}))
    sent = []
    async def send(bot, action, **params):
        sent.append(params["message"])
        return {}
    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
    monkeypatch.setattr("bot.services.tangtang_chat.random.random", lambda: 0.0)
    await service.handle(None, event("娅娅发个表情包"), config)
    assert len(sent) == 1
    assert [segment.type for segment in sent[0]] == ["reply", "text", "image"]
    assert not engine.history("denia", service.db).list_calls(2001, 1001)
    assert engine.store.delivery("1001:1")["status"] == "uncertain"
    with engine.store.connect() as conn:
        assert conn.execute("SELECT status FROM expression_events WHERE request_id='1001:1'").fetchone()[0] == 'uncertain'
    assert not engine.expressions.history(engine.snapshot(event(), 'model', False), time.time())[0]


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


def test_denia_lock_ignores_old_and_future_tangtang_selections(tmp_path):
    engine, _ = make_locked_runtime(tmp_path)
    assert engine.store.selection(1001) == ("tangtang", 0)
    assert engine.profile(1001).key == "denia"
    frozen = engine.snapshot(event(), "model", False)
    assert frozen.persona.key == "denia"

    engine.store.switch(1001, "tangtang")

    assert engine.profile(1001).key == "denia"
    assert engine.current(frozen)
    assert "tangtang" in engine.profiles


def test_persona_lock_rejects_unknown_profile(tmp_path):
    engine, _ = make_runtime(tmp_path)
    with pytest.raises(ValueError, match="unknown locked persona"):
        PersonaEngine(
            engine.store,
            engine.speech,
            feature_enabled=lambda *_: True,
            chat_enabled=lambda *_: True,
            locked_persona="missing",
        )


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
    records = [json.loads(line) for path in (tmp_path / "usage").glob("*.jsonl")
               for line in path.read_text("utf-8").splitlines()]
    assert len({row["request_trace"] for row in records}) == 1
    assert records[0]["request_trace"]
    assert all("group_id" not in row and "user_id" not in row for row in records)
    assert all("request_id" not in row and "message_id" not in row for row in records)
    assert {"model_started", "model_result", "send_result", "reply"} <= {row["event"] for row in records}
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
async def test_correction_in_other_group_cancels_generated_reply(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, 'denia')
    provider = Provider({'decision':'reply', 'messages':['你喜欢草莓'], 'voice':'text'})
    service, config = service_for(tmp_path, engine, provider)
    memory = engine.memory('denia', service._base_db, service._now)
    memory.observe_user_message(group_id=1001, user_id=2001, message_id='old', text='记住我喜欢草莓')
    provider.after = lambda: memory.observe_user_message(group_id=1002, user_id=2001,
        message_id='new', text='我现在不喜欢草莓了')
    async def forbidden(*args, **kwargs):
        pytest.fail('reply containing pre-correction memory was sent')
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', forbidden)
    await service.handle(None, event('娅娅，我喜欢什么'), config)
    assert not engine.store.interactions('denia', 1001)


@async_test
async def test_shared_person_prompt_keeps_each_groups_current_topic(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, 'denia')
    engine.store.switch(1002, 'denia')
    provider = Provider({'decision':'silent', 'messages':[]})
    service, config = service_for(tmp_path, engine, provider)
    memory = engine.memory('denia', service._base_db, service._now)
    memory.observe_user_message(group_id=1001, user_id=2001, message_id='old', text='记住我喜欢草莓')
    service._base_db.insert_group_message(group_id=1001, user_id=2001, nickname='团长', text='游戏配队话题', message_id='a', created_at=service._now())
    service._base_db.insert_group_message(group_id=1002, user_id=2001, nickname='小明', text='今天晚饭话题', message_id='b', created_at=service._now())
    await service.handle(None, event('娅娅，你觉得呢', group=1002), config)
    prompt = provider.seen[0][1]
    assert '喜欢草莓' not in prompt and '今天晚饭话题' in prompt
    assert '游戏配队话题' not in prompt


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


@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("saved_limits", [(12, 60), (0, 0)])
@async_test
async def test_local_speech_remains_available_after_legacy_daily_caps(tmp_path, monkeypatch, explicit, saved_limits):
    engine, backend = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    engine.store.set_option("speech_group_limit", saved_limits[0])
    engine.store.set_option("speech_global_limit", saved_limits[1])
    day = engine.store.day(time.time())
    with engine.store.connect() as conn:
        conn.executemany("INSERT INTO budgets(day,kind,scope,used) VALUES(?,'speech',?,?)",
                         [(day, "global", 600), (day, "group:1001", 120)])
    sent = []
    async def send(*args, **kwargs):
        sent.append(kwargs["message"])
        return {"message_id": 99}
    monkeypatch.setattr("bot.services.speech.call_qq_action", send)
    assert engine.speech.status("denia", 1001) == "可用"
    result = await engine.speech.deliver(None, engine.snapshot(event(), "model", False),
        "你好", explicit=explicit, current=lambda: True)
    assert result.status == "delivered" and len(sent) == len(backend.calls) == 1
    assert engine.speech.status("denia", 1001) == "可用"
    if not explicit:
        assert not engine.speech.random_candidate(1001, 0)
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


@pytest.mark.parametrize("roll", [0.1, 0.9])
@pytest.mark.parametrize("outcome", ["delivered", "missing_ack", "timeout", "failed"])
@async_test
async def test_expression_acknowledgements_control_usage(tmp_path, monkeypatch, roll, outcome):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, "denia")
    service, config = service_for(tmp_path, engine, Provider({}))
    attempts = []
    async def send(bot, action, **params):
        msg = params['message']
        image = getattr(msg, 'type', '') == 'image' or (isinstance(msg, Message) and bool(msg['image']))
        if image:
            assert isinstance(msg, Message) and msg['text']
            attempts.append(str(msg))
            if outcome == 'missing_ack':
                return {}
            if outcome == 'timeout':
                raise TimeoutError('test-only')
            if outcome == 'failed':
                raise ActionFailed(retcode=100, msg='test-only')
        return {'message_id': 77}
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', send)
    monkeypatch.setattr('bot.services.tangtang_chat.random.random', lambda: roll)
    await service.handle(None, event('娅娅发个手动微笑表情'), config)
    assert len(attempts) == 1 and 'expr_026.gif' in attempts[0]
    with engine.store.connect() as conn:
        row = conn.execute('SELECT * FROM expression_events').fetchone()
    assert row['selected_id'] == 'expr_026'
    assert row['status'] == ('uncertain' if outcome in {'missing_ack', 'timeout'} else outcome)
    counts, _ = engine.expressions.history(engine.snapshot(event(), 'model', False), time.time())
    assert counts.get('expr_026', 0) == (1 if outcome == 'delivered' else 0)


@async_test
async def test_persona_switch_after_combined_send_does_not_send_another_expression(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, 'denia')
    service, config = service_for(tmp_path, engine, Provider({}))
    sent = []
    async def send(bot, action, **params):
        sent.append(params['message'])
        engine.store.switch(1001, 'tangtang')
        return {'message_id': 77}
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', send)
    monkeypatch.setattr('bot.services.tangtang_chat.random.random', lambda: .9)
    await service.handle(None, event('娅娅发个手动微笑表情'), config)
    assert len(sent) == 1 and isinstance(sent[0], Message) and sent[0]['image'] and sent[0]['text']
    with engine.store.connect() as conn:
        assert conn.execute('SELECT status FROM expression_events').fetchone()[0] == 'delivered'


@async_test
async def test_dispatch_does_not_hold_event_path_and_limits_groups():
    dispatcher = ChatDispatcher(limit=2)
    wait = asyncio.Event()
    async def slow():
        await wait.wait()
    assert dispatcher.submit(1, slow)
    assert not dispatcher.submit(1, slow, proactive=True)
    assert dispatcher.submit(2, slow)
    assert not dispatcher.submit(3, slow)
    await dispatcher.close()


@async_test
async def test_recognition_prompt_does_not_cross_groups(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, 'denia')
    engine.store.switch(1002, 'denia')
    provider=Provider({'decision':'reply','messages':['你之前自称鸣潮高手呀。'],'voice':'text'})
    service,config=service_for(tmp_path,engine,provider)
    history=engine.history('denia',service._base_db)
    history.insert_call(group_id=1001,user_id=2001,message_id='earlier',call_text='我是鸣潮高手',
                        reply_text='嗯',reply_kind='model',mode='d',created_at='2026-09-17T19:00:00+08:00')
    async def send(*args,**kwargs):
        return {'message_id':123}
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action',send)
    await service.handle(None,event('娅娅，还记得我是谁吗',group=1002,message=20),config)
    assert provider.seen
    assert '用户本人曾说：我是鸣潮高手' not in provider.seen[0][1]
    assert '我是鸣潮高手' not in provider.seen[0][1]


@async_test
async def test_matching_topic_never_imports_another_groups_conversation(tmp_path):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, 'denia')
    engine.store.switch(1002, 'denia')
    provider = Provider({'decision':'silent','messages':[]})
    service,config = service_for(tmp_path,engine,provider)
    memory=engine.memory('denia',service._base_db,service._now)
    memory.observe_user_message(group_id=1001,user_id=2001,message_id='profile',text='记住我是教师')
    memory.db.insert_call(group_id=1001,user_id=2001,message_id='foreign-topic',
        call_text='今天抽卡五星先聊配队',reply_text='你刚才说配队还没解决',reply_kind='model',mode='d',created_at=service._now())
    memory.db.insert_call(group_id=1002,user_id=2001,message_id='local-topic',
        call_text='今天抽卡五星先聊养成',reply_text='继续聊养成',reply_kind='model',mode='d',created_at=service._now())
    await service.handle(None,event('娅娅，抽卡五星你觉得呢',group=1002,message=31),config)
    prompt=provider.seen[0][1]
    assert '我是教师' not in prompt
    assert '先聊配队' not in prompt and '配队还没解决' not in prompt
    assert '先聊养成' not in prompt
