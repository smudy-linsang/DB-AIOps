# -*- coding: utf-8 -*-
"""
Phase 8 SIT 集成测试 (第2轮): 全链路 + 降级 + 回归。

场景:
  SIT-1 LLM 关闭时诊断全链路 (等同 Phase 7, 红线: 无 llm 字段)
  SIT-2 LLM 开启但服务不可达 → 诊断无损降级 + LLMCallLog 留痕
  SIT-3 Agent 开启但 LLM 不可达 → 排查优雅失败 (trace=failed)
  SIT-4 事故蒸馏闭环 (LLM 关 → 模板降级; 重复蒸馏跳过)
  SIT-5 反馈 → 校准闭环 (RcaFeedback → RuleStat → get_calibrated_base)
  SIT-6 变更流进入诊断上下文 (record_change → recent_changes)
  SIT-7 因果挖掘无数据时优雅返回
  SIT-8 向量检索关闭时词法降级 (search_cases_v2)

运行: ./venv/bin/python verify_phase8.py   (需 PostgreSQL, 会创建并清理测试数据)
"""
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.utils import timezone  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def make_incident(suffix, **extra):
    from monitor.models import Incident
    return Incident.objects.create(
        incident_id=f"INC-SIT8-{suffix}",
        config=CFG, db_type='mysql', category='performance',
        title=f"SIT 测试事故 {suffix}", priority='P2',
        dedup_key=f"sit8-{suffix}", occurred_at=timezone.now(), **extra)


def cleanup():
    from monitor.models import (AgentTrace, AlertCase, ChangeEvent, Incident,
                                LLMCallLog, RcaFeedback, RuleStat)
    Incident.objects.filter(incident_id__startswith='INC-SIT8-').delete()
    AlertCase.objects.filter(case_id__startswith='CASE-INC-INC-SIT8-').delete()
    RcaFeedback.objects.filter(rule_id__startswith='SIT8_').delete()
    RuleStat.objects.filter(rule_id__startswith='SIT8_').delete()
    ChangeEvent.objects.filter(title__startswith='SIT8').delete()
    LLMCallLog.objects.filter(incident_id__startswith='INC-SIT8-').delete()
    AgentTrace.objects.filter(trace_id__contains='SIT8').delete()
    from monitor.models import DatabaseConfig
    DatabaseConfig.objects.filter(name='sit8-mysql').delete()


# ---------------------------------------------------------------------------
from monitor.models import DatabaseConfig  # noqa: E402

cleanup()
CFG = DatabaseConfig.objects.create(
    name='sit8-mysql', db_type='mysql', host='127.0.0.1', port=1,
    username='sit', password='sit', is_active=False)

print("== SIT-1 LLM 关闭时诊断全链路 ==")
settings.LLM_ENABLED = False
inc1 = make_incident('001')
from monitor.diagnosis_pipeline import run_diagnosis  # noqa: E402

r1 = run_diagnosis(inc1.incident_id)
inc1.refresh_from_db()
check("诊断完成且落库", bool(inc1.rca_result), str(r1))
check("engine 为 rca_v2+signal (无LLM)",
      inc1.rca_result.get('engine') == 'rca_v2+signal',
      inc1.rca_result.get('engine'))
check("rca_result 不含 llm 字段", 'llm' not in inc1.rca_result)
check("生成了处置方案", isinstance(inc1.plans, list))
check("无 llm_advisory 方案混入",
      all(p.get('scenario') != 'llm_advisory' for p in (inc1.plans or [])))

print("== SIT-2 LLM 开启但服务不可达 → 无损降级 ==")
from monitor.llm.providers import reset_providers  # noqa: E402
from monitor.models import LLMCallLog  # noqa: E402

settings.LLM_ENABLED = True
settings.LLM_BASE_URL = 'http://127.0.0.1:9/v1'   # 不可达端口
settings.LLM_TIMEOUT_SEC = 3
reset_providers()
before = LLMCallLog.objects.count()
inc2 = make_incident('002')
r2 = run_diagnosis(inc2.incident_id)
inc2.refresh_from_db()
check("诊断仍完成 (降级不阻断)", bool(inc2.rca_result), str(r2))
check("降级后 engine 仍为 rca_v2+signal",
      inc2.rca_result.get('engine') == 'rca_v2+signal',
      inc2.rca_result.get('engine'))
bad_logs = LLMCallLog.objects.filter(incident_id=inc2.incident_id).exclude(status='ok')
check("LLMCallLog 留痕失败调用", LLMCallLog.objects.count() > before and bad_logs.exists(),
      f"count={LLMCallLog.objects.count()} before={before}")

print("== SIT-3 Agent 开启但 LLM 不可达 → 优雅失败 ==")
settings.AGENT_ENABLED = True
from monitor.llm.agent import investigate  # noqa: E402

trace = investigate(inc2, triggered_by='sit8')
check("investigate 返回 trace 而非抛异常", trace is not None)
check("trace 状态为 failed", getattr(trace, 'status', '') == 'failed',
      getattr(trace, 'status', ''))
check("trace 结论含 error", 'error' in (getattr(trace, 'conclusion', {}) or {}))
settings.AGENT_ENABLED = False
settings.LLM_ENABLED = False
reset_providers()

print("== SIT-4 事故蒸馏闭环 (模板降级 + 幂等) ==")
from monitor.case_distiller import distill_incident  # noqa: E402
from monitor.models import AlertCase  # noqa: E402

inc1.transition('resolved', 'sit8', '测试解决')
case_id = distill_incident(inc1)
check("蒸馏产出案例", case_id == f"CASE-INC-{inc1.incident_id}", str(case_id))
case = AlertCase.objects.filter(case_id=case_id).first()
check("案例 source=distilled", case and case.source == 'distilled')
check("案例记录来源事故", case and case.source_incident == inc1.incident_id)
check("重复蒸馏被跳过", distill_incident(inc1) is None)

print("== SIT-5 反馈 → 校准闭环 ==")
from monitor.models import RcaFeedback, RuleStat  # noqa: E402
from monitor.rule_calibrator import get_calibrated_base, recompute_rule_stats  # noqa: E402

for i, v in enumerate(['correct'] * 4 + ['wrong', 'partial']):
    RcaFeedback.objects.create(incident=inc1, rule_id='SIT8_R1',
                               user=f'sit8_u{i}', verdict=v, source='rule')
recompute_rule_stats()
stat = RuleStat.objects.filter(rule_id='SIT8_R1').first()
check("RuleStat 已生成", stat is not None)
check("准确率=0.75 (partial 半票)", stat and abs(stat.accuracy - 0.75) < 0.001,
      stat and stat.accuracy)
check("校准分=0.705", stat and abs(stat.calibrated_base - 0.705) < 0.001,
      stat and stat.calibrated_base)
check("RCA 接入点读到校准分", abs(get_calibrated_base('SIT8_R1') - 0.705) < 0.001)

print("== SIT-6 变更流进入诊断上下文 ==")
from monitor.change_stream import record_change  # noqa: E402
from monitor.context_aggregator import build_incident_context  # noqa: E402

record_change(CFG, 'manual', 'SIT8 调整参数', change_type='param_change', operator='sit8')
ctx = build_incident_context(inc1)
changes = ctx.get('recent_changes') or []
check("上下文 recent_changes 含登记的变更",
      any('SIT8' in str(c) for c in changes), f"n={len(changes)}")

print("== SIT-7 因果挖掘无数据时优雅返回 ==")
from monitor.causal_miner import mine_config  # noqa: E402

try:
    rm = mine_config(CFG)
    check("mine_config 优雅返回", isinstance(rm, dict), str(rm))
except Exception as e:
    check("mine_config 优雅返回", False, str(e))

print("== SIT-8 向量检索关闭时词法降级 ==")
settings.EMBED_ENABLED = False
from monitor.case_rag_v2 import search_cases_v2  # noqa: E402

try:
    hits = search_cases_v2('CPU 使用率高 慢查询', db_type='mysql')
    check("search_cases_v2 词法降级不抛异常", isinstance(hits, list), type(hits))
except Exception as e:
    check("search_cases_v2 词法降级不抛异常", False, str(e))

# ---------------------------------------------------------------------------
cleanup()
print("=" * 50)
print(f"SIT 结果: PASS={len(PASS)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项:", FAIL)
sys.exit(1 if FAIL else 0)
