from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

from PIL import Image

from bot.services.wuwa_help_render import WuwaFullHelpRenderer, WuwaHelpRenderer
from bot.services.wuwa_rank_data import (
    PAGE_SIZE,
    WuwaRankDataService,
    WuwaRankRequest,
    is_full_wuwa_help_command,
    is_wuwa_help_command,
    parse_wuwa_rank_command,
)
from bot.services.wuwa_rank_render import WuwaRankRenderer


GROUP = 1128870029


def _core_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE wavesbind (id INTEGER PRIMARY KEY,bot_id TEXT,user_id TEXT,group_id TEXT,uid TEXT,pgr_uid TEXT)")


def _metadata_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE managed_groups (group_id INTEGER PRIMARY KEY,group_name TEXT,alias TEXT)")
        connection.execute("CREATE TABLE group_members (group_id INTEGER,user_id INTEGER,card TEXT,nickname TEXT,active INTEGER)")
        connection.execute("INSERT INTO managed_groups VALUES (?,?,?)", (GROUP, "A海岸一群", "修会"))
        connection.execute("INSERT INTO group_members VALUES (?,?,?,?,?)", (GROUP, 101, "测试卡片", "测试昵称", 1))


def _role(char_id: str = "1304", name: str = "今汐") -> dict:
    return {
        "level": 90,
        "role": {"roleId": int(char_id), "roleName": name, "level": 90},
        "chainList": [{"unlocked": True}, {"unlocked": False}],
        "weaponData": {"level": 90, "resonLevel": 2, "weapon": {"weaponName": "时和岁稔"}},
        "phantomData": {"equipPhantomList": [{"fetterDetail": {"name": "浮星祛暗"}}]},
    }


def _player(root: Path, uid: str, score: float, *, compressed: bool = False) -> None:
    directory = root / uid
    directory.mkdir(parents=True)
    (directory / "charListData.json").write_text(json.dumps({"1304": score}), encoding="utf-8")
    if compressed:
        with gzip.open(directory / "rawData.json.gz", "wt", encoding="utf-8") as stream:
            json.dump([_role()], stream, ensure_ascii=False)
    else:
        (directory / "rawData.json").write_text(json.dumps([_role()], ensure_ascii=False), encoding="utf-8")


def test_wuwa_rank_parser_supports_local_and_total_boards():
    assert parse_wuwa_rank_command("#ww今汐评分排行") == WuwaRankRequest("role", "今汐", None, 1)
    assert parse_wuwa_rank_command("#ww今汐综合评分排行2") == WuwaRankRequest("role", "今汐", None, 2)
    assert parse_wuwa_rank_command("WW 今汐声骸总排行 页2") == WuwaRankRequest("phantom", "今汐", "bot", 2)
    assert parse_wuwa_rank_command("ww今汐评分排行页3") == WuwaRankRequest("role", "今汐", None, 3)
    assert parse_wuwa_rank_command("ww练度排行") == WuwaRankRequest("practice", None, None, 1)
    assert parse_wuwa_rank_command("ww练度排行3") == WuwaRankRequest("practice", None, None, 3)
    assert parse_wuwa_rank_command("#ww最强总排行") == WuwaRankRequest("strongest", None, "bot", 1)


def test_wuwa_rank_parser_leaves_upstream_activity_rankings_untouched():
    for command in (
        "#ww抽卡排行",
        "#ww群抽卡排行",
        "#ww无尽排行",
        "#ww群无尽排行",
        "#ww冥海总排行",
        "#ww矩阵排行",
        "#ww矩阵单队总排行",
        "#ww长离排行",
        "#ww长离排行2",
        "#ww长离伤害排行",
        "#ww长离伤害排行2",
        "#ww深塔排行",
        "#ww长离评分排行ss2",
    ):
        assert parse_wuwa_rank_command(command) is None


def test_wuwa_full_help_aliases_are_intercepted():
    for command in ("#ww完整帮助", "ww 完整帮助", "WW fullhelp"):
        assert is_wuwa_help_command(command)
        assert is_full_wuwa_help_command(command)


def test_wuwa_rank_reads_group_binding_compressed_cache_and_project_identity(tmp_path: Path):
    core = tmp_path / "GsData.db"
    metadata = tmp_path / "bot.db"
    players = tmp_path / "players"
    _core_db(core)
    _metadata_db(metadata)
    with sqlite3.connect(core) as connection:
        connection.execute("INSERT INTO wavesbind VALUES (NULL,'onebot','101',?,'123456789','')", (str(GROUP),))
        connection.execute("INSERT INTO wavesbind VALUES (NULL,'onebot','202','9000','987654321','')")
    _player(players, "123456789", 321.5, compressed=True)
    _player(players, "987654321", 400)
    service = WuwaRankDataService(core, players, metadata_db_path=metadata, resource_root=tmp_path / "resource")

    group = service.build(WuwaRankRequest("role", "1304", None), GROUP, 101)
    assert group.total == 1
    assert group.rows[0].nickname == "测试卡片"
    assert group.rows[0].group_name == "修会"
    assert group.rows[0].weapon_name == "时和岁稔"
    assert group.rows[0].sonata_name == "浮星祛暗"
    assert group.rows[0].is_self

    total = service.build(WuwaRankRequest("role", "1304", "bot"), GROUP)
    assert [row.uid for row in total.rows] == ["987654321", "123456789"]


def test_wuwa_rank_page_size_is_100_and_empty_board_is_valid(tmp_path: Path):
    core = tmp_path / "GsData.db"
    players = tmp_path / "players"
    _core_db(core)
    with sqlite3.connect(core) as connection:
        for index in range(105):
            uid = f"1{index:08d}"
            connection.execute("INSERT INTO wavesbind VALUES (NULL,'onebot',?,?,?,'')", (str(index), str(GROUP), uid))
            _player(players, uid, 500 - index)
    service = WuwaRankDataService(core, players, resource_root=tmp_path / "resource")
    first = service.build(WuwaRankRequest("role", "1304", None), GROUP, 104)
    assert PAGE_SIZE == 100
    assert len(first.rows) == 100
    assert first.total == 105
    assert first.self_overflow is not None and first.self_overflow.rank == 105
    empty = service.build(WuwaRankRequest("role", "不存在", None), GROUP)
    assert empty.total == 0 and empty.total_pages == 1


def test_wuwa_help_and_rank_render_are_dynamic_rgba_images(tmp_path: Path):
    help_path = WuwaHelpRenderer(output_dir=tmp_path).render(force=True)
    with Image.open(help_path) as image:
        assert image.mode == "RGBA"
        assert image.width == WuwaHelpRenderer.WIDTH
    full_data = WuwaFullHelpRenderer.load_data()
    assert "群管理员功能" in full_data
    assert "Bot 主人功能" in full_data
    result = type("Result", (), {})
    core = tmp_path / "GsData.db"
    players = tmp_path / "players"
    _core_db(core)
    built = WuwaRankDataService(core, players, resource_root=tmp_path / "resource").build(
        WuwaRankRequest("practice", None, None), GROUP
    )
    rank_path = WuwaRankRenderer(output_dir=tmp_path).render(built, {})
    with Image.open(rank_path) as image:
        assert image.mode == "RGBA"
        assert image.width == WuwaRankRenderer.WIDTH
        assert image.height >= WuwaRankRenderer.HEADER_HEIGHT + WuwaRankRenderer.ROW_HEIGHT
