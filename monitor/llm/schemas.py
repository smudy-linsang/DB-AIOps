# -*- coding: utf-8 -*-
"""
Phase 8A: LLM 结构化输出契约与校验 (phase8/20 §3, phase8/30 §5)。

validate_*() 四步: 剥代码围栏 → json.loads → jsonschema → 语义修正。
校验失败抛 SchemaValidationError, 由调用方决定重试或降级。
"""
import json
import logging
import re

from django.conf import settings

logger = logging.getLogger("monitor.llm")


class SchemaValidationError(Exception):
    """LLM 输出不满足契约"""


# ==========================================================================
# 契约 1: 诊断输出 (brain.diagnose_incident)
# ==========================================================================
DIAGNOSIS_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["hypotheses", "summary"],
    "properties": {
        "summary": {"type": "string", "maxLength": 2000},
        "hypotheses": {
            "type": "array", "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["name", "confidence", "reasoning"],
                "properties": {
                    "rule_id": {"type": "string", "maxLength": 24},
                    "name": {"type": "string", "maxLength": 200},
                    "domain": {"type": "string", "maxLength": 40},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reasoning": {"type": "string", "maxLength": 3000},
                    "verdict_on_rule": {"type": "string", "enum": ["agree", "disagree", "new"]},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                    "suggestions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                },
            },
        },
        "plan_draft": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 200},
                "steps": {
                    "type": "array", "maxItems": 10,
                    "items": {
                        "type": "object",
                        "required": ["desc"],
                        "properties": {
                            "desc": {"type": "string", "maxLength": 500},
                            "sql_hint": {"type": "string", "maxLength": 2000},
                            "risk": {"type": "string", "enum": ["low", "mid", "high"]},
                        },
                    },
                },
                "caution": {"type": "string", "maxLength": 1000},
            },
        },
        "need_more_evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
}

# ==========================================================================
# 契约 2: 案例蒸馏输出 (case_distiller)
# ==========================================================================
DISTILL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["title", "root_cause", "resolution"],
    "properties": {
        "title": {"type": "string", "maxLength": 200},
        "root_cause": {"type": "string", "maxLength": 3000},
        "resolution": {"type": "string", "maxLength": 3000},
        "tags": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 10},
        "symptom_text": {"type": "string", "maxLength": 2000},
        "lessons": {"type": "string", "maxLength": 2000},
    },
}

# ==========================================================================
# 契约 3: Agent 单步输出 (agent.investigate)
# ==========================================================================
AGENT_STEP_SCHEMA = {
    "type": "object",
    "required": ["thought", "action"],
    "properties": {
        "thought": {"type": "string", "maxLength": 2000},
        "action": {"type": "string", "maxLength": 40},
        "params": {"type": "object"},
        "final": {
            "type": "object",
            "properties": {
                "root_cause": {"type": "string", "maxLength": 2000},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence_summary": {"type": "string", "maxLength": 3000},
                "suggestions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            },
        },
    },
    # action=final 时必须携带 final 对象
    "allOf": [{
        "if": {"properties": {"action": {"const": "final"}}},
        "then": {"required": ["final"]},
    }],
}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """剥掉 markdown 代码围栏与首尾杂讯, 提取最外层 JSON 对象。"""
    s = _FENCE_RE.sub('', (text or '').strip()).strip()
    if not s.startswith('{'):
        # 容错: 在文本中定位第一个 { 到最后一个 }
        i, j = s.find('{'), s.rfind('}')
        if i >= 0 and j > i:
            s = s[i:j + 1]
    return s


def _validate(text: str, schema: dict, what: str) -> dict:
    s = _strip_fences(text)
    try:
        obj = json.loads(s)
    except ValueError as e:
        raise SchemaValidationError(f"{what}: JSON 解析失败: {e}") from e
    try:
        import jsonschema
    except ImportError as e:
        # BUG-137: 原先这里只 warning 然后**放行** —— 一旦 jsonschema 缺失
        # (依赖装漏、精简镜像)，所有 LLM 输出的结构校验被静默跳过，
        # 而这些输出会驱动 RCA 结论与自动修复预案。安全姿态必须 fail-closed。
        # jsonschema 是 requirements.txt 中的硬依赖，缺失即环境异常。
        if getattr(settings, 'LLM_SCHEMA_VALIDATION_REQUIRED', True):
            raise SchemaValidationError(
                f"{what}: jsonschema 未安装，无法校验 LLM 输出结构。"
                f"这是硬依赖，请检查部署环境 (pip install jsonschema)") from e
        logger.warning("[llm] jsonschema 未安装, 已按配置跳过结构校验")
        # W6-L2: 即便按配置放行，跳过校验这一降级也必须留痕可见
        from monitor import degrade
        degrade.note('llm.schema_validation_skipped', reason='jsonschema missing')
        return obj
    try:
        jsonschema.validate(obj, schema)
    except Exception as e:
        raise SchemaValidationError(f"{what}: 结构校验失败: {getattr(e, 'message', e)}") from e
    return obj


def validate_diagnosis_output(text: str) -> dict:
    """诊断输出校验 + 语义修正 (置信度截断/verdict 默认值)。"""
    obj = _validate(text, DIAGNOSIS_OUTPUT_SCHEMA, "diagnosis")
    for h in obj.get('hypotheses', []):
        h['confidence'] = max(0.0, min(1.0, float(h.get('confidence', 0.5))))
        h.setdefault('verdict_on_rule', 'new')
        h.setdefault('rule_id', '')
        h.setdefault('domain', 'performance')
        h.setdefault('suggestions', [])
        h.setdefault('evidence_refs', [])
    obj.setdefault('need_more_evidence', [])
    return obj


def validate_distill_output(text: str) -> dict:
    obj = _validate(text, DISTILL_OUTPUT_SCHEMA, "distill")
    obj.setdefault('tags', [])
    obj.setdefault('symptom_text', '')
    obj.setdefault('lessons', '')
    return obj


def validate_agent_step(text: str, allowed_actions=None) -> dict:
    """Agent 单步校验; action 必须在工具白名单或 'final'。"""
    obj = _validate(text, AGENT_STEP_SCHEMA, "agent_step")
    action = obj.get('action', '')
    if allowed_actions is not None and action != 'final' and action not in allowed_actions:
        raise SchemaValidationError(f"agent_step: 非法 action '{action}'")
    obj.setdefault('params', {})
    return obj
