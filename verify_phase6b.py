# -*- coding: utf-8 -*-
"""
Phase 6B 验收脚本。诊断层: 事故→诊断管道→根因/影响/方案→通知。
"""
import os
import sys
import time
import traceback
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbmonitor.settings")
import django  # noqa
django.setup()


def verify(name, fn):
    print(f"\n[验证] {name} ... ", end="")
    try:
        fn()
        print("[OK]")
        return True
    except Exception as e:
        print(f"[FAIL]: {e}")
        traceback.print_exc()
        return False


def _make_lock_incident():
    """构造一个带阻塞链的锁事故(不走真实库, 直接建模型)。"""
    from monitor.models import DatabaseConfig, Incident, Event, _uid
    from django.utils import timezone
    import datetime as dt
    cfg = DatabaseConfig.objects.filter(is_active=True).first()
    Incident.objects.filter(config=cfg, dedup_key=f'{cfg.id}:lock',
                            status__in=['open', 'diagnosing', 'plan_ready']).update(status='closed')
    inc = Incident.objects.create(
        incident_id=f"INC-VERIFY6B-{int(time.time())}", config=cfg, db_type=cfg.db_type,
        category='lock', title='锁等待阻塞: 2 个会话被阻塞', priority='P2',
        status='open', dedup_key=f'{cfg.id}:lock', event_count=1,
        occurred_at=timezone.now() - dt.timedelta(seconds=5), detected_at=timezone.now(),
        health_snapshot=80.0)
    Event.objects.create(
        event_uid=_uid('EVT'), config=cfg, db_type=cfg.db_type, source='ash',
        signal='blocked_session', metric_key='blocked_sessions', value=2, severity='critical',
        dedup_key=f'{cfg.id}:blocked_session', occurred_at=inc.occurred_at, incident=inc,
        detail={'waiters': 2, 'blockers': ['99'], 'max_wait_sec': 40,
                'chains': [{'blocker': '99', 'waiter': '10', 'wait_sec': 40},
                           {'blocker': '99', 'waiter': '11', 'wait_sec': 20}]})
    inc.transition('diagnosing', 'system', 'verify')
    return inc


def run_all():
    print("=" * 70)
    print(f"Phase 6B 验收 - {datetime.now().isoformat()}")
    print("=" * 70)
    results = {}

    def t_modules():
        from monitor.diagnosis_pipeline import run_diagnosis
        from monitor.context_aggregator import build_incident_context, get_ash_timeline
        from monitor.impact_engine import assess_incident_impact
        from monitor.remediation_planner import generate_incident_plans
        from monitor.incident_notify import build_incident_card, notify_incident_plan_ready
        assert all([run_diagnosis, build_incident_context, assess_incident_impact,
                    generate_incident_plans, build_incident_card])
    results['6B 模块导入'] = verify("模块导入", t_modules)

    holder = {}

    def t_pipeline():
        from monitor.diagnosis_pipeline import run_diagnosis
        inc = _make_lock_incident()
        holder['inc'] = inc
        t0 = time.time()
        res = run_diagnosis(inc.incident_id)
        elapsed = time.time() - t0
        inc.refresh_from_db()
        assert inc.status == 'plan_ready', f"状态={inc.status}"
        assert inc.plan_ready_at is not None
        assert elapsed <= 60, f"超预算 {elapsed}s"
        assert inc.t_plan_sec is not None and inc.t_plan_sec <= 300
    results['6B-01/07 诊断管道≤60s进plan_ready'] = verify("诊断管道", t_pipeline)

    def t_rca():
        inc = holder['inc']; inc.refresh_from_db()
        roots = inc.rca_result.get('root_causes', [])
        assert len(roots) >= 1, "无根因"
        top = roots[0]
        assert top['domain'] == 'lock', f"域={top['domain']}"
        assert len(top.get('evidence', [])) >= 1, "无证据链"
        assert any(e['type'] == 'lock' for e in top['evidence']), "证据无锁链"
    results['6B-03 根因命中锁类+证据链'] = verify("根因+证据链", t_rca)

    def t_plans():
        inc = holder['inc']; inc.refresh_from_db()
        plans = inc.plans
        assert len(plans) >= 2, f"方案数={len(plans)}"
        std = [p for p in plans if p['scenario'] == 'standard']
        assert std, "无标准方案"
        # kill 目标参数化: 标准方案 SQL 含 blocker id 99
        sqls = ' '.join(s.get('sql', '') for s in std[0]['steps'])
        assert '99' in sqls or std[0].get('playbook_ref'), "kill 目标未参数化"
        assert std[0]['verify'].get('metric') == 'blocked_sessions'
    results['6B-06 方案≥2套+参数化+verify'] = verify("方案生成", t_plans)

    def t_impact():
        inc = holder['inc']; inc.refresh_from_db()
        imp = inc.impact
        assert 'impact_level' in imp
        assert imp.get('affected_sessions', 0) >= 1
    results['6B-05 影响量化'] = verify("影响量化", t_impact)

    def t_notify():
        from monitor.incident_notify import build_incident_card
        inc = holder['inc']; inc.refresh_from_db()
        card = build_incident_card(inc)
        for kw in ['发生了什么', '为什么', '影响谁', '怎么修', '处理']:
            assert kw in card['body'], f"通知缺 {kw}"
    results['6B-08 五要素通知'] = verify("五要素通知", t_notify)

    # 清理
    try:
        if 'inc' in holder:
            from monitor.models import Event
            Event.objects.filter(incident=holder['inc']).delete()
            holder['inc'].delete()
    except Exception:
        pass

    print("\n" + "=" * 70)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        print(f"  {'[OK]' if ok else '[FAIL]'} {name}")
    print(f"\n通过: {passed}/{total}")
    if passed == total:
        print("\n>>> Phase 6B 所有模块验收通过!")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
