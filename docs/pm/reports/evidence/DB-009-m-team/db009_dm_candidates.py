#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM 方言候选替换 SQL 实跑校验（候选 → 实库判定）。

只读：只执行 SELECT；凭据内存解密直用。
输出：docs/pm/reports/evidence/DB-009-m-team/db009_dm_candidates.json
"""
import json
import os
import sys
import time

sys.path.insert(0, r'C:\DB-AIOps')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
import pyodbc  # noqa: E402
from monitor.models import DatabaseConfig  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db009_dm_candidates.json')

CANDIDATES = [
    # --- 基础信息 ---
    ("c01_instance_mode", "SELECT MODE$ FROM V$INSTANCE"),
    ("c02_instance_status", "SELECT STATUS$ FROM V$INSTANCE"),
    ("c03_dsc_role", "SELECT DSC_ROLE FROM V$INSTANCE"),
    ("c04_db_role", "SELECT ROLE$ FROM V$DATABASE"),
    ("c05_db_arch", "SELECT ARCH_MODE FROM V$DATABASE"),
    ("c06_dsc_nodes", "SELECT DSC_NODES FROM V$DATABASE"),
    # --- 空间 ---
    ("c07_temp_space_type", "SELECT NAME, TYPE$ FROM V$TABLESPACE"),
    ("c08_temp_space_usage",
     "SELECT NAME, ROUND(TOTAL_SIZE * PAGE / 1024.0 / 1024.0, 2) as TOTAL_MB, "
     "ROUND(USED_SIZE * PAGE / 1024.0 / 1024.0, 2) as USED_MB "
     "FROM V$TABLESPACE WHERE TYPE$='TEMP'"),
    # --- 性能/统计 ---
    ("c09_sysstat_sql", "SELECT NAME, STAT_VAL FROM V$SYSSTAT WHERE UPPER(NAME) LIKE '%SQL%'"),
    ("c10_sysstat_cache", "SELECT NAME, STAT_VAL FROM V$SYSSTAT WHERE UPPER(NAME) LIKE '%CACHE%' OR UPPER(NAME) LIKE '%SORT%'"),
    ("c11_io_stat", "SELECT TYPE, COUNT, PAGES, COST FROM V$IO_STAT"),
    ("c12_sql_history_top",
     "SELECT TOP_SQL_TEXT, TIME_USED, AFFECTED_ROWS, N_LOGIC_READ, N_PHY_READ FROM V$SQL_HISTORY "
     "WHERE TIME_USED IS NOT NULL ORDER BY TIME_USED DESC"),
    ("c13_sql_history_group",
     "SELECT SQL_ID, SUBSTR(TOP_SQL_TEXT, 1, 200) as SQL_TEXT, COUNT(*) as EXECUTE_COUNT, "
     "SUM(TIME_USED) as ELAPSED_TIME, SUM(AFFECTED_ROWS) as ROWS_PROCESSED, "
     "SUM(N_LOGIC_READ + N_PHY_READ) as BUFFER_GETS "
     "FROM V$SQL_HISTORY GROUP BY SQL_ID, TOP_SQL_TEXT ORDER BY EXECUTE_COUNT DESC"),
    ("c14_sql_history_rownum",
     "SELECT * FROM (SELECT SQL_ID, SUBSTR(TOP_SQL_TEXT,1,200) as SQL_TEXT, COUNT(*) as EXECUTE_COUNT, "
     "SUM(TIME_USED) as AVG_TIME FROM V$SQL_HISTORY GROUP BY SQL_ID, TOP_SQL_TEXT ORDER BY EXECUTE_COUNT DESC) "
     "WHERE ROWNUM <= 10"),
    # --- 锁 ---
    ("c15_lock_join",
     "SELECT t.SESS_ID, s.USER_NAME, t.ID as TRANSACTION_ID, SUBSTR(s.SQL_TEXT,1,200) as SQL_TEXT, "
     "l.LMODE, l.BLOCKED FROM V$LOCK l LEFT JOIN V$TRX t ON l.TRX_ID = t.ID "
     "LEFT JOIN V$SESSIONS s ON t.SESS_ID = s.SESS_ID WHERE l.BLOCKED = 1"),
    ("c16_lock_count_blocked", "SELECT COUNT(*) FROM V$LOCK WHERE BLOCKED = 1"),
    # --- 会话详情 ---
    ("c17_session_detail",
     "SELECT SESS_ID, USER_NAME, STATE, CLNT_IP, CLNT_HOST, APPNAME, "
     "SUBSTR(SQL_TEXT, 1, 200) as SQL_TEXT, TRX_ID FROM V$SESSIONS "
     "WHERE USER_NAME IS NOT NULL ORDER BY STATE, SESS_ID LIMIT 100"),
    ("c18_session_limit_only",
     "SELECT SESS_ID, USER_NAME, STATE, CLNT_IP, CLNT_HOST, APPNAME, "
     "SUBSTR(SQL_TEXT, 1, 200) as SQL_TEXT, TRX_ID FROM V$SESSIONS "
     "WHERE USER_NAME IS NOT NULL AND ROWNUM <= 100 ORDER BY STATE, SESS_ID"),
    ("c19_session_run_status", "SELECT RUN_STATUS, COUNT(*) FROM V$SESSIONS GROUP BY RUN_STATUS"),
    # --- 事务 ---
    ("c20_long_trx",
     "SELECT t.ID as TRX_ID, t.SESS_ID, t.STATUS, (SYSDATE - s.CREATE_TIME) * 86400 as DURATION_SEC, "
     "SUBSTR(s.SQL_TEXT, 1, 200) as SQL_TEXT FROM V$TRX t LEFT JOIN V$SESSIONS s ON t.SESS_ID = s.SESS_ID "
     "WHERE (SYSDATE - s.CREATE_TIME) * 86400 > 60 ORDER BY DURATION_SEC DESC"),
    ("c21_trx_cols", "SELECT ID, SESS_ID, STATUS, LOCK_CNT FROM V$TRX"),
    # --- 死锁 ---
    ("c22_deadlock",
     "SELECT * FROM (SELECT SESS_ID, TRX_ID, SQL_TEXT, HAPPEN_TIME FROM V$DEADLOCK_HISTORY "
     "ORDER BY HAPPEN_TIME DESC) WHERE ROWNUM <= 5"),
    # --- 缓冲池 ---
    ("c23_buffer_group",
     "SELECT POOL_ID, SPACE_ID, FILE_ID, COUNT(*) as BUFFER_COUNT, SUM(ACCESS_CNT) as ACCESS_CNT "
     "FROM V$BUFFER GROUP BY POOL_ID, SPACE_ID, FILE_ID"),
    ("c24_buffer_pagetype", "SELECT PAGETYPE, COUNT(*) FROM V$BUFFER GROUP BY PAGETYPE"),
    ("c25_buffer_status", "SELECT STATUS, COUNT(*) FROM V$BUFFER GROUP BY STATUS"),
    # --- redo ---
    ("c26_rlog_almost_full", "SELECT ALMOST_FULL_STATUS, COUNT(*) FROM V$RLOG GROUP BY ALMOST_FULL_STATUS"),
    ("c27_rlog_basic", "SELECT CUR_FILE, CUR_OFFSET, TOTAL_SPACE, FREE_SPACE FROM V$RLOG"),
    # --- 归档 ---
    ("c28_arch_file_status", "SELECT STATUS, COUNT(*) FROM V$ARCH_FILE GROUP BY STATUS"),
    ("c29_arch_file_path", "SELECT PATH, STATUS, CREATE_TIME FROM V$ARCH_FILE"),
    # --- 对象统计 ---
    ("c30_top20_fixed",
     "SELECT * FROM (SELECT OWNER, TABLE_NAME, NUM_ROWS FROM DBA_TABLES "
     "WHERE OWNER NOT IN ('SYS', 'SYSTEM', 'SYSDBA', 'SYSAUDITOR', 'SYSSSO') "
     "ORDER BY NUM_ROWS DESC) WHERE ROWNUM <= 20"),
    ("c31_top20_with_size",
     "SELECT * FROM (SELECT OWNER, TABLE_NAME, NUM_ROWS, "
     "(SELECT ROUND(SUM(USED_SIZE * PAGE) / 1024 / 1024, 2) FROM V$TABLESPACE WHERE NAME = t.TABLESPACE_NAME) as SIZE_MB "
     "FROM DBA_TABLES t WHERE OWNER NOT IN ('SYS', 'SYSTEM', 'SYSDBA', 'SYSAUDITOR', 'SYSSSO') "
     "ORDER BY NUM_ROWS DESC) WHERE ROWNUM <= 20"),
    ("c32_dba_users_count", "SELECT COUNT(*) FROM DBA_USERS"),
    # --- ASH / sentinel ---
    ("c33_sentinel_dm_sessions",
     "SELECT count(*) FROM V$SESSIONS WHERE TYPE='USER'"),
    ("c34_sessions_type_col", "SELECT count(*) FROM V$SESSIONS WHERE CLNT_TYPE='USER'"),
    ("c35_ash_dm",
     "SELECT SESS_ID, USER_NAME, STATE, CLNT_IP, APPNAME, SUBSTR(SQL_TEXT,1,200) as SQL_TEXT "
     "FROM V$SESSIONS WHERE USER_NAME IS NOT NULL AND ROWNUM <= 100"),
]


def main():
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'target': {'id': cfg.id, 'host': cfg.host, 'port': cfg.port},
           'candidates': []}
    conn = pyodbc.connect('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                           % (cfg.host, cfg.port, cfg.username, cfg.get_password()),
                           timeout=15)
    cur = conn.cursor()
    for name, sql in CANDIDATES:
        rec = {'name': name, 'sql': sql}
        try:
            cur.execute(sql)
            rows = cur.fetchmany(3)
            rec['ok'] = True
            rec['rows'] = len(rows)
            rec['sample'] = [[str(c)[:32] for c in r] for r in rows]
        except Exception as e:
            rec['ok'] = False
            rec['error'] = str(e)[:200].replace('\n', ' ')
        out['candidates'].append(rec)
    conn.close()
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    for r in out['candidates']:
        if r['ok']:
            print('OK   %-24s rows=%s %s' % (r['name'], r['rows'], str(r['sample'])[:110]))
        else:
            print('FAIL %-24s %s' % (r['name'], r['error'][:130]))
    print('summary: ok=%d fail=%d' % (sum(1 for r in out['candidates'] if r['ok']),
                                      sum(1 for r in out['candidates'] if not r['ok'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
