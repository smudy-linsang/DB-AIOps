# -*- coding: utf-8 -*-
"""L4 统一决策策略：Think-Act-Observe 循环 + 可替换后端。

设计 §3.3：L4 的角色是*"替代 3 代 RCA + 关键词路由"*。注意是**替代角色**，
不是立刻删掉三者 —— 设计 §10 明确"一次性替换现有链路"是**不做**的事，
`monitor/oam/` 与既有模块解耦、灰度并行、基准数据说话后再切流。
"""
from monitor.oam.policy.action_space import (
    STATUS_EVIDENCE_INSUFFICIENT, DiagnosticState, escalate_report, parse_action,
)
from monitor.oam.policy.backends import backend_name, build
from monitor.oam.policy.loop import diagnose

__all__ = ['diagnose', 'DiagnosticState', 'parse_action', 'escalate_report',
           'backend_name', 'build', 'STATUS_EVIDENCE_INSUFFICIENT']
