"""Probe the independent 9890 GPT-SoVITS instance with a real synthesis."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import time

import httpx


def wav_info(raw: bytes) -> dict:
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("TTS did not return a RIFF/WAVE payload")
    channels = struct.unpack_from("<H", raw, 22)[0]
    sample_rate = struct.unpack_from("<I", raw, 24)[0]
    bits = struct.unpack_from("<H", raw, 34)[0]
    data_offset = raw.find(b"data")
    data_size = struct.unpack_from("<I", raw, data_offset + 4)[0] if data_offset >= 0 else 0
    duration = data_size / max(1, sample_rate * channels * bits // 8)
    nonzero = sum(1 for value in raw[data_offset + 8 : data_offset + 8 + data_size] if value)
    return {"sample_rate": sample_rate, "channels": channels, "bits": bits,
            "data_bytes": data_size, "duration_seconds": round(duration, 3),
            "nonzero_bytes": nonzero}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    endpoint = "http://127.0.0.1:9890"
    reference = root / "runtime" / "speech-config" / "reference.wav"
    text = "这是 TangtangHarness 的独立语音测试。"
    payload = {
        "text": text, "text_lang": "all_zh", "ref_audio_path": str(reference),
        "prompt_text": "怎么啊？如果有你在也不放心，那就干脆给我也装个限制器或者炸弹喽。",
        "prompt_lang": "zh", "top_k": 15, "top_p": 0.8, "temperature": 0.7,
        "text_split_method": "cut0", "batch_size": 1, "speed_factor": 0.85,
        "fragment_interval": 0.45, "seed": 42, "repetition_penalty": 1.35,
        "media_type": "wav", "streaming_mode": False,
    }
    started = time.perf_counter()
    with httpx.Client(timeout=180) as client:
        docs = None
        for _ in range(30):
            try:
                docs = client.get(endpoint + "/docs", timeout=3)
                if docs.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        if docs is None or docs.status_code != 200:
            status = "connection failed" if docs is None else f"HTTP {docs.status_code}"
            raise RuntimeError(f"/docs readiness check failed: {status}")
        response = client.post(endpoint + "/tts", json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"/tts returned HTTP {response.status_code}: {response.text[:1000]}")
    raw = response.content
    info = wav_info(raw)
    if info["duration_seconds"] <= 0 or info["nonzero_bytes"] <= 0:
        raise ValueError("TTS returned an empty or silent WAV")
    digest = hashlib.sha256(raw).hexdigest()
    output = root / "runtime" / "speech" / f"real-probe-{digest[:16]}.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    report = {"endpoint": endpoint, "docs_status": docs.status_code, "text_length": len(text),
              "elapsed_seconds": round(time.perf_counter() - started, 3), "sha256": digest,
              "wav": info, "output": str(output)}
    report_path = root / "reports" / "p8-speech.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
