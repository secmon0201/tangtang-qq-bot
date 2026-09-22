"""DeepSeek vision wire limits, applied to current AND retained user images."""
from __future__ import annotations

import base64
import io
import json
from typing import Any

from PIL import Image

from bot.services.tangtang_media import ImageReference, TangtangMediaResolver, VisionImage


def image_parts(images: tuple[VisionImage, ...]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for image in images:
        parts.extend((
            {"type": "text", "text": f"[{image.label}]"},
            {"type": "image_url", "image_url": {"url": image.data_url, "detail": "high"}},
        ))
    return parts


def responses_content(content: Any, role: str) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return [{"type": "output_text" if role == "assistant" else "input_text", "text": str(content or "")}]
    result = []
    for part in content:
        if part.get("type") == "image_url":
            if role != "user":
                raise ValueError("images require a user message")
            result.append({"type": "input_image", "image_url": part["image_url"]["url"], "detail": "high"})
        else:
            result.append({"type": "output_text" if role == "assistant" else "input_text", "text": part.get("text", "")})
    return result


def chat_content(content: Any) -> Any:
    """Restore the same key order as freshly constructed image_parts."""
    if not isinstance(content, list):
        return content or ""
    parts = []
    for part in content:
        if part.get("type") == "image_url":
            parts.append({"type": "image_url", "image_url": {"url": part["image_url"]["url"], "detail": "high"}})
        else:
            parts.append({"type": "text", "text": part.get("text", "")})
    return parts


def enforce_vision_limits(payload: dict[str, Any]) -> None:
    """Never evict historical images to admit a new one; reject an oversized request."""
    images: list[tuple[dict, str]] = []
    for message in payload.get("messages", payload.get("input", ())):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") == "image_url":
                target, key = part["image_url"], "url"
            elif part.get("type") == "input_image":
                target, key = part, "image_url"
            else:
                continue
            if message.get("role") != "user":
                raise ValueError("images require a user message")
            target["detail"] = "high"
            images.append((target, key))
    if len(images) > 600:
        raise ValueError("vision request exceeds 600 images")
    dimension = 4096 if len(images) >= 15 else 8192
    resolver = TangtangMediaResolver(max_dimension=dimension)
    total = 0
    for target, key in images:
        value = target[key]
        if not value.startswith("data:"):
            if len(value) > 8192:
                raise ValueError("image URL exceeds 8192 characters")
            # The chat service resolves external sources before this boundary.
            raise ValueError("unresolved image at provider boundary")
        raw = resolver._decode_data_url(value)
        with Image.open(io.BytesIO(raw)) as opened:
            oversize = max(opened.size) > dimension
        if oversize:
            normalized = resolver._normalise(ImageReference("current", 1, value), raw)
            target[key] = normalized.data_url
            raw = base64.b64decode(normalized.data_url.split(",", 1)[1])
        total += len(raw)
    if total > 64 * 1024 * 1024:
        raise ValueError("vision request exceeds 64 MiB of images")
    # httpx JSON uses UTF-8, compact separators and ensure_ascii=False.
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    if size > 48 * 1024 * 1024:
        raise ValueError("provider request exceeds 48 MiB")
