# -*- coding: utf-8 -*-
"""
Phase 8: LLM 智能诊断包 (phase8/20 §2-§6)。

设计红线:
1. LLM 只推理不执行 —— 本包所有产物均为"建议", 永不直接触发写操作;
2. 可降级 —— LLM_ENABLED=False 或任何异常时, 调用方行为等同 Phase 7;
3. 预算受控 —— 单事故 LLM 阶段不超过 LLM_DIAG_BUDGET_SEC;
4. 全程留痕 —— 每次调用写 LLMCallLog。
"""
from django.conf import settings


def llm_enabled() -> bool:
    return bool(getattr(settings, 'LLM_ENABLED', False))


def embed_enabled() -> bool:
    return bool(getattr(settings, 'EMBED_ENABLED', False))


def agent_enabled() -> bool:
    return llm_enabled() and bool(getattr(settings, 'AGENT_ENABLED', False))
