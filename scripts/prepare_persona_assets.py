"""Copy an explicit public-data allowlist from the captured Denia research tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    shared = source / "denia" / "共享"
    target = ROOT / "bot" / "resources" / "personas" / "denia"
    files = {
        shared / "语料" / "语料库_标签版.md": "dialogue-corpus.md",
        shared / "记忆-虚质之前" / "核心事实.md": "canonical-facts.md",
        shared / "记忆-虚质之前" / "关键事件.md": "canonical-events.md",
        source / "LICENSE": "UPSTREAM-LICENSE.txt",
    }
    # Only reviewed semantic slots; no upstream tools, private memory or agents.
    for filename, slot in (("微笑.jpg", "smile"), ("大笑.jpg", "laugh"), ("思考.jpg", "think"), ("探头.jpg", "peek")):
        files[shared / "工具" / "表情包" / filename] = f"expressions/{slot}.jpg"
    manifest = {"repository": "https://github.com/SSDeutschland/Denia-chat", "revision": "becf50be0f9ce78c7678ac75dc9c0dbbbf2de9f4", "files": []}
    for origin, relative in files.items():
        destination = target / relative
        content = origin.read_bytes()
        if destination.exists() and destination.read_bytes() != content:
            raise SystemExit(f"Refusing to overwrite changed resource: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(origin, destination)
        manifest["files"].append({"source": origin.relative_to(source).as_posix(), "path": relative, "sha256": hashlib.sha256(content).hexdigest()})
    manifest_path = target / "asset-manifest.json"
    output = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != output:
        raise SystemExit("Asset manifest already exists with different content")
    manifest_path.write_text(output, encoding="utf-8")
    print(f"Verified {len(files)} allowlisted public assets.")


if __name__ == "__main__":
    main()
