from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from PIL import Image

import bot.services.asoul as asoul_module
from bot.db import Database
from bot.services.asoul import ASoulService
from bot.services.asoul_render import ASoulImageRenderer
from bot.services.emoji_text import is_emoji_character


ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260727T120000
SUMMARY:嘉然：午间杂谈
DESCRIPTION:直播 | 嘉然
URL:https://live.bilibili.com/22632424
END:VEVENT
BEGIN:VEVENT
DTSTART:20260727T120000
SUMMARY:贝拉：午间杂谈
DESCRIPTION:直播 | 贝拉
URL:https://live.bilibili.com/22632424
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260728
SUMMARY:全天活动
END:VEVENT
BEGIN:VEVENT
DTSTART:20260727T150000
STATUS:CANCELLED
SUMMARY:取消的直播
END:VEVENT
END:VCALENDAR
"""


def test_schedule_parser_merges_same_program_and_ignores_cancelled(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    async def events():
        return service._parse_ics(ICS)

    service._calendar_events = events  # type: ignore[method-assign]
    rows = asyncio.run(service.schedule_for_day(__import__("datetime").date(2026, 7, 27)))

    assert len(rows) == 1
    assert rows[0].hosts == ("嘉然", "贝拉")
    assert rows[0].content == "午间杂谈"


def test_schedule_highlight_persists_by_stable_item_key(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))
    item = asoul_module.ScheduleItem(
        __import__("datetime").datetime(2026, 7, 27, 12, tzinfo=service.timezone),
        ("嘉然",),
        "午间杂谈",
        "杂谈",
    )

    service.set_highlight(item, "粉色")

    restarted = ASoulService(service.db)
    assert restarted.highlight_records()[item.key] == "粉色"
    assert restarted.remove_highlight(item)
    assert restarted.highlight_records() == {}


def test_local_renderer_creates_schedule_and_bilibili_cards(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))
    item = asoul_module.ScheduleItem(
        __import__("datetime").datetime(2026, 7, 27, 12, tzinfo=service.timezone),
        ("嘉然",),
        "午间杂谈",
        "杂谈",
    )
    renderer = ASoulImageRenderer(tmp_path)

    schedule_path = asyncio.run(
        renderer.render_schedule(__import__("datetime").date(2026, 7, 27), "今日直播", [item])
    )
    bilibili_path = asyncio.run(
        renderer.render_bilibili_notification("【B站新动态】测试UP\n测试内容\nhttps://example.test/d")
    )
    dynamic_path = asyncio.run(
        renderer.render_bilibili_notification(
            "【B站最新动态】测试UP\n测试内容\nhttps://example.test/d",
            dynamic={
                "uid": "100",
                "author": "测试UP",
                "profile": "测试签名",
                "text": "这是支持自动换行的动态正文。",
                "emoji_urls": "",
                "quote_author": "原作者",
                "quote_text": "这是一条被引用的动态。",
            },
        )
    )
    live_path = asyncio.run(
        renderer.render_bilibili_notification(
            "【开播】测试UP\n直播标题\nhttps://live.bilibili.com/1",
            live={"phase": "start", "author": "测试UP", "text": "直播标题", "url": "https://live.bilibili.com/1"},
        )
    )
    video_path = asyncio.run(
        renderer.render_bilibili_notification(
            "【B站新视频】测试UP\n视频标题\nhttps://www.bilibili.com/video/BV1",
            video={
                "uid": "100",
                "author": "测试UP",
                "profile": "测试签名",
                "text": "视频标题",
                "description": "这是会自动换行的视频简介，用于介绍视频的主要内容。" * 8,
                "url": "https://www.bilibili.com/video/BV1",
                "likes": "10000",
                "following": "32",
                "followers": "123456",
            },
        )
    )
    plain_video_path = asyncio.run(
        renderer.render_bilibili_notification(
            "【B站新视频】测试UP\n视频标题\nhttps://www.bilibili.com/video/BV1",
            video={
                "uid": "100",
                "author": "测试UP",
                "text": "视频标题",
                "url": "https://www.bilibili.com/video/BV1",
            },
        )
    )
    short_dynamic_path = asyncio.run(
        renderer.render_bilibili_notification(
            "preview",
            dynamic={"author": "测试UP", "text": "短文", "emoji_urls": ""},
        )
    )
    reservation_dynamic_path = asyncio.run(
        renderer.render_bilibili_notification(
            "preview",
            dynamic={
                "author": "测试UP",
                "text": "第一行\n第二行",
                "rich_nodes": json.dumps([
                    {"type": "text", "text": "第一行\n"},
                    {"type": "text", "text": "第二行"},
                ]),
                "reserve_title": "【突击】睡前电台～",
                "reserve_subtitle": "今天 22:00 直播 · 4人预约",
                "reserve_action": "预约",
            },
        )
    )
    long_dynamic_path = asyncio.run(
        renderer.render_bilibili_notification(
            "preview",
            dynamic={"author": "测试UP", "text": "长文" * 600, "emoji_urls": ""},
        )
    )

    assert schedule_path.is_file() and schedule_path.stat().st_size > 1024
    assert bilibili_path.is_file() and bilibili_path.stat().st_size > 1024
    assert dynamic_path.is_file() and dynamic_path.stat().st_size > 1024
    assert live_path.is_file() and live_path.stat().st_size > 1024
    assert video_path.is_file() and video_path.stat().st_size > 1024
    assert plain_video_path.is_file() and plain_video_path.stat().st_size > 1024
    assert short_dynamic_path.is_file() and reservation_dynamic_path.is_file() and long_dynamic_path.is_file()
    with Image.open(dynamic_path) as image:
        assert image.getpixel((540, 10))[:3] != (251, 114, 153)
    with Image.open(live_path) as image:
        assert image.getpixel((540, 10))[:3] != (251, 114, 153)
    with Image.open(short_dynamic_path) as image:
        assert image.height < 600
    with Image.open(reservation_dynamic_path) as image, Image.open(short_dynamic_path) as short_image:
        assert image.height > short_image.height
    with Image.open(long_dynamic_path) as image:
        assert image.height > 1800
    with Image.open(video_path) as image, Image.open(plain_video_path) as plain_image:
        assert image.height > plain_image.height


def test_dynamic_forward_quote_keeps_every_adaptive_line(tmp_path):
    renderer = ASoulImageRenderer(tmp_path)
    common = {"author": "测试UP", "text": "转发说明", "emoji_urls": ""}
    fifteen_lines = "\n".join(f"引用内容第 {index} 行" for index in range(15))
    thirty_lines = "\n".join(f"引用内容第 {index} 行" for index in range(30))

    fifteen_path = asyncio.run(
        renderer.render_bilibili_notification("preview", dynamic={**common, "quote_text": fifteen_lines})
    )
    thirty_path = asyncio.run(
        renderer.render_bilibili_notification("preview", dynamic={**common, "quote_text": thirty_lines})
    )

    with Image.open(fifteen_path) as fifteen_image, Image.open(thirty_path) as thirty_image:
        assert fifteen_image.height > 900
        assert thirty_image.height > fifteen_image.height


def test_dynamic_parser_accepts_null_major_and_opus(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    class FakeUser:
        async def get_dynamics_new(self):
            return {
                "items": [
                    {
                        "id_str": "123",
                        "modules": {
                            "module_author": {"name": "test-up"},
                            "module_dynamic": {"desc": {"text": "hello"}, "major": {"opus": None}},
                        },
                        "basic": {"jump_url": "//t.bilibili.com/123"},
                    },
                    {
                        "id_str": "456",
                        "modules": {
                            "module_author": {"name": "test-up"},
                            "module_dynamic": {"desc": {"text": "world"}, "major": None},
                        },
                        "basic": {"jump_url": "//t.bilibili.com/456"},
                    },
                ]
            }

    service._user = lambda _: FakeUser()  # type: ignore[method-assign]

    rows = asyncio.run(service.fetch_dynamics("100"))

    assert [(row["id"], row["text"], row["url"]) for row in rows] == [
        ("123", "hello", "https://t.bilibili.com/123"),
        ("456", "world", "https://t.bilibili.com/456"),
    ]


def test_dynamic_parser_sorts_by_published_timestamp_before_selecting_latest(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    class FakeUser:
        async def get_dynamics_new(self):
            return {"items": [
                {
                    "id_str": "old",
                    "modules": {
                        "module_author": {"name": "test-up", "pub_ts": 100},
                        "module_dynamic": {"desc": {"text": "old"}},
                    },
                },
                {
                    "id_str": "new",
                    "modules": {
                        "module_author": {"name": "test-up", "pub_ts": 300},
                        "module_dynamic": {"desc": {"text": "new"}},
                    },
                },
                {
                    "id_str": "middle",
                    "pub_ts": 200,
                    "modules": {
                        "module_author": {"name": "test-up"},
                        "module_dynamic": {"desc": {"text": "middle"}},
                    },
                },
            ]}

    service._user = lambda _: FakeUser()  # type: ignore[method-assign]

    rows = asyncio.run(service.fetch_dynamics("100"))

    assert [row["id"] for row in rows] == ["new", "middle", "old"]
    assert asyncio.run(service.fetch_dynamic("100"))["id"] == "new"


def test_video_description_keeps_source_linebreaks_in_the_card_renderer(tmp_path):
    renderer = ASoulImageRenderer(tmp_path)

    lines = renderer._wrap_preserving_linebreaks("first line\nsecond line", renderer._font(22), 844, 12)

    assert lines == ["first line", "second line"]


def test_dynamic_parser_keeps_every_source_image_url():
    urls = [f"https://example.test/{index}.png" for index in range(7)]

    result = ASoulService._dynamic_image_urls({"major": {"opus": {"pics": [{"url": url} for url in urls]}}})

    assert result == urls


def test_card_renderer_uses_twemoji_asset_for_title_emoji(tmp_path):
    assert is_emoji_character(chr(0x1F3B7))
    assert is_emoji_character(chr(0x2764))
    assert not is_emoji_character("A")


def test_dynamic_parser_keeps_profile_emoji_and_quoted_dynamic(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    class FakeUser:
        async def get_dynamics_new(self):
            return {
                "items": [{
                    "id_str": "123",
                    "modules": {
                        "module_author": {"mid": 100, "name": "测试UP", "face": "https://example.test/avatar.png"},
                        "module_dynamic": {
                            "major": {"opus": {"jump_url": "//t.bilibili.com/123", "summary": {
                                "text": "正文[表情]", "rich_text_nodes": [{"text": "[表情]", "emoji": {"text": "[表情]", "icon_url": "https://example.test/emoji.png"}}],
                            }}},
                        },
                    },
                    "orig": {
                        "modules": {
                            "module_author": {"name": "原作者"},
                            "module_dynamic": {"desc": {"text": "被引用的动态"}},
                        },
                    },
                }]
            }

        async def get_user_info(self):
            return {"mid": 100, "name": "测试UP", "face": "https://example.test/avatar.png", "sign": "测试签名"}

    service._user = lambda _: FakeUser()  # type: ignore[method-assign]

    assert asyncio.run(service.fetch_dynamic("100")) == {
        "id": "123",
        "uid": "100",
        "author": "测试UP",
        "avatar_url": "https://example.test/avatar.png",
        "profile": "测试签名",
        "text": "正文[表情]",
        "emoji_labels": "[表情]",
        "emoji_urls": "https://example.test/emoji.png",
        "quote_author": "原作者",
        "quote_text": "被引用的动态",
        "url": "https://t.bilibili.com/123",
        "notification_kind": "forward",
        "cover_url": "",
    }


def test_dynamic_parser_preserves_rich_nodes_linebreaks_and_reservation(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    class FakeUser:
        async def get_dynamics_new(self):
            return {"items": [{
                "id_str": "reserve-1",
                "modules": {
                    "module_author": {"mid": 100, "name": "测试UP"},
                    "module_dynamic": {
                        "major": {"opus": {"jump_url": "//t.bilibili.com/reserve-1", "summary": {
                            "text": "第一行\n[表情]@测试链接第二行",
                            "rich_text_nodes": [
                                {"text": "第一行\n"},
                                {"text": "[表情]", "emoji": {"text": "[表情]", "icon_url": "https://example.test/emoji.png"}},
                                {"text": "@测试", "type": "RICH_TEXT_NODE_TYPE_AT", "rid": "200"},
                                {"text": "链接", "jump_url": "//example.test/link"},
                                {"text": "第二行"},
                            ],
                        }}},
                        "additional": {"reserve": {
                            "title": "【突击】睡前电台～",
                            "desc1": {"text": "今天 22:00 直播"},
                            "desc2": {"text": "4人预约"},
                            "button": {"uncheck": {"text": "预约"}},
                        }},
                    },
                },
            }]}

        async def get_user_info(self):
            return {"mid": 100, "name": "测试UP"}

    service._user = lambda _: FakeUser()  # type: ignore[method-assign]

    result = asyncio.run(service.fetch_dynamic("100"))

    assert result is not None
    assert result["text"] == "第一行\n[表情]@测试链接第二行"
    assert json.loads(result["rich_nodes"]) == [
        {"type": "text", "text": "第一行\n"},
        {"type": "emoji", "text": "[表情]", "url": "https://example.test/emoji.png"},
        {"type": "link", "text": "@测试", "url": "https://space.bilibili.com/200"},
        {"type": "link", "text": "链接", "url": "https://example.test/link"},
        {"type": "text", "text": "第二行"},
    ]
    assert result["reserve_title"] == "【突击】睡前电台～"
    assert result["reserve_subtitle"] == "今天 22:00 直播 · 4人预约"
    assert result["reserve_action"] == "预约"


def test_dynamic_parser_and_renderer_keep_images_and_forwarded_rich_nodes(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    class FakeUser:
        async def get_dynamics_new(self):
            return {"items": [{
                "id_str": "images-1",
                "modules": {
                    "module_author": {"mid": 100, "name": "测试UP"},
                    "module_dynamic": {
                        "major": {"opus": {
                            "jump_url": "//t.bilibili.com/images-1",
                            "summary": {"text": "正文", "rich_text_nodes": [{"text": "正文"}]},
                            "pics": [
                                {"url": "//example.test/one.png"},
                                {"orig_url": "https://example.test/two.png"},
                            ],
                        }},
                    },
                },
                "orig": {
                    "modules": {
                        "module_author": {"name": "原作者"},
                        "module_dynamic": {"major": {"opus": {
                            "summary": {"text": "转发[表情]", "rich_text_nodes": [
                                {"text": "转发"},
                                {"text": "[表情]", "emoji": {"text": "[表情]", "icon_url": "https://example.test/emoji.png"}},
                            ]},
                            "pics": [{"url": "https://example.test/forward.png"}],
                        }}},
                    },
                },
            }]}

        async def get_user_info(self):
            return {"mid": 100, "name": "测试UP"}

    service._user = lambda _: FakeUser()  # type: ignore[method-assign]
    dynamic = asyncio.run(service.fetch_dynamic("100"))

    assert dynamic is not None
    assert json.loads(dynamic["image_urls"]) == ["https://example.test/one.png", "https://example.test/two.png"]
    assert json.loads(dynamic["quote_image_urls"]) == ["https://example.test/forward.png"]
    assert json.loads(dynamic["quote_rich_nodes"])[1]["type"] == "emoji"
    renderer = ASoulImageRenderer(tmp_path)
    renderer._remote_image = lambda _: None  # type: ignore[method-assign]
    path = asyncio.run(renderer.render_bilibili_notification("preview", dynamic=dynamic))
    assert path.is_file() and path.stat().st_size > 1024


def test_live_parser_reads_a_single_status_from_the_batch_response(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    async def statuses(uids):
        assert uids == ("100",)
        return {"100": {
            "id": "22637261",
            "uid": "100",
            "author": "Jiaran",
            "text": "live title",
            "url": "https://live.bilibili.com/22637261",
            "live": "1",
            "avatar_url": "",
            "cover_url": "",
            "online": "500",
            "live_time": "1720000000",
            "area": "虚拟主播",
        }}

    service.fetch_live_statuses = statuses  # type: ignore[method-assign]

    assert asyncio.run(service.fetch_live("100")) == {
        "id": "22637261",
        "uid": "100",
        "author": "Jiaran",
        "text": "live title",
        "url": "https://live.bilibili.com/22637261",
        "live": "1",
        "avatar_url": "",
        "cover_url": "",
        "online": "500",
        "live_time": "1720000000",
        "area": "虚拟主播",
    }


def test_video_parser_and_notification_enrichment_include_card_fields(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    class FakeUser:
        async def get_videos(self, ps=10):
            assert ps == 10
            return {"list": {"vlist": [{
                "bvid": "BV1test",
                "mid": 100,
                "author": "测试UP",
                "title": "视频标题",
                "description": "  这是  视频简介\n会被规整。  ",
                "pic": "//example.test/cover.jpg",
            }]}}

        async def get_user_info(self):
            return {
                "mid": 100,
                "face": "https://example.test/avatar.jpg",
                "sign": "测试签名",
            }

        async def get_relation_info(self):
            return {"following": 32, "follower": 123456}

        async def get_up_stat(self):
            return {"likes": 10000, "archive": {"view": 1}}

    service._user = lambda _: FakeUser()  # type: ignore[method-assign]

    video = asyncio.run(service.fetch_video("100"))
    assert video == {
        "id": "BV1test",
        "uid": "100",
        "author": "测试UP",
        "text": "视频标题",
        "description": "这是 视频简介\n会被规整。",
        "url": "https://www.bilibili.com/video/BV1test",
        "cover_url": "https://example.test/cover.jpg",
    }
    assert asyncio.run(service.enrich_video_notification(video)) == {
        **video,
        "avatar_url": "https://example.test/avatar.jpg",
        "profile": "测试签名",
        "likes": "10000",
        "following": "32",
        "followers": "123456",
        "views": "1",
    }
    assert asyncio.run(service.enrich_dynamic_notification({"uid": "100", "author": "测试UP", "text": "动态正文"})) == {
        "uid": "100",
        "author": "测试UP",
        "text": "动态正文",
        "avatar_url": "https://example.test/avatar.jpg",
        "profile": "测试签名",
        "likes": "10000",
        "following": "32",
        "followers": "123456",
        "views": "1",
    }


def test_profile_stat_failure_does_not_replace_missing_data_with_zero(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    class FakeUser:
        async def get_user_info(self):
            return {"mid": 100, "name": "测试UP"}

        async def get_relation_info(self):
            raise RuntimeError("relation endpoint unavailable")

        async def get_up_stat(self):
            return {"likes": 10000}

    service._user = lambda _: FakeUser()  # type: ignore[method-assign]

    details = asyncio.run(service.enrich_dynamic_notification({"uid": "100", "text": "动态正文"}))

    assert details["likes"] == "10000"
    assert "following" not in details
    assert "followers" not in details



def test_archive_dynamic_becomes_a_video_card_item(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    class FakeUser:
        async def get_dynamics_new(self):
            return {"items": [{
                "id_str": "dynamic-video-1",
                "modules": {
                    "module_author": {"mid": 100, "name": "test-up", "face": "https://example.test/avatar.jpg"},
                    "module_dynamic": {"major": {"archive": {
                        "bvid": "BV1test",
                        "title": "video title",
                        "desc": "video description\nsecond paragraph",
                        "cover": "//example.test/cover.jpg",
                        "jump_url": "//www.bilibili.com/video/BV1test",
                    }}},
                },
            }]}

        async def get_user_info(self):
            return {"mid": 100, "name": "test-up", "sign": "test profile"}

    service._user = lambda _: FakeUser()  # type: ignore[method-assign]

    assert asyncio.run(service.fetch_dynamic("100")) == {
        "id": "dynamic-video-1",
        "uid": "100",
        "author": "test-up",
        "avatar_url": "https://example.test/avatar.jpg",
        "profile": "test profile",
        "text": "video title",
        "description": "video description\nsecond paragraph",
        "emoji_labels": "",
        "emoji_urls": "",
        "quote_author": "",
        "quote_text": "",
        "url": "https://www.bilibili.com/video/BV1test",
        "notification_kind": "video",
        "cover_url": "https://example.test/cover.jpg",
    }


def test_dynamic_notification_kind_separates_live_reservation_forward_and_video(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))

    assert service._dynamic_notification_kind(
        {}, {}, {"live": None, "live_rcmd": None}, {}
    ) == "dynamic"
    assert service._dynamic_notification_kind(
        {}, {}, {"live": {}, "live_rcmd": []}, {}
    ) == "dynamic"
    assert service._dynamic_notification_kind(
        {}, {}, {"live_rcmd": {"content": "live"}}, {}
    ) == "live"
    assert service._dynamic_notification_kind(
        {}, {"additional": {"reserve": {"title": "预约"}}}, {}, {}
    ) == "reservation"
    assert service._dynamic_notification_kind(
        {"orig": {}}, {}, {}, {}
    ) == "forward"
    assert service._dynamic_notification_kind(
        {}, {}, {}, {"bvid": "BV1test"}
    ) == "video"
    assert service._dynamic_notification_kind(
        {"type": "DYNAMIC_TYPE_LIVE_RCMD"}, {}, {}, {}
    ) == "live"


def test_monitor_uses_dynamic_feed_for_video_submissions_without_video_polling(tmp_path, monkeypatch):
    monkeypatch.setattr(
        asoul_module,
        "settings",
        SimpleNamespace(
            timezone="Asia/Shanghai",
            asoul_bili_target_uids=("100",),
            asoul_bili_comment_target_uids=(),
            asoul_bili_push_dynamic=True,
            asoul_bili_push_video=True,
            asoul_bili_push_live=True,
            asoul_bili_push_comment=False,
        ),
    )
    service = ASoulService(Database(tmp_path / "bot.db"))
    current = {"dynamics": ["d1"], "live": "0"}

    async def dynamics(_: str):
        return [
            {
                "id": value,
                "uid": "100",
                "author": "test-up",
                "text": "video title" if value == "d3" else "dynamic text",
                "url": f"https://example.test/{value}",
                "notification_kind": (
                    "video" if value == "d3" else "live" if value == "live-card" else "dynamic"
                ),
                "cover_url": "https://example.test/cover.jpg" if value == "d3" else "",
            }
            for value in current["dynamics"]
        ]

    async def videos(_: str):
        raise AssertionError("automatic monitoring must not poll the video list")

    live_batch_calls = []

    async def live_statuses(uids):
        live_batch_calls.append(uids)
        return {"100": {
            "id": "1",
            "uid": "100",
            "author": "测试UP",
            "text": "直播",
            "url": "https://example.test/l",
            "live": current["live"],
            "online": "50" if current["live"] == "1" else "0",
        }}

    async def profile_stats(_: str):
        return {"likes": "10000", "following": "32", "followers": "123456"}

    async def video_details(item):
        return {**item, "uid": "100", "cover_url": "https://example.test/cover.jpg", "likes": "10000"}

    async def live_details(item):
        return {**item, **await profile_stats(item["uid"])}

    service.fetch_dynamics = dynamics  # type: ignore[method-assign]
    service.fetch_videos = videos  # type: ignore[method-assign]
    service.fetch_live_statuses = live_statuses  # type: ignore[method-assign]
    service.fetch_profile_stats = profile_stats  # type: ignore[method-assign]
    service.enrich_live_notification = live_details  # type: ignore[method-assign]
    service.enrich_video_notification = video_details  # type: ignore[method-assign]

    assert asyncio.run(service.poll_updates()) == []
    current.update(dynamics=["d3", "live-card", "d2", "d1"], live="1")
    updates = asyncio.run(service.poll_updates())

    assert len(updates) == 3
    assert all("live-card" not in message for message in updates)
    dynamic = next(message for message in updates if "新动态" in message)
    assert dynamic == "【B站新动态】test-up\ndynamic text\nhttps://example.test/d2"
    video_message = next(message for message in updates if message.startswith("【B站新视频】"))
    assert service.video_notification_details(video_message) == {
        "id": "d3",
        "author": "test-up",
        "text": "video title",
        "url": "https://example.test/d3",
        "notification_kind": "video",
        "uid": "100",
        "cover_url": "https://example.test/cover.jpg",
        "likes": "10000",
    }
    opening = next(message for message in updates if message.startswith("【开播】"))
    assert opening == "【开播】测试UP\n直播\nhttps://example.test/l"
    details = service.live_notification_details(opening)
    assert details is not None
    assert details["phase"] == "start"
    assert details["likes"] == "10000"
    assert details["following"] == "32"
    assert details["followers"] == "123456"
    assert updates.index(dynamic) < updates.index(video_message)
    assert live_batch_calls == [("100",), ("100",)]

    current["live"] = "0"
    ended = asyncio.run(service.poll_updates())

    assert len(ended) == 1
    assert ended[0] == "【已下播】测试UP"
    ended_details = service.live_notification_details(ended[0])
    assert ended_details is not None
    assert ended_details["phase"] == "end"
    assert ended_details["popularity_peak"] == "50"
    assert ended_details["popularity_average"] == "50"
    assert ended_details["likes_delta"] == "+0"
    assert live_batch_calls == [("100",), ("100",), ("100",)]
    assert asyncio.run(service.poll_updates()) == []


def test_polling_pushes_new_target_reply_from_latest_six_hour_dynamic_once(tmp_path, monkeypatch):
    monkeypatch.setattr(
        asoul_module,
        "settings",
        SimpleNamespace(
            timezone="Asia/Shanghai",
            asoul_bili_target_uids=("100",),
            asoul_bili_comment_target_uids=("100",),
            asoul_bili_push_dynamic=False,
            asoul_bili_push_video=False,
            asoul_bili_push_live=False,
            asoul_bili_push_comment=True,
        ),
    )
    service = ASoulService(Database(tmp_path / "bot.db"))
    now = int(__import__("time").time())
    replies = [{"id": "r1", "author_uid": "100", "author": "测试UP", "text": "第一条回复"}]

    async def dynamics(_: str):
        return [{
            "id": "d1",
            "uid": "100",
            "author": "测试UP",
            "text": "最近动态",
            "url": "https://t.bilibili.com/d1",
            "notification_kind": "dynamic",
            "published_at": str(now - 60),
            "comment_oid": "9001",
            "comment_type": "17",
        }]

    async def videos(_: str):
        raise AssertionError("automatic monitoring must not poll the video list")

    async def live_statuses(_: tuple[str, ...]):
        return {}

    async def latest_comments(resource):
        assert resource["id"] == "d1"
        return list(replies)

    service.fetch_dynamics = dynamics  # type: ignore[method-assign]
    service.fetch_videos = videos  # type: ignore[method-assign]
    service.fetch_live_statuses = live_statuses  # type: ignore[method-assign]
    service.fetch_latest_comments = latest_comments  # type: ignore[method-assign]

    assert asyncio.run(service.poll_updates()) == []
    replies.append({"id": "r2", "author_uid": "100", "author": "测试UP", "text": "新增回复"})
    updates = asyncio.run(service.poll_updates())

    assert updates == [
        "【B站评论区回复】测试UP\n在测试UP的动态底下的回复\n新增回复\nhttps://t.bilibili.com/d1"
    ]
    assert asyncio.run(service.poll_updates()) == []


def test_latest_comment_resource_requires_latest_eligible_item_within_six_hours(tmp_path):
    service = ASoulService(Database(tmp_path / "bot.db"))
    now = 1_800_000_000
    old = {
        "id": "old",
        "notification_kind": "dynamic",
        "published_at": str(now - 6 * 60 * 60),
        "comment_oid": "1",
        "comment_type": "17",
    }
    live_card = {
        "id": "live",
        "notification_kind": "live",
        "published_at": str(now - 30),
        "comment_oid": "2",
        "comment_type": "17",
    }
    video = {
        "id": "video",
        "notification_kind": "video",
        "published_at": str(now - 60),
        "comment_oid": "3",
        "comment_type": "1",
    }

    assert service.latest_comment_resource([live_card, video, old], now) == video
    assert service.latest_comment_resource([old], now) is None


def test_comment_page_filters_authors_by_the_five_account_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(
        asoul_module,
        "settings",
        SimpleNamespace(
            timezone="Asia/Shanghai",
            asoul_bili_comment_target_uids=("100", "200"),
        ),
    )
    service = ASoulService(Database(tmp_path / "bot.db"))

    async def comments(oid, resource_type, **kwargs):
        assert oid == 9001
        assert resource_type == asoul_module.comment.CommentResourceType.DYNAMIC
        assert kwargs["order"] == asoul_module.comment.OrderType.TIME
        return {
            "replies": [
                {
                    "rpid": 1,
                    "member": {"mid": "999", "uname": "普通用户"},
                    "content": {"message": "普通评论"},
                    "replies": [
                        {
                            "rpid": 2,
                            "member": {"mid": "100", "uname": "嘉然"},
                            "content": {"message": "跨账号楼中楼回复"},
                        }
                    ],
                },
                {
                    "rpid": 3,
                    "member": {"mid": "200", "uname": "乃琳"},
                    "content": {"message": "一级回复"},
                },
            ]
        }

    monkeypatch.setattr(asoul_module.comment, "get_comments", comments)

    rows = asyncio.run(
        service.fetch_latest_comments({"comment_oid": "9001", "comment_type": "17"})
    )

    assert [(row["id"], row["author_uid"], row["text"]) for row in rows] == [
        ("2", "100", "跨账号楼中楼回复"),
        ("3", "200", "一级回复"),
    ]


def test_comment_polling_rotates_two_targets_per_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(
        asoul_module,
        "settings",
        SimpleNamespace(
            timezone="Asia/Shanghai",
            asoul_bili_target_uids=("1", "2", "3", "4", "5"),
            asoul_bili_comment_target_uids=("1", "2", "3", "4", "5"),
            asoul_bili_push_dynamic=False,
            asoul_bili_push_video=False,
            asoul_bili_push_live=False,
            asoul_bili_push_comment=True,
        ),
    )
    service = ASoulService(Database(tmp_path / "bot.db"))
    calls: list[str] = []
    now = int(__import__("time").time())

    async def dynamics(uid):
        calls.append(uid)
        return [{
            "id": f"d-{uid}",
            "uid": uid,
            "author": uid,
            "text": "最近动态",
            "url": f"https://t.bilibili.com/d-{uid}",
            "notification_kind": "dynamic",
            "published_at": str(now - 60),
            "comment_oid": uid,
            "comment_type": "17",
        }]

    async def live_statuses(_):
        return {}

    async def latest_comments(_):
        return []

    service.fetch_dynamics = dynamics  # type: ignore[method-assign]
    service.fetch_live_statuses = live_statuses  # type: ignore[method-assign]
    service.fetch_latest_comments = latest_comments  # type: ignore[method-assign]

    cycles: list[set[str]] = []
    for _ in range(3):
        before = len(calls)
        asyncio.run(service.poll_updates())
        cycles.append(set(calls[before:]))

    assert cycles == [{"1", "2"}, {"3", "4"}, {"1", "5"}]
    state = service.db.asoul_state(asoul_module.MONITOR_KEY, {})
    assert state["comment_scan_index"] == 1
