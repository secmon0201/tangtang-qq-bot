"""Export the upstream NTEUID help image without modifying GsUID.Core.

Run from the project root after an upstream NTEUID update:
    python scripts/export_nte_original_help.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "GsUID.Core"
OUTPUT = ROOT / "data" / "nte_original_help.png"


async def _render() -> bytes:
    sys.path.insert(0, str(CORE))
    from gsuid_core.plugins.NTEUID.NTEUID.nte_help.get_help import get_help

    result = await get_help(6)
    if isinstance(result, bytes):
        return result
    if hasattr(result, "save"):
        import io

        stream = io.BytesIO()
        result.save(stream, format="PNG")
        return stream.getvalue()
    raise TypeError(f"unexpected upstream help image type: {type(result)!r}")


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(asyncio.run(_render()))
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
