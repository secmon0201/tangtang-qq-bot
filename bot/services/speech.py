"""Independent text-to-audio worker and delivery journal; no persona reasoning."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit

from nonebot.adapters.onebot.v11 import ActionFailed, MessageSegment

from bot.services.persona_profiles import ChatContext, VoiceProfile
from bot.services.persona_store import PersonaStore
from bot.services.qq_platform import call_qq_action
from bot.services.pacing import OutboundCancelled


class SpeechBackend(Protocol):
    async def health(self, voice: VoiceProfile) -> bool: ...
    async def synthesize(self, text: str, voice: VoiceProfile) -> bytes: ...


@dataclass(frozen=True, slots=True)
class SpeechResult:
    status: str
    message_id: str = ""
    reason: str = ""


@dataclass(slots=True)
class _SpeechJob:
    context: ChatContext | None
    text: str
    voice: VoiceProfile
    deadline: float
    result: asyncio.Future
    active: bool = True
    finished: bool = False


def load_voice_profiles(path: Path) -> tuple[dict[str, VoiceProfile], dict[str, str]]:
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    profiles = {}
    for key, entry in data.get("voices", {}).items():
        endpoint = str(entry["endpoint"]).rstrip("/")
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.path:
            raise ValueError("TTS endpoint must be a local HTTP origin")
        files = {name: Path(entry[name]).resolve(strict=True) for name in ("reference_audio", "gpt_weights", "sovits_weights")}
        hashes = {}
        for name, file in files.items():
            with file.open("rb") as stream:
                hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
            if hashes[name] != entry["sha256"][name]:
                raise ValueError(f"voice asset checksum mismatch: {key}/{name}")
        profiles[key] = VoiceProfile(
            key=key, endpoint=endpoint, reference_audio=files["reference_audio"],
            reference_text=str(entry["reference_text"]),
            model_version=hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
            reference_hash=hashes["reference_audio"], gpt_weights=files["gpt_weights"],
            sovits_weights=files["sovits_weights"], speed=float(entry.get("speed", 1)),
            runtime_version=str(entry.get("runtime_revision", "")),
        )
    bindings = dict(data.get("bindings", {}))
    if any(p not in {"tangtang", "denia"} or key not in profiles for p, key in bindings.items()):
        raise ValueError("invalid persona voice binding")
    return profiles, bindings


class SpeechService:
    def __init__(self, store: PersonaStore, backend: SpeechBackend, cache_dir: Path,
                 *, timeout: float = 30, clock: Callable[[], float] = time.time) -> None:
        self.store, self.backend, self.cache_dir = store, backend, cache_dir
        self.timeout, self.clock = timeout, clock
        self.profiles: dict[str, VoiceProfile] = {}
        self.bindings: dict[str, str] = {}
        self.ready: set[str] = set()
        self.faults: dict[str, str] = {}
        self.configuration_fault = False
        self._queue: asyncio.Queue[_SpeechJob] = asyncio.Queue(maxsize=2)
        self._worker: asyncio.Task | None = None
        self._health_lock = asyncio.Lock()
        self._last_random: dict[int, float] = {}
        self._healthy: dict[str, VoiceProfile] = {}
        self._warmups: dict[str, _SpeechJob] = {}
        self._warmup_monitors: set[asyncio.Task] = set()
        self._warmup_retry_at: dict[tuple[str, str], float] = {}

    def voice(self, persona: str) -> VoiceProfile | None:
        return self.profiles.get(self.bindings.get(persona, ""))

    def status(self, persona: str, group_id: int, *, group_enabled: bool = True) -> str:
        if not group_enabled or not self.store.option("speech_enabled", True):
            return "已关闭"
        if self.configuration_fault:
            return "故障"
        voice = self.voice(persona)
        if voice is None:
            return "未绑定声线"
        if voice.key in self.faults:
            return "故障"
        if voice.key not in self.ready:
            return "准备中"
        now = self.clock()
        if (self.store.budget_used("speech", "global", now) >= self.store.option("speech_global_limit", 60)
                or self.store.budget_used("speech", f"group:{group_id}", now) >= self.store.option("speech_group_limit", 12)):
            return "额度不足"
        return "可用"

    def random_candidate(self, group_id: int, roll: float) -> bool:
        return (roll < float(self.store.option("speech_probability", 0.10))
                and self.clock() - self.store.option(f"last_random_voice:{group_id}", -1e12) >= self.store.option("speech_cooldown", 600))

    async def health_check(self) -> None:
        if self._health_lock.locked():
            return
        async with self._health_lock:
            if not self.store.option("speech_enabled", True):
                for key in tuple(self._healthy):
                    self._invalidate_readiness(key)
                return
            for voice in tuple(self.profiles.values()):
                try:
                    healthy = await asyncio.wait_for(self.backend.health(voice), 4)
                except Exception:
                    healthy = False
                if self.profiles.get(voice.key) is not voice:
                    continue
                if healthy:
                    if self._healthy.get(voice.key) is not voice:
                        self.ready.discard(voice.key)
                    self._healthy[voice.key] = voice
                    if voice.key not in self.ready:
                        self._schedule_warmup(voice)
                else:
                    self._invalidate_readiness(voice.key)
                    self.faults[voice.key] = "服务不可用或接口不兼容"

    def _invalidate_readiness(self, key: str) -> None:
        self.ready.discard(key)
        self._healthy.pop(key, None)
        job = self._warmups.get(key)
        if job is not None:
            job.active = False
            job.result.cancel()

    def _warmup_current(self, job: _SpeechJob) -> bool:
        return (job.active and not self.configuration_fault
                and self.store.option("speech_enabled", True)
                and self.profiles.get(job.voice.key) is job.voice
                and self._healthy.get(job.voice.key) is job.voice)

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    def _schedule_warmup(self, voice: VoiceProfile) -> None:
        if (voice.key in self._warmups or self._queue.full()
                or self.clock() < self._warmup_retry_at.get((voice.key, voice.version), 0)):
            return
        future = asyncio.get_running_loop().create_future()
        job = _SpeechJob(None, "你好。", voice, self.clock() + self.timeout, future)
        self._warmups[voice.key] = job
        self.faults.pop(voice.key, None)
        self._queue.put_nowait(job)
        self._ensure_worker()
        monitor = asyncio.create_task(self._monitor_warmup(job))
        self._warmup_monitors.add(monitor)
        monitor.add_done_callback(self._warmup_monitors.discard)

    async def _monitor_warmup(self, job: _SpeechJob) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(job.result), self.timeout)
            if self._warmup_current(job) and self.clock() < job.deadline:
                self.ready.add(job.voice.key)
                self.faults.pop(job.voice.key, None)
        except asyncio.CancelledError:
            job.active = False
            job.result.cancel()
        except Exception:
            job.active = False
            job.result.cancel()
            if self.profiles.get(job.voice.key) is job.voice:
                self.ready.discard(job.voice.key)
                self.faults[job.voice.key] = "预热失败或超时，等待后台重试"
                self._warmup_retry_at[(job.voice.key, job.voice.version)] = self.clock() + 60
        finally:
            self._release_warmup(job)

    def _release_warmup(self, job: _SpeechJob) -> None:
        if job.finished and self._warmups.get(job.voice.key) is job:
            self._warmups.pop(job.voice.key)

    @staticmethod
    def _validate_audio(audio: bytes) -> None:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if not 0 < wav.getnframes() / wav.getframerate() <= 60:
                raise ValueError("invalid speech duration")

    async def _run_worker(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if not job.active or self.clock() >= job.deadline:
                    continue
                if job.context is None:
                    if not self._warmup_current(job):
                        job.active = False
                        continue
                    # Warmup shares the only synthesis slot, bypasses reply
                    # caches, and has no group quota, history or send capability.
                    audio = await self.backend.synthesize(job.text, job.voice)
                    self._validate_audio(audio)
                    path = None
                else:
                    digest = hashlib.sha256(json.dumps((job.context.group_id, job.context.persona.key, job.voice.version, job.text), ensure_ascii=False).encode()).hexdigest()
                    path = self.cache_dir / (digest + ".wav")
                    if not path.exists():
                        audio = await self.backend.synthesize(job.text, job.voice)
                        self._validate_audio(audio)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(audio)
                if job.active and self.clock() < job.deadline and not job.result.done():
                    job.result.set_result(path)
            except Exception as exc:
                if job.active and not job.result.done():
                    job.result.set_exception(exc)
            finally:
                job.finished = True
                if not job.active:
                    self._release_warmup(job)
                if not job.result.done():
                    if job.active:
                        job.result.set_exception(TimeoutError("speech queue deadline expired"))
                    else:
                        job.result.cancel()
                self._queue.task_done()

    async def deliver(self, bot, context: ChatContext, text: str, *, explicit: bool,
                      current: Callable[[], bool]) -> SpeechResult:
        old = self.store.delivery(context.request_id)
        if old is not None:
            return SpeechResult("duplicate")
        voice = self.voice(context.persona.key)
        if voice is None or self.status(context.persona.key, context.group_id) != "可用" or self._queue.full():
            return SpeechResult("failed", reason="语音服务暂不可用")
        if not current():
            return SpeechResult("cancelled")
        if not self.store.claim_budget("speech", context.group_id, self.clock(), self.store.option("speech_global_limit", 60), self.store.option("speech_group_limit", 12)):
            return SpeechResult("failed", reason="语音额度已用完")
        if not explicit:
            self._last_random[context.group_id] = self.clock()
            self.store.set_option(f"last_random_voice:{context.group_id}", self.clock(), invalidate=False)
        self.store.journal(context.request_id, context.persona.key, context.group_id, "synthesizing", text)
        self._ensure_worker()
        future = asyncio.get_running_loop().create_future()
        job = _SpeechJob(context, text, voice, self.clock() + self.timeout, future)
        self._queue.put_nowait(job)
        try:
            path = await asyncio.wait_for(asyncio.shield(future), self.timeout)
        except asyncio.CancelledError:
            job.active = False
            future.cancel()
            raise
        except Exception:
            job.active = False
            future.cancel()
            self.store.journal(context.request_id, context.persona.key, context.group_id, "failed", text, detail="synthesis_failed_or_timeout")
            return SpeechResult("failed", reason="这次语音没合成好，先打字吧。")
        selected_voice = self.voice(context.persona.key)
        if not current() or self.clock() >= job.deadline or selected_voice is None or selected_voice.version != voice.version:
            self.store.journal(context.request_id, context.persona.key, context.group_id, "cancelled", text)
            return SpeechResult("cancelled")
        self.store.journal(context.request_id, context.persona.key, context.group_id, "sending", text)
        try:
            result = await call_qq_action(bot, "send_group_msg", group_id=context.group_id,
                                          message=MessageSegment.record(path.resolve().as_uri()))
            payload = result.get("data", result) if isinstance(result, dict) else {}
            message_id = str(payload.get("message_id") or "")
            if not message_id:
                raise ValueError("missing voice delivery acknowledgement")
        except Exception as exc:
            cause = exc.__cause__ or exc
            status = "cancelled" if isinstance(cause, OutboundCancelled) else "failed" if isinstance(cause, ActionFailed) else "uncertain"
            self.store.journal(context.request_id, context.persona.key, context.group_id, status, text, detail=type(cause).__name__)
            # Without a message ID OneBot cannot reliably correlate a timed out
            # record send. Never infer failure from a missing acknowledgement.
            return SpeechResult(status, reason="语音没有发出去，先打字吧。")
        self.store.journal(context.request_id, context.persona.key, context.group_id, "delivered", text, message_id)
        return SpeechResult("delivered", message_id)

    async def close(self) -> None:
        monitors = tuple(self._warmup_monitors)
        for monitor in monitors:
            monitor.cancel()
        await asyncio.gather(*monitors, return_exceptions=True)
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
