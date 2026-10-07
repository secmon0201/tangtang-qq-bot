"""OneBot v11 values and reverse-WebSocket RPC; no bot framework required."""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .message_text import message_text


@dataclass
class MessageSegment:
    type: str
    data: dict[str, Any]

    @classmethod
    def text(cls, text: str):
        return cls("text", {"text": str(text)})

    @classmethod
    def image(cls, file: Any, **kwargs):
        return cls("image", {"file": cls._file(file), **kwargs})

    @classmethod
    def record(cls, file: Any, **kwargs):
        return cls("record", {"file": cls._file(file), **kwargs})

    @classmethod
    def at(cls, user_id: Any):
        return cls("at", {"qq": str(user_id)})

    @classmethod
    def reply(cls, message_id: Any):
        return cls("reply", {"id": str(message_id)})

    @classmethod
    def node_custom(cls, user_id: Any, nickname: str, content: Any):
        return cls("node", {"uin": str(user_id), "name": nickname, "content": wire_message(content)})

    @classmethod
    def node(cls, id: Any):
        return cls("node", {"id": str(id)})

    @staticmethod
    def _file(value):
        if isinstance(value, bytes):
            return "base64://" + base64.b64encode(value).decode("ascii")
        if isinstance(value, Path):
            return value.resolve().as_uri()
        return str(value)

    def __str__(self):
        if self.type in {"record", "audio", "voice"}:
            return message_text(self)
        return str(self.data.get("text", "")) if self.type == "text" else f"[{self.type}]"

    def __add__(self, other):
        return Message([self]) + other

    def __radd__(self, other):
        return Message(other) + self


class Message(list):
    def __init__(self, value=None):
        if value is None:
            parts = []
        elif isinstance(value, str):
            parts = [MessageSegment.text(value)]
        elif isinstance(value, MessageSegment):
            parts = [value]
        elif isinstance(value, dict):
            parts = [MessageSegment(value["type"], dict(value.get("data", {})))]
        else:
            parts = [MessageSegment(v["type"], dict(v.get("data", {}))) if isinstance(v, dict) else v for v in value]
        super().__init__(parts)

    def extract_plain_text(self):
        return "".join(str(v.data.get("text", "")) for v in self if v.type == "text")

    def __str__(self):
        return "".join(str(v) for v in self)

    def __getitem__(self, key):
        if isinstance(key, str):
            return Message(v for v in self if v.type == key)
        return super().__getitem__(key)

    def __add__(self, other):
        return Message([*self, *Message(other)])

    def __radd__(self, other):
        return Message(other) + self

    def __iadd__(self, other):
        self.extend(Message(other))
        return self


def wire_message(value):
    return [{"type": v.type, "data": v.data} for v in Message(value)]


class OneBotError(RuntimeError):
    def __init__(self, message: str, retcode: int = -1):
        super().__init__(message)
        self.retcode = retcode


class NativeBot:
    def __init__(self, *, timeout: float = 10, send_interval: float = 0.3):
        self.self_id = 0
        self.socket = None
        self.timeout = timeout
        self.send_interval = send_interval
        self.pending: dict[str, asyncio.Future] = {}
        self._send_lock = asyncio.Lock()
        self.connected_at = 0.0
        self.can_write = lambda: True

    @property
    def connected(self):
        return self.socket is not None

    async def attach(self, websocket, self_id: int):
        if self.socket is not None:
            raise OneBotError("Harness already has a OneBot connection")
        self.socket = websocket
        self.self_id = int(self_id)
        self.connected_at = asyncio.get_running_loop().time()

    def detach(self, socket=None):
        if socket is not None and socket is not self.socket:
            return
        self.socket = None
        for future in self.pending.values():
            if not future.done():
                future.set_exception(OneBotError("SnowLuma disconnected"))
        self.pending.clear()

    def receive_response(self, packet: dict):
        future = self.pending.pop(str(packet.get("echo", "")), None)
        if future and not future.done():
            if packet.get("status") == "ok" and int(packet.get("retcode", 0)) == 0:
                future.set_result(packet.get("data"))
            else:
                future.set_exception(OneBotError(str(packet.get("message") or packet.get("wording") or "QQ action failed"), int(packet.get("retcode", -1))))

    async def call_api(self, action: str, **params):
        if action.startswith("send_"):
            async with self._send_lock:
                result = await self._rpc(action, params)
                await asyncio.sleep(self.send_interval)
                return result
        return await self._rpc(action, params)

    async def _rpc(self, action, params):
        if not getattr(self, "can_write", lambda: True)() and not action.startswith(("get_", "can_")):
            raise OneBotError("观察或回放模式不执行 QQ 写操作")
        if self.socket is None:
            raise OneBotError("SnowLuma is not connected")
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[echo] = future
        try:
            await self.socket.send_json({"action": action, "params": params, "echo": echo})
            return await asyncio.wait_for(future, self.timeout)
        finally:
            self.pending.pop(echo, None)

    def __getattr__(self, action):
        if action.startswith("_"):
            raise AttributeError(action)
        async def call(**params):
            for key in ("message", "messages"):
                if key in params and key == "message":
                    params[key] = wire_message(params[key])
            return await self.call_api(action, **params)
        return call

    async def send(self, event, message):
        params = {"message": wire_message(message)}
        action = "send_group_msg" if event.group_id is not None else "send_private_msg"
        params["group_id" if event.group_id is not None else "user_id"] = event.group_id if event.group_id is not None else event.user_id
        result = await self.call_api(action, **params)
        if not isinstance(result, dict) or not result.get("message_id"):
            raise OneBotError("QQ did not confirm a message_id")
        return result


def parse_event(packet):
    from tangtang_harness.types import InboundEvent
    segments = packet.get("message") or []
    if isinstance(segments, str):
        segments = [{"type": "text", "data": {"text": segments}}]
    text = message_text(segments)
    return InboundEvent(
        event_id=str(packet.get("message_id") or packet.get("id") or uuid.uuid4().hex),
        self_id=int(packet.get("self_id", 0)), user_id=int(packet.get("user_id", 0)),
        group_id=int(packet["group_id"]) if packet.get("message_type") == "group" else None,
        text=text, segments=tuple(segments), sender=dict(packet.get("sender") or {}),
        timestamp=float(packet.get("time", 0)), quoted=packet.get("quoted"),
    )
