# -*- coding: utf-8 -*-
"""根因匹配（论文 §3.3）。

论文原文口径：*"根因匹配（轻度归一化后精确字符串相等）"*，
括号里点明归一化内容是 *lowercasing、空白与标点清理*。

**明确不做的事**：不做语义相似度、不做同义词扩展、不做"看起来差不多就算对"。
原因有两条，都不是风格问题：

1. 论文紧接着就说这意味着"标签空间必须是枚举，不能是自由文本"。
   一旦验证器容忍语义相似，`oam/labels.py` 的封闭标签空间就形同虚设，
   整个度量地基随之失效。
2. 验证器必须**确定性、可复现**（设计 §8 第一条验收项："同一 case_id +
   policy_version 重跑结果一致"）。相似度匹配引入模型或浮点阈值后就不再确定。
"""
from monitor.oam.labels import LABELS, is_valid_label
from monitor.oam.verifier.normalize import normalize_cause


def match_cause(predicted: str, expected: str, db_type: str) -> tuple:
    """返回 (matched: bool, reason: str)。

    三步：
      1. 轻度归一化；
      2. **先查标签合法性** —— 预测值不在该方言的封闭标签空间内时直接判否。
         这条不是形式主义：§9.2 的 REV-10 教训是"把 Oracle 专有标签发给 MySQL"，
         标签空间就是拦它的闸门。
      3. 归一化后精确相等。
    """
    pred = normalize_cause(predicted)
    exp = normalize_cause(expected)

    if not pred:
        return False, '未给出根因'
    if not is_valid_label(pred, db_type):
        return False, (f'根因 {predicted!r} 不在方言 {db_type} 的封闭标签空间内'
                       f'（已归一化为 {pred!r}）')
    if not is_valid_label(exp, db_type):
        # 标注本身越界 = 基准集有问题，这是要修数据，不是判策略错。
        return False, f'标注真值 {expected!r} 不在方言 {db_type} 的标签空间内（基准集有误）'

    if pred != exp:
        near = _closest(pred, LABELS.keys())
        hint = f'；形近标签：{near}' if near else ''
        return False, f'根因不匹配：预测 {pred} ≠ 真值 {exp}{hint}'
    return True, '根因匹配'


def _closest(pred: str, candidates) -> str:
    """给一条形近提示。仅用于失败理由的可读性，不参与判定。"""
    best, best_d = '', 10 ** 9
    for c in candidates:
        d = _edit_distance(pred, c.lower())
        if d < best_d:
            best, best_d = c, d
    return best if best_d <= max(2, len(pred) // 3) else ''


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein 距离（两行动态规划，纯 Python）。仅用于失败提示。"""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


__all__ = ['match_cause']
