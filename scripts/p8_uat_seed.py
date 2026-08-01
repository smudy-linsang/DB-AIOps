# -*- coding: utf-8 -*-
"""UAT 场景铺底: 构造带完整 Phase 8 AI 产物的演示事故 (INC-UAT8-001)。
运行: ./venv/bin/python scripts/p8_uat_seed.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django

django.setup()

from django.utils import timezone

from monitor.models import (AgentTrace, DatabaseConfig, Incident, LLMCallLog,
                            RcaFeedback, RuleStat)

cfg = DatabaseConfig.objects.filter(is_active=True).first() or DatabaseConfig.objects.first()
print(f"使用数据库配置: {cfg.id} {cfg.name}")

Incident.objects.filter(incident_id='INC-UAT8-001').delete()
inc = Incident.objects.create(
    incident_id='INC-UAT8-001', config=cfg, db_type=cfg.db_type,
    category='performance', title='UAT演示: CPU 飙高伴随活跃会话堆积',
    priority='P2', status='plan_ready', dedup_key='uat8-demo',
    occurred_at=timezone.now() - timezone.timedelta(minutes=30),
    detected_at=timezone.now() - timezone.timedelta(minutes=28))

# --- 诊断 (含 LLM 融合产物) ---
from monitor.diagnosis_pipeline import run_diagnosis

run_diagnosis(inc.incident_id)
inc.refresh_from_db()
root_causes = inc.rca_result.get('root_causes') or []
if not root_causes:
    root_causes = [{
        'rule_id': 'GEN_PERF_DEGRADED', 'name': '整体性能退化', 'domain': 'performance',
        'confidence': 0.72, 'evidence': [{'kind': 'metric', 'desc': 'cpu_usage 94% (基线 35%)'}],
        'suggestions': ['检查 TOP SQL', '确认是否有批处理任务'],
    }]
root_causes[0].update({
    'source': 'both', 'agent_confirmed': True,
    'reasoning': 'AI 研判: 结合 ASH 时间线, CPU 飙高与 10:02 上线的报表 SQL 高度相关; '
                 '执行计划走了全表扫描, 与规则引擎结论互相印证。',
})
root_causes.append({
    'rule_id': '', 'name': '连接池配置偏小放大了排队 (AI 新假设)', 'domain': 'performance',
    'confidence': 0.55, 'source': 'llm', 'verdict_on_rule': 'new',
    'reasoning': 'AI 研判: max_connections=200 而应用池上限 400, 高峰期新建连接被拒, '
                 '重试风暴进一步抬升 CPU。建议核对应用侧连接池配置。',
    'evidence': [], 'suggestions': ['将应用池上限与 max_connections 对齐'],
})

rca = inc.rca_result
rca['engine'] = 'rca_v2+llm'
rca['root_causes'] = root_causes
rca['llm'] = {
    'summary': 'AI 综合分析: 本次事故主因是 10:02 上线的报表 SQL 走全表扫描导致 CPU 飙高, '
               '活跃会话随之堆积; 连接池配置偏小放大了排队效应。建议先限流/杀掉报表会话, '
               '再补建索引, 最后核对连接池参数。',
    'need_more_evidence': ['报表 SQL 的执行计划详情', '应用侧连接池配置'],
    'meta': {'model': 'qwen2.5:14b-instruct', 'latency_ms': 2350},
}

# --- Agent 排查轨迹 (done) ---
AgentTrace.objects.filter(incident=inc).delete()
trace = AgentTrace.objects.create(
    trace_id=f"AGT-UAT8-{inc.id}", incident=inc, status='done', triggered_by='admin',
    started_at=timezone.now() - timezone.timedelta(minutes=10),
    finished_at=timezone.now() - timezone.timedelta(minutes=9),
    steps=[
        {'step': 1, 'thought': '先看当前活跃会话分布, 确认是否有集中等待。',
         'action': 'get_active_sessions', 'params': {'limit': 20},
         'observation': '{"active": 187, "top_wait": "cpu", "top_sql_id": "rep_daily_full"}',
         'elapsed_ms': 812},
        {'step': 2, 'thought': '定位 top SQL 的执行计划, 验证全表扫描假设。',
         'action': 'explain_sql', 'params': {'sql_id': 'rep_daily_full'},
         'observation': '{"plan": "FULL TABLE SCAN orders (rows=42000000)", "cost": 986432}',
         'elapsed_ms': 640},
        {'step': 3, 'thought': '证据充分, 给出结论。', 'action': 'final', 'params': {},
         'observation': '', 'elapsed_ms': 305},
    ],
    conclusion={
        'root_cause': '报表 SQL rep_daily_full 全表扫描 42M 行导致 CPU 饱和',
        'confidence': 0.86,
        'evidence_summary': '187 活跃会话集中在 CPU 等待; top SQL 执行计划为全表扫描, 代价 986k。',
        'suggestions': ['为 orders(report_date) 补建索引', '报表任务错峰到低峰期', '为报表账号设置资源组限流'],
    })
rca['agent'] = {
    'trace_id': trace.trace_id,
    'root_cause': trace.conclusion['root_cause'],
    'confidence': 0.86,
    'evidence_summary': trace.conclusion['evidence_summary'],
    'suggestions': trace.conclusion['suggestions'],
    'finished_at': timezone.now().isoformat(),
}
inc.rca_result = rca

# --- 方案 (含 llm_advisory 建议型方案) ---
from monitor.remediation_planner import generate_incident_plans

inc.plans = generate_incident_plans(inc, root_causes, plan_draft={
    'title': '报表 SQL 治理三步走',
    'steps': [
        {'desc': '限流或暂停 rep_daily_full 报表任务, 释放 CPU', 'risk': 'low'},
        {'desc': '为 orders(report_date) 建立二级索引, 消除全表扫描',
         'sql_hint': 'CREATE INDEX idx_orders_report_date ON orders(report_date);', 'risk': 'mid'},
        {'desc': '应用连接池上限与 max_connections 对齐, 避免重试风暴', 'risk': 'low'},
    ],
    'caution': '建索引请在低峰期执行并评估磁盘空间; 本方案仅供参考, 需 DBA 人工确认。',
})
inc.save(update_fields=['rca_result', 'plans', 'updated_at'])

# --- AI 运营数据: LLM 调用日志 + 规则准确率 ---
LLMCallLog.objects.filter(incident_id='INC-UAT8-001').delete()
for scene, status, lat, tin, tout in [
        ('diagnosis', 'ok', 2350, 3800, 720), ('agent', 'ok', 812, 2100, 260),
        ('agent', 'ok', 640, 2600, 240), ('distill', 'ok', 1500, 1900, 380),
        ('diagnosis', 'timeout', 25000, 3800, 0)]:
    LLMCallLog.objects.create(scene=scene, status=status, model='qwen2.5:14b-instruct',
                              incident_id='INC-UAT8-001', latency_ms=lat,
                              prompt_tokens=tin, completion_tokens=tout)
top_rule = root_causes[0].get('rule_id') or 'GEN_PERF_DEGRADED'
RuleStat.objects.update_or_create(rule_id=top_rule, defaults={
    'hit_count': 12, 'correct_count': 9, 'wrong_count': 3,
    'accuracy': 0.75, 'calibrated_base': 0.705})
RuleStat.objects.update_or_create(rule_id='MYSQL_LOCK_CHAIN', defaults={
    'hit_count': 8, 'correct_count': 7, 'wrong_count': 1,
    'accuracy': 0.875, 'calibrated_base': 0.793})
RcaFeedback.objects.filter(incident=inc).delete()

print(f"UAT 事故就绪: {inc.incident_id} 根因={len(root_causes)} 方案={len(inc.plans)} "
      f"trace={trace.trace_id}")
