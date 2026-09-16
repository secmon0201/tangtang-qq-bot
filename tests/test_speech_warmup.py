from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import time
import wave

import pytest

from bot.services.persona_profiles import ChatContext, VoiceProfile, load_personas
from bot.services.persona_store import PersonaStore
from bot.services.speech import SpeechService


def audio():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 160)
    return buffer.getvalue()


class Backend:
    def __init__(self):
        self.calls = []
        self.available = True
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.invalid = False
        self.concurrent = 0
        self.peak = 0

    async def health(self, voice):
        return self.available

    async def synthesize(self, text, voice):
        self.calls.append((voice.key, text))
        self.concurrent += 1
        self.peak = max(self.peak, self.concurrent)
        self.started.set()
        try:
            await self.release.wait()
            return b"not audio" if self.invalid else audio()
        finally:
            self.concurrent -= 1


def setup(tmp_path, *, timeout=0.2, clock=time.time):
    store = PersonaStore(tmp_path / "state.db")
    backend = Backend()
    service = SpeechService(store, backend, tmp_path / "audio", timeout=timeout, clock=clock)
    voice = VoiceProfile("voice", "http://127.0.0.1:9880", tmp_path / "ref.wav", "示例", "m1", "r1")
    service.profiles[voice.key] = voice
    service.bindings["denia"] = voice.key
    return service, backend


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


def test_health_alone_never_enables_voice_and_warmup_has_no_sends_or_quota(tmp_path, monkeypatch):
    async def never_send(*args, **kwargs):
        pytest.fail("warmup must never send to QQ")
    monkeypatch.setattr("bot.services.speech.call_qq_action", never_send)
    async def run():
        service, backend = setup(tmp_path)
        await service.health_check()
        await backend.started.wait()
        assert service.status("denia", 1001) == "准备中"
        await asyncio.gather(*(service.health_check() for _ in range(10)))
        assert len(backend.calls) == 1
        context = ChatContext(load_personas()["denia"], 1001, 2001, "event", 0, 0, "model")
        result = await asyncio.wait_for(service.deliver(None, context, "你好", explicit=True, current=lambda:True), 0.1)
        assert result.status == "failed"
        backend.release.set()
        await settle()
        assert service.status("denia", 1001) == "可用"
        await service.health_check()
        assert len(backend.calls) == 1
        with service.store.connect() as conn:
            for table in ("budgets", "deliveries", "evidence"):
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert not service.cache_dir.exists()
        await service.close()
    asyncio.run(run())


def test_timed_out_warmup_keeps_slot_and_late_success_cannot_enable_voice(tmp_path):
    async def run():
        now = [time.time()]
        service, backend = setup(tmp_path, timeout=0.02, clock=lambda:now[0])
        await service.health_check()
        await backend.started.wait()
        await asyncio.sleep(0.04)
        assert service.status("denia", 1001) == "故障"
        now[0] += 120
        await service.health_check()
        await settle()
        assert len(backend.calls) == 1 and backend.concurrent == 1
        backend.release.set()
        await settle()
        assert service.status("denia", 1001) != "可用"
        await service.health_check()
        await settle()
        assert len(backend.calls) == 2 and backend.peak == 1
        assert service.status("denia", 1001) == "可用"
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize("change", ["disabled", "profile", "offline"])
def test_obsolete_warmup_cannot_mark_service_ready(tmp_path, change):
    async def run():
        service, backend = setup(tmp_path)
        await service.health_check()
        await backend.started.wait()
        if change == "disabled":
            service.store.set_option("speech_enabled", False)
        elif change == "profile":
            service.profiles["voice"] = replace(service.profiles["voice"], model_version="m2")
        else:
            backend.available = False
        await service.health_check()
        backend.release.set()
        await settle()
        assert service.status("denia", 1001) != "可用"
        await service.close()
    asyncio.run(run())


def test_invalid_warmup_audio_backs_off_and_recovery_requires_new_synthesis(tmp_path):
    async def run():
        now = [time.time()]
        service, backend = setup(tmp_path, clock=lambda:now[0])
        backend.invalid = True
        backend.release.set()
        await service.health_check()
        await settle()
        assert service.status("denia", 1001) == "故障"
        await service.health_check()
        await settle()
        assert len(backend.calls) == 1
        backend.invalid = False
        now[0] += 61
        await service.health_check()
        await settle()
        assert service.status("denia", 1001) == "可用"
        backend.available = False
        await service.health_check()
        assert service.status("denia", 1001) == "故障"
        backend.available = True
        await service.health_check()
        await settle()
        assert len(backend.calls) == 3 and service.status("denia", 1001) == "可用"
        await service.close()
    asyncio.run(run())
