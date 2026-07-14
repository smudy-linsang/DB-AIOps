# -*- coding: utf-8 -*-
"""6C 破坏演练 4/8: 复制中断全自动闭环 (真实主从, 真实断复制)。

环境: 临时起 mysql:8.0 从库容器挂到 db-aiops_default 网络, file/pos 复制
      db-aiops-mysql, 纳管为临时实例。
注入: 从库 STOP REPLICA SQL_THREAD
链路: 采集 L1 repl_broken(critical) → 事故 → 守护自动诊断 → PB-REPL-RESTART
      (STOP SLAVE; START SLAVE) → 验证 repl_running==1 → resolved
清理: 删临时实例配置(级联事件/事故) → 删容器 → 删复制账号。

用法: python phase6/drills/e2e_repl.py
"""
import subprocess
import sys
import threading
import time

from _drill_common import (  # noqa
    DatabaseConfig, timezone,
    assert_1_5_10, collect_once, pump_collect, run_playbook,
    wait_incident, wait_incident_status, wait_run_final,
)

SOURCE = 'db-aiops-mysql'
REPLICA = 'db-aiops-mysql-replica'
NET = 'db-aiops_default'
REPL_PORT = 3307
REPL_USER, REPL_PWD = 'repl', 'Repl123!'
ROOT_PWD = 'root123'


def sql_in(container, sql, cols=False):
    # -h127.0.0.1 强制 TCP: 避开 mysql 镜像初始化阶段只听 socket 的临时服务
    # -N 去列名; \G 纵向输出需要列名时传 cols=True
    args = ['docker', 'exec', container, 'mysql', '-h127.0.0.1', '-uroot',
            f'-p{ROOT_PWD}', '-e', sql]
    if not cols:
        args.insert(-2, '-N')
    r = subprocess.run(args, capture_output=True, timeout=60)
    out = r.stdout.decode().strip()
    err = '\n'.join(l for l in r.stderr.decode().splitlines()
                    if 'Warning' not in l).strip()
    if r.returncode != 0:
        raise RuntimeError(f"{container}: {err or out}")
    return out


def replica_status():
    raw = sql_in(REPLICA, "SHOW REPLICA STATUS\\G", cols=True)
    d = {}
    for line in raw.splitlines():
        if ':' in line:
            k, _, v = line.partition(':')
            d[k.strip()] = v.strip()
    return d


def main():
    print("=== 演练4 复制中断: 临时主从 ===")
    cfg = None
    try:
        # ---- 环境: 复制账号 + 从库容器 + 建立复制 ----
        sql_in(SOURCE, f"CREATE USER IF NOT EXISTS '{REPL_USER}'@'%' "
                       f"IDENTIFIED WITH mysql_native_password BY '{REPL_PWD}'; "
                       f"GRANT REPLICATION SLAVE ON *.* TO '{REPL_USER}'@'%';")
        subprocess.run(['docker', 'rm', '-f', REPLICA], capture_output=True)
        subprocess.run(
            ['docker', 'run', '-d', '--name', REPLICA, '--network', NET,
             '-p', f'{REPL_PORT}:3306', '-e', f'MYSQL_ROOT_PASSWORD={ROOT_PWD}',
             'mysql:8.0', '--server-id=4207'], check=True, capture_output=True)
        print("从库容器启动中...")
        for _ in range(60):
            try:
                sql_in(REPLICA, "SELECT 1")
                break
            except Exception:
                time.sleep(3)
        else:
            print("[FAIL] 从库容器未就绪")
            return 1

        fp = sql_in(SOURCE, "SHOW MASTER STATUS").split('\t')
        log_file, log_pos = fp[0], fp[1]
        sql_in(REPLICA,
               f"CHANGE REPLICATION SOURCE TO SOURCE_HOST='{SOURCE}', SOURCE_PORT=3306, "
               f"SOURCE_USER='{REPL_USER}', SOURCE_PASSWORD='{REPL_PWD}', "
               f"SOURCE_LOG_FILE='{log_file}', SOURCE_LOG_POS={log_pos}; "
               f"START REPLICA;")
        time.sleep(3)
        st = replica_status()
        print(f"复制状态: IO={st.get('Replica_IO_Running')} SQL={st.get('Replica_SQL_Running')}")
        if st.get('Replica_IO_Running') != 'Yes' or st.get('Replica_SQL_Running') != 'Yes':
            print(f"[FAIL] 复制未跑通: {st.get('Last_IO_Error') or st.get('Last_SQL_Error')}")
            return 1

        # ---- 纳管临时实例 + 基线采集 ----
        cfg = DatabaseConfig(name='演练-MySQL复制副本', db_type='mysql',
                             host='127.0.0.1', port=REPL_PORT,
                             username='root', is_active=True)
        cfg.set_password(ROOT_PWD)
        cfg.save()
        print(f"已纳管临时实例 config_id={cfg.id}")
        collect_once(cfg)  # 基线快照 (复制正常)

        # ---- 注入: 真实断复制 ----
        t0 = timezone.now()
        sql_in(REPLICA, "STOP REPLICA SQL_THREAD")
        print("已注入: STOP REPLICA SQL_THREAD")
        collect_once(cfg)

        # ---- 发现 ----
        inc = wait_incident(cfg, 'replication', since=t0, timeout=60, signal='repl_broken')
        if not inc:
            print("[FAIL] 未生成复制事故")
            return 1
        print(f"[发现] {inc.incident_id} [{inc.priority}] {inc.title} "
              f"t_detect={inc.t_detect_sec}s")

        # ---- 诊断 ----
        if not wait_incident_status(inc, ('plan_ready',), timeout=90, label='plan_ready'):
            return 1
        print(f"[诊断] t_plan={inc.t_plan_sec}s 方案={len(inc.plans)}")

        # ---- 处置: 重启复制线程 ----
        run = run_playbook(inc, 'PB-REPL-RESTART')
        if run.status == 'failed':
            print(f"[FAIL] Playbook 执行失败: {run.error_message}")
            return 1

        # ---- 验证 ----
        stop_pump = threading.Event()
        pump_collect(cfg, stop_pump, interval=10)
        try:
            final = wait_run_final(run, timeout=240)
            print(f"[验证] run={final}")
            wait_incident_status(inc, ('resolved',), timeout=30, label='resolved')
        finally:
            stop_pump.set()
            time.sleep(1)

        st = replica_status()
        print(f"复制恢复: IO={st.get('Replica_IO_Running')} SQL={st.get('Replica_SQL_Running')}")
        return 0 if assert_1_5_10(inc, run) else 1
    finally:
        # 先删配置(哨兵/级联安全), 再删容器与账号
        if cfg and cfg.id:
            try:
                cfg.delete()
                print("(清理: 临时实例配置已删除)")
            except Exception as e:
                print(f"(清理配置失败: {e})")
        subprocess.run(['docker', 'rm', '-f', REPLICA], capture_output=True)
        try:
            sql_in(SOURCE, f"DROP USER IF EXISTS '{REPL_USER}'@'%';")
        except Exception:
            pass
        print("(清理: 从库容器与复制账号已删除)")


if __name__ == "__main__":
    sys.exit(main())
