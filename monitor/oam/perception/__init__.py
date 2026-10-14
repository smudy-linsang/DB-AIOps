# -*- coding: utf-8 -*-
"""L2 多模态感知层（MMA）—— 论文 §4.2 / §1.3③ 的三块感知能力。

对应论文 MMA 表格的三行：

| 模态 | 方法 | 本模块 |
|------|------|--------|
| 时序 KPI | STL(LOESS) → 残差上 Generalized ESD（median + MAD） | `kpi_stl_gesd.detect` |
| 执行计划 | 提取器归一化为算子图 → 规则分析器在局部邻域应用启发式 | `plan_graph.extract` + `plan_rules.analyze` |
| KB 检索结果 | 把多个分散事实/冗长段落综合成连贯凝练摘要 | `kb_digest.digest` |

**元数据与参数这类简单结果跳过该层**（设计 §1.3③ 末句）—— 少一层加工就少一层
"看起来很有信息、其实没有"的噪声。

## 贯穿三个模态的两条口径

1. **观测必带 evidence。** `Observation.evidence` 是 L5 证据审计的地基
   （`monitor.oam.types` 的不变量 1），空 evidence 等于编造；
2. **宁可空着也不降阈值。** 无异常返回空列表、无检索结果就标 `empty=True`，
   解析不了就抛 `PlanParseError` —— 绝不允许为了"有话说"而放宽任何判定。
   论文把时序检测这一层**调向高精度**，它的定位是"可疑时段定位器"而不是
   "异常检测器"（设计 §1.4 第 1 条）。

## 执行计划这一块的分工（设计 §9.4）

**LLM 永远拿不到原始计划文本**，只能拿结构化后的算子图与 `PlanAlert`：
去掉 MMA（原始文本直给 LLM）在 60–90 算子的大计划上成功率直接 0.0。
反过来"仅规则"也不是处处占优（30–60 算子区间 85.7 < 仅 LLM 的 100.0），
所以**规则是结构骨干，不是全部** —— 自然语言化与规则覆盖不到的局部判断留给策略侧。

方言口径见 §9.2：**本版只支持 MySQL 与 PostgreSQL 的计划分析**，
其余（Oracle / 达梦 / GBase / TDSQL）抛 `PlanParseError`，
错误消息固定含"该方言暂不支持计划分析"，**不降级猜测**。
"""
from __future__ import annotations

from monitor.oam.types import Observation, PlanAlert, PlanNode

from .kb_digest import digest, render_digest_with_llm
from .kpi_stl_gesd import (detect, generalized_esd, gesd_critical_value,
                           infer_period, norm_cdf, norm_ppf, stl_decompose)
from .plan_graph import (SUPPORTED_DIALECTS, UNSUPPORTED_MESSAGE, PlanParseError,
                         extract, is_supported, normalize_dialect)
from .plan_rules import RULES, Rule, RuleContext, analyze


# ---------------------------------------------------------------------------
# 集成桥：工具原始结果 → 观测
# ---------------------------------------------------------------------------
def summarize_tool_result(tool_name: str, data, state=None) -> Observation:
    """把 L1 环境返回的工具原始结果凝练成一条 `Observation`。

    L4 循环（`policy/loop.py`）只认这一个入口，避免各人各写一套凝练逻辑。
    按论文的三种模态分派：

      · `kb.*`      → KB 多片段凝练（`kb_digest.digest`）
      · 带时序的点  → STL + Generalized ESD（`kpi_stl_gesd.detect`）
      · 执行计划    → 提取器 + 规则分析器（`plan_graph` + `plan_rules`）

    **每条分支都必须产出可核对的 `evidence`** —— L5 证据审计靠它判断
    "这条结论有没有工具观测支撑"。凝练不出来时**不编造**：refs 留空、
    原始数据留在 text 里，由上层决定如何处置。
    """
    db_type = getattr(state, 'db_type', '') if state is not None else ''

    # ---- KB 检索结果 ----
    if tool_name.startswith('kb.'):
        fragments = data.get('records') if isinstance(data, dict) else data
        return digest(fragments or [], db_type)

    # ---- 时序 KPI ----
    series = _extract_series(data)
    if series:
        metric_key = _pick_metric(data, series)
        obs = detect(series, metric_key)
        if obs:
            return obs[0]
        # 无异常就是无异常：**不降低阈值凑一条观测**（设计 §4.2 硬约束 2）
        return Observation(
            text=f'{metric_key} 在快照窗口内未检测到显著异常（高精度口径，宁可漏报不误报）',
            evidence={'metric': metric_key, 'points': len(series),
                      'anomalies': 0, 'refs': []})

    # ---- 执行计划 ----
    plan_text = _extract_plan_text(data)
    if plan_text:
        try:
            root = extract(plan_text, db_type)
        except PlanParseError as e:
            # 方言不支持是**如实返回**，不是失败、更不是降级猜测（§9.2）
            return Observation(text=str(e),
                               evidence={'tool': tool_name, 'plan_supported': False,
                                         'refs': []})
        alerts = analyze(root, db_type)
        refs = [f'plan:{tool_name}:{a.code}' for a in alerts]
        text = '；'.join(a.alert for a in alerts) if alerts else \
            '执行计划结构未触发启发式告警'
        return Observation(text=text,
                           evidence={'tool': tool_name, 'alerts': len(alerts),
                                     'codes': [a.code for a in alerts], 'refs': refs})

    # ---- 其余：原样透传，refs 记工具名 ----
    from monitor import degrade
    degrade.note(f'oam.mma.{tool_name}', '无法凝练为结构化观测，原样透传')
    return Observation(text=str(data)[:4000],
                       evidence={'tool': tool_name, 'passthrough': True,
                                 'refs': [f'tool:{tool_name}']})


def _extract_series(data) -> list:
    """从工具结果里抠出 `[(ts, value)]`。识别两种常见形状。"""
    if not isinstance(data, dict):
        return []
    val = data.get('points')
    if isinstance(val, list) and val and all(
            isinstance(p, (list, tuple)) and len(p) == 2 for p in val):
        return [(p[0], p[1]) for p in val]
    # {metric: {'points': [...]}} 的聚合形状：取点数最多的那条
    best = []
    for _, v in data.items():
        if isinstance(v, dict) and isinstance(v.get('points'), list):
            if len(v['points']) > len(best):
                best = v['points']
    return list(best)


def _pick_metric(data, series) -> str:
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and v.get('points') is series:
                return k
    return 'metric'


def _extract_plan_text(data) -> str:
    if isinstance(data, dict):
        val = data.get('plan_text') or data.get('plans')
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and item.get('plan_text'):
                    return item['plan_text']
    return ''

__all__ = [
    # 契约类型（从 types.py 原样转出，感知层不另造一份形状）
    'Observation', 'PlanNode', 'PlanAlert',
    # 模态一：KPI 时序（norm_ppf / gesd_critical_value 一并导出，供 L5 复算临界值）
    'detect', 'infer_period', 'stl_decompose', 'generalized_esd', 'gesd_critical_value',
    'norm_cdf', 'norm_ppf',
    # 模态二：执行计划
    'extract', 'PlanParseError', 'analyze', 'Rule', 'RULES', 'RuleContext',
    'SUPPORTED_DIALECTS', 'UNSUPPORTED_MESSAGE', 'normalize_dialect', 'is_supported',
    # 模态三：KB 凝练
    'digest', 'render_digest_with_llm',
    # 集成桥：L4 循环唯一的凝练入口
    'summarize_tool_result',
]
