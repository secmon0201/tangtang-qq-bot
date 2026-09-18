"""Operator-readable decisions without exposing private evidence in a group."""
import json
from datetime import datetime


LABELS = {
    'insufficient_topic_evidence': '同主题跨日证据不足，未调用模型',
    'empty_proposals': '模型未提出成长内容', 'evaluated': '已检查模型提议',
    'accepted': '已采纳', 'local_or_third_party_global': '群内或第三人内容不能跨群',
    'invalid_fields_or_private_identity': '字段无效或涉及私人资料、身份设定',
    'invalid_or_unavailable_citations': '引用不存在、原文不符、已遗忘或不相关',
    'insufficient_cross_day_evidence': '不足三次独立互动或两个自然日',
    'personal_or_local_source_global': '个人资料或群内证据不能生成共享成长',
    'disabled_or_unchanged': '条目已停用或内容未变化',
    'evidence_not_new': '修订缺少新证据', 'invalid_proposal_object': '提议格式无效',
    'disabled_during_generation': '生成期间功能已关闭',
    'ValueError': '模型返回结构无效', 'JSONDecodeError': '模型返回的 JSON 无效',
    'TimeoutError': '整理请求超时', 'HTTPStatusError': '模型服务请求失败',
    'provider_http': '服务 HTTP 错误', 'provider_timeout': '服务超时',
    'provider_network': '连接异常', 'invalid_model_output': '模型返回结构无效',
    'background_internal_error': '整理内部错误，需要检查',
}


def _decision_text(decision: dict, timezone) -> str:
    reason = LABELS.get(decision['reason'], decision['reason'])
    if 'retryable' not in decision:
        return reason
    status = decision.get('http_status')
    if isinstance(status, int):
        reason += f"（HTTP {status}，{decision.get('error_code', 'unclassified')}）"
    stamp = datetime.fromtimestamp(decision['next_attempt_at'], timezone).strftime('%m-%d %H:%M')
    if decision.get('retryable'):
        return reason + f"；最早 {stamp} 重试，仍受额度和群整理间隔限制"
    return reason + f'；等待配置修复或 {stamp} 复检，证据保留'


def diagnostic_text(store, persona: str, group_id: int) -> str:
    status = '每轮成功互动即时整理；不等待跨日或后台额度' if persona == 'denia' else store.option('background_status', '尚未整理')
    lines = ['人格成长诊断：' + status]
    for row in store.growth_diagnostics(persona, group_id):
        stamp = datetime.fromtimestamp(row['created_at'], store.timezone).strftime('%m-%d %H:%M')
        reasons = [_decision_text(d, store.timezone) for d in json.loads(row['decisions'])]
        lines.append(f"{stamp} · {LABELS.get(row['outcome'], row['outcome'])}" + ('；' + '；'.join(reasons) if reasons else ''))
    if len(lines) == 1:
        lines.append('暂无诊断记录；旧任务未记录提议拒绝原因。')
    return '\n'.join(lines)
