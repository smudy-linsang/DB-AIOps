# -*- coding: utf-8 -*-
"""OAM 各层共享的数据契约。

这个文件是 L1/L2/L3/L4/L5 之间的**唯一**类型来源。新增类型前先在这里定义，
不要在各层各造一份形状相近但字段不同的类 —— V2.6 的"三代 RCA 引擎并存"
正是形状漂移逼出来的债。

三条不变量（被 L5 证据审计逐条依赖，破坏其中任何一条审计就失去意义）：
1. `Observation.evidence` 必填。空的 evidence 等于"编造"——审计模块要靠它
   判断这条结论是不是有工具观测支撑（设计 §4.2、§4.5）。
2. `ToolResult` 不允许"看起来成功"。失败一律带确定性 `error_code`
   （设计 §4.1 的"绝不出现静默失败"）。
3. `Verdict.escalated=True` 表示"证据不足、转人工"，它与 `passed` 正交：
   合法终止**不计成功，也不计幻觉**（设计 §9.1）。
"""
from dataclasses import dataclass, field
from typing import Any


# ==========================================================================
# L2 感知层产出
# ==========================================================================
@dataclass
class Observation:
    """一条自然语言观测。`evidence` 不是可选字段，是审计的地基。"""

    text: str
    evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.evidence, dict):
            raise TypeError('Observation.evidence 必须是 dict（证据审计要逐条核对）')

    @property
    def evidence_refs(self) -> list:
        """供报告引用为 evidence_refs 的稳定 ID 列表。"""
        refs = self.evidence.get('refs')
        if isinstance(refs, list):
            return [str(r) for r in refs]
        return []


@dataclass
class PlanNode:
    """执行计划的算子节点。原文 §5.2：算子可达上百个，故必须结构化。

    注意：设计 §4.2/§9.4 明确 **LLM 永远拿不到原始计划文本**，
    只能拿结构化后的算子图与告警（论文 Table 3b：原始文本直给 LLM
    在 60-90 算子的大计划上成功率直接归零）。
    """

    op: str
    est_rows: float = 0.0
    act_rows: float = 0.0
    total_cost: float = 0.0
    local_cost: float = 0.0
    sort: bool = False
    parallel_degree: int = 1
    distribution: str | None = None
    relation: str | None = None
    object_name: str | None = None
    filter_pred: str | None = None
    children: list = field(default_factory=list)

    def walk(self):
        """深度优先遍历，产出 (节点, 父节点) —— 规则分析器要取父子上下文。"""
        stack = [(self, None)]
        while stack:
            node, parent = stack.pop()
            yield node, parent
            for child in reversed(node.children):
                stack.append((child, node))

    @property
    def operator_count(self) -> int:
        return sum(1 for _ in self.walk())


@dataclass
class PlanAlert:
    """规则分析器出的结构化告警（设计 §4.2 的三条启发式）。"""

    code: str
    op: str
    alert: str
    evidence: dict = field(default_factory=dict)


# ==========================================================================
# L1 环境与工具
# ==========================================================================
@dataclass
class ToolResult:
    """工具调用的结果。成功与失败都是**一等**，不允许空壳成功。

    构造用 `ToolResult.success()` / `ToolResult.error()`，不用 `ToolResult.ok()` ——
    后者会与实例字段 `ok` 撞名。字段名沿用设计文档的 `ok`。
    """

    ok: bool
    data: Any = None
    error_code: str = ''
    error_msg: str = ''
    error_params: dict = field(default_factory=dict)

    @classmethod
    def success(cls, data: Any) -> 'ToolResult':
        return cls(ok=True, data=data)

    @classmethod
    def error(cls, code: str, msg: str, **params) -> 'ToolResult':
        return cls(ok=False, error_code=code, error_msg=msg, error_params=params)

    def to_observation_payload(self) -> dict:
        """给策略看的载荷。失败时也带上确定性错误信号，模型才知道怎么改。"""
        if self.ok:
            return {'ok': True, 'data': self.data}
        return {'ok': False, 'error_code': self.error_code,
                'error': self.error_msg, **self.error_params}


# 结构化错误码（设计 §4.1；这些值会进 OamStep.tool_error_code）
ERR_UNKNOWN_TOOL = 'UNKNOWN_TOOL'
ERR_BAD_PARAMS = 'BAD_PARAMS'
ERR_NOT_REPLAYABLE = 'NOT_REPLAYABLE'
ERR_MUTATION_FORBIDDEN = 'MUTATION_FORBIDDEN'
ERR_TOOL_FAILED = 'TOOL_FAILED'
ERR_NO_DATA = 'NO_DATA'          # 快照里没有这条信号 —— 属"证据不足"，不是失败


# ==========================================================================
# L4 策略层
# ==========================================================================
@dataclass
class Action:
    """一次决策：要么调工具，要么终止并给出报告（设计 §1.2 的动作空间）。"""

    tool: str = ''
    params: dict = field(default_factory=dict)
    final: bool = False
    report: dict = field(default_factory=dict)
    reason: str = ''               # final 时的终止理由，含 evidence_insufficient

    @property
    def is_final(self) -> bool:
        return self.final


@dataclass
class Step:
    """一轮思考-行动-观察。

    `plan_tool` / `plan_params` 是**思考块里规划的动作**，`action` 是**实际执行的
    动作**。两者分开存是 r_tool 的前提 —— 该奖励专门惩罚"空想式调用"
    （想了不调）与"未经规划的执行"（不 想就调）。合成一个字段就没法判这个了。

    `tool_error_code` 非空即本轮工具未产出观测。
    """

    turn: int
    thought: str = ''
    plan_tool: str = ''                       # 思考块里规划要调的工具
    plan_params: dict = field(default_factory=dict)
    action: Action = field(default_factory=Action)
    observation: Observation | None = None
    tool_error_code: str = ''
    tool_error_msg: str = ''
    format_ok: bool = False
    elapsed_ms: int = 0
    belief_entropy: float | None = None

    @property
    def planned(self) -> bool:
        return bool(self.plan_tool.strip()) or bool(self.thought.strip())

    @property
    def executed(self) -> bool:
        return not self.action.final and bool(self.action.tool)

    @property
    def call_failed(self) -> bool:
        return self.executed and bool(self.tool_error_code)


@dataclass
class Trajectory:
    """一次完整的 Think-Act-Observe 回合。

    既是评测对象（`passed`/`reward_parts`），也是审计对象（`steps[].evidence`），
    还是未来的训练样本。论文 §5.3.2 的损失掩码即作用在 `observation` 上：
    环境生成的 token 不进梯度。
    """

    case_id: str
    db_type: str = ''
    policy_backend: str = 'prompted'
    policy_version: str = 'v1'
    steps: list = field(default_factory=list)
    report: dict = field(default_factory=dict)
    passed: bool | None = None
    verdict_reason: str = ''
    escalated: bool = False
    hallucinated: bool | None = None
    reward_total: float | None = None
    reward_parts: dict = field(default_factory=dict)
    started_at: Any = None
    finished_at: Any = None

    @property
    def turns(self) -> int:
        return len(self.steps)

    def executed_calls(self) -> list:
        """实际执行过的工具调用序列（r_tool 的 Exec 来源）。"""
        return [s for s in self.steps if s.executed and not s.tool_error_code]

    def error_calls(self) -> list:
        return [s for s in self.steps if s.executed and s.tool_error_code]

    def agent_turns(self) -> list:
        """策略自己产出的轮次（r_format 的 T）。终止轮也计入 —— 它同样要过格式检查。"""
        return list(self.steps)

    def observation_refs(self) -> set:
        """轨迹中出现过的全部证据引用，供证据审计反查。"""
        refs = set()
        for s in self.steps:
            if s.observation:
                refs.update(s.observation.evidence_refs)
        return refs

    def kb_refs(self) -> set:
        return {r for r in self.observation_refs() if r.startswith('kb:')}


# ==========================================================================
# L1 验证器
# ==========================================================================
@dataclass
class Verdict:
    """验证器判定。

    `escalated=True` = 策略输出"证据不足"并要求转人工 —— 这是**合法终止**，
    不是失败。评测口径：不计成功，也不计幻觉（设计 §9.1）。
    """

    passed: bool
    reason: str = ''
    score: float = 0.0
    escalated: bool = False
    cause_matched: bool = False
    fix_ok: bool = False
    detail: dict = field(default_factory=dict)


__all__ = [
    'Observation', 'PlanNode', 'PlanAlert', 'ToolResult', 'Action', 'Step',
    'Trajectory', 'Verdict',
    'ERR_UNKNOWN_TOOL', 'ERR_BAD_PARAMS', 'ERR_NOT_REPLAYABLE',
    'ERR_MUTATION_FORBIDDEN', 'ERR_TOOL_FAILED', 'ERR_NO_DATA',
]
