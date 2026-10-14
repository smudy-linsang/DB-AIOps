# -*- coding: utf-8 -*-
"""L5 安全与治理：独立证据审计。

论文 §5.5 的生产做法在这一层收口：只读工具访问、限速、白名单、
变更走审批、报告经独立审计后才呈现给 DBA、低置信/高风险转人工。

只读/限速/白名单/变更拦截在 `env/tool_proxy.py`；风险标签与人工审批
复用既有 `approval_engine` / `autonomy_policy` / `playbook_authz`。
"""
from monitor.oam.audit.evidence_audit import (
    AuditResult, Claim, audit, extract_claims, persist_miss,
)

__all__ = ['audit', 'AuditResult', 'Claim', 'extract_claims', 'persist_miss']
