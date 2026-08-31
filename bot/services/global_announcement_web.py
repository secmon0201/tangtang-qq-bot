"""Short-lived, capability-based sessions for the global-announcement web UI."""

from __future__ import annotations

import secrets
import threading
from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

from PIL import Image

from bot.config import ROOT


SESSION_TTL_SECONDS = 15 * 60
TUNNEL_URL_PATH = ROOT / "data" / "global_announcement_tunnel_url.txt"
UPLOAD_DIR = ROOT / "data" / "global_announcement_uploads"
WEB_PREVIEW_MAX_WIDTH = 540
WEB_PREVIEW_MAX_HEIGHT = 3200


def announcement_web_preview_bytes(poster_path: Path) -> bytes:
    """Encode a small WebP preview while retaining the full local poster for delivery."""
    with Image.open(poster_path) as source:
        preview = source.convert("RGBA")
    preview.thumbnail(
        (WEB_PREVIEW_MAX_WIDTH, WEB_PREVIEW_MAX_HEIGHT),
        Image.Resampling.LANCZOS,
    )
    encoded = BytesIO()
    preview.save(encoded, format="WEBP", quality=78, method=0)
    return encoded.getvalue()


@dataclass(slots=True)
class AnnouncementWebSession:
    token: str
    actor_id: int
    expires_at: float
    draft_path: Path | None = None
    draft_text: str = ""
    draft_extra_text: str = ""
    draft_at_all: bool = False
    delivered: bool = False
    sending: bool = False
    upload_path: Path | None = None


@dataclass(slots=True)
class AnnouncementWebSessions:
    _sessions: dict[str, AnnouncementWebSession] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create(self, actor_id: int) -> AnnouncementWebSession:
        self.cleanup()
        session = AnnouncementWebSession(
            token=secrets.token_urlsafe(32),
            actor_id=int(actor_id),
            expires_at=monotonic() + SESSION_TTL_SECONDS,
        )
        with self._lock:
            self._sessions[session.token] = session
        return session

    def get(self, token: str) -> AnnouncementWebSession | None:
        self.cleanup()
        with self._lock:
            return self._sessions.get(token)

    def begin_send(self, token: str) -> AnnouncementWebSession:
        with self._lock:
            session = self._sessions.get(token)
            if session is None or session.expires_at <= monotonic():
                raise ValueError("链接已过期，请重新发送 #公告网页 获取新链接。")
            if session.delivered or session.sending:
                raise ValueError("此链接已提交，请重新发送 #公告网页 创建新的公告。")
            session.sending = True
            return session

    def finish_send(self, token: str) -> None:
        with self._lock:
            session = self._sessions.get(token)
            if session is not None:
                session.sending = False
                session.delivered = True

    def fail_send(self, token: str) -> None:
        with self._lock:
            session = self._sessions.get(token)
            if session is not None:
                session.sending = False

    def remaining_seconds(self, session: AnnouncementWebSession) -> int:
        return max(0, int(session.expires_at - monotonic()))

    def cleanup(self) -> None:
        expired: list[AnnouncementWebSession] = []
        with self._lock:
            for token, session in tuple(self._sessions.items()):
                if session.expires_at <= monotonic():
                    expired.append(self._sessions.pop(token))
        for session in expired:
            for path in (session.upload_path,):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass


def announcement_web_base_url() -> str | None:
    """Return the public, path-restricted tunnel URL when the operator enabled it."""
    try:
        value = TUNNEL_URL_PATH.read_text(encoding="utf-8").strip().rstrip("/")
    except OSError:
        return None
    return value if value.startswith("https://") else None


web_sessions = AnnouncementWebSessions()
