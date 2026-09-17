from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_domains (
    domain_id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_key TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK (mode IN ('solo', 'cluster')),
    name TEXT NOT NULL,
    alias TEXT NOT NULL DEFAULT '',
    public_token TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS managed_groups (
    group_id INTEGER PRIMARY KEY,
    group_name TEXT NOT NULL DEFAULT '',
    alias TEXT NOT NULL DEFAULT '',
    public_key TEXT NOT NULL DEFAULT '',
    domain_id INTEGER,
    joined_at TEXT,
    disabled_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    stats_enabled INTEGER NOT NULL DEFAULT 0,
    stats_role TEXT NOT NULL DEFAULT '',
    last_member_sync_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_features (
    group_id INTEGER NOT NULL,
    feature_key TEXT NOT NULL,
    configured_enabled INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, feature_key),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS group_features_enabled_idx
    ON group_features(feature_key, configured_enabled, group_id);

CREATE TABLE IF NOT EXISTS group_filters (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    card TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'member',
    active INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS whitelist (
    user_id INTEGER PRIMARY KEY,
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS active_filters (
    user_id INTEGER PRIMARY KEY,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS passive_filters (
    user_id INTEGER PRIMARY KEY,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS passive_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asoul_plugin_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qq_transport_connection_incidents (
    incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    last_connected_at TEXT,
    recovered_at TEXT,
    duration_seconds INTEGER,
    bot_self_id TEXT NOT NULL DEFAULT '',
    trigger TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'recovered')),
    diagnosis TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    recovery_snapshot_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS qq_transport_one_open_connection_incident
    ON qq_transport_connection_incidents(status) WHERE status='open';

CREATE INDEX IF NOT EXISTS qq_transport_connection_incidents_detected_idx
    ON qq_transport_connection_incidents(detected_at DESC);

CREATE TABLE IF NOT EXISTS passive_group_settings (
    group_id INTEGER NOT NULL,
    setting_key TEXT NOT NULL,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, setting_key)
);

CREATE TABLE IF NOT EXISTS passive_repeat_state (
    group_id INTEGER PRIMARY KEY,
    last_repeat_at REAL NOT NULL DEFAULT 0,
    messages_since_repeat INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hourly_announcement_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hourly_announcement_deliveries (
    slot_key TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    text_index INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (slot_key, group_id),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ranking_deliveries (
    day TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    delivery_type TEXT NOT NULL,
    domain_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (day, group_id, delivery_type),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS a_coast_profile_states (
    user_id INTEGER NOT NULL,
    scope_key TEXT NOT NULL,
    profile_text TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, scope_key)
);

CREATE TABLE IF NOT EXISTS daily_counts (
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    message_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, day, user_id),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS daily_top100 (
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    rank INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    finalized_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'events',
    PRIMARY KEY (group_id, day, user_id),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS member_totals (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    total_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rollup_runs (
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'events',
    row_count INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_id, day),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS recovery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS group_activity_snapshots (
    group_id INTEGER NOT NULL,
    report_day TEXT NOT NULL,
    window TEXT NOT NULL,
    active_member_count INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    collected_at TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_id, report_day, window),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS group_activity_members (
    group_id INTEGER NOT NULL,
    report_day TEXT NOT NULL,
    window TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    activity_count INTEGER NOT NULL DEFAULT 0,
    rank INTEGER NOT NULL,
    PRIMARY KEY (group_id, report_day, window, user_id),
    FOREIGN KEY (group_id, report_day, window)
        REFERENCES group_activity_snapshots(group_id, report_day, window)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_collection_runs (
    group_id INTEGER NOT NULL,
    report_day TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_id, report_day),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_dedup (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_bindings (
    user_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    game_uid TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, game)
);

CREATE TABLE IF NOT EXISTS game_cache (
    game TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (game, cache_key)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    group_id INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mini_game_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    game_type TEXT NOT NULL CHECK (game_type IN ('roulette', 'bomb', 'dice', 'guess')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended', 'cancelled')),
    creator_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    ended_at TEXT,
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS mini_game_one_active_session_per_group
    ON mini_game_sessions(group_id) WHERE status='active';

CREATE TABLE IF NOT EXISTS mini_game_participants (
    session_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    action_order INTEGER NOT NULL DEFAULT 0,
    dice_value INTEGER,
    pass_count INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (session_id, user_id),
    FOREIGN KEY (session_id) REFERENCES mini_game_sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mini_game_stats (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    roulette_deaths INTEGER NOT NULL DEFAULT 0,
    roulette_games INTEGER NOT NULL DEFAULT 0,
    bomb_deaths INTEGER NOT NULL DEFAULT 0,
    bomb_passes INTEGER NOT NULL DEFAULT 0,
    bomb_games INTEGER NOT NULL DEFAULT 0,
    dice_highs INTEGER NOT NULL DEFAULT 0,
    dice_lows INTEGER NOT NULL DEFAULT 0,
    dice_games INTEGER NOT NULL DEFAULT 0,
    guess_wins INTEGER NOT NULL DEFAULT 0,
    guess_misses INTEGER NOT NULL DEFAULT 0,
    guess_games INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS today_wife_records (
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    draw_index INTEGER NOT NULL DEFAULT 1 CHECK (draw_index IN (1, 2)),
    actor_nickname TEXT NOT NULL DEFAULT '',
    target_id INTEGER NOT NULL,
    target_nickname TEXT NOT NULL DEFAULT '',
    relationship_key TEXT NOT NULL DEFAULT '',
    story_id TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT 'ordinary' CHECK (branch IN ('ordinary', 'mutual', 'contested', 'popular')),
    draw_source TEXT NOT NULL DEFAULT 'random' CHECK (draw_source IN ('random', 'directed')),
    context_nickname TEXT NOT NULL DEFAULT '',
    story_flags TEXT NOT NULL DEFAULT '',
    taken_by_nickname TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'divorced')),
    drawn_at TEXT NOT NULL,
    divorced_at TEXT,
    PRIMARY KEY (group_id, day, actor_id, draw_index),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS today_wife_target_idx
    ON today_wife_records(group_id, day, target_id, status);

CREATE INDEX IF NOT EXISTS today_wife_actor_history_idx
    ON today_wife_records(group_id, actor_id, day DESC);

CREATE TABLE IF NOT EXISTS today_wife_activity_events (
    event_id TEXT PRIMARY KEY,
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS today_wife_activity_counts (
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, day, user_id),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS today_wife_activity_day_idx
    ON today_wife_activity_counts(group_id, day, message_count DESC);

-- The daily relationship itself stays in today_wife_records.  These tables
-- hold the expandable game layer so new story systems never require another
-- migration of the relationship history primary key.
CREATE TABLE IF NOT EXISTS today_wife_global_themes (
    day TEXT PRIMARY KEY,
    theme_id TEXT NOT NULL,
    selected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS today_wife_day_states (
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    theme_id TEXT NOT NULL,
    script_id TEXT NOT NULL,
    act INTEGER NOT NULL DEFAULT 1 CHECK (act BETWEEN 1 AND 3),
    route_key TEXT NOT NULL DEFAULT 'opening',
    interaction_count INTEGER NOT NULL DEFAULT 0,
    last_interactor_id INTEGER,
    last_event_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'locked', 'published')),
    state_json TEXT NOT NULL DEFAULT '{}',
    conclusion_json TEXT NOT NULL DEFAULT '{}',
    conclusion_built_at TEXT,
    delivered_at TEXT,
    delivery_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, day),
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS today_wife_day_state_delivery_idx
    ON today_wife_day_states(day, status, delivered_at);

CREATE TABLE IF NOT EXISTS today_wife_relation_states (
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    draw_index INTEGER NOT NULL,
    affection INTEGER NOT NULL DEFAULT 0,
    minimum_affection INTEGER NOT NULL DEFAULT 0,
    frozen_affection INTEGER,
    mood TEXT NOT NULL DEFAULT 'calm',
    marks_json TEXT NOT NULL DEFAULT '[]',
    pending_json TEXT NOT NULL DEFAULT '{}',
    narrative_json TEXT NOT NULL DEFAULT '{}',
    interaction_count INTEGER NOT NULL DEFAULT 0,
    response_count INTEGER NOT NULL DEFAULT 0,
    third_party_impacts INTEGER NOT NULL DEFAULT 0,
    reunion_progress INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, day, actor_id, draw_index),
    FOREIGN KEY (group_id, day, actor_id, draw_index)
        REFERENCES today_wife_records(group_id, day, actor_id, draw_index) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS today_wife_relation_state_target_idx
    ON today_wife_relation_states(group_id, day, actor_id, draw_index);

CREATE TABLE IF NOT EXISTS today_wife_interaction_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    actor_nickname TEXT NOT NULL DEFAULT '',
    mentioned_id INTEGER,
    mentioned_nickname TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    role TEXT NOT NULL,
    mechanism TEXT NOT NULL,
    act INTEGER NOT NULL,
    title TEXT NOT NULL,
    narrative TEXT NOT NULL,
    effects_json TEXT NOT NULL DEFAULT '[]',
    marks_json TEXT NOT NULL DEFAULT '[]',
    narrative_json TEXT NOT NULL DEFAULT '{}',
    intent TEXT NOT NULL DEFAULT 'auto',
    source_message_id TEXT NOT NULL DEFAULT '',
    key_event INTEGER NOT NULL DEFAULT 0 CHECK (key_event IN (0, 1)),
    created_at TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS today_wife_interaction_event_day_idx
    ON today_wife_interaction_events(group_id, day, event_id);

CREATE TABLE IF NOT EXISTS today_wife_collective_rounds (
    group_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    round_no INTEGER NOT NULL CHECK (round_no BETWEEN 1 AND 3),
    payload_json TEXT NOT NULL DEFAULT '{}',
    prepared_at TEXT NOT NULL,
    delivered_at TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    delivery_error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_id, day, round_no),
    FOREIGN KEY (group_id, day)
        REFERENCES today_wife_day_states(group_id, day) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS today_wife_collective_round_delivery_idx
    ON today_wife_collective_rounds(day, round_no, delivered_at);

"""




def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_qq_transport_incidents(connection)
            legacy_filter = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reaction_filters'"
            ).fetchone()
            if legacy_filter is not None:
                # The former table only controlled passive reactions. Preserve all
                # existing entries while moving to the explicit passive list.
                connection.execute(
                    """INSERT OR IGNORE INTO passive_filters(user_id,created_by,created_at)
                       SELECT user_id,created_by,created_at FROM reaction_filters"""
                )
                connection.execute("DROP TABLE reaction_filters")
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(group_members)")
            }
            if "avatar_url" not in columns:
                connection.execute(
                    "ALTER TABLE group_members ADD COLUMN avatar_url TEXT NOT NULL DEFAULT ''"
                )
            hourly_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(hourly_announcement_deliveries)")
            }
            if "message" not in hourly_columns:
                connection.execute(
                    "ALTER TABLE hourly_announcement_deliveries ADD COLUMN message TEXT NOT NULL DEFAULT ''"
                )
            self._migrate_today_wife_records(connection)
            self._migrate_today_wife_game_tables(connection)
            self._migrate_mini_game_tables(connection)
            self._migrate_group_domain_columns(connection)
            self._initialize_chat_gate_revisions(connection)
            self._drop_retired_codex_task_tables(connection)

    @staticmethod
    def _drop_retired_codex_task_tables(connection: sqlite3.Connection) -> None:
        """Remove the retired QQ-to-local-CLI task queue without touching notifications."""

        connection.execute("DROP TABLE IF EXISTS codex_task_messages")
        connection.execute("DROP TABLE IF EXISTS codex_tasks")
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES (2,?)",
            (utc_now(),),
        )

    @staticmethod
    def _migrate_qq_transport_incidents(connection: sqlite3.Connection) -> None:
        """Preserve outage history recorded before transport-neutral naming."""

        legacy = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='napcat_connection_incidents'"
        ).fetchone()
        if legacy is None:
            return
        connection.execute(
            """INSERT OR IGNORE INTO qq_transport_connection_incidents
               (incident_id,detected_at,last_connected_at,recovered_at,duration_seconds,
                bot_self_id,trigger,status,diagnosis,snapshot_json,recovery_snapshot_json)
               SELECT incident_id,detected_at,last_connected_at,recovered_at,duration_seconds,
                      bot_self_id,trigger,status,diagnosis,snapshot_json,recovery_snapshot_json
               FROM napcat_connection_incidents"""
        )
        connection.execute("DROP TABLE napcat_connection_incidents")
    @staticmethod
    def _migrate_group_domain_columns(connection: sqlite3.Connection) -> None:
        """Apply the additive v1 group-domain schema in one SQLite transaction."""

        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(managed_groups)")
        }
        additions = {
            "group_name": "TEXT NOT NULL DEFAULT ''",
            "alias": "TEXT NOT NULL DEFAULT ''",
            "public_key": "TEXT NOT NULL DEFAULT ''",
            "domain_id": "INTEGER",
            "joined_at": "TEXT",
            "disabled_at": "TEXT",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "stats_enabled": "INTEGER NOT NULL DEFAULT 0",
            "stats_role": "TEXT NOT NULL DEFAULT ''",
            "last_member_sync_at": "TEXT",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE managed_groups ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS managed_groups_domain_idx "
            "ON managed_groups(domain_id, enabled)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES (1,?)",
            (utc_now(),),
        )

    @staticmethod
    def _migrate_today_wife_records(connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(today_wife_records)")}
        if "draw_index" in columns:
            if "draw_source" not in columns:
                connection.execute(
                    "ALTER TABLE today_wife_records ADD COLUMN draw_source TEXT NOT NULL DEFAULT 'random'"
                )
            if "story_flags" not in columns:
                connection.execute(
                    "ALTER TABLE today_wife_records ADD COLUMN story_flags TEXT NOT NULL DEFAULT ''"
                )
            if "taken_by_nickname" not in columns:
                connection.execute(
                    "ALTER TABLE today_wife_records ADD COLUMN taken_by_nickname TEXT NOT NULL DEFAULT ''"
                )
            index = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='today_wife_actor_history_idx'"
            ).fetchone()
            if index is not None and "draw_index" not in str(index["sql"] or ""):
                connection.execute("DROP INDEX today_wife_actor_history_idx")
                connection.execute(
                    """CREATE INDEX today_wife_actor_history_idx
                       ON today_wife_records(group_id, actor_id, day DESC, draw_index DESC)"""
                )
            return

        # A primary key cannot be changed in place. Preserve every existing
        # relationship as the first daily draw before enabling one redraw.
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.executescript(
                """
                DROP INDEX IF EXISTS today_wife_target_idx;
                DROP INDEX IF EXISTS today_wife_actor_history_idx;
                CREATE TABLE today_wife_records_new (
                    group_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    actor_id INTEGER NOT NULL,
                    draw_index INTEGER NOT NULL DEFAULT 1 CHECK (draw_index IN (1, 2)),
                    actor_nickname TEXT NOT NULL DEFAULT '',
                    target_id INTEGER NOT NULL,
                    target_nickname TEXT NOT NULL DEFAULT '',
                    relationship_key TEXT NOT NULL DEFAULT '',
                    story_id TEXT NOT NULL DEFAULT '',
                    branch TEXT NOT NULL DEFAULT 'ordinary'
                        CHECK (branch IN ('ordinary', 'mutual', 'contested', 'popular')),
                    draw_source TEXT NOT NULL DEFAULT 'random'
                        CHECK (draw_source IN ('random', 'directed')),
                    context_nickname TEXT NOT NULL DEFAULT '',
                    story_flags TEXT NOT NULL DEFAULT '',
                    taken_by_nickname TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'divorced')),
                    drawn_at TEXT NOT NULL,
                    divorced_at TEXT,
                    PRIMARY KEY (group_id, day, actor_id, draw_index),
                    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
                );
                INSERT INTO today_wife_records_new
                    (group_id,day,actor_id,draw_index,actor_nickname,target_id,target_nickname,
                     relationship_key,story_id,branch,draw_source,context_nickname,story_flags,taken_by_nickname,status,drawn_at,divorced_at)
                SELECT
                    group_id,day,actor_id,1,actor_nickname,target_id,target_nickname,
                    relationship_key,story_id,branch,'random',context_nickname,'','',status,drawn_at,divorced_at
                FROM today_wife_records;
                DROP TABLE today_wife_records;
                ALTER TABLE today_wife_records_new RENAME TO today_wife_records;
                CREATE INDEX today_wife_target_idx
                    ON today_wife_records(group_id, day, target_id, status);
                CREATE INDEX today_wife_actor_history_idx
                    ON today_wife_records(group_id, actor_id, day DESC, draw_index DESC);
                """
            )
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _migrate_today_wife_game_tables(connection: sqlite3.Connection) -> None:
        """Add story-plan and idempotency fields to databases created before them."""

        relation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(today_wife_relation_states)")
        }
        if "narrative_json" not in relation_columns:
            connection.execute(
                "ALTER TABLE today_wife_relation_states ADD COLUMN narrative_json TEXT NOT NULL DEFAULT '{}'"
            )
        event_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(today_wife_interaction_events)")
        }
        if "narrative_json" not in event_columns:
            connection.execute(
                "ALTER TABLE today_wife_interaction_events ADD COLUMN narrative_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "intent" not in event_columns:
            connection.execute(
                "ALTER TABLE today_wife_interaction_events ADD COLUMN intent TEXT NOT NULL DEFAULT 'auto'"
            )
        if "source_message_id" not in event_columns:
            connection.execute(
                "ALTER TABLE today_wife_interaction_events ADD COLUMN source_message_id TEXT NOT NULL DEFAULT ''"
            )
        duplicate_sources = connection.execute(
            """SELECT group_id,source_message_id
               FROM today_wife_interaction_events
               WHERE source_message_id <> ''
               GROUP BY group_id,source_message_id
               HAVING COUNT(*) > 1"""
        ).fetchall()
        for duplicate in duplicate_sources:
            first = connection.execute(
                """SELECT event_id FROM today_wife_interaction_events
                   WHERE group_id=? AND source_message_id=?
                   ORDER BY event_id LIMIT 1""",
                (int(duplicate["group_id"]), str(duplicate["source_message_id"])),
            ).fetchone()
            if first is not None:
                # Preserve every historical event.  The earliest event keeps
                # the retry key; later pre-index duplicates become legacy rows.
                connection.execute(
                    """UPDATE today_wife_interaction_events SET source_message_id=''
                       WHERE group_id=? AND source_message_id=? AND event_id<>?""",
                    (int(duplicate["group_id"]), str(duplicate["source_message_id"]), int(first["event_id"])),
                )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS today_wife_interaction_source_message_idx
               ON today_wife_interaction_events(group_id, source_message_id)
               WHERE source_message_id <> ''"""
        )

    @staticmethod
    def _migrate_mini_game_tables(connection: sqlite3.Connection) -> None:
        session_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='mini_game_sessions'"
        ).fetchone()
        if session_schema is not None and "'guess'" not in str(session_schema["sql"] or ""):
            # SQLite cannot alter a CHECK constraint in place. Rebuild only the
            # parent table while foreign keys are temporarily disabled; child
            # rows continue to reference the same final table name.
            connection.execute("PRAGMA foreign_keys=OFF")
            try:
                connection.executescript(
                    """
                    DROP INDEX IF EXISTS mini_game_one_active_session_per_group;
                    CREATE TABLE mini_game_sessions_new (
                        session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_id INTEGER NOT NULL,
                        game_type TEXT NOT NULL CHECK (game_type IN ('roulette', 'bomb', 'dice', 'guess')),
                        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended', 'cancelled')),
                        creator_id INTEGER NOT NULL,
                        started_at TEXT NOT NULL,
                        ends_at TEXT NOT NULL,
                        state_json TEXT NOT NULL DEFAULT '{}',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        ended_at TEXT,
                        FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
                    );
                    INSERT INTO mini_game_sessions_new
                        (session_id,group_id,game_type,status,creator_id,started_at,ends_at,state_json,result_json,ended_at)
                    SELECT
                        session_id,group_id,game_type,status,creator_id,started_at,ends_at,state_json,result_json,ended_at
                    FROM mini_game_sessions;
                    DROP TABLE mini_game_sessions;
                    ALTER TABLE mini_game_sessions_new RENAME TO mini_game_sessions;
                    CREATE UNIQUE INDEX mini_game_one_active_session_per_group
                        ON mini_game_sessions(group_id) WHERE status='active';
                    """
                )
            finally:
                connection.execute("PRAGMA foreign_keys=ON")

        stat_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(mini_game_stats)")
        }
        for column in ("guess_wins", "guess_misses", "guess_games"):
            if column not in stat_columns:
                connection.execute(
                    f"ALTER TABLE mini_game_stats ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                )

    def seed_groups(self, group_ids: Iterable[int]) -> None:
        """Import configured groups without disabling groups already known to SQLite."""

        now = utc_now()
        ids = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        with self.connect() as connection:
            for group_id in ids:
                connection.execute(
                    """INSERT INTO managed_groups(group_id, enabled, updated_at)
                       VALUES (?, 1, ?)
                       ON CONFLICT(group_id) DO NOTHING""",
                    (group_id, now),
                )

    def configure_groups(self, group_ids: Iterable[int]) -> None:
        """Backward-compatible alias for the former destructive startup seeding."""

        self.seed_groups(group_ids)

    def ensure_group(
        self,
        group_id: int,
        *,
        group_name: str = "",
        joined_at: str | None = None,
    ) -> bool:
        """Register or reactivate one group while preserving its prior settings."""

        group_id = int(group_id)
        now = utc_now()
        with self.connect() as connection:
            existed = connection.execute(
                "SELECT 1 FROM managed_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            connection.execute(
                """INSERT INTO managed_groups
                       (group_id,group_name,joined_at,enabled,disabled_at,updated_at)
                   VALUES (?,?,?,?,NULL,?)
                   ON CONFLICT(group_id) DO UPDATE SET
                       group_name=CASE WHEN excluded.group_name<>'' THEN excluded.group_name
                                       ELSE managed_groups.group_name END,
                       joined_at=COALESCE(managed_groups.joined_at,excluded.joined_at),
                       enabled=1,
                       disabled_at=NULL,
                       updated_at=excluded.updated_at""",
                (group_id, str(group_name)[:80], joined_at, 1, now),
            )
        return existed is None

    def disable_group(self, group_id: int) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """UPDATE managed_groups
                   SET enabled=0,disabled_at=?,updated_at=? WHERE group_id=?""",
                (now, now, int(group_id)),
            )

    def all_managed_groups(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute("SELECT * FROM managed_groups ORDER BY group_id"))

    def managed_group(
        self, group_id: int, *, include_disabled: bool = False
    ) -> sqlite3.Row | None:
        clause = "" if include_disabled else " AND enabled=1"
        with self.connect() as connection:
            return connection.execute(
                f"SELECT * FROM managed_groups WHERE group_id=?{clause}",
                (int(group_id),),
            ).fetchone()

    def set_group_alias(self, group_id: int, alias: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE managed_groups SET alias=?,updated_at=? WHERE group_id=?",
                (str(alias)[:40], utc_now(), int(group_id)),
            )

    def set_group_joined_at(
        self, group_id: int, joined_at: str, *, overwrite: bool = False
    ) -> None:
        assignment = "joined_at=?" if overwrite else "joined_at=COALESCE(joined_at,?)"
        with self.connect() as connection:
            connection.execute(
                f"UPDATE managed_groups SET {assignment},updated_at=? WHERE group_id=?",
                (str(joined_at), utc_now(), int(group_id)),
            )

    @staticmethod
    def _initialize_chat_gate_revisions(connection: sqlite3.Connection) -> None:
        """Remember gate transitions even when a request never observes the off state."""
        connection.execute("""CREATE TABLE IF NOT EXISTS chat_gate_revisions (
            scope INTEGER PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0)""")
        specifications = (
            ("group_features", "feature_key", "configured_enabled", "group_id",
             ("mention_chat", "proactive_chat", "persona_voice", "persona_growth",
              "persona_expressions", "persona_topics")),
            ("passive_settings", "setting_key", "setting_value", None,
             ("mention_chat_global_enabled", "proactive_chat_global_enabled", "automation_pause_active")),
        )
        # These identifiers and values are fixed project-owned schema constants.
        for table, key, value, scope, relevant in specifications:
            selected = ",".join(f"'{item}'" for item in relevant)
            for operation in ("INSERT", "UPDATE", "DELETE"):
                row = "OLD" if operation == "DELETE" else "NEW"
                where = f"{row}.{key} IN ({selected})"
                if operation == "UPDATE":
                    where += f" AND NEW.{value} IS NOT OLD.{value}"
                group = f"{row}.{scope}" if scope else "0"
                connection.execute(f"""CREATE TRIGGER IF NOT EXISTS chat_gate_{table}_{operation.lower()}
                    AFTER {operation} ON {table} WHEN {where}
                    BEGIN
                      INSERT INTO chat_gate_revisions(scope,revision) VALUES({group},1)
                      ON CONFLICT(scope) DO UPDATE SET revision=revision+1;
                    END""")
        connection.execute("""CREATE TRIGGER IF NOT EXISTS chat_gate_managed_group_enabled
            AFTER UPDATE OF enabled ON managed_groups WHEN NEW.enabled IS NOT OLD.enabled
            BEGIN
              INSERT INTO chat_gate_revisions(scope,revision) VALUES(NEW.group_id,1)
              ON CONFLICT(scope) DO UPDATE SET revision=revision+1;
            END""")

    def chat_gate_revision(self, group_id: int) -> tuple[int, int]:
        """Global plus current-group generation, unaffected by other groups' toggles."""
        with self.connect() as connection:
            values = {int(row["scope"]): int(row["revision"]) for row in connection.execute(
                "SELECT scope,revision FROM chat_gate_revisions WHERE scope IN (0,?)", (int(group_id),))}
        return values.get(0, 0), values.get(int(group_id), 0)

    def group_features(self, group_id: int) -> dict[str, bool]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT feature_key,configured_enabled FROM group_features WHERE group_id=?",
                (int(group_id),),
            )
        return {
            str(row["feature_key"]): bool(row["configured_enabled"]) for row in rows
        }

    def set_group_feature(self, group_id: int, feature_key: str, enabled: bool) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO group_features
                       (group_id,feature_key,configured_enabled,updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(group_id,feature_key) DO UPDATE SET
                       configured_enabled=excluded.configured_enabled,
                       updated_at=excluded.updated_at""",
                (int(group_id), str(feature_key), int(bool(enabled)), utc_now()),
            )

    def feature_enabled(self, group_id: int, feature_key: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT f.configured_enabled
                   FROM group_features AS f
                   JOIN managed_groups AS g ON g.group_id=f.group_id
                   WHERE f.group_id=? AND f.feature_key=? AND g.enabled=1""",
                (int(group_id), str(feature_key)),
            ).fetchone()
        return bool(row and row["configured_enabled"])

    def enabled_feature_groups(self, feature_key: str) -> frozenset[int]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT f.group_id
                   FROM group_features AS f
                   JOIN managed_groups AS g ON g.group_id=f.group_id
                   WHERE f.feature_key=? AND f.configured_enabled=1 AND g.enabled=1""",
                (str(feature_key),),
            )
        return frozenset(int(row["group_id"]) for row in rows)

    def group_filter_contains(self, group_id: int, user_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM group_filters WHERE group_id=? AND user_id=?",
                (int(group_id), int(user_id)),
            ).fetchone()
        return row is not None

    def add_group_filter(self, group_id: int, user_id: int, created_by: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO group_filters
                       (group_id,user_id,created_by,created_at) VALUES (?,?,?,?)""",
                (int(group_id), int(user_id), int(created_by), utc_now()),
            )

    def remove_group_filter(self, group_id: int, user_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM group_filters WHERE group_id=? AND user_id=?",
                (int(group_id), int(user_id)),
            )

    def group_filter_members(self, group_id: int) -> tuple[int, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT user_id FROM group_filters WHERE group_id=? ORDER BY user_id",
                (int(group_id),),
            )
        return tuple(int(row["user_id"]) for row in rows)

    def restrict_message_statistics(self, group_ids: Iterable[int]) -> None:
        """Remove legacy message aggregates outside the fixed statistics scope."""
        selected = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        if not selected:
            raise ValueError("message statistics scope cannot be empty")
        placeholders = ",".join("?" for _ in selected)
        with self.connect() as connection:
            for table in ("daily_counts", "daily_top100", "member_totals", "rollup_runs", "recovery_runs"):
                connection.execute(f"DELETE FROM {table} WHERE group_id NOT IN ({placeholders})", selected)
            event_conditions = " OR ".join("event_id LIKE ?" for _ in selected)
            event_parameters = tuple(f"{group_id}:%" for group_id in selected)
            connection.execute(
                f"DELETE FROM event_dedup WHERE NOT ({event_conditions})", event_parameters
            )

    def managed_groups(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute("SELECT * FROM managed_groups WHERE enabled=1 ORDER BY group_id"))

    def is_managed_group(self, group_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM managed_groups WHERE group_id=? AND enabled=1", (group_id,)
            ).fetchone()
            return row is not None

    def set_group_info(self, group_id: int, group_name: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE managed_groups SET group_name=?, updated_at=? WHERE group_id=?",
                (group_name, utc_now(), group_id),
            )

    def set_stats_capability(self, group_id: int, enabled: bool, role: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE managed_groups SET stats_enabled=?, stats_role=?, updated_at=? WHERE group_id=?",
                (int(enabled), role, utc_now(), group_id),
            )

    def group_stats_enabled(self, group_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT stats_enabled FROM managed_groups WHERE group_id=? AND enabled=1", (group_id,)
            ).fetchone()
            return bool(row and row["stats_enabled"])

    def mark_rollup_error(self, group_id: int, day: date, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO rollup_runs(group_id,day,status,source,row_count,completed_at,error)
                   VALUES (?,?,?,?,0,?,?)
                   ON CONFLICT(group_id,day) DO UPDATE SET status='error',completed_at=excluded.completed_at,error=excluded.error""",
                (group_id, day.isoformat(), "error", "events", utc_now(), error[:1000]),
            )

    def record_recovery(self, group_id: int, status: str, row_count: int = 0, error: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO recovery_runs(group_id,status,row_count,error,created_at) VALUES (?,?,?,?,?)",
                (group_id, status, row_count, error[:1000], utc_now()),
            )

    def open_qq_transport_connection_incident(
        self,
        *,
        last_connected_at: str | None,
        bot_self_id: str,
        trigger: str,
        diagnosis: str,
        snapshot: Mapping[str, Any],
    ) -> sqlite3.Row:
        """Persist one continuous OneBot outage without duplicating scheduler ticks."""
        now = utc_now()
        payload = json.dumps(dict(snapshot), ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO qq_transport_connection_incidents
                   (detected_at,last_connected_at,bot_self_id,trigger,status,diagnosis,snapshot_json)
                   VALUES (?,?,?,?, 'open', ?, ?)""",
                (
                    now,
                    (last_connected_at or "")[:64] or None,
                    str(bot_self_id)[:64],
                    str(trigger)[:64],
                    str(diagnosis)[:500],
                    payload[:8000],
                ),
            )
            row = connection.execute(
                """SELECT * FROM qq_transport_connection_incidents
                   WHERE status='open' ORDER BY incident_id DESC LIMIT 1"""
            ).fetchone()
            if row is None:  # pragma: no cover - defensive guard for corrupted external DB edits.
                raise RuntimeError("QQ transport connection incident was not persisted")
            return row

    def recover_open_qq_transport_connection_incident(
        self, *, recovery_snapshot: Mapping[str, Any]
    ) -> sqlite3.Row | None:
        """Close the current outage after the QQ transport reconnects, if one exists."""
        now = utc_now()
        payload = json.dumps(dict(recovery_snapshot), ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM qq_transport_connection_incidents
                   WHERE status='open' ORDER BY incident_id DESC LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            try:
                detected_at = datetime.fromisoformat(str(row["detected_at"]))
                duration_seconds = max(0, int((datetime.now(timezone.utc) - detected_at).total_seconds()))
            except ValueError:
                duration_seconds = None
            connection.execute(
                """UPDATE qq_transport_connection_incidents
                   SET status='recovered', recovered_at=?, duration_seconds=?, recovery_snapshot_json=?
                   WHERE incident_id=? AND status='open'""",
                (now, duration_seconds, payload[:8000], int(row["incident_id"])),
            )
            return connection.execute(
                "SELECT * FROM qq_transport_connection_incidents WHERE incident_id=?",
                (int(row["incident_id"]),),
            ).fetchone()

    def qq_transport_connection_incidents(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM qq_transport_connection_incidents
                       ORDER BY incident_id DESC LIMIT ?""",
                    (max(1, min(int(limit), 50)),),
                )
            )

    def current_qq_transport_connection_incident(self) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT * FROM qq_transport_connection_incidents
                   WHERE status='open' ORDER BY incident_id DESC LIMIT 1"""
            ).fetchone()

    def record_activity_run(
        self, group_id: int, day: date, status: str, source: str = "", error: str = ""
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO activity_collection_runs
                   (group_id,report_day,status,source,completed_at,error)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(group_id,report_day) DO UPDATE SET
                     status=excluded.status, source=excluded.source,
                     completed_at=excluded.completed_at, error=excluded.error""",
                (group_id, day.isoformat(), status, source, utc_now(), error[:1000]),
            )

    def save_activity_snapshot(
        self,
        group_id: int,
        day: date,
        window: str,
        active_member_count: int,
        members: Iterable[dict[str, Any]],
        source: str,
    ) -> None:
        if window not in {"yesterday", "seven_days"}:
            raise ValueError(f"unsupported activity window: {window}")
        day_value = day.isoformat()
        now = utc_now()
        normalized = []
        for rank, member in enumerate(members, 1):
            normalized.append(
                (
                    group_id,
                    day_value,
                    window,
                    int(member["user_id"]),
                    str(member.get("nickname") or ""),
                    max(0, int(member.get("activity_count", 0))),
                    rank,
                )
            )
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO group_activity_snapshots
                   (group_id,report_day,window,active_member_count,source,status,collected_at,error)
                   VALUES (?,?,?,?,?,'completed',?,'')
                   ON CONFLICT(group_id,report_day,window) DO UPDATE SET
                     active_member_count=excluded.active_member_count,
                     source=excluded.source, status='completed',
                     collected_at=excluded.collected_at, error=''""",
                (group_id, day_value, window, max(0, int(active_member_count)), source, now),
            )
            connection.execute(
                """DELETE FROM group_activity_members
                   WHERE group_id=? AND report_day=? AND window=?""",
                (group_id, day_value, window),
            )
            connection.executemany(
                """INSERT INTO group_activity_members
                   (group_id,report_day,window,user_id,nickname,activity_count,rank)
                   VALUES (?,?,?,?,?,?,?)""",
                normalized,
            )

    def mark_activity_snapshot_error(
        self, group_id: int, day: date, window: str, source: str, error: str
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO group_activity_snapshots
                   (group_id,report_day,window,active_member_count,source,status,collected_at,error)
                   VALUES (?,?,?,0,?,'error',?,?)
                   ON CONFLICT(group_id,report_day,window) DO UPDATE SET
                     source=excluded.source, status='error', collected_at=excluded.collected_at,
                     error=excluded.error""",
                (group_id, day.isoformat(), window, source, utc_now(), error[:1000]),
            )

    def activity_snapshot(self, group_id: int, day: date, window: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT * FROM group_activity_snapshots
                   WHERE group_id=? AND report_day=? AND window=?""",
                (group_id, day.isoformat(), window),
            ).fetchone()

    def activity_rows(self, group_id: int, day: date, window: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT user_id,nickname,activity_count AS message_count,rank
                       FROM group_activity_members
                       WHERE group_id=? AND report_day=? AND window=? ORDER BY rank""",
                    (group_id, day.isoformat(), window),
                )
            )

    def latest_activity_run(self, group_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT * FROM activity_collection_runs
                   WHERE group_id=? ORDER BY report_day DESC LIMIT 1""",
                (group_id,),
            ).fetchone()

    def replace_members(self, group_id: int, members: Iterable[dict[str, Any]]) -> None:
        now = utc_now()
        normalized = []
        for member in members:
            user_id = int(member["user_id"])
            normalized.append(
                (
                    group_id,
                    user_id,
                    str(member.get("nickname") or ""),
                    str(member.get("card") or ""),
                    str(member.get("avatar_url") or member.get("avatar") or ""),
                    str(member.get("role") or "member"),
                    now,
                )
            )
        with self.connect() as connection:
            connection.execute("UPDATE group_members SET active=0 WHERE group_id=?", (group_id,))
            connection.executemany(
                """INSERT INTO group_members
                   (group_id,user_id,nickname,card,avatar_url,role,active,last_seen_at)
                   VALUES (?,?,?,?,?,?,1,?)
                   ON CONFLICT(group_id,user_id) DO UPDATE SET
                     nickname=excluded.nickname, card=excluded.card, avatar_url=excluded.avatar_url,
                     role=excluded.role,
                     active=1, last_seen_at=excluded.last_seen_at""",
                normalized,
            )
            connection.execute(
                "UPDATE managed_groups SET last_member_sync_at=?, updated_at=? WHERE group_id=?",
                (now, now, group_id),
            )

    def whitelist_contains(self, user_id: int) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM whitelist WHERE user_id=?", (user_id,)).fetchone() is not None

    @staticmethod
    def _filter_table(kind: str) -> str:
        tables = {"active": "active_filters", "passive": "passive_filters"}
        try:
            return tables[kind]
        except KeyError as exc:
            raise ValueError(f"unknown filter kind: {kind}") from exc

    def filter_contains(self, kind: str, user_id: int) -> bool:
        table = self._filter_table(kind)
        with self.connect() as connection:
            return connection.execute(
                f"SELECT 1 FROM {table} WHERE user_id=?", (int(user_id),)
            ).fetchone() is not None

    def active_filter_contains(self, user_id: int) -> bool:
        return self.filter_contains("active", user_id)

    def passive_filter_contains(self, user_id: int) -> bool:
        return self.filter_contains("passive", user_id)

    def add_filter_members(self, kind: str, user_ids: Iterable[int], created_by: int) -> tuple[int, ...]:
        table = self._filter_table(kind)
        members = tuple(dict.fromkeys(int(user_id) for user_id in user_ids if int(user_id) > 0))
        if not members:
            return ()
        with self.connect() as connection:
            connection.executemany(
                f"""INSERT INTO {table}(user_id,created_by,created_at) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET created_by=excluded.created_by""",
                ((user_id, int(created_by), utc_now()) for user_id in members),
            )
        return members

    def remove_filter_members(self, kind: str, user_ids: Iterable[int]) -> tuple[int, ...]:
        table = self._filter_table(kind)
        members = tuple(dict.fromkeys(int(user_id) for user_id in user_ids if int(user_id) > 0))
        removed: list[int] = []
        with self.connect() as connection:
            for user_id in members:
                cursor = connection.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
                if cursor.rowcount:
                    removed.append(user_id)
        return tuple(removed)

    def filter_members(self, kind: str) -> list[sqlite3.Row]:
        table = self._filter_table(kind)
        with self.connect() as connection:
            return list(connection.execute(f"SELECT * FROM {table} ORDER BY user_id"))

    def passive_settings(self) -> dict[str, str]:
        with self.connect() as connection:
            return {
                str(row["setting_key"]): str(row["setting_value"])
                for row in connection.execute("SELECT setting_key,setting_value FROM passive_settings")
            }

    def set_passive_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO passive_settings(setting_key,setting_value,updated_at) VALUES (?,?,?)
                   ON CONFLICT(setting_key) DO UPDATE SET
                     setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
                (str(key), str(value), utc_now()),
            )

    def asoul_state(self, key: str, default: object | None = None) -> object | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state_value FROM asoul_plugin_state WHERE state_key=?", (str(key),)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(str(row["state_value"]))
        except json.JSONDecodeError:
            return default

    def set_asoul_state(self, key: str, value: object) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO asoul_plugin_state(state_key,state_value,updated_at) VALUES (?,?,?)
                   ON CONFLICT(state_key) DO UPDATE SET
                     state_value=excluded.state_value, updated_at=excluded.updated_at""",
                (str(key), payload, utc_now()),
            )

    def passive_group_settings(self, group_id: int) -> dict[str, str]:
        with self.connect() as connection:
            return {
                str(row["setting_key"]): str(row["setting_value"])
                for row in connection.execute(
                    "SELECT setting_key,setting_value FROM passive_group_settings WHERE group_id=?",
                    (int(group_id),),
                )
            }

    def set_passive_group_setting(self, group_id: int, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO passive_group_settings(group_id,setting_key,setting_value,updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(group_id,setting_key) DO UPDATE SET
                     setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
                (int(group_id), str(key), str(value), utc_now()),
            )

    def hourly_announcement_settings(self) -> dict[str, str]:
        with self.connect() as connection:
            return {
                str(row["setting_key"]): str(row["setting_value"])
                for row in connection.execute(
                    "SELECT setting_key,setting_value FROM hourly_announcement_settings"
                )
            }

    def set_hourly_announcement_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO hourly_announcement_settings(setting_key,setting_value,updated_at)
                   VALUES (?,?,?)
                   ON CONFLICT(setting_key) DO UPDATE SET
                     setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
                (str(key), str(value), utc_now()),
            )

    def ensure_hourly_delivery(
        self, slot_key: str, group_id: int, text_index: int, message: str = ""
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO hourly_announcement_deliveries
                   (slot_key,group_id,text_index,message,created_at) VALUES (?,?,?,?,?)""",
                (str(slot_key), int(group_id), int(text_index), str(message), utc_now()),
            )
            return cursor.rowcount == 1

    def update_hourly_delivery_message(self, slot_key: str, group_id: int, message: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE hourly_announcement_deliveries
                   SET message=?
                   WHERE slot_key=? AND group_id=? AND status='pending'""",
                (str(message), str(slot_key), int(group_id)),
            )

    def recent_hourly_text_indices(self, group_id: int, limit: int = 24) -> tuple[int, ...]:
        """Return recently sent copy indexes so a large pool does not repeat immediately."""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT text_index FROM hourly_announcement_deliveries
                   WHERE group_id=? AND status='sent'
                   ORDER BY sent_at DESC, slot_key DESC LIMIT ?""",
                (int(group_id), max(1, min(int(limit), 200))),
            )
            return tuple(int(row["text_index"]) for row in rows)

    def pending_hourly_deliveries(
        self, slot_key: str, maximum_attempts: int, limit: int = 50
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM hourly_announcement_deliveries
                       WHERE slot_key=? AND status='pending' AND attempts<?
                       ORDER BY group_id LIMIT ?""",
                    (str(slot_key), int(maximum_attempts), max(1, min(int(limit), 200))),
                )
            )

    def mark_hourly_delivery_sent(self, slot_key: str, group_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE hourly_announcement_deliveries
                   SET status='sent', sent_at=?, last_error=''
                   WHERE slot_key=? AND group_id=? AND status='pending'""",
                (utc_now(), str(slot_key), int(group_id)),
            )

    def mark_hourly_delivery_uncertain(self, slot_key: str, group_id: int, error: str) -> None:
        """Stop retries when the adapter may have delivered the message already."""
        with self.connect() as connection:
            connection.execute(
                """UPDATE hourly_announcement_deliveries
                   SET attempts=attempts+1, status='uncertain', sent_at=?, last_error=?
                   WHERE slot_key=? AND group_id=? AND status='pending'""",
                (utc_now(), str(error)[:1000], str(slot_key), int(group_id)),
            )

    def mark_hourly_delivery_error(
        self,
        slot_key: str,
        group_id: int,
        error: str,
        maximum_attempts: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE hourly_announcement_deliveries
                   SET attempts=attempts+1,
                       status=CASE WHEN attempts+1>=? THEN 'failed' ELSE 'pending' END,
                       last_error=?
                   WHERE slot_key=? AND group_id=? AND status='pending'""",
                (int(maximum_attempts), str(error)[:1000], str(slot_key), int(group_id)),
            )

    def hourly_deliveries(self, slot_key: str, limit: int = 200) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM hourly_announcement_deliveries
                       WHERE slot_key=? ORDER BY group_id LIMIT ?""",
                    (str(slot_key), max(1, min(int(limit), 500))),
                )
            )

    def ensure_ranking_delivery(
        self, day: date, group_id: int, delivery_type: str, domain_id: int
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO ranking_deliveries
                   (day,group_id,delivery_type,domain_id,created_at)
                   VALUES (?,?,?,?,?)""",
                (
                    day.isoformat(),
                    int(group_id),
                    str(delivery_type),
                    int(domain_id),
                    utc_now(),
                ),
            )
        return cursor.rowcount == 1

    def pending_ranking_deliveries(
        self, day: date, delivery_type: str
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM ranking_deliveries
                       WHERE day=? AND delivery_type=? AND status='pending'
                       ORDER BY group_id""",
                    (day.isoformat(), str(delivery_type)),
                )
            )

    def mark_ranking_delivery_sent(
        self, day: date, group_id: int, delivery_type: str
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE ranking_deliveries
                   SET status='sent',sent_at=?,last_error=''
                   WHERE day=? AND group_id=? AND delivery_type=? AND status='pending'""",
                (utc_now(), day.isoformat(), int(group_id), str(delivery_type)),
            )

    def mark_ranking_delivery_uncertain(
        self, day: date, group_id: int, delivery_type: str, error: str
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE ranking_deliveries
                   SET attempts=attempts+1,status='uncertain',sent_at=?,last_error=?
                   WHERE day=? AND group_id=? AND delivery_type=? AND status='pending'""",
                (
                    utc_now(),
                    str(error)[:1000],
                    day.isoformat(),
                    int(group_id),
                    str(delivery_type),
                ),
            )

    def mark_ranking_delivery_error(
        self, day: date, group_id: int, delivery_type: str, error: str
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE ranking_deliveries
                   SET attempts=attempts+1,last_error=?
                   WHERE day=? AND group_id=? AND delivery_type=? AND status='pending'""",
                (
                    str(error)[:1000],
                    day.isoformat(),
                    int(group_id),
                    str(delivery_type),
                ),
            )

    def ranking_deliveries(
        self, day: date, delivery_type: str
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM ranking_deliveries
                       WHERE day=? AND delivery_type=? ORDER BY group_id""",
                    (day.isoformat(), str(delivery_type)),
                )
            )

    def a_coast_profile_state(self, user_id: int, scope_key: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT profile_text FROM a_coast_profile_states WHERE user_id=? AND scope_key=?",
                (int(user_id), str(scope_key)),
            ).fetchone()
        return str(row["profile_text"] or "") if row else ""

    def a_coast_profile_updated_at(self, user_id: int, scope_key: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT updated_at FROM a_coast_profile_states WHERE user_id=? AND scope_key=?",
                (int(user_id), str(scope_key)),
            ).fetchone()
        return str(row["updated_at"] or "") if row else ""

    def set_a_coast_profile_state(self, user_id: int, scope_key: str, profile_text: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO a_coast_profile_states(user_id,scope_key,profile_text,updated_at)
                   VALUES (?,?,?,?) ON CONFLICT(user_id,scope_key) DO UPDATE SET
                   profile_text=excluded.profile_text,updated_at=excluded.updated_at""",
                (int(user_id), str(scope_key), str(profile_text)[:12000], utc_now()),
            )

    def a_coast_group_profile_states(self, user_id: int, group_ids: Iterable[int]) -> list[sqlite3.Row]:
        keys = tuple(f"group:{int(group_id)}" for group_id in group_ids)
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        with self.connect() as connection:
            return list(connection.execute(
                f"""SELECT scope_key,profile_text,updated_at FROM a_coast_profile_states
                    WHERE user_id=? AND scope_key IN ({placeholders}) ORDER BY scope_key""",
                (int(user_id), *keys),
            ))

    def claim_random_repeat(
        self,
        group_id: int,
        now: float,
        probability: float,
        cooldown_seconds: int,
        message_interval: int,
        random_value: float,
        repeatable: bool,
    ) -> str:
        """Count one passive message and atomically reserve a safe repeat slot when allowed."""
        group_id = int(group_id)
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO passive_repeat_state(group_id) VALUES (?)", (group_id,)
            )
            row = connection.execute(
                "SELECT last_repeat_at,messages_since_repeat FROM passive_repeat_state WHERE group_id=?",
                (group_id,),
            ).fetchone()
            message_count = int(row["messages_since_repeat"]) + 1
            connection.execute(
                "UPDATE passive_repeat_state SET messages_since_repeat=? WHERE group_id=?",
                (message_count, group_id),
            )
            if not repeatable:
                return "not_repeatable"
            if float(now) < float(row["last_repeat_at"]) + int(cooldown_seconds):
                return "cooldown"
            if message_count < int(message_interval):
                return "message_interval"
            if float(random_value) >= float(probability):
                return "probability"
            connection.execute(
                """UPDATE passive_repeat_state
                   SET last_repeat_at=?,messages_since_repeat=0 WHERE group_id=?""",
                (float(now), group_id),
            )
            return "claimed"

    def add_whitelist(self, user_id: int, created_by: int, note: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO whitelist(user_id,note,created_by,created_at) VALUES (?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET note=excluded.note""",
                (user_id, note, created_by, utc_now()),
            )

    def remove_whitelist(self, user_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM whitelist WHERE user_id=?", (user_id,))
            return cursor.rowcount > 0

    def whitelist(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute("SELECT * FROM whitelist ORDER BY user_id"))

    def user_profiles(self, user_ids: Iterable[int]) -> dict[int, dict[str, str]]:
        ids = tuple(dict.fromkeys(int(user_id) for user_id in user_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT gm.user_id, gm.nickname, gm.card, gm.avatar_url
                    FROM group_members gm JOIN managed_groups mg ON mg.group_id=gm.group_id
                    WHERE gm.active=1 AND gm.user_id IN ({placeholders})
                    ORDER BY gm.user_id, gm.group_id""",
                ids,
            ).fetchall()
        profiles: dict[int, dict[str, str]] = {
            user_id: {"nickname": "", "avatar_url": ""} for user_id in ids
        }
        for row in rows:
            profile = profiles[int(row["user_id"])]
            nickname = str(row["nickname"] or "")
            avatar_url = str(row["avatar_url"] or "")
            if nickname and not profile["nickname"]:
                profile["nickname"] = nickname
            if avatar_url and not profile["avatar_url"]:
                profile["avatar_url"] = avatar_url
        return profiles

    def group_member_name(self, group_id: int, user_id: int) -> str:
        """Display name (group card first, then nickname) for one active member."""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT nickname, card FROM group_members "
                "WHERE group_id=? AND user_id=? AND active=1 LIMIT 1",
                (int(group_id), int(user_id)),
            ).fetchone()
        if row is None:
            return ""
        return str(row["card"] or row["nickname"] or "")

    def whitelist_profiles(self) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.whitelist()]
        profiles = self.user_profiles(row["user_id"] for row in rows)
        for row in rows:
            profile = profiles.get(int(row["user_id"]), {})
            row["nickname"] = profile.get("nickname", "")
            row["avatar_url"] = profile.get("avatar_url", "")
        return rows

    def duplicate_members(
        self, group_ids: Iterable[int], ignore_whitelist: bool = False
    ) -> list[dict[str, Any]]:
        ids = tuple(group_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        whitelist_clause = "" if ignore_whitelist else (
            "AND NOT EXISTS (SELECT 1 FROM whitelist w WHERE w.user_id=gm.user_id)"
        )
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT gm.user_id, gm.group_id, mg.group_name, gm.nickname, gm.card, gm.avatar_url
                    FROM group_members gm JOIN managed_groups mg ON mg.group_id=gm.group_id
                    WHERE gm.active=1 AND gm.group_id IN ({placeholders})
                      {whitelist_clause}
                    ORDER BY gm.user_id, gm.group_id""",
                ids,
            ).fetchall()
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            user_id = int(row["user_id"])
            item = grouped.setdefault(
                user_id,
                {
                    "user_id": user_id,
                    "nickname": "",
                    "avatar_url": "",
                    "groups": [],
                },
            )
            nickname = str(row["nickname"] or "")
            avatar_url = str(row["avatar_url"] or "")
            if nickname and not item["nickname"]:
                item["nickname"] = nickname
            if avatar_url and not item["avatar_url"]:
                item["avatar_url"] = avatar_url
            item["groups"].append(dict(row))
        return [
            item for item in grouped.values() if len(item["groups"]) > 1
        ]

    def duplicate_members_from_source(
        self,
        source_group_id: int,
        target_group_ids: Iterable[int],
        ignore_whitelist: bool = False,
    ) -> list[dict[str, Any]]:
        """Return only source-group members also present in target groups."""
        targets = tuple(dict.fromkeys(int(group_id) for group_id in target_group_ids))
        if not targets:
            return []
        selected = (int(source_group_id), *targets)
        placeholders = ",".join("?" for _ in selected)
        target_placeholders = ",".join("?" for _ in targets)
        whitelist_clause = "" if ignore_whitelist else (
            "AND NOT EXISTS (SELECT 1 FROM whitelist w WHERE w.user_id=gm.user_id)"
        )
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT gm.user_id, gm.group_id, mg.group_name, gm.nickname, gm.card, gm.avatar_url
                    FROM group_members gm JOIN managed_groups mg ON mg.group_id=gm.group_id
                    WHERE gm.active=1 AND gm.group_id IN ({placeholders})
                      {whitelist_clause}
                      AND gm.user_id IN (
                          SELECT source.user_id FROM group_members source
                          WHERE source.group_id=? AND source.active=1
                            AND EXISTS (
                                SELECT 1 FROM group_members target
                                WHERE target.user_id=source.user_id AND target.active=1
                                  AND target.group_id IN ({target_placeholders})
                            )
                      )
                    ORDER BY gm.user_id, gm.group_id""",
                (*selected, int(source_group_id), *targets),
            ).fetchall()
        grouped: dict[int, dict[str, Any]] = {}
        order = {group_id: index for index, group_id in enumerate(selected)}
        for row in rows:
            user_id = int(row["user_id"])
            item = grouped.setdefault(
                user_id,
                {"user_id": user_id, "nickname": "", "avatar_url": "", "groups": []},
            )
            nickname = str(row["nickname"] or "")
            avatar_url = str(row["avatar_url"] or "")
            if nickname and not item["nickname"]:
                item["nickname"] = nickname
            if avatar_url and not item["avatar_url"]:
                item["avatar_url"] = avatar_url
            item["groups"].append(dict(row))
        for item in grouped.values():
            item["groups"].sort(key=lambda group: order.get(int(group["group_id"]), len(order)))
        return list(grouped.values())

    def record_message(
        self,
        event_id: str,
        group_id: int,
        user_id: int,
        nickname: str,
        message_at: datetime,
    ) -> bool:
        day = message_at.date().isoformat()
        now = utc_now()
        with self.connect() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO event_dedup(event_id,received_at) VALUES (?,?)",
                (event_id, now),
            ).rowcount
            if not inserted:
                return False
            connection.execute(
                """INSERT INTO daily_counts(group_id,day,user_id,nickname,message_count,updated_at)
                   VALUES (?,?,?,?,1,?)
                   ON CONFLICT(group_id,day,user_id) DO UPDATE SET
                     nickname=excluded.nickname, message_count=message_count+1,
                     updated_at=excluded.updated_at""",
                (group_id, day, user_id, nickname, now),
            )
        return True

    def record_today_wife_activity(
        self,
        event_id: str,
        group_id: int,
        user_id: int,
        message_at: datetime,
    ) -> bool:
        """Store only deduplicated daily activity counts for today-wife weighting."""
        day = message_at.date().isoformat()
        cutoff = (message_at.date() - timedelta(days=2)).isoformat()
        now = utc_now()
        with self.connect() as connection:
            connection.execute("DELETE FROM today_wife_activity_events WHERE day<?", (cutoff,))
            connection.execute("DELETE FROM today_wife_activity_counts WHERE day<?", (cutoff,))
            inserted = connection.execute(
                """INSERT OR IGNORE INTO today_wife_activity_events(event_id,group_id,day,recorded_at)
                   VALUES (?,?,?,?)""",
                (event_id, int(group_id), day, now),
            ).rowcount
            if not inserted:
                return False
            connection.execute(
                """INSERT INTO today_wife_activity_counts(group_id,day,user_id,message_count,updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(group_id,day,user_id) DO UPDATE SET
                     message_count=message_count+1, updated_at=excluded.updated_at""",
                (int(group_id), day, int(user_id), 1, now),
            )
        return True

    def today_wife_activity_counts(self, group_id: int, day: date) -> dict[int, int]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT user_id,message_count FROM today_wife_activity_counts
                   WHERE group_id=? AND day=?""",
                (int(group_id), day.isoformat()),
            )
            return {int(row["user_id"]): int(row["message_count"]) for row in rows}

    def clear_today_wife_data(self, group_id: int) -> int:
        """Fully clear relationship records and the anonymous activity cache."""
        with self.connect() as connection:
            deleted = connection.execute(
                "DELETE FROM today_wife_records WHERE group_id=?", (int(group_id),)
            )
            connection.execute("DELETE FROM today_wife_day_states WHERE group_id=?", (int(group_id),))
            connection.execute("DELETE FROM today_wife_interaction_events WHERE group_id=?", (int(group_id),))
            connection.execute("DELETE FROM today_wife_activity_events WHERE group_id=?", (int(group_id),))
            connection.execute("DELETE FROM today_wife_activity_counts WHERE group_id=?", (int(group_id),))
        return int(deleted.rowcount)

    def clear_today_wife_relationships(self, group_id: int) -> int:
        """Clear only relationship history, preserving recent activity eligibility."""
        with self.connect() as connection:
            deleted = connection.execute(
                "DELETE FROM today_wife_records WHERE group_id=?", (int(group_id),)
            )
            connection.execute("DELETE FROM today_wife_day_states WHERE group_id=?", (int(group_id),))
            connection.execute("DELETE FROM today_wife_interaction_events WHERE group_id=?", (int(group_id),))
        return int(deleted.rowcount)

    def finalize_day(self, group_id: int, day: date, source: str = "events") -> dict[str, Any]:
        day_value = day.isoformat()
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT status,row_count FROM rollup_runs WHERE group_id=? AND day=?",
                (group_id, day_value),
            ).fetchone()
            if existing and existing["status"] == "completed":
                return {"status": "already_completed", "row_count": int(existing["row_count"])}
            rows = connection.execute(
                """SELECT user_id,nickname,message_count FROM daily_counts
                   WHERE group_id=? AND day=?
                   ORDER BY message_count DESC, user_id ASC LIMIT 100""",
                (group_id, day_value),
            ).fetchall()
            connection.execute("DELETE FROM daily_top100 WHERE group_id=? AND day=?", (group_id, day_value))
            for rank, row in enumerate(rows, 1):
                connection.execute(
                    """INSERT INTO daily_top100
                       (group_id,day,user_id,nickname,rank,message_count,finalized_at,source)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (group_id, day_value, row["user_id"], row["nickname"], rank, row["message_count"], now, source),
                )
                connection.execute(
                    """INSERT INTO member_totals(group_id,user_id,nickname,total_count,updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(group_id,user_id) DO UPDATE SET
                         nickname=excluded.nickname, total_count=total_count+excluded.total_count,
                         updated_at=excluded.updated_at""",
                    (group_id, row["user_id"], row["nickname"], row["message_count"], now),
                )
            connection.execute(
                """INSERT INTO rollup_runs(group_id,day,status,source,row_count,completed_at,error)
                   VALUES (?,?,?,?,?,?, '')
                   ON CONFLICT(group_id,day) DO UPDATE SET
                     status=excluded.status, source=excluded.source, row_count=excluded.row_count,
                     completed_at=excluded.completed_at, error=''""",
                (group_id, day_value, "completed", source, len(rows), now),
            )
        return {"status": "completed", "row_count": len(rows)}

    def today_counts(self, group_id: int, day: date) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT user_id,nickname,message_count FROM daily_counts
                       WHERE group_id=? AND day=? ORDER BY message_count DESC,user_id ASC LIMIT 100""",
                    (group_id, day.isoformat()),
                )
            )

    def message_ranking(
        self,
        group_ids: Iterable[int],
        start_day: date | None,
    ) -> list[dict[str, Any]]:
        """Return one Top 100 aggregate without storing message text or raw events."""
        selected = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        clauses = [f"group_id IN ({placeholders})"]
        parameters: list[Any] = list(selected)
        if start_day is not None:
            clauses.append("day>=?")
            parameters.append(start_day.isoformat())
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT group_id,user_id,nickname,message_count,updated_at
                    FROM daily_counts
                    WHERE {' AND '.join(clauses)}""",
                parameters,
            ).fetchall()
            names = {
                int(row["group_id"]): str(row["group_name"] or "")
                for row in connection.execute(
                    f"SELECT group_id,group_name FROM managed_groups WHERE group_id IN ({placeholders})",
                    selected,
                )
            }

        aggregated: dict[int, dict[str, Any]] = {}
        for row in rows:
            user_id = int(row["user_id"])
            current = aggregated.setdefault(
                user_id,
                {
                    "user_id": user_id,
                    "nickname": "",
                    "message_count": 0,
                    "group_counts": {},
                    "_latest": "",
                },
            )
            count = int(row["message_count"])
            group_id = int(row["group_id"])
            current["message_count"] += count
            current["group_counts"][group_id] = current["group_counts"].get(group_id, 0) + count
            updated_at = str(row["updated_at"] or "")
            if updated_at >= current["_latest"]:
                current["nickname"] = str(row["nickname"] or "")
                current["_latest"] = updated_at

        values = sorted(
            aggregated.values(), key=lambda item: (-int(item["message_count"]), int(item["user_id"]))
        )[:100]
        result: list[dict[str, Any]] = []
        group_order = {group_id: index for index, group_id in enumerate(selected)}
        for rank, item in enumerate(values, 1):
            group_counts = item.pop("group_counts")
            dominant_group_id = min(
                group_counts,
                key=lambda group_id: (-group_counts[group_id], group_order[group_id]),
            )
            item.pop("_latest", None)
            item["rank"] = rank
            item["group_labels"] = names.get(dominant_group_id) or "未命名群"
            result.append(item)
        return result

    def group_message_totals(
        self, group_ids: Iterable[int], start_day: date | None
    ) -> list[dict[str, Any]]:
        """Return independent message totals for every selected group, including zeroes."""
        selected = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        clauses = [f"group_id IN ({placeholders})"]
        parameters: list[Any] = list(selected)
        if start_day is not None:
            clauses.append("day>=?")
            parameters.append(start_day.isoformat())
        with self.connect() as connection:
            names = {
                int(row["group_id"]): str(row["group_name"] or "")
                for row in connection.execute(
                    f"SELECT group_id,group_name FROM managed_groups WHERE group_id IN ({placeholders})",
                    selected,
                )
            }
            counts = {
                int(row["group_id"]): int(row["message_count"] or 0)
                for row in connection.execute(
                    f"SELECT group_id,SUM(message_count) AS message_count FROM daily_counts "
                    f"WHERE {' AND '.join(clauses)} GROUP BY group_id",
                    parameters,
                )
            }
        return [
            {"group_id": group_id, "group_name": names.get(group_id) or str(group_id), "message_count": counts.get(group_id, 0)}
            for group_id in selected
        ]

    def group_daily_message_totals(
        self, group_id: int, start_day: date, end_day: date
    ) -> list[dict[str, Any]]:
        """Return one point per calendar day, retaining days with no messages."""
        if end_day < start_day:
            return []
        with self.connect() as connection:
            counts = {
                str(row["day"]): int(row["message_count"] or 0)
                for row in connection.execute(
                    """SELECT day,SUM(message_count) AS message_count FROM daily_counts
                       WHERE group_id=? AND day>=? AND day<=? GROUP BY day""",
                    (int(group_id), start_day.isoformat(), end_day.isoformat()),
                )
            }
        day = start_day
        values: list[dict[str, Any]] = []
        while day <= end_day:
            values.append({"day": day.isoformat(), "message_count": counts.get(day.isoformat(), 0)})
            day += timedelta(days=1)
        return values

    def history_top(self, group_id: int, day: date) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT rank,user_id,nickname,message_count FROM daily_top100
                       WHERE group_id=? AND day=? ORDER BY rank""",
                    (group_id, day.isoformat()),
                )
            )

    def group_total_rows(self, group_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT user_id,nickname,total_count FROM member_totals
                       WHERE group_id=? ORDER BY total_count DESC,user_id ASC LIMIT 100""",
                    (group_id,),
                )
            )

    def get_binding(self, user_id: int, game: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT game_uid FROM game_bindings WHERE user_id=? AND game=?", (user_id, game)
            ).fetchone()
            return str(row["game_uid"]) if row else None

    def set_binding(self, user_id: int, game: str, game_uid: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO game_bindings(user_id,game,game_uid,updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(user_id,game) DO UPDATE SET game_uid=excluded.game_uid,updated_at=excluded.updated_at""",
                (user_id, game, game_uid, utc_now()),
            )

    def get_cache(self, game: str, cache_key: str, now: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT payload_json FROM game_cache WHERE game=? AND cache_key=? AND expires_at>?""",
                (game, cache_key, now),
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None

    def set_cache(self, game: str, cache_key: str, payload: dict[str, Any], fetched_at: str, expires_at: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO game_cache(game,cache_key,payload_json,fetched_at,expires_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(game,cache_key) DO UPDATE SET payload_json=excluded.payload_json,
                     fetched_at=excluded.fetched_at, expires_at=excluded.expires_at""",
                (game, cache_key, json.dumps(payload, ensure_ascii=False), fetched_at, expires_at),
            )

    def audit(self, actor_id: int, action: str, group_id: int | None = None, detail: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_log(actor_id,action,group_id,detail,created_at) VALUES (?,?,?,?,?)",
                (actor_id, action, group_id, detail[:1000], utc_now()),
            )

    def prune_dedup(self, before: datetime) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM event_dedup WHERE received_at < ?", (before.isoformat(),))
