"""Export the upstream XutheringWavesUID help image without changing Core."""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "GsUID.Core"
OUTPUT = ROOT / "data" / "wuwa_original_help.png"


def _configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


async def _render() -> bytes:
    sys.path.insert(0, str(CORE))
    from gsuid_core.plugins.XutheringWavesUID.XutheringWavesUID.wutheringwaves_help.get_help import get_help

    result = await get_help(6)
    if isinstance(result, bytes):
        return result
    if hasattr(result, "save"):
        stream = io.BytesIO()
        result.save(stream, format="PNG")
        return stream.getvalue()
    raise TypeError(f"unexpected upstream help image type: {type(result)!r}")


def main() -> None:
    _configure_utf8_streams()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(asyncio.run(_render()))
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
