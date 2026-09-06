import asyncio
import os
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from bot.services.avatars import AvatarService


def test_avatar_service_fetches_and_caches_image_locally(tmp_path: Path):
    payload = BytesIO()
    Image.new("RGB", (16, 16), "#00ff00").save(payload, format="PNG")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/avatar/7"
        return httpx.Response(200, content=payload.getvalue(), request=request)

    service = AvatarService(
        tmp_path,
        "https://avatar.test/avatar/{user_id}",
        transport=httpx.MockTransport(handler),
    )

    paths = asyncio.run(service.prefetch([{"user_id": 7}]))

    assert paths[7].parent == tmp_path
    assert paths[7].name.startswith("7.")
    assert paths[7].suffix == ".png"
    with Image.open(paths[7]) as image:
        assert image.size == (16, 16)


def test_avatar_service_returns_no_path_when_source_is_disabled(tmp_path: Path):
    service = AvatarService(tmp_path, "")

    assert asyncio.run(service.prefetch([{"user_id": 7}])) == {}


def test_avatar_service_refreshes_due_avatar_with_a_new_media_path(tmp_path: Path):
    payloads = {}
    calls = 0

    def image_payload(color: str) -> bytes:
        payload = BytesIO()
        Image.new("RGB", (16, 16), color).save(payload, format="PNG")
        return payload.getvalue()

    payloads["current"] = image_payload("#00ff00")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=payloads["current"], request=request)

    service = AvatarService(
        tmp_path,
        "https://avatar.test/avatar/{user_id}",
        refresh_interval=3600,
        transport=httpx.MockTransport(handler),
    )

    first = asyncio.run(service.prefetch([{"user_id": 7}]))[7]
    assert asyncio.run(service.prefetch([{"user_id": 7}]))[7] == first
    assert calls == 1

    old_time = time.time() - 7200
    os.utime(first, (old_time, old_time))
    payloads["current"] = image_payload("#ff0000")
    second = asyncio.run(service.prefetch([{"user_id": 7}]))[7]

    assert calls == 2
    assert second != first
    with Image.open(second) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)


def test_avatar_service_migrates_legacy_cache_path_on_first_refresh(tmp_path: Path):
    legacy = tmp_path / "7.png"
    Image.new("RGB", (16, 16), "#00ff00").save(legacy)
    payload = BytesIO()
    Image.new("RGB", (16, 16), "#ff0000").save(payload, format="PNG")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload.getvalue(), request=request)

    service = AvatarService(
        tmp_path,
        "https://avatar.test/avatar/{user_id}",
        transport=httpx.MockTransport(handler),
    )

    refreshed = asyncio.run(service.prefetch([{"user_id": 7}]))[7]

    assert refreshed != legacy
    assert refreshed.name.startswith("7.")
    assert not legacy.exists()


def test_avatar_service_refresh_removes_every_previous_version(tmp_path: Path):
    payload = BytesIO()
    Image.new("RGB", (16, 16), "#ff0000").save(payload, format="PNG")
    legacy = tmp_path / "7.png"
    previous = tmp_path / "7.aaaaaaaaaaaaaaaa.png"
    Image.new("RGB", (16, 16), "#00ff00").save(legacy)
    Image.new("RGB", (16, 16), "#0000ff").save(previous)
    old_time = time.time() - 7200
    os.utime(legacy, (old_time - 1, old_time - 1))
    os.utime(previous, (old_time, old_time))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload.getvalue(), request=request)

    service = AvatarService(
        tmp_path,
        "https://avatar.test/avatar/{user_id}",
        refresh_interval=1,
        transport=httpx.MockTransport(handler),
    )

    current = asyncio.run(service.prefetch([{"user_id": 7}]))[7]

    assert current.exists()
    assert not legacy.exists()
    assert not previous.exists()


def test_avatar_service_keeps_old_avatar_during_failure_and_applies_cooldown(tmp_path: Path):
    payload = BytesIO()
    Image.new("RGB", (16, 16), "#00ff00").save(payload, format="PNG")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, content=payload.getvalue(), request=request)
        return httpx.Response(503, request=request)

    service = AvatarService(
        tmp_path,
        "https://avatar.test/avatar/{user_id}",
        refresh_interval=1,
        refresh_cooldown=3600,
        transport=httpx.MockTransport(handler),
    )
    first = asyncio.run(service.prefetch([{"user_id": 7}]))[7]
    old_time = time.time() - 10
    os.utime(first, (old_time, old_time))

    assert asyncio.run(service.prefetch([{"user_id": 7}], force_refresh=True))[7] == first
    assert asyncio.run(service.prefetch([{"user_id": 7}], force_refresh=True))[7] == first
    assert calls == 2


def test_avatar_service_limits_refreshes_per_prefetch_call(tmp_path: Path):
    payload = BytesIO()
    Image.new("RGB", (16, 16), "#00ff00").save(payload, format="PNG")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=payload.getvalue(), request=request)

    service = AvatarService(
        tmp_path,
        "https://avatar.test/avatar/{user_id}",
        max_refresh_per_call=1,
        transport=httpx.MockTransport(handler),
    )

    first = asyncio.run(service.prefetch([{"user_id": 7}, {"user_id": 8}]))
    second = asyncio.run(service.prefetch([{"user_id": 7}, {"user_id": 8}]))

    assert set(first) == {7}
    assert set(second) == {7, 8}
    assert calls == ["/avatar/7", "/avatar/8"]
