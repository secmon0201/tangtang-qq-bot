"""Run the pinned API with g2pW's writable cache outside vendor source."""
from __future__ import annotations

import builtins
from contextlib import contextmanager
import os
from pathlib import Path
import runpy
import sys


@contextmanager
def redirect_g2pw_cache(source: Path, cache: Path):
    vendor = source / "GPT_SoVITS/text/g2pw"
    # Pinned g2pW uses builtins.open and os.path.exists for these two files.
    # Keep its pronunciation dictionaries and executable code untouched.
    module = (vendor / "g2pw.py").read_text(encoding="utf-8")
    if not all(token in module for token in ('"polyphonic.pickle"', '"polyphonic.md5"', "open(MD5_PATH", "open(CACHE_PATH")):
        raise RuntimeError("GPT-SoVITS g2pW cache API changed")
    cache.mkdir(parents=True, exist_ok=True)
    mapping = {os.path.normcase(str((vendor / name).resolve())):cache / name
               for name in ("polyphonic.md5", "polyphonic.pickle")}
    original_open, original_exists = builtins.open, os.path.exists
    def redirect(path):
        if not isinstance(path, (str, bytes, os.PathLike)):
            return path
        normalized = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        return mapping.get(normalized, path)
    def cached_open(file, *args, **kwargs):
        return original_open(redirect(file), *args, **kwargs)
    def cached_exists(path):
        return original_exists(redirect(path))
    builtins.open, os.path.exists = cached_open, cached_exists
    try:
        yield
    finally:
        builtins.open, os.path.exists = original_open, original_exists


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    source = Path(sys.argv[1]).resolve(strict=True)
    api = source / "api_v2.py"
    sys.argv = [str(api), *sys.argv[2:]]
    sys.path[:0] = [str(source), str(source / "GPT_SoVITS")]
    with redirect_g2pw_cache(source, root / "data/tts/g2pw-cache"):
        runpy.run_path(str(api), run_name="__main__")


if __name__ == "__main__":
    main()
