# -*- coding: utf-8 -*-
"""
Phase 8E: 自治分级放权 (phase8/20 §18)。

每实例 autonomy_level (null=跟随全局 AUTONOMY_DEFAULT_LEVEL):
  L0 观察  : 只诊断, 一切执行转人工审批 (execute_now 一律 False)
  L1 建议  : 默认档, 保持 Phase 6 行为 (仅低风险且 auto_execute 才自动)
  L2 半自主: 低风险一律自动执行
  L3 高自主: 低风险自动; 中风险且带回滚步骤时自动(补审留痕); 高风险永远完整审批
红线 (phase8/30 §7): llm_advisory 方案 forbidden, 任何等级都不得进入执行通道。
gate() 由 playbook_authz.decide_trigger 前置调用, 返回 None 表示交回原逻辑。
"""
import logging

from django.conf import settings

logger = logging.getLogger("monitor.playbook_authz")

LEVEL_NAMES = {0: '观察', 1: '建议', 2: '半自主', 3: '高自主'}


def effective_level(config) -> int:
    """实例生效的自治等级 (实例覆盖 > 全局默认)。"""
    lv = getattr(config, 'autonomy_level', None)
    if lv is None:
        lv = int(getattr(settings, 'AUTONOMY_DEFAULT_LEVEL', 1))
    return max(0, min(int(lv), 3))


def is_forbidden_plan(plan: dict) -> bool:
    """红线: LLM 建议型方案永不执行。"""
    return (plan or {}).get('scenario') == 'llm_advisory' \
        or (plan or {}).get('executable') is False


def gate(incident, playbook) -> dict:
    """自治闸门 (phase8/20 §18 决策矩阵)。返回 decide_trigger 风格的裁决 dict,
    或 None (交回 Phase 6 默认逻辑)。"""
    level = effective_level(incident.config)

    # L0: 观察模式, 一切转人工
    if level == 0:
        return {'mode': 'approved', 'execute_now': False, 'need_approval': True,
                'reason': f'自治等级L0({LEVEL_NAMES[0]}): 仅诊断, 执行需人工审批'}

    risk = playbook.risk_level
    # L2+: 低风险一律自动 (不再要求 auto_execute 标志)
    if risk == 'low' and level >= 2:
        return {'mode': 'auto', 'execute_now': True, 'need_approval': False,
                'reason': f'自治等级L{level}({LEVEL_NAMES[level]}): 低风险自动执行'}

    # L3: 中风险且带回滚步骤 → 自动执行(补审留痕)
    if risk == 'mid' and level >= 3 and (playbook.rollback or []):
        return {'mode': 'auto', 'execute_now': True, 'need_approval': True,
                'reason': f'自治等级L3({LEVEL_NAMES[3]}): 中风险带回滚, 自动执行(补审留痕)'}

    # 其余 (L1 全部 / 无回滚的中风险 / 高风险) 交回 Phase 6 原逻辑
    return None
