"""Conversational delivery for the local Denia image gallery."""
from __future__ import annotations

from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import FinishedException

from bot.application.local_features import FeatureRequest, register_local_feature
from bot.services.denia_gallery import DeniaGallery
from bot.services.media import local_image_segment


gallery = DeniaGallery()


@register_local_feature("denia_gallery")
async def _send_random_denia_image(
    matcher: object,
    bot: Bot,
    event: object,
    request: FeatureRequest,
) -> None:
    del bot, request
    image = gallery.draw(
        group_id=int(getattr(event, "group_id", 0) or 0),
        user_id=int(getattr(event, "user_id", 0) or 0),
    )
    await matcher.send(local_image_segment(image.path))  # type: ignore[attr-defined]
    raise FinishedException


__all__ = ["gallery"]
