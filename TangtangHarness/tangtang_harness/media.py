"""Resolve the existing QQ image-source contract into frozen local assets."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import ipaddress
import socket
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from .models import model_error_summary
from .store import Store
from .types import InboundEvent


MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGES = 600
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PAYLOAD_BYTES = 48 * 1024 * 1024
MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}
QQ_IMAGE_FAKE_IP_HOSTS = frozenset({'multimedia.nt.qq.com.cn'})
PROXY_FAKE_IPV4_NETWORK = ipaddress.ip_network('198.18.0.0/15')


@dataclass(slots=True)
class MediaResolution:
    parts: list[dict[str, Any]] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def image_sources(event: InboundEvent, previous: list[InboundEvent], *, proactive: bool = False,
                  include_history: bool = True,
                  excluded_keys: set[str] | None = None) -> list[tuple[str, str]]:
    sources: list[tuple[InboundEvent | dict[str, Any], str]] = [(event, "当前消息")]
    if not proactive:
        if event.quoted:
            sources.append((event.quoted, "引用消息"))
        if include_history:
            same_scope = [item for item in previous if item.session_key == event.session_key and item.key != event.key]
            if same_scope:
                sources.append((same_scope[-1], "本会话上一条消息"))
            own = [item for item in same_scope if item.user_id == event.user_id]
            if own:
                sources.append((own[-1], "发送者上一条消息"))
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source, origin in sources:
        if isinstance(source, InboundEvent):
            if source.key in (excluded_keys or set()):
                continue
            segments, nickname = source.segments, source.nickname
        else:
            segments = source.get("segments", source.get("message", ()))
            sender = source.get("sender", {})
            nickname = str(sender.get("card") or sender.get("nickname") or "引用作者")
        if not isinstance(segments, (list, tuple)):
            continue
        for segment in segments:
            if segment.get("type") != "image":
                continue
            data = segment.get("data", {})
            value = str(data.get("url") or data.get("file") or "")
            if value and value not in seen:
                seen.add(value)
                found.append((value, f"{origin}，发送者{nickname}"))
    return found


def normalize_image(raw: bytes, *, max_dimension: int = 8192,
                    first_frame: bool = False) -> tuple[str, bytes]:
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("图片超过 32 MiB")
    with Image.open(io.BytesIO(raw)) as opened:
        if opened.format not in MIME:
            raise ValueError("图片格式不支持")
        if opened.width * opened.height > 100_000_000:
            raise ValueError("图片像素过大")
        media_type = MIME[opened.format]
        if max(opened.size) <= max_dimension and not (first_frame and opened.format == 'GIF'):
            return media_type, raw
        opened.seek(0)
        image = opened.convert("RGBA" if opened.mode in {"RGBA", "LA", "P"} else "RGB")
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return "image/png", buffer.getvalue()


async def public_image_url(url: str) -> None:
    parsed = urlparse(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("图片必须来自 OneBot 的公网 HTTP(S) 来源")
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
    addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, port, type=socket.SOCK_STREAM)
    # Windows proxy DNS can map this QQ image CDN to its synthetic IP range.
    # The exception covers only its exact HTTPS host and port, never an
    # arbitrary private address or a caller-supplied host/port combination.
    qq_fake_ip = parsed.hostname in QQ_IMAGE_FAKE_IP_HOSTS and parsed.scheme == 'https' and port == 443
    for row in addresses:
        address = ipaddress.ip_address(row[4][0])
        if address.is_global:
            continue
        if qq_fake_ip and isinstance(address, ipaddress.IPv4Address) and address in PROXY_FAKE_IPV4_NETWORK:
            continue
        raise ValueError("图片来源不是公网地址")


class MediaResolver:
    def __init__(self, store: Store, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.store, self.transport = store, transport

    async def resolve(self, event: InboundEvent, *, proactive: bool = False,
                      include_history: bool = True) -> MediaResolution:
        previous = ([InboundEvent.from_dict(row["payload"])
                     for row in self.store.events(event.session_key, limit=100)] if include_history else [])
        scopes = self.store.event_scopes([item.key for item in previous])
        excluded = {item.key for item in previous if not scopes[item.key]}
        blocked = self.store.blocked_users(event.group_id or 0)
        if event.user_id in blocked:
            raise ValueError('当前用户已被过滤，不能读取其模型图片')
        quote = event.quoted or {}
        author = quote.get('user_id', quote.get('sender', {}).get('user_id'))
        if author is not None and int(author) in blocked:
            event = replace(event, quoted=None)
        excluded.update(item.key for item in previous if item.user_id in blocked)
        sources = image_sources(event, previous, proactive=proactive,
                                include_history=include_history, excluded_keys=excluded)
        result = MediaResolution()
        total = 0
        async with httpx.AsyncClient(transport=self.transport, timeout=10, follow_redirects=False) as client:
            for value, label in sources:
                try:
                    if value.startswith("base64://"):
                        raw = base64.b64decode(value[9:], validate=True)
                    elif value.startswith("data:"):
                        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
                    else:
                        await public_image_url(value)
                        async with client.stream("GET", value) as response:
                            response.raise_for_status()
                            data = bytearray()
                            async for chunk in response.aiter_bytes():
                                data.extend(chunk)
                                if len(data) > MAX_IMAGE_BYTES:
                                    raise ValueError("图片超过 32 MiB")
                            raw = bytes(data)
                    gif = raw.startswith((b'GIF87a', b'GIF89a'))
                    media_type, raw = normalize_image(raw, first_frame=True)
                    if gif:
                        label += '（GIF首帧）'
                    total += len(raw)
                    if total > MAX_TOTAL_BYTES or len(result.assets) >= MAX_IMAGES:
                        raise ValueError("新增图片超过请求预算")
                    digest = hashlib.sha256(raw).hexdigest()
                    self.store.put_asset(digest, media_type, raw)
                    data_url = f"data:{media_type};base64," + base64.b64encode(raw).decode("ascii")
                    result.parts.extend([{"type": "text", "text": f"[图片来源：{label}]"},
                                         {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}}])
                    result.assets.append({"digest": digest, "media_type": media_type, "bytes": len(raw), "label": label})
                except (ValueError, OSError, httpx.HTTPError) as exc:
                    result.failures.append(f"{label}：图片读取失败（{model_error_summary(exc)}）")
        return result


def enforce_payload_limits(payload: dict[str, Any]) -> None:
    images: list[dict[str, Any]] = []
    for message in payload.get("messages", payload.get("input", ())):
        if not isinstance(message.get("content"), list):
            continue
        for part in message["content"]:
            if part.get("type") in {"image_url", "input_image"}:
                images.append(part)
    if len(images) > MAX_IMAGES:
        raise ValueError("请求图片超过 600 张")
    maximum = 4096 if len(images) >= 15 else 8192
    total = 0
    for part in images:
        is_chat = part.get("type") == "image_url"
        target = part["image_url"] if is_chat else part
        key = "url" if is_chat else "image_url"
        url = target[key]
        if not str(url).startswith("data:"):
            raise ValueError("发送边界出现尚未解析的图片")
        raw = base64.b64decode(url.split(",", 1)[1])
        media_type, normalized = normalize_image(raw, max_dimension=maximum)
        total += len(normalized)
        if normalized != raw:
            target[key] = f"data:{media_type};base64," + base64.b64encode(normalized).decode("ascii")
    if total > MAX_TOTAL_BYTES:
        raise ValueError("请求图片超过 64 MiB")
    if len(json_payload(payload)) > MAX_PAYLOAD_BYTES:
        raise ValueError("模型请求体超过 48 MiB")


def json_payload(payload: dict[str, Any]) -> bytes:
    import json
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
