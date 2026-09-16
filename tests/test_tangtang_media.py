from __future__ import annotations

import asyncio
import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from bot.services.tangtang_media import (
    ImageReference,
    TangtangMediaResolver,
    extract_image_references,
)


def _data_url(width: int = 4, height: int = 3) -> str:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def test_extract_image_references_keeps_current_then_reply_and_deduplicates():
    current = [
        SimpleNamespace(type="text", data={"text": "看看"}),
        SimpleNamespace(type="image", data={"url": "https://example.test/a.png"}),
    ]
    reply = SimpleNamespace(
        sender=SimpleNamespace(user_id=22, card="被引用的人", nickname="quoted"),
        message=[
            SimpleNamespace(type="image", data={"url": "https://example.test/a.png"}),
            SimpleNamespace(type="image", data={"file": _data_url()}),
        ]
    )
    event = SimpleNamespace(
        user_id=11,
        sender=SimpleNamespace(user_id=11, card="当前发图人", nickname="current"),
        message=current,
        reply=reply,
    )
    refs = extract_image_references(event, 4)
    assert [(ref.source, ref.ordinal) for ref in refs] == [
        ("current", 1),
        ("reply", 2),
    ]
    assert [(ref.sender_id, ref.sender_name) for ref in refs] == [
        (11, "当前发图人"),
        (22, "被引用的人"),
    ]


def test_image_at_dimension_limit_keeps_original_bytes_and_mime_type():
    resolver = TangtangMediaResolver(max_images=1)
    value = _data_url(1000, 1000)
    result = asyncio.run(
        resolver.resolve_references((ImageReference("current", 1, value),))
    )
    assert result.failures == ()
    assert len(result.images) == 1
    assert result.images[0].data_url == value
    assert result.images[0].mime_type == "image/png"
    assert result.images[0].byte_count > 0
    assert len(result.images[0].sha256) == 64


def test_default_normalisation_scales_only_images_over_1000_pixels():
    resolver = TangtangMediaResolver(max_images=1)
    result = asyncio.run(
        resolver.resolve_references(
            (ImageReference("current", 1, _data_url(2000, 1500)),)
        )
    )
    encoded = result.images[0].data_url.split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert image.size == (1000, 750)
        assert image.format == "JPEG"


def test_cached_image_uses_current_reference_sender_instead_of_cached_sender():
    resolver = TangtangMediaResolver(max_images=1)
    value = _data_url()
    first = asyncio.run(
        resolver.resolve_references(
            (ImageReference("current", 1, value, 11, "甲"),)
        )
    )
    second = asyncio.run(
        resolver.resolve_references(
            (ImageReference("context", 1, value, 22, "乙"),)
        )
    )
    assert first.images[0].label == "当前消息图片 1（发送者：甲）"
    assert second.images[0].label == "最近群聊上下文图片 1（发送者：乙）"


def test_private_network_image_url_is_rejected_before_download():
    resolver = TangtangMediaResolver()
    with pytest.raises(ValueError, match="non-public"):
        asyncio.run(resolver._validate_public_url("http://127.0.0.1/image.png"))


def test_non_image_data_url_is_rejected_as_a_failure():
    resolver = TangtangMediaResolver()
    result = asyncio.run(
        resolver.resolve_references(
            (ImageReference("current", 1, "data:text/plain;base64,SGVsbG8="),)
        )
    )
    assert result.images == ()
    assert result.failures == ("当前消息图片 1 读取失败",)
