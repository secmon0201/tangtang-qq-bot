from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11 import MessageSegment


def local_image_segment(path: Path) -> MessageSegment:
    """Build a OneBot-compatible image segment for a local file."""
    return MessageSegment.image(file=path.resolve().as_uri())
