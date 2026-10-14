# -*- coding: utf-8 -*-
"""V3.0 OAM：运维智能体（Operational Agentic Maintenance）分层实现。

依据 `V3.0_DBAGENT_REDESIGN.md`（VLDB 2026 DBAgent 论文的工程化改写）。
分层与既有 `monitor/llm/`、`monitor/rca_engine_v2.py` **并存**，不做一次性替换
（设计 §10："一次性替换现有链路"是明确不做的事）。

分层：
    L1 env/      可回放环境 + 验证器 + 奖励 —— 地基，无 GPU 也必须先建
    L2 perception/ 多模态感知层 MMA（KPI 的 STL+GESD、计划提取器+规则分析器）
    L3 kb/       知识库三步构建（分类/切分/抽取）+ 工具目录
    L4 policy/   统一 Think-Act-Observe 策略，**后端可替换**（prompted/sft/rl）
    L5 audit/    独立证据审计（对治"编造事实能活三个版本"）

三条贯穿全层的硬约束（都来自设计文档，不是本文件的自由发挥）：
1. **方言是一等维度**（§9.2）。Oracle 的 `enq: TX` 对 MySQL 不存在，
   REV-10 正是把它发给所有方言造成的。
2. **"证据不足"是合法终止**（§4.4/§9.1）。策略必须被允许说"我不知道"，
   且不得因此被记为幻觉。
3. **评测必须与覆盖度同屏**（§4.1）。回放环境造不出快照里没有的新遥测，
   所以成功率只能支撑"已覆盖场景无回归"，不能当泛化能力的证明。
"""
import logging

logger = logging.getLogger("monitor.oam")


def oam_enabled() -> bool:
    """OAM 总开关。走 `appconf.get` 而不是裸 `getattr(settings, ...)` ——
    仓库约定"默认值只在此处定义一次"，裸 getattr 会让默认值散落。"""
    from monitor import appconf
    return bool(appconf.get('OAM_ENABLED'))


__all__ = ['oam_enabled']
