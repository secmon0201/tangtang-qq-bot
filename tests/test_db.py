import sqlite3
from datetime import date, datetime

from bot.db import Database


def make_db(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002, 1003))
    return db


def test_database_removes_retired_codex_task_tables(tmp_path):
    path = tmp_path / "bot.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE codex_tasks (task_id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE codex_task_messages (
                message_id INTEGER PRIMARY KEY,
                task_id INTEGER REFERENCES codex_tasks(task_id)
            );
            INSERT INTO codex_tasks(task_id,title) VALUES (1,'retired');
            INSERT INTO codex_task_messages(message_id,task_id) VALUES (1,1);
            """
        )

    db = Database(path)

    with db.connect() as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'codex_task%'"
            )
        }
        migration = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version=2"
        ).fetchone()
    assert tables == set()
    assert migration is not None


def test_duplicate_scan_and_whitelist(tmp_path):
    db = make_db(tmp_path)
    db.replace_members(
        1001,
        [{"user_id": 7, "nickname": "A", "avatar_url": "https://avatar/7"}, {"user_id": 8, "nickname": "B"}],
    )
    db.replace_members(1002, [{"user_id": 7, "nickname": "A2"}, {"user_id": 9, "nickname": "C"}])
    db.replace_members(1003, [{"user_id": 7, "nickname": "A3"}])

    result = db.duplicate_members((1001, 1002, 1003))
    assert len(result) == 1
    assert result[0]["user_id"] == 7
    assert result[0]["nickname"] == "A"
    assert result[0]["avatar_url"] == "https://avatar/7"
    assert [row["group_id"] for row in result[0]["groups"]] == [1001, 1002, 1003]

    db.add_whitelist(7, 99, "trusted")
    assert db.duplicate_members((1001, 1002, 1003)) == []


def test_active_and_passive_filters_are_independent_and_persistent(tmp_path):
    db = make_db(tmp_path)

    assert not db.active_filter_contains(7)
    assert not db.passive_filter_contains(7)
    assert db.add_filter_members("active", (7, 8, 7), 99) == (7, 8)
    assert db.add_filter_members("passive", (8, 9), 99) == (8, 9)
    assert db.active_filter_contains(7)
    assert not db.passive_filter_contains(7)
    assert db.active_filter_contains(8)
    assert db.passive_filter_contains(8)
    assert [int(row["user_id"]) for row in db.filter_members("active")] == [7, 8]
    assert [int(row["user_id"]) for row in db.filter_members("passive")] == [8, 9]

    assert db.remove_filter_members("active", (7, 9)) == (7,)
    assert not db.active_filter_contains(7)
    assert db.passive_filter_contains(9)


def test_legacy_reaction_filter_entries_migrate_to_passive_filters(tmp_path):
    path = tmp_path / "legacy-filter.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE reaction_filters (user_id INTEGER PRIMARY KEY, created_by INTEGER NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO reaction_filters(user_id,created_by,created_at) VALUES (7,99,'2026-07-26T00:00:00+00:00')"
        )

    db = Database(path)

    assert db.passive_filter_contains(7)
    assert not db.active_filter_contains(7)
    with db.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reaction_filters'"
        ).fetchone() is None


def test_random_repeat_requires_time_message_interval_and_probability(tmp_path):
    db = make_db(tmp_path)

    for _ in range(49):
        assert db.claim_random_repeat(1001, 1000, 0.1, 900, 50, 0.0, True) == "message_interval"
    assert db.claim_random_repeat(1001, 1000, 0.1, 900, 50, 0.2, True) == "probability"
    assert db.claim_random_repeat(1001, 1000, 0.1, 900, 50, 0.0, True) == "claimed"
    assert db.claim_random_repeat(1001, 1001, 0.1, 900, 50, 0.0, True) == "cooldown"


def test_random_repeat_allows_zero_cooldown_and_message_interval(tmp_path):
    db = make_db(tmp_path)

    assert db.claim_random_repeat(1001, 1000, 1.0, 0, 0, 0.0, True) == "claimed"


def test_source_duplicate_scan_ignores_target_only_duplicates(tmp_path):
    db = make_db(tmp_path)
    db.replace_members(1001, [{"user_id": 7, "nickname": "起点成员"}])
    db.replace_members(
        1002,
        [
            {"user_id": 7, "nickname": "目标一成员"},
            {"user_id": 9, "nickname": "目标群重复成员"},
        ],
    )
    db.replace_members(1003, [{"user_id": 9, "nickname": "目标二成员"}])

    result = db.duplicate_members_from_source(1001, (1002, 1003))

    assert [item["user_id"] for item in result] == [7]
    assert [row["group_id"] for row in result[0]["groups"]] == [1001, 1002]


def test_duplicate_scans_can_explicitly_ignore_whitelist(tmp_path):
    db = make_db(tmp_path)
    members = [{"user_id": 7, "nickname": "白名单成员"}]
    db.replace_members(1001, members)
    db.replace_members(1002, members)
    db.replace_members(1003, members)
    db.add_whitelist(7, 99, "trusted")

    assert db.duplicate_members((1001, 1002, 1003)) == []
    assert [item["user_id"] for item in db.duplicate_members((1001, 1002, 1003), True)] == [7]
    assert db.duplicate_members_from_source(1001, (1002, 1003)) == []
    assert [item["user_id"] for item in db.duplicate_members_from_source(1001, (1002, 1003), True)] == [7]


def test_existing_database_gets_avatar_column_migrated(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE group_members (
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            nickname TEXT NOT NULL DEFAULT '',
            card TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'member',
            active INTEGER NOT NULL DEFAULT 1,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (group_id, user_id)
        )"""
    )
    connection.commit()
    connection.close()

    db = Database(path)
    db.configure_groups((1001,))
    db.replace_members(1001, [{"user_id": 7, "nickname": "A", "avatar_url": "local"}])

    with db.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(group_members)")}
    assert "avatar_url" in columns


def test_existing_database_migrates_mini_game_tables_for_guess_number(tmp_path):
    path = tmp_path / "legacy-mini-game.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=OFF;
            CREATE TABLE managed_groups (group_id INTEGER PRIMARY KEY);
            CREATE TABLE mini_game_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                game_type TEXT NOT NULL CHECK (game_type IN ('roulette', 'bomb', 'dice')),
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended', 'cancelled')),
                creator_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                ended_at TEXT,
                FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
            );
            CREATE TABLE mini_game_participants (
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
            CREATE TABLE mini_game_stats (
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
                updated_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id),
                FOREIGN KEY (group_id) REFERENCES managed_groups(group_id) ON DELETE CASCADE
            );
            INSERT INTO managed_groups(group_id) VALUES (1001);
            INSERT INTO mini_game_sessions
                (group_id,game_type,status,creator_id,started_at,ends_at,ended_at)
                VALUES (1001,'dice','ended',7,'2026-01-01T00:00:00+00:00','2026-01-01T00:02:00+00:00','2026-01-01T00:02:00+00:00');
            INSERT INTO mini_game_participants
                (session_id,user_id,nickname,action_order,joined_at)
                VALUES (1,7,'旧玩家',1,'2026-01-01T00:00:00+00:00');
            """
        )

    db = Database(path)
    with db.connect() as connection:
        stat_columns = {row["name"] for row in connection.execute("PRAGMA table_info(mini_game_stats)")}
        connection.execute(
            """INSERT INTO mini_game_sessions
               (group_id,game_type,creator_id,started_at,ends_at)
               VALUES (1001,'guess',8,'2026-01-02T00:00:00+00:00','2026-01-02T00:02:00+00:00')"""
        )
        assert connection.execute("SELECT COUNT(*) FROM mini_game_participants").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    assert {"guess_wins", "guess_misses", "guess_games"}.issubset(stat_columns)


def test_message_dedup_and_daily_rollup_is_idempotent(tmp_path):
    db = make_db(tmp_path)
    stamp = datetime(2026, 7, 16, 12, 0)
    assert db.record_message("1001:1", 1001, 7, "A", stamp)
    assert not db.record_message("1001:1", 1001, 7, "A", stamp)
    assert db.record_message("1001:2", 1001, 8, "B", stamp)

    first = db.finalize_day(1001, date(2026, 7, 16))
    second = db.finalize_day(1001, date(2026, 7, 16))
    assert first == {"status": "completed", "row_count": 2}
    assert second["status"] == "already_completed"
    rows = db.history_top(1001, date(2026, 7, 16))
    assert [row["user_id"] for row in rows] == [7, 8]
    assert db.group_total_rows(1001)[0]["total_count"] == 1


def test_top_100_tie_breaks_by_user_id(tmp_path):
    db = make_db(tmp_path)
    stamp = datetime(2026, 7, 16, 12, 0)
    for user_id in range(1, 121):
        db.record_message(f"1001:{user_id}", 1001, user_id, str(user_id), stamp)
    db.finalize_day(1001, date(2026, 7, 16))
    rows = db.history_top(1001, date(2026, 7, 16))
    assert len(rows) == 100
    assert rows[0]["user_id"] == 1
    assert rows[-1]["user_id"] == 100
