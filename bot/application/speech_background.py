"""Independent speech recovery, profile refresh and local readiness evidence."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from nonebot import logger

from bot.integrations.speech_runtime import SpeechRuntime
from bot.services.speech import SpeechService, load_voice_profiles


class SpeechSupervisor:
    def __init__(self, root: Path, speech: SpeechService, runtime: SpeechRuntime) -> None:
        self.root, self.speech, self.runtime = root, speech, runtime
        self.loaded_signature = None
        self.invalidated_signature = None
        self.runtime_signature = None

    async def tick(self) -> None:
        path = self.root / "data/personas/voices.json"
        signature = path.stat().st_mtime_ns if path.exists() else 0
        if signature != self.loaded_signature:
            self.speech.invalidate_runtime()
            self.speech.profiles.clear()
            self.speech.bindings.clear()
            self.speech.configuration_fault = True
            if signature != self.invalidated_signature:
                self.speech.store.set_option("voice_profiles_revision", signature)
                self.invalidated_signature = signature
            profiles, bindings = await asyncio.to_thread(load_voice_profiles, path)
            self.speech.profiles, self.speech.bindings = profiles, bindings
            self.speech.configuration_fault = False
            self.loaded_signature = signature
        pid_path = self.root / "data/tts/service.pid"
        runtime_signature = pid_path.stat().st_mtime_ns if pid_path.exists() else 0
        if runtime_signature != self.runtime_signature:
            self.speech.invalidate_runtime()
            self.runtime_signature = runtime_signature
        # Probe first, so a dead service is detected and recovered in this tick.
        # A launcher failure must not suppress checks of an already running API.
        await self.speech.health_check()
        if (self.speech.store.option("speech_enabled", True)
                and self.speech.profiles and not self.speech.ready):
            await self.runtime.ensure_started()

    def write_status(self, error: str = "") -> None:
        path = self.root / "data/tts/readiness.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps({
            "checked_at": time.time(), "bot_pid": os.getpid(),
            "enabled": self.speech.store.option("speech_enabled", True),
            "personas": {p: self.speech.status(p, 0) for p in ("tangtang", "denia")},
            "faults": self.speech.faults, "error": error,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(path)

    async def run(self) -> None:
        while True:
            error = ""
            try:
                await self.tick()
            except Exception as exc:
                error = type(exc).__name__
                logger.warning("Speech background check failed: {}", error)
            try:
                self.write_status(error)
            except OSError as exc:
                logger.warning("Speech readiness report failed: {}", type(exc).__name__)
            await asyncio.sleep(10)
