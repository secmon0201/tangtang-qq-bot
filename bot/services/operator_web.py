"""Short-lived capability sessions for operator-only web pages."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

from bot.config import ROOT


SESSION_TTL_SECONDS = 15 * 60
TUNNEL_URL_PATH = ROOT / "data" / "operator_web_tunnel_url.txt"
KINDS = frozenset({"duplicate"})


@dataclass(slots=True)
class OperatorWebSession:
    token: str
    actor_id: int
    kind: str
    expires_at: float
    is_super_admin: bool = False


@dataclass(slots=True)
class OperatorWebSessions:
    _sessions: dict[str, OperatorWebSession] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create(self, actor_id: int, kind: str, *, is_super_admin: bool = False) -> OperatorWebSession:
        if kind not in KINDS:
            raise ValueError("unsupported operator web session")
        self.cleanup()
        session = OperatorWebSession(
            token=secrets.token_urlsafe(32),
            actor_id=int(actor_id),
            kind=kind,
            expires_at=monotonic() + SESSION_TTL_SECONDS,
            is_super_admin=bool(is_super_admin),
        )
        with self._lock:
            self._sessions[session.token] = session
        return session

    def get(self, token: str, kind: str | None = None) -> OperatorWebSession | None:
        self.cleanup()
        with self._lock:
            session = self._sessions.get(token)
            if session is None or (kind is not None and session.kind != kind):
                return None
            return session

    def remaining_seconds(self, session: OperatorWebSession) -> int:
        return max(0, int(session.expires_at - monotonic()))

    def cleanup(self) -> None:
        with self._lock:
            for token, session in tuple(self._sessions.items()):
                if session.expires_at <= monotonic():
                    self._sessions.pop(token, None)


def operator_web_base_url() -> str | None:
    try:
        value = TUNNEL_URL_PATH.read_text(encoding="utf-8").strip().rstrip("/")
    except OSError:
        return None
    return value if value.startswith("https://") else None


def operator_web_url(base_url: str, kind: str, token: str) -> str:
    if kind not in KINDS:
        raise ValueError("unsupported operator web session")
    return f"{base_url.rstrip('/')}/{kind}/{token}"


operator_web_sessions = OperatorWebSessions()
