# -*- coding: utf-8 -*-
"""
Phase 6A 完整验收脚本 (仿 verify_phase5.py 风格)。
逐项验证发现层各模块 + 端到端 event→incident + 契约。
"""
import os
import sys
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


def run_all():
    print("=" * 70)
    print(f"Phase 6A 验收 - {datetime.now().isoformat()}")
    print("=" * 70)
    results = {}

    def t01():
        from monitor.models import Event, Incident, Problem, IncidentStateError
        assert 'diagnosing' in Incident.ALLOWED_TRANSITIONS['open']
        assert 'executing' not in Incident.ALLOWED_TRANSITIONS['diagnosing']
    results['6A-01 模型+状态机'] = verify("6A-01 事件/事故/问题模型", t01)

    def t02():
        from monitor.db_connector import DbConnector
        from monitor.models import DatabaseConfig
        n = 0
        for cfg in DatabaseConfig.objects.filter(is_active=True):
            conn = DbConnector.get_connection(cfg)
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM DUAL" if cfg.db_type in ('oracle', 'dm') else "SELECT 1")
            cur.fetchone(); cur.close(); conn.close(); n += 1
        assert n >= 1, "无可连实例"
    results['6A-02 DbConnector'] = verify("6A-02 六库连接", t02)

    def t03():
        from monitor.timeseries import get_timeseries_storage
        from django.utils import timezone
        import datetime as dt
        ts = get_timeseries_storage()
        ts.write_session_samples(1, 'mysql', [{'session_id': 'v', 'is_blocked': False}])
        got = ts.query_session_samples(1, timezone.now() - dt.timedelta(minutes=5))
        assert isinstance(got, list)
    results['6A-03 session_sample'] = verify("6A-03 会话样本超表", t03)

    def t04():
        from monitor import redis_bus as rb
        rb.ensure_groups()
        bus = rb.get_bus()
        for s in (rb.STREAM_EVENTS, rb.STREAM_DIAG, rb.STREAM_VERIFY):
            groups = bus.xinfo_groups(s)
            assert len(groups) >= 1
    results['6A-04 redis_bus'] = verify("6A-04 Stream+消费组", t04)

    def t06():
        from monitor.detectors import detect_l1, detect_l2, detect_l3
        from monitor.detectors.l1_hard import detect_blocked_from_ash
        from monitor.models import DatabaseConfig
        cfg = DatabaseConfig.objects.filter(is_active=True).first()
        e = detect_l1(cfg, {'conn_usage_pct': 97})
        assert any(x['signal'] == 'conn_high' for x in e)
        eb = detect_blocked_from_ash(cfg, [{'session_id': '1', 'is_blocked': True,
                                            'blocker_id': '2', 'active_secs': 40}])
        assert eb and eb[0]['signal'] == 'blocked_session'
    results['6A-06 检测器'] = verify("6A-06 三层检测器", t06)

    def t07():
        from monitor.incident_manager import compute_priority, ingest_event
        from monitor.models import DatabaseConfig, Incident, Event
        cfg = DatabaseConfig.objects.filter(is_active=True).first()
        assert compute_priority(cfg, 'config_drift') in ('P3', 'P4')
        # 端到端: event → incident
        Incident.objects.filter(config=cfg, dedup_key=f'{cfg.id}:performance',
                                status__in=['open', 'diagnosing']).update(status='closed')
        inc = ingest_event({'event_uid': 'EVT-VERIFY6A01', 'config_id': cfg.id,
                            'db_type': cfg.db_type, 'source': 'ml', 'signal': 'slow_surge',
                            'metric_key': 'slow_queries', 'value': 100, 'severity': 'warning',
                            'occurred_at': None, 'dedup_key': f'{cfg.id}:slow_surge',
                            'category': 'performance', 'detail': {}})
        assert inc and inc.status == 'diagnosing'
        assert inc.t_detect_sec is not None
        Incident.objects.filter(incident_id=inc.incident_id).update(status='closed')
        Event.objects.filter(event_uid='EVT-VERIFY6A01').delete()
    results['6A-07 incident_manager'] = verify("6A-07 聚合/定级/入队", t07)

    def t08():
        from monitor.pipeline import process_pending_once, Pipeline
        assert callable(process_pending_once)
        assert Pipeline
    results['6A-08 pipeline'] = verify("6A-08 pipeline_worker", t08)

    def t05():
        from monitor.sentinel import sample_sessions, golden_metrics, InstanceSentinel, SentinelManager
        assert callable(sample_sessions) and SentinelManager
    results['6A-05 sentinel'] = verify("6A-05 哨兵", t05)

    def t09():
        from monitor.management.commands.start_monitor import Command
        assert hasattr(Command, '_emit_phase6_events')
    results['6A-09 collector发事件'] = verify("6A-09/10 collector", t09)

    def t11():
        from monitor.api_views_incident import (
            IncidentListView, IncidentDetailView, IncidentTimelineView,
            IncidentAckView, IncidentCloseView, EventListView)
        assert all([IncidentListView, IncidentDetailView, IncidentTimelineView,
                    IncidentAckView, IncidentCloseView, EventListView])
        for f in ['frontend/src/pages/IncidentList.jsx', 'frontend/src/pages/IncidentDetail.jsx']:
            assert os.path.exists(os.path.join(PROJECT_ROOT, f)), f
    results['6A-11 API+前端'] = verify("6A-11 事故 API + 前端", t11)

    def t_settings():
        from django.conf import settings
        for k in ('SENTINEL_INTERVAL_SEC', 'ASH_INTERVAL_SEC', 'INCIDENT_STORM_THRESHOLD',
                  'PIPELINE_STREAM_MAXLEN'):
            assert hasattr(settings, k), k
    results['配置项登记'] = verify("配置项 (README §4)", t_settings)

    print("\n" + "=" * 70)
    print("验收结果汇总")
    print("=" * 70)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        print(f"  {'[OK]' if ok else '[FAIL]'} {name}")
    print(f"\n通过: {passed}/{total}")
    if passed == total:
        print("\n>>> Phase 6A 所有模块验收通过!")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
