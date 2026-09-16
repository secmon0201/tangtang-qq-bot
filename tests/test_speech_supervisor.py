from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from bot.application import speech_background as background
from tests.test_speech_warmup import setup, settle


class Runtime:
    def __init__(self):
        self.calls = 0
        self.fail = False

    async def ensure_started(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("launcher failure")


def test_supervisor_recovers_failed_profile_without_waiting_for_file_edit(tmp_path, monkeypatch):
    async def run():
        service, backend = setup(tmp_path)
        profiles, bindings = dict(service.profiles), dict(service.bindings)
        valid = [False]
        def load(path):
            if not valid[0]:
                raise ValueError("temporarily missing asset")
            return profiles, bindings
        monkeypatch.setattr(background, "load_voice_profiles", load)
        manager = background.SpeechSupervisor(tmp_path, service, Runtime())
        with pytest.raises(ValueError):
            await manager.tick()
        assert service.configuration_fault and not service.ready
        revision = service.store.revision()
        with pytest.raises(ValueError):
            await manager.tick()
        assert service.store.revision() == revision
        valid[0] = True
        backend.release.set()
        await manager.tick()
        await settle()
        assert service.status("denia", 1001) == "可用"
        manager.write_status()
        report = json.loads((tmp_path / "data/tts/readiness.json").read_text(encoding="utf-8"))
        assert report["personas"]["denia"] == "可用"
        await service.close()
    asyncio.run(run())


def test_new_runtime_requires_warmup_even_when_offline_health_was_missed(tmp_path, monkeypatch):
    async def run():
        service, backend = setup(tmp_path)
        profiles, bindings = dict(service.profiles), dict(service.bindings)
        monkeypatch.setattr(background, "load_voice_profiles", lambda _: (profiles, bindings))
        manager = background.SpeechSupervisor(tmp_path, service, Runtime())
        backend.release.set()
        await manager.tick()
        await settle()
        assert service.status("denia", 1001) == "可用"
        path = tmp_path / "data/tts/service.pid"
        path.parent.mkdir(parents=True)
        path.write_text("12345", encoding="ascii")
        backend.release.clear()
        await manager.tick()
        assert service.status("denia", 1001) == "准备中"
        backend.release.set()
        await settle()
        assert len(backend.calls) == 2 and backend.resets == 2
        await service.close()
    asyncio.run(run())


def test_supervisor_checks_health_before_recovery_and_respects_disable(tmp_path, monkeypatch):
    async def run():
        service, backend = setup(tmp_path)
        profiles, bindings = dict(service.profiles), dict(service.bindings)
        monkeypatch.setattr(background, "load_voice_profiles", lambda _: (profiles, bindings))
        runtime = Runtime()
        manager = background.SpeechSupervisor(tmp_path, service, runtime)
        backend.release.set()
        await manager.tick()
        await settle()
        assert service.status("denia", 1001) == "可用"
        baseline = runtime.calls
        await manager.tick()
        assert runtime.calls == baseline
        backend.available = False
        await manager.tick()
        assert service.status("denia", 1001) == "故障" and runtime.calls == baseline + 1
        service.store.set_option("speech_enabled", False)
        await manager.tick()
        assert runtime.calls == baseline + 1 and service.status("denia", 1001) == "已关闭"
        service.store.set_option("speech_enabled", True)
        backend.available = True
        runtime.fail = True
        # The launch failure must not prevent a healthy API from warming up.
        with pytest.raises(RuntimeError):
            await manager.tick()
        await settle()
        assert service.status("denia", 1001) == "可用"
        await service.close()
    asyncio.run(run())


def test_local_speech_gate_changes_do_not_touch_group_intent(tmp_path):
    from scripts.speech_switch import enabled
    from bot.services.persona_store import PersonaStore
    path = tmp_path / "personas/state.db"
    assert enabled(path) and not path.exists()
    store = PersonaStore(path)
    store.switch(1001, "denia")
    store.set_option("speech_enabled", False)
    assert not enabled(path)
    assert store.selection(1001) == ("denia", 1)
    store.set_option("speech_enabled", True)
    assert enabled(path)
