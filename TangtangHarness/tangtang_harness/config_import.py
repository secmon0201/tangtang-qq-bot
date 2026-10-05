"""Explicit config migration: read legacy values, write only the new ignored instance."""
from __future__ import annotations

import ast
import json
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, set_key

from .config import HarnessConfig, ModelProfile, save_config
from .store import Store


CHAT_FLAGS = {'TANGTANG_ENABLED': 'mention_chat_enabled', 'TANGTANG_PROACTIVE_ENABLED': 'proactive_enabled',
              'TANGTANG_MEMORY_ENABLED': 'memory_enabled', 'TANGTANG_PERSONA_STATE_ENABLED': 'cognition_enabled',
              'TANGTANG_GROUP_SUMMARY_ENABLED': 'summary_enabled', 'TANGTANG_CONTEXT_COMPACTION_ENABLED': 'compaction_enabled',
              'TANGTANG_HUMANIZE_ENABLED': 'humanize_enabled'}
CHAT_DEFAULTS = {'TANGTANG_ENABLED': False, 'TANGTANG_PROACTIVE_ENABLED': False,
    'TANGTANG_MEMORY_ENABLED': True, 'TANGTANG_PERSONA_STATE_ENABLED': True,
    'TANGTANG_GROUP_SUMMARY_ENABLED': True, 'TANGTANG_CONTEXT_COMPACTION_ENABLED': False,
    'TANGTANG_HUMANIZE_ENABLED': True}
BILI_FIELDS = {'ENABLED': 'enabled', 'TARGET_UIDS': 'target_uids', 'COMMENT_TARGET_UIDS': 'comment_target_uids',
               'PUSH_DYNAMIC': 'push_dynamic', 'PUSH_VIDEO': 'push_video', 'PUSH_LIVE': 'push_live',
               'PUSH_COMMENT': 'push_comment', 'RENDER_CARDS': 'render_cards', 'POLL_INTERVAL_SECONDS': 'poll_interval_seconds'}
PASSIVE_FIELDS = {'BOT_RANDOM_REACTION_ENABLED': ('reaction_enabled', bool),
    'BOT_RANDOM_REACTION_PROBABILITY': ('reaction_probability', float),
    'BOT_RANDOM_REACTION_COOLDOWN_SECONDS': ('reaction_cooldown', int),
    'BOT_RANDOM_REPEAT_ENABLED': ('repeat_enabled', bool),
    'BOT_RANDOM_REPEAT_PROBABILITY': ('repeat_probability', float),
    'BOT_RANDOM_REPEAT_COOLDOWN_SECONDS': ('repeat_cooldown', int),
    'BOT_RANDOM_REPEAT_MESSAGE_INTERVAL': ('repeat_interval', int),
    'BOT_RANDOM_TRIPLE_REPEAT_ENABLED': ('triple_enabled', bool),
    'BOT_RANDOM_TRIPLE_REPEAT_PROBABILITY': ('triple_probability', float)}


def boolean(value: Any) -> bool:
    text = str(value).strip().casefold()
    if text in {'1', 'true', 'yes', 'on', '开启', '开'}:
        return True
    if text in {'0', 'false', 'no', 'off', '关闭', '关'}:
        return False
    raise ValueError('配置开关必须是 true 或 false')


def csv_values(value: Any) -> list[str]:
    return [item.strip() for item in str(value or '').split(',') if item.strip()]


def legacy_bili_uid_defaults(legacy_root: Path) -> dict[str, str]:
    source = legacy_root / 'bot/config.py'
    if not source.is_file():
        return {}
    names = {'ASOUL_BILI_TARGET_UIDS', 'ASOUL_BILI_COMMENT_TARGET_UIDS'}
    defaults = {}
    for node in ast.walk(ast.parse(source.read_text(encoding='utf-8-sig'))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'os'
                and node.func.attr == 'getenv' and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant) and node.args[0].value in names
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            defaults[node.args[0].value] = node.args[1].value
    return defaults


def read_rows(path: Path, table: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return []
        return [dict(row) for row in conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"')]


def prepare_config(legacy_root: Path, root: Path) -> tuple[HarnessConfig, dict[str, str], dict[str, Any], dict[str, Any]]:
    values = {key: value for key, value in dotenv_values(legacy_root / '.env').items() if value is not None}
    used: set[str] = set()
    def get(key, default=''):
        if key in values:
            used.add(key)
        return values.get(key, default)
    flags = {target: boolean(get(key, CHAT_DEFAULTS[key])) for key, target in CHAT_FLAGS.items()}
    profiles, secrets, require_review = [], {}, []
    slots = sorted(int(match[1]) for key in values if (match := re.fullmatch(r'TANGTANG_MODEL_PROFILE_(\d+)_NAME', key)) and values[key].strip())
    prefixes = [(f'legacy-{slot}', f'TANGTANG_MODEL_PROFILE_{slot}_') for slot in slots]
    if not prefixes and get('TANGTANG_MODEL'):
        prefixes = [('legacy-default', 'TANGTANG_')]
    for profile_id, prefix in prefixes:
        def field(name, default=''):
            return get(prefix + name, default)
        name = field('NAME', profile_id)
        api_key = field('API_KEY')
        env_name = 'HARNESS_' + profile_id.upper().replace('-', '_') + '_API_KEY'
        if api_key:
            secrets[env_name] = api_key
        raw_limit = field('CONTEXT_LIMIT', get('TANGTANG_CONTEXT_LIMIT', ''))
        context_limit = int(raw_limit) if str(raw_limit).strip() else None
        if context_limit is None:
            require_review.append(profile_id)
        raw_vision = field('VISION_ENABLED', get('TANGTANG_VISION_ENABLED', 'true'))
        profiles.append(ModelProfile(id=profile_id, name=name, provider=field('PROVIDER', get('TANGTANG_PROVIDER', 'custom')),
            model=field('MODEL'), base_url=field('API_URL'), api_key_env=env_name if api_key else '',
            api_style=field('API_STYLE', get('TANGTANG_API_STYLE', 'responses')),
            reasoning_effort=field('REASONING_EFFORT', get('TANGTANG_REASONING_EFFORT', 'none')),
            max_output_tokens=int(field('MAX_OUTPUT_TOKENS', get('TANGTANG_MAX_OUTPUT_TOKENS', 512))),
            context_limit=context_limit, vision=boolean(raw_vision),
            timeout_seconds=float(get('TANGTANG_TIMEOUT_SECONDS', 30)),
            output_token_field=field('OUTPUT_TOKEN_FIELD', 'max_completion_tokens')))
    active_name = get('TANGTANG_MODEL_ACTIVE_PROFILE')
    active = next((item.id for item in profiles if item.name == active_name), profiles[0].id if profiles else '')
    extra: dict[str, Any] = {'isolated_scope_enabled': False, 'private_user_ids': [], 'test_prefix': '#harness',
        'context_limits_require_review': require_review,
        'context_limit_note': '导入档案的空 context_limit 必须填写真实供应商容量；不能按名称猜。'}
    settings: dict[str, Any] = {}
    public_base_url = get('PUBLIC_SITE_BASE_URL').strip().rstrip('/')
    if public_base_url:
        settings['web_base_url'] = public_base_url
        settings['public_gateway_enabled'] = True
    public_short_host = get('PUBLIC_SHORT_HOST').strip()
    if public_short_host:
        settings['public_short_host'] = public_short_host
    for field, key in (('BOT_OPERATOR_IDS', 'operator_ids'), ('GLOBAL_ANNOUNCEMENT_OPERATOR_IDS', 'announcement_ids')):
        settings[key] = [int(item) for item in csv_values(get(field))]
    target = get('CODEX_COMPLETION_NOTIFY_GROUP_ID')
    if target:
        settings['notification_group_id'] = int(target)
    settings['notifications_enabled'] = boolean(get('CODEX_COMPLETION_NOTIFY_ENABLED', 'false'))
    operator = get('CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID')
    if operator:
        settings['notification_operator_id'] = int(operator)
    notification_token = get('CODEX_COMPLETION_NOTIFY_TOKEN').strip() or get('ONEBOT_ACCESS_TOKEN').strip()
    settings['notification_token_env'] = 'HARNESS_NOTIFICATION_TOKEN'
    if notification_token:
        secrets['HARNESS_NOTIFICATION_TOKEN'] = notification_token
    settings['stats_realtime_enabled'] = boolean(get('STATS_REALTIME_ENABLED', 'true'))
    if 'GLOBAL_ANNOUNCEMENT_DEFAULT_CLUSTER' in values:
        settings['announcement_cluster'] = get('GLOBAL_ANNOUNCEMENT_DEFAULT_CLUSTER')
    bili_defaults = legacy_bili_uid_defaults(legacy_root)
    for suffix, key in BILI_FIELDS.items():
        name = 'ASOUL_BILI_' + suffix
        if name not in values and name not in bili_defaults:
            continue
        raw = get(name, bili_defaults.get(name, ''))
        settings['bili_' + key] = csv_values(raw) if key.endswith('uids') else int(raw) if key == 'poll_interval_seconds' else boolean(raw)
    settings.setdefault('bili_push_comment', False)
    for name, key in (('HOURLY_ANNOUNCEMENT_ENABLED', 'hourly_enabled'), ('HOURLY_ANNOUNCEMENT_START', 'hourly_start'),
                      ('HOURLY_ANNOUNCEMENT_END', 'hourly_end'), ('ZHIJIANG_SCHEDULE_URL', 'schedule_url')):
        if name in values:
            raw = get(name)
            settings[key] = boolean(raw) if key.endswith('enabled') else raw
    for name, key, converter, default in (
        ('ZHIJIANG_LIVE_GUARD_ENABLED', 'live_guard_enabled', boolean, True),
        ('ZHIJIANG_SCHEDULE_REFRESH_MINUTES', 'schedule_refresh_minutes', int, 5),
        ('ZHIJIANG_LIVE_PAUSE_MINUTES', 'live_pause_minutes', int, 60),
        ('ZHIJIANG_SCHEDULE_LOOKAHEAD_DAYS', 'schedule_lookahead_days', int, 7),
        ('ZHIJIANG_SCHEDULE_TIMEOUT', 'schedule_timeout', float, 15),
    ):
        settings[key] = converter(get(name, default))
    business = legacy_root / get('BOT_DB_PATH', 'data/bot.db')
    passive = {row['setting_key']: row['setting_value'] for row in read_rows(business, 'passive_settings')}
    hourly = {row['setting_key']: row['setting_value'] for row in read_rows(business, 'hourly_announcement_settings')}
    settings['hourly_enabled'] = boolean(hourly.get('enabled', get('HOURLY_ANNOUNCEMENT_ENABLED', 'false')))
    for old_key, key, name, default in (
        ('start_minute', 'hourly_start', 'HOURLY_ANNOUNCEMENT_START', '00:00'),
        ('end_minute', 'hourly_end', 'HOURLY_ANNOUNCEMENT_END', '23:00'),
    ):
        if old_key in hourly:
            minutes = int(hourly[old_key])
            settings[key] = f'{minutes // 60:02d}:{minutes % 60:02d}'
        else:
            settings[key] = get(name, default)
    settings['mini_games_enabled'] = boolean(passive.get('game_global_enabled', 'true'))
    settings['game_api_enabled'] = boolean(passive.get('game_api_enabled', get('GAME_API_ENABLED', 'true')))
    settings['game_mute_disabled_group_ids'] = [int(item) for item in csv_values(passive.get('game_mute_disabled_group_ids', ''))]
    passive_aliases = {'reaction_probability': ('reaction_probability', float), 'reaction_cooldown_seconds': ('reaction_cooldown', int),
        'repeat_probability': ('repeat_probability', float), 'repeat_cooldown_seconds': ('repeat_cooldown', int),
        'repeat_message_interval': ('repeat_interval', int), 'triple_repeat_enabled': ('triple_enabled', bool),
        'triple_repeat_probability': ('triple_probability', float)}
    template = {}
    for old_key, (key, converter) in passive_aliases.items():
        if old_key in passive:
            template[key] = boolean(passive[old_key]) if converter is bool else converter(passive[old_key])
    groups = [int(item) for item in csv_values(get('BOT_RANDOM_REACTION_GROUP_IDS'))]
    for index, group in enumerate(groups):
        config = {}
        for name, (key, converter) in PASSIVE_FIELDS.items():
            items = csv_values(get(name))
            if not items:
                continue
            if len(items) not in {1, len(groups)}:
                raise ValueError(name + ' 的逐群值数量与群列表不一致')
            raw = items[index] if len(items) > 1 else items[0]
            config[key] = boolean(raw) if converter is bool else converter(raw)
        if 'BOT_RANDOM_REACTION_EMOJI_IDS' in values:
            config['emoji_ids'] = csv_values(get('BOT_RANDOM_REACTION_EMOJI_IDS'))
        config.update(template)
        settings['passive:' + str(group)] = config
    if 'mention_chat_global_enabled' in passive:
        flags['mention_chat_enabled'] = flags.get('mention_chat_enabled', True) and boolean(passive['mention_chat_global_enabled'])
    if 'proactive_chat_global_enabled' in passive:
        flags['proactive_enabled'] = flags.get('proactive_enabled', False) and boolean(passive['proactive_chat_global_enabled'])
    for row in read_rows(business, 'group_features'):
        if row['feature_key'] == 'passive_interaction' and row['configured_enabled']:
            settings.setdefault('passive:' + str(row['group_id']), dict(template))
    for row in read_rows(business, 'passive_group_settings'):
        old_key = row['setting_key']
        if old_key in passive_aliases:
            key, converter = passive_aliases[old_key]
            raw = row['setting_value']
            settings.setdefault('passive:' + str(row['group_id']), {})[key] = boolean(raw) if converter is bool else converter(raw)
    defaults = {'reaction_enabled': False, 'repeat_enabled': True, 'triple_enabled': False,
        'reaction_probability': .15, 'reaction_cooldown': 180, 'repeat_probability': .10,
        'repeat_cooldown': 900, 'repeat_interval': 50, 'triple_probability': .30}
    for name, (key, converter) in PASSIVE_FIELDS.items():
        items = csv_values(get(name))
        if items:
            defaults[key] = boolean(items[0]) if converter is bool else converter(items[0])
    defaults.update(template)
    for key, config in settings.items():
        if key.startswith('passive:'):
            for field, default in defaults.items():
                config.setdefault(field, default)
    options = {row['key']: json.loads(row['value']) for row in read_rows(legacy_root / 'data/personas/state.db', 'options')}
    memory_control = read_rows(legacy_root / 'data/personas/denia-history.db', 'person_memory_control')
    if memory_control:
        flags['memory_enabled'] = flags.get('memory_enabled', True) and bool(memory_control[0]['enabled'])
    if 'background_enabled' in options:
        flags['background_enabled'] = bool(options['background_enabled'])
    continuation = {key.removeprefix('continuation_'): options[key]
        for key in ('continuation_idle_seconds', 'continuation_hard_seconds', 'continuation_max_attempts')
        if key in options}
    if continuation:
        extra['continuation'] = continuation
    settings['legacy_persona_options'] = options
    settings['profile_worker_enabled'] = False
    settings['speech_profile_ai_enabled'] = False
    for name, key, converter in (('TANGTANG_IGNORE_PROBABILITY', 'call_ignore_probability', float),
        ('TANGTANG_CACHE_RECENT_ROUNDS', 'recent_rounds', int),
        ('TANGTANG_REPLY_MAX_BUBBLES', 'max_reply_bubbles', int), ('TANGTANG_MAX_RESPONSE_CHARS', 'max_reply_chars', int)):
        if name in values:
            if key in {'recent_rounds', 'max_reply_bubbles', 'max_reply_chars'}:
                flags[key] = converter(get(name))
            else:
                extra[key] = converter(get(name))
    chat_groups = csv_values(get('TANGTANG_GROUP_IDS'))
    by_group = {group: {} for group in chat_groups}
    for name, key, converter in (('TANGTANG_PROACTIVE_PROBABILITY', 'proactive_probability', float),
        ('TANGTANG_PROACTIVE_COOLDOWN_SECONDS', 'proactive_cooldown_seconds', int),
        ('TANGTANG_PROACTIVE_MESSAGE_INTERVAL', 'proactive_message_interval', int)):
        items = csv_values(get(name))
        if len(items) == 1:
            extra[key] = converter(items[0])
        elif items:
            if len(items) != len(chat_groups):
                raise ValueError(name + ' 的逐群值数量与群列表不一致')
            for group, raw in zip(chat_groups, items):
                by_group[group][key] = converter(raw)
    if any(by_group.values()):
        extra['proactive_by_group'] = by_group
    config = HarnessConfig(root=root.resolve(), mode='observe', profiles=tuple(profiles), active_model=active,
        group_ids=(), cache_warmer_enabled=False, profile_enabled=False, speech_enabled=False, extra=extra, **flags)
    # Reuse the new config's own value checks without loading the old application.
    raw_config = asdict(config)
    raw_config.pop('root')
    config = HarnessConfig.from_dict(raw_config, root=root)
    report = {'status': 'preview', 'source_fields': sorted(used), 'model_profile_count': len(profiles),
        'source_default_fields': sorted(name for name in bili_defaults if name not in values),
        'secret_field_count': len(secrets), 'context_limits_requiring_review_count': len(require_review),
        'setting_fields': sorted({'passive:<group>' if key.startswith('passive:') else key for key in settings}),
        'passive_group_setting_count': sum(key.startswith('passive:') for key in settings),
        'scope_group_count': 0, 'scope_private_user_count': 0,
        'legacy_writes': 0}
    return config, secrets, settings, report


def import_config(legacy_root: Path, root: Path, *, apply: bool = False) -> dict[str, Any]:
    legacy_root, root = Path(legacy_root).resolve(), Path(root).resolve()
    if root == legacy_root or not root.is_relative_to(legacy_root):
        raise ValueError('新配置目录必须位于旧仓库中的独立子目录')
    config, secrets, settings, report = prepare_config(legacy_root, root)
    if not apply:
        return report
    save_config(config)
    env_path = root / '.env'
    env_path.touch(exist_ok=True)
    for key, value in secrets.items():
        set_key(env_path, key, value, quote_mode='always')
    store = Store(root)
    for key, value in settings.items():
        store.set_setting(key, value)
    store.set_setting('config_import', {**report, 'status': 'imported'})
    return {**report, 'status': 'imported'}
