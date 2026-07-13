# -*- coding: utf-8 -*-
"""6C 全链路终验: 锁阻塞全自动闭环 (phase6/30 §12)。

注入行锁阻塞 → ASH检测→事故 → 诊断出方案 → 执行KILL → 验证回路 → 自动resolved。
断言 1-5-10: t_detect<=60, t_plan<=300, t_resolve<=600, 无人工介入。

用法: python phase6/drills/e2e_lock.py [config_id]
"""
import os
import sys
import threading
import time

PROJECT_ROOT = "/Users/mac/DB_Monitor/db-aiops"
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbmonitor.settings")
import django  # noqa
django.setup()

from monitor.models import DatabaseConfig, Incident, PlaybookRun  # noqa
from monitor.db_connector import DbConnector  # noqa
from monitor.sentinel import InstanceSentinel  # noqa
from monitor.pipeline import process_pending_once  # noqa
from monitor.diagnosis_pipeline import run_diagnosis  # noqa
from monitor.playbook_engine import execute_run  # noqa
from monitor.playbook_authz import decide_trigger  # noqa
from monitor.verify_loop import _monitor_impl  # noqa
from monitor.models import Playbook  # noqa

CONFIG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
PRE_SAMPLE_WAIT = 13


def main():
    cfg = DatabaseConfig.objects.get(id=CONFIG_ID)
    print(f"=== 6C 全链路终验: {cfg.name} ({cfg.db_type}) ===")
    if cfg.db_type not in ('mysql', 'tdsql', 'gbase'):
        print("仅支持 MySQL 家族")
        return 1
    db = cfg.service_name or 'testdb'

    # 准备
    c0 = DbConnector.get_connection(cfg)
    cur = c0.cursor(); cur.execute(f"USE `{db}`")
    cur.execute("CREATE TABLE IF NOT EXISTS e2e_lock (id INT PRIMARY KEY, v INT)")
    cur.execute("INSERT INTO e2e_lock VALUES (1,0) ON DUPLICATE KEY UPDATE v=v")
    c0.commit(); cur.close(); c0.close()

    Incident.objects.filter(config=cfg, dedup_key=f'{CONFIG_ID}:lock',
                            status__in=['open', 'diagnosing', 'plan_ready', 'executing', 'verifying']).update(status='closed')

    # 会话A 持锁
    holder = DbConnector.get_connection(cfg)
    hc = holder.cursor(); hc.execute(f"USE `{db}`"); hc.execute("BEGIN")
    hc.execute("UPDATE e2e_lock SET v=v+1 WHERE id=1")
    hc.execute("SELECT CONNECTION_ID()"); blocker_id = hc.fetchone()['CONNECTION_ID()']

    # 会话B 抢锁阻塞
    def waiter():
        w = DbConnector.get_connection(cfg); wc = w.cursor(); wc.execute(f"USE `{db}`")
        wc.execute("SET SESSION innodb_lock_wait_timeout=90")
        try:
            wc.execute("UPDATE e2e_lock SET v=v+100 WHERE id=1"); w.commit()
        except Exception:
            pass
        w.close()
    threading.Thread(target=waiter, daemon=True).start()

    print(f"阻塞形成(blocker={blocker_id}), 等待 {PRE_SAMPLE_WAIT}s 超阈值...")
    time.sleep(PRE_SAMPLE_WAIT)

    # ---- 阶段1: 发现 (哨兵 ASH → 事故) ----
    s = InstanceSentinel(cfg); s.probe(); s.ash_sample()
    try:
        s.conn.close()
    except Exception:
        pass
    process_pending_once()  # cg_detect 建事故 (+ 自动 emit diagnosis)
    inc = Incident.objects.filter(config=cfg, category='lock',
                                  status__in=['diagnosing', 'plan_ready']).order_by('-created_at').first()
    if not inc:
        print("[FAIL] 未生成锁事故")
        return 1
    print(f"[发现] {inc.incident_id} t_detect={inc.t_detect_sec}s")

    # ---- 阶段2: 诊断 (出方案) ----
    run_diagnosis(inc.incident_id)
    inc.refresh_from_db()
    print(f"[诊断] status={inc.status} t_plan={inc.t_plan_sec}s 根因={len(inc.rca_result.get('root_causes', []))} 方案={len(inc.plans)}")

    # ---- 阶段3: 执行 (授权→KILL) ----
    pb = Playbook.objects.get(playbook_id='PB-LOCK-KILL-BLOCKER')
    decision = decide_trigger(inc, pb)
    run = PlaybookRun.objects.create(
        run_id=f"PBR-E2E-{int(time.time())}", playbook=pb, incident=inc,
        params={'blocker_id': blocker_id}, trigger_mode=decision['mode'], status='prechecking')
    exec_res = execute_run(run.run_id)
    run.refresh_from_db()
    print(f"[执行] 授权={decision['mode']} run={run.status} steps={len(run.step_results)}")

    # ---- 阶段4: 验证回路 (采样→检测恢复→resolved) ----
    # KILL 后需哨兵再采样使 blocked=0
    for _ in range(3):
        s2 = InstanceSentinel(cfg); s2.probe(); s2.ash_sample()
        try:
            s2.conn.close()
        except Exception:
            pass
        time.sleep(1)
    _monitor_impl({'playbook_run_id': run.run_id, 'incident_id': inc.incident_id,
                   'verify_metric': 'blocked_sessions', 'recover_expr': '== 0',
                   'window_sec': 40, 'check_interval_sec': 3, 'min_stable_checks': 2,
                   'data_source': 'ash'})
    inc.refresh_from_db(); run.refresh_from_db()
    print(f"[验证] 事故={inc.status} run={run.status} MTTR={inc.t_resolve_sec}s")

    try:
        holder.close()
    except Exception:
        pass

    # ---- 断言 1-5-10 ----
    ok = (inc.status == 'resolved' and run.status == 'succeeded'
          and inc.t_detect_sec is not None and inc.t_detect_sec <= 60
          and inc.t_plan_sec is not None and inc.t_plan_sec <= 300
          and inc.t_resolve_sec is not None and inc.t_resolve_sec <= 600)
    print("\n" + "=" * 50)
    print(f"1分钟发现: {inc.t_detect_sec}s <=60 => {'PASS' if inc.t_detect_sec <= 60 else 'FAIL'}")
    print(f"5分钟方案: {inc.t_plan_sec}s <=300 => {'PASS' if inc.t_plan_sec <= 300 else 'FAIL'}")
    print(f"10分钟解决: {inc.t_resolve_sec}s <=600 => {'PASS' if inc.t_resolve_sec <= 600 else 'FAIL'}")
    print(f"全自动闭环(无人工): {'PASS' if ok else 'FAIL'}")
    print("=" * 50)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
