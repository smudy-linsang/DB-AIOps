# -*- coding: utf-8 -*-
"""单步奖励：贝叶斯信念的熵下降（论文 §5.2 奖励分量④）。

原文公式（逐字核对）：

```
r_stepwise⁽ᵗ⁾ = max{ 0 , ( Ent(P_t) − Ent(P_{t+1}) ) / log|C_k| }
   Ent(P) = − Σ_{c∈C_k} P(c) log P(c)
   P_t(c) = q( c | φ(V_t) )，由在历史工单上训练的贝叶斯网络给出
   φ(·) 编码结构化证据：KPI 异常、计划告警、WLM 检查
   若一次决策触发超过 L_tool 次工具调用，则该轮 r_stepwise⁽ᵗ⁾ = 0（防过度探查）
```

## 本项目的保留意见（论文自己承认的偏差，本项目会更严重）

论文自己点出贝叶斯信号带**历史频率偏差** —— 稀有或新出现的根因先验偏低，
熵下降会偏向那些确认常见诊断的动作。所以论文把它降级为**辅助塑形项**，
终止成败仍由基于规则、基于证据的验证器决定。

**本项目的语料规模远小于 DWS，这个偏差会更严重。** 对策与论文一致但要更克制：

1. 单步奖励只作辅助塑形项（权重 0.2），终止成败**必须**由验证器决定；
2. 根因先验不要直接用历史频率；对出现次数低于阈值的根因做**加性平滑**
   （`smooth_belief`，系数取 `OAM_PRIOR_SMOOTHING`）；
3. 生产中低置信或证据冲突一律转人工，**不强行归类**。

另有一处本项目特有的加固：低频根因**不参与归一化分母的 |C_k|**。
理由是若把只出现一两次的罕见根因留在分母里，`log|C_k|` 被撑大，
每个常见动作的熵下降都会被稀释到接近 0，策略学到的就是"什么都别做"。
"""
import logging
import math

from monitor import appconf

logger = logging.getLogger('monitor.oam')

# 低于该计数视为低频根因，加性平滑后才允许进入信念分布
LOW_FREQUENCY_THRESHOLD = 3


def entropy(belief: dict) -> float:
    """香农熵。信念未归一化时先归一化 —— 熵的定义域是概率分布。"""
    total = float(sum(belief.values()))
    if total <= 0:
        return 0.0
    out = 0.0
    for v in belief.values():
        p = float(v) / total
        if p > 0:
            out -= p * math.log(p)
    return out


def smooth_belief(counts: dict, alpha: float | None = None) -> dict:
    """按出现次数构造带加性平滑的先验（§9.3 对策②）。

    `alpha` 取 `OAM_PRIOR_SMOOTHING`（默认 1.0）。低频根因因此不会被压到接近 0，
    熵下降也就不会系统性忽略罕见故障。
    """
    alpha = appconf.get('OAM_PRIOR_SMOOTHING') if alpha is None else alpha
    return {k: float(v) + alpha for k, v in (counts or {}).items()}


def eligible_label_space(counts: dict, exclude_below: int = LOW_FREQUENCY_THRESHOLD) -> set:
    """参与熵归一化的根因集合 |C_k|：剔除低频根因。

    见模块 docstring 末段：把只出现一两次的罕见根因留在分母里会稀释所有
    熵下降，策略会学成"什么都不做"。
    """
    return {k for k, v in (counts or {}).items() if float(v) >= exclude_below}


def step_reward(belief_before: dict, belief_after: dict,
                tool_calls_this_turn: int,
                over_probe_threshold: int | None = None) -> float:
    """归一化熵下降；单轮工具调用数超阈值则置零（论文的 L_tool 规则）。

    `L_tool` 的取值论文原文**没有给出**，故默认取 `OAM_REWARD_OVER_PROBE`
    （登记在 appconf.SPECS，设计 §4.6 要求"一律先登记再给默认值"）。
    """
    threshold = (appconf.get('OAM_REWARD_OVER_PROBE')
                 if over_probe_threshold is None else over_probe_threshold)
    if tool_calls_this_turn > threshold:
        return 0.0        # 原文的 L_tool 置零规则：防过度探查
    n = len(belief_before or {})
    if n <= 1:
        return 0.0
    drop = entropy(belief_before) - entropy(belief_after or belief_before)
    return max(0.0, drop / math.log(n))


def belief_from_evidence(evidence: dict, priors: dict, likelihood: dict) -> dict:
    """P(c | φ(V)) —— 由结构化证据算后验。

    论文 φ(·) 编码的是：KPI 异常、计划告警、WLM 检查。这里用最朴素的
    朴素贝叶斯：把每条证据命中与否当作对数似然累加，再叠加带平滑的先验。

    刻意保持简单：这一层只是**塑形信号**，不参与成败判定
    （论文 §5.2 的保留意见），复杂化它只会引入更多不可解释的偏差。
    """
    post = dict(priors or {})
    for cause in post:
        score = 0.0
        for signal, hit in (evidence or {}).items():
            table = (likelihood or {}).get(signal) or {}
            score += math.log(max(float(table.get('hit' if hit else 'miss', 0.5)), 1e-6))
        post[cause] = math.exp(score) * float(post[cause])
    total = sum(post.values())
    if total <= 0:
        return dict(priors or {})
    return {k: v / total for k, v in post.items()}


__all__ = ['entropy', 'step_reward', 'smooth_belief', 'eligible_label_space',
           'belief_from_evidence', 'LOW_FREQUENCY_THRESHOLD']
