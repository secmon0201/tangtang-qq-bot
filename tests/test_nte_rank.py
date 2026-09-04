from __future__ import annotations

import asyncio
from io import BytesIO
import json
import sqlite3
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw

from bot.config import ROOT
from bot.services.nte_help_render import NTEHelpRenderer
from bot.services.nte_rank_data import (
    NTERankDataService,
    RankRequest,
    parse_rank_command,
    resolve_scope,
    is_new_nte_help_command,
    is_nte_help_command,
    is_original_nte_help_command,
)
from bot.services.nte_rank_render import NTERankRenderer


def _create_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE ntechardata (
            uid TEXT, char_id TEXT, detail TEXT, score INTEGER, grade TEXT, updated_at TEXT
        );
        CREATE TABLE ntegroupmember (
            group_id TEXT, bot_id TEXT, uid TEXT, user_id TEXT, role_name TEXT, updated_at TEXT
        );
        CREATE TABLE coregroup (
            id INTEGER, group_id TEXT, group_name TEXT
        );
        CREATE TABLE nteuser (
            uid TEXT, bot_id TEXT, user_id TEXT, role_name TEXT, updated_at TEXT
        );
        """
    )
    connection.executemany(
        "INSERT INTO coregroup VALUES (?, ?, ?)",
        [(1, "1128870029", "A海岸一群"), (2, "1077416717", "A海岸二群"), (3, "9001", "外部群")],
    )
    return connection


def _detail(name: str = "早雾", awaken: int = 2, suit: str = "昼夜") -> str:
    return json.dumps(
        {
            "name": name,
            "elementType": "CHARACTER_ELEMENT_TYPE_NATURE",
            "awakenLev": awaken,
            "suit": {"name": suit, "suitActivateNum": 4},
        },
        ensure_ascii=False,
    )


def test_rank_parser_and_default_scope_split():
    for prefix in ("#nte", "nte", "#NTE", "NTE"):
        assert parse_rank_command(f"{prefix}早雾评分排名 页2") == RankRequest("早雾", False, None, 2)
    assert parse_rank_command("#ntebot早雾排名") == RankRequest("早雾", False, "bot", 1)
    assert parse_rank_command("nte群最强排行") == RankRequest(None, True, "group", 1)
    assert parse_rank_command("nte早雾总排行") == RankRequest("早雾", False, "bot", 1)
    assert parse_rank_command("nte最强总排行") == RankRequest(None, True, "bot", 1)
    for prefix in ("#nte", "nte", "#NTE", "NTE"):
        assert is_nte_help_command(f"{prefix}帮助")
        assert is_new_nte_help_command(f"{prefix}帮助")
        assert is_nte_help_command(f"{prefix}原版帮助")
        assert is_original_nte_help_command(f"{prefix}原版帮助")
    assert resolve_scope(1128870029, None) == "group"
    assert resolve_scope(9001, None) == "group"
    assert resolve_scope(9001, "群") == "group"
    assert resolve_scope(1128870029, "bot") == "bot"


def test_recent_group_and_stable_sorting(tmp_path: Path):
    db = tmp_path / "GsData.db"
    connection = _create_db(db)
    rows = [
        ("u1", "c1", _detail(), 100, "A", "2026-01-01 00:00:00"),
        ("u2", "c1", _detail(), 100, "A", "2026-01-01 00:00:00"),
        ("u3", "c1", _detail(), 100, "S", "2026-01-01 00:00:00"),
        ("u4", "c1", _detail(), 100, "A", "2026-01-01 00:00:00"),
    ]
    connection.executemany("INSERT INTO ntechardata VALUES (?, ?, ?, ?, ?, ?)", rows)
    connection.executemany(
        "INSERT INTO ntegroupmember VALUES (?, 'onebot', ?, ?, ?, ?)",
        [
            ("1128870029", "u1", "101", "one", "2026-01-01 00:00:00"),
            ("1077416717", "u2", "102", "two", "2026-01-01 00:00:00"),
            ("1128870029", "u2", "102", "two-coast", "2026-01-01 00:00:00"),
            ("1128870029", "u3", "103", "three", "2026-01-01 00:00:00"),
            ("1128870029", "u4", "104", "four-old", "2025-01-01 00:00:00"),
            ("9001", "u4", "104", "four-new", "2026-01-02 00:00:00"),
        ],
    )
    connection.commit()
    connection.close()

    service = NTERankDataService(db)
    result = service.build_role_rank(RankRequest("c1", False, None), 1128870029)
    assert result.scope == "group"
    assert [row.uid for row in result.rows] == ["u3", "u1", "u2", "u4"]
    assert result.rows[2].group_id == 1128870029
    assert result.rows[2].group_name == "A海岸一群"
    assert result.rows[2].element_type == "CHARACTER_ELEMENT_TYPE_NATURE"
    assert result.rows[2].nickname == "two-coast"

    bot_result = service.build_role_rank(RankRequest("c1", False, "bot"), 9001)
    external_row = next(row for row in bot_result.rows if row.uid == "u4")
    assert external_row.group_id == 9001
    assert external_row.group_name == "外部群"


def test_rank_uses_bot_group_name_when_core_has_placeholder(tmp_path: Path):
    db = tmp_path / "GsData.db"
    connection = _create_db(db)
    connection.execute("UPDATE coregroup SET group_name='1' WHERE group_id='1128870029'")
    connection.execute("INSERT INTO ntechardata VALUES ('u1', 'c1', ?, 100, 'A', '2026-01-01 00:00:00')", (_detail(),))
    connection.execute(
        "INSERT INTO ntegroupmember VALUES ('1128870029', 'onebot', 'u1', '101', 'one', '2026-01-01 00:00:00')"
    )
    connection.commit()
    connection.close()

    bot_db = tmp_path / "bot.db"
    with sqlite3.connect(bot_db) as metadata:
        metadata.execute("CREATE TABLE managed_groups (group_id INTEGER PRIMARY KEY, group_name TEXT, alias TEXT, enabled INTEGER)")
        metadata.execute("INSERT INTO managed_groups VALUES (1128870029, 'A海岸测试群', '修会', 1)")

    result = NTERankDataService(db, group_metadata_path=bot_db).build_role_rank(
        RankRequest("c1", False, "group"), 1128870029
    )
    assert result.rows[0].group_name == "修会"


def test_page_size_and_personal_overflow(tmp_path: Path):
    db = tmp_path / "GsData.db"
    connection = _create_db(db)
    scores = [
        (f"u{index:03d}", "c1", _detail(), 200 - index, "A", f"2026-01-01 00:{index:02d}:00")
        for index in range(105)
    ]
    connection.executemany("INSERT INTO ntechardata VALUES (?, ?, ?, ?, ?, ?)", scores)
    connection.executemany(
        "INSERT INTO ntegroupmember VALUES ('1128870029', 'onebot', ?, ?, ?, ?)",
        [(row[0], "999" if row[0] == "u104" else row[0], row[0], row[5]) for row in scores],
    )
    connection.commit()
    connection.close()

    service = NTERankDataService(db)
    request = RankRequest("c1", False, "group", 1)
    first = service.build_role_rank(request, 1128870029, viewer_user_id=999)
    assert len(first.rows) == 100
    assert first.total == 105
    assert first.self_overflow is not None
    assert first.self_overflow.uid == "u104"
    assert first.self_overflow.rank == 105

    second = service.build_role_rank(RankRequest("c1", False, "group", 2), 1128870029, viewer_user_id=999)
    assert [row.uid for row in second.rows] == [f"u{index:03d}" for index in range(100, 105)]
    assert second.self_overflow is None
    assert second.rows[-1].is_self


def test_help_data_has_custom_ranking_category_and_original_help_entry():
    data = NTEHelpRenderer.load_data()
    assert tuple(data) == ("登录绑定", "信息查询", "定制排行", "配队攻略", "签到服务", "抽卡记录", "其他")
    info_text = json.dumps(data["信息查询"], ensure_ascii=False).lower()
    assert "bot" not in info_text
    assert "页2" not in json.dumps(data, ensure_ascii=False)
    all_help_text = json.dumps(data, ensure_ascii=False)
    for removed in ("全部登出", "获取laohutoken", "获取accessToken", "幻塔签到日历", "抽卡记录TapTap", "抽卡记录小黑盒"):
        assert removed not in all_help_text
    assert [(entry["name"], entry["eg"]) for entry in data["定制排行"]["data"]] == [
        ("角色排行", "薄荷排行"),
        ("角色总排行", "薄荷总排行"),
        ("最强排行", "最强排行"),
        ("最强总排行", "最强总排行"),
    ]
    assert {entry["sticker_group"] for entry in data["定制排行"]["data"]} == {"贝拉"}
    sticker_groups = {
        entry["name"]: entry.get("sticker_group")
        for section in data.values()
        for entry in section["data"]
    }
    assert {name: sticker_groups[name] for name in ("探索", "体力")} == {"探索": "贝拉", "体力": "贝拉"}
    assert {
        name: sticker_groups[name]
        for name in ("兑换码", "帮助", "原版帮助")
    } == {"兑换码": "思诺", "帮助": "思诺", "原版帮助": "思诺"}
    other_names = {entry["name"] for entry in data["其他"]["data"]}
    assert {"帮助", "原版帮助"} <= other_names


def test_custom_ranking_section_uses_tangtang_pink(tmp_path: Path):
    renderer = NTEHelpRenderer(output_dir=tmp_path)
    image_path = renderer.render(force=True)
    data = renderer.load_data()
    top = renderer.BANNER_HEIGHT
    for category, section in data.items():
        if category == "定制排行":
            with Image.open(image_path) as image:
                assert image.getpixel((renderer.MARGIN + 100, top + 48))[:3] == ImageColor.getrgb(renderer.TANGTANG_PINK)
            return
        count = (len(section["data"]) + 3) // 4
        top += renderer.SECTION_HEIGHT + count * renderer.CARD_HEIGHT + max(0, count - 1) * 26 + renderer.SECTION_GAP
    raise AssertionError("定制排行分类未找到")


def test_help_image_has_prefix_compatibility_footer(tmp_path: Path):
    renderer = NTEHelpRenderer(output_dir=tmp_path)
    image_path = renderer.render(force=True)
    assert renderer.COMPATIBILITY_NOTE == "兼容识别：#NTE、NTE、#nte、nte 均可识别"
    with Image.open(image_path) as image:
        assert image.height >= renderer.FOOTER_HEIGHT
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((34, 34))[3] == 255


def test_help_background_scales_once_across_width_before_vertical_tiling(monkeypatch):
    renderer = NTEHelpRenderer()
    renderer.WIDTH = 8
    texture = Image.new("RGBA", (4, 2), "#ff0000")
    ImageDraw.Draw(texture).rectangle((2, 0, 3, 1), fill="#0000ff")
    monkeypatch.setattr(renderer, "_open_image", lambda _path: texture.copy())

    background = renderer._background(9)

    assert background.size == (8, 9)
    for y in (0, 3, 4, 7, 8):
        left = background.getpixel((0, y))
        right = background.getpixel((7, y))
        assert left[0] > left[2]
        assert right[2] > right[0]


def test_rank_renderer_grows_with_rows(tmp_path: Path):
    db = tmp_path / "GsData.db"
    connection = _create_db(db)
    row = ("u1", "c1", _detail(), 100, "S", "2026-01-01 00:00:00")
    connection.execute("INSERT INTO ntechardata VALUES (?, ?, ?, ?, ?, ?)", row)
    connection.execute(
        "INSERT INTO ntegroupmember VALUES ('1128870029', 'onebot', 'u1', '100', 'tester', '2026-01-01 00:00:00')"
    )
    connection.commit()
    connection.close()
    result = NTERankDataService(db).build_role_rank(RankRequest("c1", False, "group"), 1128870029)
    image_path = NTERankRenderer(output_dir=tmp_path).render(result, {})
    with Image.open(image_path) as image:
        assert image.width == NTERankRenderer.WIDTH
        assert image.height > NTERankRenderer.HEADER_HEIGHT + NTERankRenderer.FOOTER_HEIGHT
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((34, 34))[3] == 255


def test_default_character_art_cache_stays_outside_project_resources():
    renderer = NTERankRenderer()
    assert renderer.character_art_dir == ROOT / "data" / "nte_rank_characters"


def test_role_header_uses_the_matching_original_character_art(tmp_path: Path):
    renderer = NTERankRenderer(character_art_dir=tmp_path / "character_art")
    art_path = renderer._character_art_path("1019")
    assert art_path is not None
    assert art_path.name == "1019.png"
    assert art_path.parent == ROOT / "bot" / "resources" / "nte_rank_characters"


def test_character_art_refresh_overwrites_cache_and_accepts_new_ids(tmp_path: Path):
    image = Image.new("RGBA", (12, 12), "#00aacc")
    payload = BytesIO()
    image.save(payload, format="PNG")
    seen: list[str] = []

    async def fetch(char_id: str) -> bytes:
        seen.append(char_id)
        return payload.getvalue()

    art_dir = tmp_path / "character_art"
    stale = art_dir / "1019.png"
    stale.parent.mkdir()
    stale.write_bytes(b"old")
    renderer = NTERankRenderer(character_art_dir=art_dir)

    refreshed = asyncio.run(renderer.refresh_character_art(("1019", "1020", "bad"), fetch))

    assert refreshed == ("1019", "1020")
    assert seen == ["1019", "1020"]
    assert stale.read_bytes() == payload.getvalue()
    assert (art_dir / "1020.png").read_bytes() == payload.getvalue()
