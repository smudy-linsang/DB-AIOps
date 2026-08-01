# -*- coding: utf-8 -*-
"""
Phase 8B: 事故案例蒸馏 (phase8/20 §12)。

把 resolved/closed 事故蒸馏为 AlertCase (source='distilled'), 并投影进 ES 向量索引。
双触发: Incident.transition 钩子 (resolved) + 每小时补偿扫描 (漏网事故)。
LLM 可用时用 LLM 蒸馏; 不可用时用模板降级蒸馏 (保证 100% 沉淀率)。
"""
import json
import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("monitor.llm")


def _already_distilled(incident) -> bool:
    from monitor.models import AlertCase
    return AlertCase.objects.filter(source_incident=incident.incident_id).exists()


def _template_distill(incident) -> dict:
    """无 LLM 的降级蒸馏: 从事故结构化字段直接拼卡片。"""
    rca = incident.rca_result or {}
    roots = rca.get('root_causes') or []
    top = roots[0] if roots else {}
    ev0 = incident.events.first() if incident.events.exists() else None
    runs = list(incident.playbook_runs.filter(status='succeeded')[:3])
    resolution_parts = []
    for r in runs:
        resolution_parts.append(f"执行剧本 {r.playbook.playbook_id}({r.playbook.name}) 成功")
    if not resolution_parts:
        resolution_parts.append('; '.join((top.get('suggestions') or ['人工处置'])[:3]))
    return {
        'title': incident.title[:200],
        'root_cause': top.get('summary') or top.get('name') or '未记录',
        'resolution': '; '.join(resolution_parts) or '未记录',
        'tags': [incident.db_type, incident.category,
                 getattr(ev0, 'signal', '') or ''],
        'symptom_text': f"{incident.db_type} {incident.category} "
                        f"{getattr(ev0, 'signal', '')} {incident.title} "
                        f"{getattr(ev0, 'metric_key', '')}={getattr(ev0, 'value', '')}",
        'lessons': '',
    }


def _llm_distill(incident) -> dict:
    from monitor.llm.prompts import DISTILL_SYSTEM, DISTILL_USER_TMPL
    from monitor.llm.providers import get_chat_provider
    from monitor.llm.schemas import validate_distill_output

    rca = incident.rca_result or {}
    inc_json = json.dumps({
        'incident_id': incident.incident_id, 'title': incident.title,
        'db_type': incident.db_type, 'category': incident.category,
        'priority': incident.priority,
        'root_causes': (rca.get('root_causes') or [])[:3],
        'impact': incident.impact or {},
    }, ensure_ascii=False, default=str)[:6000]
    runs_json = json.dumps([{
        'playbook': r.playbook.playbook_id, 'status': r.status,
        'steps': (r.step_results or [])[:5],
    } for r in incident.playbook_runs.all()[:3]], ensure_ascii=False, default=str)[:3000]
    fb_json = json.dumps([{
        'rule_id': f.rule_id, 'verdict': f.verdict, 'actual_cause': f.actual_cause,
    } for f in incident.rca_feedbacks.all()[:10]], ensure_ascii=False, default=str)[:2000]

    res = get_chat_provider().chat([
        {'role': 'system', 'content': DISTILL_SYSTEM},
        {'role': 'user', 'content': DISTILL_USER_TMPL.format(
            incident_json=inc_json, runs_json=runs_json, feedback_json=fb_json)},
    ], scene='distill', incident_id=incident.incident_id)
    return validate_distill_output(res.content)


def distill_incident(incident, force: bool = False):
    """单事故蒸馏入口。返回 case_id 或 None (跳过/失败)。"""
    if not getattr(settings, 'CASE_DISTILL_ENABLED', True):
        return None
    if incident.status not in ('resolved', 'closed'):
        return None
    if not force and _already_distilled(incident):
        return None

    card = None
    if getattr(settings, 'LLM_ENABLED', False):
        try:
            card = _llm_distill(incident)
        except Exception as e:
            logger.warning("[distill] %s LLM 蒸馏失败, 用模板降级: %s",
                           incident.incident_id, e)
    if card is None:
        card = _template_distill(incident)

    from monitor.models import AlertCase
    ev0 = incident.events.first() if incident.events.exists() else None
    case_id = f"CASE-INC-{incident.incident_id}"
    obj, created = AlertCase.objects.update_or_create(
        case_id=case_id,
        defaults={
            'title': card['title'][:200],
            'db_type': incident.db_type,
            'severity': 'critical' if incident.priority in ('P1', 'P2') else 'warning',
            'symptom_signature': {
                'signal': getattr(ev0, 'signal', ''),
                'metric': getattr(ev0, 'metric_key', ''),
                'value': getattr(ev0, 'value', None),
                'symptom_text': card.get('symptom_text', ''),
            },
            'root_cause': card['root_cause'],
            'resolution': card['resolution'],
            'tags': [t for t in card.get('tags', []) if t][:10],
            'confidence': 0.6,
            'source': 'distilled',
            'source_incident': incident.incident_id,
            'created_by': 'case_distiller',
            'embedding_indexed': False,
        },
    )
    # 投影进 ES (失败不阻断, 补偿任务会重试)
    try:
        from monitor.case_rag_v2 import index_case_to_es
        index_case_to_es(obj)
    except Exception as e:
        logger.debug("[distill] %s ES 投影失败: %s", case_id, e)
    logger.info("[distill] 事故 %s → 案例 %s (%s)", incident.incident_id,
                case_id, 'created' if created else 'updated')
    return case_id


def scan_and_distill(hours: int = 24) -> dict:
    """补偿扫描: 近 N 小时 resolved/closed 且未蒸馏的事故。"""
    from monitor.models import Incident
    cutoff = timezone.now() - timezone.timedelta(hours=hours)
    qs = Incident.objects.filter(status__in=('resolved', 'closed'),
                                 updated_at__gte=cutoff)
    total, done = 0, 0
    for inc in qs:
        total += 1
        try:
            if distill_incident(inc):
                done += 1
        except Exception as e:
            logger.warning("[distill] 扫描蒸馏 %s 失败: %s", inc.incident_id, e)
    return {'scanned': total, 'distilled': done}
