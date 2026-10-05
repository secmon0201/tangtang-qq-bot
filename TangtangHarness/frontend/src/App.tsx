import { useCallback, useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { api, contextSourceLabel, date, displayJson, humanLabel, number, percent, pretty, rows, sessionDetail, sessionLabel, sessionScope } from './api';
import type { RecordValue } from './api';
import { AnalyticsDashboard } from './AnalyticsDashboard';
import type { AnalyticsPage } from './AnalyticsDashboard';
import { ConsoleBusiness } from './ConsoleBusiness';
import { useApiData as useLoad } from './useApiData';

type Tab = AnalyticsPage | 'sessions' | 'context' | 'metrics' | 'models' | 'preview' | 'tools' | 'experiments' | 'ranking' | 'groups';
const tabs: { id: Tab; name: string; subtitle: string; icon: string }[] = [
  { id: 'overview', name: '总览', subtitle: '流量、缓存、费用和响应表现', icon: 'chart' },
  { id: 'traffic', name: '流量日志', subtitle: '每次模型尝试与真实用量', icon: 'layers' },
  { id: 'cache', name: '缓存与费用', subtitle: '加权占比、覆盖率和时间趋势', icon: 'chart' },
  { id: 'session-analytics', name: '群与上下文', subtitle: '上下文增长、压缩快照和群摘要', icon: 'chat' },
  { id: 'people', name: '个人资料', subtitle: '发言、长期记忆、画像与认知版本', icon: 'eye' },
  { id: 'operations', name: '后台与执行', subtitle: '队列预算、工具执行和 QQ 送达', icon: 'tool' },
  { id: 'business-analytics', name: '业务与运行', subtitle: '发言统计、订阅、游戏和连接状态', icon: 'settings' },
  { id: 'ranking', name: '发言排行', subtitle: '直接查看本群与集群实时发言榜', icon: 'chart' },
  { id: 'groups', name: '群与集群', subtitle: '完整群名、排行缩写、功能开关和过滤名单', icon: 'settings' },
  { id: 'sessions', name: '会话', subtitle: '消息来源与会话记录', icon: 'chat' },
  { id: 'context', name: '实际上下文', subtitle: '模型请求、分层与前缀变化', icon: 'layers' },
  { id: 'metrics', name: '缓存与用量', subtitle: '真实 usage、成本和延迟', icon: 'chart' },
  { id: 'models', name: '模型与设置', subtitle: '多个模型配置及运行选项', icon: 'settings' },
  { id: 'preview', name: '请求预览', subtitle: '本地检查路由与上下文', icon: 'eye' },
  { id: 'tools', name: '工具与后台', subtitle: '本地业务能力与工作状态', icon: 'tool' },
  { id: 'experiments', name: '实验', subtitle: '回放与缓存实验', icon: 'flask' },
];

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    chat: <><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7A8.4 8.4 0 0 1 4 11.5a8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z" /></>,
    layers: <><path d="m12 3 10 5-10 5L2 8l10-5Z" /><path d="m2 12 10 5 10-5M2 16l10 5 10-5" /></>,
    chart: <><path d="M3 3v18h18M7 14v3M12 9v8M17 5v12" /></>,
    settings: <><path d="M4 7h16M4 17h16M8 4v6M16 14v6" /><circle cx="8" cy="7" r="2" /><circle cx="16" cy="17" r="2" /></>,
    eye: <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></>,
    tool: <><path d="m14 6 4-4a6 6 0 0 1-8 8L3 17a2.8 2.8 0 0 0 4 4l7-7a6 6 0 0 0 8-8l-4 4-4-4Z" /></>,
    flask: <><path d="M9 3h6M10 3v7L4 20a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1l-6-10V3M8 14h8" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 11-2l3 3M4 16l3 3a7 7 0 0 0 11-2" /></>,
    arrow: <path d="m8 5 7 7-7 7" />,
    copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V4H4v12h4" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.layers}</svg>;
}

function MainNavigation({ tab }: { tab: Tab }) {
  const groups: { title: string; items: Tab[] }[] = [
    { title: '对话与群', items: ['sessions', 'context', 'session-analytics', 'people', 'ranking', 'groups'] },
    { title: '运行观察', items: ['overview', 'traffic', 'cache', 'operations', 'business-analytics'] },
    { title: '管理', items: ['tools', 'models'] },
  ];
  const links = (ids: Tab[]) => ids.map((id) => {
    const item = tabs.find((entry) => entry.id === id)!;
    return <a key={item.id} href={`#${item.id}`} aria-label={item.name} title={item.name} aria-current={tab === item.id ? 'page' : undefined} className={tab === item.id ? 'active' : ''}><Icon name={item.icon} /><span>{item.name}</span>{tab === item.id && <Icon name="arrow" size={15} />}</a>;
  });
  return <nav aria-label="主导航">{groups.map((group) => <div className="nav-group" key={group.title}><div className="nav-section-title">{group.title}</div>{links(group.items)}</div>)}<details className="nav-advanced" open={['preview', 'experiments'].includes(tab) || undefined}><summary title="高级工具"><Icon name="flask" /><span>高级工具</span><Icon name="arrow" size={14} /></summary><div className="nav-group">{links(['preview', 'experiments'])}</div></details></nav>;
}

function Empty({ title = '还没有记录', detail = '数据会在实际运行后显示。', icon = 'layers' }: { title?: string; detail?: string; icon?: string }) {
  return <div className="empty"><span className="empty-icon"><Icon name={icon} size={28} /></span><h3>{title}</h3><p>{detail}</p></div>;
}

function ErrorNotice({ message }: { message: string }) {
  return message ? <div className="notice error" role="alert">{message}</div> : null;
}

function Loading({ active }: { active: boolean }) {
  return <span className="loading-indicator" style={{ visibility: active ? 'visible' : 'hidden' }} role={active ? 'status' : undefined} aria-label={active ? '正在刷新' : undefined} aria-hidden={!active} title={active ? '正在刷新' : undefined}>{active && <span className="spinner" />}</span>;
}

function Panel({ title, hint, children, actions, className = '' }: { title?: string; hint?: string; children: ReactNode; actions?: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{title && <div className="panel-heading"><div><h2>{title}</h2>{hint && <p>{hint}</p>}</div>{actions}</div>}{children}</section>;
}

function JsonBlock({ value, label = '查看完整数据', open = false }: { value: unknown; label?: string; open?: boolean }) {
  const [copied, setCopied] = useState(false);
  return <details className="json-block" open={open}><summary>{label}</summary><div className="json-toolbar"><button className="button quiet small" onClick={() => navigator.clipboard.writeText(pretty(value) ?? '').then(() => setCopied(true))}><Icon name="copy" size={15} />{copied ? '已复制' : '复制 JSON'}</button></div><pre>{displayJson(value)}</pre></details>;
}

function Badge({ value }: { value: unknown }) {
  const raw = value == null ? 'unknown' : String(value).toLowerCase();
  const text = humanLabel(value);
  const tone = ['completed', 'delivered', 'success', 'connected', 'enabled', '已启用', 'active'].includes(raw) ? 'green'
    : ['failed', 'error', 'disconnected'].includes(raw) ? 'red' : 'neutral';
  return <span className={`badge ${tone}`}>{text}</span>;
}

function Stats({ items }: { items: { label: string; value: string; hint?: string }[] }) {
  return <div className="stats-grid">{items.map((item) => <div className="stat-card" key={item.label}><div className="stat-label">{item.label}</div><div className="stat-value">{item.value}</div>{item.hint && <div className="stat-hint">{item.hint}</div>}</div>)}</div>;
}

function RequestFilters({ models, apply }: { models: RecordValue[]; apply: (query: string) => void }) {
  const [draft, setDraft] = useState<Record<string, string>>({ profile_id: '', purpose: '', provider: '', account: '', after: '', before: '' });
  function change(key: string, value: string) { setDraft((old) => ({ ...old, [key]: value })); }
  function submit(event: FormEvent) {
    event.preventDefault();
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(draft)) {
      if (value) query.set(key, ['after', 'before'].includes(key) ? String(new Date(value).getTime() / 1000) : value);
    }
    apply(query.size ? `?${query}` : '');
  }
  return <Panel><details className="filters"><summary>筛选请求<span>模型、渠道、用途与时间</span></summary><form className="filter-form" onSubmit={submit}>
    <label className="field">模型档案<select value={draft.profile_id} onChange={(event) => change('profile_id', event.target.value)}><option value="">全部模型</option>{models.map((model) => <option key={model.id} value={model.id}>{model.name ?? model.id}</option>)}</select></label>
    <label className="field">调用场景<select value={draft.purpose} onChange={(event) => change('purpose', event.target.value)}><option value="">全部场景</option>{['chat', 'continuation', 'proactive', 'summary', 'compaction', 'memory', 'profile', 'profile_review', 'cognition', 'growth', 'experiment', 'prewarm'].map((purpose) => <option key={purpose} value={purpose}>{humanLabel(purpose)}</option>)}</select></label>
    <label className="field">提供商<input value={draft.provider} onChange={(event) => change('provider', event.target.value)} placeholder="全部提供商" /></label>
    <label className="field">账户标签<input value={draft.account} onChange={(event) => change('account', event.target.value)} placeholder="全部账户" /></label>
    <label className="field">开始时间<input type="datetime-local" value={draft.after} onChange={(event) => change('after', event.target.value)} /></label>
    <label className="field">结束时间<input type="datetime-local" value={draft.before} onChange={(event) => change('before', event.target.value)} /></label>
    <div className="form-actions wide"><button className="button primary small" type="submit">应用筛选</button><button className="button quiet small" type="button" onClick={() => { setDraft({ profile_id: '', purpose: '', provider: '', account: '', after: '', before: '' }); apply(''); }}>清除筛选</button></div>
  </form></details></Panel>;
}

function SessionSelect({ value, onChange, sessions, required = true }: { value: string; onChange: (value: string) => void; sessions: RecordValue[]; required?: boolean }) {
  return <label className="field">会话<select value={value} onChange={(event) => onChange(event.target.value)} required={required}><option value="">选择会话</option>{sessions.map((session) => <option key={session.key ?? session.session_key} value={session.key ?? session.session_key}>{sessionLabel(session)}{sessionDetail(session) ? `（${sessionDetail(session)}）` : ''}</option>)}</select></label>;
}

function LayerView({ layers }: { layers: RecordValue[] }) {
  if (!layers.length) return <Empty title="没有分层数据" detail="此请求还未记录上下文层；完整 payload 可在下方查看。" />;
  const total = layers.reduce((sum, layer) => sum + (typeof layer.estimated_tokens === 'number' ? layer.estimated_tokens : 0), 0);
  return <div className="layer-view">
    <div className="layer-strip" aria-label="上下文各层占比">{layers.map((layer, index) => <div key={`${layer.name}-${index}`} className={`layer-segment tone-${index % 5}`} style={{ flex: total ? Math.max(1, layer.estimated_tokens ?? 0) : 1 }} title={`${humanLabel(layer.name ?? layer.kind, '上下文层')} · ${number(layer.estimated_tokens)} token`}>{index + 1}</div>)}</div>
    <p className="caption">顺序对应实际请求。占比仅表示本地 token 估算，不表示提供商实际缓存命中。</p>
    {layers.map((layer, index) => <details className="layer" key={`${layer.name}-${index}`}><summary><span className={`layer-number tone-${index % 5}`}>{index + 1}</span><span className="layer-title">{humanLabel(layer.name ?? layer.kind, '上下文层')}<small>{contextSourceLabel(layer.source ?? layer.origin)}</small></span><span className="layer-size">{number(layer.estimated_tokens)} token</span>{typeof layer.stable === 'boolean' && <span className="badge neutral">{layer.stable ? '稳定前缀' : '本轮内容'}</span>}</summary><pre>{layer.text ?? layer.content ?? pretty(layer)}</pre></details>)}
  </div>;
}

function ContextBudget({ telemetry }: { telemetry?: RecordValue }) {
  if (!telemetry) return null;
  const estimate = telemetry.estimated_input_tokens;
  const budget = telemetry.input_budget_tokens;
  const measured = typeof estimate === 'number' && typeof budget === 'number' && budget > 0;
  return <div className="context-budget"><div className="budget-heading"><h3>上下文预算</h3><span className="caption">本地估算，不是提供商账单</span></div>
    <Stats items={[{ label: '裁剪前 / 当前 token', value: `${number(telemetry.input_tokens_before_trim)} / ${number(estimate)}` }, { label: '输入预算', value: number(budget) }, { label: '软 / 硬水位', value: `${number(telemetry.soft_watermark)} / ${number(telemetry.hard_watermark)}` }, { label: '压缩快照版本', value: number(telemetry.snapshot_revision) }]} />
    {measured && <div className="budget-track" role="meter" aria-label="上下文预算使用比例（本地估算）" aria-valuemin={0} aria-valuemax={budget} aria-valuenow={estimate}><div className="budget-fill" style={{ width: `${Math.min(100, estimate / budget * 100)}%` }} />{(['soft_watermark', 'hard_watermark'] as const).map((key) => <span className="budget-marker" key={key} style={{ left: `${telemetry[key] / budget * 100}%` }} title={`${key === 'soft_watermark' ? '软水位' : '硬水位'} ${number(telemetry[key])}`} />)}</div>}
    <p className="caption">{measured ? `使用 ${percent(estimate / budget)}；` : ''}本次裁剪 {number(telemetry.trimmed_turn_ids?.length)} 轮历史、{number(telemetry.trimmed_event_keys?.length)} 条新增消息。{telemetry.compaction_due ? '已达到历史整理水位。' : ''}</p>
  </div>;
}

function SessionPage({ revision, sessions, initialSessionKey, onOpenRequest, onOpenCache }: { revision: number; sessions: ReturnType<typeof useLoad>; initialSessionKey: string; onOpenRequest: (id: string) => void; onOpenCache: (key: string) => void }) {
  const [selected, setSelected] = useState(initialSessionKey);
  const [showTools, setShowTools] = useState(false);
  useEffect(() => { if (initialSessionKey) setSelected(initialSessionKey); }, [initialSessionKey]);
  const list = rows(sessions.data);
  useEffect(() => {
    const first = list[0]?.key ?? list[0]?.session_key;
    if (first) setSelected((current) => current || String(first));
  }, [sessions.data]);
  const current = selected;
  const events = useLoad(current ? `/sessions/${encodeURIComponent(current)}/events` : null, revision);
  const selectedSession = list.find((session) => (session.key ?? session.session_key) === current);
  const eventRows = rows(events.data).filter((event) => showTools || event.role !== 'tool').slice().sort((a, b) => {
    const stamp = (event: RecordValue) => {
      const value = event.at ?? event.timestamp ?? event.created_at ?? 0;
      return new Date(typeof value === 'number' ? value * 1000 : value).getTime();
    };
    return stamp(b) - stamp(a);
  });
  return <div className="split-layout"><Panel title="会话列表" hint="群聊与私聊分别保存；列表按最近活动排序" actions={<Loading active={sessions.loading} />} className="list-panel"><ErrorNotice message={sessions.error} />{list.length ? <div className="item-list">{list.map((session) => { const key = session.key ?? session.session_key; return <button key={key} className={`list-item ${current === key ? 'selected' : ''}`} onClick={() => setSelected(key)}><div className="item-title"><span className={`scope-badge ${sessionScope(session) === '群' ? 'group' : 'private'}`}>{sessionScope(session)}</span>{sessionLabel(session).replace(`${sessionScope(session)} · `, '')}</div><div className="item-subtitle">{sessionDetail(session) || key} · {number(session.event_count)} 条消息</div><div className="item-time">{date(session.last_event_at ?? session.updated_at)}</div></button>; })}</div> : <Empty title="暂无会话" detail="observe 捕获的消息或本地回放会建立会话。" icon="chat" />}</Panel>
    <Panel title={selectedSession ? sessionLabel(selectedSession) : '会话记录'} hint={current ? `${sessionDetail(selectedSession) || current} · 最新消息在上方` : '选择左侧会话查看完整事件'} actions={<div className="console-actions"><Loading active={events.loading} />{current && <button className="button quiet small" onClick={() => onOpenCache(current)}>会话缓存结构</button>}</div>}><ErrorNotice message={events.error} /><label className="toggle-field conversation-options"><input type="checkbox" checked={showTools} onChange={(e) => setShowTools(e.target.checked)} /><span>显示工具执行记录</span></label>{eventRows.length ? <div className="event-timeline conversation-list">{eventRows.map((event, index) => <article className={`event-card conversation-message ${event.role === 'assistant' ? 'assistant' : ''}`} key={event.id ?? index}><div className="event-meta"><Badge value={event.role === 'assistant' ? '机器人' : event.role === 'user' ? '用户' : event.type ?? '消息'} /><span>{event.speaker_nickname ?? event.nickname ?? event.payload?.sender?.card ?? event.payload?.sender?.nickname ?? (event.role === 'assistant' ? '机器人' : '未知用户')}{event.speaker_user_id ? `（QQ ${event.speaker_user_id}）` : ''}</span>{event.tool_name && <span className="badge neutral">{event.tool_label ?? humanLabel(event.tool_name)}</span>}<time>{date(event.at ?? event.timestamp ?? event.created_at)}</time></div><p>{event.text || event.payload?.text || event.content || '图片、语音或其他消息（详情中查看）'}</p>{event.cache && <p className="caption">输入 {number(event.cache.input_tokens)} · 缓存读取 {number(event.cache.cache_read_tokens)} · 输出 {number(event.cache.output_tokens)} token · 缓存占比 {percent(event.cache.cache_ratio)}</p>}{event.request_id && <button className="button quiet small" onClick={() => onOpenRequest(event.request_id)}>查看这句话的上下文与缓存</button>}<JsonBlock value={event} label="查看消息详情" /></article>)}</div> : <Empty title={current ? '暂无消息' : '选择一个会话'} detail="这里只显示实际捕获和执行记录。" icon="chat" />}</Panel>
  </div>;
}

function ContextPage({ revision, models, initialRequestId = '' }: { revision: number; models: RecordValue[]; initialRequestId?: string }) {
  const [query, setQuery] = useState('');
  const requests = useLoad(`/requests${query}`, revision);
  const [selected, setSelected] = useState(initialRequestId);
  useEffect(() => { if (initialRequestId) setSelected(initialRequestId); }, [initialRequestId]);
  const [view, setView] = useState<'layers' | 'payload' | 'diff'>('layers');
  const list = rows(requests.data);
  const current = selected || list[0]?.id || '';
  const detail = useLoad(current ? `/requests/${encodeURIComponent(current)}` : null, revision);
  const diff = useLoad(current && view === 'diff' ? `/requests/${encodeURIComponent(current)}/diff` : null, revision);
  const request = detail.data;
  const usage = request?.usage ?? request ?? {};
  return <div className="page-stack"><RequestFilters models={models} apply={(value) => { setSelected(''); setQuery(value); }} /><div className="split-layout request-layout"><Panel title="请求记录" hint="按实际模型请求查看；最新请求在上方" actions={<Loading active={requests.loading} />} className="list-panel"><ErrorNotice message={requests.error} />{list.length ? <div className="item-list">{list.map((item) => <button className={`list-item ${item.id === current ? 'selected' : ''}`} key={item.id} onClick={() => setSelected(item.id)}><div className="item-title">{item.model ?? item.profile_id ?? '模型请求'}<Badge value={item.status} /></div><div className="item-subtitle">{sessionLabel(item)} · {sessionDetail(item) || item.session_key || item.id}</div><div className="item-time">{date(item.created_at)} · {percent(item.cache_ratio)} 缓存</div></button>)}</div> : <Empty title="还没有模型请求" detail="observe 不会调用模型。请求预览也不会计为实发。" />}</Panel>
    <Panel title="请求详情" hint={current || '选择请求，检查实际发送内容'} actions={<Loading active={detail.loading || diff.loading} />}><ErrorNotice message={detail.error} />{request ? <>
      <ErrorNotice message={request.error} />
      <Stats items={[
        { label: '输入 token', value: number(usage.input_tokens ?? usage.prompt_tokens) },
        { label: '缓存读 token', value: number(usage.cache_read_tokens), hint: usage.cache_status ?? '提供商 usage' },
        { label: '缓存命中率', value: percent(usage.cache_ratio), hint: '缓存读 / 输入' },
        { label: '总耗时', value: typeof (request.latency_ms ?? usage.latency_ms) === 'number' ? `${number(request.latency_ms ?? usage.latency_ms)} ms` : '未报告' },
      ]} />
      <ContextBudget telemetry={request.telemetry} />
      <div className="segmented" aria-label="请求详情视图">{([{ id: 'layers', label: '上下文分层' }, { id: 'payload', label: '实际 payload' }, { id: 'diff', label: '前缀对比' }] as const).map((item) => <button key={item.id} aria-pressed={view === item.id} className={view === item.id ? 'active' : ''} onClick={() => setView(item.id)}>{item.label}</button>)}</div>
      {view === 'layers' && <LayerView layers={request.layers ?? request.telemetry?.layers ?? request.context?.layers ?? []} />}
      {view === 'payload' && <><p className="caption">这里展示执行记录中的 payload。API 密钥不会作为上下文显示。</p><JsonBlock value={request.payload ?? request.request_payload} label="发送给模型的实际请求" open /></>}
      {view === 'diff' && <><ErrorNotice message={diff.error} />{diff.data ? <><div className="notice">对比请求：{diff.data.previous_request_id ?? '没有前一请求'}；共同前缀：{number(diff.data.common_prefix_bytes)} bytes。前缀相同不代表服务商一定命中缓存。</div>{Array.isArray(diff.data.changed_layers) && <div className="chip-row">{diff.data.changed_layers.map((layer: any, index: number) => <span className="badge neutral" key={index}>{humanLabel(typeof layer === 'string' ? layer : layer.name ?? pretty(layer))}</span>)}</div>}<JsonBlock value={diff.data} label="完整对比结果" open /></> : !diff.loading && <Empty title="没有可对比的请求" detail="同一会话有多个请求后可以检查追加和变化。" />}</>}
      {request.reply && <div className="reply-box"><h3>模型回复</h3><p>{Array.isArray(request.reply) ? request.reply.join('\n\n') : request.reply}</p></div>}
      <JsonBlock value={request} label="完整执行记录（含 usage、工具与时间线）" />
    </> : <Empty title="等待实际请求" detail="上下文和缓存数据以真实发送记录为准。" />}</Panel>
  </div></div>;
}

function MetricsPage({ revision, models }: { revision: number; models: RecordValue[] }) {
  const [query, setQuery] = useState('');
  const { data, error, loading } = useLoad(`/metrics${query}`, revision);
  const summary = data?.summary ?? data ?? {};
  const cache = summary.cache ?? {};
  const modelRows = rows(data?.by_model ?? data?.models);
  const knownRequests = modelRows.filter((item) => typeof item.request_count === 'number');
  const largest = Math.max(1, ...knownRequests.map((item) => item.request_count));
  return <div className="page-stack"><RequestFilters models={models} apply={setQuery} /><ErrorNotice message={error} /><Panel title="缓存与用量" actions={<Loading active={loading} />}><Stats items={[
    { label: '模型请求', value: number(summary.request_count ?? summary.requests_total), hint: '实际调用次数' },
    { label: '输入 / 输出 token', value: `${number(summary.input_tokens)} / ${number(summary.output_tokens)}` },
    { label: '加权缓存命中率', value: percent(summary.cache_ratio ?? cache.weighted_cache_ratio), hint: `可报告请求 ${number(summary.cache_known_requests)} · 覆盖率 ${percent(summary.coverage_ratio)}` },
    { label: '估算成本', value: summary.cost == null ? '未报告' : `${number(summary.cost, 6)} ${summary.currency ?? ''}`, hint: '按配置单价计算；非账单' },
    { label: '请求命中率', value: percent(summary.request_hit_ratio), hint: '有缓存读的请求 / 缓存可报告请求' },
    { label: '缓存指标覆盖率', value: percent(summary.coverage_ratio), hint: '缓存可报告请求 / 全部请求' },
    { label: '缓存读 / 写 token', value: `${number(summary.cache_read_tokens)} / ${number(summary.cache_write_tokens)}` },
    { label: '平均耗时', value: typeof summary.latency_ms === 'number' ? `${number(summary.latency_ms)} ms` : '未报告', hint: '已完成请求；包含后台场景时按筛选比较' },
  ]} /></Panel><div className="two-columns"><Panel title="按模型比较" hint="单独计算各模型的缓存与成本">{modelRows.length ? <div className="table-scroll"><table><thead><tr><th>模型</th><th>请求</th><th>输入</th><th>缓存命中率</th><th>成本</th></tr></thead><tbody>{modelRows.map((model, index) => <tr key={model.profile_id ?? model.model ?? index}><td><strong>{model.model ?? model.name ?? model.profile_id}</strong><small>{model.provider}</small></td><td>{number(model.request_count)}</td><td>{number(model.input_tokens)}</td><td>{percent(model.cache_ratio)}</td><td>{number(model.cost, 6)}</td></tr>)}</tbody></table></div> : <Empty title="暂无模型用量" detail="完成实际请求后，会显示模型之间的差异。" icon="chart" />}</Panel><Panel title="请求分布" hint="按模型实际请求数">{knownRequests.length ? <div className="bar-chart">{knownRequests.map((model, index) => <div className="bar-row" key={model.profile_id ?? index}><span>{model.model ?? model.profile_id}</span><div className="bar-track"><div className={`bar-fill tone-${index % 5}`} style={{ width: `${model.request_count / largest * 100}%` }} /></div><strong>{number(model.request_count)}</strong></div>)}</div> : <Empty title="暂无统计" detail="不支持或未报告的数值保持未知，不转成 0。" icon="chart" />}</Panel></div>
    <Panel title="统计口径"><div className="definition-grid"><div><h3>缓存命中率</h3><p>区分冷启动、追加请求、模型切换和前缀变更。模型端未报告缓存数据时，显示“未报告”。</p></div><div><h3>成本与延迟</h3><p>成本使用已配置的单价，缺少单价时保持未知。完整响应保留后端提供的样本量、延迟和计费字段。</p></div></div>{data && <JsonBlock value={data} label="查看指标完整响应" />}</Panel>
  </div>;
}

const emptyModel: RecordValue = { id: '', name: '', provider: 'custom', model: '', api_style: 'chat_completions', base_url: '', api_key_env: '', context_limit: null, max_output_tokens: 4096, reasoning_effort: 'none', cache_key_enabled: false, vision: true, timeout_seconds: 60, fallback_profile_id: '', account_label: '', usage_semantics: 'auto', tokenizer: '', extra_body: {}, input_price_per_million: null, output_price_per_million: null, cache_read_price_per_million: null, cache_write_price_per_million: null };

function editableSettings(settings: RecordValue) {
  const value = { ...settings };
  for (const key of ['profiles', 'active_model', 'onebot_access_token_configured', 'context', 'background', 'routing', 'context_policy', 'effective_chat_features']) delete value[key];
  return pretty(value);
}

function ModelPage({ revision, refresh }: { revision: number; refresh: () => void }) {
  const models = useLoad('/models', revision);
  const settings = useLoad('/settings', revision);
  const [draft, setDraft] = useState<RecordValue>({ ...emptyModel });
  const [extraBodyDraft, setExtraBodyDraft] = useState('{}');
  const [settingsDraft, setSettingsDraft] = useState('');
  const [saving, setSaving] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  useEffect(() => { if (settings.data && !settingsDraft) setSettingsDraft(editableSettings(settings.data)); }, [settings.data, settingsDraft]);
  const list = rows(models.data);
  async function saveModel(event: FormEvent) {
    event.preventDefault(); setSaving('model'); setError(''); setMessage('');
    try {
      const extraBody = JSON.parse(extraBodyDraft);
      if (!extraBody || Array.isArray(extraBody) || typeof extraBody !== 'object') throw new Error('额外请求参数必须是 JSON 对象。');
      const items = [...list.filter((item) => item.id !== draft.id), { ...draft, extra_body: extraBody }];
      await api('/models', { method: 'PUT', body: pretty({ ...(Array.isArray(models.data) ? {} : models.data), items }) });
      setMessage('模型配置已保存。后续请求使用新的配置。'); refresh();
    } catch (reason) { setError((reason as Error).message); } finally { setSaving(''); }
  }
  async function saveSettings(event: FormEvent) {
    event.preventDefault(); setSaving('settings'); setError(''); setMessage('');
    try {
      const value = JSON.parse(settingsDraft);
      delete value.profiles;
      delete value.onebot_access_token_configured;
      const saved = await api('/settings', { method: 'PUT', body: pretty(value) });
      setSettingsDraft(editableSettings(saved)); setMessage('运行设置已保存。'); refresh();
    }
    catch (reason) { setError((reason as Error).message); } finally { setSaving(''); }
  }
  async function selectModel(profileId: string) {
    setSaving('model'); setError(''); setMessage('');
    try {
      await api('/models', { method: 'PUT', body: pretty({ items: list, active_model: profileId }) });
      setMessage('默认模型已更新。后续请求使用所选档案。'); refresh();
    } catch (reason) { setError((reason as Error).message); } finally { setSaving(''); }
  }
  function field(key: string, value: unknown) { setDraft((previous) => ({ ...previous, [key]: value })); }
  function editModel(value: RecordValue) { setDraft({ ...value }); setExtraBodyDraft(pretty(value.extra_body ?? {})); }
  const contextPolicy = settings.data?.context_policy;
  return <div className="page-stack"><ErrorNotice message={error || models.error || settings.error} />{message && <div className="notice success" role="status">{message}</div>}
    {settings.data?.context_policy?.mode === 'cache_first' && <div className="notice" role="status">当前优先复用聊天上下文：自动画像、记忆抽取、成长、群摘要及跨模型降级已停用。显式记忆管理与本地业务工具继续可用。</div>}
    {contextPolicy?.mode === 'cache_first' && <Panel title="当前模型的有效上下文预算" hint="按模型档案中的容量计算；Harness 尚未验证中转站实际容量"><Stats items={[
      { label: '预算设置', value: contextPolicy.configured_input_budget_tokens == null ? '自动' : number(contextPolicy.configured_input_budget_tokens), hint: contextPolicy.configured_input_budget_tokens == null ? '跟随当前模型容量' : '受模型容量限制的手动上限' },
      { label: '实际输入预算', value: typeof contextPolicy.effective_input_budget_tokens === 'number' ? number(contextPolicy.effective_input_budget_tokens) : '等待模型容量', hint: 'token；已预留最大输出及 1,024 token 余量' },
      { label: '模型配置容量', value: typeof contextPolicy.model_context_limit === 'number' ? number(contextPolicy.model_context_limit) : '未配置', hint: '配置值，不是中转站实测结果' },
      { label: '重建触发水位', value: typeof contextPolicy.rebuild_trigger_tokens === 'number' ? number(contextPolicy.rebuild_trigger_tokens) : '未确定', hint: `输入预算的 ${percent(contextPolicy.rebuild_trigger_ratio)}` },
      { label: '重建后目标', value: typeof contextPolicy.rebuild_target_tokens === 'number' ? number(contextPolicy.rebuild_target_tokens) : '未确定', hint: `输入预算的 ${percent(contextPolicy.rebuild_target_ratio)}；完整保留本轮输入` },
    ]} /><p className="caption">运行设置中的 cache_input_budget_tokens 设为 null 使用自动预算：模型容量 − 最大输出 − 1,024。填写正整数可限制输入预算，切换模型后仍按所选模型容量计算。</p></Panel>}
    <Panel title="模型配置" hint="每个 profile 保存独立的提供商、模型、上下文容量和单价" actions={<button className="button quiet" onClick={() => editModel(emptyModel)}>新增模型</button>}><div className="chip-row">{list.map((item) => <button className={`profile-chip ${draft.id === item.id ? 'active' : ''}`} key={item.id} onClick={() => editModel(item)}><span>{item.name ?? item.id}</span><small>{item.model}</small></button>)}{!list.length && <p className="caption">暂未配置模型。observe 和本地预览仍然可用。</p>}</div>
      {list.length > 0 && <label className="field active-profile">当前默认模型<select value={models.data?.active_model ?? ''} disabled={saving !== ''} onChange={(event) => selectModel(event.target.value)}><option value="" disabled>选择默认模型</option>{list.map((item) => <option key={item.id} value={item.id}>{item.name ?? item.id} · {item.model}</option>)}</select></label>}
      <form onSubmit={saveModel} className="form-grid model-form">
        <label className="field">Profile ID<input value={draft.id ?? ''} onChange={(event) => field('id', event.target.value)} placeholder="main" required /></label>
        <label className="field">显示名称<input value={draft.name ?? ''} onChange={(event) => field('name', event.target.value)} placeholder="日常聊天" /></label>
        <label className="field">提供商<input value={draft.provider ?? ''} onChange={(event) => field('provider', event.target.value)} required /></label>
        <label className="field">模型名称<input value={draft.model ?? ''} onChange={(event) => field('model', event.target.value)} placeholder="提供商实际模型名称" required /></label>
        <label className="field wide">API 地址<input type="url" value={draft.base_url ?? ''} onChange={(event) => field('base_url', event.target.value)} placeholder="https://api.example.invalid/v1" required /></label>
        <label className="field">API 形式<select value={draft.api_style ?? 'chat_completions'} onChange={(event) => field('api_style', event.target.value)}><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option></select></label>
        <label className="field">密钥环境变量名称<input value={draft.api_key_env ?? ''} onChange={(event) => field('api_key_env', event.target.value)} placeholder="HARNESS_MAIN_API_KEY" /><small>密钥放新目录的 .env，界面只填写变量名。</small></label>
        <label className="field">上下文容量（token）<input type="number" min="1" value={draft.context_limit ?? ''} onChange={(event) => field('context_limit', event.target.value === '' ? null : Number(event.target.value))} /><small>模型档案配置值；留空为未知。Harness 不自动推断或验证中转站容量。</small></label>
        <label className="field">最大输出（token）<input type="number" min="1" value={draft.max_output_tokens ?? ''} onChange={(event) => field('max_output_tokens', Number(event.target.value))} required /></label>
        <label className="field">推理强度<input value={draft.reasoning_effort ?? 'none'} onChange={(event) => field('reasoning_effort', event.target.value)} placeholder="none / low / medium / high" /></label>
        <label className="field">提供商缓存亲和键<select value={draft.cache_key_enabled ? 'on' : 'off'} onChange={(event) => field('cache_key_enabled', event.target.value === 'on')}><option value="off">关闭</option><option value="on">开启（提供商支持时）</option></select></label>
        <label className="field">账户标签<input value={draft.account_label ?? ''} onChange={(event) => field('account_label', event.target.value)} placeholder="区分相同提供商的不同账户" /></label>
        <label className="field">计价币种<input value={draft.currency ?? ''} onChange={(event) => field('currency', event.target.value)} placeholder="例如 USD 或 CNY；留空表示未知" /><small>历史未知币种保持未知；不同币种分别统计。</small></label>
        <label className="field">价格版本<input value={draft.price_version ?? ''} onChange={(event) => field('price_version', event.target.value)} placeholder="留空按本次单价生成版本" /><small>每次请求保存所用价格，不用新价格改写旧费用。</small></label>
        <label className="field">故障切换模型<select value={draft.fallback_profile_id ?? ''} onChange={(event) => field('fallback_profile_id', event.target.value)}><option value="">关闭自动切换</option>{list.filter((item) => item.id !== draft.id).map((item) => <option key={item.id} value={item.id}>{item.name ?? item.id}</option>)}</select></label>
        <label className="field">图片输入<select value={draft.vision === false ? 'off' : 'on'} onChange={(event) => field('vision', event.target.value === 'on')}><option value="on">支持图片</option><option value="off">仅文本</option></select></label>
        <label className="field">请求超时（秒）<input type="number" min="1" value={draft.timeout_seconds ?? 60} onChange={(event) => field('timeout_seconds', Number(event.target.value))} /></label>
        <label className="field">usage 输入计数语义<select value={draft.usage_semantics ?? 'auto'} onChange={(event) => field('usage_semantics', event.target.value)}><option value="auto">自动按响应格式判断</option><option value="total_input">输入数已包含缓存读取</option><option value="anthropic">输入与缓存读写分别计数</option></select></label>
        <label className="field">本地 tokenizer（可选）<input value={draft.tokenizer ?? ''} onChange={(event) => field('tokenizer', event.target.value)} placeholder="留空使用明确标记的估算" /></label>
        {(['input_price_per_million', 'output_price_per_million', 'cache_read_price_per_million', 'cache_write_price_per_million'] as const).map((key) => <label className="field" key={key}>{({ input_price_per_million: '输入单价', output_price_per_million: '输出单价', cache_read_price_per_million: '缓存读单价', cache_write_price_per_million: '缓存写单价' })[key]}<input type="number" min="0" step="any" value={draft[key] ?? ''} onChange={(event) => field(key, event.target.value === '' ? null : Number(event.target.value))} placeholder="未配置" /><small>每百万 token；使用同一计价币种。</small></label>)}
        <label className="field wide">额外请求参数（JSON）<textarea className="code-editor" rows={4} value={extraBodyDraft} onChange={(event) => setExtraBodyDraft(event.target.value)} spellCheck={false} /><small>提供商需要的额外请求字段；留空对象表示不附加参数。</small></label>
        <div className="form-actions wide"><button className="button primary" disabled={saving !== '' || models.loading} type="submit">{saving === 'model' ? '正在保存…' : '保存模型'}</button><p className="caption">保存配置不会调用模型。</p></div>
      </form>{models.data && <JsonBlock value={models.data} label="模型配置完整响应" />}
    </Panel><Panel title="运行设置" hint="编辑上下文、路由和后台选项；模型档案在上方单独保存"><form onSubmit={saveSettings}><label className="field">当前配置 JSON<textarea className="code-editor" value={settingsDraft} onChange={(event) => setSettingsDraft(event.target.value)} rows={16} spellCheck={false} required /></label><div className="form-actions"><button className="button primary" disabled={saving !== '' || !settings.data} type="submit">{saving === 'settings' ? '正在保存…' : '保存运行设置'}</button><button className="button quiet" type="button" onClick={() => { if (settings.data) setSettingsDraft(editableSettings(settings.data)); }} disabled={!settings.data}>恢复当前配置</button></div></form></Panel>
  </div>;
}

function PreviewPage({ sessions, models }: { sessions: RecordValue[]; models: RecordValue[] }) {
  const [sessionKey, setSessionKey] = useState('');
  const [customKey, setCustomKey] = useState('');
  const [profile, setProfile] = useState('');
  const [text, setText] = useState('');
  const [result, setResult] = useState<RecordValue | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { setResult(await api('/preview', { method: 'POST', body: pretty({ session_key: customKey || sessionKey, text, ...(profile ? { profile_id: profile } : {}) }) })); }
    catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  return <div className="page-stack"><Panel title="预览下一轮请求" hint="只组装本地上下文和路由，不请求模型，不发送 QQ"><form className="form-grid" onSubmit={submit}><SessionSelect value={sessionKey} onChange={setSessionKey} sessions={sessions} required={!customKey} /><label className="field">新会话键（可选）<input value={customKey} onChange={(event) => setCustomKey(event.target.value)} placeholder="group:900000001" /><small>填写后优先使用这个会话键。</small></label><label className="field wide">模型 profile<select value={profile} onChange={(event) => setProfile(event.target.value)}><option value="">使用当前默认模型</option>{models.map((model) => <option key={model.id} value={model.id}>{model.name ?? model.id} · {model.model}</option>)}</select></label><label className="field wide">输入内容<textarea rows={5} value={text} onChange={(event) => setText(event.target.value)} placeholder="输入一条聊天消息或工具请求，检查它会如何处理。" required /></label><div className="form-actions wide"><button className="button primary" type="submit" disabled={busy}>{busy ? '正在预览…' : '生成请求预览'}</button><span className="caption">远端模型调用：0</span></div></form><ErrorNotice message={error} /></Panel>
    {result ? <Panel title="预览结果" hint="此结果仅代表本地构建，不是实发请求或实际命中"><div className="chip-row"><Badge value={result.route?.kind ?? result.route?.type ?? result.route ?? '本地预览'} />{result.profile_id && <span className="badge neutral">{result.profile_id}</span>}</div><ContextBudget telemetry={result.telemetry} /><LayerView layers={result.layers ?? result.context?.layers ?? []} /><JsonBlock value={result} label="完整预览（路由、分层及 payload）" open /></Panel> : <Panel><Empty title="先预览，再运行" detail="查看本地工具能否识别，以及模型真正需要接收哪些内容。" icon="eye" /></Panel>}
  </div>;
}

function toolValueMatches(actual: unknown, expected: any): boolean {
  if (Array.isArray(expected)) return expected.some((item) => toolValueMatches(actual, item));
  if (expected && typeof expected === 'object' && 'prefix' in expected) return String(actual ?? '').startsWith(String(expected.prefix));
  return expected == null ? actual == null : actual === expected;
}

function toolCondition(when: RecordValue | undefined, value: RecordValue): boolean {
  return !when || Object.entries(when).every(([key, expected]) => toolValueMatches(value[key], expected));
}

function toolParameter(definition: RecordValue, value: RecordValue): RecordValue | null {
  if (!toolCondition(definition.when, value)) return null;
  if (definition.type !== 'variant') return definition;
  const variant = (definition.variants ?? []).find((item: RecordValue) => toolCondition(item.when, value));
  return variant ? { ...definition, ...variant } : null;
}

const qqIds = (text: string): number[] => [...new Set(text.split(/[\s,，、;；]+/).filter(Boolean).map(Number).filter((id) => Number.isSafeInteger(id) && id > 0))];

function UserIdsInput({ value, change, definition }: { value: unknown; change: (value: unknown) => void; definition: RecordValue }) {
  const ids = Array.isArray(value) ? value : [];
  const [text, setText] = useState(ids.join('、'));
  useEffect(() => { if (qqIds(text).join(',') !== ids.join(',')) setText(ids.join('、')); }, [value]);
  return <input value={text} placeholder={definition.placeholder ?? '多个 QQ 号用逗号或空格分隔'} required={definition.required} onChange={(event) => {
    const next = event.target.value;
    const valid = next.split(/[\s,，、;；]+/).filter(Boolean).every((id) => /^\d+$/.test(id) && Number.isSafeInteger(Number(id)) && Number(id) > 0);
    event.target.setCustomValidity(valid ? '' : '请填写有效 QQ 号，用逗号或空格分隔。');
    setText(next); change(qqIds(next));
  }} />;
}

function ToolParameters({ definitions, value, change, groups }: { definitions: RecordValue; value: RecordValue; change: (key: string, value: unknown) => void; groups: RecordValue[] }) {
  return <div className="tool-parameter-grid wide">{Object.entries(definitions).map(([key, base]: [string, any]) => {
    const definition = toolParameter(base, value);
    if (!definition) return null;
    const label = definition.label ?? humanLabel(key);
    if (definition.type === 'checkbox') return <label className="toggle-field" key={key}><input type="checkbox" checked={Boolean(value[key])} onChange={(event) => change(key, event.target.checked)} /><span>{label}{definition.description && <small>{definition.description}</small>}</span></label>;
    if (definition.type === 'group-multi') return <fieldset className="tool-group-options wide" key={key}><legend>{label}{definition.required ? '（至少选择一个）' : ''}</legend>{groups.length ? groups.map((group) => <label className="toggle-field" key={group.group_id}><input type="checkbox" checked={Array.isArray(value[key]) && value[key].includes(Number(group.group_id))} onChange={(event) => { const selected = Array.isArray(value[key]) ? value[key] : []; change(key, event.target.checked ? [...selected, Number(group.group_id)] : selected.filter((id: number) => id !== Number(group.group_id))); }} /><span>群 · {group.group_name || '未命名群'}<small>群号 {group.group_id}</small></span></label>) : <p className="caption">暂无机器人当前所在的群。</p>}{definition.description && <p className="caption wide">{definition.description}</p>}</fieldset>;
    if (definition.type === 'select' || definition.type === 'group-key') {
      const options = [...(definition.options ?? []).filter((option: RecordValue) => toolCondition(option.when, value)), ...(definition.type === 'group-key' ? (definition.group_options ?? []).flatMap((option: RecordValue) => groups.map((group) => ({ value: `${option.prefix}${group.group_id}`, label: `${option.label} · 群：${group.group_name || '未命名群'}（${group.group_id}）` }))) : [])];
      const selected = value[key] ?? definition.default;
      const hasEmptyOption = options.some((option: RecordValue) => option.value === '');
      return <label className="field" key={key}>{label}<select value={options.some((option: RecordValue) => option.value === selected) ? String(selected) : ''} onChange={(event) => change(key, event.target.value === '' ? undefined : options.find((option: RecordValue) => String(option.value) === event.target.value)?.value)} required={definition.required}>{!hasEmptyOption && <option value="">请选择</option>}{options.map((option: RecordValue) => <option key={String(option.value)} value={String(option.value)}>{option.label ?? humanLabel(option.value)}</option>)}</select>{definition.description && <small>{definition.description}</small>}</label>;
    }
    if (definition.type === 'user-multi') return <label className="field" key={key}>{label}<UserIdsInput value={value[key]} change={(ids) => change(key, ids)} definition={definition} />{definition.description && <small>{definition.description}</small>}</label>;
    if (definition.type === 'textarea') return <label className="field wide" key={key}>{label}<textarea value={value[key] ?? ''} rows={definition.rows ?? 4} placeholder={definition.placeholder} required={definition.required} onChange={(event) => change(key, event.target.value || undefined)} />{definition.description && <small>{definition.description}</small>}</label>;
    if (definition.type === 'time-range') return <fieldset className="tool-group-options wide" key={key}><legend>{label}</legend>{['开始时间', '结束时间'].map((title, index) => <label className="field" key={title}>{title}<input type="time" value={Array.isArray(value[key]) ? value[key][index] ?? '' : ''} required={definition.required} onChange={(event) => { const range = Array.isArray(value[key]) ? [...value[key]] : ['', '']; range[index] = event.target.value; change(key, range); }} /></label>)}{definition.description && <p className="caption wide">{definition.description}</p>}</fieldset>;
    return <label className="field" key={key}>{label}<input type={definition.type === 'number' ? 'number' : 'text'} value={value[key] ?? ''} min={definition.min} max={definition.max} step={definition.type === 'number' ? definition.step ?? 'any' : undefined} placeholder={definition.placeholder} required={definition.required} onChange={(event) => change(key, event.target.value === '' ? undefined : definition.type === 'number' ? Number(event.target.value) : event.target.value)} />{definition.description && <small>{definition.description}</small>}</label>;
  })}</div>;
}

function ToolsPage({ revision, refresh }: { revision: number; refresh: () => void }) {
  const tools = useLoad('/tools', revision);
  const jobs = useLoad('/jobs', revision);
  const sessions = useLoad('/sessions', revision);
  const business = useLoad('/analytics/business', revision);
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [toolName, setToolName] = useState('');
  const [sessionKey, setSessionKey] = useState('');
  const [toolArguments, setToolArguments] = useState('{}');
  const [toolText, setToolText] = useState('');
  const [deliver, setDeliver] = useState(false);
  const [runResult, setRunResult] = useState<RecordValue | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const toolList = rows(tools.data);
  const selectedTool = toolList.find((tool) => tool.name === toolName) ?? toolList[0];
  const activeGroups = rows(business.data?.groups).filter((group) => group.enabled !== false && group.enabled !== 0);
  const knownSessions = rows(sessions.data).filter((session) => session.enabled !== false && session.enabled !== 0);
  const toolSessionMap = new Map(knownSessions.filter((session) => sessionScope(session) === '私聊' || !business.data).map((session) => [String(session.key ?? session.session_key), session]));
  for (const group of activeGroups) {
    const key = `group:${group.group_id}`;
    const session = knownSessions.find((item) => (item.key ?? item.session_key) === key);
    toolSessionMap.set(key, { ...session, ...group, key, session_key: key, scope_label: `群：${group.group_name || '未命名群'}` });
  }
  const toolSessions = [...toolSessionMap.values()];
  let argumentValues: RecordValue = {};
  try { const parsed = JSON.parse(toolArguments || '{}'); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) argumentValues = parsed; } catch { /* The advanced editor reports malformed JSON when submitted. */ }
  function changeArgument(key: string, value: unknown) {
    const next = { ...argumentValues, [key]: value };
    for (const [name, definition] of Object.entries(selectedTool?.parameters ?? {}) as [string, RecordValue][]) {
      const before = toolParameter(definition, argumentValues);
      const after = toolParameter(definition, next);
      if (!after || (name !== key && definition.type === 'variant' && before?.type !== after.type)) delete next[name];
      if (after?.type === 'select' && next[name] !== undefined && !(after.options ?? []).some((option: RecordValue) => option.value === next[name] && toolCondition(option.when, next))) delete next[name];
    }
    setToolArguments(pretty(next) ?? '{}');
  }
  useEffect(() => {
    if (!toolName && toolList[0]?.name) setToolName(toolList[0].name);
  }, [toolName, toolList]);
  useEffect(() => {
    if (!selectedTool) return;
    const definitions = selectedTool.parameters ?? {};
    const defaults = Object.fromEntries(Object.entries(definitions).filter(([, value]: [string, any]) => value?.default !== undefined).map(([key, value]: [string, any]) => [key, value.default]));
    setToolArguments(pretty(defaults) ?? '{}');
  }, [toolName]);
  async function changeTool(tool: RecordValue) {
    setPending(`tool:${tool.name}`); setError('');
    try {
      await api(`/tools/${encodeURIComponent(tool.name)}/enabled`, { method: 'PUT', body: JSON.stringify({ enabled: !tool.enabled }) });
      refresh();
    } catch (reason) { setError((reason as Error).message); } finally { setPending(''); }
  }
  async function changeJob(id: string, action: 'pause' | 'resume') {
    setPending(`job:${id}`); setError('');
    try { await api(`/jobs/${encodeURIComponent(id)}/${action}`, { method: 'POST' }); refresh(); }
    catch (reason) { setError((reason as Error).message); } finally { setPending(''); }
  }
  async function runTool(event: FormEvent) {
    event.preventDefault(); setRunBusy(true); setError(''); setRunResult(null);
    try {
      let args: RecordValue;
      try {
        args = JSON.parse(toolArguments || '{}');
      } catch {
        throw new Error('参数 JSON 格式不正确，请检查括号、引号和逗号。');
      }
      if (!args || Array.isArray(args) || typeof args !== 'object') throw new Error('参数必须是 JSON 对象。');
      const consoleUserId = Number(tools.data?.console_user_id ?? 0);
      const userId = sessionKey.startsWith('private:') ? Number(sessionKey.split(':')[1]) : consoleUserId;
      const result = await api('/tools/run', { method: 'POST', body: pretty({ name: toolName, arguments: args, ...(sessionKey ? { session_key: sessionKey } : {}), text: toolText, ...(userId ? { user_id: userId } : {}), deliver }) });
      setRunResult(result); refresh();
    } catch (reason) { setError((reason as Error).message); }
    finally { setRunBusy(false); }
  }
  return <div className="page-stack"><ErrorNotice message={error || tools.error || jobs.error} />
    <Panel title="执行本地工具" hint="选择工具、会话并填写参数；默认不发送工具返回结果。" actions={<Loading active={tools.loading || sessions.loading} />}>
      <form className="tool-run-form" onSubmit={runTool}>
        <label className="field">工具<select value={selectedTool?.name ?? ''} onChange={(event) => setToolName(event.target.value)} required><option value="">选择工具</option>{toolList.map((tool) => <option key={tool.name} value={tool.name}>{tool.label ?? humanLabel(tool.name)}</option>)}</select><small>{selectedTool?.description ?? '选择一个已注册的本地能力。'}</small></label>
        <label className="field">执行会话<select value={sessionKey} onChange={(event) => setSessionKey(event.target.value)}><option value="">当前操作者私聊</option>{toolSessions.map((session) => <option key={session.key ?? session.session_key} value={session.key ?? session.session_key}>{sessionLabel(session)}{sessionDetail(session) ? `（${sessionDetail(session)}）` : ''}</option>)}</select><small>群工具请明确选择“群”会话；权限使用当前控制台操作者身份。</small></label>
        <label className="field wide">触发文本（可选）<input value={toolText} onChange={(event) => setToolText(event.target.value)} placeholder="需要关键词或查询内容时填写" /></label>
        {selectedTool?.parameters && Object.keys(selectedTool.parameters).length > 0 && <ToolParameters definitions={selectedTool.parameters} value={argumentValues} change={changeArgument} groups={activeGroups} />}
        <details className="json-block tool-advanced wide"><summary>高级参数 JSON{selectedTool?.parameters && Object.keys(selectedTool.parameters).length ? '（与上方控件同步）' : ''}</summary><label className="field"><textarea className="code-editor" rows={8} value={toolArguments} onChange={(event) => setToolArguments(event.target.value)} spellCheck={false} aria-label="高级工具参数 JSON" /><small>需要未列出的参数时在这里补充；参数必须为 JSON 对象。</small></label></details>
        <label className="toggle-field wide"><input type="checkbox" checked={deliver} onChange={(event) => setDeliver(event.target.checked)} /><span>执行后发送到所选 QQ 会话（默认关闭）</span></label>
        <div className="form-actions wide"><button className="button primary" type="submit" disabled={runBusy || !selectedTool}>{runBusy ? '正在执行…' : '执行工具'}</button><span className="caption">默认在控制台展示返回结果；工具自身的配置和业务操作仍会生效。</span></div>
      </form>
      {runResult && <div className="tool-run-result"><div className="chip-row"><Badge value={runResult.status} /><span className="badge neutral">{selectedTool?.label ?? humanLabel(toolName)}</span></div><p>{runResult.text ?? runResult.message ?? '工具已返回结果。'}</p><JsonBlock value={runResult.data ?? runResult} label="查看工具返回数据" open /></div>}
    </Panel>
    <Panel title="本地工具" hint="查看并启停业务工具；开关只保存新系统配置，不执行工具" actions={<Loading active={tools.loading} />}>
      {rows(tools.data).length ? <div className="tool-grid">{rows(tools.data).map((tool, index) => <article className="tool-card" key={tool.id ?? tool.name ?? index}>
        <div className="tool-card-heading"><span className="tool-icon"><Icon name="tool" /></span><Badge value={typeof tool.enabled === 'boolean' ? (tool.enabled ? '已启用' : '已停用') : tool.status_label ?? humanLabel(tool.status ?? 'registered')} /></div>
        <h3>{tool.label ?? tool.title ?? humanLabel(tool.name ?? tool.id)}</h3><p>{tool.description ?? '无描述'}</p>
        <div className="chip-row">{typeof tool.token_cost === 'number' && <span className="badge neutral">{number(tool.token_cost)} token</span>}{tool.kind && <span className="badge neutral">{humanLabel(tool.kind)}</span>}</div>
        <div className="tool-card-controls"><span className="caption">{tool.group_ids?.length ? `指定群：${tool.group_ids.map((id: number) => `${activeGroups.find((group) => Number(group.group_id) === Number(id))?.group_name || '未记录群名'}（${id}）`).join('、')}` : '未设置额外群限制'}</span>
          {typeof tool.enabled === 'boolean' && <button className="button quiet small" disabled={pending !== '' || tools.loading} aria-label={`${tool.enabled ? '停用' : '启用'} ${tool.name}`} onClick={() => changeTool(tool)}>{pending === `tool:${tool.name}` ? '保存中…' : tool.enabled ? '停用' : '启用'}</button>}
        </div><JsonBlock value={tool} label="参数与能力详情" />
      </article>)}</div> : <Empty title="尚未注册工具" detail="工具迁移后会显示可执行能力、参数和状态。" icon="tool" />}
    </Panel>
    <Panel title="后台工作" hint="排队工作可暂停和恢复；正在执行的请求等待当前步骤结束" actions={<Loading active={jobs.loading} />}>{rows(jobs.data).length ? <div className="table-scroll"><table><thead><tr><th>工作</th><th>状态</th><th>创建时间</th><th>操作</th><th>详情</th></tr></thead><tbody>{rows(jobs.data).map((job, index) => <tr key={job.id ?? index}><td><strong>{job.kind_label ?? humanLabel(job.kind ?? job.name ?? job.id)}</strong><small>{job.scope_label ?? sessionLabel(job)}</small></td><td><Badge value={job.status_label ?? humanLabel(job.status)} /></td><td>{date(job.created_at)}</td><td>{['queued', 'paused'].includes(job.status) && <button className="button quiet small" disabled={pending !== ''} onClick={() => changeJob(job.id, job.status === 'paused' ? 'resume' : 'pause')}>{pending === `job:${job.id}` ? '处理中…' : job.status === 'paused' ? '恢复' : '暂停'}</button>}</td><td><JsonBlock value={job} label="查看详情" /></td></tr>)}</tbody></table></div> : <Empty title="暂无后台工作" detail="不会从旧快照恢复或补发历史 pending 任务。" icon="layers" />}</Panel>
  </div>;
}

function ExperimentsPage({ models, refresh }: { models: RecordValue[]; refresh: () => void }) {
  const [kind, setKind] = useState('cache_append');
  const [profile, setProfile] = useState('');
  const [paid, setPaid] = useState(false);
  const [prewarm, setPrewarm] = useState(false);
  const [text, setText] = useState('我们下次继续讨论今天的安排。');
  const [payload, setPayload] = useState('');
  const [result, setResult] = useState<RecordValue | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  async function run(event: FormEvent, type: 'experiment' | 'replay') {
    event.preventDefault(); setBusy(type); setError('');
    try {
      const body = type === 'experiment' ? { kind, text, ...(profile ? { profile_id: profile } : {}), paid, prewarm: paid && prewarm }
        : { event: JSON.parse(payload), live: false, send_qq: false };
      setResult(await api(type === 'experiment' ? '/experiments' : '/replay', { method: 'POST', body: pretty(body) })); refresh();
    } catch (reason) { setError((reason as Error).message); } finally { setBusy(''); }
  }
  return <div className="page-stack"><div className="notice">{paid ? '真实实验将调用两个模型请求并按提供商计费；只使用合成实验会话，不发送 QQ。' : '默认离线：不调用付费模型，不发送 QQ。离线前缀比较不能证明实际缓存命中。'}</div><ErrorNotice message={error} /><div className="two-columns"><Panel title="缓存与上下文实验" hint="检查追加、模型切换和前缀变化"><form onSubmit={(event) => run(event, 'experiment')} className="vertical-form"><label className="field">实验类型<select value={kind} onChange={(event) => setKind(event.target.value)}><option value="cache_append">同会话追加</option><option value="cold_warm">冷启动 / 热请求</option><option value="model_switch">模型切换</option><option value="prefix_change">稳定前缀变更</option><option value="tool_isolation">工具与聊天隔离</option><option value="compaction">压缩快照切换</option></select></label><label className="field">模型 profile<select value={profile} onChange={(event) => setProfile(event.target.value)}><option value="">使用默认模型</option>{models.map((model) => <option key={model.id} value={model.id}>{model.name ?? model.id}</option>)}</select></label><label className="field">实验输入<textarea rows={3} value={text} onChange={(event) => setText(event.target.value)} required /></label><label className="toggle-field"><input type="checkbox" checked={paid} onChange={(event) => setPaid(event.target.checked)} /><span>真实模型调用（会计费）</span></label>{paid && <label className="toggle-field"><input type="checkbox" checked={prewarm} onChange={(event) => setPrewarm(event.target.checked)} /><span>记录为手动预热用途</span></label>}<button className="button primary" disabled={busy !== ''} type="submit">{busy === 'experiment' ? '正在运行…' : paid ? '运行真实模型实验（会计费）' : '运行离线实验'}</button></form></Panel>
    <Panel title="OneBot 消息回放" hint="输入一条实际或合成 OneBot 事件 JSON；回放始终不请求模型、不发 QQ"><form onSubmit={(event) => run(event, 'replay')} className="vertical-form"><label className="field">事件 JSON<textarea className="code-editor" value={payload} onChange={(event) => setPayload(event.target.value)} rows={9} spellCheck={false} placeholder={'{\n  "post_type": "message",\n  "message_type": "group",\n  "group_id": 900000001,\n  "user_id": 900000002,\n  "self_id": 900000003,\n  "message_id": 1,\n  "message": [{"type": "text", "data": {"text": "你好"}}]\n}'} required /></label><button className="button primary" disabled={busy !== ''} type="submit">{busy === 'replay' ? '正在回放…' : '回放一次'}</button></form></Panel></div>{result && <Panel title="执行结果" hint="本次实验或回放的实际返回"><div className="chip-row"><Badge value={result.status} /><span className="badge neutral">模型调用 {number(result.model_calls)}</span><span className="badge neutral">QQ 发送 {number(result.qq_writes)}</span></div>{result.result?.reason && <div className="notice">{result.result.reason}</div>}{result.result?.diff && <p className="caption">共同前缀 {number(result.result.diff.common_prefix_bytes)} bytes；变化层 {result.result.diff.changed_layers?.join('、') || '无'}。实际缓存以 usage 为准。</p>}{Array.isArray(result.result?.steps) && <div className="two-columns">{result.result.steps.map((step: RecordValue, index: number) => <div key={index}><h3>{step.label}</h3><p className="caption">{step.preview?.profile_id} · {step.preview?.model || '未配置模型名称'}</p>{step.usage && <Stats items={[{ label: '输入 token', value: number(step.usage.input_tokens) }, { label: '缓存读 token', value: number(step.usage.cache_read_tokens) }, { label: '命中率', value: percent(step.usage.cache_ratio) }, { label: '估算成本', value: number(step.usage.cost, 6) }]} />}<LayerView layers={step.preview?.layers ?? []} /></div>)}</div>}<JsonBlock value={result} label="完整结果" open={!result.result?.steps} /></Panel>}
  </div>;
}

export default function App() {
  const initial = window.location.hash.slice(1) as Tab;
  const [tab, setTab] = useState<Tab>(tabs.some((item) => item.id === initial) ? initial : 'sessions');
  const [initialSessionKey, setInitialSessionKey] = useState('');
  const [initialRequestId, setInitialRequestId] = useState('');
  const [initialUserId, setInitialUserId] = useState<number | undefined>();
  const [revision, setRevision] = useState(0);
  const [theme, setTheme] = useState<'light' | 'dark'>(() => localStorage.getItem('harness-theme') === 'dark' ? 'dark' : 'light');
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('harness-theme', theme);
    window.dispatchEvent(new Event('harness-theme'));
  }, [theme]);
  const [connected, setConnected] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const refresh = useCallback(() => { setRevision((value) => value + 1); setLastUpdated(new Date()); }, []);
  const status = useLoad('/status', revision);
  const sessions = useLoad('/sessions', revision);
  const models = useLoad('/models', revision);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [tab]);
  useEffect(() => {
    const updateTab = () => { const value = window.location.hash.slice(1) as Tab; if (tabs.some((item) => item.id === value)) setTab(value); };
    window.addEventListener('hashchange', updateTab);
    return () => window.removeEventListener('hashchange', updateTab);
  }, []);
  useEffect(() => {
    const stream = new EventSource('/api/events/stream');
    let pending: ReturnType<typeof setTimeout> | null = null;
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    const changed = () => { if (!pending) pending = setTimeout(() => { pending = null; refresh(); }, 350); };
    stream.onmessage = changed;
    stream.addEventListener('status', changed);
    return () => { stream.close(); if (pending) clearTimeout(pending); };
  }, [refresh]);
  const current = tabs.find((item) => item.id === tab)!;
  const mode = status.data?.mode ?? '未知';
  const transportConnected = status.data?.transport?.connected;
  return <div className="app-shell"><a className="skip-link" href="#main-content">跳到主要内容</a><aside className="sidebar"><div className="brand"><span className="brand-mark"><svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true"><path d="M4 5h18M13 5v16M7 11h12" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" /><circle cx="13" cy="21" r="2" fill="currentColor" /></svg></span><div><strong>糖糖</strong><span>本地控制台</span></div></div><MainNavigation tab={tab} /><div className="sidebar-bottom"><span className={`connection-dot ${connected ? 'online' : ''}`} /><span>{connected ? '事件流已连接' : '事件流未连接'}</span><p>本地工作空间<br />独立于旧机器人运行</p></div></aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumbs"><span>糖糖控制台</span><span>/</span><strong>{current.name}</strong></div><div className="header-actions"><span className="mode-label">{humanLabel(mode)}</span><button className="button quiet small" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')} aria-label={theme === 'light' ? '切换深色主题' : '切换浅色主题'}>{theme === 'light' ? '深色' : '浅色'}</button><button className="button quiet small" onClick={refresh} aria-label="刷新所有数据"><Icon name="refresh" size={17} />刷新</button></div></header><main id="main-content"><div className="page-heading"><div><div className="eyebrow">本地机器人工作空间</div><h1>{current.name}</h1><p>{current.subtitle}</p></div><div className="runtime-status"><span className={`connection-dot ${transportConnected === true ? 'online' : ''}`} />SnowLuma：{transportConnected === true ? '已连接' : transportConnected === false ? '未连接' : '未知'}<small>{lastUpdated ? `更新于 ${lastUpdated.toLocaleTimeString('zh-CN', { hour12: false })}` : '等待读取运行状态'}</small></div></div>
      {status.error && <div className="notice error" role="alert">无法连接 Harness 后端。请启动本地服务后刷新。<span className="error-detail">{status.error}</span></div>}
      {mode === 'observe' && <div className="observe-banner"><span className="badge neutral">观察模式</span><span>正在捕获事件；自动聊天、远端模型调用和 QQ 发送均未开启。</span></div>}
      {(['overview', 'traffic', 'cache', 'session-analytics', 'people', 'operations', 'business-analytics'] as string[]).includes(tab) && <AnalyticsDashboard
        page={tab as AnalyticsPage} revision={revision} initialSessionKey={initialSessionKey} initialUserId={initialUserId}
        onNavigate={(page) => { window.location.hash = page; }}
        onOpenRequest={(id) => { setInitialRequestId(id); window.location.hash = 'context'; }}
        onOpenContext={(sessionKey) => { setInitialSessionKey(sessionKey); setInitialUserId(undefined); window.location.hash = 'sessions'; }}
        onOpenPerson={(sessionKey, userId) => { setInitialSessionKey(sessionKey); setInitialUserId(userId); window.location.hash = 'people'; }} />}
      {tab === 'sessions' && <SessionPage revision={revision} sessions={sessions} initialSessionKey={initialSessionKey} onOpenRequest={(id) => { setInitialRequestId(id); window.location.hash = 'context'; }} onOpenCache={(key) => { setInitialSessionKey(key); setInitialUserId(undefined); window.location.hash = 'session-analytics'; }} />}
      {(tab === 'ranking' || tab === 'groups') && <ConsoleBusiness view={tab} revision={revision} refresh={refresh} />}
      {tab === 'context' && <ContextPage revision={revision} models={rows(models.data)} initialRequestId={initialRequestId} />}
      {tab === 'metrics' && <MetricsPage revision={revision} models={rows(models.data)} />}
      {tab === 'models' && <ModelPage revision={revision} refresh={refresh} />}
      {tab === 'preview' && <PreviewPage sessions={rows(sessions.data)} models={rows(models.data)} />}
      {tab === 'tools' && <ToolsPage revision={revision} refresh={refresh} />}
      {tab === 'experiments' && <ExperimentsPage models={rows(models.data)} refresh={refresh} />}
    </main><footer className="page-footer"><span>TangtangHarness · 独立运行时</span><span>以真实请求和交付记录为准</span></footer></div>
  </div>;
}
