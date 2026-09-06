"""Run a local OneBot smoke test against an already running bot and Core."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import dataclass

import websockets
from dotenv import dotenv_values


DEFAULT_COMMANDS = (
    "#nte帮助",
    "#nte查看",
    "#nte查询",
    "#nte角色",
    "#nte角色练度",
    "#nte实时信息",
    "#nte刷新令牌",
    "#nte签到",
    "#nte签到日历",
    "#nte公告",
    "#nte角色列表",
    "#nte早雾配队",
    "#nte早雾攻略",
    "#nte早雾图鉴",
    "#nte登录",
    "#nte退出登录",
)
EXPECTED_OUTPUT = {
    "#nte帮助",
    "#nte查看",
    "#nte查询",
    "#nte角色",
    "#nte角色练度",
    "#nte实时信息",
    "#nte刷新令牌",
    "#nte签到",
    "#nte签到日历",
    "#nte公告",
    "#nte角色列表",
    "#nte早雾配队",
    "#nte早雾攻略",
    "#nte早雾图鉴",
    "#nte登录",
    "#nte退出登录",
}


@dataclass(slots=True)
class CommandResult:
    command: str
    actions: tuple[str, ...]
    elapsed: float

    @property
    def output_actions(self) -> tuple[str, ...]:
        return tuple(
            action
            for action in self.actions
            if action
            in {
                "send_msg",
                "send_group_msg",
                "send_group_forward_msg",
                "send_private_msg",
                "send_private_forward_msg",
            }
        )


def response_data(action: str, user_id: int) -> object:
    if action == "get_group_member_info":
        return {"role": "owner", "nickname": "local-smoke-test", "card": ""}
    if action == "get_group_member_list":
        return [
            {
                "user_id": user_id,
                "nickname": "local-smoke-test",
                "card": "",
                "role": "owner",
            }
        ]
    if action == "get_group_info":
        return {"group_name": "local-smoke-test-group"}
    if action == "get_group_msg_history":
        return []
    if action.startswith("send_"):
        return {"message_id": int(time.time() * 1000) % 2_000_000_000}
    return {}


async def run_command(
    websocket: websockets.ClientConnection,
    self_id: int,
    command: str,
    message_id: int,
    user_id: int,
    group_id: int,
    command_timeout: float,
    idle_timeout: float,
) -> CommandResult:
    started = time.monotonic()
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
                    "nickname": "local-smoke-test",
                    "card": "",
                    "role": "owner",
                },
            }
        )
    )

    actions: list[str] = []
    deadline = started + command_timeout
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
        await websocket.send(
            json.dumps(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": response_data(action, user_id),
                    "echo": data.get("echo"),
                }
            )
        )
        idle_deadline = time.monotonic() + idle_timeout
    return CommandResult(command, tuple(actions), time.monotonic() - started)


async def run(
    commands: tuple[str, ...],
    command_timeout: float,
    idle_timeout: float,
    expect_no_output: bool,
) -> int:
    values = dotenv_values(".env")
    self_id = int(values.get("QQ_ACCOUNT_ID") or 0)
    access_token = values.get("ONEBOT_ACCESS_TOKEN")
    if not self_id or not access_token:
        raise RuntimeError(".env must contain QQ_ACCOUNT_ID and ONEBOT_ACCESS_TOKEN")
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
        raise RuntimeError(".env must contain a managed group and BOT_OPERATOR_IDS for smoke testing")

    headers = {
        "X-Self-ID": str(self_id),
        "Authorization": f"Bearer {access_token}",
    }
    async with websockets.connect(
        "ws://127.0.0.1:8080/onebot/v11/ws",
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
        results = [
            await run_command(
                websocket,
                self_id,
                command,
                990100 + index,
                user_id,
                group_id,
                command_timeout,
                idle_timeout,
            )
            for index, command in enumerate(commands)
        ]

    if expect_no_output:
        failures = [result.command for result in results if result.output_actions]
    else:
        failures = [
            result.command
            for result in results
            if result.command in EXPECTED_OUTPUT and not result.output_actions
        ]
    for result in results:
        outputs = ",".join(result.output_actions) or "none"
        if expect_no_output:
            status = "PASS_NO_OUTPUT" if not result.output_actions else "UNEXPECTED_OUTPUT"
        else:
            status = "PASS" if result.output_actions else "NO_OUTPUT"
        print(
            f"{result.command}: {status}; "
            f"elapsed={result.elapsed:.1f}s; output={outputs}; api={dict(Counter(result.actions))}",
            flush=True,
        )
    if failures:
        print("FAILED_EXPECTED_OUTPUT=" + ",".join(failures))
        return 1
    print(f"PASS: {len(results)} commands checked")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", action="append", dest="commands")
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=25.0,
        help="Maximum seconds to wait for one command, including image downloads.",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=3.0,
        help="Seconds without another OneBot action before considering a command complete.",
    )
    parser.add_argument(
        "--expect-no-output",
        action="store_true",
        help="Pass only when the supplied commands produce no send_* action.",
    )
    args = parser.parse_args()
    commands = tuple(args.commands or DEFAULT_COMMANDS)
    return asyncio.run(
        run(commands, args.command_timeout, args.idle_timeout, args.expect_no_output)
    )


if __name__ == "__main__":
    raise SystemExit(main())
