"""Local console controls backed by the existing group and ranking services."""
from __future__ import annotations

import time

from .business.group_domains import FEATURES


class ConsoleBusiness:
    def __init__(self, runtime):
        self.runtime = runtime
        self.tools = runtime.tools

    def groups(self):
        tools = self.tools
        with tools.db.connect() as connection:
            clusters = [dict(row) for row in connection.execute(
                "SELECT domain_id,name,alias FROM group_domains WHERE mode='cluster' AND enabled=1 ORDER BY name")]
        cluster_map = {row['domain_id']: row for row in clusters}
        items = []
        for raw in tools.db.all_managed_groups():
            row = dict(raw)
            cluster = cluster_map.get(row['domain_id'])
            filters = [{'user_id': user_id, 'nickname': tools.db.group_member_name(row['group_id'], user_id)}
                       for user_id in tools.db.group_filter_members(row['group_id'])]
            items.append({
                'group_id': row['group_id'], 'group_name': row['group_name'] or '未记录群名',
                'alias': row['alias'], 'enabled': bool(row['enabled']),
                'status_label': '在群中' if row['enabled'] else '已归档 · 机器人已离群',
                'cluster_id': cluster['domain_id'] if cluster else None,
                'cluster_name': cluster['name'] if cluster else '独立群',
                'features': tools.domains.feature_rows(row['group_id'], include_internal=True),
                'filters': filters,
            })
        for cluster in clusters:
            cluster['group_ids'] = list(tools.domains.domain_groups(cluster['domain_id']))
        return {'items': items, 'clusters': clusters, 'as_of': time.time()}

    def ranking(self, group_id: int, period: str = 'day', cluster: bool = False,
                offset: int = 0, page_size: int = 100):
        if period not in {'day', 'week', 'month', 'total'}:
            raise ValueError('统计周期请选择今日、本周、本月或累计。')
        if not self.tools.db.is_managed_group(group_id):
            raise ValueError('机器人已离开此群，历史资料只供归档查看。')
        domain = self.tools.domains.domain_for_group(group_id)
        if cluster and (domain is None or domain.mode != 'cluster'):
            raise ValueError('当前群尚未加入集群。')
        groups = self.tools.domains.ranking_group_ids(group_id, cluster=cluster)
        labels = {int(row['group_id']): row['group_name'] or str(row['group_id'])
                  for row in self.tools.db.managed_groups()}
        ranked = self.tools.stats.ranking_rows_for_groups(period, groups)
        size = min(100, max(1, page_size))
        start = max(0, offset)
        items = [{**row, 'rank': index + 1} for index, row in enumerate(ranked)][start:start + size]
        return {'items': items, 'total': len(ranked), 'offset': start, 'page_size': size,
                'message_total': sum(row['message_count'] for row in ranked),
                'scope_label': '集群：' + domain.name if cluster else '群：' + labels[group_id],
                'groups': [{'group_id': gid, 'group_name': labels[gid]} for gid in groups],
                'period': period, 'as_of': time.time()}

    def save_group(self, group_id: int, raw: dict):
        tools = self.tools
        if not tools.db.is_managed_group(group_id):
            raise ValueError('已离群的群为归档状态，不能启用自动功能。')
        alias = str(raw.get('alias', '')).strip()
        if 'alias' in raw and len(alias) > 20:
            raise ValueError('排行缩写最多 20 个字。')
        feature_changes = raw.get('features', {})
        if any(key not in FEATURES for key in feature_changes):
            raise ValueError('包含未知群功能。')
        filters = raw.get('filter_user_ids')
        if filters is not None:
            filters = set(int(value) for value in filters)
            if any(value <= 0 for value in filters):
                raise ValueError('过滤名单需要有效 QQ 号。')
        current = tools.domains.domain_for_group(group_id)
        target = raw.get('cluster_id')
        if 'cluster_id' in raw and target is not None:
            target = int(target)
            with tools.db.connect() as connection:
                found = connection.execute("SELECT 1 FROM group_domains WHERE domain_id=? AND mode='cluster' AND enabled=1", (target,)).fetchone()
            if not found:
                raise ValueError('所选集群不存在。')
        if 'cluster_id' in raw and (current is None or target != (current.domain_id if current.mode == 'cluster' else None)):
            saved_features = tools.db.group_features(group_id)
            if target is None:
                if current and current.mode == 'cluster':
                    tools.domains.remove_group_from_cluster(group_id)
            else:
                tools.domains.add_group_to_cluster(group_id, target)
                # Changing a console grouping never silently starts other features.
                for key, enabled in saved_features.items():
                    tools.domains.set_feature(group_id, key, enabled)
        if 'alias' in raw:
            tools.db.set_group_alias(group_id, alias)
        for key, enabled in feature_changes.items():
            tools.domains.set_feature(group_id, key, bool(enabled))
            if key == 'mini_games' and not enabled:
                tools.games.cancel_group_session(group_id)
        if filters is not None:
            existing = set(tools.db.group_filter_members(group_id))
            actor = int(next(iter(self.runtime.store.get_setting('operator_ids', [])), 0))
            for user in existing - filters:
                tools.db.remove_group_filter(group_id, user)
            for user in filters - existing:
                tools.db.add_group_filter(group_id, user, actor)
        self.runtime.publish('group_settings', {'group_id': group_id})
        return self.groups()

    def create_cluster(self, name: str):
        name = ' '.join(str(name).split())
        if not name:
            raise ValueError('请填写集群名称。')
        if self.tools.domains.cluster_by_name_or_alias(name):
            raise ValueError('这个集群名称已存在。')
        self.tools.domains.create_cluster(name)
        self.runtime.publish('group_settings', {'kind': 'cluster_created'})
        return self.groups()
