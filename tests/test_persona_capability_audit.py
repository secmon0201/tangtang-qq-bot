"""Adversarial integration cases for the capability completion audit."""
from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_persona_integration import Provider, event, make_runtime, service_for


def _proposal(text):
    return {"category": "experience", "quote": text, "summary": text, "tags": ["创作"]}


@pytest.mark.parametrize("body", ["今天不想发语音。", "语音已经发好了。"])
def test_memory_receipt_cannot_erase_a_voice_consistency_failure(tmp_path, monkeypatch, body):
    async def run():
        engine, backend = make_runtime(tmp_path)
        engine.store.switch(1001, "denia")
        source = "我上周参加了绘画展"
        provider = Provider({"decision": "reply", "messages": [body], "voice": "accept",
            "text_fallback": ["先打字说。"], "memory_updates": [_proposal(source)]})
        service, config = service_for(tmp_path, engine, provider)
        sent = []
        async def send(*args, **params):
            sent.append(params["message"])
            return {"message_id": 91}
        monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
        monkeypatch.setattr("bot.services.speech.call_qq_action", send)
        await service.handle(None, event("娅娅，记住" + source + "，用语音回答"), config)
        await engine.speech.close()
        assert sent
        assert not backend.calls, "Persistence receipt must not clear an original voice refusal/promise conflict"
        assert "已经保存" in str(sent[0])
    asyncio.run(run())


def test_failed_explicit_save_is_not_silently_retried_after_failure_receipt(tmp_path, monkeypatch):
    async def run():
        engine, _ = make_runtime(tmp_path)
        engine.store.switch(1001, "denia")
        source = "我上周参加了绘画展"
        provider = Provider({"decision": "reply", "messages": ["好呀。"], "voice": "text",
            "memory_updates": [_proposal(source)]})
        service, config = service_for(tmp_path, engine, provider)
        memory = engine.memory("denia", service._base_db, service._now)
        original = memory.semantic.save
        attempts = []
        def transient_failure(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("synthetic full disk")
            return original(*args, **kwargs)
        monkeypatch.setattr(memory.semantic, "save", transient_failure)
        sent = []
        async def send(*args, **params):
            sent.append(str(params["message"]))
            return {"message_id": 92}
        monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
        await service.handle(None, event("娅娅，记住" + source), config)
        assert sent and "保存失败" in sent[0]
        assert not memory.semantic.recall(1002, 2001, "创作经历"), "Sent failure receipt must remain true after delivery"
        assert len(attempts) == 1
    asyncio.run(run())


def test_growth_storage_failure_does_not_disable_delivered_personal_memory(tmp_path, monkeypatch):
    async def run():
        engine, _ = make_runtime(tmp_path)
        engine.store.switch(1001, "denia")
        source = "我上周参加了绘画展"
        provider = Provider({"decision": "reply", "messages": ["画了什么呀？"], "voice": "text",
            "memory_updates": [_proposal(source)]})
        service, config = service_for(tmp_path, engine, provider)
        memory = engine.memory("denia", service._base_db, service._now)
        def fail_growth(*args):
            raise OSError("synthetic growth-store failure")
        monkeypatch.setattr(engine, "observe", fail_growth)
        async def send(*args, **params):
            return {"message_id": 93}
        monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
        await service.handle(None, event("娅娅，" + source), config)
        assert memory.semantic.recall(1002, 2001, "创作经历"), "Independent growth failure must not suppress acknowledged personal memory"
    asyncio.run(run())


def test_model_failure_has_no_memory_or_speech_side_effects(tmp_path, monkeypatch):
    async def run():
        engine, backend = make_runtime(tmp_path)
        engine.store.switch(1001, "denia")
        class BrokenProvider:
            async def generate_agent(self, *args):
                raise TimeoutError("synthetic provider timeout")
        service, config = service_for(tmp_path, engine, BrokenProvider())
        sent = []
        async def send(*args, **params):
            sent.append(params)
            return {"message_id": 94}
        monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
        monkeypatch.setattr("bot.services.speech.call_qq_action", send)
        await service.handle(None, event("娅娅，记住我上周参加了绘画展，用语音回答"), config)
        memory = engine.memory("denia", service._base_db, service._now)
        assert not sent and not backend.calls
        assert not memory.semantic.recall(1002, 2001, "创作经历")
    asyncio.run(run())


def test_actual_generation_prompt_contains_scene_and_memory_contracts(tmp_path):
    async def run():
        engine, _ = make_runtime(tmp_path)
        engine.store.switch(1001, "denia")
        provider = Provider({"decision": "silent", "messages": []})
        service, config = service_for(tmp_path, engine, provider)
        await service.handle(None, event("娅娅，我考试没过，真的有点难受"), config)
        assert provider.seen
        actual_prompt = "\n".join(provider.seen[0])
        assert "真实失落、疲惫或严肃求助" in actual_prompt
        assert "[个人记忆提取合同]" in actual_prompt
        assert "未完成问题只使用当前群记录" in actual_prompt or "未完问题只使用本群记录" in actual_prompt
    asyncio.run(run())


def test_ordinary_voice_remains_available_after_memory_receipt_guard(tmp_path, monkeypatch):
    async def run():
        engine, backend = make_runtime(tmp_path)
        engine.store.switch(1001, "denia")
        provider = Provider({"decision": "reply", "messages": ["今天过得还不错呀。"], "voice": "accept"})
        service, config = service_for(tmp_path, engine, provider)
        sent = []
        async def send(*args, **params):
            sent.append(params["message"])
            return {"message_id": 95}
        monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
        monkeypatch.setattr("bot.services.speech.call_qq_action", send)
        await service.handle(None, event("娅娅，用语音说说今天过得怎么样"), config)
        await engine.speech.close()
        assert backend.calls == [("今天过得还不错呀。", "dania")]
        assert len(sent) == 1 and sent[0].type == "record"
        assert engine.history("denia", service._base_db).list_calls(2001, 1001)[0]["reply_text"] == "今天过得还不错呀。"
    asyncio.run(run())


@pytest.mark.parametrize("acknowledged", [True, False])
@pytest.mark.parametrize("voice_choice", ["text", "accept"])
def test_explicit_save_marks_exactly_one_source_after_ack(tmp_path, monkeypatch, acknowledged, voice_choice):
    async def run():
        engine, backend = make_runtime(tmp_path)
        engine.store.switch(1001, "denia")
        source = "我上周参加了绘画展"
        provider = Provider({"decision": "reply", "messages": ["好呀。"], "voice": voice_choice,
            "memory_updates": [_proposal(source)]})
        service, config = service_for(tmp_path, engine, provider)
        async def send(*args, **params):
            return {"message_id": 96} if acknowledged else {}
        monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
        monkeypatch.setattr("bot.services.speech.call_qq_action", send)
        request = "娅娅，记住" + source + ("，用语音回答" if voice_choice == "accept" else "")
        await service.handle(None, event(request), config)
        await engine.speech.close()
        if voice_choice == "accept":
            assert backend.calls and "已经保存" in backend.calls[0][0]
        memory = engine.memory("denia", service._base_db, service._now)
        assert memory.semantic.recall(1002, 2001, "创作经历")
        with memory.people.connect() as conn:
            evidence = conn.execute("SELECT delivered FROM person_semantic_evidence").fetchall()
        assert len(evidence) == 1 and evidence[0][0] == int(acknowledged)
    asyncio.run(run())


def test_background_malformed_output_retains_evidence_then_real_growth_is_applied(tmp_path, monkeypatch):
    from bot.services.persona_background import PersonaBackground
    from bot.services.tangtang_db import TangtangDb
    from tests.test_persona_background_retry import seeded_worker_parts

    store, engine, clock, _config, loader = seeded_worker_parts(tmp_path, monkeypatch)
    engine.history("denia", TangtangDb(tmp_path / "history.db"))
    class Provider:
        calls = 0
        async def generate(self, config, identity, prompt):
            self.calls += 1
            if self.calls == 1:
                return "我已经整理好了", {}
            payload = json.loads(prompt[prompt.index('{"current"'):])
            citations = [{"id": row["id"], "quote": "休息很重要，可以慢慢来"}
                         for row in payload["delivered_interactions"]]
            return json.dumps({"proposals": [{"scope": "group", "kind": "opinion", "topic": "休息",
                "content": "累了可以先休息，不必勉强赶路。", "evidence": citations}]}, ensure_ascii=False), {}
    provider = Provider()
    async def run():
        worker = PersonaBackground(engine, provider, loader)
        await worker.tick({1001})
        assert provider.calls == 1 and store.pending_interactions("denia", 1001)
        assert store.option("background_provider_retry")["reason"] == "invalid_model_output"
        await worker.tick({1001})
        assert provider.calls == 1
        clock[0] += 21600
        await worker.tick({1001})
        assert provider.calls == 2
        assert store.budget_used("background", "global", clock[0]) == 2
        assert not store.pending_interactions("denia", 1001)
        assert not store.option("background_provider_retry")
        review = store.growth_diagnostics("denia", 1001)[0]
        assert json.loads(review["decisions"]) == [{"reason": "accepted"}]
        assert "累了可以先休息" in engine.growth.prompt("denia", 1001)
        assert "累了可以先休息" in engine.growth.prompt("denia", 1002)
    asyncio.run(run())
