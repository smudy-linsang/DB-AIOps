# -*- coding: utf-8 -*-
"""6C 破坏演练 3/8: 表空间高水位全自动闭环 (Oracle, 真实灌满)。

注入: 建 50M 定容演练表空间 E2E_SPACE, 真实灌数据至 >=95%
链路: 采集 L1 space_high(critical, 按表空间分键) → 事故 → 守护自动诊断 →
      PB-SPACE-EXTEND ADD DATAFILE(OMF) → 验证该表空间 used_pct<90 → resolved
环境准备(幂等, sysdba): 处置账号授 CREATE/ALTER/DROP TABLESPACE;
      PDB 开 db_create_file_dest (OMF, ADD DATAFILE 免文件名) — 结束后还原。

用法: python phase6/drills/e2e_space.py [config_id=3]
"""
import subprocess
import sys
import threading
import time

from _drill_common import (  # noqa
    DatabaseConfig, DbConnector, timezone,
    assert_1_5_10, close_stale_incidents, collect_once,
    pump_collect, run_playbook, wait_incident, wait_incident_status,
    wait_run_final,
)

CONFIG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 3
TBS = 'E2E_SPACE'
CONTAINER = 'db-aiops-oracle'
OMF_DEST = '/opt/oracle/oradata/XE/XEPDB1'


def sysdba(sql):
    """容器内 sysdba 执行 (环境准备/兜底清理用, 不属产品链路)。"""
    script = f"ALTER SESSION SET CONTAINER=XEPDB1;\n{sql}\nEXIT;\n"
    r = subprocess.run(
        ['docker', 'exec', '-i', CONTAINER,
         '/opt/oracle/product/21c/dbhomeXE/bin/sqlplus', '-s', '/', 'as', 'sysdba'],
        input=script.encode(), capture_output=True, timeout=60)
    out = r.stdout.decode()
    if 'ORA-' in out and 'ORA-01918' not in out:
        print(f"  [sysdba] {out.strip()[:300]}")
    return out


def used_pct(cur, tbs=TBS):
    cur.execute(f"""
        SELECT ROUND((a.total_mb - NVL(b.free_mb, 0)) / NULLIF(a.total_mb, 0) * 100, 2)
        FROM (SELECT SUM(bytes)/1024/1024 total_mb FROM dba_data_files
              WHERE tablespace_name='{tbs}') a,
             (SELECT SUM(bytes)/1024/1024 free_mb FROM dba_free_space
              WHERE tablespace_name='{tbs}') b""")
    row = cur.fetchone()
    return row[0] if row else None


def main():
    cfg = DatabaseConfig.objects.get(id=CONFIG_ID)
    print(f"=== 演练3 表空间高水位: {cfg.name} ({cfg.db_type}) ===")
    if cfg.db_type != 'oracle':
        print("仅支持 Oracle")
        return 1

    # ---- 环境准备 (幂等): 处置权限 + OMF ----
    sysdba(f"GRANT CREATE TABLESPACE, ALTER TABLESPACE, DROP TABLESPACE TO {cfg.username};")
    sysdba(f"ALTER SYSTEM SET db_create_file_dest='{OMF_DEST}' SCOPE=BOTH;")

    conn = DbConnector.get_connection(cfg)
    cur = conn.cursor()
    close_stale_incidents(cfg, f'{cfg.id}:capacity')

    stop_pump = threading.Event()
    try:
        # ---- 注入: 50M 定容表空间灌到 >=95% ----
        try:
            cur.execute(f"DROP TABLESPACE {TBS} INCLUDING CONTENTS AND DATAFILES")
        except Exception:
            pass
        cur.execute(f"CREATE TABLESPACE {TBS} DATAFILE SIZE 50M AUTOEXTEND OFF")
        cur.execute(f"CREATE TABLE e2e_space_fill (filler CHAR(2000)) TABLESPACE {TBS}")
        t0 = timezone.now()
        pct = 0
        while pct is None or pct < 95.5:
            try:
                cur.execute("INSERT INTO e2e_space_fill "
                            "SELECT RPAD('x', 2000, 'x') FROM dual CONNECT BY LEVEL <= 1500")
                conn.commit()
            except Exception as e:
                if 'ORA-01653' in str(e) or 'ORA-01654' in str(e):
                    conn.rollback()
                    break  # 已满到无法扩展, 水位必然超阈值
                raise
            pct = used_pct(cur)
        print(f"已灌满: {TBS} used_pct={used_pct(cur)}%")

        # ---- 发现 ----
        collect_once(cfg)
        inc = wait_incident(cfg, 'capacity', since=t0, timeout=60,
                            signal='space_high', event_dedup_contains=TBS)
        if not inc:
            print("[FAIL] 未生成容量事故")
            return 1
        print(f"[发现] {inc.incident_id} [{inc.priority}] {inc.title} "
              f"t_detect={inc.t_detect_sec}s")

        # ---- 诊断 (守护自动) ----
        if not wait_incident_status(inc, ('plan_ready',), timeout=90, label='plan_ready'):
            return 1
        print(f"[诊断] t_plan={inc.t_plan_sec}s 方案={len(inc.plans)}")

        # ---- 处置: 扩容 ----
        run = run_playbook(inc, 'PB-SPACE-EXTEND',
                           params={'tablespace': TBS, 'add_mb': 100})
        if run.status == 'failed':
            print(f"[FAIL] Playbook 执行失败: {run.error_message}")
            return 1
        print(f"扩容后 used_pct={used_pct(cur)}%")

        # ---- 验证 ----
        pump_collect(cfg, stop_pump, interval=10)
        final = wait_run_final(run, timeout=240)
        print(f"[验证] run={final}")
        wait_incident_status(inc, ('resolved',), timeout=30, label='resolved')

        return 0 if assert_1_5_10(inc, run) else 1
    finally:
        stop_pump.set()
        try:
            cur.execute(f"DROP TABLESPACE {TBS} INCLUDING CONTENTS AND DATAFILES")
            print(f"(清理: 已删除演练表空间 {TBS})")
        except Exception as e:
            print(f"(清理失败, 请手工 DROP TABLESPACE {TBS}: {e})")
        sysdba("ALTER SYSTEM RESET db_create_file_dest SCOPE=BOTH;")
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
