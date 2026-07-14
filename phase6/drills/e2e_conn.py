# -*- coding: utf-8 -*-
"""6C 破坏演练 2/8: 连接风暴全自动闭环 (真实打满连接)。

注入: 向实例开真实空闲连接至 conn_usage_pct>=95 (留安全余量给哨兵/采集/处置连接)
链路: 哨兵黄金量/采集 L1 → conn_high 事故 → 守护自动诊断 → PB-CONN-CLEAN-IDLE
      批量 kill 空闲会话 → 验证 conn_usage_pct<80 → resolved
断言: 1-5-10 全自动闭环。

用法: python phase6/drills/e2e_conn.py [config_id=1]
"""
import math
import sys
import threading
import time

from _drill_common import (  # noqa
    DatabaseConfig, DbConnector, timezone,
    assert_1_5_10, close_stale_incidents, collect_once, mysql_conn,
    pump_collect, run_playbook, wait_incident, wait_incident_status,
    wait_run_final,
)

CONFIG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
IDLE_SEC = 10          # 空闲判定阈值 (哨兵探活间隔8s, 其常驻连接不会被误杀)
SAFETY_SLOTS = 4       # 给哨兵/采集/Playbook 留的连接余量


def main():
    cfg = DatabaseConfig.objects.get(id=CONFIG_ID)
    print(f"=== 演练2 连接风暴: {cfg.name} ({cfg.db_type}) ===")
    if cfg.db_type not in ('mysql', 'tdsql', 'gbase'):
        print("仅支持 MySQL 家族")
        return 1

    ctl = mysql_conn(cfg)
    cur = ctl.cursor()
    cur.execute("SHOW VARIABLES LIKE 'max_connections'")
    max_conn = int(cur.fetchone()['Value'])
    cur.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
    now_conn = int(cur.fetchone()['Value'])
    cur.close()

    target = min(math.ceil(max_conn * 0.96), max_conn - SAFETY_SLOTS)
    need = target - now_conn
    print(f"max_connections={max_conn} 当前={now_conn} 目标={target} 需注入={need}")
    if need > 300:
        print("[SKIP] 需注入连接过多, 请先调低 max_connections")
        return 1

    close_stale_incidents(cfg, f'{cfg.id}:conn_high')
    close_stale_incidents(cfg, f'{cfg.id}:conn_storm')

    holders = []
    stop_pump = threading.Event()
    try:
        t0 = timezone.now()
        for i in range(need):
            holders.append(DbConnector.get_connection(cfg))
        print(f"已注入 {len(holders)} 个真实空闲连接, 等待检测...")

        # 采集一轮 (L1 conn_high + 写 MonitorLog); 哨兵守护同时也在发黄金量事件
        collect_once(cfg)

        # ---- 发现 ----
        inc = wait_incident(cfg, 'connection', since=t0, timeout=60, signal='conn_high')
        if not inc:
            print("[FAIL] 未生成连接事故")
            return 1
        print(f"[发现] {inc.incident_id} [{inc.priority}] {inc.title} "
              f"t_detect={inc.t_detect_sec}s")

        # ---- 诊断 (守护自动) ----
        if not wait_incident_status(inc, ('plan_ready',), timeout=90, label='plan_ready'):
            return 1
        print(f"[诊断] t_plan={inc.t_plan_sec}s 根因={len(inc.rca_result.get('root_causes', []))} "
              f"方案={len(inc.plans)}")

        # ---- 处置: 等空闲时长过阈值再触发 ----
        time.sleep(IDLE_SEC + 2)
        run = run_playbook(inc, 'PB-CONN-CLEAN-IDLE', params={'idle_sec': IDLE_SEC})
        if run.status == 'failed':
            print(f"[FAIL] Playbook 执行失败: {run.error_message}")
            return 1

        # ---- 验证 (守护验证回路 + 本进程泵采集保持快照新鲜) ----
        pump_collect(cfg, stop_pump, interval=10)
        final = wait_run_final(run, timeout=240)
        print(f"[验证] run={final}")
        wait_incident_status(inc, ('resolved',), timeout=30, label='resolved')

        return 0 if assert_1_5_10(inc, run) else 1
    finally:
        stop_pump.set()
        for h in holders:
            try:
                h.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
