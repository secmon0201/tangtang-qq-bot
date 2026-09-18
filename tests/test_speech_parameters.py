"""Saved tuning must reach the API and invalidate cached speech."""
import asyncio
from dataclasses import replace
import hashlib
import json

import httpx
import pytest

from bot.integrations.sovits import SovitsBackend
from bot.services.speech import load_voice_profiles


@pytest.fixture
def voice_file(tmp_path):
    files = {}
    hashes = {}
    for name in ("gpt_weights", "sovits_weights", "reference_audio"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        files[name] = str(path)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    entry = dict(endpoint="http://127.0.0.1:9880", reference_text="参考台词。",
                 sha256=hashes, **files)
    path = tmp_path / "voices.json"
    path.write_text(json.dumps({"voices": {"test": entry}, "bindings": {"denia": "test"}}))
    return path


def test_saved_tuning_reaches_http_request(voice_file, monkeypatch):
    data = json.loads(voice_file.read_text())
    data["voices"]["test"].update(language="all_zh", prompt_language="zh", speed=0.85,
        top_k=15, top_p=0.8, temperature=0.7, text_split_method="cut0",
        fragment_interval=0.45, seed=42, repetition_penalty=1.35, batch_size=1)
    voice_file.write_text(json.dumps(data))
    profiles, bindings = load_voice_profiles(voice_file)
    voice = profiles[bindings["denia"]]
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"message": "success"})
        return httpx.Response(200, content=b"RIFF0000WAVEdata")

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **kwargs, transport=httpx.MockTransport(respond)))
    asyncio.run(SovitsBackend().synthesize("慢慢说清楚。", voice))
    assert [r.url.path for r in requests] == ["/set_gpt_weights", "/set_sovits_weights", "/tts"]
    assert json.loads(requests[-1].content) == dict(
        text="慢慢说清楚。", text_lang="all_zh", prompt_lang="zh", prompt_text="参考台词。",
        ref_audio_path=str(voice.reference_audio), speed_factor=0.85, top_k=15,
        top_p=0.8, temperature=0.7, text_split_method="cut0", fragment_interval=0.45,
        seed=42, repetition_penalty=1.35, batch_size=1, media_type="wav", streaming_mode=False)


@pytest.mark.parametrize("field,value", [
    ("language", "all_zh"), ("prompt_language", "all_zh"), ("speed", 0.85),
    ("top_k", 10), ("top_p", 0.8), ("temperature", 0.7),
    ("text_split_method", "cut0"), ("fragment_interval", 0.45), ("seed", 42),
    ("repetition_penalty", 1.2), ("batch_size", 2),
])
def test_each_tuning_change_invalidates_cached_voice(voice_file, field, value):
    profiles, _ = load_voice_profiles(voice_file)
    voice = profiles["test"]
    assert replace(voice, **{field: value}).version != voice.version


def test_legacy_voice_uses_original_defaults(voice_file):
    profiles, _ = load_voice_profiles(voice_file)
    voice = profiles["test"]
    assert (voice.language, voice.prompt_language, voice.speed) == ("zh", "zh", 1)
    assert (voice.top_k, voice.top_p, voice.temperature) == (15, 1, 1)
    assert (voice.text_split_method, voice.fragment_interval, voice.seed) == ("cut5", 0.3, -1)
