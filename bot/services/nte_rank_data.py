"""Read-only NTEUID ranking data and command parsing.

The service intentionally knows only the SQLite schema used by NTEUID.  It
does not import the upstream plugin, so an upstream upgrade cannot change the
NoneBot process' import graph or mutate Core data.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from bot.config import ROOT, settings


ScopeKind = Literal["bot", "group"]
NTE_BOT_ID = "onebot"
PAGE_SIZE = 100
_GRADE_ORDER = {"S": 3, "A": 2, "B": 1}
_COMMAND_RE = re.compile(r"^#?\s*nte\s*(?P<body>.+?)\s*$", re.IGNORECASE)
_PAGE_RE = re.compile(r"^(?P<body>.+?)\s+页(?P<page>[1-9]\d*)$", re.IGNORECASE)
_STRONGEST_RE = re.compile(
    r"^(?P<leading>bot|群|总)?最强(?P<trailing>bot|群|总)?排行$", re.IGNORECASE
)
_ROLE_RE = re.compile(
    r"^(?P<char>.+?)(?P<scope>bot|群|总)?(?:评分)?(?:排名|排行榜|排行)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RankRequest:
    """A parsed command before its default scope is resolved."""

    character: str | None
    strongest: bool
    explicit_scope: Literal["bot", "group"] | None
    page: int = 1


@dataclass(frozen=True, slots=True)
class RankRow:
    rank: int
    uid: str
    user_id: str
    nickname: str
    char_id: str
    char_name: str
    element_type: str
    awaken_level: int
    suit_name: str
    suit_pieces: int
    score: int
    grade: str
    group_id: int | None
    group_name: str
    is_self: bool = False


@dataclass(frozen=True, slots=True)
class RankResult:
    request: RankRequest
    scope: ScopeKind
    scope_label: str
    title: str
    rows: tuple[RankRow, ...]
    total: int
    total_pages: int
    self_overflow: RankRow | None = None


class NTERankDataError(RuntimeError):
    """The local Core database is missing or has an incompatible schema."""


def parse_rank_command(text: str) -> RankRequest | None:
    """Parse the project-owned subset of the upstream rank command surface."""

    match = _COMMAND_RE.fullmatch(text.strip())
    if match is None:
        return None
    body = match.group("body")
    page = 1
    page_match = _PAGE_RE.fullmatch(body)
    if page_match is not None:
        body = page_match.group("body").strip()
        page = int(page_match.group("page"))

    strongest = _STRONGEST_RE.fullmatch(body)
    if strongest is not None:
        return RankRequest(
            character=None,
            strongest=True,
            explicit_scope=(
                _scope_token(strongest.group("trailing"))
                or _scope_token(strongest.group("leading"))
            ),
            page=page,
        )

    leading_scope: Literal["bot", "group"] | None = None
    for token, scope in (("bot", "bot"), ("总", "bot"), ("群", "group")):
        if body.startswith(token) and len(body) > len(token):
            leading_scope = scope
            body = body[len(token):]
            break
    role = _ROLE_RE.fullmatch(body)
    if role is None:
        return None
    character = role.group("char").strip()
    if not character:
        return None
    return RankRequest(
        character=character,
        strongest=False,
        explicit_scope=_scope_token(role.group("scope")) or leading_scope,
        page=page,
    )


def is_nte_help_command(text: str) -> bool:
    body = _nte_command_body(text)
    return body is not None and body.lower() in {"帮助", "原版帮助"}


def is_new_nte_help_command(text: str) -> bool:
    body = _nte_command_body(text)
    return body is not None and body.lower() == "帮助"


def is_original_nte_help_command(text: str) -> bool:
    body = _nte_command_body(text)
    return body is not None and body.lower() == "原版帮助"


def _nte_command_body(text: str) -> str | None:
    match = _COMMAND_RE.fullmatch(text.strip())
    return match.group("body").strip() if match is not None else None


def resolve_scope(group_id: int, explicit_scope: str | None) -> ScopeKind:
    """Default to the current group; only an explicit total token is bot-wide."""

    if explicit_scope in {"群", "group"}:
        return "group"
    if explicit_scope and explicit_scope.lower() == "bot":
        return "bot"
    return "group"


def scope_label(scope: ScopeKind, group_id: int) -> str:
    if scope == "group":
        return f"本群榜 · {int(group_id)}"
    return "机器人总榜"


class NTERankDataService:
    """Synchronous, read-only SQLite service; call it in ``asyncio.to_thread``."""

    REQUIRED_COLUMNS = {
        "ntechardata": {"uid", "char_id", "detail", "score", "grade", "updated_at"},
        "ntegroupmember": {"group_id", "bot_id", "uid", "user_id", "role_name", "updated_at"},
        "coregroup": {"group_id", "group_name"},
        "nteuser": {"uid", "bot_id", "user_id", "role_name", "updated_at"},
    }

    def __init__(
        self,
        db_path: Path,
        bot_id: str = NTE_BOT_ID,
        group_metadata_path: Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.bot_id = bot_id
        self.group_metadata_path = Path(group_metadata_path) if group_metadata_path else None

    def build_role_rank(
        self,
        request: RankRequest,
        group_id: int,
        viewer_user_id: int | None = None,
    ) -> RankResult:
        if request.character is None:
            raise NTERankDataError("角色名不能为空")
        with self._connect() as connection:
            self.validate_schema(connection)
            char_id, char_name = self._resolve_character(connection, request.character)
            scope = resolve_scope(group_id, request.explicit_scope)
            records, identity = self._eligible_records(connection, char_id, scope, group_id)
            rows = self._make_rows(records, identity, char_id, char_name)
            return self._paginate(request, scope, group_id, rows, viewer_user_id, char_name)

    def character_ids(self) -> tuple[str, ...]:
        """Return every character currently known to the upstream game cache."""

        with self._connect() as connection:
            self.validate_schema(connection)
            return tuple(
                str(row["char_id"])
                for row in connection.execute(
                    "SELECT DISTINCT char_id FROM ntechardata "
                    "WHERE char_id <> '' ORDER BY char_id"
                )
            )

    def build_strongest_rank(
        self,
        request: RankRequest,
        group_id: int,
        viewer_user_id: int | None = None,
    ) -> RankResult:
        del viewer_user_id  # The cross-character board has no single personal row.
        with self._connect() as connection:
            self.validate_schema(connection)
            scope = resolve_scope(group_id, request.explicit_scope)
            records, identity = self._eligible_records(connection, None, scope, group_id)
            best: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
            for record in records:
                key = str(record["char_id"])
                current = best.get(key)
                candidate_key = self._record_sort_key(record, identity.get(str(record["uid"])))
                if current is None or candidate_key < self._record_sort_key(current[0], current[1]):
                    best[key] = (record, identity.get(str(record["uid"]), {}))

            rows: list[RankRow] = []
            for record, holder in best.values():
                detail = _detail(record.get("detail"))
                char_name = str(detail.get("name") or record["char_id"])
                rows.append(
                    self._make_row(
                        record,
                        holder,
                        str(record["char_id"]),
                        char_name,
                        rank=0,
                    )
                )
            rows.sort(key=lambda row: (-row.score, -_GRADE_ORDER.get(row.grade, 0), _uid_key(row.uid)))
            ranked = tuple(replace(row, rank=index) for index, row in enumerate(rows, 1))
            start = (request.page - 1) * PAGE_SIZE
            total = len(ranked)
            return RankResult(
                request=request,
                scope=scope,
                scope_label=scope_label(scope, group_id),
                title="异环最强排行",
                rows=ranked[start : start + PAGE_SIZE],
                total=total,
                total_pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
            )

    def validate_schema(self, connection: sqlite3.Connection) -> None:
        for table, required in self.REQUIRED_COLUMNS.items():
            actual = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not actual:
                raise NTERankDataError(f"GsData.db 缺少表 {table}")
            missing = sorted(required - actual)
            if missing:
                raise NTERankDataError(f"GsData.db 表 {table} 缺少字段：{', '.join(missing)}")

    def _paginate(
        self,
        request: RankRequest,
        scope: ScopeKind,
        group_id: int,
        rows: list[RankRow],
        viewer_user_id: int | None,
        character_title: str,
    ) -> RankResult:
        total = len(rows)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        start = (request.page - 1) * PAGE_SIZE
        page_rows = list(rows[start : start + PAGE_SIZE])
        self_row = next(
            (row for row in rows if viewer_user_id is not None and row.user_id == str(viewer_user_id)),
            None,
        )
        page_uids = {row.uid for row in page_rows}
        overflow = replace(self_row, is_self=True) if self_row and self_row.uid not in page_uids else None
        page_rows = [replace(row, is_self=row.user_id == str(viewer_user_id)) for row in page_rows]
        return RankResult(
            request=request,
            scope=scope,
            scope_label=scope_label(scope, group_id),
            title=f"「{character_title or '角色'}」评分排名",
            rows=tuple(page_rows),
            total=total,
            total_pages=total_pages,
            self_overflow=overflow,
        )

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise NTERankDataError(f"找不到 GsData.db：{self.db_path}")
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5)
        except sqlite3.Error as exc:
            raise NTERankDataError(f"无法只读打开 GsData.db：{exc}") from exc
        connection.row_factory = sqlite3.Row
        return connection

    def _resolve_character(self, connection: sqlite3.Connection, query: str) -> tuple[str, str]:
        normalized = _normalize(query)
        candidates: list[tuple[str, str]] = []
        for row in connection.execute(
            "SELECT char_id, detail FROM ntechardata WHERE grade <> '' ORDER BY char_id"
        ):
            detail = _detail(row["detail"])
            name = str(detail.get("name") or "").strip()
            if name:
                candidates.append((str(row["char_id"]), name))
        unique: dict[str, str] = {}
        for char_id, name in candidates:
            unique.setdefault(char_id, name)
        if query.strip() in unique:
            return query.strip(), unique[query.strip()]
        exact = [(char_id, name) for char_id, name in unique.items() if _normalize(name) == normalized]
        if exact:
            return exact[0]
        contains = [
            (char_id, name)
            for char_id, name in unique.items()
            if normalized and (normalized in _normalize(name) or _normalize(name) in normalized)
        ]
        if contains:
            return sorted(contains, key=lambda item: (len(item[1]), item[0]))[0]
        raise NTERankDataError(f"未找到角色：{query}")

    def _eligible_records(
        self,
        connection: sqlite3.Connection,
        char_id: str | None,
        scope: ScopeKind,
        group_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        where = "WHERE grade <> ''"
        params: tuple[Any, ...] = ()
        if char_id is not None:
            where += " AND char_id = ?"
            params = (char_id,)
        raw_records: dict[tuple[str, str], dict[str, Any]] = {}
        for row in connection.execute(
            f"SELECT uid, char_id, detail, score, grade, updated_at FROM ntechardata {where}", params
        ):
            record = dict(row)
            key = (str(record["uid"]), str(record["char_id"]))
            old = raw_records.get(key)
            if old is None or str(record["updated_at"]) > str(old["updated_at"]):
                raw_records[key] = record

        group_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT group_id, uid, user_id, role_name, updated_at "
                "FROM ntegroupmember WHERE bot_id = ? AND uid <> ''",
                (self.bot_id,),
            )
        ]
        latest_members = _latest_members(group_rows)
        group_members = {
            str(row["uid"]): row for row in group_rows if _as_int(row.get("group_id")) == int(group_id)
        }
        if scope == "group":
            eligible_uids = set(group_members)
            identity = {uid: group_members[uid] for uid in eligible_uids}
        else:
            eligible_uids = {str(record["uid"]) for record in raw_records.values()}
            identity = {uid: latest_members[uid] for uid in eligible_uids if uid in latest_members}

        fallback = self._user_identity(connection)
        for uid in eligible_uids:
            identity.setdefault(uid, fallback.get(uid, {}))
        records = [record for record in raw_records.values() if str(record["uid"]) in eligible_uids]
        records.sort(key=lambda record: self._record_sort_key(record, identity.get(str(record["uid"]))))
        names = self._group_names(connection)
        names.update(self._managed_group_names())
        for uid, row in identity.items():
            group_value = _as_int(row.get("group_id"))
            row["group_id"] = group_value
            row["group_name"] = names.get(group_value, str(group_value) if group_value else "")
        return records, identity

    def _user_identity(self, connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            "SELECT uid, user_id, role_name, updated_at FROM nteuser "
            "WHERE bot_id = ? AND uid <> '' ORDER BY updated_at DESC",
            (self.bot_id,),
        ):
            result.setdefault(str(row["uid"]), dict(row))
        return result

    @staticmethod
    def _group_names(connection: sqlite3.Connection) -> dict[int, str]:
        names: dict[int, str] = {}
        for row in connection.execute(
            "SELECT group_id, group_name FROM coregroup ORDER BY id DESC"
        ):
            group_id = _as_int(row["group_id"])
            name = str(row["group_name"] or "").strip()
            if group_id and name and name != "1":
                names.setdefault(group_id, name)
        return names

    def _managed_group_names(self) -> dict[int, str]:
        """Prefer the bot's QQ-synchronised group names over Core placeholders."""

        if self.group_metadata_path is None or not self.group_metadata_path.exists():
            return {}
        try:
            connection = sqlite3.connect(f"file:{self.group_metadata_path.as_posix()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            with connection:
                return {
                    int(row["group_id"]): str(
                        row["alias"] or row["group_name"] or ""
                    ).strip()
                    for row in connection.execute(
                        "SELECT group_id, group_name, alias FROM managed_groups "
                        "WHERE enabled = 1 AND (alias <> '' OR group_name <> '')"
                    )
                    if str(row["alias"] or row["group_name"] or "").strip()
                }
        except (OSError, sqlite3.Error):
            return {}

    def _make_rows(
        self,
        records: list[dict[str, Any]],
        identity: dict[str, dict[str, Any]],
        char_id: str,
        char_name: str,
    ) -> list[RankRow]:
        result = [
            self._make_row(record, identity.get(str(record["uid"]), {}), char_id, char_name, index)
            for index, record in enumerate(records, 1)
        ]
        return result

    @staticmethod
    def _make_row(
        record: dict[str, Any],
        holder: dict[str, Any],
        char_id: str,
        char_name: str,
        rank: int,
    ) -> RankRow:
        detail = _detail(record.get("detail"))
        suit = detail.get("suit") if isinstance(detail.get("suit"), dict) else {}
        return RankRow(
            rank=rank,
            uid=str(record.get("uid") or ""),
            user_id=str(holder.get("user_id") or ""),
            nickname=str(holder.get("role_name") or holder.get("user_id") or "未获取昵称"),
            char_id=char_id,
            char_name=char_name,
            element_type=str(detail.get("elementType") or ""),
            awaken_level=_as_int(detail.get("awakenLev")) or 0,
            suit_name=str(suit.get("name") or "未识别套装"),
            suit_pieces=_as_int(suit.get("suitActivateNum")) or 0,
            score=_as_int(record.get("score")) or 0,
            grade=str(record.get("grade") or "?").upper(),
            group_id=_as_int(holder.get("group_id")),
            group_name=str(holder.get("group_name") or ""),
        )

    @staticmethod
    def _record_sort_key(record: dict[str, Any], holder: dict[str, Any] | None) -> tuple[Any, ...]:
        holder = holder or {}
        group_id = _as_int(holder.get("group_id"))
        return (
            -(_as_int(record.get("score")) or 0),
            -_GRADE_ORDER.get(str(record.get("grade") or "").upper(), 0),
            group_id if group_id is not None else 10**18,
            _uid_key(str(record.get("uid") or "")),
        )


def _scope_token(value: str | None) -> Literal["bot", "group"] | None:
    if value is None:
        return None
    return "group" if value == "群" else "bot"


def _latest_members(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        uid = str(row.get("uid") or "")
        if not uid:
            continue
        current = result.get(uid)
        current_key = (str(current.get("updated_at") or ""), -(_as_int(current.get("group_id")) or 10**18)) if current else None
        candidate_key = (str(row.get("updated_at") or ""), -(_as_int(row.get("group_id")) or 10**18))
        if current_key is None or candidate_key > current_key:
            result[uid] = row
    return result


def _detail(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize(value: str) -> str:
    return re.sub(r"[\s·・.。\-_]", "", value).casefold()


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _uid_key(value: str) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    return (1, value)


def default_rank_service() -> NTERankDataService:
    return NTERankDataService(
        settings.gsuid_core_dir / "data" / "GsData.db",
        group_metadata_path=settings.db_path,
    )


__all__ = [
    "NTE_BOT_ID",
    "NTERankDataError",
    "NTERankDataService",
    "PAGE_SIZE",
    "RankRequest",
    "RankResult",
    "RankRow",
    "default_rank_service",
    "is_new_nte_help_command",
    "is_nte_help_command",
    "is_original_nte_help_command",
    "parse_rank_command",
    "resolve_scope",
    "scope_label",
]
