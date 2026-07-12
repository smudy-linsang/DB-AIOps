# -*- coding: utf-8 -*-
"""6A 演练: 锁阻塞注入 (phase6/10 §11)。

对目标 MySQL 家族实例 (mysql/tdsql/gbase) 制造行锁阻塞:
  会话A: BEGIN; UPDATE 某行 (不提交, 持锁)
  会话B: UPDATE 同行 (阻塞等待)
然后跑 sentinel --once + pipeline --once, 断言生成 blocked_session 事故且 t_detect_sec<=60。

用法: python phase6/drills/inject_lock.py <config_id>   (默认 1=本地MySQL)
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

from monitor.models import DatabaseConfig, Incident  # noqa
from monitor.db_connector import DbConnector  # noqa
from monitor.sentinel import InstanceSentinel  # noqa
from monitor.pipeline import process_pending_once  # noqa

CONFIG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
HOLD_SEC = 45
# 阻塞需超过检测阈值(BLOCK_WAIT_WARN_SEC=10s)才产事件, 采样前等待此秒数
PRE_SAMPLE_WAIT = 13

_setup = """
CREATE TABLE IF NOT EXISTS drill_lock (id INT PRIMARY KEY, v INT);
INSERT INTO drill_lock (id, v) VALUES (1, 0) ON DUPLICATE KEY UPDATE v=v;
"""


def _exec_multi(conn, sql):
    cur = conn.cursor()
    for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
        cur.execute(stmt)
    conn.commit()
    cur.close()


def holder(cfg, ready, release):
    """会话A: 持有 drill_lock.id=1 的行锁 HOLD_SEC 秒。"""
    conn = DbConnector.get_connection(cfg)
    try:
        db = cfg.service_name or 'testdb'
        cur = conn.cursor()
        cur.execute(f"USE `{db}`")
        cur.execute("BEGIN")
        cur.execute("UPDATE drill_lock SET v=v+1 WHERE id=1")
        ready.set()
        release.wait(HOLD_SEC)
        conn.rollback()
        cur.close()
    finally:
        conn.close()


def waiter(cfg, ready):
    """会话B: 抢同一行锁 → 阻塞。"""
    ready.wait(10)
    conn = DbConnector.get_connection(cfg)
    try:
        db = cfg.service_name or 'testdb'
        cur = conn.cursor()
        cur.execute(f"USE `{db}`")
        cur.execute("SET SESSION innodb_lock_wait_timeout=30")
        cur.execute("UPDATE drill_lock SET v=v+100 WHERE id=1")  # 阻塞在此
        conn.commit()
        cur.close()
    except Exception:
        pass
    finally:
        conn.close()


def main():
    cfg = DatabaseConfig.objects.get(id=CONFIG_ID)
    print(f"=== 锁注入演练: {cfg.name} ({cfg.db_type}) ===")
    if cfg.db_type not in ('mysql', 'tdsql', 'gbase'):
        print("仅支持 MySQL 家族, 退出")
        return 1

    # 准备表
    conn = DbConnector.get_connection(cfg)
    db = cfg.service_name or 'testdb'
    cur = conn.cursor(); cur.execute(f"USE `{db}`"); cur.close()
    _exec_multi(conn, _setup)
    conn.close()

    # 记录基线: 现存 open 锁事故
    before = set(Incident.objects.filter(
        config_id=CONFIG_ID, category='lock',
        status__in=['open', 'diagnosing', 'plan_ready']
    ).values_list('incident_id', flat=True))

    ready, release = threading.Event(), threading.Event()
    th = threading.Thread(target=holder, args=(cfg, ready, release), daemon=True)
    tw = threading.Thread(target=waiter, args=(cfg, ready), daemon=True)
    th.start(); tw.start()
    ready.wait(10)
    print(f"阻塞形成, 等待 {PRE_SAMPLE_WAIT}s 使等待时长超过检测阈值(10s)...")
    time.sleep(PRE_SAMPLE_WAIT)  # 让阻塞等待超过 BLOCK_WAIT_WARN_SEC

    t0 = time.time()
    # 哨兵采样 (检测阻塞并 emit 事件)
    s = InstanceSentinel(cfg)
    s.probe()
    s.ash_sample()
    try:
        if s.conn:
            s.conn.close()
    except Exception:
        pass
    # pipeline 消费 → 生成事故
    counts = process_pending_once()
    print(f"pipeline: {counts}")

    inc = Incident.objects.filter(
        config_id=CONFIG_ID, category='lock',
        status__in=['open', 'diagnosing', 'plan_ready']
    ).exclude(incident_id__in=before).order_by('-created_at').first()

    release.set()
    th.join(timeout=5); tw.join(timeout=5)

    if not inc:
        print("[FAIL] 未生成锁阻塞事故")
        return 1
    chains = (inc.events.first().detail or {}).get('chains', []) if inc.events.exists() else []
    detect = inc.t_detect_sec
    print(f"[OK] 事故 {inc.incident_id} | {inc.priority} | {inc.status}")
    print(f"     t_detect_sec={detect} chains={len(chains)} title={inc.title}")
    ok = (detect is not None and detect <= 60 and len(chains) >= 1)
    print(f"验收: MTTD<=60s 且 chains 非空 => {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
