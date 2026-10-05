import { useState } from 'react';
import type { FormEvent } from 'react';
import { api, date, number, rows } from './api';
import type { RecordValue } from './api';
import { ChartPanel, DataTable, Panel, barOption } from './charts';
import { useApiData as useLocalData } from './useApiData';
import './console-business.css';

function Spinner({ active }: { active: boolean }) {
  return <span className="console-refresh-slot">{active && <span className="loading-indicator" role="status" aria-label="正在刷新" title="正在刷新"><span className="spinner" /></span>}</span>;
}

export function ConsoleBusiness({ view, revision, refresh }: { view: 'ranking' | 'groups'; revision: number; refresh: () => void }) {
  const groups = useLocalData('/console/groups', revision);
  return view === 'ranking'
    ? <RankingView groups={groups} revision={revision} refresh={refresh} />
    : <GroupView groups={groups} refresh={refresh} />;
}

function RankingView({ groups, revision, refresh }: { groups: ReturnType<typeof useLocalData>; revision: number; refresh: () => void }) {
  const active = rows(groups.data).filter((group) => group.enabled);
  const [selected, setSelected] = useState('');
  const [period, setPeriod] = useState('day');
  const [cluster, setCluster] = useState(false);
  const [offset, setOffset] = useState(0);
  const current = active.find((group) => String(group.group_id) === selected) ?? active[0];
  const query = new URLSearchParams({ group_id: String(current?.group_id ?? 0), period, cluster: String(cluster && !!current?.cluster_id), offset: String(offset) });
  const ranking = useLocalData('/console/ranking?' + query, revision);
  const result = ranking.data;
  const ranked = rows(result);
  const periods = [['day', '今日'], ['week', '本周'], ['month', '本月'], ['total', '累计']];
  return <div className="page-stack">
    <Panel title="实时发言排行" hint="直接读取已采集发言计数；选择本群或所属集群，结果仅在控制台展示。" actions={<div className="console-actions"><Spinner active={groups.loading || ranking.loading} /><button className="button quiet small" onClick={refresh}>刷新</button></div>}>
      <div className="console-ranking-controls">
        <label className="field">群<select value={current?.group_id ?? ''} onChange={(e) => { setSelected(e.target.value); setOffset(0); setCluster(false); }}><option value="" disabled>选择群</option>{active.map((group) => <option key={group.group_id} value={group.group_id}>群：{group.group_name}（{group.group_id}）</option>)}</select></label>
        <label className="field">统计周期<select value={period} onChange={(e) => { setPeriod(e.target.value); setOffset(0); }}>{periods.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="field">统计范围<select value={cluster && current?.cluster_id ? 'cluster' : 'group'} onChange={(e) => { setCluster(e.target.value === 'cluster'); setOffset(0); }}><option value="group">当前群</option>{current?.cluster_id && <option value="cluster">集群：{current.cluster_name}</option>}</select></label>
      </div>
      {groups.error && <p className="notice error" role="alert">{groups.error}</p>}
      {!!active.length && ranking.error && <p className="notice error" role="alert">{ranking.error}</p>}
      {!active.length && !groups.loading && <p className="caption">暂无机器人仍在的群；已离群的历史保留在“群与集群”的归档列表中。</p>}
      {result && current && <><div className="console-ranking-summary"><strong>{result.scope_label}</strong><span>{number(result.message_total)} 条发言 · {number(result.total)} 名成员</span><small>读取时间：{date(result.as_of)}</small></div><div className="console-member-groups">{rows(result.groups).map((group) => <span key={group.group_id}>群：{group.group_name}（{group.group_id}）</span>)}</div><DataTable rows={ranked} columns={[{ key: 'rank', label: '排名' }, { key: 'nickname', label: '成员', render: (row) => <><strong>{row.nickname || '未记录昵称'}</strong><small>QQ：{row.user_id}</small></> }, { key: 'message_count', label: '发言条数' }]} empty="当前统计周期暂无发言记录。" /><div className="analytics-pager"><span>每页 100 名 · 按发言条数从高到低</span><div className="console-actions"><button className="button quiet small" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 100))}>上一页</button><button className="button quiet small" disabled={offset + 100 >= result.total} onClick={() => setOffset(offset + 100)}>下一页</button></div></div></>}
    </Panel>
    {!!ranked.length && <details className="console-chart-details"><summary>查看本页前 15 名柱状图</summary><ChartPanel title="发言数量对比" rows={ranked.slice(0, 15)} columns={[{ key: 'nickname', label: '成员' }, { key: 'message_count', label: '发言条数' }]} option={barOption(ranked.slice(0, 15), 'message_count', 'nickname', true)} height={Math.max(300, Math.min(560, ranked.length * 30))} /></details>}
  </div>;
}

function GroupView({ groups, refresh }: { groups: ReturnType<typeof useLocalData>; refresh: () => void }) {
  const [selected, setSelected] = useState('');
  const [archived, setArchived] = useState(false);
  const [alias, setAlias] = useState('');
  const [clusterId, setClusterId] = useState('');
  const [features, setFeatures] = useState<Record<string, boolean>>({});
  const [filters, setFilters] = useState('');
  const [newCluster, setNewCluster] = useState('');
  const [pending, setPending] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const items = rows(groups.data).filter((group) => archived || group.enabled);
  const current = rows(groups.data).find((group) => String(group.group_id) === selected);
  function choose(group: RecordValue) {
    setSelected(String(group.group_id)); setAlias(group.alias ?? ''); setClusterId(String(group.cluster_id ?? ''));
    setFeatures(Object.fromEntries(rows(group.features).map((feature) => [feature.key, !!feature.configured_enabled])));
    setFilters(rows(group.filters).map((person) => person.user_id).join('\n')); setNotice(''); setError('');
  }
  async function save(e: FormEvent) {
    e.preventDefault(); if (!current) return; setPending('group'); setError(''); setNotice('');
    try {
      const ids = filters.split(/[\s,，、;；]+/).filter(Boolean).map((value) => Number(value));
      if (ids.some((value) => !Number.isSafeInteger(value) || value <= 0)) throw new Error('过滤名单请填写 QQ 号，以换行或逗号分隔。');
      await api('/console/groups/' + current.group_id, { method: 'PUT', body: JSON.stringify({ alias, cluster_id: clusterId ? Number(clusterId) : null, features, filter_user_ids: ids }) });
      setNotice('群设置已保存。'); refresh();
    } catch (reason) { setError((reason as Error).message); } finally { setPending(''); }
  }
  async function create(e: FormEvent) {
    e.preventDefault(); setPending('cluster'); setError('');
    try { await api('/console/clusters', { method: 'POST', body: JSON.stringify({ name: newCluster }) }); setNewCluster(''); setNotice('集群已创建，请选择成员群并保存所属集群。'); refresh(); }
    catch (reason) { setError((reason as Error).message); } finally { setPending(''); }
  }
  const clusters = rows(groups.data?.clusters);
  const archivedCount = rows(groups.data).filter((group) => !group.enabled).length;
  return <div className="page-stack">
    <Panel title="群与集群" hint="完整群名用于后台识别；缩写只用于发言榜。机器人已离开的群自动归档，自动功能忽略这些群。" actions={<div className="console-actions"><Spinner active={groups.loading} /><button className="button quiet small" onClick={refresh}>刷新</button></div>}>
      <label className="toggle-field console-archive-toggle"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /><span>显示已归档群（{archivedCount}）</span></label>
      <DataTable rows={items} columns={[{ key: 'group_name', label: '完整群名', render: (group) => <><strong>群：{group.group_name}</strong><small>群号：{group.group_id}</small></> }, { key: 'alias', label: '排行缩写', render: (group) => group.alias || '未设置' }, { key: 'cluster_name', label: '所属集群' }, { key: 'status_label', label: '状态' }]} onRow={choose} empty="暂无群。连接 QQ 后会同步当前群列表。" />
      {groups.error && <p className="notice error" role="alert">{groups.error}</p>}
    </Panel>
    {clusters.length > 0 && <Panel title="集群成员" hint="23:50 向启用榜单推送的成员群发送本集群总榜。"><div className="console-cluster-grid">{clusters.map((cluster) => <article key={cluster.domain_id}><h3>{cluster.name}</h3>{rows(groups.data).filter((group) => group.enabled && group.cluster_id === cluster.domain_id).map((group) => <p key={group.group_id}>群：{group.group_name}<small>{group.group_id} · {group.alias ? `排行缩写：${group.alias}` : '未设置缩写'}</small></p>)}{!cluster.group_ids?.length && <p className="caption">暂无在群成员</p>}</article>)}</div></Panel>}
    {current && <Panel title={`群设置：${current.group_name}`} hint={`群号：${current.group_id} · ${current.status_label}`}>
      {notice && <p className="notice success" role="status">{notice}</p>}{error && <p className="notice error" role="alert">{error}</p>}
      <form className="form-grid" onSubmit={save}><label className="field">发言榜缩写<input value={alias} maxLength={20} disabled={!current.enabled} onChange={(e) => setAlias(e.target.value)} /><small>例如“木岛”；不会替代控制台里的完整群名。</small></label><label className="field">所属集群<select value={clusterId} disabled={!current.enabled} onChange={(e) => setClusterId(e.target.value)}><option value="">独立群</option>{clusters.map((cluster) => <option key={cluster.domain_id} value={cluster.domain_id}>{cluster.name}</option>)}</select></label><div className="wide"><h3>群功能开关</h3><div className="console-feature-grid">{rows(current.features).map((feature) => <label className="toggle-field" key={feature.key}><input type="checkbox" disabled={!current.enabled} checked={features[feature.key] ?? false} onChange={(e) => setFeatures((old) => ({ ...old, [feature.key]: e.target.checked }))} /><span>{feature.label}</span></label>)}</div></div><label className="field wide">群内过滤名单<textarea value={filters} disabled={!current.enabled} onChange={(e) => setFilters(e.target.value)} rows={3} placeholder="每行一个 QQ 号" /><small>这些成员的业务触发会被忽略；已有名单：{rows(current.filters).map((person) => `${person.nickname || '未记录昵称'}（${person.user_id}）`).join('、') || '无'}</small></label>{current.enabled && <div className="form-actions wide"><button className="button primary" disabled={pending !== ''} type="submit">{pending === 'group' ? '保存中…' : '保存群设置'}</button><span className="caption">改变所属集群会保留当前功能开关；勾选项以此表单保存结果为准。</span></div>}</form>
    </Panel>}
    <Panel title="创建集群" hint="创建后，在上方成员群设置中选择该集群。"><form className="console-create-cluster" onSubmit={create}><label className="field">集群名称<input value={newCluster} onChange={(e) => setNewCluster(e.target.value)} placeholder="例如：A海岸" required /></label><button className="button primary" disabled={pending !== ''} type="submit">{pending === 'cluster' ? '创建中…' : '创建集群'}</button></form>{!current && error && <p className="notice error" role="alert">{error}</p>}{!current && notice && <p className="notice success" role="status">{notice}</p>}</Panel>
  </div>;
}
