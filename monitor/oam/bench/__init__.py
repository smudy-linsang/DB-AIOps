# -*- coding: utf-8 -*-
"""L1 基准台：把"成功率/幻觉率"变成可复现的数字。

设计 §2.1 的判断：*"这个项目从来没有'成功率'这个指标……论文给出的答案不是
'再写一个更聪明的 RCA 引擎'，而是：**先把能判分的环境建起来**，再谈策略怎么学。"*

本包就是那个"能判分的环境"的评测入口。
"""
from monitor.oam.bench.runner import (
    assert_split_instance_disjoint, run_bench, run_case,
)

__all__ = ['run_bench', 'run_case', 'assert_split_instance_disjoint']
