import asyncio
import io
import json
import wave

import httpx
import pytest

from tangtang_harness.external import SpeechClient


def wav_bytes(frames=80):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * frames)
    return output.getvalue()


def speech_settings(reference, **extra):
    return {"endpoint": "http://127.0.0.1:9890", "ref_audio_path": str(reference),
            "text_lang": "all_zh", "prompt_text": "参考", "prompt_lang": "zh", **extra}


@pytest.mark.asyncio
async def test_speech_cache_invalidates_when_reference_audio_changes(tmp_path, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/docs":
            return httpx.Response(200)
        return httpx.Response(200, content=wav_bytes())

    client_class = httpx.AsyncClient
    monkeypatch.setattr("tangtang_harness.external.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference-v1")
    client = SpeechClient(tmp_path)
    settings = speech_settings(reference)
    await client.check(settings)
    first = await client.synthesize("你好", settings)
    second = await client.synthesize("你好", settings)
    reference.write_bytes(b"reference-v2")
    third = await client.synthesize("你好", settings)
    assert first == second and third != first
    assert [request.url.path for request in requests] == ["/docs", "/tts", "/tts"]


@pytest.mark.asyncio
async def test_speech_invalid_output_revokes_readiness_and_does_not_cache(tmp_path, monkeypatch):
    responses = iter([httpx.Response(200), httpx.Response(200, content=b"not audio")])

    def respond(request):
        return next(responses)

    client_class = httpx.AsyncClient
    monkeypatch.setattr("tangtang_harness.external.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    client = SpeechClient(tmp_path)
    settings = speech_settings(tmp_path / "reference.wav")
    await client.check(settings)
    with pytest.raises(RuntimeError, match="有效 WAV"):
        await client.synthesize("你好", settings)
    assert client.ready is False
    assert not list((tmp_path / "runtime" / "speech").glob("*.wav"))


@pytest.mark.asyncio
async def test_speech_queue_has_a_single_total_deadline_and_endpoint_readiness(tmp_path, monkeypatch):
    release = asyncio.Event()

    async def delayed(request):
        await release.wait()
        return httpx.Response(200, content=wav_bytes())

    def respond(request):
        if request.url.path == "/docs":
            return httpx.Response(200)
        return delayed(request)

    client_class = httpx.AsyncClient
    monkeypatch.setattr("tangtang_harness.external.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    client = SpeechClient(tmp_path)
    settings = speech_settings(tmp_path / "reference.wav", timeout_seconds=.02)
    await client.check(settings)
    with pytest.raises(RuntimeError, match="超时"):
        await client.synthesize("你好", settings)
    assert client.ready is False
    changed = {**settings, "endpoint": "http://127.0.0.1:9891"}
    with pytest.raises(RuntimeError, match="未就绪"):
        await client.synthesize("你好", changed)


@pytest.mark.asyncio
async def test_speech_health_does_not_mark_active_cpu_synthesis_unavailable(tmp_path, monkeypatch):
    release, entered = asyncio.Event(), asyncio.Event()
    requests = []

    async def respond(request):
        requests.append(request.url.path)
        if request.url.path == '/docs':
            return httpx.Response(200)
        entered.set()
        await release.wait()
        return httpx.Response(200, content=wav_bytes())

    client_class = httpx.AsyncClient
    monkeypatch.setattr('tangtang_harness.external.httpx.AsyncClient',
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    client = SpeechClient(tmp_path)
    settings = speech_settings(tmp_path / 'reference.wav', timeout_seconds=1)
    await client.check(settings)
    task = asyncio.create_task(client.synthesize('你好', settings))
    await entered.wait()
    await client.check(settings)
    assert client.ready and requests == ['/docs', '/tts']
    release.set()
    assert (await task).is_file()


@pytest.mark.asyncio
async def test_speech_rebuilds_corrupt_cache_and_invalidates_voice_configuration(tmp_path, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request.url.path)
        return httpx.Response(200, content=b'' if request.url.path == '/docs' else wav_bytes())

    client_class = httpx.AsyncClient
    monkeypatch.setattr('tangtang_harness.external.httpx.AsyncClient',
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    voice_config = tmp_path / 'runtime' / 'speech-config' / 'tts_infer.yaml'
    voice_config.parent.mkdir(parents=True)
    voice_config.write_text('custom:\n  version: v2\n', encoding='utf-8')
    settings = speech_settings(tmp_path / 'reference.wav')
    client = SpeechClient(tmp_path)
    await client.check(settings)
    first = await client.synthesize('你好', settings)
    first.write_bytes(b'corrupt cache')
    rebuilt = await client.synthesize('你好', settings)
    assert rebuilt == first and rebuilt.read_bytes().startswith(b'RIFF') and client.ready
    voice_config.write_text('custom:\n  version: v2Pro\n', encoding='utf-8')
    changed = await client.synthesize('你好', settings)
    assert changed != first and requests == ['/docs', '/tts', '/tts', '/tts']
