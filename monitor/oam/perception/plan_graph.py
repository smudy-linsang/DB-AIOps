# -*- coding: utf-8 -*-
"""L2 感知层 · 模态二上半段：执行计划 → 算子图提取器。

对应设计 §4.2「执行计划：**提取器**把计划文本归一化为算子图（树/DAG，带估算与实际
行数、累计与本地代价、排序/并行度/数据分布）」。

## 方言是一等维度（设计 §9.2）

论文只面向 DWS（PostgreSQL 系），单方言。本项目纳管 6 个方言，而
PG 的 `EXPLAIN ANALYZE` 缩进树、MySQL 的 `EXPLAIN FORMAT=JSON`、
Oracle 的 `DBMS_XPLAN` 文本表格**结构完全不同**。

设计 §9.2 的原文要求是：*"先做 MySQL/PG，其余返回'该方言暂不支持计划分析'
而非降级猜测"*。因此本模块**只实现 MySQL 与 PostgreSQL**，
其余方言（Oracle / 达梦 / GBase / TDSQL，含 TDSQL 的 Oracle 兼容模式）
一律抛 `PlanParseError`，错误消息固定包含 **"该方言暂不支持计划分析"**。

**绝不允许降级猜测**的理由不是洁癖：把 Oracle 的行格式硬套到 PG 的解析器上，
不会报错，只会产出一棵看起来正常、字段全是 0 的算子图 —— 那正是设计 §9.1
说的"证据链看着满、其实没有信息"，而且错误会一路流到 L5 审计。
**解析失败必须是一次显式的失败。**

## 为什么不让 LLM 直接读原始计划文本（设计 §9.4）

论文 Table 3b：去掉该模块（原始计划文本直给 LLM）在 60–90 算子的大计划上
成功率直接 **0.0**，不是"差一点"。所以本模块的产物是**结构化算子图**，
LLM 只能拿图与告警，拿不到文本。

## 解析时写入的私有附加属性

`monitor/oam.types.PlanNode` 的字段是**共享契约**（别的 Agent 在维护），本模块不改它。
计划里那些"很有用但契约里没有槽位"的量，用**动态附加属性**带出来，
由 `plan_rules` 用 `getattr(..., None)` 读取（读不到就走降级路径，不会 KeyError）：

| 属性 | 含义 | 来源 |
|------|------|------|
| `_selectivity` | 过滤后存活行数占扫描行数的比例 | MySQL `filtered/100` |
| `_scan_rows` | 该扫描节点要读的行数 | MySQL `rows_examined_per_scan` |
| `_rows_removed_by_filter` | 被过滤掉的行数 | PG `Rows Removed by Filter` |
| `_possible_keys` | 优化器认为可用的索引 | MySQL `possible_keys` |
| `_access_type` | 原始访问方式 token | MySQL `access_type` |
| `_loops` | 实际执行次数（并行时即 worker 数） | PG `loops` |
| `_act_time_min` / `_act_time_max` | 各次执行的实际耗时上下界 | PG `actual time=` |

## 已知限制（诚实起见）

- **MySQL 的 `EXPLAIN FORMAT=JSON` 没有"实际行数"**（那是 `EXPLAIN ANALYZE`
  或 performance_schema 的事），所以 MySQL 计划的 `act_rows` 恒为 0，
  `CARD_EST_ERROR` 规则在 MySQL 上不会触发。这是数据缺失，不是漏实现。
- MySQL 的 `EXPLAIN FORMAT=JSON` 也**不含表总行数**，大表判定只能退到
  "该次扫描预计要读的行数"，或由调用方从目录表补（`plan_rules.analyze` 的
  `table_rows` 参数）。
- MySQL 的中间层算子（nested_loop 等）不给 `cost_info`，代价字段留 0，不臆造。
- PG 的 `local_cost` 用 `total_cost - Σ children.total_cost` 反推（PG 本身
  只给累计代价），因此可能为负，截断到 0。
- `distribution`（数据分布）目前没有任何已实现方言会填：PG 与 MySQL 的
  EXPLAIN 都不报分布信息。字段留着是因为算子图契约里有它，
  分布式方言（达梦/TDSQL/GBase）接入时才有意义。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from monitor.oam.types import PlanNode

logger = logging.getLogger('monitor.oam.perception')

__all__ = [
    'extract', 'PlanParseError', 'SUPPORTED_DIALECTS', 'UNSUPPORTED_MESSAGE',
    'normalize_dialect', 'is_supported',
]

PG_DIALECT = 'pgsql'
MYSQL_DIALECT = 'mysql'
# 本版支持计划分析的方言（设计 §9.2：先做 MySQL/PG）
SUPPORTED_DIALECTS = (MYSQL_DIALECT, PG_DIALECT)
UNSUPPORTED_MESSAGE = '该方言暂不支持计划分析'
ERR_DIALECT_UNSUPPORTED = 'PLAN_DIALECT_UNSUPPORTED'
ERR_EMPTY_PLAN = 'PLAN_EMPTY'
ERR_BAD_JSON = 'PLAN_BAD_JSON'
ERR_BAD_TEXT = 'PLAN_BAD_TEXT'
ERR_NO_OPERATOR = 'PLAN_NO_OPERATOR'

_DIALECT_ALIASES = {
    'postgresql': PG_DIALECT, 'postgres': PG_DIALECT, 'pg': PG_DIALECT, 'dws': PG_DIALECT,
    'mysql8': MYSQL_DIALECT, 'mariadb': MYSQL_DIALECT,
}


class PlanParseError(RuntimeError):
    """计划解析失败。

    **失败必须显式**：不支持的方言、非法 JSON、认不出的文本格式，一律抛这个，
    绝不返回一棵字段全 0 的"空图"冒充成功（设计 §9.2 + §9.1）。
    `code` 是确定性错误码，供上层做分支，不必去匹配中文消息。
    """

    def __init__(self, message: str, *, db_type: str = '', code: str = 'PLAN_PARSE_ERROR'):
        super().__init__(message)
        self.db_type = db_type
        self.code = code


def normalize_dialect(db_type: str) -> str | None:
    """把 `DatabaseConfig.db_type` 归一到本模块认识的方言；不认识返回 None。"""
    key = str(db_type or '').strip().lower()
    key = _DIALECT_ALIASES.get(key, key)
    return key if key in SUPPORTED_DIALECTS else None


def is_supported(db_type: str) -> bool:
    return normalize_dialect(db_type) is not None


def _f(value: Any, default: float = 0.0) -> float:
    """宽松取浮点：'12.5' / 12 / None / 'xx' 都不该让整个解析失败。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ==========================================================================
# MySQL: EXPLAIN FORMAT=JSON
# ==========================================================================
# query_block 里的**已知算子键** → 算子名。未知键不生成节点，避免把元数据
# （cost_info / select_id / message）误当成算子。
_MYSQL_OP_KEYS = {
    'nested_loop': 'Nested Loop',
    'ordering_operation': 'Sort',
    'grouping_operation': 'Grouping',
    'duplicates_removal': 'Duplicate Removal',
    'union_result': 'Union Result',
    'materialized_from_subquery': 'Materialized Subquery',
    'select_list_subqueries': 'Select List Subquery',
    'table_value_constructor': 'Table Value Constructor',
    'json_table': 'JSON Table',
    'indexes': 'Index Scan',
    'attached_subqueries': 'Attached Subquery',
    'query_specifications': 'Union Part',
}
# MySQL 的 access_type → 算子名。未知 access_type **保留原 token**（拼进算子名），
# 不猜成"顺序扫描"—— 猜错会让 MISSING_INDEX 规则误报。
_MYSQL_ACCESS_OPS = {
    'ALL': 'Table Scan',
    'INDEX': 'Index Scan',
    'RANGE': 'Index Range Scan',
    'REF': 'Index Lookup',
    'REF_OR_RANGE': 'Index Lookup',
    'EQ_REF': 'Index Lookup',
    'CONST': 'Const Lookup',
    'SYSTEM': 'Const Lookup',
    'FULLSCAN': 'Index Scan',
    'FULL OUTER JOIN': 'Full Outer Join',
    'LEFT': 'Left Join',
    'RIGHT': 'Right Join',
}


def _load_mysql_json(plan_text: Any, db_type: str) -> dict:
    if isinstance(plan_text, dict):
        return plan_text
    if not isinstance(plan_text, str) or not plan_text.strip():
        raise PlanParseError('空的 MySQL 计划文本', db_type=db_type, code=ERR_EMPTY_PLAN)
    try:
        data = json.loads(plan_text)
    except (TypeError, ValueError) as exc:
        raise PlanParseError(
            f'MySQL 计划不是合法 JSON：{exc}（EXPLAIN FORMAT=JSON 的结果应直接传 JSON）',
            db_type=db_type, code=ERR_BAD_JSON) from exc
    if not isinstance(data, dict):
        raise PlanParseError(f'MySQL 计划 JSON 顶层应为对象，实际是 {type(data).__name__}',
                             db_type=db_type, code=ERR_BAD_JSON)
    return data


def _mysql_table_node(block: dict) -> PlanNode:
    access = str(block.get('access_type') or '').strip().upper()
    op = _MYSQL_ACCESS_OPS.get(access)
    if op is None:
        op = f'Table Scan[{access or "UNKNOWN"}]'   # 保留原 token，不降级猜测
    node = PlanNode(op=op)
    node.relation = block.get('table_name') or None
    node.object_name = block.get('key') or None            # 实际选中的索引，全表扫描时为 None
    node.filter_pred = block.get('attached_condition') or block.get('condition') or None
    node.est_rows = _f(block.get('rows_produced_per_join'),
                       _f(block.get('rows_examined_per_scan')))
    node.total_cost = _f((block.get('cost_info') or {}).get('prefix_cost'))
    node.local_cost = _f((block.get('cost_info') or {}).get('read_cost'))
    node.sort = bool(block.get('using_filesort'))
    filt = block.get('filtered')
    if filt is not None:
        # MySQL 的 filtered 就是"过滤后存活百分比"，是货真价实的选择率，不是猜的
        node._selectivity = _f(filt, 100.0) / 100.0
    node._scan_rows = _f(block.get('rows_examined_per_scan'))
    node._possible_keys = block.get('possible_keys')
    node._access_type = block.get('access_type')
    return node


def _mysql_block_node(block: dict, op_label: str) -> PlanNode:
    """把一个 MySQL 算子对象转成 PlanNode；算子键的值递归成子节点。"""
    node = PlanNode(op=op_label)
    cost = block.get('cost_info') or {}
    node.total_cost = _f(cost.get('query_cost'), _f(cost.get('prefix_cost')))
    node.local_cost = _f(cost.get('read_cost'))
    node.sort = bool(block.get('using_filesort'))
    node.filter_pred = block.get('attached_condition') or block.get('condition') or None
    for key, value in block.items():
        if key == 'table':
            if isinstance(value, dict):
                node.children.append(_mysql_table_node(value))
            continue
        label = _MYSQL_OP_KEYS.get(key)
        if label is None:
            continue          # cost_info / select_id / message 等元数据不生成算子
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                node.children.append(_mysql_block_node(item, label))
    return node


def _extract_mysql(plan_text: Any, dialect: str) -> PlanNode:
    data = _load_mysql_json(plan_text, dialect)
    if 'query_block' in data and isinstance(data['query_block'], dict):
        return _mysql_block_node(data['query_block'], 'Query Block')
    # EXPLAIN 对空结果集会给 {"message": "..."} 而不是 query_block：仍然是一棵
    # 单节点图（有据可依，不是空图），把 message 带在私有属性上。
    if 'message' in data:
        node = PlanNode(op='Query Block')
        node._message = str(data.get('message') or '')
        return node
    raise PlanParseError('MySQL 计划 JSON 里没有 query_block 字段，可能不是 EXPLAIN FORMAT=JSON 的输出',
                         db_type=dialect, code=ERR_NO_OPERATOR)


# ==========================================================================
# PostgreSQL: EXPLAIN ANALYZE 缩进树
# ==========================================================================
# 代价/行数一律用十进制小数写（EXPLAIN 不会输出科学计数法）。
# 注意字符类里**不能**放 '.'：`[-+0-9.eE]+` 会把 "1234.56..1240.00" 里的 ".." 一起吞掉，
# 导致 total_cost 解析成非法串再退化成 0。
_NUM = r'[-+]?\d+(?:\.\d+)?'
_PG_LINE_RE = re.compile(r'^(?P<lead>\s*)(?:->\s*)?(?P<body>\S.*?)\s*$')
_PG_NODE_RE = re.compile(
    rf'^(?P<op>.*?)\s*'
    rf'\(\s*cost=(?P<cost>{_NUM})(?:\.\.(?P<cost2>{_NUM}))?'
    rf'(?:\s+rows=(?P<rows>{_NUM}))?(?:\s+width=(?P<width>{_NUM}))?\s*\)'
    r'(?P<actual>\s*\(actual.*\))?\s*$'
)
_PG_ACTUAL_RE = re.compile(
    rf'time=(?P<t0>{_NUM})\.\.(?P<t1>{_NUM})\s+rows=(?P<rows>{_NUM})\s+loops=(?P<loops>\d+)'
)
_PG_DETAIL_RE = re.compile(r'^(?P<key>[A-Za-z][A-Za-z ]*?):\s*(?P<value>.*)$')
_PG_ON_RE = re.compile(r'^(?P<op>.*?)\s+on\s+(?P<rel>\S+)$')
_PG_USING_RE = re.compile(r'^(?P<op>.*?)\s+using\s+(?P<idx>\S+)$')
# 会成为 filter_pred 的下压条件行（顺序即拼接顺序）
_PG_PRED_KEYS = ('Filter', 'Index Cond', 'Recheck Cond', 'TID Cond', 'Join Filter',
                 'Hash Cond', 'Merge Cond', 'One-Time Filter', 'Subplan Filter')
# 让节点被标记为"排序/分组"的明细行
_PG_SORT_KEYS = ('Sort Key', 'Presorted Key')
_PG_GROUP_KEYS = ('Group Key',)


def _pg_parse_op(op_text: str) -> tuple[str, str | None, str | None]:
    """从 PG 算子描述里拆出 (算子名, 表/子查询名, 索引名)。

    'Index Scan using idx_orders on public.orders' → ('Index Scan', 'public.orders', 'idx_orders')
    """
    op = ' '.join(op_text.split())
    relation: str | None = None
    index_name: str | None = None
    m = _PG_ON_RE.match(op)
    if m:
        op = m.group('op').strip()
        relation = m.group('rel').strip('()"')
    m = _PG_USING_RE.match(op)
    if m:
        op = m.group('op').strip()
        index_name = m.group('idx').strip('()"')
    return op, relation, index_name


def _pg_node_from_line(op_text: str) -> PlanNode:
    m = _PG_NODE_RE.match(op_text)
    if m is None:
        raise PlanParseError(f'无法解析的 PG 算子行：{op_text!r}', code=ERR_BAD_TEXT)
    op, relation, index_name = _pg_parse_op(m.group('op'))
    node = PlanNode(op=op, relation=relation, object_name=index_name)
    cost2 = m.group('cost2')
    node.total_cost = _f(cost2, _f(m.group('cost')))
    node.local_cost = node.total_cost       # PG 只给累计代价，本地代价稍后由子节点反推
    node.est_rows = _f(m.group('rows'))
    actual = m.group('actual')
    if actual:
        am = _PG_ACTUAL_RE.search(actual)
        if am:
            rows = _f(am.group('rows'))
            loops = int(_f(am.group('loops'), 1.0)) or 1
            # PG 的 rows= 是**单次执行**的平均值，loops=1 时不用管，loops>1
            # （并行 worker 或重复循环）必须乘回去，否则一律低估实际行数
            node.act_rows = rows * loops
            node._loops = loops
            node._act_time_min = _f(am.group('t0'))
            node._act_time_max = _f(am.group('t1'))
            if loops > 1:
                node.parallel_degree = loops
        else:
            # "never executed"：没有 actual 括号，act_rows 保持 0
            pass
    if 'Parallel' in op or 'Workers' in op:
        node.parallel_degree = max(node.parallel_degree, 1)
    return node


def _pg_apply_detail(node: PlanNode, key: str, value: str) -> None:
    key = key.strip()
    if key in _PG_PRED_KEYS:
        prev = node.filter_pred
        node.filter_pred = f'({prev}) AND ({value})' if prev else value
    elif key == 'Rows Removed by Filter':
        node._rows_removed_by_filter = _f(value)
    elif key == 'Workers Planned':
        node.parallel_degree = max(node.parallel_degree, int(_f(value, 0.0)))
    elif key == 'Workers Launched':
        node._workers_launched = int(_f(value, 0.0))
    elif key in _PG_SORT_KEYS or key in _PG_GROUP_KEYS:
        node.sort = True


def _extract_pg_text(plan_text: str, dialect: str) -> PlanNode:
    root: PlanNode | None = None
    # 栈里存 (缩进深度, 节点)。**必须按记录的深度出栈，不能用 len(stack)**：
    # PG 的缩进是"每个层级多 2 个空格"，兄弟节点缩进相同，一个节点的深度可以
    # 远大于它在栈里的位置。只看 len(stack) 会把兄弟节点挂成链，得到错误的树。
    stack: list[tuple[int, PlanNode]] = []
    for raw in plan_text.splitlines():
        if not raw.strip():
            continue
        m = _PG_LINE_RE.match(raw)
        if m is None:
            continue
        lead, body = m.group('lead'), m.group('body')
        depth = len(lead.expandtabs(4)) // 2
        if _PG_NODE_RE.match(body):
            node = _pg_node_from_line(body)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if stack:
                stack[-1][1].children.append(node)
            elif root is None:
                root = node
            else:
                # 又出现一个顶层算子：显式失败，不并到根上凑数
                raise PlanParseError('PG 计划缩进结构异常：根节点之后又出现了新的顶层算子',
                                     db_type=dialect, code=ERR_BAD_TEXT)
            stack.append((depth, node))
            continue
        if stack:
            dm = _PG_DETAIL_RE.match(body)
            if dm:
                _pg_apply_detail(stack[-1][1], dm.group('key'), dm.group('value'))
        # 'Planning Time:' / 'Execution Time:' / 'JIT:' 这类行没有对应处理分支，
        # 有意忽略：它们是计划的外围元数据，不是算子也不是条件。
    if root is None:
        raise PlanParseError('PG 文本里没有解析出任何算子（EXPLAIN ANALYZE 应含 (cost=... ) 行）',
                             db_type=dialect, code=ERR_NO_OPERATOR)
    _fill_pg_local_cost(root)
    return root


def _fill_pg_local_cost(node: PlanNode) -> None:
    """PG 的本地代价 = 累计代价 - 子节点累计代价之和（截断到 0），递归补全。"""
    if node.children:
        children_cost = sum(c.total_cost for c in node.children)
        node.local_cost = max(node.total_cost - children_cost, 0.0)
        for child in node.children:
            _fill_pg_local_cost(child)


# ==========================================================================
# PostgreSQL: EXPLAIN (FORMAT JSON, ANALYZE)
# ==========================================================================
# 本仓库 monitor/plan_capture.py 落库的 PG 计划就是 JSON 形态
# （plan_json['Plan']['Total Cost']），所以文本树解析失败但输入是 JSON 时走这条路。
# 属于 PostgreSQL 方言内部，不算"降级猜测"：字段名是 PG 自己的定义。
_PG_JSON_SKIP_KEYS = ('Parent Relationship', 'Subplan Name', 'CTE Name',
                      'Alias', 'Async Capable', 'Plan Width', 'Parallel Aware')


def _pg_json_node(plan: dict) -> PlanNode:
    op = str(plan.get('Node Type') or 'Unknown')
    node = PlanNode(op=op)
    node.relation = plan.get('Relation Name') or plan.get('CTE Name') or plan.get('Subplan Name')
    node.object_name = plan.get('Index Name')
    node.est_rows = _f(plan.get('Plan Rows'))
    node.act_rows = _f(plan.get('Actual Rows')) * (int(_f(plan.get('Actual Loops'), 1.0)) or 1)
    node.total_cost = _f(plan.get('Total Cost'))
    node.local_cost = max(node.total_cost - _f(plan.get('Startup Cost')), 0.0)
    node.sort = 'Sort Key' in plan or 'Presorted Key' in plan
    node.filter_pred = ' AND '.join(
        str(plan[k]) for k in _PG_PRED_KEYS if plan.get(k)) or None
    if plan.get('Filter'):
        node._rows_removed_by_filter = _f(plan.get('Rows Removed by Filter'))
    node._loops = int(_f(plan.get('Actual Loops'), 1.0)) or 1
    node._act_time_min = _f(plan.get('Actual Startup Time'))
    node._act_time_max = _f(plan.get('Actual Total Time'))
    workers = int(_f(plan.get('Workers Planned'), 0.0))
    if workers > 0 or plan.get('Parallel Aware'):
        node.parallel_degree = max(node.parallel_degree, workers or 1)
        if workers > 0:
            node._workers_launched = int(_f(plan.get('Workers Launched'), 0.0))
    for sub in plan.get('Plans') or []:
        if isinstance(sub, dict):
            node.children.append(_pg_json_node(sub))
    _fill_pg_local_cost(node)
    return node


def _extract_pg_json(plan_text: str, dialect: str) -> PlanNode:
    try:
        data = json.loads(plan_text)
    except (TypeError, ValueError) as exc:
        raise PlanParseError(f'PG 计划 JSON 非法：{exc}', db_type=dialect, code=ERR_BAD_JSON) from exc
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict) or not isinstance(data.get('Plan'), dict):
        raise PlanParseError('PG 计划 JSON 缺少顶层 Plan 对象', db_type=dialect, code=ERR_BAD_JSON)
    return _pg_json_node(data['Plan'])


# ==========================================================================
# 入口
# ==========================================================================
def extract(plan_text: Any, db_type: str) -> PlanNode:
    """把计划文本解析并归一化为算子图。**按方言分 parser，不跨方言降级。**

    - MySQL → `EXPLAIN FORMAT=JSON`（JSON 串或已解析的 dict）
    - PostgreSQL / DWS → `EXPLAIN ANALYZE` 缩进树（`->` 缩进）；若传入的是
      `EXPLAIN (FORMAT JSON, ANALYZE)` 的 JSON，走对应的 JSON 分支
    - **TDSQL 不在支持范围内**：它的 `EXPLAIN FORMAT=JSON` 与 MySQL 形似，
      但分片键/分布式下推会让"算子"语义不同，本版不做推测，直接抛错
    - 其余方言 → 抛 `PlanParseError`，消息含"该方言暂不支持计划分析"

    失败一律抛 `PlanParseError`（带确定性 `code`），**不返回空图**。
    """
    dialect = normalize_dialect(db_type)
    if dialect is None:
        raise PlanParseError(
            f'{UNSUPPORTED_MESSAGE}：db_type={db_type!r}，本版仅支持 {SUPPORTED_DIALECTS}',
            db_type=str(db_type or ''), code=ERR_DIALECT_UNSUPPORTED)

    if dialect == MYSQL_DIALECT:
        return _extract_mysql(plan_text, dialect)

    if not isinstance(plan_text, str) or not plan_text.strip():
        raise PlanParseError('空的 PG 计划文本', db_type=dialect, code=ERR_EMPTY_PLAN)
    text = plan_text.strip()
    if text[0] in '[{':
        return _extract_pg_json(text, dialect)
    return _extract_pg_text(text, dialect)
