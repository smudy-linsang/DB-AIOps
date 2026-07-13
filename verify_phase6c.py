# -*- coding: utf-8 -*-
"""Phase 6C 验收脚本。处置层: Playbook执行→验证回路→resolved + SLA/值班。"""
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


def run_all():
    print("=" * 70)
    print(f"Phase 6C 验收 - {datetime.now().isoformat()}")
    print("=" * 70)
    results = {}

    def t01():
        from monitor.models import Playbook, PlaybookRun, OnCallSchedule
        assert Playbook.objects.count() >= 8, "Playbook 未初始化(跑 init_playbooks)"
    results['6C-01/04 模型+8类Playbook'] = verify("模型+Playbook", t01)

    def t02():
        from monitor.playbook_engine import execute_run, _eval_expect, _render
        assert _render("KILL {blocker_id}", {'blocker_id': 99}) == "KILL 99"
        assert _eval_expect('rows>=1', {'status': 'ok', 'rows_affected': 2}) is True
        assert _eval_expect('rows>=1', {'status': 'ok', 'rows_affected': 0}) is False
    results['6C-02 执行引擎'] = verify("执行引擎", t02)

    def t03():
        from monitor.verify_loop import _eval_recover, run_verify
        assert _eval_recover('== 0', 0) is True
        assert _eval_recover('< 80', 75) is True
        assert _eval_recover('< 80', 90) is False
    results['6C-03 验证回路'] = verify("验证回路判定", t03)

    def t05():
        from monitor.playbook_authz import decide_trigger
        from monitor.models import Playbook, DatabaseConfig, Incident
        from django.utils import timezone
        cfg = DatabaseConfig.objects.filter(is_active=True).first()
        pb_low = Playbook.objects.filter(risk_level='low').first()
        pb_high = Playbook.objects.filter(risk_level='high').first()
        inc = Incident(config=cfg, db_type=cfg.db_type, priority='P2', category='lock',
                       status='plan_ready', occurred_at=timezone.now())
        d_low = decide_trigger(inc, pb_low)
        d_high = decide_trigger(inc, pb_high)
        assert d_low['execute_now'] is True, "低风险应可执行"
        assert d_high['execute_now'] is False and d_high['need_approval'], "高风险应需审批"
    results['6C-05 分级授权'] = verify("分级授权", t05)

    def t06():
        from monitor.oncall import get_current_oncall, check_and_escalate
        assert callable(get_current_oncall) and callable(check_and_escalate)
    results['6C-06 值班'] = verify("值班模块", t06)

    def t07():
        from monitor.sla_report import build_sla_report
        rep = build_sla_report()
        assert 'overall' in rep and 'by_category' in rep
        assert 'detect_ok_rate' in rep['overall']
    results['6C-07 SLA报表'] = verify("SLA报表", t07)

    def t08():
        from monitor.api_views_incident import (
            IncidentExecuteView, PlaybookRunDetailView, PlaybookRunApproveView,
            PlaybookRunRollbackView, PlaybookListView, SlaReportView, OnCallView)
        for f in ['frontend/src/pages/SlaReport.jsx']:
            assert os.path.exists(os.path.join(PROJECT_ROOT, f)), f
    results['6C-08/09 API+前端'] = verify("执行API+前端", t08)

    print("\n" + "=" * 70)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        print(f"  {'[OK]' if ok else '[FAIL]'} {name}")
    print(f"\n通过: {passed}/{total}")
    if passed == total:
        print("\n>>> Phase 6C 所有模块验收通过! (全链路 e2e 见 phase6/drills/e2e_lock.py)")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
