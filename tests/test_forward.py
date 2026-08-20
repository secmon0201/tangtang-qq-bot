import json
import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import nonebot
from nonebot.utils import DataclassEncoder
from PIL import Image

from bot.services.forward import build_forward_nodes


def test_forward_nodes_put_each_image_in_its_own_page(tmp_path: Path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (8, 8), "white").save(first)
    Image.new("RGB", (8, 8), "black").save(second)

    nodes = build_forward_nodes(["第一页", "第二页"], [first, second], 2120682836)

    assert [node["type"] for node in nodes] == ["node", "node"]
    assert [node["data"]["name"] for node in nodes] == ["查重结果 第1页", "查重结果 第2页"]
    assert all(node["data"]["uin"] == "2120682836" for node in nodes)
    assert all(node["data"]["content"][0]["type"] == "image" for node in nodes)
    json.dumps(nodes, cls=DataclassEncoder)


def test_forward_nodes_use_text_when_a_page_has_no_image():
    nodes = build_forward_nodes(["文字页"], [None], "2120682836")

    assert nodes[0]["data"]["content"][0]["type"] == "text"
    assert nodes[0]["data"]["content"][0]["data"]["text"] == "文字页"


def test_duplicate_pages_are_sent_as_one_multi_image_message(tmp_path: Path):
    nonebot.init()
    commands = importlib.import_module("bot.plugins.commands")
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (8, 8), "white").save(first)
    Image.new("RGB", (8, 8), "black").save(second)
    class Matcher:
        finished = False
        sent = []

        async def finish(self):
            self.finished = True

        async def send(self, message):
            self.sent.append(message)

    matcher = Matcher()
    asyncio.run(
        commands.send_duplicate_pages(
            SimpleNamespace(self_id=3987707335),
            SimpleNamespace(user_id=595861835),
            matcher,
            ["第一页", "第二页"],
            [first, second],
        )
    )

    assert matcher.finished
    assert len(matcher.sent) == 1
    assert len(matcher.sent[0]) == 2
    assert all(segment.type == "image" for segment in matcher.sent[0])


def test_duplicate_pages_split_large_image_sets_into_message_batches(tmp_path: Path):
    nonebot.init()
    commands = importlib.import_module("bot.plugins.commands")
    paths = []
    for index in range(commands.DUPLICATE_IMAGE_MESSAGE_BATCH_SIZE + 1):
        path = tmp_path / f"{index}.png"
        Image.new("RGB", (8, 8), "white").save(path)
        paths.append(path)

    class Matcher:
        finished = False
        sent = []

        async def finish(self):
            self.finished = True

        async def send(self, message):
            self.sent.append(message)

    matcher = Matcher()
    asyncio.run(
        commands.send_duplicate_pages(
            SimpleNamespace(self_id=3987707335),
            SimpleNamespace(user_id=595861835),
            matcher,
            [f"第 {index} 页" for index in range(len(paths))],
            paths,
        )
    )

    assert matcher.finished
    assert [len(message) for message in matcher.sent] == [
        commands.DUPLICATE_IMAGE_MESSAGE_BATCH_SIZE,
        1,
    ]
