# -*- coding: utf-8 -*-
"""L2 感知层 · 模态二下半段：算子图规则分析器。

对应设计 §4.2「**规则分析器**遍历结构，在算子及其局部邻域应用启发式」，
产出结构化 `PlanAlert`（相关算子 + 父子上下文的支撑证据）：

    估算/实际行数巨大差异 → 基数估计错误（统计信息过期）
    大表低选择率顺序扫描 → 索引缺失或不可用
    并行/分布式连接或聚合中的数据倾斜

## 在整条链里的位置（设计 §9.4）

论文 Table 3b（按原文更正后的读法：完整 MMA 是**规则 + LLM 的组合**）：

| 计划算子数 | 1-30 | 30-60 | 60-90 |
|-----------|------|-------|------|
| 完整 MMA | 95.2 | 100.0 | 100.0 |
| 仅规则 | 90.5 | **85.7** | 100.0 |
| 仅 LLM | 81.0 | 100.0 | **75.0** |
| 去掉该模块（原始文本直给 LLM） | 76.2 | 42.9 | **0.0** |

读法有两层，本模块**只负责其中一层**：

1. **"仅规则"不是处处占优** —— 30-60 算子区间只有 85.7，低于仅 LLM 的 100.0。
   规则强项是**大计划的结构判读**，不是全区间。规则覆盖不到的局部判断要留给
   上层（策略侧基于这些 `PlanAlert` 做自然语言化与补充判断）；
2. **"把原始计划文本丢给 LLM"在 60-90 算子上直接归零**（不是"差一点"）。

所以 V3.0 的口径是：**本模块是结构骨干**，遍历算子图出 `PlanAlert`；
**LLM 永远拿不到原始计划文本**，只能拿结构化后的算子图与告警。
另一个理由更实际：本模块纯规则、离线、可单测，必须能在没有 LLM 时给出确定性结论。

## 阈值从哪来

设计文档给出的只有 `selectivity < 0.05` 与"一个数量级以上"。其余是**本项目的
工程取值**，统一提为模块级常量（标注"论文未给值，按需调整"），**不散落在条件里**
—— 散落就等于没人知道该调哪个。
**这些阈值只能用于调节告警数量，不得为了让某条诊断"说得通"而下调。**

## 数据缺失时的口径（不猜）

规则依赖计划里不一定存在的数据，缺就**不报**，而不是拿默认值蒙一个：

- `CARD_EST_ERROR` 要求 `est_rows > 0 且 act_rows > 0`。任一侧缺失（MySQL 的
  `EXPLAIN FORMAT=JSON` 没有实际行数）就不判 —— 把"没有实际行数"说成
  "估算错了"是编造；
- `MISSING_INDEX` 的"大表"与"选择率"优先取调用方从目录表给的
  `table_rows={'public.orders': 5000000}`；没有目录数据时退到**计划自身给出的量**
  （MySQL `rows_examined_per_scan` / `filtered`，PG `Rows Removed by Filter`），
  两者都是计划里的真实数字，不是推测；都拿不到就不报；
- `DATA_SKEW` 优先用**每次执行耗时离散度**（PG 并行节点的 `loops` 就是 worker 数，
  `actual time=tmin..tmax` 的比值直接就是倾斜度量），没有则用子节点行数失衡。

每条 evidence 都带 `*_source` 字段说明数据来自目录还是计划推导，
让 L5 审计能判断这条告警的证据强度。
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Callable

from monitor.oam.types import PlanAlert, PlanNode

from .plan_graph import normalize_dialect

logger = logging.getLogger('monitor.oam.perception')

__all__ = ['analyze', 'Rule', 'RULES', 'RuleContext', 'MAX_ALERTS']

# --------------------------------------------------------------------------
# 阈值常量
# --------------------------------------------------------------------------
CARD_EST_RATIO = 10.0         # 论文未给值，按需调整：估算/实际相差一个数量级即判基数估计错误
CARD_EST_RATIO_LO = 1.0 / CARD_EST_RATIO
CARD_EST_RATIO_HI = CARD_EST_RATIO
LARGE_TABLE_ROWS = 100_000.0  # 论文未给值，按需调整："大表"门槛（百万行量级更保守）
LOW_SELECTIVITY = 0.05        # 设计 §4.2 明确写死：选择率 < 5%
SKEW_RATIO = 3.0              # 论文未给值，按需调整：耗时或行数失衡倍数
MIN_SKEW_CHILDREN = 2         # 行数失衡至少要有 2 个子节点
MAX_ALERTS = 50               # 论文未给值，按需调整：告警上限（LLM 上下文有限，不能灌满）
ANCESTOR_DEPTH = 2            # 论文未给值，按需调整："局部邻域"往上取几层算子
EPS = 1e-9

FULL_SCAN_OPS = ('Seq Scan', 'Table Scan')   # 按后缀匹配：PG 的算子名可能是 'Parallel Seq Scan'
JOIN_AGG_TOKENS = ('Join', 'Aggregate', 'Grouping', 'Group')


@dataclass(frozen=True)
class Rule:
    """一条启发式规则。

    `evaluate(node, ctx)` 返回**支撑证据 dict**（空或 None 表示不触发），
    告警的 evidence 直接由它构成 —— "没有证据就不出告警"。
    """

    code: str
    alert: str
    evaluate: Callable[[PlanNode, 'RuleContext'], dict | None]

    def run(self, node: PlanNode, ctx: 'RuleContext') -> PlanAlert | None:
        evidence = self.evaluate(node, ctx)
        if not evidence:
            return None
        payload: dict[str, Any] = {
            'db_type': ctx.db_type,
            'relation': node.relation,
            'object_name': node.object_name,
            'op_label': ctx.node_label(node),
            'parent_op': ctx.parent_op,
            'parent_chain': ctx.parent_chain,
        }
        payload.update(evidence)
        return PlanAlert(code=self.code, op=node.op, alert=self.alert, evidence=payload)


class RuleContext:
    """规则运行时的上下文：父子关系、方言，以及计划之外的目录信息。

    设计 §4.2 的伪码里是 `ctx.table_rows(n)` / `selectivity(n)` / `skew_ratio(n)`，
    本类就是它的可运行版本 —— **表行数来自目录表、不来自执行计划**，
    所以必须由调用方注入；没有就不猜。
    """

    def __init__(self, db_type: str, table_rows: dict[str, float] | None = None,
                 ancestors: list[PlanNode] | None = None):
        self.db_type = normalize_dialect(db_type) or str(db_type or '').strip().lower()
        self.table_rows_map = {str(k): float(v) for k, v in (table_rows or {}).items()
                               if v is not None}
        #: 当前节点的祖先（由近及远），来自 `PlanNode.walk()` 的父节点上下文
        self.ancestors = list(ancestors or [])

    # ---- 结构上下文 ----
    @property
    def parent_op(self) -> str:
        return self.ancestors[0].op if self.ancestors else ''

    @property
    def parent_chain(self) -> list[str]:
        return [a.op for a in self.ancestors[:ANCESTOR_DEPTH]]

    @staticmethod
    def node_label(node: PlanNode) -> str:
        parts = [node.op]
        if node.relation:
            parts.append(f'on {node.relation}')
        if node.object_name:
            parts.append(f'using {node.object_name}')
        return ' '.join(parts)

    # ---- 目录信息 ----
    def table_rows(self, relation: str | None) -> tuple[float | None, str]:
        """返回 (表行数, 来源说明)；来源写进 evidence，便于审计判断证据强度。"""
        if not relation:
            return None, 'no_relation'
        value = self.table_rows_map.get(str(relation))
        return (value, 'catalog') if value is not None else (None, 'catalog_unknown')

    # ---- 计划内推导（不是推测，是计划里真实存在的数字）----
    @staticmethod
    def selectivity(node: PlanNode) -> tuple[float | None, str]:
        """选择率 = 过滤后存活行数 / 被扫描行数。来源三选一。"""
        native = getattr(node, '_selectivity', None)
        if native is not None:
            return float(native), 'plan.filtered'
        produced = float(node.est_rows or 0.0)
        removed = getattr(node, '_rows_removed_by_filter', None)
        if removed is not None and (produced + float(removed)) > 0:
            return produced / (produced + float(removed)), 'plan.rows_removed_by_filter'
        scan_rows = getattr(node, '_scan_rows', None)
        if scan_rows is not None and float(scan_rows) > 0 and produced > 0:
            return produced / float(scan_rows), 'plan.rows_examined_per_scan'
        return None, 'unknown'

    @staticmethod
    def scan_size(node: PlanNode) -> tuple[float | None, str]:
        """这次扫描要读多少行 —— 没有目录数据时的"大表"代理量。"""
        removed = getattr(node, '_rows_removed_by_filter', None)
        if removed is not None:
            return float(node.est_rows or 0.0) + float(removed), 'plan.rows_removed_by_filter'
        scan_rows = getattr(node, '_scan_rows', None)
        if scan_rows is not None and float(scan_rows) > 0:
            return float(scan_rows), 'plan.rows_examined_per_scan'
        return None, 'unknown'

    @staticmethod
    def act_time_spread(node: PlanNode) -> tuple[float | None, int]:
        """(耗时上界/下界, loops)。PG 并行节点的 worker 数就藏在 loops 里。"""
        loops = int(getattr(node, '_loops', 0) or 0)
        tmax = getattr(node, '_act_time_max', None)
        tmin = getattr(node, '_act_time_min', None)
        if tmax is None or tmin is None or float(tmax) <= 0:
            return None, loops
        ratio = float(tmax) / (float(tmin) if float(tmin) > 0 else EPS)
        return ratio, loops

    @staticmethod
    def child_rows(node: PlanNode) -> list[tuple[str, float]]:
        return [(c.op, float(c.act_rows)) for c in node.children if c.act_rows > 0]

    @staticmethod
    def child_imbalance(node: PlanNode) -> float | None:
        """子节点实际行数失衡倍数：最大子 / 其余子均值。无数据返回 None。"""
        values = [rows for _, rows in RuleContext.child_rows(node)]
        if len(values) < MIN_SKEW_CHILDREN:
            return None
        top = max(values)
        rest = sum(values) - top
        if rest <= 0:
            return None
        return top / (rest / (len(values) - 1))


# ==========================================================================
# 三条启发式（论文 §4.2）
# ==========================================================================
def _card_est_error(node: PlanNode, ctx: RuleContext) -> dict | None:
    """估算/实际行数相差一个数量级以上 → 基数估计错误（统计信息过期）。"""
    est, act = float(node.est_rows or 0.0), float(node.act_rows or 0.0)
    if est <= 0 or act <= 0:
        return None          # 任一侧缺失就不判，见模块 docstring「数据缺失时的口径」
    ratio = est / act
    if CARD_EST_RATIO_LO <= ratio <= CARD_EST_RATIO_HI:
        return None
    return {
        'est_rows': est,
        'act_rows': act,
        'ratio': round(ratio, 4),
        'direction': 'over_estimate' if ratio > 1.0 else 'under_estimate',
        'over_factor': round(max(ratio, 1.0 / ratio), 4),
        'filters': node.filter_pred,
        'loops': int(getattr(node, '_loops', 1) or 1),
        'rows_source': 'plan',
    }


def _missing_index(node: PlanNode, ctx: RuleContext) -> dict | None:
    """大表上的低选择率顺序扫描 → 索引缺失或索引不可用。"""
    if not any(node.op.endswith(suffix) for suffix in FULL_SCAN_OPS):
        return None
    if not node.filter_pred:
        return None          # 没有过滤条件的全表扫描不是"低选择率"，是本来就得全读
    selectivity, sel_src = ctx.selectivity(node)
    if selectivity is None or selectivity >= LOW_SELECTIVITY:
        return None
    table_rows, rows_src = ctx.table_rows(node.relation)
    if table_rows is None:
        table_rows, rows_src = ctx.scan_size(node)
    if table_rows is None or table_rows <= LARGE_TABLE_ROWS:
        return None
    possible_keys = getattr(node, '_possible_keys', None)
    return {
        'table_rows': table_rows,
        'table_rows_source': rows_src,
        'selectivity': round(float(selectivity), 6),
        'selectivity_source': sel_src,
        'est_rows': float(node.est_rows or 0.0),
        'act_rows': float(node.act_rows or 0.0),
        'filter': node.filter_pred,
        'used_index': node.object_name,
        'possible_keys': list(possible_keys) if isinstance(possible_keys, (list, tuple)) else [],
        'access_type': getattr(node, '_access_type', None),
        'child_ops': [c.op for c in node.children],
    }


def _data_skew(node: PlanNode, ctx: RuleContext) -> dict | None:
    """并行/分布式连接或聚合中的数据倾斜。

    两条证据路径，都只用计划里真实存在的数字：
    1. 耗时离散度：PG 并行节点的 `actual time=tmin..tmax`，`loops` 即 worker 数 ——
       最直接的倾斜度量（worker 之间工作量严重不均）；
    2. 子节点行数失衡：连接/聚合两侧实际行数严重不对等。
    """
    loops = int(getattr(node, '_loops', 0) or 0)
    parallel = node.parallel_degree > 1 or loops > 1
    join_or_agg = any(token in node.op for token in JOIN_AGG_TOKENS) or bool(node.distribution)
    if not (parallel or join_or_agg):
        return None

    ratio, loops = ctx.act_time_spread(node)
    if ratio is None or loops <= 1:
        ratio, source = ctx.child_imbalance(node), 'child_row_imbalance'
    else:
        source = 'act_time_spread'
    if ratio is None or not math.isfinite(ratio) or ratio <= SKEW_RATIO:
        return None
    return {
        'skew_ratio': round(float(ratio), 4),
        'signal': source,
        'parallel_degree': node.parallel_degree,
        'workers_launched': getattr(node, '_workers_launched', None),
        'loops': loops,
        'distribution': node.distribution,
        'act_time_range': [getattr(node, '_act_time_min', None),
                           getattr(node, '_act_time_max', None)],
        'child_rows': [{'op': op, 'act_rows': rows} for op, rows in ctx.child_rows(node)],
        'child_ops': [c.op for c in node.children],
    }


RULES = (
    Rule('CARD_EST_ERROR',
         '估算行数与实际行数巨大差异 → 基数估计错误，通常由统计信息过期引起',
         _card_est_error),
    Rule('MISSING_INDEX',
         '大表上低选择率顺序扫描 → 索引缺失或不可用',
         _missing_index),
    Rule('DATA_SKEW',
         '并行/分布式连接或聚合中的数据倾斜',
         _data_skew),
)


# ==========================================================================
# 入口
# ==========================================================================
def _alert_identity(alert: PlanAlert) -> tuple:
    """取一条告警的"识别量"：同规则、同识别量 ⇒ 同一个发现。

    不能直接拿整个 evidence 做键 —— evidence 里混着 `filters` / `child_ops`
    这类**随所在节点变化**的上下文字段，PG 的 LIMIT 与其子 Seq Scan 证据几乎相同、
    仅过滤器一栏不同，用全量字典当键去不了重。而这条规则真正断言的是
    "这组估算/实际行数"，与它出现在哪个包裹节点上无关。
    """
    ev = alert.evidence or {}
    code = alert.code
    if code == 'CARD_EST_ERROR':
        return (code, ev.get('est_rows'), ev.get('act_rows'))
    if code == 'MISSING_INDEX':
        return (code, str(ev.get('filter') or ''), ev.get('table_rows'),
                round(float(ev.get('selectivity') or 0.0), 6))
    if code == 'DATA_SKEW':
        return (code, str(ev.get('relation') or ev.get('op') or ''),
                round(float(ev.get('skew_ratio') or ev.get('skew') or 0.0), 4))
    # 未知规则：退回全量 evidence，宁可不合并也不误合并
    return (code, json.dumps(ev, sort_keys=True, default=str, ensure_ascii=False))


def _dedup_alerts(alerts: list[PlanAlert]) -> list[PlanAlert]:
    """同规则 + 同识别量 → 视为同一个发现，只保留第一条。

    为什么需要：PG 的 `EXPLAIN ANALYZE` 里 LIMIT / Sort / Gather 这类**包裹节点**
    会直接继承子节点的 estimated/actual 行数，于是一条基数估计错误会在父子两层
    各报一次。重复的告警会让上层以为有两个独立证据，抬高"问题严重性"的观感。

    不同算子上的同类问题识别量不同，仍各自保留 —— 去重只消除"同一个问题报两遍"。
    """
    seen = set()
    out = []
    for alert in alerts:
        key = _alert_identity(alert)
        if key in seen:
            continue
        seen.add(key)
        out.append(alert)
    return out


def analyze(root: PlanNode, db_type: str, table_rows: dict[str, float] | None = None,
            max_alerts: int = MAX_ALERTS) -> list[PlanAlert]:
    """遍历结构化计划，对每个问题输出结构化告警，记录相关算子与父子上下文证据。

    `table_rows` 是**可选的目录表行数映射**（`{'public.orders': 5000000}`）。
    设计 §4.2 的伪码里 `ctx.table_rows(n)` 需要它，而执行计划本身不含表行数；
    不给就走计划内推导的代理量（见模块 docstring），**没有就不推测**。

    `max_alerts <= 0` 表示不限。超过上限会截断，并在保留下来的每条告警 evidence 里
    标出 `dropped_alerts` —— **截断必须留痕**，否则上层会误以为"只有这些"。
    """
    if root is None:
        return []

    # 先立一次父子索引，拿到每个节点真实的祖先链（设计 §4.2 的"局部邻域"）
    parent_of: dict[int, PlanNode] = {}
    for node, parent in root.walk():
        if parent is not None:
            parent_of[id(node)] = parent

    alerts: list[PlanAlert] = []
    for node, _ in root.walk():
        ancestors: list[PlanNode] = []
        cur = parent_of.get(id(node))
        while cur is not None and len(ancestors) < ANCESTOR_DEPTH:
            ancestors.append(cur)
            cur = parent_of.get(id(cur))
        ctx = RuleContext(db_type, table_rows, ancestors)
        for rule in RULES:
            alert = rule.run(node, ctx)
            if alert is not None:
                alerts.append(alert)

    alerts = _dedup_alerts(alerts)

    limit = int(max_alerts)
    if limit > 0 and len(alerts) > limit:
        dropped = len(alerts) - limit
        alerts = alerts[:limit]
        for alert in alerts:
            alert.evidence['dropped_alerts'] = dropped
        logger.warning('计划规则告警 %s 条超过上限 %s，已截断（丢弃 %s 条）',
                       db_type, limit, dropped)
    return alerts
