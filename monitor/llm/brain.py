# -*- coding: utf-8 -*-
"""
Phase 8A: LLM 诊断大脑 (phase8/20 §6)。

diagnose_incident(): 证据包 → LLM 结构化研判 (预算受控, bad_json 重试1次, 网络错误不重试)。
merge_llm_with_rules(): 双引擎融合规则 R1-R5:
  R1 agree    → confidence +0.15, source='both'
  R2 disagree → confidence -0.20, 附 llm_dissent
  R3 new      → 新假设 source='llm', 置信度上限 0.85
  R4 排序后截断 DIAG_RCA_TOPN, 但 llm 假设至少保留 1 条
  R5 全部结果带 source 字段 (rules/llm/both)
"""
import logging
import time

from django.conf import settings

from monitor.llm import llm_enabled
from monitor.llm.evidence import build_evidence_pack, render_evidence
from monitor.llm.prompts import DIAGNOSIS_SYSTEM, DIAGNOSIS_USER_TMPL
from monitor.llm.providers import LLMBadResponse, LLMError, get_chat_provider
from monitor.llm.schemas import SchemaValidationError, validate_diagnosis_output

logger = logging.getLogger("monitor.llm")

LLM_NEW_HYPOTHESIS_CAP = 0.85  # R3: LLM 独有假设置信度上限


def diagnose_incident(incident, context: dict, features: dict,
                      root_causes: list, similar_cases: list) -> dict:
    """LLM 综合研判。返回校验后的诊断 dict; 关闭/失败时抛异常由调用方降级。

    预算: LLM_DIAG_BUDGET_SEC; bad_json/schema 失败重试 1 次; 网络错误不重试。
    """
    if not llm_enabled():
        raise LLMError("LLM_ENABLED=False")
    budget = int(getattr(settings, 'LLM_DIAG_BUDGET_SEC', 30))
    t0 = time.time()

    pack = build_evidence_pack(incident, context, features, root_causes, similar_cases)
    messages = [
        {'role': 'system', 'content': DIAGNOSIS_SYSTEM},
        {'role': 'user', 'content': DIAGNOSIS_USER_TMPL.format(
            n=len(pack), evidence=render_evidence(pack))},
    ]
    provider = get_chat_provider()

    last_err = None
    for attempt in (1, 2):  # 第2次仅用于 bad_json 重试
        remain = budget - (time.time() - t0)
        if remain < 3:
            raise LLMError(f"LLM 诊断预算耗尽 ({budget}s)")
        try:
            res = provider.chat(messages, scene='diagnosis',
                                incident_id=incident.incident_id,
                                timeout=int(min(remain, provider.timeout)))
        except LLMError:
            raise  # 网络类错误不重试, 直接交由管道降级
        try:
            out = validate_diagnosis_output(res.content)
            out['_meta'] = {
                'model': res.model, 'latency_ms': res.latency_ms,
                'evidence_count': len(pack), 'attempt': attempt,
            }
            return out
        except SchemaValidationError as e:
            last_err = e
            logger.warning("[llm] 诊断输出不合法(第%d次): %s", attempt, e)
            # 把错误反馈给模型重试一次
            messages.append({'role': 'assistant', 'content': res.content[:4000]})
            messages.append({'role': 'user',
                             'content': f"你的输出不符合 JSON 契约: {e}. 请严格按契约重新只输出 JSON。"})
    raise LLMBadResponse(f"LLM 诊断输出两次均不合法: {last_err}")


def merge_llm_with_rules(root_causes: list, llm_result: dict) -> list:
    """双引擎融合 R1-R5。root_causes 为规则引擎产物(list[dict]), 原地不改、返回新列表。"""
    merged = []
    by_rule = {}
    for r in (root_causes or []):
        r = dict(r)
        r.setdefault('source', 'rules')
        merged.append(r)
        if r.get('rule_id'):
            by_rule[r['rule_id']] = r

    for h in (llm_result or {}).get('hypotheses', []):
        rid = (h.get('rule_id') or '').strip()
        verdict = h.get('verdict_on_rule', 'new')
        target = by_rule.get(rid) if rid else None
        if target is not None and verdict == 'agree':
            # R1: 认同 → 加分
            target['confidence'] = round(min(target['confidence'] + 0.15, 1.0), 2)
            target['source'] = 'both'
            target['reasoning'] = h.get('reasoning', '')
            # LLM 补充建议去重合并
            extra = [s for s in h.get('suggestions', []) if s not in target.get('suggestions', [])]
            target['suggestions'] = (target.get('suggestions') or []) + extra[:3]
        elif target is not None and verdict == 'disagree':
            # R2: 不认同 → 降分并留异议
            target['confidence'] = round(max(target['confidence'] - 0.20, 0.05), 2)
            target['llm_dissent'] = h.get('reasoning', '')
        else:
            # R3: 新假设 (含 rule_id 未命中场景)
            merged.append({
                'rule_id': rid or f"LLM-{h.get('domain', 'gen')[:8]}",
                'name': h.get('name', 'LLM 假设'),
                'domain': h.get('domain', 'performance'),
                'confidence': round(min(float(h.get('confidence', 0.5)), LLM_NEW_HYPOTHESIS_CAP), 2),
                'causal_chain': [],
                'summary': (h.get('reasoning') or '')[:500],
                'suggestions': h.get('suggestions', []),
                'evidence': [],
                'source': 'llm',
                'reasoning': h.get('reasoning', ''),
                'evidence_refs': h.get('evidence_refs', []),
            })

    # R4: 排序截断, 但 llm 假设至少保留 1 条
    merged.sort(key=lambda r: -r.get('confidence', 0))
    topn = int(getattr(settings, 'DIAG_RCA_TOPN', 3))
    top = merged[:topn]
    if not any(r.get('source') == 'llm' for r in top):
        llm_rest = [r for r in merged[topn:] if r.get('source') == 'llm']
        if llm_rest:
            top = top[:max(topn - 1, 1)] + [llm_rest[0]]
    return top
