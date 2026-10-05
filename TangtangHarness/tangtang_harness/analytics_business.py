"""Read-only business aggregates from Harness-owned data, without business execution."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psutil


LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


def _epoch(value):
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=LOCAL_ZONE).timestamp() if parsed.tzinfo is None else parsed.timestamp()


def _scope(filters):
    result = dict(filters or {})
    session = result.get("session_key")
    if session and session.startswith("group:"):
        if result.get("group_id") in (None, ""):
            result["group_id"] = int(session.split(":", 1)[1])
    elif session and session.startswith("private:"):
        if result.get("user_id") in (None, ""):
            result["user_id"] = int(session.split(":", 1)[1])
        if result.get("group_id") in (None, ""):
            result["group_id"] = 0
    for key in ("group_id", "user_id"):
        if result.get(key) in (None, ""):
            result.pop(key, None)
        elif key in result:
            result[key] = int(result[key])
    result["after"], result["before"] = _epoch(result.get("after")), _epoch(result.get("before"))
    result["limit"] = max(1, min(200, int(result.get("limit") or 50)))
    result["offset"] = max(0, int(result.get("offset") or 0))
    return result


def _where(filters, *, time_col=None, group_col=None, user_col=None, session_col=None, kind="iso"):
    clauses, values = [], []
    if group_col and "group_id" in filters:
        clauses.append(f"{group_col}=?")
        values.append(filters["group_id"])
    if user_col and "user_id" in filters:
        clauses.append(f"{user_col}=?")
        values.append(filters["user_id"])
    if session_col:
        session = filters.get("session_key")
        if not session and "group_id" in filters:
            session = f"group:{filters['group_id']}"
        if session:
            clauses.append(f"{session_col}=?")
            values.append(session)
    if time_col:
        for key, operator in (("after", ">="), ("before", "<")):
            stamp = filters.get(key)
            if stamp is None:
                continue
            if kind == "epoch":
                clauses.append(f"{time_col}{operator}?")
                values.append(stamp)
            elif kind == "day":
                # Daily counters have no intraday timestamps. Include days intersecting the window.
                day = datetime.fromtimestamp(stamp, LOCAL_ZONE)
                if key == "before" and day.time().isoformat() != "00:00:00":
                    operator = "<="
                clauses.append(f"{time_col}{operator}?")
                values.append(day.date().isoformat())
            else:
                clauses.append(f"julianday({time_col}){operator}julianday(?)")
                values.append(datetime.fromtimestamp(stamp, timezone.utc).isoformat())
    return " AND ".join(clauses) or "1", values


class _Reader:
    def __init__(self, conn):
        self.conn = conn
        self.tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} if conn else set()

    def rows(self, table, select="*", where="1", values=(), tail=""):
        if table not in self.tables:
            return []
        return [dict(row) for row in self.conn.execute(f"SELECT {select} FROM {table} WHERE {where} {tail}", values)]

    def count(self, table, where="1", values=()):
        rows = self.rows(table, "COUNT(*) AS count", where, values)
        return rows[0]["count"] if rows else None


class BusinessAnalytics:
    def __init__(self, rt):
        self.rt = rt
        self.root = Path(rt.config.root)
        self._process = psutil.Process()
        self._cpu_started = False
        self._capacity = None
        self._capacity_at = 0.0
        self._speech_key = None
        self._speech_result = None
        self._speech_at = 0.0

    @contextmanager
    def _read(self, relative):
        path = self.root / relative
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) if path.exists() else None
        if conn:
            conn.row_factory = sqlite3.Row
        try:
            yield _Reader(conn)
        finally:
            if conn:
                conn.close()

    @staticmethod
    def _group_names(db):
        return {row['group_id']: row['group_name'] for row in db.rows('managed_groups', 'group_id,group_name')}

    @staticmethod
    def _named_groups(records, names):
        for row in records:
            if row.get('group_id') is not None:
                row['group_name'] = names.get(row['group_id']) or '未记录群名'
        return records

    def overview(self, filters=None):
        f = _scope(filters)
        with self._read("runtime/business.db") as db:
            group_names = self._group_names(db)
            group_where, group_args = _where(f, group_col="g.group_id")
            groups = []
            # Only public metadata is selected; configured credentials and operation drafts never enter this result.
            if "managed_groups" in db.tables:
                groups = [dict(row) for row in db.conn.execute(
                    "SELECT g.group_id,g.group_name,g.alias,g.domain_id,g.enabled,g.stats_enabled,g.stats_role,"
                    "g.last_member_sync_at,g.updated_at FROM managed_groups g WHERE " + group_where + " ORDER BY g.group_id", group_args)]
            feature_rows = db.rows("group_features", "group_id,feature_key,configured_enabled,updated_at")
            members = {r["group_id"]: r for r in db.rows("group_members", "group_id,COUNT(*) AS count,SUM(active) AS active,MAX(last_seen_at) AS last_seen_at", tail="GROUP BY group_id")}
            features = {}
            for row in feature_rows:
                features.setdefault(row["group_id"], {})[row["feature_key"]] = bool(row["configured_enabled"])
            for row in groups:
                row["features"] = features.get(row["group_id"], {})
                row["members"] = members.get(row["group_id"], {"count": 0, "active": 0, "last_seen_at": None})
            domains = db.rows("group_domains", "domain_id,domain_key,mode,name,alias,enabled")
            groups_available = "managed_groups" in db.tables
            games = self._games(db, f)
            relationships = self._relationships(db, f)
            for records in (games['recent'], games['active'], relationships['recent'], relationships['relation_states']):
                self._named_groups(records, group_names)
            pending_where, pending_args = _where(f, group_col="group_id")
            pending = db.rows("harness_pending_outputs", "delivery_key,group_id,next_attempt,attempts", pending_where,
                              [*pending_args, f["limit"], f["offset"]], "ORDER BY next_attempt LIMIT ? OFFSET ?")
            self._named_groups(pending, group_names)
            subscriptions = self._subscriptions(db)
            deliveries = {"pending": pending, "pending_count": db.count("harness_pending_outputs", pending_where, pending_args)}
        with self._read("runtime/knowledge.db") as db:
            knowledge = {"count": db.count("knowledge_entries"),
                "by_domain": db.rows("knowledge_entries", "domain,COUNT(*) AS count", tail="GROUP BY domain"),
                "by_status": db.rows("knowledge_entries", "status,COUNT(*) AS count", tail="GROUP BY status"),
                "recent": db.rows("knowledge_entries", "entry_id,domain,category,title,status,updated_at", values=(f["limit"], f["offset"]), tail="ORDER BY updated_at DESC LIMIT ? OFFSET ?"),
                "scope": "current_global_inventory"}
        with self._read("runtime/gallery.db") as db:
            where, args = _where(f, time_col="drawn_at", group_col="group_id", user_col="user_id", kind="epoch")
            gallery = {"count": db.count("denia_gallery_draws", where, args),
                "counts": db.rows("denia_gallery_draws", "image_id,COUNT(*) AS count", where, args, "GROUP BY image_id ORDER BY count DESC"),
                "recent": db.rows("denia_gallery_draws", "id,image_id,group_id,user_id,drawn_at", where, [*args, f["limit"], f["offset"]], "ORDER BY drawn_at DESC LIMIT ? OFFSET ?")}
            self._named_groups(gallery['recent'], group_names)
        with self._read("data/harness.db") as db:
            where, args = _where(f, time_col="d.created_at", session_col="d.session_key", user_col="e.user_id", kind="epoch")
            deliveries["counts"] = [dict(row) for row in db.conn.execute(
                "SELECT d.outcome,COUNT(*) AS count FROM deliveries d LEFT JOIN events e ON e.event_key=d.event_key WHERE "
                + where + " GROUP BY d.outcome", args)] if {"deliveries", "events"} <= db.tables else []
            deliveries["series"] = [dict(row) for row in db.conn.execute(
                "SELECT date(d.created_at,'unixepoch','+8 hours') AS date,d.outcome,COUNT(*) AS count FROM deliveries d "
                "LEFT JOIN events e ON e.event_key=d.event_key WHERE " + where + " GROUP BY date,d.outcome ORDER BY date", args)] if {"deliveries", "events"} <= db.tables else []
            imports = db.rows("imports", "origin,source_hash,created_at")
        return {"groups": {"items": groups, "count": len(groups) if groups_available else None, "domains": domains, "scope": "current_configuration"}, "games": games,
                "relationships": relationships, "knowledge": knowledge, "gallery": gallery,
                "subscriptions": subscriptions, "deliveries": deliveries, "imports": imports,
                "notes": ["群和订阅展示当前配置；成员是最后采集值，不代表实时在线。", "缘分与小游戏保留原业务状态，不以模型推断补齐。",
                          "已完成业务投递队列会删除；送达历史以 Harness deliveries 为准。", "知识库存为全局当前状态，不按消息会话或日期裁切。"]}

    def _games(self, db, f):
        where, args = _where(f, time_col="started_at", group_col="group_id", user_col="creator_id")
        if "user_id" in f and "mini_game_participants" in db.tables:
            where, args = _where(f, time_col="started_at", group_col="group_id")
            where += " AND (creator_id=? OR session_id IN (SELECT session_id FROM mini_game_participants WHERE user_id=?))"
            args.extend([f["user_id"], f["user_id"]])
        columns = "session_id,group_id,game_type,status,creator_id,started_at,ends_at,ended_at"
        kind = "CASE WHEN game_type='bomb' AND json_extract(state_json,'$.bomb_idiom_mode')=1 THEN 'idiom_bomb' ELSE game_type END"
        return {"count": db.count("mini_game_sessions", where, args),
                "by_kind": db.rows("mini_game_sessions", f"{kind} AS kind,status,COUNT(*) AS count", where, args, "GROUP BY kind,status"),
                "series": db.rows("mini_game_sessions", f"date(started_at,'+8 hours') AS date,{kind} AS kind,COUNT(*) AS count", where, args, "GROUP BY date,kind ORDER BY date"),
                "active": db.rows("mini_game_sessions", columns, where + " AND status='active'", args, "ORDER BY started_at DESC"),
                "recent": db.rows("mini_game_sessions", columns, where, [*args, f["limit"], f["offset"]], "ORDER BY started_at DESC LIMIT ? OFFSET ?")}

    def _relationships(self, db, f):
        where, args = _where(f, time_col="drawn_at", group_col="group_id")
        if "user_id" in f:
            where += " AND (actor_id=? OR target_id=?)"
            args.extend([f["user_id"], f["user_id"]])
        state_where, state_args = _where(f, time_col="r.drawn_at", group_col="r.group_id")
        if "user_id" in f:
            state_where += " AND (r.actor_id=? OR r.target_id=?)"
            state_args.extend([f["user_id"], f["user_id"]])
        state_where = ("EXISTS (SELECT 1 FROM today_wife_records r WHERE r.group_id=today_wife_relation_states.group_id "
                       "AND r.day=today_wife_relation_states.day AND r.actor_id=today_wife_relation_states.actor_id "
                       "AND r.draw_index=today_wife_relation_states.draw_index AND " + state_where + ")")
        relation_states = db.rows("today_wife_relation_states", "group_id,day,actor_id,draw_index,affection,minimum_affection,frozen_affection,mood,interaction_count,response_count,third_party_impacts,reunion_progress,updated_at",
                                 state_where, [*state_args, f["limit"], f["offset"]], "ORDER BY updated_at DESC LIMIT ? OFFSET ?") if "today_wife_records" in db.tables else []
        by_mood = db.rows("today_wife_relation_states", "mood,COUNT(*) AS count,AVG(affection) AS average_affection,SUM(interaction_count) AS interactions", state_where, state_args,
                          "GROUP BY mood ORDER BY count DESC") if "today_wife_records" in db.tables else []
        return {"count": db.count("today_wife_records", where, args),
                "series": db.rows("today_wife_records", "day AS date,COUNT(*) AS count", where, args, "GROUP BY day ORDER BY day"),
                "states": db.rows("today_wife_records", "status,branch,COUNT(*) AS count", where, args, "GROUP BY status,branch"),
                "relation_states": relation_states, "by_mood": by_mood,
                "recent": db.rows("today_wife_records", "group_id,day,actor_id,target_id,draw_index,branch,draw_source,status,drawn_at,divorced_at", where, [*args, f["limit"], f["offset"]], "ORDER BY drawn_at DESC LIMIT ? OFFSET ?"),
                "interactions": self._interaction_counts(db, f),
                "retention": {"group_detail_days": 7, "personal_history": "retained", "note": "群故事细节仅保留最近七天；抽取记录与个人历史另存。"}}

    def _interaction_counts(self, db, f):
        where, args = _where(f, time_col="created_at", group_col="group_id", user_col="actor_id")
        return db.rows("today_wife_interaction_events", "kind,COUNT(*) AS count", where, args, "GROUP BY kind ORDER BY count DESC")

    def _subscriptions(self, db):
        keys = ("bili_enabled", "bili_target_uids", "bili_comment_target_uids", "bili_poll_interval_seconds")
        with self._read("data/harness.db") as harness:
            configured = {r["key"]: json.loads(r["value"]) for r in harness.rows("settings", "key,value",
                "key IN ('bili_enabled','bili_target_uids','bili_comment_target_uids','bili_poll_interval_seconds')")}
        configuration = {"enabled": configured.get("bili_enabled"),
            "target_count": len(configured["bili_target_uids"]) if "bili_target_uids" in configured else None,
            "comment_target_count": len(configured["bili_comment_target_uids"]) if "bili_comment_target_uids" in configured else None,
            "poll_interval_seconds": configured.get("bili_poll_interval_seconds"),
            "missing": [key for key in keys if key not in configured]}
        states = {r["state_key"]: json.loads(r["state_value"]) for r in db.rows("asoul_plugin_state", "state_key,state_value", "state_key IN ('calendar_cache','bilibili_monitor','schedule_highlights')")}
        monitor = states.get("bilibili_monitor", {})
        targets = []
        for uid, entry in monitor.items():
            if not isinstance(entry, dict):
                continue
            live = entry.get("live_session", {})
            samples = live.get("online_samples", [])
            targets.append({"uid": uid, "live": entry.get("live"), "dynamic_initialized": entry.get("dynamic_initialized", False),
                            "known_dynamics": len(entry.get("dynamic_ids", [])), "comment_initialized_at": entry.get("comment_initialized_at"),
                            "live_session": {key: live.get(key) for key in ("observed_started_at", "source_live_time", "last_seen_at")},
                            "online_samples": [{"at": row[0], "value": row[1]} for row in samples]})
        guard = {r["setting_key"]: r["setting_value"] for r in db.rows("passive_settings", "setting_key,setting_value",
                 "setting_key IN ('zhijiang_live_schedule_last_refresh','zhijiang_live_schedule_last_error','zhijiang_live_game_paused_until')")}
        calendar = states.get("calendar_cache", {})
        return {"configuration": configuration, "calendar": {"fetched_at": calendar.get("fetched_at")},
                "monitor": {"initialized": monitor.get("initialized", False), "cursor_count": len(targets), "targets": targets},
                "live_guard": {"last_refresh": guard.get("zhijiang_live_schedule_last_refresh"),
                    "last_error": guard.get("zhijiang_live_schedule_last_error"), "paused_until": guard.get("zhijiang_live_game_paused_until")},
                "note": "已存游标是保留的监控状态，不代表当前配置订阅；直播采样仅当前场次最近720次，已结束场次没有完整采样归档。"}

    def speech_stats(self, filters=None):
        f = _scope(filters)
        key = json.dumps(f, sort_keys=True)
        now = time.time()
        if key == self._speech_key and now - self._speech_at < 30:
            return self._speech_result
        with self._read("runtime/business.db") as db:
            counters_available = "daily_counts" in db.tables
            where, args = _where(f, time_col="day", group_col="group_id", user_col="user_id", kind="day")
            daily = db.rows("daily_counts", "day AS date,SUM(message_count) AS count,COUNT(DISTINCT user_id) AS users,COUNT(DISTINCT group_id) AS groups", where, args, "GROUP BY day ORDER BY day")
            by_group = db.rows("daily_counts", "group_id,SUM(message_count) AS count,COUNT(DISTINCT user_id) AS users", where, args, "GROUP BY group_id ORDER BY count DESC")
            self._named_groups(by_group, self._group_names(db))
            top_users = db.rows("daily_counts", "user_id,MAX(nickname) AS nickname,SUM(message_count) AS count", where,
                                [*args, f["limit"], f["offset"]], "GROUP BY user_id ORDER BY count DESC,user_id LIMIT ? OFFSET ?")
        with self._read("runtime/business-history.db") as db:
            archive_available = "tangtang_group_messages" in db.tables
            where, args = _where(f, time_col="created_at", group_col="group_id", user_col="user_id")
            bins = [dict(row) for row in db.conn.execute("WITH bins AS (SELECT strftime('%w:%H',created_at,'+8 hours') AS slot,COUNT(*) AS count,unixepoch(MIN(julianday(created_at))) AS first_at,unixepoch(MAX(julianday(created_at))) AS last_at FROM tangtang_group_messages WHERE " + where + " GROUP BY slot) SELECT CAST(substr(slot,1,1) AS INTEGER) AS weekday,CAST(substr(slot,3,2) AS INTEGER) AS hour,count,first_at,last_at FROM bins ORDER BY slot", args)] if archive_available else []
            hourly = [{key: row[key] for key in ('weekday', 'hour', 'count')} for row in bins]
            first_at = min((row['first_at'] for row in bins if row['first_at'] is not None), default=None)
            last_at = max((row['last_at'] for row in bins if row['last_at'] is not None), default=None)
            sample_count = sum(r["count"] for r in hourly) if archive_available else None
            dimensions = self._profile_dimensions(db, where, args) if "user_id" in f else []
        result = {"daily": daily, "hourly": hourly, "by_group": by_group, "top_users": top_users,
                "sources": {"counters_available": counters_available, "archive_available": archive_available},
                "profile_dimensions": dimensions, "sample": {"count": sample_count, "scope": "retained_group_message_archive", 'first_at': first_at, 'last_at': last_at,
                    "dimensions_computed": "user_id" in f and archive_available, "user_id": f.get("user_id"), "group_id": f.get("group_id"),
                    "calculated_at": now, "refresh_seconds": 30,
                    "timezone": "Asia/Shanghai", "weekday_zero": "Sunday", "dimension_kind": "local_text_rule_hits"},
                "notes": ["daily/by_group/top_users 来自去重计数，包含过滤入站及已确认送达的机器人发言。",
                          "hourly/profile_dimensions 来自保留正文档案，不能与发言计数相加。", "日期筛选对 daily_counts 按相交整日统计；档案按精确时间筛选。",
                          "六维是规则命中条数，允许一条发言命中多个维度，仅选择用户后重算，不是人格评分。"]}
        self._speech_key, self._speech_at, self._speech_result = key, now, result
        return result

    def _profile_dimensions(self, db, where, args):
        if "tangtang_group_messages" not in db.tables:
            return []
        db.conn.create_function("normalized_text", 1, lambda value: " ".join((value or "").split()), deterministic=True)
        db.conn.create_function("has_digit", 1, lambda value: int(any(char.isdigit() for char in value)), deterministic=True)
        base = f"WITH content AS (SELECT normalized_text(text) AS text FROM tangtang_group_messages WHERE {where}), lines AS (SELECT text FROM content WHERE text<>'') "
        checks = {"好奇雷达": ("?", "？", "吗", "怎么"), "情绪电波": tuple("!！～~哈哈呵呵呜哭笑"),
                  "接话欲": ("你", "您", "大家", "各位", "谢谢", "早安", "晚安"), "情报站": ("直播", "公告", "更新", "数据", "时间", "消息", "据说")}
        expressions, values = ["SUM(length(text)>=60) AS long_text"], list(args)
        for i, (name, words) in enumerate(checks.items()):
            expression = " OR ".join("instr(text,?)>0" for _ in words)
            if name == "情报站":
                expression += " OR has_digit(text)=1"
            expressions.append(f"SUM({expression}) AS m{i}")
            values.extend(words)
        row = db.conn.execute(base + "SELECT " + ",".join(expressions) + " FROM lines", values).fetchone()
        repeats = db.conn.execute(base + "SELECT COALESCE(SUM(n),0) FROM (SELECT COUNT(*) AS n FROM lines GROUP BY text HAVING n>1)", args).fetchone()[0]
        counts = {name: row[f"m{i}"] or 0 for i, name in enumerate(checks)}
        counts.update({"复读魂": repeats, "小作文": row["long_text"] or 0})
        return [{"name": name, "count": counts[name]} for name in ("复读魂", "好奇雷达", "情绪电波", "小作文", "接话欲", "情报站")]

    def continuation(self, filters=None):
        f = _scope(filters)
        with self._read('runtime/business.db') as business:
            group_names = self._group_names(business)
        with self._read("data/continuation.db") as db:
            where, args = _where(f, time_col="created_at", group_col="group_id", kind="epoch")
            if "user_id" in f and "continuation_attempts" in db.tables:
                # Attempt rows predate user_id; join the actual request/event identity when available.
                db.conn.execute("ATTACH DATABASE ? AS harness", ((self.root / "data/harness.db").resolve().as_uri() + "?mode=ro",))
                where += " AND request_id IN (SELECT e.event_key FROM harness.events e WHERE e.user_id=?)"
                args.append(f["user_id"])
            attempts = db.rows("continuation_attempts", "request_id,group_id,day,created_at,outcome", where, [*args, f["limit"], f["offset"]], "ORDER BY created_at DESC LIMIT ? OFFSET ?")
            counts = db.rows("continuation_attempts", "outcome,COUNT(*) AS count", where, args, "GROUP BY outcome")
            series = db.rows("continuation_attempts", "day AS date,outcome,COUNT(*) AS count", where, args, "GROUP BY day,outcome ORDER BY day")
            quota_where, quota_args = _where(f, time_col="day", group_col="group_id", kind="day")
            quotas = db.rows("continuation_group_quotas", where=quota_where, values=quota_args, tail="ORDER BY day DESC")
            self._named_groups(attempts, group_names)
            self._named_groups(quotas, group_names)
            refreshes = db.rows("continuation_quota_refreshes", where=_where(f, time_col="started_at", kind="epoch")[0], values=_where(f, time_col="started_at", kind="epoch")[1], tail="ORDER BY started_at DESC LIMIT 50")
        windows = []
        for session, value in tuple(self.rt.windows.items()):
            if f.get("session_key") and session != f["session_key"]:
                continue
            if "group_id" in f and session != f"group:{f['group_id']}":
                continue
            if "user_id" in f and value.context != f["user_id"]:
                continue
            windows.append({"session_key": session, "user_id": value.context, "opened_at": value.opened_at,
                            "delivered_at": value.delivered_at, "attempts": value.attempts, "silences": value.silences})
            if session.startswith('group:') and session.split(':')[1].isdigit():
                windows[-1]['group_id'] = int(session.split(':')[1])
        self._named_groups(windows, group_names)
        with self._read("data/harness.db") as db:
            where, args = _where(f, time_col="r.started_at", session_col="r.session_key", user_col="e.user_id", kind="epoch")
            proactive_counts = [dict(row) for row in db.conn.execute("SELECT r.outcome,COUNT(*) AS count FROM requests r LEFT JOIN events e ON e.event_key=r.event_key WHERE r.purpose='proactive' AND " + where + " GROUP BY r.outcome", args)] if {"requests", "events"} <= db.tables else []
        return {"enabled": self.rt.config.continuation_enabled, "attempts": attempts, "counts": counts, "series": series,
                "quotas": quotas, "quota_refreshes": refreshes, "windows": windows,
                "proactive": {"enabled": self.rt.config.proactive_enabled, "strategy": self.rt.config.extra.get("proactive_strategy", "active_v1"), "counts": proactive_counts},
                "notes": ["续聊尝试和结果持久保存；当前窗口只在内存，重启不恢复。", "群额度未采集时运行默认20次；空额度表不代表额度为零。", "额度是群级设置，选择用户不改变群额度。"]}

    def runtime(self):
        with self._read("data/harness.db") as db:
            incidents = db.rows("transport_incidents", "id,started_at,ended_at,reason", tail="ORDER BY started_at DESC LIMIT 100")
            incident_counts = db.rows("transport_incidents", "COUNT(*) AS count,SUM(ended_at IS NULL) AS open,SUM(CASE WHEN ended_at IS NOT NULL THEN ended_at-started_at ELSE 0 END) AS completed_seconds")
        now = time.time()
        if self._capacity is None or now - self._capacity_at > 30:
            self._capacity = self._owned_capacity()
            self._capacity_at = now
        cpu = self._process.cpu_percent(interval=None)
        resources = {"pid": os.getpid(), "rss_bytes": self._process.memory_info().rss,
                     "cpu_percent": cpu if self._cpu_started else None, "threads": self._process.num_threads(),
                     "cpu_seconds": sum(self._process.cpu_times()[:2])}
        self._cpu_started = True
        return {"at": now, "mode": self.rt.config.mode, "uptime_seconds": now - self.rt.started_at,
                "connections": {"onebot": self.rt.bot.connected, "core": self.rt.core.socket is not None},
                "active_tasks": len(self.rt.tasks), "active_windows": len(self.rt.windows), "resources": resources,
                "owned_capacity": self._capacity, "incidents": incidents, "incident_counts": incident_counts[0] if incident_counts else None,
                "history_available": False, "notes": ["资源是当前采样，没有历史性能曲线。", "目录容量每30秒刷新，仅统计新系统运行及资源目录，排除虚拟环境和 node_modules。"]}

    def _owned_capacity(self):
        result = []
        for name in ("data", "runtime", "logs", "reports", "resources", "config"):
            folder = self.root / name
            if folder.is_symlink():
                continue
            count, total = 0, 0
            for directory, children, files in os.walk(folder, followlinks=False):
                children[:] = [child for child in children if child not in {".venv", "node_modules", ".git"} and not (Path(directory) / child).is_symlink()]
                for filename in files:
                    path = Path(directory) / filename
                    if path.is_symlink():
                        continue
                    try:
                        total += path.stat().st_size
                        count += 1
                    except FileNotFoundError:
                        # Generated files can expire while the directory is being measured.
                        continue
            result.append({"directory": name, "files": count, "bytes": total})
        return {"items": result, "files": sum(row["files"] for row in result), "bytes": sum(row["bytes"] for row in result)}

    def experiments(self, filters=None):
        f = _scope(filters)
        with self._read("data/harness.db") as db:
            if "settings" not in db.tables:
                return {"items": [], "count": 0, "notes": ["尚无实验历史。"]}
            rows = db.rows("settings", """
                json_extract(value,'$.id') AS id,
                json_extract(value,'$.kind') AS kind,
                json_extract(value,'$.status') AS status,
                json_extract(value,'$.model_calls') AS model_calls,
                json_extract(value,'$.qq_writes') AS qq_writes,
                json_extract(value,'$.created_at') AS created_at,
                (SELECT json_group_array(json(
                    (SELECT json_group_object(field.key,
                        CASE WHEN field.key='usage' AND field.type='object' THEN json(
                            (SELECT json_group_object(metric.key,metric.value)
                             FROM json_each(field.value) AS metric
                             WHERE metric.key IN ('input_tokens','output_tokens','reasoning_tokens','total_tokens',
                                 'cache_read_tokens','cache_write_tokens','cache_miss_tokens','cache_status','cache_ratio',
                                 'cost','currency','cost_currency','input_semantics','output_semantics',
                                 'latency_ms','first_token_latency_ms')))
                        WHEN field.type IN ('array','object') THEN json(field.value) ELSE field.value END)
                     FROM json_each(step.value) AS field
                     WHERE field.key IN ('label','request_id','status','usage'))))
                 FROM json_each(settings.value,'$.result.steps') AS step) AS steps,
                CASE WHEN json_type(value,'$.result.diff')='object' THEN
                    (SELECT json_group_object(field.key,
                        CASE WHEN field.type IN ('array','object') THEN json(field.value) ELSE field.value END)
                     FROM json_each(settings.value,'$.result.diff') AS field
                     WHERE field.key IN ('previous_request_id','common_prefix_bytes','changed_layers','note'))
                END AS diff
                """, "key LIKE 'experiment:%'")
            paid = {r["id"]: r for r in db.rows("requests", "json_extract(telemetry,'$.experiment_id') AS id,MIN(started_at) AS created_at,COUNT(*) AS requests", "json_extract(telemetry,'$.experiment_id') IS NOT NULL", tail="GROUP BY id")}
        selected = []
        for row in rows:
            row["created_at"] = row["created_at"] or paid.get(row["id"], {}).get("created_at")
            at = row["created_at"]
            if (f["after"] is not None and (at is None or at < f["after"])) or (f["before"] is not None and (at is None or at >= f["before"])):
                continue
            row["steps"] = json.loads(row["steps"])
            row["diff"] = json.loads(row["diff"]) if row["diff"] else None
            selected.append(row)
        selected.sort(key=lambda row: row["created_at"] or 0, reverse=True)
        return {"items": selected[f["offset"]:f["offset"] + f["limit"]], "count": len(selected),
                "notes": ["只读取已执行实验，不产生模型调用。", "旧离线实验未保存时间，显示未知；设置时间筛选后不包含时间未知项。", "实验是独立合成输入，不按真实群或用户过滤。"]}
