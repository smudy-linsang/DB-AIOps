# -*- coding: utf-8 -*-
"""Phase 7D-03: 性能中心 6 大 DBA 排障场景注入脚本 (phase7/40 §3)。

只注入真实负载, 不做断言 — 断言由人在性能中心页面按走查表完成 (神似终验)。
每个场景注入后保持负载一段时间, 供页面观察; 结束自动清理。

用法: python phase6/drills/e2e_perf_scenarios.py <1-6|all> [config_id=1] [hold_sec]
场景: 1 CPU突高 / 2 锁风暴 / 3 计划劣化 / 4 连接风暴 / 5 IO突高 / 6 长事务
"""
import subprocess
import sys
import threading
import time

from _drill_common import DatabaseConfig, DbConnector, timezone, collect_once  # noqa

CONTAINER = 'db-aiops-mysql'
ROOT_PWD = 'root123'


def root_sql(sql, db=None):
    args = ['docker', 'exec', CONTAINER, 'mysql', '-uroot', f'-p{ROOT_PWD}', '-N']
    if db:
        args += ['-D', db]
    args += ['-e', sql]
    r = subprocess.run(args, capture_output=True, timeout=60)
    return r.stdout.decode().strip()


def _conn(cfg, db='testdb'):
    c = DbConnector.get_connection(cfg)
    cur = c.cursor()
    cur.execute(f"USE `{db}`")
    return c, cur


def scenario1_cpu(cfg, hold):
    """CPU 突高: 8 并发计算型 SQL。"""
    print(f"[场景1 CPU突高] {8} 并发 BENCHMARK, 持续 {hold}s")
    stop = threading.Event()

    def worker():
        c = DbConnector.get_connection(cfg); cur = c.cursor()
        while not stop.is_set():
            try:
                cur.execute("SELECT BENCHMARK(20000000, MD5(RAND()))"); cur.fetchall()
            except Exception:
                break
        c.close()
    ts = [threading.Thread(target=worker, daemon=True) for _ in range(8)]
    for t in ts:
        t.start()
    _hold_with_collect(cfg, hold, stop)
    print("[场景1] 走查: 性能主页 on_cpu 绿带隆起, 总AAS≈8; Top SQL 首行=BENCHMARK; "
          "运行中语句可见8条 → 申请终止其一")


def scenario2_lock(cfg, hold):
    """锁风暴: 1 持锁 + 5 waiter。"""
    print(f"[场景2 锁风暴] 1 持锁 + 5 waiter, 持续 {hold}s")
    c0, k = _conn(cfg)
    k.execute("CREATE TABLE IF NOT EXISTS perf_s2 (id INT PRIMARY KEY, v INT)")
    k.execute("INSERT INTO perf_s2 VALUES (1,0) ON DUPLICATE KEY UPDATE v=v"); c0.commit()
    holder, hc = _conn(cfg)
    hc.execute("BEGIN"); hc.execute("UPDATE perf_s2 SET v=v+1 WHERE id=1")
    stop = threading.Event()

    def waiter():
        c, cur = _conn(cfg)
        cur.execute(f"SET SESSION innodb_lock_wait_timeout={hold + 20}")
        try:
            cur.execute("UPDATE perf_s2 SET v=v+9 WHERE id=1"); c.commit()
        except Exception:
            pass
        c.close()
    for _ in range(5):
        threading.Thread(target=waiter, daemon=True).start()
    _hold_with_collect(cfg, hold, stop)
    holder.close(); c0.close()
    root_sql("DROP TABLE IF EXISTS perf_s2", db='testdb')
    print("[场景2] 走查: 主页 concurrency 暗红隆起+事故横幅; 阻塞分析树 影响面=5, "
          "锁明细 RECORD/X + testdb.perf_s2; ASH 过滤 concurrency → 对象 Top1=perf_s2")


def scenario3_plan(cfg, hold):
    """执行计划劣化: 建索引跑量 → 删索引跑量 → 触发 plan_change。"""
    print(f"[场景3 计划劣化] 建索引→跑量→删索引→跑量")
    c0, k = _conn(cfg)
    k.execute("DROP TABLE IF EXISTS perf_s3")
    k.execute("CREATE TABLE perf_s3 (id INT PRIMARY KEY AUTO_INCREMENT, uid INT, v INT, KEY idx_uid(uid))")
    k.execute("INSERT INTO perf_s3 (uid, v) SELECT FLOOR(RAND()*1000), FLOOR(RAND()*100) "
              "FROM information_schema.columns a, information_schema.columns b LIMIT 20000")
    c0.commit()
    sql = "SELECT COUNT(*) FROM perf_s3 WHERE uid = 42"
    print("  基线: 带索引跑量 40 次 + 采集")
    for _ in range(40):
        k.execute(sql); k.fetchall()
    _capture_plan(cfg, sql)
    collect_once(cfg); time.sleep(1); collect_once(cfg)
    print("  注入: DROP INDEX → 全表扫描跑量 40 次")
    k.execute("ALTER TABLE perf_s3 DROP INDEX idx_uid"); c0.commit()
    for _ in range(40):
        k.execute(sql); k.fetchall()
    _capture_plan(cfg, sql)   # 新计划 → plan_change 事件(降噪门槛允许则发)
    collect_once(cfg)
    _hold_with_collect(cfg, hold, threading.Event())
    c0.close()
    root_sql("DROP TABLE IF EXISTS perf_s3", db='testdb')
    print("[场景3] 走查: SQL详情→计划对比 旧ref索引扫描 vs 新ALL全表, diff高亮; "
          "优化建议给出重建 idx_uid; (若达降噪门槛)事故中心出 plan_change")


def scenario4_conn(cfg, hold):
    """连接风暴溯源: 大量空闲连接。"""
    n = int(root_sql("SELECT @@max_connections")) - 15
    n = min(n, 130)
    print(f"[场景4 连接风暴] {n} 空闲连接, 持续 {hold}s")
    holders = [DbConnector.get_connection(cfg) for _ in range(n)]
    stop = threading.Event()
    _hold_with_collect(cfg, hold, stop)
    for h in holders:
        try:
            h.close()
        except Exception:
            pass
    print("[场景4] 走查: 主页卡片/事故横幅; ASH 按 user_name 分面→注入账号第一; "
          "按 client_host 分面→定位来源主机")


def scenario5_io(cfg, hold):
    """I/O 突高: 大表全表聚合并发。"""
    print(f"[场景5 IO突高] 建 30万行表 + 4 并发全表聚合, 持续 {hold}s")
    c0, k = _conn(cfg)
    k.execute("DROP TABLE IF EXISTS perf_s5")
    k.execute("CREATE TABLE perf_s5 (id INT PRIMARY KEY AUTO_INCREMENT, pad CHAR(200), v INT)")
    for _ in range(6):
        k.execute("INSERT INTO perf_s5 (pad, v) SELECT RPAD('x',200,'x'), FLOOR(RAND()*100) "
                  "FROM information_schema.columns a, information_schema.columns b LIMIT 50000")
    c0.commit()
    stop = threading.Event()

    def scan():
        c, cur = _conn(cfg)
        while not stop.is_set():
            try:
                cur.execute("SELECT v, COUNT(*) FROM perf_s5 GROUP BY v"); cur.fetchall()
            except Exception:
                break
        c.close()
    for _ in range(4):
        threading.Thread(target=scan, daemon=True).start()
    _hold_with_collect(cfg, hold, stop)
    c0.close()
    root_sql("DROP TABLE IF EXISTS perf_s5", db='testdb')
    print("[场景5] 走查: 主页 user_io 蓝带隆起; 顶级活动 dim=等待事件 Top='Sending data'; "
          "dim=SQL 定位全表扫 → 详情 reads_delta 陡增")


def scenario6_longtrx(cfg, hold):
    """长事务拖库: BEGIN + UPDATE 不提交。"""
    hold = max(hold, 310)  # 需超过 LONG_TRX_WARN_SEC=300
    print(f"[场景6 长事务] BEGIN+UPDATE 不提交, 持续 {hold}s (>300s 触发)")
    c0, k = _conn(cfg)
    k.execute("CREATE TABLE IF NOT EXISTS perf_s6 (id INT PRIMARY KEY, v INT)")
    k.execute("INSERT INTO perf_s6 VALUES (1,0) ON DUPLICATE KEY UPDATE v=v"); c0.commit()
    holder, hc = _conn(cfg)
    hc.execute("BEGIN"); hc.execute("UPDATE perf_s6 SET v=v+1 WHERE id=1")
    _hold_with_collect(cfg, hold, threading.Event())
    holder.close(); c0.close()
    root_sql("DROP TABLE IF EXISTS perf_s6", db='testdb')
    print("[场景6] 走查: 事故中心 long_transaction 事件(会话/时长/最后SQL); "
          "ASH 过滤 application → 该会话样本连续; 事故方案含 kill → 一键执行 resolved")


def _capture_plan(cfg, sql):
    """按原生 DIGEST 采计划 (与 sql_stat/ASH 同 key, 详情页数据才连贯)。"""
    from monitor.plan_capture import capture
    from monitor.sqlfingerprint import unified_digest
    from monitor.db_connector import DbConnector
    native = None
    try:
        c = DbConnector.get_connection(cfg); cur = c.cursor()
        cur.execute("USE testdb")
        cur.execute("SELECT DIGEST FROM performance_schema.events_statements_summary_by_digest "
                    "WHERE DIGEST_TEXT LIKE 'SELECT COUNT%perf_s3%' ORDER BY LAST_SEEN DESC LIMIT 1")
        row = cur.fetchone()
        native = (row['DIGEST'] if isinstance(row, dict) else row[0]) if row else None
        c.close()
    except Exception:
        pass
    digest = unified_digest(cfg.db_type, native, sql)
    capture(cfg, digest, sql_text=sql, source='manual', db_name='testdb')
    return digest


def _hold_with_collect(cfg, hold, stop):
    """保持负载并周期采集, 让页面有连续数据。"""
    end = time.time() + hold
    while time.time() < end:
        try:
            collect_once(cfg)
        except Exception:
            pass
        time.sleep(min(10, max(1, end - time.time())))
    stop.set()
    time.sleep(1)


SCENARIOS = {
    '1': scenario1_cpu, '2': scenario2_lock, '3': scenario3_plan,
    '4': scenario4_conn, '5': scenario5_io, '6': scenario6_longtrx,
}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else '1'
    cid = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    hold = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    cfg = DatabaseConfig.objects.get(id=cid)
    print(f"=== 性能场景注入: {cfg.name} ({cfg.db_type}) ===")
    if cfg.db_type not in ('mysql', 'tdsql', 'gbase'):
        print("场景脚本仅支持 MySQL 家族")
        return 1
    keys = list(SCENARIOS) if which == 'all' else [which]
    for k in keys:
        if k not in SCENARIOS:
            print(f"未知场景 {k}")
            continue
        SCENARIOS[k](cfg, hold)
    print("=== 注入完成 ===")
    return 0


if __name__ == '__main__':
    sys.exit(main())
