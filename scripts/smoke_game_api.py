"""End-to-end game-interface smoke test without touching the live NapCat bot.

Run a second, isolated NoneBot instance on port 18081 with its own temporary
SQLite database, .env mirror, and GsUID Core bot id. The real bot keeps its
own OneBot connection, so QQ/NapCat login is never disturbed.

Server: .venv/Scripts/python.exe -u scripts/smoke_game_api.py --server
Client: .venv/Scripts/python.exe -u scripts/smoke_game_api.py --client
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import websockets
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent.parent
SMOKE_DB = ROOT / "data" / "smoke_game_api.db"
SMOKE_ENV = ROOT / "data" / "smoke_game_api.env"
SMOKE_PORT = 18081
SMOKE_BOT_ID = "SmokeGameApiBot"

POSITIVE_COMMANDS = (
    ("#nte帮助", True, None, 45.0),
    ("nte帮助", True, None, 45.0),
    ("#NTE帮助", True, None, 45.0),
    ("NTE帮助", True, None, 45.0),
    ("#nte原版帮助", True, None, 25.0),
    ("#nte早雾评分排名", True, None, 25.0),
    ("#nte早雾评分排名 页2", True, None, 25.0),
    ("#nte最强排行", True, None, 25.0),
    ("#nte群最强排行", True, None, 25.0),
    ("#nte公告", True, None, 45.0),
    ("#nte角色列表", True, None, 45.0),
    ("#nte早雾配队", True, None, 45.0),
    ("#nte早雾攻略", True, None, 45.0),
    ("#nte早雾图鉴", True, None, 45.0),
    ("#nte查看", True, None, 25.0),
    ("#nte登录", True, None, 30.0),
    ("#nte退出登录", True, None, 25.0),
)

ADMIN_COMMANDS = (
    ("#游戏接口 状态", True, None, 15.0),
    ("#游戏接口 关闭", True, None, 15.0),
    ("#nte帮助", False, None, 10.0),
    ("#游戏接口 开启", True, None, 15.0),
    ("#游戏接口 状态", True, None, 15.0),
)

SCOPE_COMMANDS = (
    ("#功能范围 游戏接口 移除 {group}", True, None, 20.0),
    ("#nte帮助", False, None, 10.0),
    ("#功能范围 游戏接口 添加 {group}", True, None, 20.0),
    ("#nte帮助", True, None, 45.0),
)

NEGATIVE_COMMANDS = (
    ("#yh帮助", False, None, 10.0),
    ("#gs帮助", False, None, 10.0),
    ("#ww帮助", False, None, 10.0),
    ("#ntext", False, None, 10.0),
)


def _prepare_server_env() -> None:
    values = dotenv_values(ROOT / ".env")
    for key, value in values.items():
        os.environ.setdefault(key, value or "")
    os.environ["PORT"] = str(SMOKE_PORT)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["COMMAND_START"] = json.dumps(["#"])
    os.environ["BOT_TRANSPORT"] = "onebot"
    os.environ["BOT_DB_PATH"] = str(SMOKE_DB)
    os.environ["GSUID_ENABLED"] = "true"
    os.environ["gsuid_core_host"] = os.environ.get("gsuid_core_host", "127.0.0.1")
    os.environ["gsuid_core_port"] = os.environ.get("gsuid_core_port", "8765")
    os.environ["gsuid_core_botid"] = SMOKE_BOT_ID
    os.environ["STATS_REALTIME_ENABLED"] = "false"
    os.environ["CODEX_WORKER_ENABLED"] = "false"
    os.environ["CODEX_COMPLETION_NOTIFY_ENABLED"] = "false"


def _run_server() -> int:
    _prepare_server_env()
    if SMOKE_DB.exists():
        SMOKE_DB.unlink()
    if SMOKE_ENV.exists():
        SMOKE_ENV.unlink()

    from bot.services import env_sync

    env_sync.ENV_PATH = SMOKE_ENV

    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter

    nonebot.init()
    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    for name in (
        "bot.plugins.scope",
        "bot.plugins.game_api",
        "bot.plugins.nte_game_ui",
        "bot.plugins.commands",
        "GenshinUID",
    ):
        if nonebot.load_plugin(name) is None:
            raise RuntimeError(f"failed to load plugin: {name}")
    nonebot.run()
    return 0


def _response_data(action: str, user_id: int) -> object:
    if action == "get_group_member_info":
        return {"role": "owner", "nickname": "smoke-game-api", "card": ""}
    if action == "get_group_member_list":
        return [{"user_id": user_id, "nickname": "smoke-game-api", "card": "", "role": "owner"}]
    if action == "get_group_info":
        return {"group_name": "smoke-game-api-group"}
    if action == "get_group_msg_history":
        return []
    if action.startswith("send_"):
        return {"message_id": int(time.time() * 1000) % 2_000_000_000}
    return {}


def _output_actions(actions: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        action
        for action in actions
        if action
        in {
            "send_msg",
            "send_group_msg",
            "send_group_forward_msg",
            "send_private_msg",
            "send_private_forward_msg",
        }
    )


async def _run_command(
    websocket,
    self_id: int,
    command: str,
    message_id: int,
    user_id: int,
    group_id: int,
    command_timeout: float,
    idle_timeout: float,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    await websocket.send(
        json.dumps(
            {
                "time": int(time.time()),
                "self_id": self_id,
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "message_id": message_id,
                "user_id": user_id,
                "group_id": group_id,
                "message": [{"type": "text", "data": {"text": command}}],
                "raw_message": command,
                "font": 0,
                "sender": {
                    "user_id": user_id,
                    "nickname": "smoke-game-api",
                    "card": "",
                    "role": "owner",
                },
            }
        )
    )
    actions: list[str] = []
    texts: list[str] = []
    deadline = time.monotonic() + command_timeout
    idle_deadline = time.monotonic() + idle_timeout
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            if time.monotonic() >= idle_deadline:
                break
            continue
        data = json.loads(raw)
        action = data.get("action")
        if not action:
            continue
        actions.append(action)
        params = data.get("params") or {}
        message = params.get("message")
        if isinstance(message, list):
            for segment in message:
                if isinstance(segment, dict) and segment.get("type") == "text":
                    text = (segment.get("data") or {}).get("text")
                    if text:
                        texts.append(str(text))
        await websocket.send(
            json.dumps(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": _response_data(action, user_id),
                    "echo": data.get("echo"),
                }
            )
        )
        idle_deadline = time.monotonic() + idle_timeout
    return tuple(actions), tuple(texts)


async def _run_client() -> int:
    values = dotenv_values(ROOT / ".env")
    self_id = int(values["NAPCAT_QQ_ID"] or 0)
    access_token = values.get("ONEBOT_ACCESS_TOKEN")
    if not self_id or not access_token:
        raise RuntimeError(".env must contain NAPCAT_QQ_ID and ONEBOT_ACCESS_TOKEN")
    managed_groups = tuple(
        int(item.strip())
        for item in (values.get("MANAGED_GROUP_IDS") or "").split(",")
        if item.strip().isdigit()
    )
    group_id = int(os.getenv("SMOKE_GROUP_ID") or (managed_groups[0] if managed_groups else 0))
    operator_values = tuple(
        int(item.strip())
        for item in (values.get("BOT_OPERATOR_IDS") or "").split(",")
        if item.strip().isdigit()
    )
    user_id = int(os.getenv("SMOKE_USER_ID") or (operator_values[0] if operator_values else 0))
    if not group_id or not user_id:
        raise RuntimeError(".env must contain a managed group and BOT_OPERATOR_IDS")

    headers = {
        "X-Self-ID": str(self_id),
        "Authorization": f"Bearer {access_token}",
    }
    all_results: list[tuple[str, bool, tuple[str, ...]]] = []
    captured_texts: dict[str, tuple[str, ...]] = {}
    async with websockets.connect(
        f"ws://127.0.0.1:{SMOKE_PORT}/onebot/v11/ws",
        additional_headers=headers,
        max_size=None,
    ) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "time": int(time.time()),
                    "self_id": self_id,
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                }
            )
        )
        await asyncio.sleep(3)
        message_id = 990100
        for phase in (POSITIVE_COMMANDS, ADMIN_COMMANDS, NEGATIVE_COMMANDS):
            for command, expect_output, _, timeout in phase:
                actions, texts = await _run_command(
                    websocket,
                    self_id,
                    command,
                    message_id,
                    user_id,
                    group_id,
                    timeout,
                    15.0 if expect_output else 4.0,
                )
                message_id += 1
                all_results.append((command, expect_output, _output_actions(actions)))
                captured_texts.setdefault(command, ())
                captured_texts[command] = (*captured_texts[command], *texts)
        for command, expect_output, _, timeout in SCOPE_COMMANDS:
            text = command.format(group=group_id)
            actions, texts = await _run_command(
                websocket,
                self_id,
                text,
                message_id,
                user_id,
                group_id,
                timeout,
                15.0 if expect_output else 4.0,
            )
            message_id += 1
            all_results.append((text, expect_output, _output_actions(actions)))
            captured_texts.setdefault(command, ())
            captured_texts[command] = (*captured_texts[command], *texts)
        # Out-of-managed-scope group must stay silent.
        actions, _texts = await _run_command(
            websocket, self_id, "#nte帮助", message_id, user_id, 999999999, 10.0, 4.0
        )
        all_results.append(("#nte帮助@999999999", False, _output_actions(actions)))

    failures = 0
    for command, expect_output, outputs in all_results:
        ok = bool(outputs) if expect_output else not outputs
        if not ok:
            failures += 1
        status = "PASS" if ok else ("NO_OUTPUT" if expect_output else "UNEXPECTED_OUTPUT")
        print(f"{command}: {status}; output={','.join(outputs) or 'none'}", flush=True)
    login_link = next(
        (
            text
            for text in captured_texts.get("#nte登录", ())
            if "trycloudflare.com" in text or "/nte/i/" in text
        ),
        None,
    )
    if login_link is None:
        failures += 1
        print("#nte登录: LOGIN_LINK_MISSING", flush=True)
    else:
        print(f"#nte登录: LOGIN_LINK_OK {login_link}", flush=True)
    if failures:
        print(f"FAILED={failures}")
        return 1
    print(f"PASS: {len(all_results)} game-interface checks completed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--server", action="store_true")
    mode.add_argument("--client", action="store_true")
    args = parser.parse_args()
    if args.server:
        return _run_server()
    return asyncio.run(_run_client())


if __name__ == "__main__":
    sys.exit(main())
