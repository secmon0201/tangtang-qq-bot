"""Bounded HTTP compatibility for the pristine Windows GPT-SoVITS runtime."""
from __future__ import annotations

import httpx

from bot.services.persona_profiles import VoiceProfile


class SovitsBackend:
    def __init__(self) -> None:
        self._loaded: tuple[str, str] | None = None

    def reset(self) -> None:
        """A fresh warmup must select weights again after a runtime restart."""
        self._loaded = None

    async def health(self, voice: VoiceProfile) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.get(voice.endpoint + "/openapi.json")
                response.raise_for_status()
                paths = response.json().get("paths", {})
                return all(p in paths for p in ("/tts", "/set_gpt_weights", "/set_sovits_weights"))
        except (httpx.HTTPError, ValueError, AttributeError):
            self._loaded = None
            return False

    async def synthesize(self, text: str, voice: VoiceProfile) -> bytes:
        # Calls are serialized by SpeechService, including model loading. A timed
        # out caller never releases the worker until this HTTP call finishes.
        # The caller has its own 30 s deadline. Keep this work slot reserved
        # until the server finishes even if synthesis takes much longer.
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5, write=5, pool=5), trust_env=False) as client:
            signature = (voice.endpoint, voice.version)
            if self._loaded != signature:
                for endpoint, weights in (("/set_gpt_weights", voice.gpt_weights), ("/set_sovits_weights", voice.sovits_weights)):
                    if weights is not None:
                        response = await client.get(voice.endpoint + endpoint, params={"weights_path": str(weights)})
                        response.raise_for_status()
                        payload = response.json()
                        if isinstance(payload, dict) and payload.get("message") not in {None, "success"}:
                            raise ValueError("GPT-SoVITS rejected model weights")
                self._loaded = signature
            response = await client.post(voice.endpoint + "/tts", json={
                "text": text, "text_lang": voice.language,
                "ref_audio_path": str(voice.reference_audio),
                "prompt_text": voice.reference_text, "prompt_lang": voice.prompt_language,
                "speed_factor": voice.speed, "media_type": "wav", "streaming_mode": False,
                "top_k": voice.top_k, "top_p": voice.top_p, "temperature": voice.temperature,
                "text_split_method": voice.text_split_method,
                "fragment_interval": voice.fragment_interval, "seed": voice.seed,
                "repetition_penalty": voice.repetition_penalty, "batch_size": voice.batch_size,
            })
            response.raise_for_status()
            audio = response.content
            if len(audio) > 12 * 1024 * 1024 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                raise ValueError("GPT-SoVITS returned invalid WAV audio")
            return audio
