# -*- coding: utf-8 -*-
"""
Phase 6C: 分级授权 + P1 紧急通道 + 熔断 (phase6/30 §3)。

low  : PLAYBOOK_AUTO_LOW_RISK 且 auto_execute → 自动; 否则一键
mid  : 一键 + 快速审批; P1 事故可"先执行后补审"
high : 完整多级审批
熔断: 实例 1 小时内 auto/one_click 达 PLAYBOOK_AUTO_CIRCUIT_BREAK → 转人工
"""
import logging

from django.conf import settings

logger = logging.getLogger("monitor.playbook_authz")


def decide_trigger(incident, playbook) -> dict:
    """决定触发方式。返回 {mode, execute_now, need_approval, reason}。
    mode: auto|one_click|approved(需先审批)。"""
    risk = playbook.risk_level

    # 熔断检查
    if _circuit_broken(incident.config_id):
        return {'mode': 'one_click', 'execute_now': False, 'need_approval': True,
                'reason': '自动化熔断中(1小时内自动动作过多), 转人工审批'}

    if risk == 'low':
        if getattr(settings, 'PLAYBOOK_AUTO_LOW_RISK', True) and playbook.auto_execute:
            return {'mode': 'auto', 'execute_now': True, 'need_approval': False,
                    'reason': '低风险自动执行'}
        return {'mode': 'one_click', 'execute_now': True, 'need_approval': False,
                'reason': '低风险一键执行'}

    if risk == 'mid':
        # P1 紧急通道: 先执行后补审
        if (incident.priority == 'P1'
                and getattr(settings, 'INCIDENT_P1_EXECUTE_FIRST', True)):
            return {'mode': 'one_click', 'execute_now': True, 'need_approval': True,
                    'reason': 'P1 紧急通道: 先执行后补审'}
        return {'mode': 'one_click', 'execute_now': True, 'need_approval': True,
                'reason': '中风险一键执行(需快速审批, 已授权即执行)'}

    # high
    return {'mode': 'approved', 'execute_now': False, 'need_approval': True,
            'reason': '高风险需完整审批后执行'}


def record_auto_action(config_id: int):
    """记录一次 auto/one_click 执行(用于熔断计数)。"""
    try:
        from monitor.redis_bus import get_bus
        bus = get_bus()
        key = f"dbaiops:autoact:{config_id}"
        cnt = bus.incr(key)
        if cnt == 1:
            bus.expire(key, 3600)
    except Exception:
        pass


def _circuit_broken(config_id: int) -> bool:
    try:
        from monitor.redis_bus import get_bus
        bus = get_bus()
        cnt = bus.get(f"dbaiops:autoact:{config_id}")
        limit = int(getattr(settings, 'PLAYBOOK_AUTO_CIRCUIT_BREAK', 3))
        return cnt is not None and int(cnt) >= limit
    except Exception:
        return False
