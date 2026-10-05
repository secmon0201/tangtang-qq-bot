from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image


class AvatarService:
    """Fetch and cache QQ avatars for local report rendering.

    The network is used only as an avatar source. Report composition itself is
    still performed locally by Pillow, and a failed fetch never fails a scan.
    """

    def __init__(
        self,
        cache_dir: Path,
        base_url: str,
        timeout: float = 5,
        cache_ttl: int = 604800,
        transport: httpx.AsyncBaseTransport | None = None,
        refresh_interval: int = 3600,
        refresh_cooldown: int = 900,
        max_refresh_per_call: int = 24,
        concurrency: int = 2,
    ) -> None:
        self.cache_dir = cache_dir
        self.base_url = base_url
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.transport = transport
        self.refresh_interval = max(1, int(refresh_interval))
        self.refresh_cooldown = max(1, int(refresh_cooldown))
        self.max_refresh_per_call = max(1, int(max_refresh_per_call))
        self.concurrency = max(1, int(concurrency))

    _registry_lock = threading.Lock()
    _cache_name = re.compile(r"^(?P<user_id>\d+)(?:\.[0-9a-f]{16})?\.png$", re.IGNORECASE)
    _refresh_locks: dict[tuple[str, int], asyncio.Lock] = {}
    _next_attempt_at: dict[tuple[str, int], float] = {}

    @classmethod
    def _shared_lock(cls, key: tuple[str, int]) -> asyncio.Lock:
        with cls._registry_lock:
            return cls._refresh_locks.setdefault(key, asyncio.Lock())

    @classmethod
    def _attempt_allowed(cls, key: tuple[str, int]) -> bool:
        with cls._registry_lock:
            return time.monotonic() >= cls._next_attempt_at.get(key, 0.0)

    @classmethod
    def _record_failed_attempt(cls, key: tuple[str, int], cooldown: int) -> None:
        with cls._registry_lock:
            cls._next_attempt_at[key] = time.monotonic() + cooldown

    async def prefetch(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        force_refresh: bool = False,
        max_refresh: int | None = None,
    ) -> dict[int, Path]:
        """Return local avatars and refresh only the entries due for a probe.

        A normal render can therefore refresh changed avatars without making a
        request for every image.  ``force_refresh`` is intentionally explicit
        for controlled maintenance or a future user-facing refresh action.
        """
        users: dict[int, str] = {}
        for item in items:
            user_id = int(item["user_id"])
            users.setdefault(user_id, str(item.get("avatar_url") or ""))
        if not users:
            return {}

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[int, Path] = {}
        candidates: list[tuple[int, str]] = []
        for user_id, source_url in users.items():
            current = self._current_path(user_id)
            if current is not None:
                paths[user_id] = current
            if force_refresh or self._refresh_due(current):
                candidates.append((user_id, source_url))

        refresh_limit = self.max_refresh_per_call if max_refresh is None else max(1, int(max_refresh))
        candidates = candidates[:refresh_limit]
        if not candidates:
            return paths

        semaphore = asyncio.Semaphore(self.concurrency)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.timeout,
            transport=self.transport,
            headers={
                "User-Agent": "qq-local-data-bot/avatar-cache",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        ) as client:
            tasks = [
                self._ensure_one(client, semaphore, user_id, source_url, force_refresh)
                for user_id, source_url in candidates
            ]
            values = await asyncio.gather(*tasks, return_exceptions=True)

        for (user_id, _), value in zip(candidates, values):
            if isinstance(value, Path):
                paths[user_id] = value
        return paths

    def cached_paths(self, items: Sequence[Mapping[str, Any]]) -> dict[int, Path]:
        """Return fresh local avatars without performing network requests."""
        paths: dict[int, Path] = {}
        for item in items:
            user_id = int(item["user_id"])
            path = self._current_path(user_id)
            if path is not None and self._is_fresh(path, self.cache_ttl):
                paths[user_id] = path
        return paths

    def refresh_due(self, item: Mapping[str, Any]) -> bool:
        """Return whether a demand-triggered refresh probe is due."""

        return self._refresh_due(self._current_path(int(item["user_id"])))

    async def _ensure_one(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        user_id: int,
        source_url: str,
        force_refresh: bool,
    ) -> Path | None:
        key = (str(self.cache_dir.resolve()), user_id)
        lock = self._shared_lock(key)
        async with lock:
            current = self._current_path(user_id)
            if not force_refresh and not self._refresh_due(current):
                return current
            if not self._attempt_allowed(key):
                return current

            url = source_url or self._default_url(user_id)
            if not url:
                return current
            path: Path | None = current
            try:
                async with semaphore:
                    response = await client.get(url)
                    response.raise_for_status()
                    if len(response.content) > 8 * 1024 * 1024:
                        self._record_failed_attempt(key, self.refresh_cooldown)
                        return current
                    with Image.open(BytesIO(response.content)) as source:
                        image = source.convert("RGB")
                    encoded = BytesIO()
                    image.save(encoded, format="PNG", optimize=True)
                    encoded_bytes = encoded.getvalue()
                    digest = hashlib.sha256(encoded_bytes).hexdigest()[:16]
                    path = self.cache_dir / f"{user_id}.{digest}.png"
                    if not path.exists():
                        temporary = path.with_suffix(".tmp")
                        try:
                            temporary.write_bytes(encoded_bytes)
                            temporary.replace(path)
                        finally:
                            temporary.unlink(missing_ok=True)
                    path.touch()
                    self._cleanup_versions(user_id, path)
                    return path
            except (httpx.HTTPError, OSError, ValueError):
                self._record_failed_attempt(key, self.refresh_cooldown)
                return current

    def _current_path(self, user_id: int) -> Path | None:
        candidates = [self.cache_dir / f"{user_id}.png"]
        candidates.extend(self.cache_dir.glob(f"{user_id}.*.png"))
        existing: list[tuple[float, Path]] = []
        for path in candidates:
            try:
                existing.append((path.stat().st_mtime, path))
            except OSError:
                continue
        return max(existing, key=lambda item: item[0])[1] if existing else None

    def _refresh_due(self, path: Path | None) -> bool:
        if path is None:
            return True
        # Migrate the pre-existing ``<qq>.png`` cache on first use so a
        # rollout does not preserve an old image merely because its mtime is new.
        if path.suffix == ".png" and path.stem.isdigit():
            return True
        return not self._is_fresh(path, self.refresh_interval)

    @staticmethod
    def _is_fresh(path: Path, ttl: int) -> bool:
        try:
            return time.time() - path.stat().st_mtime < ttl
        except OSError:
            return False

    def _cleanup_versions(self, user_id: int, active: Path) -> None:
        candidates = [self.cache_dir / f"{user_id}.png", *self.cache_dir.glob(f"{user_id}.*.png")]
        for path in candidates:
            if path == active:
                continue
            try:
                path.unlink()
            except OSError:
                pass

    @classmethod
    def cleanup_stale_versions(cls, cache_dir: Path) -> tuple[int, int, int]:
        """Remove cache files that can no longer be selected by ``_current_path``."""

        if not cache_dir.is_dir():
            return (0, 0, 0)
        grouped: dict[int, list[Path]] = {}
        for path in cache_dir.iterdir():
            if not path.is_file() or (match := cls._cache_name.fullmatch(path.name)) is None:
                continue
            grouped.setdefault(int(match.group("user_id")), []).append(path)

        scanned = sum(len(paths) for paths in grouped.values())
        removed = 0
        removed_bytes = 0
        for paths in grouped.values():
            existing: list[tuple[float, str, Path, int]] = []
            for path in paths:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                existing.append((stat.st_mtime, path.name.lower(), path, stat.st_size))
            if not existing:
                continue
            active = max(existing, key=lambda item: (item[0], item[1]))[2]
            for _modified, _name, path, size in existing:
                if path == active:
                    continue
                try:
                    path.unlink()
                except OSError:
                    continue
                removed += 1
                removed_bytes += size
        return (scanned, removed, removed_bytes)

    def _default_url(self, user_id: int) -> str:
        try:
            return self.base_url.format(user_id=user_id)
        except (KeyError, ValueError):
            return ""
