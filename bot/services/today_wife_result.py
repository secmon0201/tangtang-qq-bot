from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11 import MessageSegment

from bot.services.media import local_image_segment


def first_draw_result_message(
    message_id: int,
    target_id: int,
    path: Path,
    lead: str | None = None,
) -> MessageSegment:
    """Build the first-draw reply without separating its natural-language lead.

    Keep the no-argument form byte-for-byte compatible with the original
    message shape.  New story reveals pass a scene-specific lead and receive a
    complete sentence around the mention instead of a bare ``@``.
    """

    normalized_lead = str(lead or "").strip()
    if not normalized_lead or normalized_lead == "今天的缘分悄悄落在了":
        return (
            MessageSegment.reply(message_id)
            + local_image_segment(path)
            + "\n今天的缘分悄悄落在了 "
            + MessageSegment.at(target_id)
            + " 身上。"
        )
    return (
        MessageSegment.reply(message_id)
        + local_image_segment(path)
        + f"\n{normalized_lead} "
        + MessageSegment.at(target_id)
        + "，这一幕从这里开始。"
    )
