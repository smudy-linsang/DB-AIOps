# -*- coding: utf-8 -*-
"""
Phase 8A: 证据包构建 (phase8/20 §4)。

把 context/features/root_causes/similar_cases 组装为给 LLM 的紧凑证据包:
- 按优先级组装 (metric=1 最高 → baseline=5 最低);
- token 预算裁剪 (估算 tokens ≈ chars // 2, 低优先级先驱逐);
- 脱敏 (主机/IP/密码类键值)。
"""
import json
import logging
import re

from django.conf import settings

logger = logging.getLogger("monitor.llm")

# 优先级: 越小越重要, 裁剪时从大到小驱逐
_PRIORITY = {'metric': 1, 'lock': 1, 'event': 1, 'ash': 2, 'sql': 2,
             'change': 3, 'case': 4, 'rule_hits': 2, 'baseline': 5, 'business': 5}

_SENSITIVE_KEY_RE = re.compile(r'password|passwd|secret|token|api_key', re.I)
_IP_RE = re.compile(r'\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b')


def _sanitize(obj):
    """递归脱敏: 敏感键置 ***; IP 尾段打码。"""
    if isinstance(obj, dict):
        return {k: ('***' if _SENSITIVE_KEY_RE.search(str(k)) else _sanitize(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    if isinstance(obj, str):
        return _IP_RE.sub(lambda m: f"{m.group(1)}.{m.group(2)}.*.{m.group(4)}", obj)
    return obj


def _tok(s: str) -> int:
    """粗略 token 估算: 中英文混排取 chars//2。"""
    return len(s) // 2


def build_evidence_pack(incident, context: dict, features: dict,
                        root_causes: list, similar_cases: list) -> list:
    """返回 [{id, type, priority, text}], 已按预算裁剪并脱敏。

    id 形如 E1/E2..., 供 LLM 在 evidence_refs 中引用。
    """
    items = []

    def add(etype, payload, label=''):
        try:
            text = json.dumps(_sanitize(payload), ensure_ascii=False, default=str)
        except Exception:
            text = str(payload)[:1000]
        items.append({'type': etype, 'priority': _PRIORITY.get(etype, 4),
                      'label': label, 'text': text})

    # 1. 事故概要 + 触发事件 (priority=1)
    ev0 = incident.events.first() if incident.events.exists() else None
    add('event', {
        'incident_id': incident.incident_id, 'title': incident.title,
        'db_type': incident.db_type, 'category': incident.category,
        'priority': incident.priority,
        'signal': getattr(ev0, 'signal', ''), 'metric': getattr(ev0, 'metric_key', ''),
        'value': getattr(ev0, 'value', None),
        'detail': (getattr(ev0, 'detail', {}) or {}),
        'occurred_at': incident.occurred_at.isoformat() if incident.occurred_at else None,
    }, '事故与触发事件')

    # 2. 关键指标快照 (priority=1, 只保留数值型关键指标)
    keep = {}
    for k, v in (features or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            keep[k] = v
    if keep:
        add('metric', keep, '当前指标快照')

    # 3. 阻塞链 (priority=1)
    ash = (context or {}).get('ash_timeline', {}) or {}
    chains = ash.get('blocking_chains') or []
    if chains:
        add('lock', {'blocking_chains': chains[:5]}, '阻塞链')

    # 4. ASH 画像 (priority=2)
    ash_brief = {}
    if ash.get('top_wait_events'):
        ash_brief['top_wait_events'] = ash['top_wait_events'][:5]
    if ash.get('top_sql_by_samples'):
        ash_brief['top_sql'] = ash['top_sql_by_samples'][:5]
    if ash_brief:
        add('ash', ash_brief, 'ASH 活动画像')

    # 5. 规则引擎初判 (priority=2)
    if root_causes:
        add('rule_hits', [{
            'rule_id': r.get('rule_id'), 'name': r.get('name'),
            'domain': r.get('domain'), 'confidence': r.get('confidence'),
            'summary': r.get('summary'),
        } for r in root_causes[:5]], '规则引擎初判')

    # 6. 近期变更 (priority=3)
    changes = (context or {}).get('recent_changes') or []
    if changes:
        add('change', changes[:10], '近72h变更')

    # 7. 相似案例 (priority=4)
    if similar_cases:
        add('case', [{
            'case_id': c.get('case_id'), 'similarity': c.get('similarity'),
            'root_cause': (c.get('root_cause') or '')[:300],
            'fix': (c.get('fix') or '')[:300], 'success': c.get('success'),
        } for c in similar_cases[:3]], '相似历史案例')

    # 8. 业务上下文 (priority=5)
    biz = (context or {}).get('business_context') or {}
    if biz.get('systems'):
        add('business', biz, '业务影响面')

    return _clip_to_budget(items)


def _clip_to_budget(items: list) -> list:
    """按预算裁剪: 优先级升序保留, 超预算先驱逐低优先级; 单条超长截断。"""
    budget = int(getattr(settings, 'LLM_EVIDENCE_MAX_TOKENS', 6000))
    items = sorted(items, key=lambda x: x['priority'])
    out, used = [], 0
    for it in items:
        t = _tok(it['text'])
        if used + t > budget:
            remain = budget - used
            if remain > 200 and it['priority'] <= 2:
                # 高优先级证据: 截断保留
                it = dict(it, text=it['text'][:remain * 2] + '…(截断)')
                t = _tok(it['text'])
            else:
                continue  # 低优先级直接驱逐
        out.append(it)
        used += t
    for i, it in enumerate(out, 1):
        it['id'] = f"E{i}"
    return out


def render_evidence(items: list) -> str:
    """渲染为提示词文本块。"""
    lines = []
    for it in items:
        lines.append(f"[{it['id']}] ({it['type']}) {it['label']}: {it['text']}")
    return '\n'.join(lines)
