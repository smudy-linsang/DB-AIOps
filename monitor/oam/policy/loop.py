# -*- coding: utf-8 -*-
"""L4 统一 Think-Act-Observe 循环（论文 §5.1 的运行循环，设计 §4.4）。

论文原文算法：*"从症状出发初始化诊断状态；策略每轮生成思考并选择执行
KB 检索 / 指标或计划或配置或日志类工具 / 直接给出答案；一旦生成答案就返回
最终报告，否则由 MMA 把环境原始结果概括为观测并追加到诊断状态；
循环在超时或触发升级报告时结束。"*

## 三条不变量

1. **每一步要么格式良好，要么确定性错误**。工具失败不会静默 —— 它带
   `error_code` 回喂给策略，让模型知道换个工具还是换参数。
2. **变更类工具永远拿不到**。它们在 `ToolProxy` 层被拦，即使策略点了也会
   得到 `MUTATION_FORBIDDEN`。
3. **证据不足是合法终止**。循环到轮数上限、观测全空、或策略自己判断
   证据不足，都走 `escalate_report()` 而不是强行给一个根因。

## 策略后端可替换（设计 §3.2 的关键工程改写）

论文的 RL 训练需要 8 块昇腾 910B3 + VeRL；本项目是可自托管的产品，
绝大多数部署方拿不出这个算力。所以**策略后端可替换，其余四层完全不变**：
    prompted —— 只需 LLM API，对应论文的 ReAct 基线，但因为有整理后 KB + MMA
               会显著好于裸 ReAct（论文实测 MMA 让 ReAct-8B 成功率 11.8%→22.9%）
    sft     —— 单卡可做；论文实测难任务仅 18%，聊胜于无
    rl      —— 多卡；论文实测难任务 68%

P0 只落地 `prompted`。sft/rl 留接口不留实现，**部署方在没有 P2 的情况下
必须完整可用**（设计 §7 P2 的现实判断）。
"""
import logging

from django.utils import timezone

from monitor import appconf
from monitor.oam.format_protocol import check_step_format
from monitor.oam.policy.action_space import (
    DiagnosticState, escalate_report, parse_action,
)
from monitor.oam.types import Observation, Step, Trajectory

logger = logging.getLogger('monitor.oam')

MAX_BAD_STREAK = 2      # 连续非法输出达到该次数即终止，避免在坏输出上空转烧预算


def diagnose(case, policy, env, max_turns: int | None = None) -> Trajectory:
    """跑一次完整的诊断。返回 `Trajectory`（不落库，落库由调用方做）。"""
    max_turns = max_turns or appconf.get('OAM_MAX_TURNS')
    state = DiagnosticState(
        symptom=getattr(case, 'symptom', ''),
        db_type=getattr(case, 'db_type', ''),
        tool_catalog=env.catalog(),
        case_id=getattr(case, 'case_id', ''),
    )
    traj = Trajectory(case_id=getattr(case, 'case_id', ''),
                      db_type=getattr(case, 'db_type', ''),
                      policy_backend=getattr(policy, 'backend', 'prompted'),
                      policy_version=getattr(policy, 'version', 'v1'),
                      started_at=timezone.now())

    bad_streak = 0
    final_report = None

    for turn in range(1, max_turns + 1):
        try:
            raw_text = policy.step(state, turn=turn)
        except Exception as e:
            logger.warning('[oam] 策略第 %d 轮异常: %s', turn, e)
            traj.steps.append(Step(turn=turn, thought='',
                                  tool_error_code='POLICY_FAILED',
                                  tool_error_msg=f'{type(e).__name__}: {e}'))
            break

        step, err = parse_action(raw_text)
        if err:
            bad_streak += 1
            logger.warning('[oam] 第 %d 轮输出不合协议: %s', turn, err)
            traj.steps.append(Step(turn=turn, thought=raw_text[:2000],
                                   tool_error_code='BAD_PROTOCOL',
                                   tool_error_msg=err))
            state.append(raw_text[:2000], _error_action(err),
                         Observation(text=f'协议错误：{err}',
                                     evidence={'error_code': 'BAD_PROTOCOL', 'refs': []}))
            if bad_streak >= MAX_BAD_STREAK:
                logger.info('[oam] 连续 %d 轮协议错误，提前终止', bad_streak)
                break
            continue

        bad_streak = 0
        step.turn = turn

        # ---- 终止动作 ----
        if step.action.is_final:
            step.observation = None
            step.format_ok = check_step_format(step).ok
            traj.steps.append(step)
            final_report = step.action.report
            break

        # ---- 工具调用 ----
        result = env.call(step.action.tool, step.action.params)
        if not result.ok:
            step.tool_error_code = result.error_code
            step.tool_error_msg = result.error_msg
            step.observation = Observation(
                text=f'工具 {step.action.tool} 未执行：[{result.error_code}] {result.error_msg}',
                evidence={'error_code': result.error_code, 'tool': step.action.tool,
                          'refs': []})
        else:
            step.observation = _summarize(env, step.action.tool, result.data, state)

        step.format_ok = check_step_format(step).ok
        traj.steps.append(step)
        state.append(step.thought, step.action, step.observation)

    if final_report is None:
        # 轮数耗尽或连续坏输出：合法终止，转人工，不硬凑结论。
        final_report = escalate_report(
            f'超出最大轮数({max_turns})或连续协议错误，未能形成可判定结论，转交人工 DBA')

    traj.report = final_report
    traj.escalated = str(final_report.get('status') or '').lower() == 'evidence_insufficient'
    traj.finished_at = timezone.now()
    return traj


def _error_action(message: str):
    from monitor.oam.types import Action
    action = Action(tool='', params={})
    action.error_code = 'BAD_PROTOCOL'
    action.reason = message
    return action


# 需要走 MMA 凝练的信号类型；元数据与参数这类简单结果**跳过该层**
_MMA_KINDS = {'kb', 'os', 'db'}


def _summarize(env, tool_name: str, data, state) -> Observation:
    """把工具原始结果交给感知层凝练；元数据/参数类直接透传。

    设计 §4.2：*"元数据与参数这类简单结果跳过该层"*。
    跳过不是偷懒 —— MMA 是为"多模态原始数据→自然语言观测"设计的，
    给它一个已经自解释的参数字典只会平白多一层失真。
    """
    kind = (tool_name.split('.')[0] or '')
    payload = {'tool': tool_name, 'kind': kind, 'data': data}
    if kind not in _MMA_KINDS:
        # 元数据/配置类：原样透传，evidence 直接记工具名与参数
        return Observation(text=str(data)[:4000],
                           evidence={'tool': tool_name, 'refs': [f'tool:{tool_name}']})
    try:
        from monitor.oam.perception import summarize_tool_result
        return summarize_tool_result(tool_name, data, state)
    except Exception as e:
        # 感知层不可用时**如实降级**，不留空、不编造；并按 degrade 留痕。
        from monitor import degrade
        degrade.note(f'oam.mma.{tool_name}', '观测凝练失败，退回原始结果', e)
        return Observation(text=str(data)[:4000],
                           evidence={'tool': tool_name, 'degraded': True,
                                     'refs': [f'tool:{tool_name}']})


__all__ = ['diagnose', 'MAX_BAD_STREAK']
