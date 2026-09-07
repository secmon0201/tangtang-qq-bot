from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bot.services.wuwa_data_import import TABLE_KEYS, WuwaDataImporter, WuwaImportError


GROUP = 910000101


SCHEMAS = {
    "wavesbind": "id INTEGER PRIMARY KEY,bot_id TEXT,user_id TEXT,group_id TEXT,uid TEXT,pgr_uid TEXT",
    "wavesuser": "id INTEGER PRIMARY KEY,bot_id TEXT,user_id TEXT,uid TEXT,game_id INTEGER,cookie TEXT",
    "WavesUserActivity": "id INTEGER PRIMARY KEY,bot_id TEXT,user_id TEXT,bot_self_id TEXT,last_active_time INTEGER",
    "WavesGroupActivity": "id INTEGER PRIMARY KEY,bot_id TEXT,group_id TEXT,bot_self_id TEXT,last_active_time INTEGER",
    "WavesStaminaRecord": "id INTEGER PRIMARY KEY,bot_id TEXT,user_id TEXT,uid TEXT,bot_self_id TEXT,mr_value INTEGER",
    "WavesUserSdk": "id INTEGER PRIMARY KEY,bot_id TEXT,user_id TEXT,uid TEXT,region TEXT,bat_expires_at INTEGER",
    "WavesGachaCloud": "id INTEGER PRIMARY KEY,bot_id TEXT,user_id TEXT,uid TEXT,login_info TEXT",
    "WavesSubscribe": "id INTEGER PRIMARY KEY,bot_id TEXT,user_id TEXT,group_id TEXT,bot_self_id TEXT",
    "WavesLangSettings": "id INTEGER PRIMARY KEY,user_id TEXT,lang TEXT",
}


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for table, schema in SCHEMAS.items():
            connection.execute(f'CREATE TABLE "{table}" ({schema})')


def _source(path: Path, *, outside: bool = False) -> None:
    _database(path)
    with sqlite3.connect(path) as connection:
        group = f"{GROUP}_9000" if outside else str(GROUP)
        connection.execute("INSERT INTO wavesbind VALUES (1,'onebot','101',?,'123456789','')", (group,))
        connection.execute("INSERT INTO wavesuser VALUES (1,'onebot','101','123456789',3,'secret-cookie')")
        connection.execute("INSERT INTO WavesUserActivity VALUES (1,'onebot','101','bot',1)")
        connection.execute("INSERT INTO WavesGroupActivity VALUES (1,'onebot',?,'bot',1)", (group,))
        connection.execute("INSERT INTO WavesStaminaRecord VALUES (1,'onebot','101','123456789','bot',200)")
        connection.execute("INSERT INTO WavesUserSdk VALUES (1,'onebot','101','123456789','cn',1)")
        connection.execute("INSERT INTO WavesGachaCloud VALUES (1,'onebot','101','123456789','secret-login')")
        connection.execute("INSERT INTO WavesSubscribe VALUES (1,'onebot','101',?,'bot')", (group,))
        connection.execute("INSERT INTO WavesLangSettings VALUES (1,'101','chs')")


def test_import_plan_is_dry_run_and_never_exposes_sensitive_values(tmp_path: Path):
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    source_players = tmp_path / "source-players"
    target_players = tmp_path / "target-players"
    _source(source)
    _database(target)
    player = source_players / "123456789"
    player.mkdir(parents=True)
    (player / "charListData.json").write_text('{"1304": 100}', encoding="utf-8")
    importer = WuwaDataImporter(
        source, target, source_players, target_players, allowed_groups=(GROUP,)
    )

    plan = importer.plan()
    audit = plan.audit(applied=False)
    assert plan.users == ("101",)
    assert plan.uids == ("123456789",)
    assert plan.table_counts == {table: 1 for table in TABLE_KEYS}
    assert audit["mode"] == "dry-run"
    assert "secret" not in repr(audit)
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM wavesbind").fetchone()[0] == 0


def test_apply_backs_up_and_merges_rows_without_copying_ids(tmp_path: Path):
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    source_players = tmp_path / "source-players"
    target_players = tmp_path / "target-players"
    _source(source)
    _database(target)
    with sqlite3.connect(target) as connection:
        connection.execute("INSERT INTO wavesbind VALUES (99,'onebot','101',?,'111111111','')", (str(GROUP),))
    source_dir = source_players / "123456789"
    source_dir.mkdir(parents=True)
    (source_dir / "rawData.json.gz").write_bytes(b"source")
    existing = target_players / "123456789"
    existing.mkdir(parents=True)
    (existing / "old.txt").write_text("old", encoding="utf-8")
    importer = WuwaDataImporter(
        source, target, source_players, target_players, allowed_groups=(GROUP,)
    )

    audit = importer.apply()
    assert audit["mode"] == "apply"
    assert audit["backups"]
    with sqlite3.connect(target) as connection:
        row = connection.execute("SELECT id,uid,group_id FROM wavesbind").fetchone()
        assert row == (99, "111111111_123456789", str(GROUP))
        assert connection.execute("SELECT cookie FROM wavesuser").fetchone()[0] == "secret-cookie"
    assert (target_players / "123456789" / "rawData.json.gz").read_bytes() == b"source"
    assert any(Path(path).exists() for path in audit["backups"])


def test_apply_rejects_any_source_group_outside_selected_cluster(tmp_path: Path):
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _source(source, outside=True)
    _database(target)
    importer = WuwaDataImporter(
        source,
        target,
        tmp_path / "players",
        tmp_path / "target-players",
        allowed_groups=(GROUP,),
    )
    plan = importer.plan()
    assert plan.outside_groups == (9000,)
    with pytest.raises(WuwaImportError, match="指定集群之外"):
        importer.apply(plan)


def test_import_rejects_player_uid_that_could_escape_player_root(tmp_path: Path):
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _source(source)
    _database(target)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE wavesbind SET uid='../outside'")
    importer = WuwaDataImporter(
        source,
        target,
        tmp_path / "players",
        tmp_path / "target-players",
        allowed_groups=(GROUP,),
    )

    with pytest.raises(WuwaImportError, match="安全导入"):
        importer.plan()
