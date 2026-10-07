"""Data shared by the independent QQ runtime, tools and chat service."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .message_text import VOICE_MARKER, VOICE_TYPES, message_text, normalize_voice


@dataclass(frozen=True, slots=True)
class InboundEvent:
    event_id: str
    self_id: int
    user_id: int
    group_id: int | None
    text: str
    segments: tuple[dict[str, Any], ...] = ()
    sender: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    quoted: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        text = normalize_voice(self.text)
        segments = tuple(normalize_voice(self.segments))
        voice_segments = [item for item in segments if item.get("type") in VOICE_TYPES]
        if voice_segments and VOICE_MARKER not in text:
            plain = "".join(str(item.get("data", {}).get("text", ""))
                            for item in segments if item.get("type") == "text")
            text = (message_text([item for item in segments if item.get("type") in {"text", *VOICE_TYPES}])
                    if text == plain else text + VOICE_MARKER)
        quote = normalize_voice(self.quoted)
        if quote is not None:
            content = quote.get("segments", quote.get("message"))
            if content is not None:
                rendered = message_text(content)
                quote_text = str(quote.get("text", ""))
                if not quote_text:
                    quote = {**quote, "text": rendered}
                elif VOICE_MARKER in rendered and VOICE_MARKER not in quote_text:
                    plain = "".join(str(item.get("data", {}).get("text", ""))
                                    for item in content if isinstance(item, dict) and item.get("type") == "text")
                    quote = {**quote, "text": rendered if quote_text == plain else quote_text + VOICE_MARKER}
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "quoted", quote)

    @property
    def session_key(self) -> str:
        return f"group:{self.group_id}" if self.group_id is not None else f"private:{self.user_id}"

    @property
    def nickname(self) -> str:
        return str(self.sender.get("card") or self.sender.get("nickname") or f"成员{self.user_id}")

    @property
    def key(self) -> str:
        return f"{self.self_id}:{self.session_key}:{self.event_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> InboundEvent:
        return cls(
            event_id=str(value["event_id"]), self_id=int(value["self_id"]),
            user_id=int(value["user_id"]),
            group_id=int(value["group_id"]) if value.get("group_id") is not None else None,
            text=str(value.get("text", "")), segments=tuple(value.get("segments", ())),
            sender=dict(value.get("sender", {})), timestamp=float(value.get("timestamp", 0)),
            quoted=value.get("quoted"),
        )

    @classmethod
    def from_onebot(cls, value: dict[str, Any]) -> InboundEvent:
        message = value.get("message", ())
        segments = tuple(message) if isinstance(message, list) else ()
        text = message_text(segments)
        if isinstance(message, str):
            text = message_text(message)
        return cls(event_id=str(value.get("message_id", "")),
                   self_id=int(value.get("self_id", 0)), user_id=int(value.get("user_id", 0)),
                   group_id=int(value["group_id"]) if value.get("message_type") == "group" else None,
                   text=text, segments=segments, sender=dict(value.get("sender", {})),
                   timestamp=float(value.get("time", 0)), quoted=value.get("quoted"))


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    status: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChatResponse:
    request_id: str
    session_key: str
    messages: list[str]
    usage: dict[str, Any]
    profile_id: str
    payload: dict[str, Any]
    status: str = "generated"
    error: str = ""
    user_content: Any = ""
    snapshot_revision: int = 0
    purpose: str = "chat"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
