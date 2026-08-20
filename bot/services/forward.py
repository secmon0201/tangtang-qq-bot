from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any


def build_forward_nodes(
    page_messages: Sequence[str],
    page_paths: Sequence[Path | None],
    bot_id: int | str,
    title: str = "查重结果",
) -> list[dict[str, Any]]:
    """Build JSON-native OneBot v11 forward nodes.

    Forward nodes are nested inside an API payload.  Keeping their content as
    plain OneBot dictionaries keeps this payload portable: adapter ``Message``
    objects are fine at the top level, but some OneBot implementations do not unwrap
    ``MessageSegment`` instances nested below ``data.content``.
    """
    if len(page_messages) != len(page_paths):
        raise ValueError("page messages and page paths must have the same length")

    nodes: list[dict[str, Any]] = []
    for index, (fallback, path) in enumerate(zip(page_messages, page_paths), 1):
        if path:
            content = [{"type": "image", "data": {"file": path.resolve().as_uri()}}]
        else:
            content = [{"type": "text", "data": {"text": fallback}}]
        nodes.append(
            {
                "type": "node",
                "data": {
                    "name": f"{title} 第{index}页",
                    "uin": str(bot_id),
                    "content": content,
                },
            }
        )
    return nodes
