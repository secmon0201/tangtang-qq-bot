"""Synthesize local acceptance samples and measurements; never sends QQ messages."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.integrations.sovits import SovitsBackend
from bot.services.persona_store import PersonaStore
from bot.services.speech import SpeechService, load_voice_profiles


async def probe_readiness(args) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    service = SpeechService(PersonaStore(output / "readiness-state.db"), SovitsBackend(), output / "audio-cache")
    service.profiles, service.bindings = load_voice_profiles(ROOT / "data/personas/voices.json")
    started = time.perf_counter()
    before = service.status(args.persona, 0)
    try:
        await service.health_check()
        after_health = service.status(args.persona, 0)
        while service.status(args.persona, 0) == "准备中" and time.perf_counter() - started < 35:
            await asyncio.sleep(0.05)
        final = service.status(args.persona, 0)
        with service.store.connect() as conn:
            voice_attempts = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        report = {"before":before, "after_health":after_health, "after_warmup":final,
                  "seconds":round(time.perf_counter() - started, 3),
                  "passed":before == after_health == "准备中" and final == "可用",
                  "sent_messages":0, "voice_attempts":voice_attempts}
        (output / "readiness.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0 if report["passed"] else 1
    finally:
        await service.close()


async def probe(args) -> int:
    profiles, bindings = load_voice_profiles(ROOT / "data" / "personas" / "voices.json")
    voice = profiles[bindings[args.persona]]
    backend = SovitsBackend()
    if not await backend.health(voice):
        print("Speech API is not ready.")
        return 2
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    texts = ["嗯？我在呢。", "唔……休息也是要花时间的呢。", "今天也有好好休息呢。", "只是闭眼思考。差一点就想明白了。", "那是你们聊的呀。愿意也跟我说说吗？"]
    results = []
    for index, text in enumerate(texts, 1):
        start = time.perf_counter()
        audio = await asyncio.wait_for(backend.synthesize(text, voice), 180)
        elapsed = time.perf_counter() - start
        with wave.open(io.BytesIO(audio)) as wav:
            duration = wav.getnframes() / wav.getframerate()
            rate, channels = wav.getframerate(), wav.getnchannels()
        path = output / f"denia-{index:02d}.wav"
        path.write_bytes(audio)
        row = {"text": text, "seconds": round(elapsed, 3), "audio_seconds": round(duration, 3), "sample_rate": rate, "channels": channels, "within_deadline": elapsed <= 30, "file": path.name}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    passed = all(r["within_deadline"] and r["audio_seconds"] > 0 for r in results)
    (output / "benchmark.json").write_text(json.dumps({"voice_version": voice.version, "passed": passed, "samples": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona", default="denia", choices=("denia", "tangtang"))
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "persona-voice-acceptance")
    parser.add_argument("--readiness-only", action="store_true", help="Verify background warmup and readiness without sending QQ messages")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(probe_readiness(args) if args.readiness_only else probe(args)))
