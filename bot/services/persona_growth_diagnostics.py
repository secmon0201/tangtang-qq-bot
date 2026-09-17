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
}


def diagnostic_text(store, persona: str, group_id: int) -> str:
    lines = ['人格成长诊断：' + store.option('background_status', '尚未整理')]
    for row in store.growth_diagnostics(persona, group_id):
        stamp = datetime.fromtimestamp(row['created_at'], store.timezone).strftime('%m-%d %H:%M')
        reasons = [LABELS.get(d['reason'], d['reason']) for d in json.loads(row['decisions'])]
        lines.append(f"{stamp} · {LABELS.get(row['outcome'], row['outcome'])}" + ('；' + '；'.join(reasons) if reasons else ''))
    if len(lines) == 1:
        lines.append('暂无诊断记录；旧任务未记录提议拒绝原因。')
    return '\n'.join(lines)
