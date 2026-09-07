import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import nonebot


def test_whitelist_feedback_sends_one_native_image(monkeypatch, tmp_path: Path):
    nonebot.init()
    commands = importlib.import_module("bot.plugins.commands")
    image = tmp_path / "whitelist.png"
    image.write_bytes(b"not inspected by the OneBot payload builder")
    class Matcher:
        finished = False
        sent = []

        async def finish(self):
            self.finished = True

        async def send(self, value):
            self.sent.append(value)

    monkeypatch.setattr(commands, "settings", SimpleNamespace(report_output_mode="local_image"))
    bot = SimpleNamespace(self_id=920000004)
    matcher = Matcher()

    asyncio.run(
        commands.finish_whitelist_forward(
            bot,
            matcher,
            SimpleNamespace(user_id=900000001),
            "查重白名单",
            "900000001（Local Operator）",
            lambda: image,
        )
    )

    assert matcher.finished
    assert len(matcher.sent) == 1
    assert matcher.sent[0].type == "image"


def test_filter_commands_accept_multiple_qq_ids_with_common_separators():
    nonebot.init()
    commands = importlib.import_module("bot.plugins.commands")

    assert commands.filter_user_ids("1,2，3 4") == (1, 2, 3, 4)
    assert commands.filter_user_ids("1,foo") is None
