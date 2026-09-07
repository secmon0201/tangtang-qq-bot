from __future__ import annotations

from datetime import date, datetime, timedelta
from collections.abc import Callable, Iterable, Mapping
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import settings
from bot.db import Database


class StatsService:
    def __init__(
        self,
        database: Database,
        realtime_enabled: bool = True,
        group_ids: Iterable[int] = (),
        group_provider: Callable[[], Iterable[int]] | None = None,
    ) -> None:
        self.database = database
        self.zone = ZoneInfo(settings.timezone)
        self.realtime_enabled = realtime_enabled
        self._group_order = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        self.group_ids = frozenset(self._group_order)
        self._group_provider = group_provider

    def enabled_groups(self) -> frozenset[int]:
        if self._group_provider is not None:
            return frozenset(int(group_id) for group_id in self._group_provider())
        return self.group_ids

    def ordered_groups(self) -> tuple[int, ...]:
        if self._group_provider is not None:
            return tuple(dict.fromkeys(int(group_id) for group_id in self._group_provider()))
        return self._group_order

    def local_now(self) -> datetime:
        return datetime.now(self.zone)

    async def on_message(self, bot: Any, event: Any) -> bool:
        if not self.realtime_enabled:
            return False
        group_id = int(event.group_id)
        if group_id not in self.enabled_groups():
            return False
        if not self.database.is_managed_group(group_id):
            return False
        user_id = int(event.user_id)
        message_id = str(getattr(event, "message_id", ""))
        if not message_id:
            return False
        nickname = str(getattr(event.sender, "card", "") or getattr(event.sender, "nickname", "") or user_id)
        timestamp = getattr(event, "time", None)
        message_at = datetime.fromtimestamp(int(timestamp), self.zone) if timestamp else self.local_now()
        return self.database.record_message(
            f"{group_id}:{message_id}", group_id, user_id, nickname, message_at
        )

    def record_outbound_success(
        self,
        bot: Any,
        action: str,
        params: Mapping[str, Any],
        result: Any,
        sent_at: datetime | None = None,
    ) -> bool:
        """Count one confirmed group send by the bot, using the OneBot message ID."""
        group_id = self._outbound_group_id(action, params)
        if group_id is None or group_id not in self.enabled_groups():
            return False
        if not self.database.is_managed_group(group_id):
            return False
        message_id = self._outbound_message_id(result)
        if not message_id:
            return False
        message_at = sent_at.astimezone(self.zone) if sent_at else self.local_now()
        return self.database.record_message(
            f"{group_id}:{message_id}",
            group_id,
            int(bot.self_id),
            "糖糖",
            message_at,
        )

    def import_napcat_outbound_logs(
        self,
        log_dir: Path,
        start_day: date,
    ) -> dict[str, int]:
        """Backfill self-sent managed-group messages from local NapCat event logs."""
        result = {"files": 0, "matched": 0, "imported": 0, "duplicates": 0, "invalid": 0}
        if not log_dir.is_dir():
            return result
        decoder = json.JSONDecoder()
        for path in sorted(log_dir.glob("*.log")):
            result["files"] += 1
            try:
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        record = self._napcat_self_sent_record(line, decoder)
                        if record is None:
                            continue
                        result["matched"] += 1
                        try:
                            group_id = int(record["group_id"])
                            user_id = int(record["user_id"])
                            message_id = str(record["message_id"])
                            occurred_at = datetime.fromtimestamp(int(record["time"]), self.zone)
                        except (KeyError, TypeError, ValueError, OSError):
                            result["invalid"] += 1
                            continue
                        if group_id not in self.enabled_groups() or occurred_at.date() < start_day:
                            continue
                        nickname = str((record.get("sender") or {}).get("nickname") or "糖糖")
                        if self.database.record_message(
                            f"{group_id}:{message_id}",
                            group_id,
                            user_id,
                            nickname,
                            occurred_at,
                        ):
                            result["imported"] += 1
                        else:
                            result["duplicates"] += 1
            except OSError:
                result["invalid"] += 1
        return result

    @staticmethod
    def _outbound_group_id(action: str, params: Mapping[str, Any]) -> int | None:
        if action in {"send_group_msg", "send_group_forward_msg"}:
            candidate = params.get("group_id")
        elif action == "send_msg" and (
            params.get("message_type") == "group" or "group_id" in params
        ):
            candidate = params.get("group_id")
        else:
            return None
        try:
            return int(candidate)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _outbound_message_id(result: Any) -> str:
        payload = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else result
        if not isinstance(payload, dict):
            return ""
        for key in ("message_id", "message_seq", "res_id"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _napcat_self_sent_record(line: str, decoder: json.JSONDecoder) -> dict[str, Any] | None:
        marker = '{"stringMsg":'
        start = line.find(marker)
        if start < 0:
            return None
        try:
            payload, _ = decoder.raw_decode(line[start:])
        except json.JSONDecodeError:
            return None
        record = payload.get("stringMsg") if isinstance(payload, dict) else None
        if not isinstance(record, dict):
            return None
        if (
            record.get("post_type") != "message_sent"
            or record.get("message_sent_type") != "self"
            or record.get("message_type") != "group"
        ):
            return None
        if str(record.get("user_id") or "") != str(record.get("self_id") or ""):
            return None
        return record

    def ranking_rows(self, scope: str, group_id: int | None = None) -> list[dict[str, Any]]:
        group_ids = self._ranking_group_ids(group_id)
        return self.ranking_rows_for_groups(scope, group_ids)

    def ranking_rows_for_groups(
        self, scope: str, group_ids: Iterable[int]
    ) -> list[dict[str, Any]]:
        group_ids = tuple(dict.fromkeys(int(value) for value in group_ids))
        if not group_ids:
            return []
        return self.database.message_ranking(
            group_ids,
            self.window_start(scope, self.local_now().date()),
        )

    def group_totals(self, scope: str) -> list[dict[str, Any]]:
        group_ids = self._ranking_group_ids(None)
        return self.group_totals_for_groups(scope, group_ids)

    def group_totals_for_groups(
        self, scope: str, group_ids: Iterable[int]
    ) -> list[dict[str, Any]]:
        group_ids = tuple(dict.fromkeys(int(value) for value in group_ids))
        return self.database.group_message_totals(
            group_ids, self.window_start(scope, self.local_now().date())
        )

    def recent_group_daily_totals(
        self, group_id: int, days: int = 7, today: date | None = None
    ) -> list[dict[str, Any]]:
        """Return a fixed local seven-day trend independently of ranking scope."""
        if group_id not in self.enabled_groups():
            return []
        end_day = today or self.local_now().date()
        start_day = end_day - timedelta(days=max(1, days) - 1)
        return self.database.group_daily_message_totals(group_id, start_day, end_day)

    def _ranking_group_ids(self, group_id: int | None) -> tuple[int, ...]:
        if group_id is None:
            return self.ordered_groups()
        return (group_id,) if group_id in self.enabled_groups() else ()

    @staticmethod
    def window_start(scope: str, today: date) -> date | None:
        if scope == "day":
            return today
        if scope == "week":
            return date.fromordinal(today.toordinal() - today.weekday())
        if scope == "month":
            return today.replace(day=1)
        if scope == "total":
            return None
        raise ValueError(f"unsupported ranking scope: {scope}")

    @staticmethod
    def render_rows(rows: list[Any], title: str) -> str:
        if not rows:
            return f"{title}\n暂无数据。"
        lines = [title]
        for index, row in enumerate(rows, 1):
            rank = row["rank"] if "rank" in row.keys() else index
            nickname = row["nickname"] or ""
            label = f" {nickname}" if nickname else ""
            lines.append(f"{rank}.{label}：{row['message_count']} 条")
        return "\n".join(lines)
