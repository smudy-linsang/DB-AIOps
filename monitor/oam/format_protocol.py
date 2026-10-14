# -*- coding: utf-8 -*-
"""思考-行动-观察的标签协议与格式校验（论文原文的四个标签）。

论文 §5.2 明列四个标签：`<think>` / `<tool_call>` / `<tool_response>` / `<answer>`，
其中 `<tool_response>` 内是**环境生成的 token，训练时被损失掩码掩掉**。

格式奖励 `r_format` 的检查项（论文原文口径）：
*"通过格式检查的轮数 / 总轮数。格式检查举例：`<tool_call>` 之前必须有 `<think>`、
工具名与参数合法、标签正确闭合"*。

本模块被 L4 策略与 L1 奖励**共用**，所以放在这里而不是任一层里 ——
否则会出现策略按一套口径生成、奖励按另一套口径判分的漂移。
"""
from dataclasses import dataclass

THINK_OPEN, THINK_CLOSE = '<think>', '</think>'
CALL_OPEN, CALL_CLOSE = '<tool_call>', '</tool_call>'
RESP_OPEN, RESP_CLOSE = '<tool_response>', '</tool_response>'
ANSWER_OPEN, ANSWER_CLOSE = '<answer>', '</answer>'

TAGS = (THINK_OPEN, THINK_CLOSE, CALL_OPEN, CALL_CLOSE,
        RESP_OPEN, RESP_CLOSE, ANSWER_OPEN, ANSWER_CLOSE)


@dataclass
class FormatCheck:
    ok: bool
    reasons: tuple = ()

    def __bool__(self):
        return self.ok


def check_text_protocol(text: str) -> FormatCheck:
    """检查一轮输出的标签结构是否合法（闭合正确、成对出现）。"""
    reasons = []
    raw = text or ''
    for open_tag, close_tag in ((THINK_OPEN, THINK_CLOSE), (CALL_OPEN, CALL_CLOSE),
                                (RESP_OPEN, RESP_CLOSE), (ANSWER_OPEN, ANSWER_CLOSE)):
        n_open, n_close = raw.count(open_tag), raw.count(close_tag)
        if n_open != n_close:
            reasons.append(f'{open_tag} 出现 {n_open} 次但 {close_tag} 出现 {n_close} 次')
    if CALL_OPEN in raw and THINK_CLOSE not in raw:
        reasons.append('tool_call 之前缺少 think 块')
    if ANSWER_OPEN in raw and THINK_CLOSE not in raw:
        reasons.append('answer 之前缺少 think 块')
    return FormatCheck(not reasons, tuple(reasons))


def check_step_format(step) -> FormatCheck:
    """检查单个 Step 是否通过格式检查。`step` 是 `types.Step`。

    论文口径三项：
      1. `<tool_call>` 之前必须有 `<think>`；
      2. 工具名与参数合法；
      3. 标签正确闭合。

    第 1、3 项在"输出文本"上查，第 2 项在结构化字段上查 ——
    我们把策略输出解析成 Step 后，工具名/参数的合法性由 `ToolProxy` 与
    `ToolSpec.validate` 判定，这里查的是**有没有进到合法的调用结构**。
    """
    reasons = []
    text_checks = check_text_protocol(getattr(step, 'raw_text', '') or '')
    reasons.extend(text_checks.reasons)

    is_final = bool(getattr(step.action, 'final', False))
    if is_final:
        # 终止轮：不要求有 think 之外的任何工具调用，但必须有终止理由或报告
        if not (getattr(step.action, 'report', None) or getattr(step.action, 'reason', '')):
            reasons.append('终止轮既没有报告也没有终止理由')
    else:
        if not getattr(step.action, 'tool', ''):
            reasons.append('非终止轮缺少工具名')
        if not isinstance(getattr(step.action, 'params', None), dict):
            reasons.append('工具参数不是对象')
        if not (getattr(step, 'thought', '') or '').strip():
            reasons.append('工具调用前缺少思考')
    return FormatCheck(not reasons, tuple(reasons))


__all__ = ['FormatCheck', 'check_step_format', 'check_text_protocol', 'TAGS',
           'THINK_OPEN', 'THINK_CLOSE', 'CALL_OPEN', 'CALL_CLOSE',
           'RESP_OPEN', 'RESP_CLOSE', 'ANSWER_OPEN', 'ANSWER_CLOSE']
