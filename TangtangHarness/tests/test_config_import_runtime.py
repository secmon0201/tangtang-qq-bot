import json
import sqlite3
import pytest

from tangtang_harness.config import load_config
from tangtang_harness.config_import import import_config, prepare_config
from tangtang_harness.store import Store


def legacy(tmp_path):
    old = tmp_path / 'legacy'
    old.mkdir()
    new = old / 'TangtangHarness'
    text = '''BOT_OPERATOR_IDS=101,102
GLOBAL_ANNOUNCEMENT_OPERATOR_IDS=103
TANGTANG_ENABLED=true
TANGTANG_MEMORY_ENABLED=false
TANGTANG_GROUP_SUMMARY_ENABLED=false
TANGTANG_PROACTIVE_ENABLED=false
TANGTANG_GROUP_IDS=201,202
TANGTANG_MODEL_ACTIVE_PROFILE=备用档案
TANGTANG_MODEL_PROFILE_1_NAME=主要档案
TANGTANG_MODEL_PROFILE_1_MODEL=synthetic-a
TANGTANG_MODEL_PROFILE_1_API_URL=https://example.invalid/v1
TANGTANG_MODEL_PROFILE_1_API_KEY=synthetic-secret-a
TANGTANG_MODEL_PROFILE_1_API_STYLE=chat_completions
TANGTANG_MODEL_PROFILE_1_REASONING_EFFORT=high
TANGTANG_MODEL_PROFILE_1_CONTEXT_LIMIT=32768
TANGTANG_MODEL_PROFILE_2_NAME=备用档案
TANGTANG_MODEL_PROFILE_2_MODEL=synthetic-b
TANGTANG_MODEL_PROFILE_2_API_URL=https://example.invalid/v1
TANGTANG_MODEL_PROFILE_2_API_KEY=synthetic-secret-b
TANGTANG_MODEL_PROFILE_2_API_STYLE=responses
TANGTANG_MODEL_PROFILE_2_VISION_ENABLED=false
TANGTANG_MAX_OUTPUT_TOKENS=512
ASOUL_BILI_ENABLED=false
ASOUL_BILI_TARGET_UIDS=111,112
ASOUL_BILI_PUSH_COMMENT=false
HOURLY_ANNOUNCEMENT_ENABLED=false
HOURLY_ANNOUNCEMENT_START=08:00
BOT_RANDOM_REACTION_GROUP_IDS=201,202
BOT_RANDOM_REACTION_ENABLED=false
BOT_RANDOM_REPEAT_ENABLED=true
BOT_RANDOM_REPEAT_PROBABILITY=0.01,0.02
BOT_RANDOM_TRIPLE_REPEAT_ENABLED=false,true
CODEX_COMPLETION_NOTIFY_GROUP_ID=201
gsuid_core_botid=do-not-import
PERSONA_SPEECH_URL=http://127.0.0.1:9880
'''
    (old / '.env').write_text(text, encoding='utf-8')
    return old, new


def test_config_preview_prints_only_field_names_and_counts_and_writes_nothing(tmp_path):
    old, new = legacy(tmp_path)
    report = import_config(old, new)
    output = json.dumps(report, ensure_ascii=False)
    assert report['model_profile_count'] == 2 and report['secret_field_count'] == 2
    assert report['context_limits_requiring_review_count'] == 1
    assert 'synthetic-secret' not in output and 'example.invalid' not in output
    assert '备用档案' not in output and '201' not in output
    assert not new.exists()


def test_explicit_config_import_keeps_old_files_and_copies_secrets_only_to_new_env(tmp_path):
    old, new = legacy(tmp_path)
    before = (old / '.env').read_bytes()
    report = import_config(old, new, apply=True)
    config = load_config(new)
    assert report['status'] == 'imported' and (old / '.env').read_bytes() == before
    assert config.active_model == 'legacy-2' and config.profiles[0].reasoning_effort == 'high'
    assert config.profiles[0].context_limit == 32768 and config.profiles[1].context_limit is None
    assert config.profiles[1].vision is False and config.profiles[1].api_style == 'responses'
    assert config.mode == 'observe' and config.group_ids == () and config.extra['private_user_ids'] == []
    assert config.extra['context_limits_require_review'] == ['legacy-2']
    assert not config.memory_enabled and not config.summary_enabled and not config.proactive_enabled
    assert not config.cache_warmer_enabled and not config.profile_enabled and not config.speech_enabled
    assert 'synthetic-secret-a' in (new / '.env').read_text(encoding='utf-8')
    stored = (new / 'config/settings.json').read_text(encoding='utf-8')
    assert 'synthetic-secret' not in stored and 'do-not-import' not in stored and '9880' not in stored
    store = Store(new)
    assert store.get_setting('operator_ids') == [101, 102]
    assert store.get_setting('notification_group_id') == 201
    assert store.get_setting('bili_enabled') is False and store.get_setting('bili_push_comment') is False
    assert store.get_setting('passive:201')['repeat_probability'] == .01
    assert store.get_setting('passive:202')['triple_enabled'] is True
    assert store.get_setting('hourly_enabled') is False


def test_legacy_sqlite_disabled_flags_override_env_without_writing_legacy(tmp_path):
    old, new = legacy(tmp_path)
    (old / 'data/personas').mkdir(parents=True)
    with sqlite3.connect(old / 'data/bot.db') as conn:
        conn.execute('CREATE TABLE passive_settings(setting_key TEXT,setting_value TEXT)')
        conn.execute("INSERT INTO passive_settings VALUES('mention_chat_global_enabled','false')")
    with sqlite3.connect(old / 'data/personas/state.db') as conn:
        conn.execute('CREATE TABLE options(key TEXT,value TEXT)')
        conn.execute("INSERT INTO options VALUES('background_enabled','false')")
    with sqlite3.connect(old / 'data/personas/denia-history.db') as conn:
        conn.execute('CREATE TABLE person_memory_control(id INTEGER,enabled INTEGER)')
        conn.execute('INSERT INTO person_memory_control VALUES(1,0)')
    before = {path: path.read_bytes() for path in old.rglob('*.db')}
    import_config(old, new, apply=True)
    config = load_config(new)
    assert config.mention_chat_enabled is False and config.background_enabled is False and config.memory_enabled is False
    assert all(path.read_bytes() == contents for path, contents in before.items())


def test_repeat_config_import_preserves_other_new_env_variables(tmp_path):
    old, new = legacy(tmp_path)
    new.mkdir()
    (new / '.env').write_text('NEW_ONLY_SETTING=preserved\n', encoding='utf-8')
    import_config(old, new, apply=True)
    import_config(old, new, apply=True)
    text = (new / '.env').read_text(encoding='utf-8')
    assert 'NEW_ONLY_SETTING=preserved' in text and text.count('HARNESS_LEGACY_1_API_KEY=') == 1


def test_public_business_origin_imports_to_owned_database_without_exposing_values_in_preview(tmp_path):
    old, new = legacy(tmp_path)
    env = old / '.env'
    env.write_text(env.read_text(encoding='utf-8') + '''PUBLIC_SITE_BASE_URL=https://business.example.invalid///
PUBLIC_SHORT_HOST=links.example.invalid
''', encoding='utf-8')
    before = env.read_bytes()
    report = import_config(old, new)
    assert 'PUBLIC_SITE_BASE_URL' in report['source_fields'] and 'PUBLIC_SHORT_HOST' in report['source_fields']
    assert {'web_base_url', 'public_short_host', 'public_gateway_enabled'} <= set(report['setting_fields'])
    assert 'example.invalid' not in json.dumps(report) and not new.exists()
    import_config(old, new, apply=True)
    store = Store(new)
    assert store.get_setting('web_base_url') == 'https://business.example.invalid'
    assert store.get_setting('public_short_host') == 'links.example.invalid'
    assert store.get_setting('public_gateway_enabled') is True
    assert env.read_bytes() == before
    assert 'business.example.invalid' not in (new / 'config/settings.json').read_text(encoding='utf-8')
    assert 'links.example.invalid' not in (new / 'config/settings.json').read_text(encoding='utf-8')
    assert 'example.invalid' not in (new / '.env').read_text(encoding='utf-8')


@pytest.mark.parametrize('public_fields', ['', 'PUBLIC_SITE_BASE_URL=\nPUBLIC_SHORT_HOST=\n',
    'PUBLIC_SITE_BASE_URL="   "\nPUBLIC_SHORT_HOST="   "\n'])
def test_empty_public_business_config_does_not_invent_an_origin_or_enable_gateway(tmp_path, public_fields):
    old, new = legacy(tmp_path)
    env = old / '.env'
    env.write_text(env.read_text(encoding='utf-8') + public_fields, encoding='utf-8')
    _, _, settings, _ = prepare_config(old, new)
    assert not {'web_base_url', 'public_short_host', 'public_gateway_enabled'} & settings.keys()
    assert not new.exists()


def test_public_short_host_without_origin_does_not_enable_gateway(tmp_path):
    old, new = legacy(tmp_path)
    env = old / '.env'
    env.write_text(env.read_text(encoding='utf-8') + 'PUBLIC_SHORT_HOST=links.example.invalid\n', encoding='utf-8')
    _, _, settings, _ = prepare_config(old, new)
    assert settings['public_short_host'] == 'links.example.invalid'
    assert 'web_base_url' not in settings and 'public_gateway_enabled' not in settings


@pytest.mark.parametrize('dedicated', ['', 'synthetic-notification-secret'])
def test_notification_auth_and_operator_move_to_owned_harness_settings(tmp_path, dedicated):
    old, new = legacy(tmp_path)
    path = old / '.env'
    path.write_text(path.read_text(encoding='utf-8') + f'''CODEX_COMPLETION_NOTIFY_ENABLED=true
CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID=101
CODEX_COMPLETION_NOTIFY_TOKEN={dedicated}
ONEBOT_ACCESS_TOKEN=synthetic-onebot-secret
''', encoding='utf-8')
    before = path.read_bytes()
    report = import_config(old, new, apply=True)
    store = Store(new)
    token = dedicated or 'synthetic-onebot-secret'
    assert store.get_setting('notifications_enabled') is True
    assert store.get_setting('notification_operator_id') == 101
    assert store.get_setting('notification_token_env') == 'HARNESS_NOTIFICATION_TOKEN'
    assert token in (new / '.env').read_text(encoding='utf-8')
    assert 'HARNESS_NOTIFICATION_TOKEN' in (new / '.env').read_text(encoding='utf-8')
    assert token not in json.dumps(report) and token not in (new / 'config/settings.json').read_text(encoding='utf-8')
    assert path.read_bytes() == before


def test_bili_uid_source_defaults_are_imported_without_loading_legacy_code(tmp_path):
    old, new = legacy(tmp_path)
    env = old / '.env'
    env.write_text(env.read_text(encoding='utf-8').replace('ASOUL_BILI_TARGET_UIDS=111,112\n', ''), encoding='utf-8')
    (old / 'bot').mkdir()
    source = old / 'bot/config.py'
    source.write_text('''import os
raise RuntimeError("legacy code must not execute")
targets = os.getenv("ASOUL_BILI_TARGET_UIDS", "211, 212,213")
comments = os.getenv("ASOUL_BILI_COMMENT_TARGET_UIDS", "211,213")
other = os.getenv("ASOUL_BILI_ENABLED", "true")
''', encoding='utf-8')
    before = source.read_bytes(), env.read_bytes()
    _, _, settings, report = prepare_config(old, new)
    assert settings['bili_target_uids'] == ['211', '212', '213']
    assert settings['bili_comment_target_uids'] == ['211', '213']
    assert settings['bili_enabled'] is False
    assert report['source_default_fields'] == ['ASOUL_BILI_COMMENT_TARGET_UIDS', 'ASOUL_BILI_TARGET_UIDS']
    assert (source.read_bytes(), env.read_bytes()) == before and not new.exists()
    assert '211' not in json.dumps(report)


def test_bili_env_empty_and_explicit_uids_override_source_defaults(tmp_path):
    old, new = legacy(tmp_path)
    env = old / '.env'
    env.write_text(env.read_text(encoding='utf-8').replace('ASOUL_BILI_TARGET_UIDS=111,112', 'ASOUL_BILI_TARGET_UIDS=')
        + 'ASOUL_BILI_COMMENT_TARGET_UIDS=214\n', encoding='utf-8')
    (old / 'bot').mkdir()
    (old / 'bot/config.py').write_text('''import os
targets = os.getenv("ASOUL_BILI_TARGET_UIDS", "211,212")
comments = os.getenv("ASOUL_BILI_COMMENT_TARGET_UIDS", "211")
''', encoding='utf-8')
    _, _, settings, report = prepare_config(old, new)
    assert settings['bili_target_uids'] == []
    assert settings['bili_comment_target_uids'] == ['214']
    assert report['source_default_fields'] == []


def test_bili_uids_are_not_invented_without_literal_source_defaults(tmp_path):
    old, new = legacy(tmp_path)
    env = old / '.env'
    env.write_text(env.read_text(encoding='utf-8').replace('ASOUL_BILI_TARGET_UIDS=111,112\n', ''), encoding='utf-8')
    _, _, settings, _ = prepare_config(old, new)
    assert 'bili_target_uids' not in settings and 'bili_comment_target_uids' not in settings
    (old / 'bot').mkdir()
    (old / 'bot/config.py').write_text('''import os
targets = os.getenv("ASOUL_BILI_TARGET_UIDS", make_targets())
comments = os.getenv("ASOUL_BILI_COMMENT_TARGET_UIDS")
''', encoding='utf-8')
    _, _, settings, _ = prepare_config(old, new)
    assert 'bili_target_uids' not in settings and 'bili_comment_target_uids' not in settings


def test_persisted_global_switches_and_punishment_exceptions_migrate(tmp_path):
    old, new = legacy(tmp_path)
    env = old / '.env'
    env.write_text(env.read_text(encoding='utf-8') + '''GAME_API_ENABLED=true
STATS_REALTIME_ENABLED=false
GLOBAL_ANNOUNCEMENT_DEFAULT_CLUSTER=测试集群
''', encoding='utf-8')
    (old / 'data').mkdir()
    with sqlite3.connect(old / 'data/bot.db') as conn:
        conn.execute('CREATE TABLE passive_settings(setting_key TEXT,setting_value TEXT)')
        conn.executemany('INSERT INTO passive_settings VALUES(?,?)', [
            ('game_global_enabled', 'false'), ('game_api_enabled', 'false'),
            ('game_mute_disabled_group_ids', '201,202')])
    before = (old / 'data/bot.db').read_bytes()
    import_config(old, new, apply=True)
    store = Store(new)
    assert store.get_setting('mini_games_enabled') is False
    assert store.get_setting('game_api_enabled') is False
    assert store.get_setting('game_mute_disabled_group_ids') == [201, 202]
    assert store.get_setting('stats_realtime_enabled') is False
    assert store.get_setting('announcement_cluster') == '测试集群'
    assert (old / 'data/bot.db').read_bytes() == before


def test_database_added_passive_group_inherits_switches_and_persisted_template(tmp_path):
    old, new = legacy(tmp_path)
    (old / 'data').mkdir()
    with sqlite3.connect(old / 'data/bot.db') as conn:
        conn.execute('CREATE TABLE passive_settings(setting_key TEXT,setting_value TEXT)')
        conn.executemany('INSERT INTO passive_settings VALUES(?,?)', [
            ('reaction_probability', '0.0'), ('reaction_cooldown_seconds', '120'),
            ('repeat_probability', '0.0'), ('triple_repeat_enabled', 'false')])
        conn.execute('CREATE TABLE group_features(group_id INTEGER,feature_key TEXT,configured_enabled INTEGER)')
        conn.execute("INSERT INTO group_features VALUES(203,'passive_interaction',1)")
        conn.execute('CREATE TABLE passive_group_settings(group_id INTEGER,setting_key TEXT,setting_value TEXT)')
        conn.executemany('INSERT INTO passive_group_settings VALUES(?,?,?)', [
            (203, 'reaction_probability', '.25'), (203, 'triple_repeat_enabled', 'true')])
    _, _, settings, _ = prepare_config(old, new)
    # Persisted global templates override old env defaults; group rows override templates.
    assert settings['passive:201']['repeat_probability'] == 0
    migrated = settings['passive:203']
    assert migrated['repeat_enabled'] is True and migrated['reaction_enabled'] is False
    assert migrated['reaction_probability'] == .25 and migrated['reaction_cooldown'] == 120
    assert migrated['repeat_probability'] == 0 and migrated['triple_enabled'] is True


def test_absent_chat_and_comment_flags_preserve_disabled_legacy_defaults(tmp_path):
    old = tmp_path / 'legacy'
    old.mkdir()
    config, _, settings, _ = prepare_config(old, old / 'TangtangHarness')
    assert config.mention_chat_enabled is False and config.proactive_enabled is False
    assert config.compaction_enabled is False
    assert settings['bili_push_comment'] is False
    assert settings['mini_games_enabled'] is True and settings['game_api_enabled'] is True


def test_live_guard_parameters_are_available_to_independent_runtime(tmp_path):
    old, new = legacy(tmp_path)
    env = old / '.env'
    env.write_text(env.read_text(encoding='utf-8') + '''ZHIJIANG_LIVE_GUARD_ENABLED=false
ZHIJIANG_SCHEDULE_REFRESH_MINUTES=10
ZHIJIANG_LIVE_PAUSE_MINUTES=90
ZHIJIANG_SCHEDULE_LOOKAHEAD_DAYS=9
ZHIJIANG_SCHEDULE_TIMEOUT=8.5
''', encoding='utf-8')
    _, _, settings, _ = prepare_config(old, new)
    assert settings['live_guard_enabled'] is False
    assert settings['schedule_refresh_minutes'] == 10 and settings['live_pause_minutes'] == 90
    assert settings['schedule_lookahead_days'] == 9 and settings['schedule_timeout'] == 8.5


def test_saved_continuation_rules_migrate_without_reopening_old_windows(tmp_path):
    old, new = legacy(tmp_path)
    (old / 'data/personas').mkdir(parents=True)
    with sqlite3.connect(old / 'data/personas/state.db') as conn:
        conn.execute('CREATE TABLE options(key TEXT,value TEXT)')
        conn.executemany('INSERT INTO options VALUES(?,?)', [
            ('continuation_idle_seconds', '45'), ('continuation_hard_seconds', '420'),
            ('continuation_max_attempts', '6')])
    config, _, _, _ = prepare_config(old, new)
    assert config.extra['continuation'] == {'idle_seconds': 45, 'hard_seconds': 420, 'max_attempts': 6}
    assert not new.exists()


def test_persisted_hourly_switch_and_clock_override_environment_defaults(tmp_path):
    old, new = legacy(tmp_path)
    (old / 'data').mkdir()
    with sqlite3.connect(old / 'data/bot.db') as conn:
        conn.execute('CREATE TABLE hourly_announcement_settings(setting_key TEXT,setting_value TEXT)')
        conn.executemany('INSERT INTO hourly_announcement_settings VALUES(?,?)', [
            ('enabled', 'true'), ('start_minute', '0'), ('end_minute', '1380')])
    before = (old / 'data/bot.db').read_bytes()
    _, _, settings, _ = prepare_config(old, new)
    assert settings['hourly_enabled'] is True
    assert settings['hourly_start'] == '00:00' and settings['hourly_end'] == '23:00'
    assert (old / 'data/bot.db').read_bytes() == before and not new.exists()


def test_hourly_absent_database_uses_legacy_clock_defaults_and_disabled_switch(tmp_path):
    old = tmp_path / 'legacy'
    old.mkdir()
    _, _, settings, _ = prepare_config(old, old / 'TangtangHarness')
    assert settings['hourly_enabled'] is False
    assert settings['hourly_start'] == '00:00' and settings['hourly_end'] == '23:00'
