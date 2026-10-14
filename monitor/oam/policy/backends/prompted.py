# -*- coding: utf-8 -*-
"""`prompted` 策略后端：提示词驱动的 Think-Act-Observe（设计 §3.2）。

这是 P0 唯一落地的策略后端。它对应论文的 ReAct 基线，但**不是裸 ReAct**：
论文实测 MMA 让 ReAct-8B 成功率从 11.8% 提到 22.9%，而整理后 KB 带来的
提升更大（成功率 84.5% vs 原始文档 29.2%，幻觉率 4.6% vs 35.5%）。
本项目这两样都在 L2/L3 里，所以 prompted 后端的底子比裸 ReAct 强。

## 为什么后端要可替换

设计 §3.2 的判断：论文的 RL 训练需要 8 块昇腾 910B3 + VeRL + Qwen3-8B。
本项目是可自托管的产品，绝大多数部署方拿不出这个算力。
**如果把 RL 当成架构前提，这份设计对绝大多数部署就是不可用的。**
所以策略是接口，prompted/sft/rl 是实现，P0/P1 的部署没有 P2 也完整可用。

## 提示词里三条硬约束

1. **"证据不足"是合法输出**。不给这句话，模型就会被格式逼着硬编一个根因 ——
   那正是 REV-10（无条件追加 0.88 置信度结论）的成因。
2. **根因只能从该方言的封闭标签空间里选**，且工具只能从该方言的工具目录里选。
   方言隔离在这里再兜一次底（ToolProxy 是第一道）。
3. **不许执行变更**。即便策略想调 `fix.*`，也只会拿到 `MUTATION_FORBIDDEN`；
   提示词里提前说清楚，省一轮无用往返。
"""
import logging

from monitor.oam.labels import labels_for
from monitor.oam.policy.action_space import STATUS_EVIDENCE_INSUFFICIENT
from monitor.oam.tool_catalog import mutating_tools

logger = logging.getLogger('monitor.oam')

POLICY_VERSION = 'prompted-v1'

SYSTEM_TMPL = """你是数据库运维诊断智能体，服务于 {db_type} 数据库。

## 输出协议（严格遵守，每轮只输出一个动作）
思考：
<think>你要验证什么、打算怎么验证。工具调用前必须有 think 块。</think>
调用工具：
<tool_call>{{"plan": "计划调的工具名", "tool": "工具名", "params": {{}}}}</tool_call>
或给出最终报告：
<answer>{{"root_cause": "...", "fix": "...", "evidence_refs": [], "status": ""}}</answer>

## 可用工具（仅限本方言）
{catalog}

## 可用根因标签（仅限本方言的封闭标签空间）
{labels}

## 三条硬约束
1. **根因必须从上面的标签空间里原样选一个**，不要自造措辞、不要输出解释性文字。
2. **证据不足是合法输出**。当快照里没有支持判断的信号时，你**应当**输出
   <answer>{{"status": "{insufficient}", "reason": "缺什么信号"}}</answer>。
   这不算失败，比编一个错的根因好得多。**严禁**在无证据时硬给一个根因。
3. **只诊断，不变更**。你没有、也不需要任何执行变更的工具。

## 当前任务
症状：{symptom}
"""


class PromptedPolicy:
    """提示词策略后端。依赖既有 `monitor.llm` 的 provider 与路由。"""

    backend = 'prompted'
    version = POLICY_VERSION

    def __init__(self, db_type: str, incident_id: str = ''):
        self.db_type = db_type
        self.incident_id = incident_id
        self._messages = None

    # ------------------------------------------------------------------
    def build_messages(self, state) -> list:
        from monitor.oam.tool_catalog import catalog_for_prompt
        label_ids = [lb.label_id for lb in labels_for(state.db_type)]
        system = SYSTEM_TMPL.format(
            db_type=state.db_type,
            catalog=catalog_for_prompt(state.db_type),
            labels='\n'.join(f'- {lid}' for lid in label_ids) or '(该方言暂无标签)',
            insufficient=STATUS_EVIDENCE_INSUFFICIENT,
            symptom=state.symptom,
        )
        return [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': '请开始第 1 轮诊断。'},
        ]

    def step(self, state, turn: int = 1) -> str:
        """产出一轮的原始文本输出。"""
        if self._messages is None:
            self._messages = self.build_messages(state)
        elif turn > 1:
            self._messages.append({'role': 'user', 'content': '请继续第 %d 轮。' % turn})

        # 把上一轮的动作与观测回喂给模型
        if state.history:
            last = state.history[-1]
            obs = last.get('observation') or ''
            err = last.get('error_code') or ''
            head = f'第 {last["turn"]} 轮' + (f'（工具返回错误码 {err}）' if err else '')
            self._messages.append({'role': 'assistant',
                                   'content': f'<think>{last.get("thought", "")}</think>'})
            self._messages.append(
                {'role': 'user',
                 'content': f'{head} 观测：\n{obs}\n\n请基于该观测决定下一步。'})

        from monitor.llm.providers import get_chat_provider
        provider = get_chat_provider()
        res = provider.chat(self._messages, scene='agent',
                            incident_id=self.incident_id)
        return res.content


def forbidden_tools_note(db_type: str) -> str:
    """变更类工具清单，用于把"这些你调不了"写进提示词，省一轮无用往返。"""
    names = [t.name for t in mutating_tools(db_type)]
    return ('变更类工具（不可调用）：' + '、'.join(names)) if names else ''


__all__ = ['PromptedPolicy', 'POLICY_VERSION', 'SYSTEM_TMPL']
