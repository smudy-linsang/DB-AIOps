# -*- coding: utf-8 -*-
"""6C 破坏演练 7/8: 死锁频发闭环 (真实死锁, 建议类自动执行)。

注入: 两会话交叉更新制造 4 次真实死锁 (INNODB_METRICS lock_deadlocks 增长)
链路: 采集算死锁增量 → L3 deadlock_surge(5min增量>=3) → 事故 → 守护自动诊断
      → PB-DEADLOCK-ADVISE 低风险自动执行(采集 INNODB STATUS 死锁证据)
      → 建议类无自动恢复判据, 由人确认后解决 (演练模拟 DBA 确认)
说明: 同时验证死锁计数修复 (原生 MySQL 8 无 Innodb_deadlocks 状态变量,
      旧实现恒为 0, 死锁永远检测不到)。

用法: python phase6/drills/e2e_deadlock.py [config_id=1]
"""
import sys
import threading
import time

from _drill_common import (  # noqa
    DatabaseConfig, DbConnector, timezone,
    assert_1_5_10, close_stale_incidents, collect_once,
    run_playbook, wait_incident, wait_incident_status,
)

CONFIG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
DEADLOCKS = 4


def make_deadlock(cfg, db):
    """两连接交叉更新, 制造一次真实死锁。返回是否成功。"""
    c1 = DbConnector.get_connection(cfg)
    c2 = DbConnector.get_connection(cfg)
    got = {'deadlock': False}
    try:
        k1, k2 = c1.cursor(), c2.cursor()
        for k in (k1, k2):
            k.execute(f"USE `{db}`")
            k.execute("BEGIN")
        k1.execute("UPDATE e2e_dl SET v=v+1 WHERE id=1")
        k2.execute("UPDATE e2e_dl SET v=v+1 WHERE id=2")

        def cross():
            try:
                k2.execute("UPDATE e2e_dl SET v=v+1 WHERE id=1")  # 等 c1 的锁
                c2.commit()
            except Exception as e:
                if 'Deadlock' in str(e):
                    got['deadlock'] = True
        t = threading.Thread(target=cross)
        t.start()
        time.sleep(0.4)
        try:
            k1.execute("UPDATE e2e_dl SET v=v+1 WHERE id=2")  # 交叉 → 死锁
            c1.commit()
        except Exception as e:
            if 'Deadlock' in str(e):
                got['deadlock'] = True
        t.join(timeout=10)
    finally:
        for c in (c1, c2):
            try:
                c.close()
            except Exception:
                pass
    return got['deadlock']


def main():
    cfg = DatabaseConfig.objects.get(id=CONFIG_ID)
    db = cfg.service_name or 'testdb'
    print(f"=== 演练7 死锁频发: {cfg.name} ({cfg.db_type}) ===")

    prep = DbConnector.get_connection(cfg)
    pc = prep.cursor()
    pc.execute(f"USE `{db}`")
    pc.execute("CREATE TABLE IF NOT EXISTS e2e_dl (id INT PRIMARY KEY, v INT)")
    pc.execute("INSERT INTO e2e_dl VALUES (1,0),(2,0) "
               "ON DUPLICATE KEY UPDATE v=VALUES(v)")
    prep.commit()

    close_stale_incidents(cfg, f'{cfg.id}:lock')
    collect_once(cfg)  # 种死锁计数器基值

    try:
        # ---- 注入: 真实死锁 x4 ----
        t0 = timezone.now()
        made = sum(1 for _ in range(DEADLOCKS) if make_deadlock(cfg, db))
        print(f"已注入: {made}/{DEADLOCKS} 次真实死锁")
        if made < 3:
            print("[FAIL] 死锁注入不足 3 次")
            return 1
        collect_once(cfg)

        # ---- 发现 ----
        inc = wait_incident(cfg, 'lock', since=t0, timeout=60, signal='deadlock_surge')
        if not inc:
            print("[FAIL] 未生成死锁事故")
            return 1
        print(f"[发现] {inc.incident_id} [{inc.priority}] {inc.title} "
              f"t_detect={inc.t_detect_sec}s")

        # ---- 诊断 ----
        if not wait_incident_status(inc, ('plan_ready',), timeout=90, label='plan_ready'):
            return 1
        print(f"[诊断] t_plan={inc.t_plan_sec}s 方案={len(inc.plans)}")

        # ---- 处置: 低风险建议类, 应走 auto 通道 ----
        run = run_playbook(inc, 'PB-DEADLOCK-ADVISE')
        if run.status != 'succeeded':
            print(f"[FAIL] 建议类 Playbook 未成功: {run.status} {run.error_message}")
            return 1
        if run.trigger_mode != 'auto':
            print(f"[WARN] 预期 auto 触发, 实际 {run.trigger_mode}")
        evidence = (run.step_results or [{}])[0].get('output', '')[:120]
        print(f"[建议] 已自动采集死锁证据: {evidence}...")

        # ---- 建议类由人确认闭环 (演练模拟 DBA 确认) ----
        inc.refresh_from_db()
        inc.transition('resolved', 'drill-dba', '阅读死锁建议并确认处理完毕')
        return 0 if assert_1_5_10(inc, run) else 1
    finally:
        try:
            pc.execute("DROP TABLE IF EXISTS e2e_dl")
            prep.commit()
            prep.close()
        except Exception:
            pass
        print("(清理: 演练表已删除)")


if __name__ == "__main__":
    sys.exit(main())
