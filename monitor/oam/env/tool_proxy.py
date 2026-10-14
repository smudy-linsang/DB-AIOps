# -*- coding: utf-8 -*-
"""工具代理：统一入口 + 结构化错误反馈（论文 §5.3.1 的"稳定反馈"）。

论文原文：*"交互全链路（工具解析、调用、响应格式化）嵌入校验与结构化错误反馈
—— 每步要么返回格式良好的输出，要么返回**确定性错误信号**"*，
并举了具体例子：智能体试图调整 `work_mem` 这类系统不允许的操作，
环境返回相应错误。

所以本模块的契约是：**不存在"静默失败"，也不存在"看起来成功"**。
`call()` 要么返回 `ToolResult.success(data)`，要么返回带确定性 `error_code`
的 `ToolResult.error(...)`。REV-02（自愈"执行"不连库却写 status=success）
就是"看起来成功"的典型，本层就是它的结构性防线。

## 错误码一览

| 码 | 含义 | 设计出处 |
|---|---|---|
| `UNKNOWN_TOOL` | 工具不存在 | §4.1 |
| `BAD_PARAMS` | 参数不合法（含"参数不在契约内"= 禁止自由 SQL） | §4.1 |
| `NOT_REPLAYABLE` | 该工具在回放环境不可用 | §4.1 |
| `MUTATION_FORBIDDEN` | 变更类操作，须走风险标签与人工审批 | §5.5 + §4.5 |
| `TOOL_FAILED` | 工具执行异常（已 `degrade.note` 留痕） | §4.1 |
| `DIALECT_MISMATCH` | **本项目增量**：工具与被诊断实例方言不符 | §9.2 / REV-10 |
| `RATE_LIMITED` | 触发工具调用限速 | §4.5 "限速"待办项 |
| `NO_DATA` | 快照里没有这条信号 —— 属**证据不足**，不是失败 | §4.4 |

## 只读是底座，不是可选项

论文 §5.5 生产第一条：*"在线工具访问只读、限速、白名单"*。
- 白名单：`monitor/oam/tool_catalog.py` 的 `TOOLS` 登记表；
- 只读：`mutating` 一律 `MUTATION_FORBIDDEN`，真实取数走
  `DbConnector.get_connection(..., readonly=True)`；
- 限速：本模块的令牌桶（在线模式生效，回放模式不限）。

**不提供旁路**：没有"跳过代理直连"的代码路径。
"""
import logging
import threading
import time

from monitor import degrade
from monitor.oam.tool_catalog import get_tool
from monitor.oam.types import (
    ERR_BAD_PARAMS, ERR_MUTATION_FORBIDDEN, ERR_NO_DATA,
    ERR_NOT_REPLAYABLE, ERR_TOOL_FAILED, ERR_UNKNOWN_TOOL,
    ToolResult,
)

logger = logging.getLogger('monitor.oam')

ERR_DIALECT_MISMATCH = 'DIALECT_MISMATCH'
ERR_RATE_LIMITED = 'RATE_LIMITED'


class _TokenBucket:
    """极简令牌桶。在线诊断按分钟限速，防智能体把目标库当自己的私库刷。"""

    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = capacity
        self.refill = refill_per_sec
        self.tokens = float(capacity)
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> bool:
        now = time.monotonic()
        with self._lock:
            self.tokens = min(self.capacity,
                              self.tokens + (now - self.updated) * self.refill)
            self.updated = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


class ToolProxy:
    """工具调用的统一入口。

    `mode`：
      · `'replay'` —— 从冻结快照取数，评测台用；
      · `'live'`   —— 连真实实例只读取数，生产诊断用，受限速约束。

    `ctx` 需提供 `.snapshot`（OamSnapshot）与可选的 `.db_type`。
    """

    def __init__(self, ctx, mode: str = 'replay', handlers=None,
                 rate_limit_per_min: int = 60):
        self.ctx = ctx
        self.mode = mode
        # handlers: {tool_name: callable(ctx, params) -> Any}，由 replay.py 注入
        self.handlers = handlers or {}
        self._bucket = _TokenBucket(capacity=max(rate_limit_per_min, 1),
                                    refill_per_sec=max(rate_limit_per_min, 1) / 60.0)
        self.call_count = 0

    # ------------------------------------------------------------------
    def call(self, name: str, params: dict) -> ToolResult:
        """七步防御：白名单 → 方言 → **变更拦截** → 参数 → 回放可用 → 限速 → 执行。

        变更拦截**故意排在参数校验之前**：设计 §8 的验收项是
        "策略请求 mutating 工具 → 断言 MUTATION_FORBIDDEN"，无条件成立。
        若把参数校验放前面，一个参数拼错的变更请求就会返回 BAD_PARAMS，
        把"这类操作根本不允许你调"这条最强的信号稀释成"参数写错了"。
        """
        params = params if isinstance(params, dict) else {}
        spec = get_tool(name, self._db_type())
        if spec is None:
            return ToolResult.error(ERR_UNKNOWN_TOOL, f'工具 {name} 不存在',
                                    requested=name)
        if spec.db_type != self._db_type():
            # REV-10 的结构性防线：Oracle 的工具规格不允许用于 MySQL 事故。
            return ToolResult.error(
                ERR_DIALECT_MISMATCH,
                f'工具 {name} 的规格属于 {spec.db_type}，不能用于 {self._db_type()} 诊断',
                tool_db_type=spec.db_type, case_db_type=self._db_type())

        # 变更类操作不由智能体直接执行（论文 §5.5）。
        # 注意这条在回放与在线**都生效** —— 变更类根本不应进策略的动作空间。
        if spec.mutating:
            return ToolResult.error(
                ERR_MUTATION_FORBIDDEN,
                '变更类操作须走风险标签与人工审批流程，不得由智能体直接执行',
                tool=name, risk='mutating')

        ok, reason = spec.validate(params)
        if not ok:
            return ToolResult.error(ERR_BAD_PARAMS, reason, tool=name)

        if self.mode == 'replay' and not spec.replayable:
            return ToolResult.error(ERR_NOT_REPLAYABLE, f'{name} 在回放环境不可用',
                                    tool=name)

        if self.mode != 'replay' and not self._bucket.take():
            return ToolResult.error(ERR_RATE_LIMITED,
                                    '工具调用超出限速，请减少探查轮次',
                                    tool=name, per_min=len(self._bucket.tokens))

        handler = self.handlers.get(name)
        if handler is None:
            return ToolResult.error(ERR_TOOL_FAILED,
                                    f'工具 {name} 未接入实现', tool=name)

        try:
            self.call_count += 1
            raw = handler(self.ctx, params)
        except Exception as e:
            # 论文要求每步要么格式良好要么确定性错误 —— 这里正是那个确定性错误。
            degrade.note(f'oam.tool.{name}', '工具执行失败', e)
            return ToolResult.error(ERR_TOOL_FAILED, f'{type(e).__name__}: {e}',
                                    tool=name)

        # 缺数据不是失败：单独一种码，让上层能区分"查了没有"和"查不了"。
        if raw is None:
            return ToolResult.error(ERR_NO_DATA,
                                    f'工具 {name} 在快照中没有对应信号（证据不足）',
                                    tool=name)
        return ToolResult.success(raw)

    # ------------------------------------------------------------------
    def _db_type(self) -> str:
        return getattr(self.ctx, 'db_type', '') or ''

    def catalog(self, include_mutating: bool = False) -> list:
        """给策略看的工具清单。变更类默认**不出现在这里**。"""
        from monitor.oam.tool_catalog import tools_for, mutating_tools
        specs = tools_for(self._db_type()) if not include_mutating \
            else tools_for(self._db_type()) + mutating_tools(self._db_type())
        return [{'name': s.name, 'kind': s.kind,
                 'params': s.params_schema.get('properties', {}),
                 'description': s.enriched_description,
                 'mutating': s.mutating, 'replayable': s.replayable}
                for s in specs]


__all__ = ['ToolProxy', 'ERR_DIALECT_MISMATCH', 'ERR_RATE_LIMITED']
