# -*- coding: utf-8 -*-
"""动作空间与诊断状态（论文 §1.2 的 MDP 形式化）。

论文原文：
*状态 = 工具目录 + 累积诊断状态（初始症状 + 此前全部〈动作, 观测〉对）；
动作 = 工具调用 | 输出最终报告的终止动作；折扣因子 γ=1。*

## 动作空间为什么只有两类

策略的输出被强制收敛成两种：调一个工具，或给出最终报告。
这不是简化，而是把"策略想干什么"变成**可判定的东西** ——
工具名与参数要过 `ToolSpec.validate`（禁止自由参数），
终止则要过 `extract_report`（解析失败判 0）。

## "证据不足"是一等公民（设计 §4.4）

论文 §3.3 Limitation：证据不足时**不被强制选择一个已知成因**，
而是走明确的升级路径。所以 `Action.final` 携带的报告允许是
`{'status': 'evidence_insufficient'}`，且这在验证器与评测里都是**合法终止**。

> 这一条直接对治 REV-10 审计缺陷：那里是**无条件追加**一个 0.88 置信度的
> RESOURCE 结论。V3.0 的策略必须能够、也被允许说"我不知道"。
"""
import json
import logging
from dataclasses import dataclass, field

from monitor.oam.format_protocol import (
    ANSWER_CLOSE, ANSWER_OPEN, CALL_CLOSE, CALL_OPEN, THINK_CLOSE, THINK_OPEN,
)
from monitor.oam.types import Action, Step

logger = logging.getLogger('monitor.oam')

STATUS_EVIDENCE_INSUFFICIENT = 'evidence_insufficient'


@dataclass
class DiagnosticState:
    """累积诊断状态：初始症状 + 工具目录 + 全部〈动作, 观测〉对。"""

    symptom: str
    db_type: str = ''
    tool_catalog: list = field(default_factory=list)
    case_id: str = ''
    history: list = field(default_factory=list)     # [{thought, action, observation}]
    facts: dict = field(default_factory=dict)        # 结构化证据累积（φ(V)）

    def append(self, thought: str, action: Action, observation=None):
        entry = {
            'turn': len(self.history) + 1,
            'thought': thought,
            'action': {'tool': action.tool, 'params': action.params, 'final': action.final},
            'observation': observation.text if observation else '',
            'observation_evidence': (observation.evidence if observation else {}),
            'error_code': getattr(action, 'error_code', ''),
        }
        self.history.append(entry)
        return entry

    def render_history(self, max_chars: int = 6000) -> str:
        """渲染给策略看的历史。超长截断但**标明截断**，不静默丢内容。"""
        import json as _json
        text = _json.dumps(self.history, ensure_ascii=False, default=str, indent=1)
        if len(text) <= max_chars:
            return text
        kept = self.history[-(max_chars // 400 or 1):]
        return _json.dumps(kept, ensure_ascii=False, default=str, indent=1) + \
            f'\n…(已截断，保留最近 {len(kept)} 轮；更早的观测见 trajectory 表)'


def parse_action(text: str) -> tuple:
    """从策略输出解析出一个 Step。返回 (step, error)。

    策略输出协议（论文的四个标签）：
        <think>…</think>
        <tool_call>{"tool": "...", "params": {...}}</tool_call>
      或
        <answer>{"root_cause": "...", "fix": "...", ...}</answer>

    解析失败**不重试到死**：返回 error 交由循环按结构化错误反馈一轮，
    连续两轮非法即终止（与既有 `llm/agent.py` 的 bad_streak 口径一致）。
    """
    raw = text or ''
    if not raw.strip():
        return None, '策略输出为空'

    thought = _extract(raw, THINK_OPEN, THINK_CLOSE)

    call = _extract(raw, CALL_OPEN, CALL_CLOSE)
    if call:
        try:
            payload = json.loads(call)
        except (ValueError, TypeError) as e:
            return None, f'tool_call 不是合法 JSON: {e}'
        tool = str(payload.get('tool') or '').strip()
        if not tool:
            return None, 'tool_call 缺少 tool 字段'
        params = payload.get('params')
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return None, f'tool_call 的 params 必须是对象，实为 {type(params).__name__}'
        return Step(turn=0, thought=thought,
                    plan_tool=str(payload.get('plan') or '').strip(),
                    plan_params=payload.get('plan_params') or {},
                    action=Action(tool=tool, params=params)), None

    answer = _extract(raw, ANSWER_OPEN, ANSWER_CLOSE)
    if answer:
        try:
            report = json.loads(answer)
        except (ValueError, TypeError) as e:
            return None, f'answer 不是合法 JSON: {e}'
        if not isinstance(report, dict):
            return None, f'answer 必须是对象，实为 {type(report).__name__}'
        status = str(report.get('status') or '').strip().lower()
        action = Action(
            final=True,
            report=report,
            reason=str(report.get('reason') or ''),
        )
        if status == STATUS_EVIDENCE_INSUFFICIENT:
            action.reason = action.reason or '证据不足'
        return Step(turn=0, thought=thought, action=action), None

    return None, '输出既不含 <tool_call> 也不含 <answer>，不符合思考-行动-观察协议'


def _extract(raw: str, open_tag: str, close_tag: str) -> str:
    """取标签内容，允许 <tool_call> 与 <think> 之间夹着说明文字。"""
    i, j = raw.find(open_tag), raw.find(close_tag)
    if i < 0 or j < 0 or j < i:
        return ''
    return raw[i + len(open_tag):j].strip()


def escalate_report(reason: str, facts: dict | None = None) -> dict:
    """构造升级报告。**这是合法终止，不是错误**。

    设计 §6：`data.report.status = 'evidence_insufficient'` 且不得给出根因，
    同时置 `escalated=true`；前端与评测都按"不计成功、也不计幻觉"处理。
    """
    return {
        'status': STATUS_EVIDENCE_INSUFFICIENT,
        'reason': reason,
        'root_cause': '',                  # 明确不给根因，而不是给一个低置信度的
        'evidence_refs': list((facts or {}).get('refs') or []),
        'requires_human': True,
    }


__all__ = ['DiagnosticState', 'parse_action', 'escalate_report',
           'STATUS_EVIDENCE_INSUFFICIENT']
