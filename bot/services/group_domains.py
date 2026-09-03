from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from bot.config import A_COAST_GROUP_IDS
from bot.db import Database, utc_now


A_COAST_DOMAIN_KEY = "cluster:a-coast"
A_COAST_ALIASES = dict(
    zip(A_COAST_GROUP_IDS, ("修会", "剧团", "莫塔里", "翡萨烈", "墓岛"), strict=True)
)


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    key: str
    label: str
    solo_default: bool
    aliases: tuple[str, ...] = ()
    dependency: str | None = None
    public: bool = True


FEATURE_SPECS = (
    FeatureSpec("speech_ranking", "发言榜查询", True, ("发言榜", "发言排行")),
    FeatureSpec("speech_ranking_push", "23:50 发言榜推送", False, ("发言榜推送", "榜单推送")),
    FeatureSpec("nte", "NTE", True, ("异环", "异环接口")),
    FeatureSpec("ww", "鸣潮", True, ("WW", "鸣潮接口")),
    FeatureSpec("zhijiang_calendar", "枝江日历", True, ("直播日历", "直播日程")),
    FeatureSpec("mini_games", "小游戏", True, ("游戏",)),
    FeatureSpec("today_wife", "今日老婆", True, ("今日缘分", "缘分")),
    FeatureSpec("passive_interaction", "被动互动", False, ("随机互动",)),
    FeatureSpec("mention_chat", "被呼叫会话", True, ("被呼叫", "糖糖会话")),
    FeatureSpec("proactive_chat", "糖糖主动聊天", False, ("主动聊天", "主动回复")),
    FeatureSpec("hourly", "准时报点", False, ("整点报时", "报点")),
    FeatureSpec("bilibili", "B站推送", False, ("B 站推送", "哔哩哔哩推送")),
    FeatureSpec("speech_archive", "发言档案", True, ("发言记录", "发言搜索", "发言画像")),
    FeatureSpec("live_guard", "枝江直播防护", True, ("直播防护",), dependency="mini_games"),
    FeatureSpec("duplicate", "查重", True, public=False),
)
FEATURES = {spec.key: spec for spec in FEATURE_SPECS}
FEATURE_ALIASES = {
    value.casefold(): spec.key
    for spec in FEATURE_SPECS
    for value in (spec.key, spec.label, *spec.aliases)
}


@dataclass(frozen=True, slots=True)
class GroupDomain:
    domain_id: int
    domain_key: str
    mode: str
    name: str
    alias: str
    public_token: str
    enabled: bool


class GroupDomainService:
    """SQLite authority for independent groups, clusters and local feature intent."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def normalize_feature(value: str) -> str | None:
        return FEATURE_ALIASES.get(str(value).strip().casefold())

    def bootstrap(
        self,
        *,
        legacy_feature_groups: Mapping[str, Iterable[int]] | None = None,
    ) -> None:
        """Migrate enabled groups once while preserving any already stored intent."""

        legacy = {
            key: frozenset(int(group_id) for group_id in group_ids)
            for key, group_ids in (legacy_feature_groups or {}).items()
        }
        now = utc_now()
        with self.database.connect() as connection:
            cluster_id = self._ensure_domain(
                connection,
                A_COAST_DOMAIN_KEY,
                "cluster",
                "A海岸",
                "A海岸",
                now,
            )
            for group_id in A_COAST_GROUP_IDS:
                connection.execute(
                    """UPDATE managed_groups
                       SET domain_id=?,alias=CASE WHEN alias='' THEN ? ELSE alias END
                       WHERE group_id=?""",
                    (cluster_id, A_COAST_ALIASES[group_id], group_id),
                )

            rows = list(
                connection.execute(
                    "SELECT group_id,domain_id FROM managed_groups WHERE enabled=1 ORDER BY group_id"
                )
            )
            for row in rows:
                group_id = int(row["group_id"])
                connection.execute(
                    """UPDATE managed_groups SET public_key=?
                       WHERE group_id=? AND public_key=''""",
                    (secrets.token_urlsafe(12), group_id),
                )
                domain_id = row["domain_id"]
                if domain_id is None:
                    domain_id = self._ensure_domain(
                        connection,
                        f"solo:{group_id}",
                        "solo",
                        str(group_id),
                        "",
                        now,
                    )
                    connection.execute(
                        "UPDATE managed_groups SET domain_id=? WHERE group_id=?",
                        (int(domain_id), group_id),
                    )

            rows = list(
                connection.execute(
                    """SELECT g.group_id,d.mode
                       FROM managed_groups AS g
                       JOIN group_domains AS d ON d.domain_id=g.domain_id
                       WHERE g.enabled=1"""
                )
            )
            for row in rows:
                group_id = int(row["group_id"])
                mode = str(row["mode"])
                for spec in FEATURE_SPECS:
                    existing = connection.execute(
                        """SELECT 1 FROM group_features
                           WHERE group_id=? AND feature_key=?""",
                        (group_id, spec.key),
                    ).fetchone()
                    if existing is not None:
                        continue
                    enabled = True if mode == "cluster" else spec.solo_default
                    if mode == "solo" and spec.key in legacy:
                        enabled = group_id in legacy[spec.key]
                    connection.execute(
                        """INSERT INTO group_features
                           (group_id,feature_key,configured_enabled,updated_at)
                           VALUES (?,?,?,?)""",
                        (group_id, spec.key, int(enabled), now),
                    )

    def ensure_group(
        self,
        group_id: int,
        *,
        group_name: str = "",
        joined_at: str | None = None,
    ) -> bool:
        existing = self.database.managed_group(int(group_id), include_disabled=True)
        previous_domain_id = (
            int(existing["domain_id"])
            if existing is not None and existing["domain_id"] is not None
            else None
        )
        was_enabled = bool(existing["enabled"]) if existing is not None else False
        created = self.database.ensure_group(
            group_id, group_name=group_name, joined_at=joined_at
        )
        now = utc_now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT domain_id FROM managed_groups WHERE group_id=?", (int(group_id),)
            ).fetchone()
            if row is None:
                raise RuntimeError("group registration did not persist")
            domain_id = row["domain_id"]
            if int(group_id) in A_COAST_GROUP_IDS:
                domain_id = self._ensure_domain(
                    connection,
                    A_COAST_DOMAIN_KEY,
                    "cluster",
                    "A海岸",
                    "A海岸",
                    now,
                )
            elif domain_id is None:
                domain_id = self._ensure_domain(
                    connection,
                    f"solo:{int(group_id)}",
                    "solo",
                    str(int(group_id)),
                    "",
                    now,
                )
            connection.execute(
                """UPDATE managed_groups SET
                       domain_id=?,
                       public_key=CASE WHEN public_key='' THEN ? ELSE public_key END
                   WHERE group_id=?""",
                (int(domain_id), secrets.token_urlsafe(12), int(group_id)),
            )
            if existing is not None and not was_enabled:
                if previous_domain_id is not None:
                    self._rotate_link(connection, previous_domain_id, now)
                if previous_domain_id != int(domain_id):
                    self._rotate_link(connection, int(domain_id), now)
            mode = str(
                connection.execute(
                    "SELECT mode FROM group_domains WHERE domain_id=?", (int(domain_id),)
                ).fetchone()["mode"]
            )
            for spec in FEATURE_SPECS:
                connection.execute(
                    """INSERT OR IGNORE INTO group_features
                       (group_id,feature_key,configured_enabled,updated_at)
                       VALUES (?,?,?,?)""",
                    (
                        int(group_id),
                        spec.key,
                        int(True if mode == "cluster" else spec.solo_default),
                        now,
                    ),
                )
        return created

    @staticmethod
    def _ensure_domain(
        connection,
        domain_key: str,
        mode: str,
        name: str,
        alias: str,
        now: str,
    ) -> int:
        row = connection.execute(
            "SELECT domain_id FROM group_domains WHERE domain_key=?", (str(domain_key),)
        ).fetchone()
        if row is not None:
            return int(row["domain_id"])
        cursor = connection.execute(
            """INSERT INTO group_domains
               (domain_key,mode,name,alias,public_token,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                str(domain_key),
                str(mode),
                str(name)[:80],
                str(alias)[:40],
                GroupDomainService._new_token(),
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def all_group_ids(self) -> tuple[int, ...]:
        return tuple(int(row["group_id"]) for row in self.database.managed_groups())

    def feature_enabled(self, group_id: int, feature_key: str) -> bool:
        normalized = self.normalize_feature(feature_key) or str(feature_key)
        return self.database.feature_enabled(int(group_id), normalized)

    def effective_feature_enabled(self, group_id: int, feature_key: str) -> bool:
        normalized = self.normalize_feature(feature_key)
        if normalized is None or not self.feature_enabled(group_id, normalized):
            return False
        dependency = FEATURES[normalized].dependency
        return dependency is None or self.feature_enabled(group_id, dependency)

    def enabled_groups(self, feature_key: str) -> frozenset[int]:
        normalized = self.normalize_feature(feature_key) or str(feature_key)
        return self.database.enabled_feature_groups(normalized)

    def set_feature(self, group_id: int, feature_key: str, enabled: bool) -> None:
        normalized = self.normalize_feature(feature_key)
        if normalized is None:
            raise ValueError("unknown feature")
        if not self.database.is_managed_group(int(group_id)):
            raise ValueError("group is not active")
        self.database.set_group_feature(int(group_id), normalized, bool(enabled))

    def feature_rows(self, group_id: int, *, include_internal: bool = False) -> list[dict[str, object]]:
        values = self.database.group_features(int(group_id))
        rows: list[dict[str, object]] = []
        for spec in FEATURE_SPECS:
            if not spec.public and not include_internal:
                continue
            configured = bool(values.get(spec.key, False))
            effective = configured and (
                spec.dependency is None or bool(values.get(spec.dependency, False))
            )
            rows.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "configured_enabled": configured,
                    "effective_enabled": effective,
                    "dependency": spec.dependency or "",
                }
            )
        return rows

    def domain_for_group(self, group_id: int) -> GroupDomain | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT d.* FROM group_domains AS d
                   JOIN managed_groups AS g ON g.domain_id=d.domain_id
                   WHERE g.group_id=? AND g.enabled=1 AND d.enabled=1""",
                (int(group_id),),
            ).fetchone()
        return self._domain(row)

    def domain_by_token(self, token: str) -> GroupDomain | None:
        if not token or len(token) > 128:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT d.* FROM group_domains AS d
                   WHERE d.public_token=? AND d.enabled=1
                     AND EXISTS (
                         SELECT 1 FROM managed_groups AS g
                         WHERE g.domain_id=d.domain_id AND g.enabled=1
                     )""",
                (str(token),),
            ).fetchone()
        return self._domain(row)

    @staticmethod
    def _domain(row) -> GroupDomain | None:
        if row is None:
            return None
        return GroupDomain(
            domain_id=int(row["domain_id"]),
            domain_key=str(row["domain_key"]),
            mode=str(row["mode"]),
            name=str(row["name"]),
            alias=str(row["alias"]),
            public_token=str(row["public_token"]),
            enabled=bool(row["enabled"]),
        )

    def domain_groups(self, domain_id: int, *, enabled_only: bool = True) -> tuple[int, ...]:
        clause = " AND enabled=1" if enabled_only else ""
        with self.database.connect() as connection:
            domain = connection.execute(
                "SELECT domain_key FROM group_domains WHERE domain_id=?",
                (int(domain_id),),
            ).fetchone()
            rows = list(
                connection.execute(
                    f"SELECT group_id FROM managed_groups WHERE domain_id=?{clause} ORDER BY group_id",
                    (int(domain_id),),
                )
            )
        group_ids = tuple(int(row["group_id"]) for row in rows)
        if domain is not None and str(domain["domain_key"]) == A_COAST_DOMAIN_KEY:
            present = set(group_ids)
            return tuple(group_id for group_id in A_COAST_GROUP_IDS if group_id in present)
        return group_ids

    def ranking_group_ids(self, group_id: int, *, cluster: bool) -> tuple[int, ...]:
        domain = self.domain_for_group(group_id)
        if domain is None:
            return ()
        if cluster and domain.mode == "cluster":
            return self.domain_groups(domain.domain_id)
        return (int(group_id),)

    def display_name(self, group_id: int) -> str:
        row = self.database.managed_group(int(group_id), include_disabled=True)
        if row is None:
            return str(int(group_id))
        return str(row["alias"] or row["group_name"] or row["group_id"])

    def domain_display_name(self, domain: GroupDomain) -> str:
        if domain.mode == "solo":
            groups = self.domain_groups(domain.domain_id)
            if groups:
                return self.display_name(groups[0])
        return domain.alias or domain.name

    def public_group_key(self, group_id: int) -> str | None:
        row = self.database.managed_group(int(group_id), include_disabled=True)
        if row is None:
            return None
        value = str(row["public_key"] or "")
        return value or None

    def group_id_from_public_key(self, domain_id: int, public_key: str) -> int | None:
        if not public_key or len(public_key) > 64:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT group_id FROM managed_groups
                   WHERE domain_id=? AND public_key=? AND enabled=1""",
                (int(domain_id), str(public_key)),
            ).fetchone()
        return int(row["group_id"]) if row is not None else None

    def joined_date(self, group_id: int) -> str | None:
        row = self.database.managed_group(int(group_id), include_disabled=True)
        if row is None or not row["joined_at"]:
            return None
        value = str(row["joined_at"])
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            return value[:10] or None

    def domain_joined_date(self, domain_id: int) -> str | None:
        dates = [
            value
            for group_id in self.domain_groups(domain_id)
            if (value := self.joined_date(group_id)) is not None
        ]
        return min(dates) if dates else None

    def set_alias(self, group_id: int, alias: str) -> None:
        cleaned = " ".join(str(alias).split())
        if not cleaned or len(cleaned) > 20:
            raise ValueError("alias must contain 1-20 visible characters")
        self.database.set_group_alias(int(group_id), cleaned)

    def rotate_link(self, domain_id: int) -> str:
        token = self._new_token()
        with self.database.connect() as connection:
            self._rotate_link(connection, int(domain_id), utc_now(), token=token)
        return token

    @staticmethod
    def _rotate_link(connection, domain_id: int, now: str, *, token: str | None = None) -> str:
        replacement = token or GroupDomainService._new_token()
        connection.execute(
            "UPDATE group_domains SET public_token=?,updated_at=? WHERE domain_id=?",
            (replacement, now, int(domain_id)),
        )
        return replacement

    def disable_group(self, group_id: int) -> None:
        domain = self.domain_for_group(int(group_id))
        self.database.disable_group(int(group_id))
        if domain is not None:
            self.rotate_link(domain.domain_id)

    def create_cluster(self, name: str, alias: str = "") -> GroupDomain:
        cleaned = " ".join(str(name).split())
        if not cleaned:
            raise ValueError("cluster name is required")
        key = f"cluster:{secrets.token_hex(12)}"
        now = utc_now()
        with self.database.connect() as connection:
            domain_id = self._ensure_domain(
                connection, key, "cluster", cleaned, str(alias).strip(), now
            )
            row = connection.execute(
                "SELECT * FROM group_domains WHERE domain_id=?", (domain_id,)
            ).fetchone()
        result = self._domain(row)
        if result is None:
            raise RuntimeError("cluster creation failed")
        return result

    def add_group_to_cluster(self, group_id: int, domain_id: int) -> None:
        group_id = int(group_id)
        now = utc_now()
        with self.database.connect() as connection:
            group = connection.execute(
                "SELECT domain_id FROM managed_groups WHERE group_id=? AND enabled=1",
                (group_id,),
            ).fetchone()
            if group is None:
                raise ValueError("group is not active")
            previous_domain_id = (
                int(group["domain_id"]) if group["domain_id"] is not None else None
            )
            domain = connection.execute(
                "SELECT mode FROM group_domains WHERE domain_id=? AND enabled=1",
                (int(domain_id),),
            ).fetchone()
            if domain is None or str(domain["mode"]) != "cluster":
                raise ValueError("cluster not found")
            if previous_domain_id == int(domain_id):
                return
            connection.execute(
                "UPDATE managed_groups SET domain_id=?,updated_at=? WHERE group_id=?",
                (int(domain_id), now, group_id),
            )
            for spec in FEATURE_SPECS:
                connection.execute(
                    """INSERT INTO group_features
                       (group_id,feature_key,configured_enabled,updated_at)
                       VALUES (?,?,1,?)
                       ON CONFLICT(group_id,feature_key) DO UPDATE SET
                           configured_enabled=1,updated_at=excluded.updated_at""",
                    (group_id, spec.key, now),
                )
            if previous_domain_id is not None:
                self._rotate_link(connection, previous_domain_id, now)
            self._rotate_link(connection, int(domain_id), now)

    def remove_group_from_cluster(self, group_id: int) -> None:
        group_id = int(group_id)
        previous = self.domain_for_group(group_id)
        if previous is None or previous.mode != "cluster":
            raise ValueError("group is not in a cluster")
        now = utc_now()
        with self.database.connect() as connection:
            solo_id = self._ensure_domain(
                connection, f"solo:{group_id}", "solo", str(group_id), "", now
            )
            connection.execute(
                "UPDATE managed_groups SET domain_id=?,updated_at=? WHERE group_id=?",
                (solo_id, now, group_id),
            )
            self._rotate_link(connection, previous.domain_id, now)
            self._rotate_link(connection, solo_id, now)

    def dissolve_cluster(self, domain_id: int) -> None:
        domain_id = int(domain_id)
        groups = self.domain_groups(domain_id)
        now = utc_now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT domain_key,mode FROM group_domains WHERE domain_id=?",
                (domain_id,),
            ).fetchone()
            if row is None or str(row["mode"]) != "cluster":
                raise ValueError("cluster not found")
            if str(row["domain_key"]) == A_COAST_DOMAIN_KEY:
                raise ValueError("A海岸 cluster is fixed")
            for group_id in groups:
                solo_id = self._ensure_domain(
                    connection, f"solo:{group_id}", "solo", str(group_id), "", now
                )
                connection.execute(
                    "UPDATE managed_groups SET domain_id=?,updated_at=? WHERE group_id=?",
                    (solo_id, now, group_id),
                )
                self._rotate_link(connection, solo_id, now)
            connection.execute(
                """UPDATE group_domains
                   SET enabled=0,public_token=?,updated_at=? WHERE domain_id=?""",
                (self._new_token(), now, domain_id),
            )


__all__ = [
    "A_COAST_ALIASES",
    "A_COAST_DOMAIN_KEY",
    "FEATURE_SPECS",
    "FEATURES",
    "FeatureSpec",
    "GroupDomain",
    "GroupDomainService",
]
