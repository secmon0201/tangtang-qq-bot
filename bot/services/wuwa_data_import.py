"""Scoped, dry-run-first import of existing XutheringWavesUID runtime data."""

from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "wavesbind": ("bot_id", "user_id"),
    "wavesuser": ("bot_id", "user_id", "uid", "game_id"),
    "WavesUserActivity": ("bot_id", "user_id", "bot_self_id"),
    "WavesGroupActivity": ("bot_id", "group_id", "bot_self_id"),
    "WavesStaminaRecord": ("bot_id", "user_id", "uid"),
    "WavesUserSdk": ("bot_id", "user_id", "uid", "region"),
    "WavesGachaCloud": ("bot_id", "user_id", "uid"),
    "WavesSubscribe": ("group_id",),
    "WavesLangSettings": ("user_id",),
}


class WuwaImportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WuwaImportPlan:
    groups: tuple[int, ...]
    users: tuple[str, ...]
    uids: tuple[str, ...]
    table_counts: dict[str, int]
    player_directories: tuple[str, ...]
    outside_groups: tuple[int, ...]

    def audit(self, *, applied: bool, backups: tuple[str, ...] = ()) -> dict[str, Any]:
        return {
            "mode": "apply" if applied else "dry-run",
            "groups": list(self.groups),
            "user_count": len(self.users),
            "uid_count": len(self.uids),
            "table_counts": dict(self.table_counts),
            "player_directory_count": len(self.player_directories),
            "outside_groups": list(self.outside_groups),
            "backups": list(backups),
        }


class WuwaDataImporter:
    def __init__(
        self,
        source_db: Path,
        target_db: Path,
        source_players: Path,
        target_players: Path,
        *,
        allowed_groups: tuple[int, ...],
    ) -> None:
        self.source_db = Path(source_db).resolve()
        self.target_db = Path(target_db).resolve()
        self.source_players = Path(source_players).resolve()
        self.target_players = Path(target_players).resolve()
        self.allowed_groups = tuple(dict.fromkeys(int(value) for value in allowed_groups))
        if not self.allowed_groups:
            raise ValueError("allowed_groups must not be empty")

    def plan(self) -> WuwaImportPlan:
        if self.source_db == self.target_db:
            raise WuwaImportError("源数据库与目标数据库不能是同一个文件")
        if self.source_players == self.target_players:
            raise WuwaImportError("源玩家目录与目标玩家目录不能是同一个目录")
        if not self.source_db.is_file():
            raise WuwaImportError(f"找不到源数据库：{self.source_db}")
        with _connect_ro(self.source_db) as source:
            self._validate_tables(source)
            binds = list(source.execute("SELECT * FROM wavesbind"))
            selected = [row for row in binds if set(_group_tokens(row["group_id"])) & set(self.allowed_groups)]
            users = tuple(sorted({str(row["user_id"] or "") for row in selected if str(row["user_id"] or "")}))
            raw_uids = {uid for row in selected for uid in _tokens(row["uid"])}
            invalid_uids = sorted(uid for uid in raw_uids if re.fullmatch(r"[0-9]{1,32}", uid) is None)
            if invalid_uids:
                raise WuwaImportError(
                    "源数据包含无法安全导入的 UID：" + ", ".join(repr(uid) for uid in invalid_uids)
                )
            uids = tuple(sorted(raw_uids))
            outside = self._outside_groups(source, binds)
            counts = {
                table: len(self._selected_rows(source, table, users, uids))
                for table in TABLE_KEYS
            }
        player_dirs = tuple(uid for uid in uids if (self.source_players / uid).is_dir())
        return WuwaImportPlan(self.allowed_groups, users, uids, counts, player_dirs, outside)

    def apply(self, plan: WuwaImportPlan | None = None) -> dict[str, Any]:
        plan = plan or self.plan()
        if plan.outside_groups:
            raise WuwaImportError(
                "源数据包含指定集群之外的群号，拒绝写入："
                + ", ".join(str(value) for value in plan.outside_groups)
            )
        if not self.target_db.is_file():
            raise WuwaImportError(f"找不到目标数据库：{self.target_db}")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = self.target_db.parent / "backups" / f"wuwa-import-{stamp}"
        backup_root.mkdir(parents=True, exist_ok=False)
        db_backup = backup_root / self.target_db.name
        with sqlite3.connect(self.target_db) as target, sqlite3.connect(db_backup) as backup:
            target.backup(backup)
        backups = [str(db_backup)]
        with _connect_ro(self.source_db) as source, sqlite3.connect(self.target_db) as target:
            source.row_factory = sqlite3.Row
            target.row_factory = sqlite3.Row
            self._validate_tables(target)
            target.execute("BEGIN IMMEDIATE")
            try:
                for table, keys in TABLE_KEYS.items():
                    for row in self._selected_rows(source, table, plan.users, plan.uids):
                        self._merge_row(target, table, keys, row)
                conflict_root = backup_root / "players"
                for uid in plan.player_directories:
                    source_dir = self.source_players / uid
                    target_dir = self.target_players / uid
                    if target_dir.exists():
                        conflict_root.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(target_dir, conflict_root / uid)
                    target_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
                target.commit()
            except Exception:
                target.rollback()
                raise
        if (backup_root / "players").exists():
            backups.append(str(backup_root / "players"))
        return plan.audit(applied=True, backups=tuple(backups))

    @staticmethod
    def _validate_tables(connection: sqlite3.Connection) -> None:
        for table, keys in TABLE_KEYS.items():
            columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
            if not columns:
                raise WuwaImportError(f"数据库缺少鸣潮表：{table}")
            missing = sorted(set(keys) - columns)
            if missing:
                raise WuwaImportError(f"数据库表 {table} 缺少字段：{', '.join(missing)}")

    def _outside_groups(self, source: sqlite3.Connection, binds: list[sqlite3.Row]) -> tuple[int, ...]:
        observed = {group for row in binds for group in _group_tokens(row["group_id"])}
        for table in ("WavesGroupActivity", "WavesSubscribe"):
            observed.update(
                group
                for row in source.execute(f"SELECT group_id FROM {table}")
                for group in _group_tokens(row["group_id"])
            )
        return tuple(sorted(observed - set(self.allowed_groups)))

    def _selected_rows(
        self,
        source: sqlite3.Connection,
        table: str,
        users: tuple[str, ...],
        uids: tuple[str, ...],
    ) -> list[sqlite3.Row]:
        rows = list(source.execute(f'SELECT * FROM "{table}"'))
        user_set = set(users)
        uid_set = set(uids)
        allowed = set(self.allowed_groups)
        if table == "wavesbind":
            return [row for row in rows if str(row["user_id"] or "") in user_set]
        if table in {"WavesGroupActivity", "WavesSubscribe"}:
            return [row for row in rows if set(_group_tokens(row["group_id"])) & allowed]
        columns = set(rows[0].keys()) if rows else {str(row[1]) for row in source.execute(f"PRAGMA table_info({table})")}
        selected = rows
        if "user_id" in columns:
            selected = [row for row in selected if str(row["user_id"] or "") in user_set]
        if "uid" in columns:
            selected = [row for row in selected if str(row["uid"] or "") in uid_set]
        return selected

    def _merge_row(self, target: sqlite3.Connection, table: str, keys: tuple[str, ...], row: sqlite3.Row) -> None:
        columns = [name for name in row.keys() if name != "id"]
        where = " AND ".join(f'"{key}" IS ?' for key in keys)
        key_values = [row[key] for key in keys]
        existing = target.execute(f'SELECT * FROM "{table}" WHERE {where} LIMIT 1', key_values).fetchone()
        values = {name: row[name] for name in columns}
        if table == "wavesbind":
            values["uid"] = _joined_union(existing["uid"] if existing else "", row["uid"])
            values["group_id"] = _joined_union(
                existing["group_id"] if existing else "",
                "_".join(str(group) for group in _group_tokens(row["group_id"]) if group in set(self.allowed_groups)),
            )
        if existing is None:
            names = ",".join(f'"{name}"' for name in columns)
            marks = ",".join("?" for _ in columns)
            target.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})', [values[name] for name in columns])
            return
        update_columns = [name for name in columns if name not in keys]
        assignment = ",".join(f'"{name}"=?' for name in update_columns)
        target.execute(
            f'UPDATE "{table}" SET {assignment} WHERE {where}',
            [values[name] for name in update_columns] + key_values,
        )


def _connect_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _tokens(value: object) -> tuple[str, ...]:
    return tuple(token for token in str(value or "").split("_") if token)


def _group_tokens(value: object) -> tuple[int, ...]:
    result: list[int] = []
    for token in _tokens(value):
        try:
            result.append(int(token))
        except ValueError:
            raise WuwaImportError(f"源数据包含无法识别的群号：{token!r}") from None
    return tuple(result)


def _joined_union(left: object, right: object) -> str:
    return "_".join(dict.fromkeys((*_tokens(left), *_tokens(right))))


__all__ = ["TABLE_KEYS", "WuwaDataImporter", "WuwaImportError", "WuwaImportPlan"]
