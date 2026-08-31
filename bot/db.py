from __future__ import annotations

import json
import random
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS managed_groups (
    group_id INTEGER PRIMARY KEY,
    group_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    stats_enabled INTEGER NOT NULL DEFAULT 0,
    stats_role TEXT NOT NULL DEFAULT '',
    last_member_sync_at TEXT,
    updated_at TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS napcat_connection_incidents (
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

CREATE UNIQUE INDEX IF NOT EXISTS napcat_one_open_connection_incident
    ON napcat_connection_incidents(status) WHERE status='open';

CREATE INDEX IF NOT EXISTS napcat_connection_incidents_detected_idx
    ON napcat_connection_incidents(detected_at DESC);

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

CREATE TABLE IF NOT EXISTS a_coast_daily_ranking_deliveries (
    day TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (day, group_id),
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

CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    activity_type TEXT NOT NULL DEFAULT 'announcement',
    visibility TEXT NOT NULL DEFAULT 'masked',
    status TEXT NOT NULL DEFAULT 'scheduled',
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    creator_id INTEGER NOT NULL,
    creator_group_id INTEGER NOT NULL,
    draw_seed TEXT NOT NULL DEFAULT '',
    cancelled_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_groups (
    activity_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    PRIMARY KEY (activity_id, group_id),
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_participants (
    participant_id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    source_group_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    card TEXT NOT NULL DEFAULT '',
    registration_no INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TEXT NOT NULL,
    cancelled_at TEXT,
    UNIQUE (activity_id, user_id),
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE,
    FOREIGN KEY (source_group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_prizes (
    prize_id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL,
    prize_name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    sort_order INTEGER NOT NULL,
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_winners (
    activity_id INTEGER NOT NULL,
    prize_id INTEGER NOT NULL,
    participant_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (activity_id, participant_id),
    UNIQUE (activity_id, user_id),
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE,
    FOREIGN KEY (prize_id) REFERENCES activity_prizes(prize_id) ON DELETE CASCADE,
    FOREIGN KEY (participant_id) REFERENCES activity_participants(participant_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_broadcasts (
    activity_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (activity_id, group_id, kind),
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS surveys (
    survey_id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    response_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    creator_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    published_at TEXT,
    ended_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS survey_groups (
    survey_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    PRIMARY KEY (survey_id, group_id),
    FOREIGN KEY (survey_id) REFERENCES surveys(survey_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS survey_responses (
    survey_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    source_group_id INTEGER NOT NULL,
    responded_at TEXT NOT NULL,
    PRIMARY KEY (survey_id, user_id),
    FOREIGN KEY (survey_id) REFERENCES surveys(survey_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS survey_broadcasts (
    survey_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (survey_id, group_id),
    FOREIGN KEY (survey_id) REFERENCES surveys(survey_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedback_entries (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id INTEGER,
    user_id INTEGER NOT NULL,
    source_group_id INTEGER,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_group_id) REFERENCES managed_groups(group_id) ON DELETE SET NULL,
    FOREIGN KEY (survey_id) REFERENCES surveys(survey_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS feedback_deliveries (
    feedback_id INTEGER NOT NULL,
    operator_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (feedback_id, operator_id),
    FOREIGN KEY (feedback_id) REFERENCES feedback_entries(feedback_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS feedback_entries_created_idx
    ON feedback_entries(feedback_id DESC);

CREATE INDEX IF NOT EXISTS feedback_deliveries_pending_idx
    ON feedback_deliveries(operator_id, status, feedback_id);

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

CREATE TABLE IF NOT EXISTS codex_tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    creator_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'queued', 'running', 'stopping', 'completed', 'failed', 'cancelled')),
    run_enabled INTEGER NOT NULL DEFAULT 0 CHECK (run_enabled IN (0, 1)),
    codex_thread_id TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    last_result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS codex_task_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    creator_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')),
    result TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (task_id) REFERENCES codex_tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS codex_task_messages_queue_idx
    ON codex_task_messages(status, task_id, message_id);
"""


ACTIVITY_ID_START = 500


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
            activity_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(activities)")
            }
            if "visibility" not in activity_columns:
                # Existing activities preserve the historical masked behavior.
                connection.execute(
                    "ALTER TABLE activities ADD COLUMN visibility TEXT NOT NULL DEFAULT 'masked'"
                )
            survey_columns = {row["name"] for row in connection.execute("PRAGMA table_info(surveys)")}
            if "published_at" not in survey_columns:
                connection.execute("ALTER TABLE surveys ADD COLUMN published_at TEXT")
                connection.execute(
                    """UPDATE surveys SET published_at=(
                           SELECT MAX(sent_at) FROM survey_broadcasts AS b
                           WHERE b.survey_id=surveys.survey_id AND b.status='sent'
                       )
                       WHERE published_at IS NULL"""
                )
            feedback_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(feedback_entries)")
            }
            if "survey_id" not in feedback_columns:
                connection.execute("ALTER TABLE feedback_entries ADD COLUMN survey_id INTEGER")
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
            # Keep activity IDs visually distinct from incidental database IDs.
            # Existing activities retain their IDs; only future inserts start at 500.
            minimum_sequence = ACTIVITY_ID_START - 1
            sequence_row = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='activities'"
            ).fetchone()
            if sequence_row is None:
                connection.execute(
                    "INSERT INTO sqlite_sequence(name,seq) VALUES ('activities', ?)",
                    (minimum_sequence,),
                )
            elif int(sequence_row["seq"]) < minimum_sequence:
                connection.execute(
                    "UPDATE sqlite_sequence SET seq=? WHERE name='activities'",
                    (minimum_sequence,),
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

    def configure_groups(self, group_ids: Iterable[int]) -> None:
        now = utc_now()
        ids = tuple(group_ids)
        with self.connect() as connection:
            connection.execute("UPDATE managed_groups SET enabled=0, updated_at=?", (now,))
            for group_id in ids:
                connection.execute(
                    """INSERT INTO managed_groups(group_id, enabled, updated_at)
                       VALUES (?, 1, ?)
                       ON CONFLICT(group_id) DO UPDATE SET enabled=1, updated_at=excluded.updated_at""",
                    (group_id, now),
                )

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

    def open_napcat_connection_incident(
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
                """INSERT OR IGNORE INTO napcat_connection_incidents
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
                """SELECT * FROM napcat_connection_incidents
                   WHERE status='open' ORDER BY incident_id DESC LIMIT 1"""
            ).fetchone()
            if row is None:  # pragma: no cover - defensive guard for corrupted external DB edits.
                raise RuntimeError("NapCat connection incident was not persisted")
            return row

    def recover_open_napcat_connection_incident(
        self, *, recovery_snapshot: Mapping[str, Any]
    ) -> sqlite3.Row | None:
        """Close the current outage after NapCat reconnects, if one exists."""
        now = utc_now()
        payload = json.dumps(dict(recovery_snapshot), ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM napcat_connection_incidents
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
                """UPDATE napcat_connection_incidents
                   SET status='recovered', recovered_at=?, duration_seconds=?, recovery_snapshot_json=?
                   WHERE incident_id=? AND status='open'""",
                (now, duration_seconds, payload[:8000], int(row["incident_id"])),
            )
            return connection.execute(
                "SELECT * FROM napcat_connection_incidents WHERE incident_id=?",
                (int(row["incident_id"]),),
            ).fetchone()

    def napcat_connection_incidents(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM napcat_connection_incidents
                       ORDER BY incident_id DESC LIMIT ?""",
                    (max(1, min(int(limit), 50)),),
                )
            )

    def current_napcat_connection_incident(self) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT * FROM napcat_connection_incidents
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

    def ensure_a_coast_daily_ranking_delivery(self, day: date, group_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO a_coast_daily_ranking_deliveries
                   (day,group_id,created_at) VALUES (?,?,?)""",
                (day.isoformat(), int(group_id), utc_now()),
            )
            return cursor.rowcount == 1

    def pending_a_coast_daily_ranking_deliveries(self, day: date) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM a_coast_daily_ranking_deliveries
                       WHERE day=? AND status='pending' ORDER BY group_id""",
                    (day.isoformat(),),
                )
            )

    def mark_a_coast_daily_ranking_delivery_sent(self, day: date, group_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE a_coast_daily_ranking_deliveries
                   SET status='sent', sent_at=?, last_error=''
                   WHERE day=? AND group_id=? AND status='pending'""",
                (utc_now(), day.isoformat(), int(group_id)),
            )

    def mark_a_coast_daily_ranking_delivery_uncertain(
        self, day: date, group_id: int, error: str
    ) -> None:
        """Suppress a retry when OneBot may already have posted the ranking."""
        with self.connect() as connection:
            connection.execute(
                """UPDATE a_coast_daily_ranking_deliveries
                   SET attempts=attempts+1, status='uncertain', sent_at=?, last_error=?
                   WHERE day=? AND group_id=? AND status='pending'""",
                (utc_now(), str(error)[:1000], day.isoformat(), int(group_id)),
            )

    def mark_a_coast_daily_ranking_delivery_error(
        self, day: date, group_id: int, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE a_coast_daily_ranking_deliveries
                   SET attempts=attempts+1, last_error=?
                   WHERE day=? AND group_id=? AND status='pending'""",
                (str(error)[:1000], day.isoformat(), int(group_id)),
            )

    def a_coast_daily_ranking_deliveries(self, day: date) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM a_coast_daily_ranking_deliveries
                       WHERE day=? ORDER BY group_id""",
                    (day.isoformat(),),
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

    def create_codex_task(self, title: str, prompt: str, creator_id: int) -> int:
        """Create a draft task with one queued turn; it only runs after an explicit start."""
        normalized_title = title.strip()[:120]
        normalized_prompt = prompt.strip()
        if not normalized_title:
            raise ValueError("Codex task title cannot be empty")
        if not normalized_prompt:
            raise ValueError("Codex task prompt cannot be empty")
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO codex_tasks
                   (title,creator_id,status,run_enabled,created_at,updated_at)
                   VALUES (?,?,'draft',0,?,?)""",
                (normalized_title, int(creator_id), now, now),
            )
            task_id = int(cursor.lastrowid)
            connection.execute(
                """INSERT INTO codex_task_messages(task_id,content,creator_id,status,created_at)
                   VALUES (?,?,?,'queued',?)""",
                (task_id, normalized_prompt, int(creator_id), now),
            )
        return task_id

    def codex_task(self, task_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT t.*,
                          (SELECT COUNT(*) FROM codex_task_messages m
                           WHERE m.task_id=t.task_id AND m.status='queued') AS queued_count,
                          (SELECT COUNT(*) FROM codex_task_messages m
                           WHERE m.task_id=t.task_id) AS message_count
                   FROM codex_tasks t WHERE t.task_id=?""",
                (int(task_id),),
            ).fetchone()

    def codex_tasks(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT t.*,
                              (SELECT COUNT(*) FROM codex_task_messages m
                               WHERE m.task_id=t.task_id AND m.status='queued') AS queued_count,
                              (SELECT COUNT(*) FROM codex_task_messages m
                               WHERE m.task_id=t.task_id) AS message_count
                       FROM codex_tasks t
                       ORDER BY CASE t.status
                           WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'stopping' THEN 2
                           WHEN 'draft' THEN 3 ELSE 4 END, t.updated_at DESC, t.task_id DESC
                       LIMIT ?""",
                    (max(1, min(int(limit), 100)),),
                )
            )

    def codex_task_messages(self, task_id: int, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM codex_task_messages WHERE task_id=?
                       ORDER BY message_id DESC LIMIT ?""",
                    (int(task_id), max(1, min(int(limit), 100))),
                )
            )

    def append_codex_task_message(self, task_id: int, prompt: str, creator_id: int) -> int:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Codex task prompt cannot be empty")
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT status,run_enabled FROM codex_tasks WHERE task_id=?", (int(task_id),)
            ).fetchone()
            if task is None:
                raise ValueError("Codex task does not exist")
            if str(task["status"]) == "cancelled":
                raise ValueError("Cancelled Codex tasks cannot receive new instructions")
            cursor = connection.execute(
                """INSERT INTO codex_task_messages(task_id,content,creator_id,status,created_at)
                   VALUES (?,?,?,'queued',?)""",
                (int(task_id), normalized_prompt, int(creator_id), now),
            )
            next_status = "queued" if int(task["run_enabled"]) else "draft"
            if str(task["status"]) in {"running", "stopping"}:
                next_status = str(task["status"])
            connection.execute(
                "UPDATE codex_tasks SET status=?,updated_at=? WHERE task_id=?",
                (next_status, now, int(task_id)),
            )
            return int(cursor.lastrowid)

    def start_codex_task(self, task_id: int) -> str:
        """Enable a queued task unless another task currently owns the single worker."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT * FROM codex_tasks WHERE task_id=?", (int(task_id),)
            ).fetchone()
            if task is None:
                return "not_found"
            if str(task["status"]) == "cancelled":
                return "cancelled"
            queued = connection.execute(
                "SELECT 1 FROM codex_task_messages WHERE task_id=? AND status='queued' LIMIT 1",
                (int(task_id),),
            ).fetchone()
            if queued is None:
                return "empty"
            active = connection.execute(
                """SELECT task_id FROM codex_tasks
                   WHERE task_id<>? AND run_enabled=1 AND status IN ('queued','running','stopping')
                   LIMIT 1""",
                (int(task_id),),
            ).fetchone()
            if active is not None:
                return f"busy:{int(active['task_id'])}"
            connection.execute(
                """UPDATE codex_tasks
                   SET status='queued',run_enabled=1,last_error='',updated_at=?
                   WHERE task_id=?""",
                (now, int(task_id)),
            )
            return "queued"

    def claim_next_codex_task_message(self) -> sqlite3.Row | None:
        """Atomically claim one turn for the local single-worker runner."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT m.*,t.title,t.codex_thread_id,t.status AS task_status
                   FROM codex_task_messages m
                   JOIN codex_tasks t ON t.task_id=m.task_id
                   WHERE m.status='queued' AND t.run_enabled=1 AND t.status='queued'
                   ORDER BY t.updated_at, t.task_id, m.message_id
                   LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE codex_task_messages SET status='running',started_at=?,error=''
                   WHERE message_id=? AND status='queued'""",
                (now, int(row["message_id"])),
            )
            connection.execute(
                """UPDATE codex_tasks SET status='running',started_at=COALESCE(started_at,?),
                   updated_at=? WHERE task_id=?""",
                (now, now, int(row["task_id"])),
            )
            return connection.execute(
                """SELECT m.*,t.title,t.codex_thread_id,t.status AS task_status
                   FROM codex_task_messages m JOIN codex_tasks t ON t.task_id=m.task_id
                   WHERE m.message_id=?""",
                (int(row["message_id"]),),
            ).fetchone()

    def set_codex_task_thread(self, task_id: int, thread_id: str) -> None:
        normalized = thread_id.strip()[:200]
        if not normalized:
            return
        with self.connect() as connection:
            connection.execute(
                """UPDATE codex_tasks SET codex_thread_id=?,updated_at=?
                   WHERE task_id=?""",
                (normalized, utc_now(), int(task_id)),
            )

    def finish_codex_task_message(
        self,
        message_id: int,
        outcome: str,
        *,
        result: str = "",
        error: str = "",
    ) -> sqlite3.Row | None:
        if outcome not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("invalid Codex task outcome")
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            message = connection.execute(
                "SELECT task_id FROM codex_task_messages WHERE message_id=?", (int(message_id),)
            ).fetchone()
            if message is None:
                return None
            task_id = int(message["task_id"])
            task = connection.execute(
                "SELECT * FROM codex_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if task is None:
                return None
            connection.execute(
                """UPDATE codex_task_messages
                   SET status=?,result=?,error=?,completed_at=? WHERE message_id=?""",
                (outcome, result[:24_000], error[:4_000], now, int(message_id)),
            )
            queued_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM codex_task_messages WHERE task_id=? AND status='queued'",
                    (task_id,),
                ).fetchone()[0]
            )
            status_before = str(task["status"])
            run_enabled = bool(task["run_enabled"])
            if status_before == "cancelled":
                status_after = "cancelled"
                run_after = 0
            elif outcome == "failed":
                status_after = "failed"
                run_after = 0
            elif outcome in {"cancelled", "interrupted"}:
                status_after = "draft" if status_before == "stopping" else "failed"
                run_after = 0
            elif queued_count and run_enabled:
                status_after = "queued"
                run_after = 1
            elif queued_count:
                status_after = "draft"
                run_after = 0
            else:
                status_after = "completed"
                run_after = 0
            connection.execute(
                """UPDATE codex_tasks
                   SET status=?,run_enabled=?,last_result=?,last_error=?,updated_at=?,
                       completed_at=CASE WHEN ? IN ('completed','failed','cancelled') THEN ? ELSE completed_at END
                   WHERE task_id=?""",
                (
                    status_after,
                    int(run_after),
                    result[:24_000],
                    error[:4_000],
                    now,
                    status_after,
                    now,
                    task_id,
                ),
            )
            return connection.execute(
                """SELECT t.*,
                          (SELECT COUNT(*) FROM codex_task_messages m
                           WHERE m.task_id=t.task_id AND m.status='queued') AS queued_count,
                          (SELECT COUNT(*) FROM codex_task_messages m
                           WHERE m.task_id=t.task_id) AS message_count
                   FROM codex_tasks t WHERE t.task_id=?""",
                (task_id,),
            ).fetchone()

    def stop_codex_task(self, task_id: int, *, cancel_all: bool = False) -> str:
        """Pause an active task or permanently discard its queued work."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT * FROM codex_tasks WHERE task_id=?", (int(task_id),)
            ).fetchone()
            if task is None:
                return "not_found"
            if str(task["status"]) == "cancelled":
                return "already_cancelled"
            running = connection.execute(
                """SELECT 1 FROM codex_task_messages
                   WHERE task_id=? AND status='running' LIMIT 1""",
                (int(task_id),),
            ).fetchone()
            if cancel_all:
                connection.execute(
                    """UPDATE codex_task_messages SET status='cancelled',completed_at=?,
                       error='Cancelled by super administrator'
                       WHERE task_id=? AND status='queued'""",
                    (now, int(task_id)),
                )
                connection.execute(
                    """UPDATE codex_tasks SET status='cancelled',run_enabled=0,updated_at=?,
                       completed_at=CASE WHEN ?=0 THEN ? ELSE completed_at END
                       WHERE task_id=?""",
                    (now, 0 if running is None else 1, now, int(task_id)),
                )
                return "cancelling" if running else "cancelled"
            next_status = "stopping" if running else "draft"
            connection.execute(
                """UPDATE codex_tasks SET status=?,run_enabled=0,updated_at=? WHERE task_id=?""",
                (next_status, now, int(task_id)),
            )
            return "stopping" if running else "paused"

    def retry_codex_task(self, task_id: int, creator_id: int) -> str:
        """Copy the latest failed/interrupted turn into a new queued turn."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT status FROM codex_tasks WHERE task_id=?", (int(task_id),)
            ).fetchone()
            if task is None:
                return "not_found"
            if str(task["status"]) == "cancelled":
                return "cancelled"
            active = connection.execute(
                """SELECT 1 FROM codex_task_messages WHERE task_id=? AND status='running' LIMIT 1""",
                (int(task_id),),
            ).fetchone()
            if active is not None:
                return "running"
            source = connection.execute(
                """SELECT content FROM codex_task_messages WHERE task_id=?
                   AND status IN ('failed','cancelled','interrupted')
                   ORDER BY message_id DESC LIMIT 1""",
                (int(task_id),),
            ).fetchone()
            if source is None:
                return "nothing_to_retry"
            connection.execute(
                """INSERT INTO codex_task_messages(task_id,content,creator_id,status,created_at)
                   VALUES (?,?,?,'queued',?)""",
                (int(task_id), str(source["content"]), int(creator_id), now),
            )
            connection.execute(
                """UPDATE codex_tasks SET status='draft',run_enabled=0,last_error='',updated_at=?
                   WHERE task_id=?""",
                (now, int(task_id)),
            )
            return "queued"

    def recover_codex_tasks_after_restart(self) -> int:
        """Never silently rerun a turn that NoneBot interrupted during a restart."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            interrupted = connection.execute(
                """UPDATE codex_task_messages SET status='interrupted',completed_at=?,
                   error='NoneBot restarted before this Codex turn finished'
                   WHERE status='running'""",
                (now,),
            ).rowcount
            connection.execute(
                """UPDATE codex_tasks SET status=CASE
                       WHEN status='cancelled' THEN 'cancelled' ELSE 'failed' END,
                   run_enabled=0,last_error=CASE WHEN status='cancelled' THEN last_error
                       ELSE 'NoneBot restarted before this Codex turn finished' END,
                   updated_at=?
                   WHERE status IN ('queued','running','stopping')""",
                (now,),
            )
            return int(interrupted)

    def audit(self, actor_id: int, action: str, group_id: int | None = None, detail: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_log(actor_id,action,group_id,detail,created_at) VALUES (?,?,?,?,?)",
                (actor_id, action, group_id, detail[:1000], utc_now()),
            )

    def create_activity(
        self,
        title: str,
        description: str,
        activity_type: str,
        starts_at: str,
        ends_at: str,
        creator_id: int,
        creator_group_id: int,
        group_ids: Iterable[int],
        prizes: Iterable[tuple[str, int]],
        broadcast: str,
        visibility: str = "masked",
        exclude_group_id: int | None = None,
    ) -> int:
        groups = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        prize_rows = tuple((str(name), int(quantity)) for name, quantity in prizes)
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO activities
                   (title,description,activity_type,visibility,status,starts_at,ends_at,
                    creator_id,creator_group_id,created_at,updated_at)
                   VALUES (?,?,?,?,'scheduled',?,?,?,?,?,?)""",
                (
                    title,
                    description,
                    activity_type,
                    visibility,
                    starts_at,
                    ends_at,
                    creator_id,
                    creator_group_id,
                    now,
                    now,
                ),
            )
            activity_id = int(cursor.lastrowid)
            broadcast = broadcast.replace("{activity_id}", str(activity_id))
            connection.executemany(
                "INSERT INTO activity_groups(activity_id,group_id) VALUES (?,?)",
                [(activity_id, group_id) for group_id in groups],
            )
            connection.executemany(
                """INSERT INTO activity_prizes(activity_id,prize_name,quantity,sort_order)
                   VALUES (?,?,?,?)""",
                [(activity_id, name, quantity, index) for index, (name, quantity) in enumerate(prize_rows, 1)],
            )
            connection.executemany(
                """INSERT INTO activity_broadcasts
                   (activity_id,group_id,kind,message,created_at)
                   VALUES (?,?, 'created', ?, ?)""",
                [
                    (activity_id, group_id, broadcast, now)
                    for group_id in groups
                    if group_id != exclude_group_id
                ],
            )
        return activity_id

    def activity(self, activity_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT a.*, COUNT(DISTINCT ap.user_id) AS participant_count,
                          COUNT(DISTINCT ag.group_id) AS group_count
                   FROM activities a
                   LEFT JOIN activity_participants ap
                     ON ap.activity_id=a.activity_id AND ap.status IN ('active','winner')
                   LEFT JOIN activity_groups ag ON ag.activity_id=a.activity_id
                   WHERE a.activity_id=? GROUP BY a.activity_id""",
                (int(activity_id),),
            ).fetchone()

    def activities(self, statuses: Iterable[str] = ("scheduled", "active")) -> list[sqlite3.Row]:
        values = tuple(statuses)
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""SELECT a.*, COUNT(DISTINCT ap.user_id) AS participant_count,
                               COUNT(DISTINCT ag.group_id) AS group_count
                        FROM activities a
                        LEFT JOIN activity_participants ap
                          ON ap.activity_id=a.activity_id AND ap.status IN ('active','winner')
                        LEFT JOIN activity_groups ag ON ag.activity_id=a.activity_id
                        WHERE a.status IN ({placeholders})
                        GROUP BY a.activity_id ORDER BY a.starts_at, a.activity_id""",
                    values,
                )
            )

    def activity_groups(self, activity_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT ag.group_id, COALESCE(mg.group_name,'') AS group_name
                       FROM activity_groups ag JOIN managed_groups mg ON mg.group_id=ag.group_id
                       WHERE ag.activity_id=? ORDER BY ag.group_id""",
                    (int(activity_id),),
                )
            )

    def activity_prizes(self, activity_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM activity_prizes WHERE activity_id=? ORDER BY sort_order, prize_id",
                    (int(activity_id),),
                )
            )

    def update_activity(
        self,
        activity_id: int,
        title: str,
        description: str,
        activity_type: str,
        visibility: str,
        starts_at: str,
        ends_at: str,
        group_ids: Iterable[int],
        prizes: Iterable[tuple[str, int]],
        broadcast: str,
        unsupported_broadcast: str,
        allow_active: bool = False,
    ) -> dict[str, tuple[int, ...]] | None:
        groups = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        prize_rows = tuple((str(name), int(quantity)) for name, quantity in prizes)
        now = utc_now()
        with self.connect() as connection:
            old_group_rows = connection.execute(
                "SELECT group_id FROM activity_groups WHERE activity_id=? ORDER BY group_id",
                (int(activity_id),),
            ).fetchall()
            old_groups = tuple(int(row["group_id"]) for row in old_group_rows)
            allowed_statuses = ("scheduled", "active") if allow_active else ("scheduled",)
            status_placeholders = ",".join("?" for _ in allowed_statuses)
            cursor = connection.execute(
                """UPDATE activities SET title=?,description=?,activity_type=?,visibility=?,
                          starts_at=?,ends_at=?,updated_at=?
                   WHERE activity_id=? AND status IN (""" + status_placeholders + ")",
                (
                    title,
                    description,
                    activity_type,
                    visibility,
                    starts_at,
                    ends_at,
                    now,
                    int(activity_id),
                    *allowed_statuses,
                ),
            )
            if cursor.rowcount != 1:
                return None
            connection.execute("DELETE FROM activity_groups WHERE activity_id=?", (int(activity_id),))
            connection.executemany(
                "INSERT INTO activity_groups(activity_id,group_id) VALUES (?,?)",
                [(int(activity_id), group_id) for group_id in groups],
            )
            connection.execute("DELETE FROM activity_prizes WHERE activity_id=?", (int(activity_id),))
            connection.executemany(
                """INSERT INTO activity_prizes(activity_id,prize_name,quantity,sort_order)
                   VALUES (?,?,?,?)""",
                [
                    (int(activity_id), name, quantity, index)
                    for index, (name, quantity) in enumerate(prize_rows, 1)
                ],
            )
            # A queued notification can describe an older version. Supersede it
            # before creating notifications for the new version.
            connection.execute(
                """UPDATE activity_broadcasts
                   SET status='superseded', last_error='activity updated before delivery'
                   WHERE activity_id=? AND status='pending'""",
                (int(activity_id),),
            )
            retained_or_added = tuple(dict.fromkeys(groups))
            connection.executemany(
                """INSERT OR IGNORE INTO activity_broadcasts
                   (activity_id,group_id,kind,message,created_at) VALUES (?,?,?,?,?)""",
                [
                    (int(activity_id), group_id, f"updated:{now}", broadcast, now)
                    for group_id in retained_or_added
                ],
            )
            removed_groups = tuple(group_id for group_id in old_groups if group_id not in groups)
            connection.executemany(
                """INSERT OR IGNORE INTO activity_broadcasts
                   (activity_id,group_id,kind,message,created_at) VALUES (?,?,?,?,?)""",
                [
                    (int(activity_id), group_id, f"unsupported:{now}", unsupported_broadcast, now)
                    for group_id in removed_groups
                ],
            )
        return {"old_group_ids": old_groups, "new_group_ids": groups, "removed_group_ids": removed_groups}

    def activity_participants(
        self,
        activity_id: int,
        active_only: bool = True,
        include_winners: bool = False,
    ) -> list[sqlite3.Row]:
        if active_only and include_winners:
            clause = "AND status IN ('active','winner')"
        elif active_only:
            clause = "AND status='active'"
        else:
            clause = ""
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""SELECT ap.*, COALESCE(mg.group_name,'') AS group_name
                        FROM activity_participants ap
                        LEFT JOIN managed_groups mg ON mg.group_id=ap.source_group_id
                        WHERE ap.activity_id=? {clause}
                        ORDER BY ap.registration_no""",
                    (int(activity_id),),
                )
            )

    def register_activity(
        self, activity_id: int, user_id: int, group_id: int, nickname: str, card: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            activity = connection.execute(
                "SELECT status FROM activities WHERE activity_id=?", (int(activity_id),)
            ).fetchone()
            if activity is None:
                return {"status": "not_found"}
            if activity["status"] not in {"scheduled", "active"}:
                return {"status": "closed"}
            existing = connection.execute(
                "SELECT * FROM activity_participants WHERE activity_id=? AND user_id=?",
                (int(activity_id), int(user_id)),
            ).fetchone()
            if existing and existing["status"] == "active":
                return {"status": "already", "registration_no": int(existing["registration_no"])}
            sequence = connection.execute(
                "SELECT COALESCE(MAX(registration_no),0)+1 FROM activity_participants WHERE activity_id=?",
                (int(activity_id),),
            ).fetchone()[0]
            if existing:
                connection.execute(
                    """UPDATE activity_participants SET source_group_id=?,nickname=?,card=?,
                       registration_no=?,status='active',registered_at=?,cancelled_at=NULL
                       WHERE participant_id=?""",
                    (int(group_id), nickname, card, int(sequence), now, int(existing["participant_id"])),
                )
                participant_id = int(existing["participant_id"])
            else:
                cursor = connection.execute(
                    """INSERT INTO activity_participants
                       (activity_id,user_id,source_group_id,nickname,card,registration_no,status,registered_at)
                       VALUES (?,?,?,?,?,?, 'active', ?)""",
                    (int(activity_id), int(user_id), int(group_id), nickname, card, int(sequence), now),
                )
                participant_id = int(cursor.lastrowid)
        return {"status": "registered", "participant_id": participant_id, "registration_no": int(sequence)}

    def cancel_activity_registration(self, activity_id: int, user_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE activity_participants SET status='cancelled',cancelled_at=?
                   WHERE activity_id=? AND user_id=? AND status='active'""",
                (utc_now(), int(activity_id), int(user_id)),
            )
            return cursor.rowcount > 0

    def transition_activity(
        self,
        activity_id: int,
        expected_status: str,
        new_status: str,
        broadcast: str,
        reason: str = "",
    ) -> bool:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE activities SET status=?,cancelled_reason=?,updated_at=?
                   WHERE activity_id=? AND status=?""",
                (new_status, reason, now, int(activity_id), expected_status),
            )
            if cursor.rowcount != 1:
                return False
            if new_status == "cancelled":
                connection.execute(
                    """UPDATE activity_participants SET status='event_cancelled',cancelled_at=?
                       WHERE activity_id=? AND status='active'""",
                    (now, int(activity_id)),
                )
            groups = connection.execute(
                "SELECT group_id FROM activity_groups WHERE activity_id=?", (int(activity_id),)
            ).fetchall()
            connection.executemany(
                """INSERT OR IGNORE INTO activity_broadcasts
                   (activity_id,group_id,kind,message,created_at) VALUES (?,?,?,?,?)""",
                [(int(activity_id), int(row["group_id"]), new_status, broadcast, now) for row in groups],
            )
        return True

    def due_activities(self, now: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM activities
                       WHERE (status='scheduled' AND (starts_at<=? OR ends_at<=?))
                          OR (status='active' AND ends_at<=?)
                       ORDER BY ends_at, activity_id""",
                    (now, now, now),
                )
            )

    def draw_activity(self, activity_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            activity = connection.execute(
                "SELECT * FROM activities WHERE activity_id=?", (int(activity_id),)
            ).fetchone()
            if not activity or activity["activity_type"] != "lottery":
                return []
            existing = connection.execute(
                """SELECT aw.*, ap.prize_name FROM activity_winners aw
                   JOIN activity_prizes ap ON ap.prize_id=aw.prize_id
                   WHERE aw.activity_id=? ORDER BY aw.prize_id, aw.participant_id""",
                (int(activity_id),),
            ).fetchall()
            if existing:
                return list(existing)
            seed = str(activity["draw_seed"] or secrets.token_hex(16))
            connection.execute(
                "UPDATE activities SET draw_seed=?,updated_at=? WHERE activity_id=?",
                (seed, utc_now(), int(activity_id)),
            )
            participants = connection.execute(
                """SELECT * FROM activity_participants
                   WHERE activity_id=? AND status='active' ORDER BY registration_no""",
                (int(activity_id),),
            ).fetchall()
            shuffled = list(participants)
            random.Random(seed).shuffle(shuffled)
            winners: list[tuple[Any, ...]] = []
            cursor = 0
            for prize in connection.execute(
                "SELECT * FROM activity_prizes WHERE activity_id=? ORDER BY sort_order,prize_id",
                (int(activity_id),),
            ).fetchall():
                for _ in range(int(prize["quantity"])):
                    if cursor >= len(shuffled):
                        break
                    participant = shuffled[cursor]
                    cursor += 1
                    winners.append(
                        (int(activity_id), int(prize["prize_id"]), int(participant["participant_id"]),
                         int(participant["user_id"]), utc_now())
                    )
            connection.executemany(
                """INSERT OR IGNORE INTO activity_winners
                   (activity_id,prize_id,participant_id,user_id,created_at) VALUES (?,?,?,?,?)""",
                winners,
            )
            connection.executemany(
                "UPDATE activity_participants SET status='winner' WHERE participant_id=?",
                [(row[2],) for row in winners],
            )
            return list(
                connection.execute(
                    """SELECT aw.*, ap.prize_name, p.nickname, p.registration_no
                       FROM activity_winners aw
                       JOIN activity_prizes ap ON ap.prize_id=aw.prize_id
                       JOIN activity_participants p ON p.participant_id=aw.participant_id
                       WHERE aw.activity_id=? ORDER BY ap.sort_order, aw.participant_id""",
                    (int(activity_id),),
                )
            )

    def activity_winners(self, activity_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT aw.*, ap.prize_name, p.nickname, p.registration_no
                       FROM activity_winners aw
                       JOIN activity_prizes ap ON ap.prize_id=aw.prize_id
                       JOIN activity_participants p ON p.participant_id=aw.participant_id
                       WHERE aw.activity_id=? ORDER BY ap.sort_order, aw.participant_id""",
                    (int(activity_id),),
                )
            )

    def my_activities(self, user_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT a.activity_id,a.title,a.activity_type,a.status,a.starts_at,a.ends_at,
                              ap.registration_no,ap.status AS participation_status,
                              aw.prize_id,pr.prize_name
                       FROM activity_participants ap JOIN activities a ON a.activity_id=ap.activity_id
                       LEFT JOIN activity_winners aw ON aw.activity_id=ap.activity_id AND aw.user_id=ap.user_id
                       LEFT JOIN activity_prizes pr ON pr.prize_id=aw.prize_id
                       WHERE ap.user_id=? ORDER BY a.created_at DESC""",
                    (int(user_id),),
                )
            )

    def pending_activity_broadcasts(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM activity_broadcasts
                       WHERE status='pending' ORDER BY created_at LIMIT ?""",
                    (max(1, min(int(limit), 200)),),
                )
            )

    def create_survey(
        self,
        question: str,
        creator_id: int,
        group_ids: Iterable[int],
    ) -> int:
        groups = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        if not groups:
            raise ValueError("survey requires at least one target group")
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO surveys(question,response_text,creator_id,created_at,updated_at)
                   VALUES (?,?,?,?,?)""",
                (question, "", int(creator_id), now, now),
            )
            survey_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO survey_groups(survey_id,group_id) VALUES (?,?)",
                [(survey_id, group_id) for group_id in groups],
            )
            connection.executemany(
                """INSERT INTO survey_broadcasts(survey_id,group_id,created_at)
                   VALUES (?,?,?)""",
                [(survey_id, group_id, now) for group_id in groups],
            )
            return survey_id

    def survey(self, survey_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT s.*, COUNT(f.feedback_id) AS feedback_count
                   FROM surveys s
                   LEFT JOIN feedback_entries f ON f.survey_id=s.survey_id
                   WHERE s.survey_id=?
                   GROUP BY s.survey_id""",
                (int(survey_id),),
            ).fetchone()

    def surveys(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT s.*, COUNT(f.feedback_id) AS feedback_count
                       FROM surveys s
                       LEFT JOIN feedback_entries f ON f.survey_id=s.survey_id
                       GROUP BY s.survey_id
                       ORDER BY s.survey_id DESC LIMIT ?""",
                    (max(1, min(int(limit), 200)),),
                )
            )

    def survey_groups(self, survey_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT group_id FROM survey_groups WHERE survey_id=? ORDER BY group_id",
                    (int(survey_id),),
                )
            )

    def end_survey(self, survey_id: int) -> bool:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE surveys
                   SET status='ended',ended_at=?,updated_at=?
                   WHERE survey_id=? AND status='active'""",
                (now, now, int(survey_id)),
            )
            return cursor.rowcount == 1

    def publish_survey(self, survey_id: int) -> bool:
        """Publish one feedback announcement and end every prior active announcement."""
        now = utc_now()
        with self.connect() as connection:
            target = connection.execute(
                "SELECT status FROM surveys WHERE survey_id=?", (int(survey_id),)
            ).fetchone()
            if target is None or str(target["status"]) != "active":
                return False
            connection.execute(
                """UPDATE surveys SET status='ended',ended_at=?,updated_at=?
                   WHERE survey_id<>? AND status='active' AND published_at IS NOT NULL""",
                (now, now, int(survey_id)),
            )
            connection.execute(
                """UPDATE surveys SET published_at=?,updated_at=? WHERE survey_id=?""",
                (now, now, int(survey_id)),
            )
            return True

    def current_feedback_survey(self) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT survey_id,question FROM surveys
                   WHERE status='active' AND published_at IS NOT NULL
                   ORDER BY published_at DESC, survey_id DESC LIMIT 1"""
            ).fetchone()

    def pending_survey_broadcasts(self, survey_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM survey_broadcasts
                       WHERE survey_id=? AND status='pending' ORDER BY group_id""",
                    (int(survey_id),),
                )
            )

    def mark_survey_broadcast_sent(self, survey_id: int, group_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE survey_broadcasts
                   SET status='sent',sent_at=?,last_error=''
                   WHERE survey_id=? AND group_id=?""",
                (utc_now(), int(survey_id), int(group_id)),
            )

    def mark_survey_broadcast_error(self, survey_id: int, group_id: int, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE survey_broadcasts
                   SET attempts=attempts+1,last_error=?
                   WHERE survey_id=? AND group_id=?""",
                (str(error)[:1000], int(survey_id), int(group_id)),
            )

    def record_feedback(
        self, survey_id: int, user_id: int, content: str, source_group_id: int | None = None
    ) -> int:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO feedback_entries(survey_id,user_id,source_group_id,content,created_at)
                   VALUES (?,?,?,?,?)""",
                (int(survey_id), int(user_id), source_group_id, str(content).strip()[:1000], now),
            )
            return int(cursor.lastrowid)

    def feedback_entries(
        self, survey_id: int | None = None, limit: int = 5000, offset: int = 0
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            where = "" if survey_id is None else "WHERE f.survey_id=?"
            parameters: tuple[int, ...] = () if survey_id is None else (int(survey_id),)
            return list(
                connection.execute(
                    f"""SELECT f.feedback_id,f.survey_id,f.user_id,f.source_group_id,f.content,f.created_at,
                               s.question AS survey_question
                        FROM feedback_entries AS f
                        LEFT JOIN surveys AS s ON s.survey_id=f.survey_id
                        {where}
                        ORDER BY f.feedback_id DESC LIMIT ? OFFSET ?""",
                    parameters + (max(1, min(int(limit), 5000)), max(0, int(offset))),
                )
            )

    def ensure_feedback_deliveries(
        self, feedback_ids: Iterable[int], operator_ids: Iterable[int]
    ) -> None:
        entries = tuple(dict.fromkeys(int(feedback_id) for feedback_id in feedback_ids))
        operators = tuple(dict.fromkeys(int(operator_id) for operator_id in operator_ids))
        if not entries or not operators:
            return
        now = utc_now()
        with self.connect() as connection:
            connection.executemany(
                """INSERT OR IGNORE INTO feedback_deliveries
                   (feedback_id,operator_id,created_at) VALUES (?,?,?)""",
                [(feedback_id, operator_id, now) for feedback_id in entries for operator_id in operators],
            )

    def pending_feedback_deliveries(self, operator_id: int, limit: int = 200) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT f.feedback_id,f.survey_id,f.user_id,f.source_group_id,f.content,f.created_at,
                              s.question AS survey_question
                       FROM feedback_deliveries AS d
                       JOIN feedback_entries AS f ON f.feedback_id=d.feedback_id
                       LEFT JOIN surveys AS s ON s.survey_id=f.survey_id
                       WHERE d.operator_id=? AND d.status='pending'
                       ORDER BY f.feedback_id ASC LIMIT ?""",
                    (int(operator_id), max(1, min(int(limit), 1000))),
                )
            )

    def mark_feedback_delivery_sent(self, feedback_id: int, operator_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE feedback_deliveries
                   SET status='sent',sent_at=?,last_error=''
                   WHERE feedback_id=? AND operator_id=?""",
                (utc_now(), int(feedback_id), int(operator_id)),
            )

    def mark_feedback_delivery_error(self, feedback_id: int, operator_id: int, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE feedback_deliveries
                   SET attempts=attempts+1,last_error=?
                   WHERE feedback_id=? AND operator_id=?""",
                (str(error)[:1000], int(feedback_id), int(operator_id)),
            )

    def mark_activity_broadcast_sent(self, activity_id: int, group_id: int, kind: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE activity_broadcasts SET status='sent',sent_at=?,last_error=''
                   WHERE activity_id=? AND group_id=? AND kind=?""",
                (utc_now(), int(activity_id), int(group_id), kind),
            )

    def mark_activity_broadcast_error(
        self, activity_id: int, group_id: int, kind: str, error: str, max_attempts: int
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE activity_broadcasts
                   SET attempts=attempts+1,
                       last_error=?,
                       status=CASE WHEN attempts+1>=? THEN 'failed' ELSE 'pending' END
                   WHERE activity_id=? AND group_id=? AND kind=?""",
                (
                    error[:1000],
                    int(max_attempts),
                    int(activity_id),
                    int(group_id),
                    kind,
                ),
            )

    def prune_dedup(self, before: datetime) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM event_dedup WHERE received_at < ?", (before.isoformat(),))
