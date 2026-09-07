from __future__ import annotations

from math import ceil
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from bot.config import settings
from bot.db import Database
from bot.services.tangtang_db import TangtangDb


def bounded_evidence(results: list[str], maximum_chars: int) -> list[str]:
    """Truncate each evidence piece and drop empty results before merging."""
    return [text[:maximum_chars] for text in results if text]


class ACoastArchiveService:
    """A Coast profile reads now come from the unified Tangtang history store."""

    def __init__(
        self,
        database: Database,
        tangtang_db: TangtangDb | None = None,
    ) -> None:
        self.database = database
        self.tangtang_db = tangtang_db or TangtangDb()
        self.zone = ZoneInfo(settings.timezone)

    def _group_names(self) -> dict[int, str]:
        return {
            int(row["group_id"]): str(row["group_name"] or "")
            for row in self.database.managed_groups()
        }

    def _attach(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        names = self._group_names()
        profiles = self.database.user_profiles(int(row["user_id"]) for row in rows)
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["content"] = str(item.pop("text", "") or "")
            item["occurred_at"] = str(item.pop("created_at", "") or "")
            item["group_name"] = names.get(int(item["group_id"]), "")
            profile = profiles.get(int(item["user_id"]), {})
            item["nickname"] = str(item.get("nickname") or "") or str(
                profile.get("nickname") or ""
            )
            item["avatar_url"] = str(profile.get("avatar_url") or "")
            result.append(item)
        return result

    def records(
        self,
        user_id: int,
        group_ids: Iterable[int],
        keyword: str = "",
        page: int = 1,
    ) -> list[dict[str, Any]]:
        rows = self.tangtang_db.profile_messages(
            int(user_id),
            tuple(group_ids),
            keyword=keyword[:100],
            limit=100,
            offset=max(0, int(page) - 1) * 100,
        )
        return self._attach(rows)

    def total_pages(
        self,
        user_id: int,
        group_ids: Iterable[int],
        keyword: str = "",
    ) -> int:
        count = self.tangtang_db.profile_message_count(
            int(user_id), tuple(group_ids), keyword=keyword[:100]
        )
        return max(1, ceil(count / 100))

    def message_count(
        self,
        user_id: int,
        group_ids: Iterable[int],
        keyword: str = "",
    ) -> int:
        return self.tangtang_db.profile_message_count(
            int(user_id), tuple(group_ids), keyword=keyword[:100]
        )

    def summary(
        self,
        user_id: int,
        group_ids: Iterable[int],
    ) -> dict[str, Any]:
        summary = self.tangtang_db.profile_summary(int(user_id), tuple(group_ids))
        names = self._group_names()
        for row in summary["groups"]:
            row["group_name"] = names.get(int(row["group_id"]), "")
        return summary

    def all_records(
        self,
        user_id: int,
        group_ids: Iterable[int],
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.tangtang_db.profile_messages(
                int(user_id), tuple(group_ids), limit=100, offset=offset
            )
            values.extend(page)
            if len(page) < 100:
                break
            offset += len(page)
        return self._attach(values)

    def unconsumed(
        self,
        user_id: int,
        group_ids: Iterable[int],
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        rows = self.tangtang_db.unconsumed_profile_messages(
            int(user_id), tuple(group_ids), limit=limit
        )
        return self._attach(rows)

    def consume(self, user_id: int, rows: list[dict[str, Any]]) -> int:
        return self.tangtang_db.consume_profile_messages(
            int(user_id), tuple(int(row["id"]) for row in rows)
        )
