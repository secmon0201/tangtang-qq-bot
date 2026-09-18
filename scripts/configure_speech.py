"""Bind a verified local GPT-SoVITS voice without changing the chat model or .env."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona", choices=("tangtang", "denia"), default="denia")
    parser.add_argument("--voice", default="dania-v2proplus")
    parser.add_argument("--source", type=Path, default=ROOT / "downloads" / "GPT-SoVITS-cpu")
    parser.add_argument("--python", type=Path, default=ROOT / "data" / "tts" / "env" / "Scripts" / "python.exe")
    parser.add_argument("--gpt", type=Path, default=ROOT / "data" / "tts" / "dania" / "dania-e15.ckpt")
    parser.add_argument("--sovits", type=Path, default=ROOT / "data" / "tts" / "dania" / "dania_e16_s2192.pth")
    parser.add_argument("--reference", type=Path, default=ROOT / "data" / "tts" / "dania" / "output.wav_0009342720_0009558400.wav")
    parser.add_argument("--reference-text", default="怎么啊？如果有你在也不放心，那就干脆给我也装个限制器或者炸弹喽。")
    parser.add_argument("--port", type=int, default=9880)
    args = parser.parse_args()
    if args.persona == "tangtang":
        import sys
        required = ("--voice", "--gpt", "--sovits", "--reference", "--reference-text")
        if any(not any(arg == flag or arg.startswith(flag + "=") for arg in sys.argv[1:]) for flag in required):
            parser.error("Tangtang requires explicit --voice, --gpt, --sovits, --reference and --reference-text")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    source, python = args.source.resolve(strict=True), args.python.resolve(strict=True)
    if not (source / "api_v2.py").is_file():
        parser.error("source must contain api_v2.py")
    files = {"gpt_weights": args.gpt.resolve(strict=True), "sovits_weights": args.sovits.resolve(strict=True), "reference_audio": args.reference.resolve(strict=True)}
    hashes = {}
    for name, path in files.items():
        with path.open("rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    folder = ROOT / "data" / "personas"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "voices.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"voices": {}, "bindings": {}}
    lock = json.loads((ROOT / "config/speech-upstream-lock.json").read_text(encoding="utf-8"))
    # Rebinding model assets must retain the operator's saved synthesis tuning.
    data["voices"][args.voice] = {**data["voices"].get(args.voice, {}), "endpoint": f"http://127.0.0.1:{args.port}", **{k: str(v) for k, v in files.items()}, "reference_text": args.reference_text, "sha256": hashes, "runtime_revision": lock["runtime"]["revision"]}
    data["bindings"][args.persona] = args.voice
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    runtime = ROOT / "data" / "tts"
    config_path = runtime / "tts-infer.yaml"
    # JSON is a YAML subset. GPT-SoVITS may rewrite this runtime configuration;
    # it is deliberately outside the pristine downloaded source tree.
    config = {"custom": {"device": "cpu", "is_half": False, "version": "v2ProPlus",
        "t2s_weights_path": str(files["gpt_weights"]), "vits_weights_path": str(files["sovits_weights"]),
        "bert_base_path": str(source / "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"),
        "cnhuhbert_base_path": str(source / "GPT_SoVITS/pretrained_models/chinese-hubert-base")}}
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    launch = {"enabled": True, "python": str(python), "source": str(source), "config": str(config_path), "port": args.port}
    (runtime / "service.json").write_text(json.dumps(launch, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Bound {args.persona} to {args.voice}; runtime config is local-only.")


if __name__ == "__main__":
    main()
