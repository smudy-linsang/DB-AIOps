# -*- coding: utf-8 -*-
"""
Phase 6B: 诊断管道 (phase6/20 §1)。

run_diagnosis(incident_id): 五阶段编排, 落 rca_result/impact/plans + plan_ready_at
+ 状态 diagnosing→plan_ready, 随后触发通知。端到端预算 DIAG_BUDGET_SEC。
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("monitor.diagnosis")

# 信号 → 派生主根因 (保证每事故都有可用根因; RCA 规则命中则补充)
_SIGNAL_ROOT_CAUSE = {
    'blocked_session': {
        'rule_id': 'R021', 'name': '锁等待阻塞', 'domain': 'lock',
        'summary': '存在会话持锁未释放, 阻塞其他会话',
        'suggestions': ['定位并终止阻塞源会话(blocker)', '检查长事务未提交', '优化涉及锁的 SQL'],
    },
    'deadlock_surge': {
        'rule_id': 'R023', 'name': '死锁频发', 'domain': 'lock',
        'summary': '死锁计数快速增长', 'suggestions': ['分析死锁链热点对象', '统一事务加锁顺序'],
    },
    'conn_high': {
        'rule_id': 'R004', 'name': '连接数接近上限', 'domain': 'connection',
        'summary': '连接使用率高, 接近 max_connections',
        'suggestions': ['清理空闲会话', '排查连接来源 IP', '评估连接池配置'],
    },
    'conn_storm': {
        'rule_id': 'R002', 'name': '连接风暴', 'domain': 'connection',
        'summary': '连接数短时突增', 'suggestions': ['定位突增来源', '限流保护', '检查应用重启']},
    'space_high': {
        'rule_id': 'R031', 'name': '表空间容量不足', 'domain': 'capacity',
        'summary': '表空间/磁盘使用率过高', 'suggestions': ['扩容数据文件', '清理历史数据', '评估归档']},
    'repl_broken': {
        'rule_id': 'R052', 'name': '复制中断', 'domain': 'replication',
        'summary': '主从复制线程停止', 'suggestions': ['重启复制线程', '排查复制错误', '评估重建从库']},
    'repl_lag': {
        'rule_id': 'R051', 'name': '主从延迟严重', 'domain': 'replication',
        'summary': '从库延迟超阈值', 'suggestions': ['排查从库负载', '检查大事务', '并行复制调优']},
    'slow_surge': {
        'rule_id': 'R011', 'name': '慢查询激增', 'domain': 'sql',
        'summary': '慢查询数量突增', 'suggestions': ['定位 Top SQL', '检查执行计划劣化', '补索引']},
    'config_drift': {
        'rule_id': 'R078', 'name': '参数偏离最佳实践', 'domain': 'config',
        'summary': '关键参数发生漂移', 'suggestions': ['回滚漂移参数', '核对变更单']},
    'instance_down': {
        'rule_id': 'R000', 'name': '实例不可连接', 'domain': 'availability',
        'summary': '实例连接失败', 'suggestions': ['检查实例进程/网络', '评估重启或切换']},
    'baseline_deviation': {
        'rule_id': 'RB01', 'name': '指标偏离基线', 'domain': 'performance',
        'summary': '指标显著偏离历史基线', 'suggestions': ['结合业务确认是否预期', '排查诱因变更']},
}


def _latest_metrics(incident) -> dict:
    from monitor.models import MonitorLog
    log = MonitorLog.objects.filter(config=incident.config).order_by('-create_time').first()
    if not log:
        return {}
    try:
        d = json.loads(log.message) if isinstance(log.message, str) else log.message
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _build_features(incident, metrics, context):
    """构造 RCA 特征 dict: 采集指标 + 事故事件派生信号。"""
    f = dict(metrics)
    ash = context.get('ash_timeline', {}) or {}
    # 阻塞链 → locks (供锁规则)
    chains = ash.get('blocking_chains', []) or []
    if not chains and incident.events.exists():
        chains = (incident.events.first().detail or {}).get('chains', [])
    if chains:
        f['locks'] = chains
        f['blocked_sessions'] = sum(len(c.get('waiters', []) or []) for c in chains) or len(chains)
    return f


def _evidence_for(incident, root, metrics, context):
    """为一条根因构造结构化证据 (phase6/20 §3.3)。"""
    ev = []
    ash = context.get('ash_timeline', {}) or {}
    domain = root.get('domain')
    if domain == 'lock':
        chains = ash.get('blocking_chains', []) or []
        if not chains and incident.events.exists():
            chains = (incident.events.first().detail or {}).get('chains', [])
        if chains:
            ev.append({'type': 'lock', 'label': f"发现 {len(chains)} 组阻塞关系",
                       'data': {'chains': chains}})
        if ash.get('top_sql_by_samples'):
            ev.append({'type': 'sql', 'label': '高频活动 SQL',
                       'data': {'sql': ash['top_sql_by_samples'][:3]}})
    if domain in ('connection',):
        v = metrics.get('conn_usage_pct')
        if v is not None:
            ev.append({'type': 'metric', 'label': f"连接使用率 {v}%",
                       'data': {'metric': 'conn_usage_pct', 'value': v}})
    if domain == 'capacity':
        e0 = incident.events.first()
        if e0:
            ev.append({'type': 'metric', 'label': f"{e0.metric_key}={e0.value}",
                       'data': {'metric': e0.metric_key, 'value': e0.value, 'detail': e0.detail}})
    if domain == 'replication':
        e0 = incident.events.first()
        if e0:
            ev.append({'type': 'metric', 'label': '复制状态异常', 'data': e0.detail})
    if ash.get('top_wait_events'):
        ev.append({'type': 'session', 'label': '等待事件画像',
                   'data': {'top_wait_events': ash['top_wait_events'][:5]}})
    changes = context.get('recent_changes', [])
    if domain == 'config' and changes:
        ev.append({'type': 'change', 'label': f"近期 {len(changes)} 条变更",
                   'data': {'changes': changes[:5]}})
    return ev


def _run_rca(incident, features, context):
    """信号派生主根因 + RCA 规则补充命中, 合并去重, 附证据与置信度增强。"""
    signal = incident.events.first().signal if incident.events.exists() else ''
    composite = bool((incident.events.first().detail or {}).get('composite')) if incident.events.exists() else False
    metrics = features

    roots = []
    seen = set()

    # 1. 信号派生主根因
    base = _SIGNAL_ROOT_CAUSE.get(signal)
    if base:
        conf = 0.75
        if composite:
            conf = min(conf * 1.2, 1.0)
        roots.append({
            'rule_id': base['rule_id'], 'name': base['name'], 'domain': base['domain'],
            'confidence': round(conf, 2), 'causal_chain': [],
            'summary': base['summary'], 'suggestions': base['suggestions'],
            'evidence': _evidence_for(incident, base, metrics, context),
        })
        seen.add(base['rule_id'])

    # 2. RCA 规则库补充
    try:
        from monitor.rca_engine_v2 import RCAEngineV2
        eng = RCAEngineV2(db_type=incident.db_type)
        for d in eng.diagnose(features, context):
            rid = d.rule_id
            if rid in seen:
                continue
            conf = d.confidence
            if composite and d.domain == (base or {}).get('domain'):
                conf = min(conf * 1.2, 1.0)
            roots.append({
                'rule_id': rid, 'name': d.rule_name, 'domain': d.domain,
                'confidence': round(conf, 2), 'causal_chain': d.causal_chain,
                'summary': d.description,
                'suggestions': d.suggestions,
                'evidence': _evidence_for(incident, {'domain': d.domain}, metrics, context),
            })
            seen.add(rid)
    except Exception as e:
        logger.warning("[diag] RCA 规则库异常: %s", e)

    topn = int(getattr(settings, 'DIAG_RCA_TOPN', 3))
    roots.sort(key=lambda r: -r['confidence'])
    return roots[:topn]


def _search_cases(incident, features):
    try:
        from monitor.case_rag import search_cases
        signal = incident.events.first().signal if incident.events.exists() else ''
        symptom = f"{incident.db_type} {incident.category} {signal} {incident.title}"
        res = search_cases(symptom, db_type=incident.db_type, top_k=5)
        matches = getattr(res, 'matches', None) or (res.get('matches') if isinstance(res, dict) else [])
        out = []
        for m in matches or []:
            out.append({
                'case_id': getattr(m, 'case_id', None) or (m.get('case_id') if isinstance(m, dict) else None),
                'similarity': getattr(m, 'similarity', None) or (m.get('similarity') if isinstance(m, dict) else 0),
                'root_cause': getattr(m, 'root_cause', '') or (m.get('root_cause', '') if isinstance(m, dict) else ''),
                'fix': getattr(m, 'fix', '') or (m.get('fix', '') if isinstance(m, dict) else ''),
                'success': getattr(m, 'success', None) if not isinstance(m, dict) else m.get('success'),
            })
        return out
    except Exception as e:
        logger.debug("[diag] 案例检索跳过: %s", e)
        return []


def run_diagnosis(incident_id: str) -> dict:
    """诊断管道主入口。返回诊断摘要 dict。"""
    from monitor.models import Incident, IncidentStateError
    inc = Incident.objects.select_related('config').filter(incident_id=incident_id).first()
    if not inc:
        logger.warning("[diag] 事故 %s 不存在", incident_id)
        return {}
    if inc.status in ('resolved', 'closed'):
        return {}

    t0 = time.time()
    budget = int(getattr(settings, 'DIAG_BUDGET_SEC', 60))

    # 阶段1: 上下文 (含 ASH 时间线)
    from monitor.context_aggregator import build_incident_context
    context = build_incident_context(inc)
    metrics = _latest_metrics(inc)
    features = _build_features(inc, metrics, context)

    # 阶段2: RCA + 案例检索 (并行)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_cases = pool.submit(_search_cases, inc, features)
        root_causes = _run_rca(inc, features, context)
        similar_cases = fut_cases.result(timeout=max(budget - (time.time() - t0), 5))
    if similar_cases and root_causes:
        good = [c for c in similar_cases if c.get('success') and (c.get('similarity') or 0) >= 0.7]
        if good:
            root_causes[0]['confidence'] = min(root_causes[0]['confidence'] + 0.1, 1.0)

    rca_result = {
        'version': (inc.rca_result or {}).get('version', 0) + 1,
        'engine': 'rca_v2+signal',
        'root_causes': root_causes,
        'similar_cases': similar_cases,
        'diagnosed_at': timezone.now().isoformat(),
        'budget_ms': int((time.time() - t0) * 1000),
    }

    # 阶段3: 影响量化
    from monitor.impact_engine import assess_incident_impact
    impact = assess_incident_impact(inc, context)
    # impact_level=high 校正优先级
    if impact.get('impact_level') == 'high' and inc.priority in ('P3', 'P4'):
        inc.priority = 'P2'
        inc.save(update_fields=['priority'])
        impact['priority_adjusted'] = 'P2'

    # 阶段4: 方案生成
    from monitor.remediation_planner import generate_incident_plans
    plans = generate_incident_plans(inc, root_causes)

    # 阶段5: 落库 + 状态转移 + 通知
    inc.rca_result = rca_result
    inc.impact = impact
    inc.plans = plans
    inc.save(update_fields=['rca_result', 'impact', 'plans', 'priority', 'updated_at'])
    try:
        if inc.status == 'diagnosing':
            inc.transition('plan_ready', 'system', '诊断完成')
    except IncidentStateError:
        pass

    elapsed = time.time() - t0
    if elapsed > budget:
        logger.warning("[diag] %s 诊断超预算: %.1fs > %ds", incident_id, elapsed, budget)
    try:
        _write_diag_metric(inc, elapsed, 'timeout' if elapsed > budget else 'ok')
    except Exception:
        pass

    # 通知 (6B-08)
    try:
        from monitor.incident_notify import notify_incident_plan_ready
        notify_incident_plan_ready(inc)
    except Exception as e:
        logger.debug("[diag] 通知发送跳过: %s", e)

    logger.info("[diag] %s 完成: %d 根因, %d 方案, %.1fs",
                incident_id, len(root_causes), len(plans), elapsed)
    return {'root_causes': len(root_causes), 'plans': len(plans), 'elapsed': elapsed}


def _write_diag_metric(inc, elapsed, status):
    from monitor.timeseries import get_timeseries_storage
    ts = get_timeseries_storage()
    ts.write_metrics_batch(inc.config_id, {'diag_budget_ms': elapsed * 1000}, status=status)
