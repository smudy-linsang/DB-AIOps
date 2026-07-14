# -*- coding: utf-8 -*-
"""6C 破坏演练 6/8: 慢SQL突增全自动闭环 (真实慢查询)。

注入: 调低 long_query_time=1 后连发 12 条 SELECT SLEEP(1.2) (真实计入
      Slow_queries), 同时挂一条 SELECT SLEEP(300) 长查询作为处置目标
链路: 采集算 slow_queries_delta → L3 slow_surge(增量>=10) → 事故 → 守护自动诊断
      → PB-SLOW-KILL-TOPSQL kill 慢会话 → 验证 slow_queries_delta<5 → resolved
说明: slow_queries 是累计计数器, 本演练同时验证"增量判定"修复
      (旧实现拿累计值对比基线, 永远无法恢复)。

用法: python phase6/drills/e2e_slow.py [config_id=1]
"""
import subprocess
import sys
import threading
import time

from _drill_common import (  # noqa
    DatabaseConfig, DbConnector, timezone,
    assert_1_5_10, close_stale_incidents, collect_once, pump_collect,
    run_playbook, wait_incident, wait_incident_status, wait_run_final,
)

CONFIG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
CONTAINER = 'db-aiops-mysql'
SLOW_N = 12


def root_sql(sql):
    r = subprocess.run(
        ['docker', 'exec', CONTAINER, 'mysql', '-uroot', '-proot123', '-N', '-e', sql],
        capture_output=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[:200])
    return r.stdout.decode().strip()


def main():
    cfg = DatabaseConfig.objects.get(id=CONFIG_ID)
    print(f"=== 演练6 慢SQL突增: {cfg.name} ({cfg.db_type}) ===")

    base_lqt = root_sql("SELECT @@GLOBAL.long_query_time")
    close_stale_incidents(cfg, f'{cfg.id}:performance')

    sleeper = None
    sleeper_id = None
    stop_pump = threading.Event()
    try:
        root_sql("SET GLOBAL long_query_time=1")
        collect_once(cfg)  # 种慢查询计数器基值

        # ---- 注入: 长查询目标会话 + 一波真实慢查询 ----
        t0 = timezone.now()
        sleeper = DbConnector.get_connection(cfg)
        sc = sleeper.cursor()
        sc.execute("SELECT CONNECTION_ID() id")
        sleeper_id = sc.fetchone()['id']

        def _long_query():
            try:
                sc.execute("SELECT SLEEP(300)")
            except Exception:
                pass
        threading.Thread(target=_long_query, daemon=True).start()

        burst = DbConnector.get_connection(cfg)
        bc = burst.cursor()
        for _ in range(SLOW_N):
            bc.execute("SELECT SLEEP(1.2)")
        burst.close()
        print(f"已注入: {SLOW_N} 条真实慢查询 + 长查询会话 {sleeper_id}")
        collect_once(cfg)

        # ---- 发现 ----
        inc = wait_incident(cfg, 'performance', since=t0, timeout=60, signal='slow_surge')
        if not inc:
            print("[FAIL] 未生成慢SQL事故")
            return 1
        print(f"[发现] {inc.incident_id} [{inc.priority}] {inc.title} "
              f"t_detect={inc.t_detect_sec}s")

        # ---- 诊断 ----
        if not wait_incident_status(inc, ('plan_ready',), timeout=90, label='plan_ready'):
            return 1
        print(f"[诊断] t_plan={inc.t_plan_sec}s 方案={len(inc.plans)}")

        # ---- 处置: kill 慢SQL会话 ----
        run = run_playbook(inc, 'PB-SLOW-KILL-TOPSQL', params={'session_id': sleeper_id})
        if run.status == 'failed':
            print(f"[FAIL] Playbook 执行失败: {run.error_message}")
            return 1

        # ---- 验证 ----
        pump_collect(cfg, stop_pump, interval=10)
        final = wait_run_final(run, timeout=240)
        print(f"[验证] run={final}")
        wait_incident_status(inc, ('resolved',), timeout=30, label='resolved')

        return 0 if assert_1_5_10(inc, run) else 1
    finally:
        stop_pump.set()
        try:
            root_sql(f"SET GLOBAL long_query_time={base_lqt}")
        except Exception:
            pass
        if sleeper_id:
            try:
                root_sql(f"KILL {sleeper_id}")
            except Exception:
                pass
        try:
            sleeper and sleeper.close()
        except Exception:
            pass
        print(f"(清理: long_query_time 已还原为 {base_lqt})")


if __name__ == "__main__":
    sys.exit(main())
