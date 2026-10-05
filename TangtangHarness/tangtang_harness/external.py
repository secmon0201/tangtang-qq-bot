"""Independent Core and speech connections; never supervises legacy processes."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import wave
import io
import uuid
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode, unquote, urlparse

import httpx
from loguru import logger
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .onebot import MessageSegment, OneBotError, wire_message
from .core_protocol import game_prefix
from .types import InboundEvent


def core_packet(event: InboundEvent, *, operator_ids=()) -> dict:
    content = []
    command_text = True
    for segment in event.segments or ({"type": "text", "data": {"text": event.text}},):
        kind, data = segment["type"], segment.get("data", {})
        if kind == "text":
            text = str(data.get("text", ""))
            if command_text and text.strip():
                text = text.lstrip().removeprefix("#").lstrip()
                command_text = False
            content.append({"type": kind, "data": text})
        elif kind == "at":
            content.append({"type": kind, "data": str(data.get("qq", ""))})
        elif kind in {"image", "record", "video"}:
            content.append({"type": kind, "data": data.get("url") or data.get("file", "")})
        elif kind == "file":
            content.append({"type": "file", "data": str(data.get("name", "file")) + "|" + str(data.get("url") or data.get("file", ""))})
        elif kind == "reply":
            content.append({"type": "reply_id", "data": str(data.get("id", ""))})
            if event.quoted:
                content.append({"type": "reply", "data": event.quoted.get("text", "")})
                for quoted_segment in event.quoted.get("message", ()):
                    if quoted_segment.get("type") == "image":
                        image = quoted_segment.get("data", {})
                        content.append({"type": "image", "data": image.get("url") or image.get("file", "")})
    role = event.sender.get("role")
    return {"bot_id": "onebot", "bot_self_id": str(event.self_id), "msg_id": event.event_id,
            "user_type": "group" if event.group_id is not None else "direct",
            "group_id": str(event.group_id) if event.group_id is not None else None,
            "user_id": str(event.user_id), "sender": {"nickname": event.nickname, "user_id": str(event.user_id)},
            "user_pm": 1 if event.user_id in operator_ids else 2 if role == "owner" else 3 if role == "admin" else 6,
            "content": content}


def core_segments(content: list[dict]) -> list[dict]:
    result = []
    for part in content:
        kind, data = part.get("type"), part.get("data")
        if kind in {"text", "markdown"}:
            result.extend(wire_message(str(data).replace("link://", "")))
        elif kind == "at":
            result.extend(wire_message(MessageSegment.at(data)))
        elif kind == "reply":
            result.extend(wire_message(MessageSegment.reply(data)))
        elif kind in {"image", "record", "video"}:
            value = str(data).replace("link://", "")
            if not value.startswith(("http", "base64://", "file://")):
                value = "base64://" + value
            result.append({"type": kind, "data": {"file": value}})
    return result


def core_nodes(items, self_id):
    nodes = []
    for item in items:
        if isinstance(item, str):
            item = {"type": "text", "data": item}
        if item.get("type") == "node":
            content = core_nodes(item["data"], self_id)
        else:
            content = core_segments([item] if "type" in item else item["content"])
        nodes.append(wire_message(MessageSegment.node_custom(self_id, "游戏助手", content))[0])
    return nodes


class CoreBridge:
    def __init__(self, runtime):
        self.runtime = runtime
        self.socket = None
        self.error = ""
        self.task = None
        self.receive_tasks = {}
        self.receive_connected = {}
        self.receive_lock = asyncio.Lock()
        self.recent_frames = {}

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="harness-core")
        settings = self.runtime.config.extra.get("core", {})
        for identity in settings.get("receive_identities", []):
            if identity != settings.get("identity", "TangtangHarness") and identity not in self.receive_tasks:
                self.receive_connected[identity] = False
                self.receive_tasks[identity] = asyncio.create_task(self.run(identity), name="harness-core-receive:" + identity)

    async def run(self, receive_identity=None):
        while True:
            settings = self.runtime.config.extra.get("core", {})
            if receive_identity and receive_identity not in settings.get("receive_identities", []):
                return
            if self.runtime.config.mode != "live" or not settings.get("enabled", False):
                await asyncio.sleep(5)
                continue
            identity = receive_identity or settings.get("identity", "TangtangHarness")
            url = settings.get("url", "ws://127.0.0.1:8765").rstrip("/") + "/ws/" + identity
            if settings.get("token"):
                url += "?" + urlencode({"token": settings["token"]})
            try:
                async with connect(url, open_timeout=5, max_size=2**26) as socket:
                    if receive_identity is None:
                        self.socket, self.error = socket, ""
                    else:
                        self.receive_connected[receive_identity] = True
                    async for raw in socket:
                        try:
                            await self.receive(json.loads(raw), socket=socket)
                        except (OneBotError, OSError, ValueError, httpx.HTTPError) as exc:
                            logger.error("Core 回程发送失败：{}: {}", type(exc).__name__, exc)
                            self.runtime.publish("core", {"status": "delivery_failed", "error": str(exc)})
            except (OSError, TimeoutError, RuntimeError, ValueError, ConnectionClosed, InvalidHandshake) as exc:
                if receive_identity is None:
                    self.error = type(exc).__name__
                    logger.warning("Core 主连接暂时断开：{}", type(exc).__name__)
                else:
                    logger.warning("Core 订阅接收连接暂时断开：{}", type(exc).__name__)
            finally:
                if receive_identity is None:
                    self.socket = None
                else:
                    self.receive_connected[receive_identity] = False
            await asyncio.sleep(15)

    async def forward(self, event: InboundEvent):
        if self.runtime.config.mode != "live" or self.socket is None or not self.runtime.config.extra.get("core", {}).get("enabled", False):
            raise RuntimeError("独立 Core 连接尚未就绪")
        # GsUID.Core reads OneBot bridge frames with receive_bytes(), so the
        # independent connection must send binary UTF-8 JSON frames.
        await self.socket.send(json.dumps(core_packet(event, operator_ids=self.runtime.store.get_setting("operator_ids", [])), ensure_ascii=False).encode("utf-8"))

    def source_for(self, packet):
        msg_id = str(packet.get("msg_id", ""))
        source = self.runtime.core_sources.get(msg_id)
        if source is not None or not msg_id:
            return source
        session = ("group:" if packet.get("target_type") == "group" else "private:") + str(packet.get("target_id"))
        row = self.runtime.store.event(f"{self.runtime.bot.self_id}:{session}:{msg_id}")
        if row:
            source = InboundEvent.from_dict(row["payload"])
            prefix = self.runtime.config.extra.get("test_prefix", "#harness")
            if prefix and source.text.lstrip().startswith(prefix):
                source = replace(source, text=source.text.lstrip()[len(prefix):].strip())
            return source
        for route in self.runtime.store.get_setting("core_subscription_sources", []):
            target_matches = str(route.get("group_id") if packet.get("target_type") == "group" else route.get("user_id")) == str(packet.get("target_id"))
            if str(route.get("event_id", "")) == msg_id and str(route.get("self_id")) == str(self.runtime.bot.self_id) and target_matches:
                return InboundEvent.from_dict(route)
        return None

    def target_for(self, packet, source):
        kind, raw_target = packet.get("target_type"), packet.get("target_id")
        if kind not in {"group", "direct"} or not str(raw_target).isdigit():
            return None
        target = int(raw_target)
        if kind == "group" and not self.runtime.group_delivery_allowed(target):
            return None
        if source is not None:
            if not self.runtime.game_allowed(source):
                return None
            if kind == "group" and target == source.group_id:
                if self.runtime.in_scope(source):
                    return source
                original = self.runtime.store.event(source.key)
                if original and self.runtime.in_scope(InboundEvent.from_dict(original["payload"])):
                    return source
                return None
            source = replace(source, group_id=target if kind == "group" else None,
                             user_id=source.user_id if kind == "group" else target)
        else:
            source = InboundEvent("core-push:" + uuid.uuid4().hex, self.runtime.bot.self_id,
                                  self.runtime.bot.self_id if kind == "group" else target,
                                  target if kind == "group" else None, "", timestamp=time.time())
        if not self.runtime.in_scope(source) or self.runtime._filtered(source):
            return None
        if not self.runtime.config.extra.get("core", {}).get("enabled", False) or not self.runtime.store.get_setting("game_api_enabled", True):
            return None
        if kind == "group":
            feature = game_prefix(source.text)
            features = (feature,) if feature else ("nte", "ww")
            if not any(self.runtime.tools.domains.effective_feature_enabled(target, item) for item in features):
                return None
        return source

    async def receive(self, packet, *, socket=None):
        async with self.receive_lock:
            await self._receive(packet, socket or self.socket)

    async def _receive(self, packet, socket):
        content = packet.get("content") or []
        if not content or str(content[0].get("type", "")).startswith("log"):
            return
        if self.runtime.config.mode != "live" or str(packet.get("bot_self_id") or "") not in {"", str(self.runtime.bot.self_id)}:
            return
        msg_id = str(packet.get("msg_id", ""))
        source = self.target_for(packet, self.source_for(packet))
        if source is None:
            return
        target = str(source.group_id if source.group_id is not None else source.user_id)
        digest = hashlib.sha256(json.dumps({k: v for k, v in packet.items() if k != "echo"}, sort_keys=True).encode()).hexdigest()
        now = time.monotonic()
        self.recent_frames = {key: value for key, value in self.recent_frames.items() if now - value[0] < 30}
        previous = self.recent_frames.get(digest)
        if previous and previous[1] is not socket:
            await self.recall(packet, source, socket, previous[2])
            return
        ids = []
        pending = []
        async def flush():
            if pending:
                ids.extend(await self.runtime.deliver(source, list(pending), request_id="core:" + msg_id))
                pending.clear()
        for part in content:
            kind, data = part.get("type"), part.get("data")
            if kind in {"excute_delete_message", "excute_ban_user", "file", "node"}:
                await flush()
            if kind == "excute_delete_message":
                if not self.runtime.group_delivery_allowed(source.group_id):
                    return
                await self.runtime.bot.call_api("delete_msg", message_id=str(data["message_id"]))
            elif kind == "excute_ban_user" and source.group_id is not None:
                if not self.runtime.group_delivery_allowed(source.group_id):
                    return
                await self.runtime.bot.call_api("set_group_ban", group_id=source.group_id,
                    user_id=int(data["user_id"]), duration=int(data.get("duration", 0)))
            elif kind == "file":
                name, value = str(data).split("|", 1)
                file = await self.materialize_file(name, value)
                if not self.runtime.group_delivery_allowed(source.group_id):
                    return
                params = {"file": str(file), "name": name}
                params["group_id" if source.group_id is not None else "user_id"] = int(target)
                await self.runtime.bot.call_api("upload_group_file" if source.group_id is not None else "upload_private_file", **params)
            elif kind == "node":
                nodes = core_nodes(data, source.self_id)
                ids.extend(await self.runtime.deliver_forward(source, nodes, request_id="core:" + msg_id))
            else:
                pending.extend(core_segments([part]))
        await flush()
        self.recent_frames[digest] = (now, socket, ids)
        await self.recall(packet, source, socket, ids)

    async def recall(self, packet, source, socket, ids):
        if packet.get("echo") and socket:
            await socket.send(json.dumps({"bot_id": "onebot", "bot_self_id": str(source.self_id), "user_id": "",
                "content": [{"type": "recall_message_id", "data": {"echo": packet["echo"], "id": ids[0] if len(ids) == 1 else ids}}]}).encode("utf-8"))

    async def materialize_file(self, name, value):
        if value.startswith("link://"):
            value = value[7:]
        if value.startswith("file://"):
            parsed = urlparse(value)
            return Path(unquote(parsed.path).lstrip("/") if parsed.path[2:3] == ":" else unquote(parsed.path))
        if value.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(value)
                response.raise_for_status()
                raw = response.content
        elif Path(value).is_absolute():
            return Path(value)
        else:
            raw = base64.b64decode(value.removeprefix("base64://"), validate=True)
        path = self.runtime.config.root / "runtime" / "core-files" / (hashlib.sha256(raw).hexdigest() + Path(name).suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    async def close(self):
        tasks = [*self.receive_tasks.values()]
        if self.task:
            tasks.append(self.task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None
        self.receive_tasks.clear()
        self.receive_connected.clear()


class SpeechClient:
    def __init__(self, root: Path):
        self.root = root
        self.lock = asyncio.Lock()
        self.ready = False
        self.error = ""
        self._ready_endpoint = ""

    async def check(self, settings: dict):
        endpoint = str(settings.get("endpoint", "http://127.0.0.1:9890")).rstrip("/")
        # CPU synthesis serves one job at a time; a busy /docs is not an outage.
        if self.lock.locked() and endpoint == self._ready_endpoint:
            return
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(endpoint + "/docs")
                self.ready = response.status_code == 200
                self.error = "" if self.ready else f"TTS /docs HTTP {response.status_code}"
                self._ready_endpoint = endpoint if self.ready else ""
        except httpx.HTTPError as exc:
            self.ready, self._ready_endpoint, self.error = False, "", str(exc)

    async def synthesize(self, text: str, settings: dict) -> Path:
        endpoint = str(settings.get("endpoint", "http://127.0.0.1:9890")).rstrip("/")
        if not self.ready or self._ready_endpoint != endpoint:
            raise RuntimeError("独立语音实例未就绪；可继续使用文字")
        params = {"text": text, "text_lang": settings.get("text_lang", "zh"), "ref_audio_path": settings.get("ref_audio_path", ""),
                  "prompt_text": settings.get("prompt_text", ""), "prompt_lang": settings.get("prompt_lang", "zh"),
                  "media_type": "wav", "streaming_mode": False}
        for name in ("top_k", "top_p", "temperature", "text_split_method", "batch_size", "speed_factor",
                     "fragment_interval", "seed", "repetition_penalty"):
            if name in settings:
                params[name] = settings[name]
        reference = Path(str(params["ref_audio_path"]))
        if reference.is_file():
            reference_hash = hashlib.sha256(reference.read_bytes()).hexdigest()
        else:
            reference_hash = "missing"
        voice_config = self.root / "runtime" / "speech-config" / "tts_infer.yaml"
        voice_config_hash = hashlib.sha256(voice_config.read_bytes()).hexdigest() if voice_config.is_file() else ""
        digest = hashlib.sha256(json.dumps({"endpoint": endpoint, "reference_hash": reference_hash,
                                           "voice_config_hash": voice_config_hash, **params}, sort_keys=True).encode()).hexdigest()
        path = self.root / "runtime" / "speech" / (digest + ".wav")
        timeout_seconds = float(settings.get("timeout_seconds", 45))
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self.lock:
                    if path.exists():
                        raw = path.read_bytes()
                        try:
                            self._validate_wav(raw)
                        except (ValueError, wave.Error):
                            path.unlink()
                        else:
                            return path
                    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                        response = await client.post(endpoint + "/tts", json=params)
                        response.raise_for_status()
                        raw = response.content
                    self._validate_wav(raw)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
        except (asyncio.TimeoutError, TimeoutError):
            self.ready, self.error = False, "TTS 请求超时"
            raise RuntimeError("独立语音实例请求超时；可继续使用文字") from None
        except (httpx.HTTPError, ValueError, OSError, wave.Error) as exc:
            self.ready, self.error = False, f"TTS 输出无效（{type(exc).__name__}）"
            raise RuntimeError("独立语音实例未返回有效 WAV；可继续使用文字") from None
        return path

    @staticmethod
    def _validate_wav(raw: bytes) -> None:
        if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            raise ValueError("not a RIFF/WAVE payload")
        with wave.open(io.BytesIO(raw), "rb") as wav:
            if wav.getnframes() <= 0 or wav.getframerate() <= 0:
                raise ValueError("empty WAV")
            if wav.getnframes() / wav.getframerate() > 180:
                raise ValueError("WAV duration exceeds limit")
