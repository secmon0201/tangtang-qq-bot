from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import ipaddress
import socket
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_IMAGE_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)


@dataclass(frozen=True, slots=True)
class ImageReference:
    source: str
    ordinal: int
    value: str


@dataclass(frozen=True, slots=True)
class VisionImage:
    source: str
    ordinal: int
    data_url: str
    mime_type: str
    sha256: str
    byte_count: int

    @property
    def label(self) -> str:
        prefix = {
            "current": "当前消息",
            "reply": "引用消息",
            "context": "最近群聊上下文",
        }.get(self.source, "消息")
        return f"{prefix}图片 {self.ordinal}"


@dataclass(frozen=True, slots=True)
class MediaResolution:
    images: tuple[VisionImage, ...]
    failures: tuple[str, ...]


def extract_image_references(event: Any, max_images: int) -> tuple[ImageReference, ...]:
    """Extract current and quoted OneBot image segments without trusting text URLs."""

    references: list[ImageReference] = []
    seen: set[str] = set()
    sources: tuple[tuple[str, Iterable[Any]], ...] = (
        ("current", getattr(event, "message", ())),
        (
            "reply",
            getattr(getattr(event, "reply", None), "message", ()),
        ),
    )
    for source, message in sources:
        ordinal = 0
        for segment in message:
            if str(getattr(segment, "type", "") or "") != "image":
                continue
            ordinal += 1
            data = getattr(segment, "data", None) or {}
            value = str(data.get("url") or data.get("file") or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            references.append(ImageReference(source, ordinal, value))
            if len(references) >= max_images:
                return tuple(references)
    return tuple(references)


class TangtangMediaResolver:
    """Resolve bounded OneBot image inputs into validated in-memory data URLs."""

    def __init__(
        self,
        *,
        max_images: int = 4,
        max_image_bytes: int = 8 * 1024 * 1024,
        max_total_bytes: int = 16 * 1024 * 1024,
        max_pixels: int = 20_000_000,
        max_dimension: int = 2048,
        timeout_seconds: int = 10,
    ) -> None:
        self.max_images = max_images
        self.max_image_bytes = max_image_bytes
        self.max_total_bytes = max_total_bytes
        self.max_pixels = max_pixels
        self.max_dimension = max_dimension
        self.timeout_seconds = timeout_seconds
        self._cache: OrderedDict[str, VisionImage] = OrderedDict()

    async def resolve_event(self, event: Any) -> MediaResolution:
        references = extract_image_references(event, self.max_images)
        return await self.resolve_references(references)

    async def resolve_references(
        self, references: Iterable[ImageReference]
    ) -> MediaResolution:
        images: list[VisionImage] = []
        failures: list[str] = []
        total = 0
        for reference in references:
            if len(images) >= self.max_images:
                break
            cached = self._cache.get(reference.value)
            if cached is not None:
                if total + cached.byte_count > self.max_total_bytes:
                    failures.append(f"{_source_label(reference.source)}图片 {reference.ordinal} 读取失败")
                    continue
                self._cache.move_to_end(reference.value)
                images.append(
                    VisionImage(
                        source=reference.source,
                        ordinal=reference.ordinal,
                        data_url=cached.data_url,
                        mime_type=cached.mime_type,
                        sha256=cached.sha256,
                        byte_count=cached.byte_count,
                    )
                )
                total += cached.byte_count
                continue
            try:
                raw = await self._read_reference(reference.value)
                if total + len(raw) > self.max_total_bytes:
                    raise ValueError("image total exceeds configured limit")
                image = self._normalise(reference, raw)
            except (OSError, ValueError, httpx.HTTPError, UnidentifiedImageError):
                failures.append(
                    f"{_source_label(reference.source)}图片 {reference.ordinal} 读取失败"
                )
                continue
            total += len(raw)
            images.append(image)
            self._cache[reference.value] = image
            self._cache.move_to_end(reference.value)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        return MediaResolution(tuple(images), tuple(failures))

    async def _read_reference(self, value: str) -> bytes:
        if value.startswith("data:"):
            return self._decode_data_url(value)
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("unsupported image reference")
        return await self._download(value)

    def _decode_data_url(self, value: str) -> bytes:
        header, separator, encoded = value.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("invalid image data URL")
        mime_type = header[5:].split(";", 1)[0].lower()
        if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
            raise ValueError("unsupported image MIME type")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("invalid base64 image") from exc
        if len(raw) > self.max_image_bytes:
            raise ValueError("image exceeds configured limit")
        return raw

    async def _download(self, url: str) -> bytes:
        current = url
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
        ) as client:
            for _redirect in range(4):
                await self._validate_public_url(current)
                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "")
                        if not location:
                            raise ValueError("image redirect has no location")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    declared = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if declared and declared not in ALLOWED_IMAGE_MIME_TYPES:
                        raise ValueError("remote content is not an image")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_image_bytes:
                            raise ValueError("image exceeds configured limit")
                        chunks.append(chunk)
                    if not chunks:
                        raise ValueError("empty image response")
                    return b"".join(chunks)
        raise ValueError("too many image redirects")

    async def _validate_public_url(self, value: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid image URL")
        if parsed.username or parsed.password:
            raise ValueError("credentialed image URL is not allowed")
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
            raise ValueError("local image URL is not allowed")
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            infos = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
            addresses = {info[4][0] for info in infos}
        else:
            addresses = {str(literal)}
        if not addresses or any(not _is_public_ip(address) for address in addresses):
            raise ValueError("non-public image URL is not allowed")

    def _normalise(self, reference: ImageReference, raw: bytes) -> VisionImage:
        with Image.open(io.BytesIO(raw)) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > self.max_pixels:
                raise ValueError("image dimensions exceed configured limit")
            opened.seek(0)
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((self.max_dimension, self.max_dimension), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True)
        encoded = output.getvalue()
        return VisionImage(
            source=reference.source,
            ordinal=reference.ordinal,
            data_url="data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii"),
            mime_type="image/jpeg",
            sha256=hashlib.sha256(raw).hexdigest(),
            byte_count=len(raw),
        )


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(address.is_global)


def _source_label(source: str) -> str:
    return {
        "current": "当前消息",
        "reply": "引用消息",
        "context": "最近群聊上下文",
    }.get(source, "消息")


__all__ = [
    "ImageReference",
    "MediaResolution",
    "TangtangMediaResolver",
    "VisionImage",
    "extract_image_references",
]
