# -*- coding: utf-8 -*-
"""四分量奖励（论文 §5.2 / §5.3.2，设计 §4.6）。

原文公式逐字：

```
r_φ = Σᵢ wᵢ · r_i ,   wᵢ ≥ 0 ,  Σᵢ wᵢ = 1 ,  每个 r_φ 归一化到 [0,1]

① r_format(τ) = K / T
② r_tool = 1 − ( |Plan_ue| + |Exec_up| ) / ( |Plan| + |Exec| ) ，截断到 [0,1]
③ r_answer(τ) = H(Ŷ; Σ)
④ r_stepwise⁽ᵗ⁾ = max{ 0 , ( Ent(P_t) − Ent(P_{t+1}) ) / log|C_k| }
```

权重照原文：`w = (0.2, 0.1, 0.5, 0.2)`，每项归一化到 [0,1]。

## 两个细节值得抄（论文 §5.2 自己点出来的）

1. **单步奖励在一次决策触发超过阈值的工具调用数量时置零**（防过度探查）；
2. **贝叶斯信号带历史频率偏差**，所以它只是辅助塑形项 ——
   终止成败仍由基于规则、基于证据的验证器决定。
   设计 §4.6 要求把这条保留意见**写进代码注释**，本文件照办。

## 为什么 P0 就要写奖励

设计 §4.6 的判断："奖励设计（RL 阶段才需要，但现在就要定）"。
P2 的 RL 训练前置条件就是这套奖励口径稳定；如果等到要训模型时才定，
口径会跟着实现漂移，而奖励口径漂移是**最难发现**的一类回归。
"""
import logging

from monitor import appconf
from monitor.oam.format_protocol import check_step_format
from monitor.oam.reward.step_bayes import step_reward
from monitor.oam.verifier import verify

logger = logging.getLogger('monitor.oam')

# 原文权重。Σ = 0.2 + 0.1 + 0.5 + 0.2 = 1
WEIGHTS = {'format': 0.2, 'tool': 0.1, 'answer': 0.5, 'stepwise': 0.2}


def r_format(traj) -> float:
    """① K / T：通过格式检查的轮数 / 总轮数。"""
    turns = traj.agent_turns()
    if not turns:
        return 0.0
    ok = sum(1 for t in turns if t.format_ok or check_step_format(t).ok)
    return ok / len(turns)


def align_gaps(plan, exec_, window: int) -> tuple:
    """按工具名与兼容参数，在 `window` 轮内对齐计划与执行。

    返回 (Plan_ue, Exec_up)：
      · `Plan_ue` —— 没被执行的计划（**空想**）
      · `Exec_up` —— 没有对应计划的执行（**未经规划的蛮干**）

    对齐口径是论文说的"按名称与兼容参数在若干轮的窗口内"：
    工具名相同、且参数字典相等（或其一为空视为兼容）即算对齐。
    """
    window = max(int(window), 1)
    unused_plan = list(plan)
    unmatched_exec = []
    for idx, e in enumerate(exec_):
        hit = None
        for j, p in enumerate(unused_plan):
            if _compatible(p, e) and abs(idx - p.get('_index', idx)) <= window:
                hit = j
                break
        if hit is None:
            unmatched_exec.append(e)
        else:
            unused_plan.pop(hit)
    return unused_plan, unmatched_exec


def _compatible(plan, execution) -> bool:
    """工具名相同且参数兼容。空的计划参数视为"兼容任何参数"。"""
    if plan.get('tool') != execution.get('tool'):
        return False
    pp, ep = plan.get('params') or {}, execution.get('params') or {}
    if not pp or not ep:
        return True
    return pp == ep


def r_tool(traj) -> float:
    """② 1 − (|Plan_ue| + |Exec_up|) / (|Plan| + |Exec|)，截断到 [0,1]。

    **同时惩罚空想式调用与未经规划的执行**（论文原话）。
    既没规划也没执行时给 0：什么都不做不该拿满分。
    """
    steps = traj.agent_turns()
    plan, executions = [], []
    for i, s in enumerate(steps):
        if getattr(s, 'plan_tool', ''):
            plan.append({'tool': s.plan_tool, 'params': getattr(s, 'plan_params', None) or {},
                         '_index': i})
        if s.executed:
            executions.append({'tool': s.action.tool, 'params': s.action.params or {},
                               '_index': i})
    if not plan and not executions:
        return 0.0
    ue, up = align_gaps(plan, executions,
                         window=appconf.get('OAM_REWARD_ALIGN_WINDOW'))
    total = len(plan) + len(executions)
    if total == 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (len(ue) + len(up)) / total))


def r_answer(traj, case, executor=None) -> float:
    """③ 直接取验证器结果 H(Ŷ;Σ) ∈ {0,1}，**不做任何软化**。

    证据不足的合法终止记 0 —— 它不是成功。但它也不算"答错"，
    那是幻觉率那一栏的事，不在这里重复惩罚，否则会二次逼压策略强行归类。
    """
    return float(verify(case, traj.report, executor=executor).passed)


def compute(traj, case, belief_before=None, belief_after=None,
            tool_calls_last_turn: int = 0, executor=None) -> tuple:
    """算四分量与总分。返回 (total: float, parts: dict)。"""
    parts = {
        'format': r_format(traj),
        'tool': r_tool(traj),
        'answer': r_answer(traj, case, executor=executor),
    }
    if belief_before and belief_after:
        parts['stepwise'] = step_reward(belief_before, belief_after,
                                        tool_calls_this_turn=tool_calls_last_turn)
    else:
        parts['stepwise'] = 0.0

    total = sum(WEIGHTS[k] * float(v) for k, v in parts.items())
    parts['weights'] = dict(WEIGHTS)
    return round(max(0.0, min(1.0, total)), 6), parts


__all__ = ['WEIGHTS', 'r_format', 'r_tool', 'r_answer', 'compute', 'align_gaps']
