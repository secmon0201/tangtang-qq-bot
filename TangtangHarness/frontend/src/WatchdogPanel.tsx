import { useState } from 'react';
import { humanLabel, number, rows } from './api';
import type { RecordValue } from './api';
import { DataTable, Panel } from './charts';
import { useApiData } from './useApiData';
import './watchdog.css';

const serviceLabels: Record<string, string> = { harness: 'Harness', core: 'Core', snowluma: 'SnowLuma', speech: '语音', supervisor: '看门狗', watchdog: '看门狗' };
const labels: Record<string, string> = {
  running: '运行中', healthy: '正常', ready: '已就绪', stopped: '已停止', missing: '进程已退出',
  unhealthy: '健康检查异常', degraded: '进程在运行，连接或就绪异常', starting: '启动中', recovering: '恢复中', disabled: '已关闭',
  conflict: '端口冲突', blocked: '等待处理', unknown: '尚未检查', unavailable: '不可用',
  state_changed: '状态变化', observed: '状态变化', detected: '检测异常', recovery_started: '开始恢复',
  recovery_attempt: '尝试恢复', recovery_succeeded: '恢复成功', recovered: '恢复成功',
  recovery_failed: '恢复失败', recovery_skipped: '跳过恢复', retry_scheduled: '安排重试',
  desired_changed: '期望状态已保存', intent_changed: '期望状态已保存', enabled: '开启守护',
  watchdog_enabled: '开启守护', watchdog_disabled: '关闭守护', check_failed: '检查失败',
  operation_started: '开始启停操作', operation_succeeded: '启停操作成功', operation_failed: '启停操作失败',
  supervisor_enabled: '开启守护', supervisor_disabled: '关闭守护', initialized: '首次登记',
  manual: '人工操作', operator: '人工操作', watchdog: '看门狗', supervisor: '看门狗',
  schedule: '计划任务', scheduled: '计划任务', startup: '启动入口', bootstrap: '首次登记',
  speech_worker: '语音后台', deployment: '部署登记', accepted: '已接受', succeeded: '成功',
  success: '成功', failed: '失败', error: '检查错误', skipped: '已跳过', ok: '正常',
  changed: '已更新', unchanged: '无变化', pending: '等待确认', timeout: '超时', busy: '操作进行中',
  not_configured: '尚未登记', adopt_current_state: '按当前运行状态登记',
  manual_start: '人工启动', manual_stop: '人工关闭', owned_process_missing: '所管理的进程已退出',
  scheduled_runner: '定时守护检查',
  verification_termination: '验收：主动终止进程', verify_unexpected_exit_recovery: '验证意外退出后的自动恢复',
  foreground_exited: '前台运行进程已退出', foreground: '前台启动', exited: '已退出',
};

function label(value: unknown, fallback = '未记录'): string {
  return value == null || value === '' ? fallback : labels[String(value)] ?? humanLabel(value, fallback);
}

function timestamp(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '未记录';
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' });
}

function tone(state: unknown): string {
  if (['running', 'healthy', 'ready', 'success', 'succeeded', 'ok', 'recovered'].includes(String(state))) return 'green';
  if (['unhealthy', 'degraded', 'failed', 'error', 'conflict', 'timeout'].includes(String(state))) return 'red';
  return 'neutral';
}

function ServiceCard({ service }: { service: RecordValue }) {
  return <article className="watchdog-service">
    <div className="watchdog-service-heading"><h3>{serviceLabels[service.service] ?? service.service}</h3><span className={`badge ${tone(service.observed_state)}`}>{label(service.observed_state, '尚未检查')}</span></div>
    <dl>
      <div><dt>期望状态</dt><dd>{service.desired == null ? '尚未登记' : service.desired ? '应当开启' : '保持关闭'}</dd></div>
      <div><dt>设置原因</dt><dd>{label(service.reason)}</dd></div>
      <div><dt>最近设置</dt><dd>{timestamp(service.updated_at)}</dd></div>
      <div><dt>进程 PID</dt><dd>{service.pid ?? '无'}</dd></div>
      <div><dt>最近检查</dt><dd>{timestamp(service.last_seen_at)}</dd></div>
      <div><dt>最近恢复</dt><dd>{timestamp(service.last_recovery_at)}</dd></div>
      <div><dt>连续失败</dt><dd>{number(service.failures)} 次</dd></div>
      {service.next_retry_at != null && <div><dt>下次重试</dt><dd>{timestamp(service.next_retry_at)}</dd></div>}
    </dl>
    {service.last_error && <p className="watchdog-service-error"><strong>最近错误</strong>{service.last_error}</p>}
  </article>;
}

export function WatchdogPanel({ revision }: { revision: number }) {
  const [manual, setManual] = useState(0);
  const load = useApiData('/watchdog?limit=100', revision + manual);
  const data = load.data;
  const events = rows(data?.events);
  return <>
    <Panel title="服务守护" hint="期望状态决定是否自动恢复；实际状态来自最近一次检查。时间均为北京时间。" actions={<div className="analytics-toolbar">{load.loading && <span className="loading-indicator" role="status" aria-label="正在读取看门狗状态"><span className="spinner" /></span>}<button className="button quiet small" onClick={() => setManual((value) => value + 1)}>刷新守护状态</button></div>}>
      {load.error && <p className="notice error" role="alert">{load.error}</p>}
      {data && <>
        <div className="analytics-kpis watchdog-summary">
          <div className="analytics-kpi"><div className="analytics-kpi-label">自动守护</div><div className="analytics-kpi-value">{data.enabled ? '已启用' : '已关闭'}</div><div className="analytics-kpi-hint">已登记为关闭的服务保持关闭</div></div>
          <div className="analytics-kpi"><div className="analytics-kpi-label">最近完成检查</div><div className="watchdog-check-time">{timestamp(data.last_check_at)}</div><div className="analytics-kpi-hint">{label(data.last_check_outcome)}</div></div>
          <div className="analytics-kpi"><div className="analytics-kpi-label">近 24 小时恢复成功</div><div className="analytics-kpi-value">{number(data.counts?.recoveries_24h)}</div><div className="analytics-kpi-hint">按实际恢复结果累计</div></div>
          <div className="analytics-kpi"><div className="analytics-kpi-label">近 24 小时恢复失败</div><div className="analytics-kpi-value">{number(data.counts?.failures_24h)}</div><div className="analytics-kpi-hint">连续失败按间隔重试</div></div>
        </div>
        <div className="watchdog-services">{rows(data.services).map((service) => <ServiceCard key={service.service} service={service} />)}</div>
      </>}
      {!data && !load.loading && !load.error && <p className="caption">暂无守护状态。</p>}
    </Panel>
    {data && <Panel title="守护事件记录" hint="展示最近 100 条状态变化、人工启停和自动恢复记录；不受上方聊天与模型筛选影响。">
      <DataTable rows={events} empty="暂无守护事件；服务登记或发生状态变化后会记录在这里。" columns={[
        { key: 'ts', label: '时间', render: (event) => timestamp(event.ts) },
        { key: 'service', label: '服务', render: (event) => serviceLabels[event.service] ?? event.service },
        { key: 'event', label: '事件', render: (event) => label(event.event) },
        { key: 'source', label: '来源', render: (event) => label(event.source) },
        { key: 'outcome', label: '结果', render: (event) => <span className={`badge ${tone(event.outcome)}`}>{label(event.outcome)}</span> },
        { key: 'pid', label: '进程 PID', render: (event) => event.pid ?? '—' },
        { key: 'reason', label: '原因与详情', render: (event) => <div className="watchdog-event-detail">{label(event.reason, '—')}{event.detail && <details><summary>查看详情</summary><p>{event.detail}</p></details>}</div> },
      ]} />
    </Panel>}
  </>;
}
