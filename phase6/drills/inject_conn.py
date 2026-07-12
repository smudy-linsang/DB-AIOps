# -*- coding: utf-8 -*-
"""6A 演练: 连接高水位注入 (phase6/10 §11)。

直接构造一份 conn_usage_pct>=95 的指标喂 collector 检测钩子, 断言生成 conn_high 事故。
(真实打满连接对共享测试库有风险, 故用指标注入验证检测→事故链路。)

用法: python phase6/drills/inject_conn.py <config_id>
"""
import os
import sys
import time

PROJECT_ROOT = "/Users/mac/DB_Monitor/db-aiops"
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbmonitor.settings")
import django  # noqa
django.setup()

from monitor.models import DatabaseConfig, Incident  # noqa
from monitor.management.commands.start_monitor import Command  # noqa
from monitor.pipeline import process_pending_once  # noqa

CONFIG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1


def main():
    cfg = DatabaseConfig.objects.get(id=CONFIG_ID)
    print(f"=== 连接高水位注入: {cfg.name} ({cfg.db_type}) ===")
    before = set(Incident.objects.filter(
        config_id=CONFIG_ID, category='connection',
        status__in=['open', 'diagnosing', 'plan_ready']
    ).values_list('incident_id', flat=True))

    t0 = time.time()
    Command()._emit_phase6_events(cfg, {
        'conn_usage_pct': 97.0, 'threads_connected': 970, 'max_connections': 1000})
    counts = process_pending_once()
    print(f"pipeline: {counts}")

    inc = Incident.objects.filter(
        config_id=CONFIG_ID, category='connection',
        status__in=['open', 'diagnosing', 'plan_ready']
    ).exclude(incident_id__in=before).order_by('-created_at').first()
    if not inc:
        print("[FAIL] 未生成连接事故")
        return 1
    detect = inc.t_detect_sec
    print(f"[OK] 事故 {inc.incident_id} | {inc.priority} | {inc.title}")
    print(f"     t_detect_sec={detect}")
    ok = detect is not None and detect <= 60
    print(f"验收: MTTD<=60s => {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
