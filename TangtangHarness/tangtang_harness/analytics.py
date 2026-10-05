"""Read-only views of Harness requests, context and copied business data.

Request lists deliberately project scalar JSON fields in SQLite. Large prompts,
image bodies and provider raw responses belong to the existing detail endpoint.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
import csv
import io
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from .tools import TOOL_LABELS


BACKGROUND = {'memory', 'cognition', 'growth', 'summary', 'compaction', 'profile', 'profile_review'}
BACKGROUND_KIND_LABELS = {
    'memory': '整理长期记忆',
    'cognition': '整理认知与约定',
    'growth': '整理公共成长',
    'summary': '生成群话题摘要',
    'compaction': '压缩上下文快照',
    'profile': '生成个人画像',
    'profile_review': '复核个人画像',
}
STATUS_LABELS = {
    'queued': '排队中', 'running': '执行中', 'completed': '已完成', 'failed': '失败',
    'paused': '已暂停', 'budget_paused': '预算等待', 'disabled': '已停用', 'delivered': '已送达',
    'error': '错误', 'pending': '待发送', 'observed': '仅观察', 'complete': '已完成',
    'ok': '成功', 'clarification': '需要补充参数', 'denied': '权限不足', 'empty': '暂无数据',
    'blocked': '已过滤', 'forwarded': '已转发', 'quiet': '静默', 'file': '文件', 'silent': '静默',
    'generated': '已生成', 'cancelled': '已取消', 'active': '运行中', 'unknown': '未记录',
    'group_not_present': '已退群，停止处理', 'ignored_group_not_present': '忽略已退群消息',
}
SESSION_KIND_LABELS = {'group': '群', 'private': '私聊'}
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens',
                'cache_read_tokens', 'cache_write_tokens', 'cache_miss_tokens')


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left = int(position)
    return ordered[left] + (ordered[min(left + 1, len(ordered) - 1)] - ordered[left]) * (position - left)


class _Metrics:
    def __init__(self):
        self.count = 0
        self.sums = Counter()
        self.known = Counter()
        self.cache_inputs = 0
        self.cache_reads = 0
        self.cache_token_known = 0
        self.hits = 0
        self.token_known = 0
        self.background = 0
        self.latencies = []
        self.first_tokens = []
        self.stages = defaultdict(list)
        self.costs = defaultdict(lambda: {'known_cost': 0.0, 'known_requests': 0, 'request_count': 0})

    def add(self, row):
        self.count += 1
        self.background += row['purpose'] in BACKGROUND
        for field in TOKEN_FIELDS:
            if row.get(field) is not None:
                self.known[field] += 1
                self.sums[field] += row[field]
        inp, read, output = row.get('input_tokens'), row.get('cache_read_tokens'), row.get('output_tokens')
        self.token_known += inp is not None and output is not None
        self.hits += read is not None and read > 0
        if inp is not None and read is not None and 0 <= read <= inp:
            self.cache_inputs += inp
            self.cache_reads += read
            self.cache_token_known += 1
        if row.get('latency_ms') is not None:
            self.latencies.append(row['latency_ms'])
        if row.get('first_token_latency_ms') is not None:
            self.first_tokens.append(row['first_token_latency_ms'])
        for stage in ('context_ms', 'media_ms', 'route_ms'):
            if row.get(stage) is not None:
                self.stages[stage].append(row[stage])
        cost = self.costs[row.get('currency') or 'unknown']
        cost['request_count'] += 1
        if row.get('cost') is not None:
            cost['known_cost'] += row['cost']
            cost['known_requests'] += 1

    def value(self):
        currencies = [{'currency': name, **value,
                       'known_cost': round(value['known_cost'], 8) if value['known_requests'] else None,
                       'coverage_ratio': value['known_requests'] / value['request_count']}
                      for name, value in sorted(self.costs.items())]
        priced = [row for row in currencies if row['known_requests']]
        known_cost = priced[0]['known_cost'] if len(priced) == 1 else None
        cost_known = sum(row['known_requests'] for row in currencies)
        cache_known = self.known['cache_read_tokens']
        return {'request_count': self.count,
                **{key: self.sums[key] if self.known[key] else None for key in TOKEN_FIELDS},
                'cache_ratio': self.cache_reads / self.cache_inputs if self.cache_inputs else None,
                'request_hit_ratio': self.hits / cache_known if cache_known else None,
                'coverage_ratio': cache_known / self.count if self.count else None,
                'cache_known_requests': cache_known, 'cache_hit_requests': self.hits,
                'cache_token_known_requests': self.cache_token_known,
                'cache_token_coverage_ratio': self.cache_token_known / self.count if self.count else None,
                'cache_unknown_requests': self.count - cache_known,
                'cache_write_known_requests': self.known['cache_write_tokens'],
                'token_known_requests': self.token_known,
                'token_coverage_ratio': self.token_known / self.count if self.count else None,
                'known_cost': known_cost, 'cost': known_cost if cost_known == self.count and self.count else None,
                'currency': priced[0]['currency'] if len(priced) == 1 else None,
                'cost_known_requests': cost_known,
                'cost_coverage_ratio': cost_known / self.count if self.count else None,
                'cost_by_currency': currencies,
                'latency_ms': sum(self.latencies) / len(self.latencies) if self.latencies else None,
                'latency_p50_ms': percentile(self.latencies, .5),
                'latency_p95_ms': percentile(self.latencies, .95),
                'latency_known_requests': len(self.latencies),
                'first_token_latency_ms': percentile(self.first_tokens, .5),
                'first_token_known_requests': len(self.first_tokens),
                'stage_timings': {key: {'p50_ms': percentile(self.stages[key], .5),
                                        'p95_ms': percentile(self.stages[key], .95),
                                        'known_requests': len(self.stages[key])}
                                  for key in ('context_ms', 'media_ms', 'route_ms')},
                'background_requests': self.background,
                'background_ratio': self.background / self.count if self.count else None}


def _number(column, field):
    path = '$.' + field
    return f"CASE WHEN json_type({column},'{path}') IN ('integer','real') THEN json_extract({column},'{path}') END"


USAGE_PROJECTION = ', '.join(f"{_number('r.usage', field)} AS {field}" for field in (*TOKEN_FIELDS, 'cache_ratio', 'cost', 'first_token_latency_ms'))
REQUEST_PROJECTION = f"""r.id,r.started_at,r.ended_at,r.session_key,r.event_key,e.user_id,
    COALESCE(NULLIF(json_extract(e.payload,'$.sender.card'),''),json_extract(e.payload,'$.sender.nickname')) AS nickname,
    r.profile_id,r.model,r.provider,r.api_style,r.purpose,r.account,r.outcome,r.error,
    {USAGE_PROJECTION},
    COALESCE(json_extract(r.usage,'$.currency'),json_extract(r.usage,'$.cost_currency'),'unknown') AS currency,
    {_number('r.usage', 'latency_ms')} AS latency_ms,
    (r.ended_at-r.started_at)*1000 AS record_elapsed_ms,
    {_number('r.telemetry', 'estimated_input_tokens')} AS estimated_input_tokens,
    {_number('r.telemetry', 'input_budget_tokens')} AS input_budget_tokens,
    {_number('r.telemetry', 'input_tokens_before_trim')} AS input_tokens_before_trim,
    {_number('r.telemetry', 'reserved_output_tokens')} AS reserved_output_tokens,
    r.snapshot_revision,json_extract(r.telemetry,'$.static_prefix_hash') AS static_prefix_hash,
    json_array_length(r.telemetry,'$.trimmed_turn_ids') AS trimmed_turn_count,
    json_array_length(r.telemetry,'$.trimmed_event_keys') AS trimmed_event_count,
    json_extract(r.telemetry,'$.compaction_due') AS compaction_due,
    json_extract(r.telemetry,'$.job_id') AS job_id,
    json_array_length(r.telemetry,'$.assets') AS image_count,
    {_number('r.telemetry', 'stage_timings.context_ms')} AS context_ms,
    {_number('r.telemetry', 'stage_timings.media_ms')} AS media_ms,
    {_number('s.value', 'route_ms')} AS route_ms,
    json_extract(r.usage,'$.input_semantics') AS input_semantics,
    json_extract(r.usage,'$.output_semantics') AS output_semantics"""
REQUEST_FROM = "FROM requests r LEFT JOIN events e ON e.event_key=r.event_key LEFT JOIN settings s ON s.key='event_scope:'||r.event_key"


class Analytics:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = Path(runtime.config.root)
        self.path = Path(runtime.store.path)

    @contextmanager
    def _connect(self, path=None):
        connection = sqlite3.connect(Path(path or self.path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _has(connection, table):
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    @staticmethod
    def _filters(filters):
        result = dict(filters or {})
        if result.get('group_id') not in (None, '') and not result.get('session_key'):
            result['session_key'] = 'group:' + str(int(result['group_id']))
        return result

    @staticmethod
    def _where(filters):
        filters = Analytics._filters(filters)
        conditions, values = [], []
        if filters.get('actual_only', True) not in (False, 'false', '0', 0):
            conditions.append("r.outcome<>'observed'")
        for key, column in {'session_key': 'r.session_key', 'user_id': 'e.user_id',
                            'profile_id': 'r.profile_id', 'purpose': 'r.purpose', 'provider': 'r.provider',
                            'model': 'r.model', 'account': 'r.account', 'api_style': 'r.api_style', 'status': 'r.outcome'}.items():
            if filters.get(key) not in (None, ''):
                conditions.append(column + '=?')
                values.append(filters[key])
        if filters.get('group_id') not in (None, ''):
            conditions.append('r.session_key=?')
            values.append('group:' + str(int(filters['group_id'])))
        if filters.get('session_type'):
            conditions.append("substr(r.session_key,1,instr(r.session_key,':')-1)=?")
            values.append(filters['session_type'])
        if filters.get('source'):
            conditions.append("COALESCE(json_extract(r.telemetry,'$.data_source'),'harness')=?")
            values.append(filters['source'])
        for key, operator in (('after', '>='), ('before', '<')):
            if filters.get(key) not in (None, '', 0, '0'):
                conditions.append('r.started_at' + operator + '?')
                values.append(float(filters[key]))
        return ('WHERE ' + ' AND '.join(conditions) if conditions else ''), values

    @staticmethod
    def _page(offset, page_size):
        return max(0, int(offset)), max(1, min(200, int(page_size)))

    def requests(self, filters=None, offset=0, page_size=50, sort='started_at', descending=True):
        offset, page_size = self._page(offset, page_size)
        where, values = self._where(filters)
        sorts = {'started_at': 'r.started_at', 'input_tokens': _number('r.usage', 'input_tokens'),
                 'output_tokens': _number('r.usage', 'output_tokens'), 'cache_ratio': _number('r.usage', 'cache_ratio'),
                 'cost': _number('r.usage', 'cost'), 'latency_ms': _number('r.usage', 'latency_ms'),
                 'model': 'r.model', 'purpose': 'r.purpose', 'session_key': 'r.session_key', 'outcome': 'r.outcome'}
        if sort not in sorts:
            raise ValueError('不支持这个请求排序字段')
        direction = 'DESC' if descending else 'ASC'
        with self._connect() as connection:
            total = connection.execute(f'SELECT count(*) {REQUEST_FROM} {where}', values).fetchone()[0]
            items = [dict(row) for row in connection.execute(
                f'SELECT {REQUEST_PROJECTION} {REQUEST_FROM} {where} ORDER BY {sorts[sort]} {direction},r.id {direction} LIMIT ? OFFSET ?',
                (*values, page_size, offset))]
            names = self._group_names()
            for item in items:
                item.update(self._scope_identity(connection, item['session_key'], item.get('user_id'), names=names))
        return {'items': items, 'total': total, 'offset': offset, 'page_size': page_size, 'as_of': time.time()}

    def overview(self, filters=None):
        filters = self._filters(filters)
        where, values = self._where(filters)
        bucket = {'minute': 60, 'hour': 3600, 'day': 86400}.get(filters.get('bucket'), 86400)
        # Buckets follow the deployment's China timezone, including local midnight.
        zone_offset = 28800
        all_metrics, model_groups, purposes, sessions, series = _Metrics(), {}, {}, {}, {}
        states, distribution = Counter(), Counter({'hit': 0, 'miss': 0, 'unknown': 0})
        scatter = []
        with self._connect() as connection:
            for raw in connection.execute(f'SELECT {REQUEST_PROJECTION} {REQUEST_FROM} {where} ORDER BY r.started_at DESC,r.id DESC', values):
                row = dict(raw)
                all_metrics.add(row)
                model_key = tuple(row[key] for key in ('profile_id', 'model', 'provider', 'api_style', 'account'))
                model_groups.setdefault(model_key, _Metrics()).add(row)
                purposes.setdefault(row['purpose'], _Metrics()).add(row)
                sessions.setdefault(row['session_key'], _Metrics()).add(row)
                at = int((row['started_at'] + zone_offset) // bucket) * bucket - zone_offset
                series.setdefault(at, _Metrics()).add(row)
                states[row['outcome']] += 1
                read = row['cache_read_tokens']
                distribution['unknown' if read is None else 'hit' if read > 0 else 'miss'] += 1
                if len(scatter) < 1500 and row['input_tokens'] is not None and row['latency_ms'] is not None:
                    scatter.append({key: row[key] for key in ('id', 'input_tokens', 'latency_ms', 'cache_ratio')} | {'at': row['started_at']})
            names = self._group_names()
            by_session = [{'session_key': key, **value.value(), **self._scope_identity(connection, key, names=names)}
                          for key, value in sorted(sessions.items())]
        return {'metrics': all_metrics.value(),
                'series': [{'timestamp': at, **stats.value()} for at, stats in sorted(series.items())],
                'by_model': [dict(zip(('profile_id', 'model', 'provider', 'api_style', 'account'), key)) | value.value()
                             for key, value in sorted(model_groups.items())],
                'by_purpose': [{'purpose': key, **value.value()} for key, value in sorted(purposes.items())],
                'by_session': by_session,
                'cache_distribution': [{'name': key, 'count': value} for key, value in distribution.items()],
                'scatter': list(reversed(scatter)), 'scatter_limit': 1500,
                'scatter_is_sample': all_metrics.count > len(scatter),
                'states': [{'name': key, 'count': value} for key, value in sorted(states.items())],
                'as_of': time.time(), 'range': {'after': filters.get('after'), 'before': filters.get('before'),
                                             'bucket_seconds': bucket, 'timezone': 'Asia/Shanghai',
                                             'interval': '[after,before)'},
                'definitions': {'cache_ratio': '可同时判断输入与缓存读的 token 总量加权；缺失字段不补零',
                                'cost': '保存的请求估算费用；不同币种不相加，未知币种单列',
                                'actual_only': '默认排除观察记录，取消和失败尝试保留',
                                'latency': '模型调用耗时；首 token 只采用真实流式记录'}}

    def _setting(self, connection, key, default=None):
        row = connection.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _group_names(self):
        path = self.root / 'runtime/business.db'
        if not path.exists():
            return {}
        with self._connect(path) as connection:
            if not self._has(connection, 'managed_groups'):
                return {}
            return {f"group:{row['group_id']}": dict(row) for row in connection.execute(
                'SELECT group_id,group_name,alias,enabled,stats_enabled,stats_role FROM managed_groups')}

    @staticmethod
    def _scope_parts(session_key, user_id=None):
        """Return stable scope metadata for console rows.

        Background batches use keys such as ``group:123:456`` while normal
        sessions use ``group:123``.  Keeping the parser here makes every
        analytics endpoint display the same group/private distinction.
        """
        raw = str(session_key or '')
        kind, _, identity = raw.partition(':')
        if kind == 'group':
            pieces = identity.split(':', 1)
            group_id = pieces[0] if pieces and pieces[0].isdigit() else None
            member_id = pieces[1] if len(pieces) > 1 and pieces[1].isdigit() else user_id
            return kind, group_id, int(member_id) if str(member_id).isdigit() else None
        if kind == 'private':
            private_id = identity.split(':', 1)[0]
            return kind, None, int(private_id) if private_id.isdigit() else user_id
        return kind or 'unknown', None, user_id

    def _scope_identity(self, connection, session_key, user_id=None, names=None):
        kind, group_id, member_id = self._scope_parts(session_key, user_id)
        names = names or {}
        if kind == 'group':
            metadata = names.get('group:' + str(group_id), {})
            group_name = metadata.get('group_name') or '未记录完整群名'
            label = f'群：{group_name}（{group_id or "未知群号"}）'
            nickname = None
            if member_id is not None:
                event_session = f'group:{group_id}' if group_id is not None else str(session_key)
                nickname = connection.execute(
                    """SELECT COALESCE(NULLIF(json_extract(payload,'$.sender.card'),''),
                               json_extract(payload,'$.sender.nickname'))
                       FROM events WHERE (session_key=? OR session_key LIKE ? || ':%') AND user_id=?
                       ORDER BY id DESC LIMIT 1""", (event_session, event_session, member_id)
                ).fetchone()
                nickname = nickname[0] if nickname else None
                if not nickname:
                    nickname = self._saved_group_nickname(group_id, member_id)
            return {'scope_kind': '群', 'scope_type': 'group', 'group_id': group_id,
                    'group_name': group_name, 'user_id': member_id, 'user_nickname': nickname,
                    'session_type': 'group', 'title': group_name, 'alias': metadata.get('alias'),
                    'enabled': metadata.get('enabled'),
                    'scope_label': label + (f' · 成员：{nickname or "未记录昵称"}（{member_id}）' if member_id is not None else '')}
        if kind == 'private':
            nickname = None
            if member_id is not None:
                nickname_row = connection.execute(
                    """SELECT COALESCE(NULLIF(json_extract(payload,'$.sender.card'),''),
                               json_extract(payload,'$.sender.nickname'))
                       FROM events WHERE session_key=? AND user_id=?
                       ORDER BY id DESC LIMIT 1""", (session_key, member_id)
                ).fetchone()
                nickname = nickname_row[0] if nickname_row else None
            return {'scope_kind': '私聊', 'scope_type': 'private', 'group_id': None,
                    'group_name': None, 'user_id': member_id, 'user_nickname': nickname,
                    'session_type': 'private', 'title': nickname or '未记录昵称', 'alias': None, 'enabled': True,
                    'scope_label': f'私聊：{nickname or "未记录昵称"}（{member_id or "未知QQ号"}）'}
        return {'scope_kind': '未知', 'scope_type': kind, 'group_id': None,
                'group_name': None, 'user_id': member_id, 'user_nickname': None,
                'session_type': kind, 'title': str(session_key or '未记录范围'), 'alias': None, 'enabled': None,
                'scope_label': str(session_key or '未记录范围')}

    def _saved_group_nickname(self, group_id, user_id):
        path = self.root / 'runtime/business.db'
        if not path.exists() or group_id is None:
            return None
        with self._connect(path) as connection:
            if not self._has(connection, 'group_members'):
                return None
            row = connection.execute(
                "SELECT COALESCE(NULLIF(card,''),NULLIF(nickname,'')) FROM group_members "
                "WHERE group_id=? AND user_id=? AND active=1", (int(group_id), int(user_id))
            ).fetchone()
            return row[0] if row else None

    def sessions(self, filters=None):
        filters = self._filters(filters)
        metrics = {row['session_key']: row for row in self.overview(filters)['by_session']}
        names = self._group_names()
        conditions, values = [], []
        if filters.get('session_key'):
            conditions.append('s.session_key=?'); values.append(filters['session_key'])
        if filters.get('session_type'):
            conditions.append("substr(s.session_key,1,instr(s.session_key,':')-1)=?"); values.append(filters['session_type'])
        if filters.get('user_id') not in (None, ''):
            conditions.append('EXISTS(SELECT 1 FROM events e WHERE e.session_key=s.session_key AND e.user_id=?)')
            values.append(int(filters['user_id']))
        where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self._connect() as connection:
            rows = connection.execute(f"""SELECT s.*,
                (SELECT count(*) FROM events e WHERE e.session_key=s.session_key) AS event_count,
                (SELECT count(DISTINCT user_id) FROM events e WHERE e.session_key=s.session_key) AS user_count,
                (SELECT count(*) FROM turns t WHERE t.session_key=s.session_key) AS turn_count,
                (SELECT count(*) FROM snapshots n WHERE n.session_key=s.session_key) AS snapshot_count,
                (SELECT count(*) FROM memory_records m WHERE m.session_key=s.session_key AND m.status='active') AS memory_count,
                (SELECT count(*) FROM background_jobs b WHERE b.status='queued' AND
                 (b.session_key=s.session_key OR json_extract(b.source,'$.source_session')=s.session_key
                  OR substr(b.session_key,1,length(s.session_key)+1)=s.session_key||':')) AS queued_jobs
                FROM sessions s {where} ORDER BY s.updated_at DESC""", values).fetchall()
            items = []
            for raw in rows:
                row = dict(raw)
                metadata = names.get(row['session_key'], {})
                current = self._setting(connection, 'group_state:' + row['session_key'], {})
                scope = self._scope_identity(connection, row['session_key'], names=names)
                items.append({**row, **scope, 'key': row['session_key'], 'kind': row['session_key'].split(':')[0],
                              # Full QQ group name is the primary console title;
                              # aliases are for ranking commands and stay metadata.
                              'title': metadata.get('group_name') or scope['title'],
                              'kind_label': SESSION_KIND_LABELS.get(row['session_key'].split(':')[0], '未知'),
                              'scope_label': scope['scope_label'], 'group': metadata,
                              'group_name': scope['group_name'], 'metrics': metrics.get(row['session_key'], _Metrics().value()),
                              'has_summary': bool(current.get('summary')), 'summary_cursor': current.get('summary_cursor'),
                              'counts_scope': '本地已保存全部事件与资料；用量遵循当前筛选'})
        return {'items': items, 'as_of': time.time()}

    def users(self, session_key):
        with self._connect() as connection:
            rows = connection.execute("""SELECT user_id,count(*) AS event_count,max(timestamp) AS last_seen_at,
                (SELECT COALESCE(NULLIF(json_extract(p.payload,'$.sender.card'),''),json_extract(p.payload,'$.sender.nickname'))
                 FROM events p WHERE p.session_key=? AND p.user_id=e.user_id ORDER BY p.id DESC LIMIT 1) AS nickname
                FROM events e WHERE session_key=? GROUP BY user_id ORDER BY event_count DESC""", (session_key, session_key)).fetchall()
            members = {row['user_id']: dict(row) for row in rows}
            for row in connection.execute('SELECT user_id,count(*) AS memory_count FROM memory_records WHERE session_key=? GROUP BY user_id', (session_key,)):
                members.setdefault(row['user_id'], {'user_id': row['user_id'], 'nickname': None, 'event_count': 0, 'last_seen_at': None})['memory_count'] = row['memory_count']
            legacy_available = self._has(connection, 'legacy_rows')
            if legacy_available:
                scope, args = self._legacy_scope(session_key)
                for row in connection.execute(f"""SELECT CAST(json_extract(data,'$.user_id') AS INTEGER) AS user_id,count(*) AS legacy_memory_count
                    FROM legacy_rows WHERE table_name='person_semantic_memory' AND {scope}
                    GROUP BY user_id""", args):
                    if row['user_id']:
                        members.setdefault(row['user_id'], {'user_id': row['user_id'], 'nickname': None, 'event_count': 0, 'last_seen_at': None})['legacy_memory_count'] = row['legacy_memory_count']
        where, args = self._where({'session_key': session_key})
        with self._connect() as connection:
            for row in connection.execute(f'SELECT e.user_id,count(*) AS request_count {REQUEST_FROM} {where} GROUP BY e.user_id', args):
                if row['user_id'] in members:
                    members[row['user_id']]['request_count'] = row['request_count']
            names = self._group_names()
            for member in members.values():
                member.setdefault('memory_count', 0)
                member.setdefault('request_count', 0)
                member.setdefault('legacy_memory_count', 0 if legacy_available else None)
                member.update(self._scope_identity(connection, session_key, member.get('user_id'), names=names))
                member['session_key'] = session_key
        return {'items': list(members.values()), 'session_key': session_key,
                'legacy_available': legacy_available, 'as_of': time.time()}

    def session(self, session_key, filters=None):
        selected = {**(filters or {}), 'session_key': session_key}
        summary = self.overview(selected)
        with self._connect() as connection:
            current = self._setting(connection, 'group_state:' + session_key, {})
            snapshots = [dict(row) | {'content': json.loads(row['content'])} for row in connection.execute(
                'SELECT * FROM snapshots WHERE session_key=? ORDER BY revision DESC', (session_key,))]
            published, published_total = [], 0
            reconstructed_where = ''
            if self._has(connection, 'summary_versions'):
                published_total = connection.execute('SELECT count(*) FROM summary_versions WHERE session_key=?', (session_key,)).fetchone()[0]
                published = [dict(row) | {'summary': json.loads(row['content']), 'source_users': json.loads(row['source_users']),
                                          'source': 'published', 'published_at': row['created_at'], 'chronology_at': row['created_at']}
                             for row in connection.execute('SELECT * FROM summary_versions WHERE session_key=? ORDER BY revision DESC LIMIT 100', (session_key,))]
                reconstructed_where = ' AND NOT EXISTS(SELECT 1 FROM summary_versions v WHERE v.session_key=b.session_key AND v.job_id=b.id)'
            reconstructed_query = "FROM background_jobs b WHERE b.session_key=? AND b.kind='summary' AND b.status='completed'" + reconstructed_where
            reconstructed_total = connection.execute('SELECT count(*) ' + reconstructed_query, (session_key,)).fetchone()[0]
            reconstructed = [dict(row) | {'summary': json.loads(row['result']), 'source': 'reconstructed',
                                          'published_at': None, 'completed_at': row['updated_at'], 'chronology_at': row['updated_at']}
                             for row in connection.execute('SELECT b.id,b.created_at,b.updated_at,b.result ' + reconstructed_query + ' ORDER BY b.updated_at DESC LIMIT 100', (session_key,))]
            history = sorted([*published, *reconstructed], key=lambda row: row['chronology_at'], reverse=True)[:100]
            history_source = 'published_and_reconstructed' if reconstructed and published else 'completed_summary_jobs' if reconstructed else 'summary_versions'
            pending = connection.execute('SELECT count(*) FROM events WHERE session_key=? AND id>?',
                                         (session_key, current.get('summary_cursor', 0))).fetchone()[0]
            latest = connection.execute("SELECT id FROM requests WHERE session_key=? AND purpose IN ('chat','proactive') AND outcome<>'observed' ORDER BY started_at DESC LIMIT 1", (session_key,)).fetchone()
            layers = []
            adopted = None
            if latest:
                adopted = latest['id']
                # Extract summaries only: no layer text, no retained media encoding.
                layers = [dict(row) for row in connection.execute("""SELECT json_extract(j.value,'$.name') AS name,
                    json_extract(j.value,'$.estimated_tokens') AS estimated_tokens,json_extract(j.value,'$.stable') AS stable,
                    json_extract(j.value,'$.source') AS source FROM requests r,json_each(r.telemetry,'$.layers') j WHERE r.id=?""", (latest['id'],))]
            archive = {}
            if self._has(connection, 'legacy_rows'):
                scope, args = self._legacy_scope(session_key)
                archive = {row['table_name']: row['count'] for row in connection.execute(
                    f"SELECT table_name,count(*) AS count FROM legacy_rows WHERE {scope} GROUP BY table_name", args)}
        session_rows = self.sessions(selected)['items']
        stats = self._speech_stats(session_key, selected.get('user_id'), filters or {})
        window_view = self.windows({**(filters or {}), 'session_key': session_key})
        return {'session': session_rows[0] if session_rows else {'session_key': session_key, 'title': session_key},
                'metrics': summary['metrics'], 'requests': self.requests(selected, page_size=50)['items'],
                'series': summary['series'], 'snapshots': snapshots,
                'windows': window_view['items'],
                'summary': current, 'summary_history': history, 'summary_history_source': history_source,
                'summary_history_total': published_total + reconstructed_total, 'summary_history_limit': 100,
                'summary_pending_events': pending,
                'last_context_request_id': adopted, 'last_context_layers': layers,
                'archive_counts': archive, 'speech': stats, 'as_of': time.time(),
                'scope_note': '归档资料不等于当前采用；本轮采用项以实发请求分层为准'}

    def windows(self, filters=None):
        """Return context-epoch cost and cache views for the selected sessions.

        Epochs are read from the request telemetry captured at the actual send
        boundary.  A missing epoch is kept under ``unknown`` so imported or
        older requests do not silently disappear from totals.
        """
        filters = self._filters(filters)
        where, values = self._where(filters)
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        with self._connect() as connection:
            query = f"SELECT r.session_key,r.started_at,r.ended_at,r.outcome,r.purpose,r.usage,r.telemetry {REQUEST_FROM} {where} ORDER BY r.started_at"
            for raw in connection.execute(query, values):
                row = dict(raw)
                telemetry = json.loads(row['telemetry']) if isinstance(row['telemetry'], str) else (row['telemetry'] or {})
                usage = json.loads(row['usage']) if isinstance(row['usage'], str) else (row['usage'] or {})
                epoch = telemetry.get('context_epoch')
                key = (row['session_key'], str(epoch) if epoch is not None else 'unknown')
                group = groups.setdefault(key, {
                    'session_key': row['session_key'], 'epoch': epoch, 'request_count': 0,
                    'started_at': row['started_at'], 'last_request_at': row['started_at'],
                    'outcomes': Counter(), 'purposes': Counter(), 'input_tokens': 0,
                    'cache_read_tokens': 0, 'cache_write_tokens': 0, 'output_tokens': 0,
                    'known': Counter(), 'hit_cost': 0.0, 'miss_cost': 0.0,
                    'write_cost': 0.0, 'output_cost': 0.0, 'known_cost_requests': 0,
                    'billing_sources': Counter(), 'cost_known': Counter(), 'snapshot_revisions': set(),
                })
                group['request_count'] += 1
                group['last_request_at'] = max(group['last_request_at'], row['started_at'])
                group['outcomes'][row['outcome']] += 1
                group['purposes'][row['purpose']] += 1
                for field in ('input_tokens', 'cache_read_tokens', 'cache_write_tokens', 'output_tokens'):
                    value = usage.get(field)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        group[field] += value
                        group['known'][field] += 1
                billing = usage.get('billing') if isinstance(usage.get('billing'), dict) else {}
                if billing:
                    group['billing_sources'][billing.get('cost_status', 'unknown')] += 1
                    for field in ('hit_cost', 'miss_cost', 'write_cost', 'output_cost'):
                        value = billing.get(field)
                        if isinstance(value, (int, float)):
                            group[field] += value
                            group['cost_known'][field] += 1
                    if isinstance(billing.get('total_cost'), (int, float)):
                        group['known_cost_requests'] += 1
                elif isinstance(usage.get('cost'), (int, float)):
                    group['known_cost_requests'] += 1
                    group['output_cost'] += float(usage['cost'])
                    group['billing_sources']['legacy_total'] += 1
                group['snapshot_revisions'].add(telemetry.get('snapshot_revision'))
        items = []
        for group in groups.values():
            count = group['request_count']
            known_input = group['known']['input_tokens']
            known_read = group['known']['cache_read_tokens']
            ratio = group['cache_read_tokens'] / group['input_tokens'] if known_input == count and known_read == count and group['input_tokens'] else None
            known_cost = group['known_cost_requests']
            values = {field: round(group[field], 8) if group['known'][field] else None
                      for field in ('input_tokens', 'cache_read_tokens', 'cache_write_tokens', 'output_tokens')}
            items.append({
                'session_key': group['session_key'], 'epoch': group['epoch'],
                'request_count': count, 'started_at': group['started_at'],
                'last_request_at': group['last_request_at'], 'outcomes': dict(group['outcomes']),
                'purposes': dict(group['purposes']), **values,
                'cache_ratio': ratio, 'cache_coverage_ratio': known_read / count if count else None,
                # A partial component total is not a reliable window amount.
                # Keep it unknown until every request in the segment reported
                # that component; the per-component coverage below explains
                # why a value is unavailable.
                'hit_cost': round(group['hit_cost'], 8) if group['cost_known']['hit_cost'] == count else None,
                'miss_cost': round(group['miss_cost'], 8) if group['cost_known']['miss_cost'] == count else None,
                'write_cost': round(group['write_cost'], 8) if group['cost_known']['write_cost'] == count else None,
                'output_cost': round(group['output_cost'], 8) if group['cost_known']['output_cost'] == count else None,
                'cost_component_coverage': {
                    field: group['cost_known'][field] / count if count else None
                    for field in ('hit_cost', 'miss_cost', 'write_cost', 'output_cost')
                },
                'known_cost_requests': known_cost, 'cost_coverage_ratio': known_cost / count if count else None,
                'billing_sources': dict(group['billing_sources']),
                'snapshot_revisions': sorted(x for x in group['snapshot_revisions'] if x is not None),
                'status': 'active' if group['outcomes'].get('running') else 'observed' if group['outcomes'].get('observed') else 'completed',
            })
        items.sort(key=lambda item: (item['last_request_at'], item['started_at']), reverse=True)
        with self._connect() as connection:
            names = self._group_names()
            for item in items:
                item.update(self._scope_identity(connection, item['session_key'], names=names))
        return {'items': items, 'total': len(items), 'as_of': time.time(),
                'definitions': {'epoch': '请求实际构建时记录的活动上下文段；旧记录无该字段显示 unknown',
                                'cache_ratio': '缓存读 token / 总输入 token；缺失字段不补零',
                                'cost': 'usage 中的分项配置估算；不是供应商最终账单'}}

    @staticmethod
    def _legacy_scope(session_key, alias=''):
        data = alias + 'data'
        if session_key.startswith('group:'):
            return f"COALESCE(NULLIF(json_extract({data},'$.scope_group'),0),NULLIF(json_extract({data},'$.group_id'),0),json_extract({data},'$.source_group'))=?", [int(session_key.split(':')[1])]
        return f"CAST(json_extract({data},'$.user_id') AS INTEGER)=?", [int(session_key.split(':')[1])]

    def records(self, session_key, user_id, offset=0, page_size=50):
        offset, page_size = self._page(offset, page_size)
        user_id = int(user_id)
        if session_key.startswith('private:') and user_id != int(session_key.split(':')[1]):
            raise ValueError('私聊资料只能查看该私聊用户')
        values = (session_key, user_id)
        result, totals = {}, {}
        with self._connect() as connection:
            queries = {
                'memories': ('SELECT * FROM memory_records WHERE session_key=? AND user_id=? ORDER BY updated_at DESC', values),
                'memory_versions': ('SELECT v.* FROM memory_versions v JOIN memory_records m ON m.id=v.memory_id WHERE m.session_key=? AND m.user_id=? ORDER BY v.created_at DESC', values),
                'cognition': ('SELECT * FROM cognition_records WHERE session_key=? AND user_id=? ORDER BY updated_at DESC', values),
                'cognition_versions': ('SELECT v.* FROM cognition_versions v JOIN cognition_records c ON c.id=v.record_id WHERE c.session_key=? AND c.user_id=? ORDER BY v.created_at DESC', values),
                'growth': ("SELECT * FROM growth_records WHERE scope IN ('global',?) ORDER BY updated_at DESC", (session_key,)),
                'growth_versions': ("SELECT v.* FROM growth_versions v JOIN growth_records g ON g.id=v.growth_id WHERE g.scope IN ('global',?) ORDER BY v.created_at DESC", (session_key,)),
                'impressions': ('SELECT * FROM impression_events WHERE session_key=? AND user_id=? ORDER BY created_at DESC', values),
            }
            for name, (query, args) in queries.items():
                totals[name] = connection.execute('SELECT count(*) FROM (' + query + ')', args).fetchone()[0]
                result[name] = [dict(row) for row in connection.execute(query + ' LIMIT ? OFFSET ?', (*args, page_size, offset))]
            prefix = f'profile_version:{session_key}:{user_id}:'
            totals['profiles'] = connection.execute('SELECT count(*) FROM settings WHERE substr(key,1,?)=?', (len(prefix), prefix)).fetchone()[0]
            result['profiles'] = [json.loads(row['value']) for row in connection.execute(
                "SELECT value FROM settings WHERE substr(key,1,?)=? ORDER BY CAST(json_extract(value,'$.version') AS INTEGER) DESC LIMIT ? OFFSET ?", (len(prefix), prefix, page_size, offset))]
            result['current_profile'] = self._setting(connection, f'profile:{session_key}:{user_id}')
            legacy = {}
            if self._has(connection, 'legacy_rows'):
                scope, scope_args = self._legacy_scope(session_key)
                tables = ('person_semantic_memory', 'persona_portraits', 'person_facts', 'person_relations', 'persona_state_factors', 'persona_intents')
                for table in tables:
                    clause = f"table_name=? AND CAST(json_extract(data,'$.user_id') AS INTEGER)=? AND {scope}"
                    args = (table, user_id, *scope_args)
                    total = connection.execute('SELECT count(*) FROM legacy_rows WHERE ' + clause, args).fetchone()[0]
                    items = [dict(row) | {'data': json.loads(row['data'])} for row in connection.execute(
                        'SELECT origin,table_name,row_key,source_hash,data,imported_at FROM legacy_rows WHERE ' + clause + ' ORDER BY imported_at DESC,row_key LIMIT ? OFFSET ?', (*args, page_size, offset))]
                    legacy[table] = {'items': items, 'total': total, 'offset': offset, 'page_size': page_size}
                parent_scope, parent_args = self._legacy_scope(session_key, 'p.')
                for table in ('person_semantic_versions', 'person_semantic_evidence'):
                    source = f"""FROM legacy_rows v JOIN legacy_rows p ON p.origin=v.origin AND p.table_name='person_semantic_memory'
                        AND json_extract(p.data,'$.id')=json_extract(v.data,'$.memory_id')
                        WHERE v.table_name=? AND CAST(json_extract(p.data,'$.user_id') AS INTEGER)=? AND {parent_scope}"""
                    args = (table, user_id, *parent_args)
                    total = connection.execute('SELECT count(*) ' + source, args).fetchone()[0]
                    items = [dict(row) | {'data': json.loads(row['data'])} for row in connection.execute(
                        'SELECT v.origin,v.table_name,v.row_key,v.data,v.imported_at ' + source + ' ORDER BY v.imported_at DESC,v.row_key LIMIT ? OFFSET ?', (*args, page_size, offset))]
                    legacy[table] = {'items': items, 'total': total, 'offset': offset, 'page_size': page_size}
            result['legacy'] = legacy
        return {**result, 'totals': totals, 'session_key': session_key, 'user_id': user_id,
                'offset': offset, 'page_size': page_size, 'as_of': time.time(),
                'profile_enabled': self.runtime.config.profile_enabled,
                'speech': self._speech_stats(session_key, user_id, {}),
                'scope_note': '本人和当前会话资料；旧归档版本不是当前采用版本，公共成长包含全局范围'}

    def _speech_stats(self, session_key, user_id, filters):
        path = self.root / 'runtime/business-history.db'
        if not path.exists():
            return {'available': False, 'source': 'business-history.db', 'message_count': None}
        from .analytics_business import BusinessAnalytics
        if not hasattr(self, '_business'):
            self._business = BusinessAnalytics(self.runtime)
        selected = {**filters, 'session_key': session_key}
        if user_id not in (None, ''):
            selected['user_id'] = int(user_id)
        stats = self._business.speech_stats(selected)
        return {**stats, 'available': True, 'source': 'business_analytics',
                'message_count': stats['sample']['count'],
                'first_at': stats['sample']['first_at'], 'last_at': stats['sample']['last_at'],
                'days': [{'day': row['date'], 'message_count': row['count']} for row in stats['daily']],
                'heatmap': [{'weekday': row['weekday'], 'hour': row['hour'], 'message_count': row['count']} for row in stats['hourly']],
                'groups': [{'group_id': row['group_id'], 'group_name': row['group_name'], 'message_count': row['count']} for row in stats['by_group']],
                'scope_note': '日期与群曲线来自去重发言计数；小时分布和画像规则来自保留正文，两种来源不能相加'}

    def jobs(self, filters=None, offset=0, page_size=50):
        filters = self._filters(filters)
        offset, page_size = self._page(offset, page_size)
        conditions, values = [], []
        for key, column in (('status', 'b.status'), ('purpose', 'b.kind')):
            if filters.get(key):
                conditions.append(column + '=?'); values.append(filters[key])
        if filters.get('session_key'):
            conditions.append("(b.session_key=? OR json_extract(b.source,'$.source_session')=? OR substr(b.session_key,1,length(?)+1)=?||':')")
            values.extend([filters['session_key']] * 4)
        if filters.get('user_id') not in (None, ''):
            conditions.append("(json_extract(b.source,'$.user_id')=? OR json_extract(b.source,'$.event.user_id')=? OR EXISTS(SELECT 1 FROM json_each(b.source,'$.events') j WHERE json_extract(j.value,'$.user_id')=?))")
            values.extend([int(filters['user_id'])] * 3)
        for key, op in (('after', '>='), ('before', '<')):
            if filters.get(key) not in (None, '', 0, '0'):
                conditions.append('b.created_at' + op + '?'); values.append(float(filters[key]))
        model_conditions, model_values = [], []
        for key in ('profile_id', 'model', 'provider', 'account', 'api_style'):
            if filters.get(key):
                model_conditions.append('r.' + key + '=?'); model_values.append(filters[key])
        if model_conditions:
            conditions.append("EXISTS(SELECT 1 FROM requests r WHERE r.purpose IN ('memory','cognition','growth','summary','compaction','profile','profile_review') AND json_extract(r.telemetry,'$.job_id')=b.id AND " + ' AND '.join(model_conditions) + ')')
            values.extend(model_values)
        where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
        now = time.time()
        with self._connect() as connection:
            total = connection.execute('SELECT count(*) FROM background_jobs b ' + where, values).fetchone()[0]
            counts = [dict(row) for row in connection.execute('SELECT b.kind,b.status,count(*) AS count FROM background_jobs b ' + where + ' GROUP BY b.kind,b.status', values)]
            items = [dict(row) for row in connection.execute(f"""SELECT b.id,b.kind,b.session_key,b.status,b.created_at,b.updated_at,
                json_array_length(b.source,'$.events') AS source_event_count,json_array_length(b.source,'$.turns') AS source_turn_count,
                json_extract(b.source,'$.source_session') AS source_session_key,
                json_extract(b.source,'$.user_id') AS user_id,json_extract(b.result,'$.error') AS error
                FROM background_jobs b {where} ORDER BY b.created_at DESC,b.id DESC LIMIT ? OFFSET ?""", (*values, page_size, offset))]
            linked = defaultdict(list)
            if items:
                ids = [item['id'] for item in items]
                for row in connection.execute("SELECT id,started_at,json_extract(telemetry,'$.job_id') AS job_id FROM requests WHERE purpose IN ('memory','cognition','growth','summary','compaction','profile','profile_review') AND json_extract(telemetry,'$.job_id') IN (" + ','.join('?' for _ in ids) + ') ORDER BY started_at', ids):
                    linked[row['job_id']].append(dict(row))
            group_names = self._group_names()
            for item in items:
                attempts = linked[item['id']]
                scope = self._scope_identity(connection, item.get('source_session_key') or item['session_key'], item.get('user_id'), names=group_names)
                item['request_id'] = attempts[-1]['id'] if attempts else None
                item['first_started_at'] = attempts[0]['started_at'] if attempts else None
                item['request_count'] = len(attempts)
                item['queue_age_seconds'] = now - item['created_at'] if item['status'] == 'queued' else None
                item['wait_seconds'] = item['first_started_at'] - item['created_at'] if item['first_started_at'] else None
                item['timing_source'] = 'request_link' if item['first_started_at'] else 'unreported'
                item.update(scope)
                item['kind_label'] = BACKGROUND_KIND_LABELS.get(item['kind'], item['kind'])
                item['status_label'] = STATUS_LABELS.get(item['status'], item['status'])
                if item['status'] == 'queued' and scope.get('enabled') == 0:
                    item['status_label'] = '已退群，暂停执行'
            if self._has(connection, 'job_timings') and items:
                timing_map = {row['job_id']: dict(row) for row in connection.execute(
                    'SELECT * FROM job_timings WHERE job_id IN (' + ','.join('?' for _ in items) + ')', [item['id'] for item in items])}
                for item in items:
                    if item['id'] in timing_map:
                        timing = timing_map[item['id']]
                        item.update({key: timing.get(key) for key in ('started_at', 'ended_at', 'state', 'reason', 'attempts')})
                        item['execution_ms'] = (timing['ended_at'] - timing['started_at']) * 1000 if timing.get('ended_at') and timing.get('started_at') else None
                        item['timing_source'] = 'job_timings'
            cache_first = self.runtime.config.extra.get('context_mode', 'cache_first') == 'cache_first'
            for item in items:
                reason = item.get('reason') or item.get('error') or ''
                item['reason_label'] = {'rolling_hour_budget': '等待滚动小时预算释放'}.get(reason, reason)
                if item['status'] == 'queued' and item.get('enabled') == 0:
                    item['reason_label'] = '机器人已退出该群，自动任务已暂停'
                if item['status'] == 'queued' and cache_first and item.get('enabled') != 0:
                    item['status_label'] = '缓存主路径已停用自动 AI 整理'
                    item['reason_label'] = '保留任务记录；当前模式不执行'
            budget_where, budget_args = self._where({'after': now - 3600})
            rows = [dict(row) for row in connection.execute(f"SELECT {REQUEST_PROJECTION} {REQUEST_FROM} {budget_where} AND r.purpose IN ('memory','cognition','growth','summary','compaction','profile','profile_review')", budget_args)]
            actual_tokens = sum(row['total_tokens'] for row in rows if row['total_tokens'] is not None)
            reserved = sum((row['estimated_input_tokens'] or 0) + (row['reserved_output_tokens'] or 0) for row in rows if row['total_tokens'] is None)
            budget = {'window_seconds': 3600, 'requests': len(rows), 'request_limit': self.runtime.config.background_requests_per_hour,
                      'tokens': actual_tokens + reserved, 'known_tokens': actual_tokens, 'estimated_reserved_tokens': reserved,
                      'token_limit': self.runtime.config.background_tokens_per_hour,
                      'enabled': self.runtime.config.background_enabled and not cache_first,
                      'saved_enabled': self.runtime.config.background_enabled,
                      'disabled_reason': 'cache_first' if cache_first else '',
                      'last_scheduler_state': self._setting(connection, 'background_budget', {}),
                      'scope_note': '全局滚动小时；失败和未知 usage 预留也计入预算'}
            budget['admission_paused'] = budget['requests'] >= budget['request_limit'] or budget['tokens'] >= budget['token_limit']
        return {'items': items, 'total': total, 'counts': counts, 'budget': budget, 'offset': offset,
                'page_size': page_size, 'as_of': now, 'scope_note': '等待时间自任务建立；合并任务保留建立时间'}

    def tools(self, filters=None):
        filters = self._filters(filters)
        path = self.root / 'runtime/business.db'
        if not path.exists():
            return {'items': [], 'series': [], 'coverage': 'unavailable', 'as_of': time.time()}
        conditions, values = [], []
        if filters.get('session_key'):
            if filters['session_key'].startswith('group:'):
                conditions.append('group_id=?'); values.append(int(filters['session_key'].split(':')[1]))
            else:
                conditions.append('group_id IS NULL AND user_id=?'); values.append(int(filters['session_key'].split(':')[1]))
        for key in ('user_id', 'status'):
            if filters.get(key) not in (None, ''):
                conditions.append(key + '=?'); values.append(filters[key])
        for key, op in (('after', '>='), ('before', '<')):
            if filters.get(key) not in (None, '', 0, '0'):
                conditions.append('unixepoch(created_at)' + op + '?'); values.append(float(filters[key]))
        where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self._connect(path) as connection:
            if not self._has(connection, 'harness_tool_usage'):
                return {'items': [], 'series': [], 'coverage': 'unavailable', 'as_of': time.time()}
            items = [dict(row) for row in connection.execute('SELECT name,status,count(*) AS count,avg(elapsed_ms) AS average_ms,max(elapsed_ms) AS max_ms FROM harness_tool_usage ' + where + ' GROUP BY name,status ORDER BY count DESC', values)]
            for item in items:
                item['label'] = TOOL_LABELS.get(item['name'], item['name'])
                item['status_label'] = STATUS_LABELS.get(item['status'], item['status'])
            series = [dict(row) for row in connection.execute("SELECT date(created_at,'+8 hours') AS day,count(*) AS count,avg(elapsed_ms) AS average_ms FROM harness_tool_usage " + where + ' GROUP BY day ORDER BY day', values)]
        return {'items': items, 'series': series, 'call_count': sum(row['count'] for row in items), 'as_of': time.time(),
                'coverage': 'business_executor_only',
                'scope_note': '记录走业务执行器的调用；提前权限拒绝、聊天管理及 Core 转发未全部记入，不能作全部消息处理率'}

    def deliveries(self, filters=None):
        filters = self._filters(filters)
        offset, page_size = self._page(filters.get('offset', 0), filters.get('page_size', 50))
        # Delivery rows include zero-model tools. Model selectors only apply when explicitly requested.
        conditions, values = [], []
        for key, column in (('session_key', 'd.session_key'), ('user_id', 'e.user_id'), ('status', 'd.outcome')):
            if filters.get(key) not in (None, ''):
                conditions.append(column + '=?'); values.append(filters[key])
        for key, op in (('after', '>='), ('before', '<')):
            if filters.get(key) not in (None, '', 0, '0'):
                conditions.append('d.created_at' + op + '?'); values.append(float(filters[key]))
        for key, column in (('profile_id', 'r.profile_id'), ('model', 'r.model'), ('purpose', 'r.purpose'), ('provider', 'r.provider'), ('account', 'r.account'), ('api_style', 'r.api_style')):
            if filters.get(key):
                conditions.append(column + '=?'); values.append(filters[key])
        where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
        source = 'FROM deliveries d LEFT JOIN events e ON e.event_key=d.event_key LEFT JOIN requests r ON r.id=d.request_id'
        with self._connect() as connection:
            items = [dict(row) for row in connection.execute(f"""SELECT d.id,d.request_id,d.session_key,d.event_key,d.outcome,d.error,d.created_at,e.user_id,
                json_array_length(d.message_ids) AS message_count,r.model,r.purpose,
                CASE WHEN r.ended_at IS NOT NULL THEN (d.created_at-r.ended_at)*1000 END AS after_model_ms
                {source} {where} ORDER BY d.created_at DESC LIMIT ? OFFSET ?""", (*values, page_size, offset))]
            states = [dict(row) for row in connection.execute('SELECT d.outcome AS name,count(*) AS count ' + source + ' ' + where + ' GROUP BY d.outcome', values)]
            total = sum(row['count'] for row in states)
            if self._has(connection, 'delivery_timings') and items:
                timings = {row['delivery_id']: dict(row) for row in connection.execute(
                    'SELECT * FROM delivery_timings WHERE delivery_id IN (' + ','.join('?' for _ in items) + ')', [item['id'] for item in items])}
                for item in items:
                    timing = timings.get(item['id'], {})
                    item.update({key: timing.get(key) for key in ('started_at', 'ended_at', 'elapsed_ms')})
                    item['timing_source'] = 'delivery_timings' if timing else 'unreported'
            group_names = self._group_names()
            for item in items:
                scope = self._scope_identity(connection, item.get('session_key'), item.get('user_id'), names=group_names)
                item.update(scope)
                item['purpose_label'] = BACKGROUND_KIND_LABELS.get(item.get('purpose'), item.get('purpose')) if item.get('purpose') else '本地业务输出'
                item['outcome_label'] = STATUS_LABELS.get(item.get('outcome'), item.get('outcome'))
            incidents = []
            if self._has(connection, 'transport_incidents'):
                incidents = [dict(row) for row in connection.execute('SELECT id,started_at,ended_at,reason FROM transport_incidents ORDER BY started_at DESC LIMIT 100')]
        return {'items': items, 'total': total, 'states': states, 'incidents': incidents,
                'offset': offset, 'page_size': page_size, 'as_of': time.time(),
                'coverage': 'persisted_delivery_receipts', 'scope_note': '仅已保存回执；模型成功生成不代表 QQ 送达，文本长度不能推算 token'}

    def exports(self, kind, filters=None, format='csv'):
        if format not in {'csv', 'json'}:
            raise ValueError('导出格式支持 csv、json')
        if kind == 'requests':
            where, args = self._where(filters)
            with self._connect() as connection:
                items = [dict(row) for row in connection.execute(f'SELECT {REQUEST_PROJECTION} {REQUEST_FROM} {where} ORDER BY r.started_at', args)]
        elif kind == 'overview':
            items = self.overview(filters)['series']
        elif kind == 'sessions':
            items = self.sessions(filters)['items']
        else:
            raise ValueError('导出支持 requests、overview、sessions')
        identities = {}
        def anonymize(value, key=''):
            if key in {'session_key', 'event_key', 'user_id', 'nickname', 'user_nickname', 'speaker_nickname', 'scope_label', 'title', 'group_id', 'group_name', 'alias', 'account', 'id', 'request_id', 'key'} and value not in (None, ''):
                identity = (key, str(value))
                if identity not in identities:
                    identities[identity] = key + '-' + str(len(identities) + 1)
                return identities[identity]
            if isinstance(value, dict):
                return {name: anonymize(item, name) for name, item in value.items()}
            if isinstance(value, list):
                return [anonymize(item) for item in value]
            return value
        items = [anonymize(item) for item in items]
        if format == 'json':
            content = json.dumps({'kind': kind, 'items': items, 'row_count': len(items),
                                  'identity_mode': 'anonymous'}, ensure_ascii=False, indent=2)
            media_type = 'application/json; charset=utf-8'
        else:
            output = io.StringIO(newline='')
            fields = list(items[0]) if items else []
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for item in items:
                writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in item.items()})
            content = output.getvalue()
            media_type = 'text/csv; charset=utf-8'
        return {'filename': 'harness-' + kind + '.' + format, 'content': content,
                'media_type': media_type, 'row_count': len(items), 'as_of': time.time(),
                'identity_mode': 'anonymous', 'scope_note': '导出匿名身份与数值摘要；不含媒体、消息正文或完整请求'}
