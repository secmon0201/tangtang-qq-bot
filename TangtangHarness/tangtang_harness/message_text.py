"""Readable voice markers without decoding or retaining audio transport data."""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Mapping


VOICE_MARKER = "[语音]"
VOICE_TYPES = frozenset({"record", "audio", "voice"})
_CQ_VOICE = re.compile(r"\[CQ:(?:record|audio|voice)(?:,[^\]]*)?\]", re.IGNORECASE)
_VOICE_LABEL = re.compile(r"\[(?:record|audio|voice)\]", re.IGNORECASE)
_SERIALIZED_VOICE = re.compile(
    r"(?:['\"]type['\"]\s*:|\btype\s*=)\s*['\"](?:record|audio|voice)['\"]",
    re.IGNORECASE,
)
_SERIALIZED_MEDIA = re.compile(
    r"(?:['\"]type['\"]\s*:|\btype\s*=)\s*['\"](?:record|audio|voice|image|video|file|node)['\"]",
    re.IGNORECASE,
)


def _segment_type(value: Any) -> str:
    return str(value.get("type", "") if isinstance(value, dict)
               else getattr(value, "type", "")).lower()


def _segment_data(value: Any) -> dict[str, Any]:
    data = value.get("data", {}) if isinstance(value, dict) else getattr(value, "data", {})
    return data if isinstance(data, dict) else {}


def _literal_message(node: ast.AST) -> Any:
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal_message(item) for item in node.elts]
    if isinstance(node, ast.Dict):
        return {ast.literal_eval(key): _literal_message(value)
                for key, value in zip(node.keys, node.values)}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "MessageSegment":
        if node.args or {item.arg for item in node.keywords} != {"type", "data"}:
            raise ValueError("unsupported segment representation")
        return {item.arg: ast.literal_eval(item.value) for item in node.keywords}
    return ast.literal_eval(node)


def _contains_voice(value: Any) -> bool:
    if _segment_type(value) in VOICE_TYPES:
        return True
    if isinstance(value, dict):
        return any(_contains_voice(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_voice(item) for item in value)
    return False


def _serialized_voice(value: str, *, voice_only: bool = True) -> Any:
    if not value.lstrip().startswith(("[", "{", "(", "MessageSegment(")):
        return None
    if voice_only and not _SERIALIZED_VOICE.search(value):
        return None
    if not voice_only and not _SERIALIZED_MEDIA.search(value):
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        try:
            parsed = _literal_message(ast.parse(value, mode="eval").body)
        except (SyntaxError, ValueError, TypeError, RecursionError):
            return None
    message_shape = (isinstance(parsed, dict) and any(key in parsed for key in ("type", "segments", "message"))
                     or isinstance(parsed, (list, tuple)) and all(_segment_type(item) for item in parsed))
    return parsed if message_shape and (not voice_only or _contains_voice(parsed)) else None


def _voice_text(value: str) -> str:
    if value.startswith(("data:image/", "base64://")):
        return value
    if "[" not in value and not value.lstrip().startswith(("{", "MessageSegment(")):
        return value
    parsed = _serialized_voice(value)
    if parsed is not None:
        return message_text(parsed)
    return _VOICE_LABEL.sub(VOICE_MARKER, _CQ_VOICE.sub(VOICE_MARKER, value))


def message_text(value: Any, mention_names: Mapping[str, str] | None = None) -> str:
    """Render messages for context/display; audio is always a fixed marker."""
    mention_names = mention_names or {}
    if isinstance(value, str):
        if value.lstrip().startswith(("[", "{", "(", "MessageSegment(")):
            parsed = _serialized_voice(value, voice_only=False)
            if parsed is not None:
                return message_text(parsed, mention_names)
        return _voice_text(value)
    if isinstance(value, (list, tuple)):
        return "".join(message_text(item, mention_names) for item in value)
    kind = _segment_type(value)
    data = _segment_data(value)
    if kind in VOICE_TYPES:
        return VOICE_MARKER
    if kind == "text":
        return _voice_text(str(data.get("text", "")))
    if kind == "reply":
        return ""
    if kind == "at":
        qq = str(data.get("qq", ""))
        name = str(data.get("text", "") or mention_names.get(qq, "")).lstrip("@").strip()
        return "@" + (name or qq)
    if kind == "node":
        return message_text(data.get("content", ""), mention_names)
    if kind:
        return {"image": "[图片]", "face": "[表情]", "video": "[视频]", "file": "[文件]"}.get(kind, f"[{kind}]")
    if isinstance(value, dict):
        if "segments" in value or "message" in value:
            return message_text(value.get("segments", value.get("message")), mention_names)
        return _voice_text(str(value.get("text", "")))
    return str(value) if value is not None else ""


def normalize_voice(value: Any) -> Any:
    """Copy structured data, removing only identified voice transport payloads."""
    if isinstance(value, str):
        return _voice_text(value)
    if _segment_type(value) in VOICE_TYPES:
        original_type = value["type"] if isinstance(value, dict) else value.type
        return {"type": original_type, "data": {}}
    if isinstance(value, dict):
        return {key: normalize_voice(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(normalize_voice(item) for item in value)
    if isinstance(value, list):
        return [normalize_voice(item) for item in value]
    if _segment_type(value):
        return {"type": value.type, "data": normalize_voice(_segment_data(value))}
    return value
