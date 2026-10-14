# -*- coding: utf-8 -*-
"""L1 可回放环境：冻结快照 + 工具代理 + 回放驱动。

这是 V3.0 的地基。设计 §4.1：*"它是 RL 的训练场，但不依赖 RL —— 它本身就是
这个项目三轮复审都缺的那个东西：一个能判断'诊断结论对不对'的离线评测台。"*

对外只有两个入口：
    `ReplayEnv(snapshot).call(tool, params)` —— 环境交互（走 ToolProxy 的七步防御）
    `ReplayEnv(snapshot).coverage_report()`  —— 覆盖度，与成绩同屏输出
"""
from monitor.oam.env.replay import PLAN_OPERATOR_BUCKETS, ReplayEnv
from monitor.oam.env.snapshot import OamSnapshot, SnapshotRangeError
from monitor.oam.env.tool_proxy import (
    ERR_DIALECT_MISMATCH, ERR_RATE_LIMITED, ToolProxy,
)

__all__ = [
    'ReplayEnv', 'OamSnapshot', 'SnapshotRangeError', 'ToolProxy',
    'PLAN_OPERATOR_BUCKETS', 'ERR_DIALECT_MISMATCH', 'ERR_RATE_LIMITED',
]
