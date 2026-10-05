import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart, HeatmapChart, RadarChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, ToolboxComponent, VisualMapComponent, RadarComponent, AriaComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { date, humanLabel, number, percent } from './api';
import type { RecordValue } from './api';

echarts.use([BarChart, LineChart, PieChart, ScatterChart, HeatmapChart, RadarChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, ToolboxComponent, VisualMapComponent, RadarComponent, AriaComponent, CanvasRenderer]);

export const colors = ['#247c78', '#4475cd', '#d09338', '#9272bb', '#cf6772', '#579cb7', '#768aa2'];
const metricColors: Record<string, string> = { input_tokens: '#4475cd', estimated_input_tokens: '#4475cd', input_budget_tokens: '#768aa2', cache_read_tokens: '#247c78', cache_ratio: '#247c78', cache_miss_tokens: '#579cb7', cache_write_tokens: '#9272bb', output_tokens: '#d09338', reasoning_tokens: '#9272bb', request_hit_ratio: '#4d9f8e', coverage_ratio: '#768aa2', cost_coverage_ratio: '#768aa2', known_cost: '#9272bb' };
export const formatValue = (value: unknown, kind?: string): string => value == null ? '未报告' : kind === 'percent' ? percent(value) : kind === 'time' ? date(value) : typeof value === 'number' ? number(value, kind === 'cost' ? 6 : 2) : typeof value === 'object' ? JSON.stringify(value) : humanLabel(value);

export function Panel({ title, hint, children, actions, className = '' }: { title: string; hint?: string; children: ReactNode; actions?: ReactNode; className?: string }) {
  return <section className={`panel analytics-panel ${className}`}><div className="panel-heading"><div><h2>{title}</h2>{hint && <p>{hint}</p>}</div>{actions}</div>{children}</section>;
}
export function Empty({ text = '所选范围没有可展示数据。' }: { text?: string }) { return <div className="analytics-empty">{text}</div>; }

function exportRows(rows: RecordValue[]) {
  const identities = new Map<string, string>();
  function clean(value: any, key = ''): any {
    if (value == null) return value;
    if (/^(payload|text|content|quote|segments|messages|response|api_key|token|authorization|image|bytes|source|evidence|body|path)$/i.test(key) || /secret|password|base64/i.test(key)) return undefined;
    if (/^(user_id|group_id|session_key|nickname|user_nickname|speaker_nickname|scope_label|sender|speaker|event_key|title|group_name|alias|account|id|key|actor_id|target_id|creator_id)$/i.test(key)) {
      const raw = String(value); if (!identities.has(raw)) identities.set(raw, `对象${identities.size + 1}`); return identities.get(raw);
    }
    if (Array.isArray(value)) return value.map((item) => clean(item));
    if (typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, clean(v, k)]).filter(([, v]) => v !== undefined));
    return value;
  }
  return rows.map((row) => clean(row));
}
export function downloadRows(rows: RecordValue[], name: string, kind: 'csv' | 'json') {
  const safe = exportRows(rows);
  const keys = [...new Set(safe.flatMap((row) => Object.keys(row)))];
  const cell = (value: unknown) => `"${String(value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : value).replaceAll('"', '""')}"`;
  const body = kind === 'json' ? JSON.stringify(safe, null, 2) : '\uFEFF' + [keys.map(cell).join(','), ...safe.map((row) => keys.map((key) => cell(row[key])).join(','))].join('\r\n');
  const url = URL.createObjectURL(new Blob([body], { type: kind === 'csv' ? 'text/csv;charset=utf-8' : 'application/json;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${name}.${kind}`; anchor.click(); URL.revokeObjectURL(url);
}
export function ExportButtons({ rows, name }: { rows: RecordValue[]; name: string }) {
  return <div className="analytics-export"><button className="button quiet small" disabled={!rows.length} onClick={() => downloadRows(rows, name, 'csv')}>CSV</button><button className="button quiet small" disabled={!rows.length} onClick={() => downloadRows(rows, name, 'json')}>JSON</button></div>;
}
export type Column = { key: string; label: string; kind?: string; render?: (row: RecordValue) => ReactNode };
export function DataTable({ rows, columns, empty, onRow, compact = false }: { rows: RecordValue[]; columns: Column[]; empty?: string; onRow?: (row: RecordValue) => void; compact?: boolean }) {
  if (!rows.length) return <Empty text={empty} />;
  return <div className={`table-scroll analytics-table ${compact ? 'compact' : ''}`}><table><thead><tr>{columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={String(row.id ?? i)} className={onRow ? 'clickable' : ''}>{columns.map((c, j) => <td key={c.key} className={['number', 'percent', 'cost'].includes(c.kind ?? '') || typeof row[c.key] === 'number' ? 'numeric' : undefined}>{onRow && j === 0 ? <button className="analytics-link" onClick={() => onRow(row)}>{c.render ? c.render(row) : formatValue(row[c.key], c.kind)}</button> : c.render ? c.render(row) : formatValue(row[c.key], c.kind)}</td>)}</tr>)}</tbody></table></div>;
}

export function Chart({ option, label, height = 300, onClick }: { option: EChartsOption; label: string; height?: number; onClick?: (event: any) => void }) {
  const node = useRef<HTMLDivElement>(null);
  const instance = useRef<echarts.EChartsType | null>(null);
  const [themeVersion, setThemeVersion] = useState(0);
  useEffect(() => { const changed = () => setThemeVersion((v) => v + 1); window.addEventListener('harness-theme', changed); return () => window.removeEventListener('harness-theme', changed); }, []);
  useEffect(() => {
    if (!node.current) return;
    const chart = echarts.init(node.current, undefined, { renderer: 'canvas' }); instance.current = chart;
    const observer = new ResizeObserver(() => chart.resize()); observer.observe(node.current);
    return () => { observer.disconnect(); chart.dispose(); instance.current = null; };
  }, []);
  useEffect(() => {
    const chart = instance.current; if (!chart) return;
    const previousZoom = chart.getOption()?.dataZoom as any[] | undefined;
    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue('--text').trim() || '#17243a';
    const muted = css.getPropertyValue('--muted').trim() || '#647086';
    const border = css.getPropertyValue('--border').trim() || '#dce2ec';
    const surface = css.getPropertyValue('--surface').trim() || '#ffffff';
    const dark = document.documentElement.dataset.theme === 'dark';
    const next: EChartsOption = { color: colors, animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches, animationDuration: 250, animationDurationUpdate: 150, aria: { enabled: true, description: `${label}。可切换数据表查看已报告数值和未知字段。` }, ...option,
      textStyle: { ...option.textStyle, fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif', color: muted },
      tooltip: { ...(option.tooltip as any), backgroundColor: surface, borderColor: border, textStyle: { color: text } },
    };
    function axes(value: any) { const apply = (axis: any) => ({ ...axis, nameTextStyle: { color: muted }, axisLabel: { ...axis.axisLabel, color: muted }, axisLine: { ...axis.axisLine, lineStyle: { color: border } }, splitLine: { ...axis.splitLine, lineStyle: { color: border } }, splitArea: { ...axis.splitArea, areaStyle: { color: dark ? ['#1c293d', '#223147'] : ['#f8fafc', '#eef2f7'] } } }); return Array.isArray(value) ? value.map(apply) : apply(value); }
    if (option.xAxis) next.xAxis = axes(option.xAxis);
    if (option.yAxis) next.yAxis = axes(option.yAxis);
    if (option.legend) next.legend = { ...(option.legend as any), textStyle: { color: muted }, inactiveColor: dark ? '#728197' : '#a6b1bf' };
    if (option.radar) next.radar = { ...(option.radar as any), axisName: { color: muted }, splitLine: { lineStyle: { color: border } }, axisLine: { lineStyle: { color: border } }, splitArea: { areaStyle: { color: dark ? ['#1c293d', '#223147'] : ['#f8fafc', '#eef2f7'] } } };
    if (option.visualMap) next.visualMap = { ...(option.visualMap as any), textStyle: { color: muted }, inRange: { color: dark ? ['#23384a', '#70b7b0', '#247c78'] : ['#e9f1f5', '#70b7b0', '#247c78'] } };
    if (Array.isArray(option.series)) next.series = option.series.map((series: any) => ({ ...series, label: { ...series.label, color: muted } }));
    if (Array.isArray(next.dataZoom)) next.dataZoom = next.dataZoom.map((zoom, i) => ({ ...zoom, textStyle: { color: muted }, borderColor: border, backgroundColor: surface, start: previousZoom?.[i]?.start ?? 0, end: previousZoom?.[i]?.end ?? 100 }));
    chart.setOption(next, { notMerge: true });
  }, [option, label, themeVersion]);
  useEffect(() => { const chart = instance.current; if (!chart || !onClick) return; chart.on('click', onClick); return () => { chart.off('click', onClick); }; }, [onClick]);
  return <div className="analytics-chart" ref={node} style={{ height }} role="img" aria-label={label} />;
}
export function ChartPanel({ title, hint, option, rows, columns, onClick, height }: { title: string; hint?: string; option: EChartsOption; rows: RecordValue[]; columns: Column[]; onClick?: (event: any) => void; height?: number }) {
  const [table, setTable] = useState(false);
  return <Panel title={title} hint={hint} actions={<div className="analytics-chart-actions"><button className="button quiet small" aria-pressed={table} onClick={() => setTable(!table)}>{table ? '图表' : '数据表'}</button><ExportButtons rows={rows} name={title} /></div>}>{rows.length ? table ? <DataTable rows={rows} columns={columns} /> : <Chart option={option} label={title} onClick={onClick} height={height} /> : <Empty />}</Panel>;
}
export type SeriesField = { key: string; name: string; stack?: string; area?: boolean; axis?: number; percent?: boolean };
export function timeOption(rows: RecordValue[], fields: SeriesField[], timeKey = 'timestamp'): EChartsOption {
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v) => { const value = Array.isArray(v) ? v[v.length - 1] : v; return value == null || value === '-' ? '未报告' : number(Number(value), 2); } },
    legend: { top: 0, type: 'scroll', itemGap: 18 }, grid: { left: 20, right: 20, top: 52, bottom: 58, containLabel: true }, xAxis: { type: 'time', axisLabel: { hideOverlap: true }, splitLine: { show: false } },
    yAxis: [{ type: 'value', min: 0, splitLine: { lineStyle: { type: 'dashed' } } }, { type: 'value', min: 0, max: fields.some((f) => f.percent) ? 100 : undefined, axisLabel: { formatter: fields.some((f) => f.percent) ? '{value}%' : '{value}' }, splitLine: { show: false } }],
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 2 }],
    series: fields.map((f, i) => ({
      name: f.name, type: 'line', yAxisIndex: f.axis ?? 0, stack: f.stack, areaStyle: f.area ? { opacity: .17 } : undefined,
      symbol: 'circle', symbolSize: 6, showSymbol: rows.length <= 60, connectNulls: false, emphasis: { focus: 'series' }, lineStyle: { width: 3, type: f.key === 'estimated_input_tokens' || f.key === 'input_budget_tokens' ? 'dashed' : 'solid' }, itemStyle: { color: metricColors[f.key] ?? colors[i % colors.length] },
      data: rows.filter((row) => row[timeKey] != null).map((row) => [new Date(typeof row[timeKey] === 'number' ? row[timeKey] * 1000 : row[timeKey]).getTime(), typeof row[f.key] === 'number' ? row[f.key] * (f.percent ? 100 : 1) : null]),
    })),
  };
}
export function barOption(rows: RecordValue[], value = 'count', name = 'name', horizontal = false): EChartsOption {
  const categories = rows.map((r) => humanLabel(r[name], '未知'));
  return { tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, confine: true }, grid: { left: 16, right: horizontal ? 48 : 20, top: 25, bottom: 35, containLabel: true }, xAxis: horizontal ? { type: 'value', min: 0, splitLine: { lineStyle: { type: 'dashed' } } } : { type: 'category', data: categories, axisLabel: { interval: 0, hideOverlap: true, rotate: categories.length > 5 ? 25 : 0, width: 120, overflow: 'truncate' } }, yAxis: horizontal ? { type: 'category', data: categories, inverse: true, axisLabel: { width: 180, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } } : { type: 'value', min: 0, splitLine: { lineStyle: { type: 'dashed' } } }, series: [{ type: 'bar', name: '数量', barMaxWidth: 32, label: { show: true, position: horizontal ? 'right' : 'top', fontSize: 12, formatter: (p: any) => number(p.value) }, emphasis: { focus: 'self' }, data: rows.map((r, i) => ({ value: r[value] ?? null, itemStyle: { color: colors[i % colors.length], borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] } })) }] };
}
export function donutOption(rows: RecordValue[], value = 'count', name = 'name'): EChartsOption {
  return { tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)', confine: true }, legend: { bottom: 0, type: 'scroll' }, series: [{ type: 'pie', radius: ['48%', '72%'], center: ['50%', '45%'], label: { formatter: '{b}\n{d}%', fontSize: 12 }, avoidLabelOverlap: true, data: rows.filter((r) => typeof r[value] === 'number').map((r) => ({ name: humanLabel(r[name], '未知'), value: r[value] })) }] };
}
