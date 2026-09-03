"""Read-only project ranking over local XutheringWavesUID bindings and caches."""

from __future__ import annotations

import gzip
import json
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from bot.config import settings


ScopeKind = Literal["bot", "group"]
RankKind = Literal["role", "phantom", "practice", "strongest"]
PAGE_SIZE = 100
WUWA_BOT_ID = "onebot"
_COMMAND_RE = re.compile(r"^#?\s*ww\s*(?P<body>.+?)\s*$", re.IGNORECASE)
_PAGE_RE = re.compile(r"^(?P<body>.+?)(?:\s*页|\s*第)(?P<page>[1-9]\d*)页?$", re.IGNORECASE)
_TRAILING_PAGE_RE = re.compile(r"^(?P<body>.+?排行)(?P<page>[1-9]\d*)$", re.IGNORECASE)
_PRACTICE_RE = re.compile(r"^(?P<scope>群|总|bot)?练度(?P<trailing>群|总|bot)?排行$", re.IGNORECASE)
_STRONGEST_RE = re.compile(r"^(?P<scope>群|总|bot)?最强(?P<trailing>群|总|bot)?排行$", re.IGNORECASE)
_PHANTOM_RE = re.compile(r"^(?P<char>.+?)声骸(?P<scope>群|总|bot)?排行$", re.IGNORECASE)
_ROLE_SCORE_RE = re.compile(
    r"^(?P<char>.+?)(?:综合)?评分(?P<scope>群|总|bot)?排行$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WuwaRankRequest:
    kind: RankKind
    character: str | None
    explicit_scope: ScopeKind | None
    page: int = 1


@dataclass(frozen=True, slots=True)
class WuwaRankRow:
    rank: int
    uid: str
    user_id: str
    nickname: str
    group_id: int | None
    group_name: str
    char_id: str
    char_name: str
    level: int
    chain: int
    weapon_name: str
    weapon_level: int
    weapon_resonance: int
    sonata_name: str
    score: float
    is_self: bool = False


@dataclass(frozen=True, slots=True)
class WuwaRankResult:
    request: WuwaRankRequest
    scope: ScopeKind
    scope_label: str
    title: str
    rows: tuple[WuwaRankRow, ...]
    total: int
    total_pages: int
    self_overflow: WuwaRankRow | None = None


class WuwaRankDataError(RuntimeError):
    """The local Core database or cache layout is incompatible."""


def parse_wuwa_rank_command(text: str) -> WuwaRankRequest | None:
    match = _COMMAND_RE.fullmatch(str(text).strip())
    if match is None:
        return None
    body = match.group("body").strip()
    page = 1
    page_match = _PAGE_RE.fullmatch(body)
    if page_match is None:
        page_match = _TRAILING_PAGE_RE.fullmatch(body)
    if page_match is not None:
        body = page_match.group("body").strip()
        page = int(page_match.group("page"))
    for pattern, kind in ((_PRACTICE_RE, "practice"), (_STRONGEST_RE, "strongest")):
        special = pattern.fullmatch(body)
        if special is not None:
            return WuwaRankRequest(
                kind=kind,  # type: ignore[arg-type]
                character=None,
                explicit_scope=_scope(special.group("trailing") or special.group("scope")),
                page=page,
            )
    phantom = _PHANTOM_RE.fullmatch(body)
    if phantom is not None:
        return WuwaRankRequest("phantom", phantom.group("char").strip(), _scope(phantom.group("scope")), page)
    role = _ROLE_SCORE_RE.fullmatch(body)
    if role is None or not role.group("char").strip():
        return None
    return WuwaRankRequest("role", role.group("char").strip(), _scope(role.group("scope")), page)


def is_wuwa_help_command(text: str) -> bool:
    body = _wuwa_body(text)
    return body is not None and body.casefold() in {
        "帮助",
        "完整帮助",
        "原版帮助",
        "help",
        "fullhelp",
    }


def is_new_wuwa_help_command(text: str) -> bool:
    body = _wuwa_body(text)
    return body is not None and body.casefold() in {"帮助", "help"}


def is_original_wuwa_help_command(text: str) -> bool:
    body = _wuwa_body(text)
    return body is not None and body.casefold() == "原版帮助"


def is_full_wuwa_help_command(text: str) -> bool:
    body = _wuwa_body(text)
    return body is not None and body.casefold() in {"完整帮助", "fullhelp"}


def _wuwa_body(text: str) -> str | None:
    match = _COMMAND_RE.fullmatch(str(text).strip())
    return match.group("body").strip() if match is not None else None


def _scope(value: str | None) -> ScopeKind | None:
    if not value:
        return None
    return "group" if value.casefold() == "群" else "bot"


class WuwaRankDataService:
    """Build local-only boards without importing or mutating the upstream plugin."""

    REQUIRED_BIND_COLUMNS = {"bot_id", "user_id", "group_id", "uid"}

    def __init__(
        self,
        db_path: Path,
        player_root: Path,
        *,
        metadata_db_path: Path | None = None,
        resource_root: Path | None = None,
        bot_id: str = WUWA_BOT_ID,
    ) -> None:
        self.db_path = Path(db_path)
        self.player_root = Path(player_root)
        self.metadata_db_path = Path(metadata_db_path) if metadata_db_path else None
        self.resource_root = Path(resource_root) if resource_root else self.player_root.parent / "resource"
        self.bot_id = bot_id

    def build(self, request: WuwaRankRequest, group_id: int, viewer_user_id: int | None = None) -> WuwaRankResult:
        scope: ScopeKind = request.explicit_scope or "group"
        bindings = self._bindings(scope, group_id)
        metadata = self._metadata(bindings, group_id)
        records: list[WuwaRankRow] = []
        for binding in bindings:
            user_id = str(binding["user_id"] or "")
            groups = _tokens(binding["group_id"])
            source_group = group_id if scope == "group" else (_last_int(groups) or None)
            identity = metadata.get((user_id, source_group)) or metadata.get((user_id, None)) or {}
            nickname = str(identity.get("nickname") or user_id or "未登记用户")
            group_name = str(identity.get("group_name") or source_group or "未登记群")
            for uid in (token for token in _tokens(binding["uid"]) if re.fullmatch(r"[0-9]{1,32}", token)):
                scores = self._scores(uid)
                if not scores:
                    continue
                details = self._details(uid)
                records.extend(
                    self._rows_for_uid(request, uid, user_id, nickname, source_group, group_name, scores, details)
                )
        if request.kind == "strongest":
            best: dict[str, WuwaRankRow] = {}
            for row in records:
                current = best.get(row.char_id)
                if current is None or _sort_key(row) < _sort_key(current):
                    best[row.char_id] = row
            records = list(best.values())
        records.sort(key=_sort_key)
        ranked = [replace(row, rank=index) for index, row in enumerate(records, 1)]
        total = len(ranked)
        start = (request.page - 1) * PAGE_SIZE
        page_rows = [replace(row, is_self=row.user_id == str(viewer_user_id)) for row in ranked[start : start + PAGE_SIZE]]
        visible_keys = {(row.uid, row.char_id) for row in page_rows}
        own = next((row for row in ranked if viewer_user_id is not None and row.user_id == str(viewer_user_id)), None)
        overflow = replace(own, is_self=True) if own and (own.uid, own.char_id) not in visible_keys else None
        return WuwaRankResult(
            request=request,
            scope=scope,
            scope_label=f"本群榜 · {group_id}" if scope == "group" else "机器人总榜",
            title=self._title(request, records),
            rows=tuple(page_rows),
            total=total,
            total_pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
            self_overflow=overflow,
        )

    def _rows_for_uid(
        self,
        request: WuwaRankRequest,
        uid: str,
        user_id: str,
        nickname: str,
        group_id: int | None,
        group_name: str,
        scores: dict[str, float],
        details: dict[str, dict[str, Any]],
    ) -> list[WuwaRankRow]:
        if request.kind == "practice":
            char_id = max(scores, key=lambda value: scores[value])
            return [self._row(uid, user_id, nickname, group_id, group_name, char_id, sum(scores.values()), details.get(char_id, {}))]
        selected = scores
        if request.character:
            char_id = self._resolve_character(request.character, set(scores), details)
            selected = {char_id: scores[char_id]} if char_id in scores else {}
        return [
            self._row(uid, user_id, nickname, group_id, group_name, char_id, score, details.get(char_id, {}))
            for char_id, score in selected.items()
        ]

    @staticmethod
    def _row(uid: str, user_id: str, nickname: str, group_id: int | None, group_name: str, char_id: str, score: float, detail: dict[str, Any]) -> WuwaRankRow:
        role = detail.get("role") if isinstance(detail.get("role"), dict) else {}
        weapon_data = detail.get("weaponData") if isinstance(detail.get("weaponData"), dict) else {}
        weapon = weapon_data.get("weapon") if isinstance(weapon_data.get("weapon"), dict) else {}
        phantoms = detail.get("phantomData") if isinstance(detail.get("phantomData"), dict) else {}
        equipped = phantoms.get("equipPhantomList") if isinstance(phantoms.get("equipPhantomList"), list) else []
        sonata = next((str(item.get("fetterDetail", {}).get("name") or "") for item in equipped if isinstance(item, dict) and isinstance(item.get("fetterDetail"), dict)), "")
        chains = detail.get("chainList") if isinstance(detail.get("chainList"), list) else []
        chain = sum(1 for item in chains if isinstance(item, dict) and item.get("unlocked"))
        return WuwaRankRow(
            rank=0,
            uid=uid,
            user_id=user_id,
            nickname=nickname,
            group_id=group_id,
            group_name=group_name,
            char_id=char_id,
            char_name=str(role.get("roleName") or char_id),
            level=_integer(detail.get("level") or role.get("level")),
            chain=chain,
            weapon_name=str(weapon.get("weaponName") or "未记录"),
            weapon_level=_integer(weapon_data.get("level")),
            weapon_resonance=_integer(weapon_data.get("resonLevel")),
            sonata_name=sonata or "未记录",
            score=float(score),
        )

    def _bindings(self, scope: ScopeKind, group_id: int) -> list[sqlite3.Row]:
        if not self.db_path.is_file():
            raise WuwaRankDataError(f"找不到 GsData.db：{self.db_path}")
        try:
            with _connect_ro(self.db_path) as connection:
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(wavesbind)")}
                missing = sorted(self.REQUIRED_BIND_COLUMNS - columns)
                if not columns:
                    raise WuwaRankDataError("GsData.db 缺少表 wavesbind")
                if missing:
                    raise WuwaRankDataError(f"GsData.db 表 wavesbind 缺少字段：{', '.join(missing)}")
                rows = list(connection.execute("SELECT bot_id,user_id,group_id,uid FROM wavesbind WHERE bot_id=?", (self.bot_id,)))
        except sqlite3.Error as exc:
            raise WuwaRankDataError(f"无法只读查询 GsData.db：{exc}") from exc
        if scope == "group":
            token = str(group_id)
            rows = [row for row in rows if token in _tokens(row["group_id"])]
        return rows

    def _metadata(self, bindings: list[sqlite3.Row], viewing_group: int) -> dict[tuple[str, int | None], dict[str, str]]:
        result: dict[tuple[str, int | None], dict[str, str]] = {}
        if not self.metadata_db_path or not self.metadata_db_path.is_file():
            return result
        user_ids = sorted({str(row["user_id"] or "") for row in bindings if str(row["user_id"] or "")})
        if not user_ids:
            return result
        marks = ",".join("?" for _ in user_ids)
        try:
            with _connect_ro(self.metadata_db_path) as connection:
                query = (
                    "SELECT gm.user_id,gm.group_id,gm.card,gm.nickname,mg.alias,mg.group_name,gm.active "
                    "FROM group_members gm JOIN managed_groups mg ON mg.group_id=gm.group_id "
                    f"WHERE CAST(gm.user_id AS TEXT) IN ({marks}) "
                    "ORDER BY (gm.group_id=?) DESC,gm.active DESC,gm.group_id"
                )
                for row in connection.execute(query, (*user_ids, viewing_group)):
                    user_id = str(row["user_id"])
                    group = int(row["group_id"])
                    value = {
                        "nickname": str(row["card"] or row["nickname"] or user_id),
                        "group_name": str(row["alias"] or row["group_name"] or group),
                    }
                    result.setdefault((user_id, group), value)
                    result.setdefault((user_id, None), value)
        except sqlite3.Error:
            return {}
        return result

    def _scores(self, uid: str) -> dict[str, float]:
        data = _load_json(self.player_root / uid / "charListData.json")
        if not isinstance(data, dict):
            return {}
        scores: dict[str, float] = {}
        for key, value in data.items():
            try:
                scores[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return scores

    def _details(self, uid: str) -> dict[str, dict[str, Any]]:
        data = _load_json(self.player_root / uid / "rawData.json")
        if not isinstance(data, list):
            data = _load_json(self.resource_root / "map" / "1.json")
        result: dict[str, dict[str, Any]] = {}
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                if isinstance(role, dict) and role.get("roleId") is not None:
                    result[str(role["roleId"])] = item
        return result

    def _resolve_character(self, query: str, char_ids: set[str], details: dict[str, dict[str, Any]]) -> str:
        normalized = _normalize(query)
        if normalized in char_ids:
            return normalized
        aliases = _load_json(self.resource_root / "map" / "alias" / "char_alias.json")
        canonical = normalized
        if isinstance(aliases, dict):
            for name, values in aliases.items():
                candidates = [name, *(values if isinstance(values, list) else [])]
                if normalized in {_normalize(value) for value in candidates}:
                    canonical = _normalize(name)
                    break
        for char_id, detail in details.items():
            role = detail.get("role") if isinstance(detail.get("role"), dict) else {}
            if _normalize(role.get("roleName")) == canonical:
                return char_id
        return ""

    @staticmethod
    def _title(request: WuwaRankRequest, records: list[WuwaRankRow]) -> str:
        if request.kind == "practice":
            return "鸣潮练度排行"
        if request.kind == "strongest":
            return "鸣潮角色最强排行"
        name = records[0].char_name if records else (request.character or "角色")
        return f"「{name}」{'声骸' if request.kind == 'phantom' else '评分'}排行"


def _connect_ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _load_json(path: Path) -> Any:
    candidates = [path.with_name(path.name + ".gz"), path] if path.name == "rawData.json" else [path]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            opener = gzip.open if candidate.suffix == ".gz" else open
            with opener(candidate, "rt", encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, ValueError):
            continue
    return None


def _tokens(value: object) -> tuple[str, ...]:
    return tuple(token for token in str(value or "").split("_") if token)


def _last_int(values: tuple[str, ...]) -> int | None:
    for value in reversed(values):
        try:
            return int(value)
        except ValueError:
            continue
    return None


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize(value: object) -> str:
    return re.sub(r"[\s·・\-_]", "", str(value or "")).casefold()


def _sort_key(row: WuwaRankRow) -> tuple[float, int, str, str]:
    return (-row.score, -row.chain, row.uid, row.char_id)


def default_wuwa_rank_service() -> WuwaRankDataService:
    data_root = settings.gsuid_core_dir / "data" / "XutheringWavesUID"
    return WuwaRankDataService(
        settings.gsuid_core_dir / "data" / "GsData.db",
        data_root / "players",
        metadata_db_path=settings.db_path,
        resource_root=data_root / "resource",
    )


__all__ = [
    "PAGE_SIZE",
    "WuwaRankDataError",
    "WuwaRankDataService",
    "WuwaRankRequest",
    "WuwaRankResult",
    "WuwaRankRow",
    "default_wuwa_rank_service",
    "is_full_wuwa_help_command",
    "is_new_wuwa_help_command",
    "is_original_wuwa_help_command",
    "is_wuwa_help_command",
    "parse_wuwa_rank_command",
]
