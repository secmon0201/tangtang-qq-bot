export type RecordValue = Record<string, any>;

/** Human-facing labels for values that are persisted in English identifiers. */
export const UI_LABELS: Record<string, string> = {
  completed: '已完成', complete: '已完成', delivered: '已送达', success: '成功', ok: '成功', connected: '已连接', enabled: '已启用',
  failed: '失败', error: '错误', disconnected: '未连接', running: '执行中', queued: '排队中',
  paused: '已暂停', cancelled: '已取消', disabled: '已停用', pending: '等待中', observed: '观察记录',
  generated: '已生成', silent: '静默', quiet: '静默', offline: '离线', active: '运行中', idle: '空闲', unknown: '未知',
  chat: '聊天', continuation: '免呼叫续聊', proactive: '主动聊天', memory: '记忆整理', cognition: '认知整理', growth: '公共成长',
  summary: '群摘要', compaction: '上下文压缩', profile: '个人画像', profile_review: '画像复核',
  experiment: '实验', prewarm: '预热', tool: '本地工具', group: '群', private: '私聊', direct: '私聊',
  group_summary: '群摘要', persona_memory: '人格记忆', persona_profile: '个人画像', profile_generate: '画像生成',
  group_topics: '群话题整理', speech: '发言统计', delivery: '消息送达',
  registered: '已注册', budget_wait: '预算等待', budget_waiting: '预算等待', waiting: '等待中', rejected: '已拒绝',
  skipped: '已跳过', expired: '已过期', file: '文件', background: '后台', foreground: '前台',
  fixed: '固定角色与输出约定', snapshot: '上下文快照', history: '已完成对话', current: '本轮输入',
  routing: '路由与会话信息', raw_events: '新增群消息', own_events: '机器人消息', impressions: '个人印象',
  legacy_memory: '导入记忆', legacy_cognition: '导入认知', group_state: '当前会话状态', tools: '本地工具结果',
  dialogue_examples: '对白风格参考', public_topics: '公共话题', dynamic: '本轮资料', local: '本地', remote: '远端',
  responses: 'Responses 接口', chat_completions: 'Chat Completions 接口', super_admin: '主管理员', member: '群成员',
  true: '是', false: '否',
  observe: '观察模式', live: '实际运行', shadow: '影子验证',
  group_not_present: '已退群，停止处理', ignored_group_not_present: '忽略已退群消息',
  clarification: '需要补充参数', denied: '权限不足', empty: '暂无数据', blocked: '已过滤',
  forwarded: '已转发', budget_paused: '预算等待', ended: '已结束',
  roulette: '俄罗斯转盘', bomb: '定时炸弹', idiom_bomb: '成语炸弹', dice: '幸运骰局', guess: '猜数字',
};

export function humanLabel(value: unknown, fallback = '未记录'): string {
  if (value == null || value === '') return fallback;
  const raw = String(value);
  if (UI_LABELS[raw]) return UI_LABELS[raw];
  const normalized = raw.toLowerCase();
  if (UI_LABELS[normalized]) return UI_LABELS[normalized];
  // User names, full group names and arbitrary text must retain their exact
  // spelling; only known persisted enum values are translated.
  return raw;
}

/** Translate structured context provenance while preserving arbitrary source text. */
export function contextSourceLabel(value: unknown): string {
  if (value == null || value === '') return '未记录来源';
  const source = String(value);
  if (source === 'persona + output protocol') return '角色资料与输出约定';
  const snapshot = /^snapshot:(\d+)$/.exec(source);
  if (snapshot) return `上下文快照，第 ${snapshot[1]} 版`;
  const history = /^completed turns after (.+)$/.exec(source);
  if (history) return `快照之后的已完成对话（起点 ${history[1]}）`;
  const session = /^(group|private):(\d+)$/.exec(source);
  if (session) return `${session[1] === 'group' ? '本群资料，群号' : '本私聊资料，QQ'} ${session[2]}`;
  const event = /^\d+:(group|private):(\d+):(.+)$/.exec(source);
  if (event) return `${event[1] === 'group' ? '群消息，群号' : '私聊消息，QQ'} ${event[2]} · 消息 ${event[3]}`;
  return humanLabel(source, '未记录来源');
}

export function sessionScope(value: RecordValue | string | null | undefined): '群' | '私聊' {
  if (typeof value === 'string') return /^(group|群)[:_-]/i.test(value) ? '群' : '私聊';
  const raw = String(value?.session_key ?? value?.key ?? value?.scope_kind ?? value?.session_type ?? value?.kind ?? value?.type ?? '');
  return /group|群/i.test(raw) ? '群' : '私聊';
}

/** Prefer the complete group name and nickname; aliases remain a secondary detail. */
export function sessionLabel(value: RecordValue | string | null | undefined): string {
  if (typeof value === 'string') return `${sessionScope(value)} · ${value || '未命名会话'}`;
  const key = String(value?.session_key ?? value?.key ?? '');
  const scope = sessionScope(value);
  if (value?.scope_label) {
    const label = String(value.scope_label).trim();
    // Some older payloads already prepended the kind before the newer
    // scope label (for example "私聊 · 私聊：昵称（QQ号）"). Keep the
    // explicit kind marker once while preserving the backend identity.
    const duplicatePrefix = `${scope} · ${scope}：`;
    if (label.startsWith(duplicatePrefix)) return `${scope}：${label.slice(duplicatePrefix.length)}`;
    return label;
  }
  const name = scope === '群'
    ? (value?.group_name ?? value?.group_title ?? value?.full_group_name ?? value?.title ?? value?.name ?? '')
    : (value?.user_nickname ?? value?.nickname ?? value?.user_name ?? value?.member_name ?? value?.title ?? value?.name ?? '');
  const id = scope === '群' ? value?.group_id : value?.user_id;
  const visible = String(name || id || key || '未命名会话');
  return `${scope} · ${visible}`;
}

export function sessionDetail(value: RecordValue | string | null | undefined): string {
  if (typeof value === 'string') return value;
  const scope = sessionScope(value); const id = scope === '群' ? value?.group_id : value?.user_id;
  return id == null ? '' : String(id);
}

export async function api<T = RecordValue>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.detail ?? payload.error ?? `请求失败 (${response.status})`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return payload as T;
}

export function rows(payload: any): RecordValue[] {
  return Array.isArray(payload) ? payload : payload?.items ?? [];
}

export const pretty = (value: unknown) => JSON.stringify(value, null, 2);

export function displayJson(value: unknown): string | undefined {
  return JSON.stringify(value, (_key, item) => {
    if (typeof item === 'string' && item.startsWith('data:image/') && item.includes(';base64,')) {
      const split = item.indexOf(',');
      return `${item.slice(0, split + 1)}[图片编码省略 ${item.length - split - 1} 字符；复制 JSON 可取得原值]`;
    }
    return item;
  }, 2);
}

export function number(value: unknown, maximumFractionDigits = 0): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits }).format(value)
    : '未报告';
}

export function percent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${number(value * 100, 1)}%` : '未报告';
}

export function date(value: unknown): string {
  if (value == null) return '未记录';
  const parsed = new Date(typeof value === 'number' ? value * 1000 : String(value));
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('zh-CN', { hour12: false });
}
