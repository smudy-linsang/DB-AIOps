# -*- coding: utf-8 -*-
"""
Phase 8C: Agentic 主动排查循环 (phase8/20 §15)。

investigate(incident): 纯提示词协议的 思考→行动→观察 循环:
1. 每轮 LLM 输出一个 JSON step (validate_agent_step 白名单校验);
2. run_tool 执行只读工具, 观察截断后回喂;
3. 防重复: 同工具+同参数拒绝执行, 提示模型换思路;
4. 硬约束: AGENT_MAX_STEPS 步数上限 + AGENT_BUDGET_SEC 时间预算,
   耗尽时强制要求 final; 全轨迹落 AgentTrace。
结论仅写回 incident.rca_result['agent'] 供参考, 不自动执行任何动作。
"""
import json
import logging
import time

from django.conf import settings
from django.utils import timezone

from monitor.llm import agent_enabled
from monitor.llm.prompts import (AGENT_OBSERVATION_TMPL, AGENT_SYSTEM_TMPL,
                                 AGENT_USER_FIRST_TMPL)
from monitor.llm.providers import LLMError, get_chat_provider
from monitor.llm.schemas import SchemaValidationError, validate_agent_step
from monitor.llm.tools import TOOL_REGISTRY, run_tool, tools_description

logger = logging.getLogger("monitor.llm")

FORCE_FINAL_MSG = ("步数/预算即将耗尽。你必须立即输出 action=final 的 JSON, "
                   "基于已有观察给出结论 (证据不足时如实给低置信度)。")


def _incident_brief(incident) -> str:
    ev0 = incident.events.first() if incident.events.exists() else None
    return json.dumps({
        'incident_id': incident.incident_id, 'title': incident.title,
        'db_type': incident.db_type, 'category': incident.category,
        'priority': incident.priority, 'status': incident.status,
        'first_signal': getattr(ev0, 'signal', ''),
        'first_metric': f"{getattr(ev0, 'metric_key', '')}={getattr(ev0, 'value', '')}",
    }, ensure_ascii=False, default=str)


def _existing_rca(incident) -> str:
    roots = (incident.rca_result or {}).get('root_causes') or []
    return json.dumps([{
        'rule_id': r.get('rule_id'), 'name': r.get('name'),
        'confidence': r.get('confidence'), 'summary': (r.get('summary') or '')[:200],
    } for r in roots[:3]], ensure_ascii=False, default=str) or '(无)'


def investigate(incident, triggered_by: str = 'system'):
    """对事故发起一次 Agent 排查。返回 AgentTrace 实例; 关闭时返回 None。"""
    if not agent_enabled():
        logger.info("[agent] AGENT_ENABLED=False, 跳过排查")
        return None
    from monitor.models import AgentTrace

    max_steps = int(getattr(settings, 'AGENT_MAX_STEPS', 6))
    budget = int(getattr(settings, 'AGENT_BUDGET_SEC', 120))
    config = incident.config
    trace = AgentTrace.objects.create(
        trace_id=f"AGT-{timezone.now().strftime('%Y%m%d%H%M%S')}-{incident.id}",
        incident=incident, status='running', triggered_by=triggered_by)

    provider = get_chat_provider()
    messages = [
        {'role': 'system', 'content': AGENT_SYSTEM_TMPL.format(
            tools_desc=tools_description(), max_steps=max_steps)},
        {'role': 'user', 'content': AGENT_USER_FIRST_TMPL.format(
            incident_brief=_incident_brief(incident),
            existing_rca=_existing_rca(incident))},
    ]
    t0 = time.time()
    seen_calls = set()   # 防重复: (action, params_json)
    steps = []
    status, conclusion = 'failed', {}
    bad_streak = 0       # 连续非法输出计数, 2 次即终止

    try:
        for step_no in range(1, max_steps + 1):
            remain_sec = budget - (time.time() - t0)
            if remain_sec < 5:
                status = 'budget_exceeded'
                break
            forcing_final = (step_no == max_steps)
            if forcing_final:
                messages.append({'role': 'user', 'content': FORCE_FINAL_MSG})

            ts = time.time()
            res = provider.chat(messages, scene='agent',
                                incident_id=incident.incident_id,
                                timeout=int(min(remain_sec, provider.timeout)))
            try:
                step = validate_agent_step(res.content,
                                           allowed_actions=set(TOOL_REGISTRY))
                bad_streak = 0
            except SchemaValidationError as e:
                bad_streak += 1
                logger.warning("[agent] %s 第%d步输出非法: %s",
                               trace.trace_id, step_no, e)
                if bad_streak >= 2:
                    break
                messages.append({'role': 'assistant', 'content': res.content[:2000]})
                messages.append({'role': 'user',
                                 'content': f"输出不符合协议: {e}. 请重新只输出一个合法 JSON。"})
                continue

            messages.append({'role': 'assistant', 'content': res.content[:4000]})
            action, params = step['action'], step.get('params') or {}
            rec = {'step': step_no, 'thought': step.get('thought', ''),
                   'action': action, 'params': params}

            if action == 'final':
                conclusion = step.get('final') or {}
                rec['observation'] = ''
                rec['elapsed_ms'] = int((time.time() - ts) * 1000)
                steps.append(rec)
                status = 'done'
                break

            # 防重复调用: 同工具+同参数直接拒绝
            call_key = (action, json.dumps(params, sort_keys=True, default=str))
            if call_key in seen_calls:
                observation = json.dumps(
                    {'error': '重复调用被拒绝, 请换工具或参数, 或给出 final'},
                    ensure_ascii=False)
            else:
                seen_calls.add(call_key)
                observation = run_tool(action, params, config)

            rec['observation'] = observation
            rec['elapsed_ms'] = int((time.time() - ts) * 1000)
            steps.append(rec)
            messages.append({'role': 'user', 'content': AGENT_OBSERVATION_TMPL.format(
                step=step_no, action=action, observation=observation,
                remaining=max_steps - step_no)})
        else:
            status = 'budget_exceeded'  # 步数耗尽仍未 final
    except LLMError as e:
        logger.warning("[agent] %s LLM 调用失败: %s", trace.trace_id, e)
        status = 'failed'
        conclusion = {'error': str(e)}
    except Exception as e:
        logger.exception("[agent] %s 排查异常: %s", trace.trace_id, e)
        status = 'failed'
        conclusion = {'error': str(e)}

    trace.steps = steps
    trace.conclusion = conclusion
    trace.status = status
    trace.finished_at = timezone.now()
    trace.save(update_fields=['steps', 'conclusion', 'status', 'finished_at'])

    # 结论回写 rca_result (仅参考标记, 不覆盖既有诊断)
    if status == 'done' and conclusion:
        try:
            rca = incident.rca_result or {}
            rca['agent'] = {
                'trace_id': trace.trace_id,
                'root_cause': conclusion.get('root_cause', ''),
                'confidence': conclusion.get('confidence', 0),
                'evidence_summary': conclusion.get('evidence_summary', ''),
                'suggestions': conclusion.get('suggestions', []),
                'finished_at': timezone.now().isoformat(),
            }
            incident.rca_result = rca
            incident.save(update_fields=['rca_result', 'updated_at'])
        except Exception as e:
            logger.warning("[agent] %s 结论回写失败: %s", trace.trace_id, e)

    logger.info("[agent] %s 完成: status=%s steps=%d 耗时%.1fs",
                trace.trace_id, status, len(steps), time.time() - t0)
    return trace
