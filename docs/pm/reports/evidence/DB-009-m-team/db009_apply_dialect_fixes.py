#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM 方言对齐补丁（对 DM8 实库实测结果做逐条精确替换）。

每条替换都要求 old 片段**恰好出现一次**，否则报错退出，避免误改。
执行：.venv\\Scripts\\python.exe docs\\pm\\reports\\evidence\\DB-009-m-team\\db009_apply_dialect_fixes.py
"""
import io
import re
import sys

ROOT = r'C:\DB-AIOps'

CHECKER = ROOT + r'\monitor\checkers\dm.py'
SENTINEL = ROOT + r'\monitor\sentinel.py'
MONITOR = ROOT + r'\monitor\management\commands\start_monitor.py'

CHECKER_PATCHES = [
    # 1) V$INSTANCE 无 MODE 列，实际为 MODE$
    ("cur.execute(\"SELECT MODE FROM V$INSTANCE\")",
     "cur.execute(\"SELECT MODE$ FROM V$INSTANCE\")", 2),
    # 2) V$SESSION_WAIT 视图不存在 → 锁等待口径
    ("""            cur.execute(
                "SELECT count(*) FROM V$SESSION_WAIT WHERE EVENT != 'Idle'"
            )
            session_wait_count = int(cur.fetchone()[0])""",
     """            # DM8 无 V$SESSION_WAIT 视图（DB-009 实测 -2106）→ 改用锁等待口径
            cur.execute("SELECT COUNT(*) FROM V$LOCK WHERE BLOCKED = 1")
            session_wait_count = int(cur.fetchone()[0])""", 1),
    # 3) 等待事件 → 锁类型分组
    ("""            cur.execute(\"\"\"
                SELECT EVENT, COUNT(*) as CNT
                FROM V$SESSION_WAIT
                WHERE EVENT != 'Idle'
                GROUP BY EVENT
                ORDER BY CNT DESC
                LIMIT 10
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT LTYPE, COUNT(*) as CNT
                FROM V$LOCK
                WHERE BLOCKED = 1
                GROUP BY LTYPE
                ORDER BY CNT DESC
                LIMIT 10
            \"\"\")""", 1),
    # 4) V$TEMP_SPACE_USAGE 视图不存在 → V$TABLESPACE(TYPE$='TEMP')
    ("""            cur.execute(\"\"\"
                SELECT NAME, ROUND(TOTAL_SIZE * PAGE / 1024.0 / 1024.0, 2) as TOTAL_MB
                FROM V$TEMP_SPACE_USAGE
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT 
                    NAME,
                    ROUND(TOTAL_SIZE * PAGE / 1024.0 / 1024.0, 2) as TOTAL_MB
                FROM (
                    SELECT 
                        t.NAME,
                        t.TOTAL_SIZE,
                        (SELECT CAST(VALUE AS INT) FROM V$PARAMETER WHERE NAME = 'GLOBAL_PAGE_SIZE') as PAGE
                    FROM V$TABLESPACE t WHERE t.TYPE$ = 2
                )
            \"\"\")""", 1),
    # 5) V$SYSTEM_INFO 不存在 → V$SYSSTAT
    ("""            cur.execute(
                "SELECT VALUE FROM V$SYSTEM_INFO WHERE NAME='SQL_COUNT'"
            )
            sql_count = int(cur.fetchone()[0])""",
     """            cur.execute(
                "SELECT STAT_VAL FROM V$SYSSTAT WHERE NAME='sql executed count'"
            )
            sql_count = int(cur.fetchone()[0] or 0)""", 1),
    # 6) V$SYSTEM_INFO 不存在 → V$SYSSTAT
    ("""            cur.execute(
                "SELECT VALUE FROM V$SYSTEM_INFO WHERE NAME='TRAN_COUNT'"
            )
            tran_count = int(cur.fetchone()[0])""",
     """            cur.execute(
                "SELECT STAT_VAL FROM V$SYSSTAT WHERE NAME='transaction total count'"
            )
            tran_count = int(cur.fetchone()[0] or 0)""", 1),
    # 7) V$BUFFER 无 REQUEST_R_SORT/CACHE_R_SORT → V$SYSSTAT 缓存命中计数
    ("""            cur.execute(\"\"\"
                SELECT 
                    ROUND((1 - A.CACHE_R_SORT / A.REQUEST_R_SORT) * 100, 2) as CACHE_HIT_RATIO
                FROM (
                    SELECT 
                        SUM(REQUEST_R_SORT) as REQUEST_R_SORT,
                        SUM(CACHE_R_SORT) as CACHE_R_SORT
                    FROM V$BUFFER
                ) A
            \"\"\")
            cache_hit_ratio = float(cur.fetchone()[0])""",
     """            # DM8 的 V$BUFFER 无 REQUEST_R_SORT/CACHE_R_SORT 列（DB-009 实测 -2111）
            # → 改用 V$SYSSTAT 的 SQL 缓存命中计数口径
            cur.execute(
                "SELECT NAME, STAT_VAL FROM V$SYSSTAT "
                "WHERE NAME IN ('sql cache hit count', 'sql get total count')"
            )
            _cache_stat = {str(k): float(v or 0) for k, v in cur.fetchall()}
            _hit = _cache_stat.get('sql cache hit count', 0)
            _get = _cache_stat.get('sql get total count', 0)
            cache_hit_ratio = round(_hit / _get * 100, 2) if _get else 0""", 1),
    # 8) V$LOCK 无 SESS_ID → 经 V$TRX 关联
    ("""            cur.execute(\"\"\"
                SELECT 
                    s.SESS_ID,
                    s.USER_NAME,
                    s.TRANSACTION_ID,
                    s.SQL_TEXT,
                    l.LMODE,
                    l.BLOCKED
                FROM V$LOCK l
                LEFT JOIN V$SESSIONS s ON l.SESS_ID = s.SESS_ID
                WHERE l.BLOCKED = 1
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT 
                    t.SESS_ID,
                    s.USER_NAME,
                    t.ID as TRANSACTION_ID,
                    SUBSTR(s.SQL_TEXT, 1, 200) as SQL_TEXT,
                    l.LMODE,
                    l.BLOCKED
                FROM V$LOCK l
                LEFT JOIN V$TRX t ON l.TRX_ID = t.ID
                LEFT JOIN V$SESSIONS s ON t.SESS_ID = s.SESS_ID
                WHERE l.BLOCKED = 1
            \"\"\")""", 1),
    # 9) V$SESSIONS 无 PROGRAM/IP/HOST/TRANSACTION_ID 列
    ("""                SELECT 
                    SESS_ID, USER_NAME, STATE, PROGRAM, IP, HOST,
                    SUBSTR(SQL_TEXT, 1, 200) as SQL_TEXT,
                    TRX_ID, TRANSACTION_ID
                FROM V$SESSIONS""",
     """                SELECT 
                    SESS_ID, USER_NAME, STATE, CLNT_IP, CLNT_HOST, APPNAME,
                    SUBSTR(SQL_TEXT, 1, 200) as SQL_TEXT,
                    TRX_ID
                FROM V$SESSIONS""", 1),
    ("""                    "program": row[3] or 'N/A',
                    "ip": row[4] or 'N/A',
                    "host": row[5] or 'N/A',
                    "sql_text": (row[6] or 'N/A')[:200],
                    "trx_id": str(row[7]) if row[7] else 'N/A',
                    "transaction_id": str(row[8]) if row[8] else 'N/A'""",
     """                    "program": row[5] or 'N/A',
                    "ip": row[3] or 'N/A',
                    "host": row[4] or 'N/A',
                    "sql_text": (row[6] or 'N/A')[:200],
                    "trx_id": str(row[7]) if row[7] else 'N/A',
                    "transaction_id": str(row[7]) if row[7] else 'N/A'""", 1),
    # 10) V$SQL_HISTORY 无 SQL_TEXT/EXECUTE_COUNT/AVG_TIME 列
    ("""            cur.execute(\"\"\"
                SELECT SQL_TEXT, EXECUTE_COUNT, AVG_TIME
                FROM (
                    SELECT SQL_TEXT, EXECUTE_COUNT, AVG_TIME
                    FROM V$SQL_HISTORY
                    ORDER BY EXECUTE_COUNT DESC
                )
                WHERE ROWNUM <= 10
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT * FROM (
                    SELECT SQL_ID, SUBSTR(TOP_SQL_TEXT, 1, 200) as SQL_TEXT,
                           COUNT(*) as EXECUTE_COUNT, SUM(TIME_USED) as AVG_TIME
                    FROM V$SQL_HISTORY
                    GROUP BY SQL_ID, TOP_SQL_TEXT
                    ORDER BY EXECUTE_COUNT DESC
                )
                WHERE ROWNUM <= 10
            \"\"\")""", 1),
    ("""                slow_queries.append({
                    "sql_text": (row[0] or 'N/A')[:200],
                    "execute_count": int(row[1]),
                    "avg_time": int(row[2]) if row[2] else 0
                })""",
     """                slow_queries.append({
                    "sql_id": str(row[0]),
                    "sql_text": (row[1] or 'N/A')[:200],
                    "execute_count": int(row[2] or 0),
                    "avg_time": int(row[3]) if row[3] else 0
                })""", 1),
    # 11) V$SQLSTATS 视图不存在 → V$SQL_HISTORY 聚合
    ("""            cur.execute(\"\"\"
                SELECT SQL_TEXT, EXECUTE_COUNT
                FROM V$SQLSTATS
                ORDER BY EXECUTE_COUNT DESC
                LIMIT 10
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT * FROM (
                    SELECT SQL_ID, SUBSTR(TOP_SQL_TEXT, 1, 200) as SQL_TEXT,
                           COUNT(*) as EXECUTE_COUNT
                    FROM V$SQL_HISTORY
                    GROUP BY SQL_ID, TOP_SQL_TEXT
                    ORDER BY EXECUTE_COUNT DESC
                )
                WHERE ROWNUM <= 10
            \"\"\")""", 1),
    ("""                top_sql.append({
                    "sql_text": (row[0] or 'N/A')[:200],
                    "execute_count": int(row[1])
                })""",
     """                top_sql.append({
                    "sql_id": str(row[0]),
                    "sql_text": (row[1] or 'N/A')[:200],
                    "execute_count": int(row[2] or 0)
                })""", 1),
    # 12) V$BUFFER 无 GROUP_ID/GROUP_NAME/... 列
    ("""            cur.execute(\"\"\"
                SELECT GROUP_ID, GROUP_NAME, NODE_NUM, BUFFER_SIZE, BUFFER_COUNT,
                       FREE_COUNT, ACTIVE_COUNT, DIRTY_COUNT
                FROM V$BUFFER
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT POOL_ID, SPACE_ID, FILE_ID, COUNT(*) as BUFFER_COUNT,
                       SUM(ACCESS_CNT) as ACCESS_CNT
                FROM V$BUFFER
                GROUP BY POOL_ID, SPACE_ID, FILE_ID
            \"\"\")""", 1),
    ("""                buffer_pools.append({
                    "group_id": int(row[0]),
                    "group_name": row[1] or 'N/A',
                    "node_num": int(row[2]),
                    "buffer_size": int(row[3]),
                    "buffer_count": int(row[4]),
                    "free_count": int(row[5]),
                    "active_count": int(row[6]),
                    "dirty_count": int(row[7])
                })""",
     """                buffer_pools.append({
                    "group_id": int(row[0] or 0),
                    "group_name": str(row[1] or 'N/A'),
                    "node_num": int(row[2] or 0),
                    "buffer_size": None,
                    "buffer_count": int(row[3] or 0),
                    "free_count": None,
                    "active_count": None,
                    "dirty_count": None,
                    "access_count": int(row[4] or 0)
                })""", 1),
    # 13) V$DATABASE 无 ARCHIVE_MODE 列（实际为 ARCH_MODE）
    ("cur.execute(\"SELECT ARCHIVE_MODE FROM V$DATABASE\")",
     "cur.execute(\"SELECT ARCH_MODE FROM V$DATABASE\")", 1),
    # 14) V$ARCH_DEST 不存在 → V$ARCH_FILE
    ("""            cur.execute(
                "SELECT DEST_ID, STATUS, DEST_NAME FROM V$ARCH_DEST"
            )
            archive_dest = []
            for row in cur.fetchall():
                archive_dest.append({
                    "dest_id": int(row[0]),
                    "status": row[1] or 'N/A',
                    "dest_name": row[2] or 'N/A'
                })""",
     """            cur.execute(
                "SELECT PATH, STATUS, CREATE_TIME FROM V$ARCH_FILE"
            )
            archive_dest = []
            for row in cur.fetchall():
                archive_dest.append({
                    "dest_id": 0,
                    "status": row[1] or 'N/A',
                    "dest_name": row[0] or 'N/A',
                    "create_time": str(row[2] or 'N/A')
                })""", 1),
    # 15) V$DATABASE 无 MODE$ 列（实际为 ROLE$）
    ("""            cur.execute("SELECT MODE$ FROM V$DATABASE")
            dm_database_mode = cur.fetchone()[0]""",
     """            cur.execute("SELECT ROLE$ FROM V$DATABASE")
            _db_role = cur.fetchone()[0]
            # DM8 用 ROLE$ 表达库角色（0 = PRIMARY），V$DATABASE 无 MODE$ 列
            dm_database_mode = ('PRIMARY' if str(_db_role) in ('0', 'PRIMARY')
                                else str(_db_role))""", 1),
    # 16) V$ARCH_DEST 不存在 → V$ARCH_FILE
    ("""            cur.execute(\"\"\"
                SELECT DEST_ID, DEST_NAME, STATUS, ARCH_TYPE, ARCH_SEQ, ARCH_SPACE, 
                       ARCH_FILE, ARCH_TIMELY, IS_LOGICAL, SYNCHRONIZED
                FROM V$ARCH_DEST WHERE STATUS != 'INACTIVE'
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT ARCH_SEQ, PATH, STATUS, CREATE_TIME, CLOSE_TIME, DB_MAGIC
                FROM V$ARCH_FILE
            \"\"\")""", 1),
    ("""                realtime_archive_dest.append({
                    "dest_id": int(row[0]) if row[0] else 0,
                    "dest_name": row[1] or 'N/A',
                    "status": row[2] or 'N/A',
                    "arch_type": row[3] or 'N/A',
                    "arch_seq": int(row[4]) if row[4] else 0,
                    "arch_space": int(row[5]) if row[5] else 0,
                    "arch_file": row[6] or 'N/A',
                    "arch_timely": row[7] or 'N/A',
                    "is_logical": row[8] or 'N/A',
                    "synchronized": row[9] or 'N/A'
                })""",
     """                realtime_archive_dest.append({
                    "dest_id": 0,
                    "dest_name": row[1] or 'N/A',
                    "status": row[2] or 'N/A',
                    "arch_type": 'N/A',
                    "arch_seq": int(row[0]) if row[0] else 0,
                    "arch_space": 0,
                    "arch_file": row[1] or 'N/A',
                    "arch_timely": str(row[3] or 'N/A'),
                    "is_logical": 'N/A',
                    "synchronized": 'N/A'
                })""", 1),
    # 17) V$RLOG 无 SRC_INSTANCE 等列
    ("""            cur.execute(\"\"\"
                SELECT SRC_INSTANCE, DEST_INSTANCE, ARCH_DEST, ARCH_SEQ, ARCH_SEQ_ALL,
                       APPLY_SEQ, APPLY_SEQ_ALL, SYNC_STATUS
                FROM V$RLOG
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT DB_MAGIC, CUR_FILE, CUR_OFFSET, TOTAL_SPACE, FREE_SPACE,
                       ALMOST_FULL_STATUS
                FROM V$RLOG
            \"\"\")""", 1),
    ("""                rlog_sync_status.append({
                    "src_instance": row[0] or 'N/A',
                    "dest_instance": row[1] or 'N/A',
                    "arch_dest": row[2] or 'N/A',
                    "arch_seq": int(row[3]) if row[3] else 0,
                    "arch_seq_all": int(row[4]) if row[4] else 0,
                    "apply_seq": int(row[5]) if row[5] else 0,
                    "apply_seq_all": int(row[6]) if row[6] else 0,
                    "sync_status": row[7] or 'N/A'
                })""",
     """                rlog_sync_status.append({
                    "src_instance": str(row[0] or 'N/A'),
                    "dest_instance": 'N/A',
                    "arch_dest": 'N/A',
                    "arch_seq": int(row[1] or 0),
                    "arch_seq_all": 0,
                    "apply_seq": int(row[2] or 0),
                    "apply_seq_all": 0,
                    "sync_status": row[5] or 'N/A'
                })""", 1),
    # 18) V$DEST_PENDING 不存在（DW 主备专用）→ 合法空集查询，不伪造
    ("""            cur.execute(\"\"\"
                SELECT DST_INST_NAME, APPLY_STATUS, APPLY_DELAY, APPLY_RST_SEQ, 
                       APPLY_RST_TIME, LAST_ARCH_SEQ
                FROM V$DEST_PENDING
            \"\"\")""",
     """            # DM8 单实例无 V$DEST_PENDING（DW 主备专用，实测 -2106）
            # → 合法空集查询占位，不伪造备库延迟数据
            cur.execute(\"\"\"
                SELECT PATH, STATUS, CREATE_TIME, CLOSE_TIME, ARCH_SEQ, DB_MAGIC
                FROM V$ARCH_FILE WHERE 1=0
            \"\"\")""", 1),
    # 19) V$CLUSTER 不存在 → V$INSTANCE / V$DATABASE 实际列
    ("""            cur.execute(\"\"\"
                SELECT INSTANCE_NAME, INSTANCE_ID, HOST_NAME, PORT_NUM, 
                       STATUS, IS_PRIMARY, UPTIME
                FROM V$CLUSTER
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT INSTANCE_NAME, INSTANCE_NUMBER, HOST_NAME, NULL as PORT_NUM,
                       STATUS$, NULL as IS_PRIMARY, NULL as UPTIME
                FROM V$INSTANCE
            \"\"\")""", 1),
    ("""                dsc_cluster_info.append({
                    "instance_name": row[0] or 'N/A',
                    "instance_id": int(row[1]) if row[1] else 0,
                    "host_name": row[2] or 'N/A',
                    "port_num": int(row[3]) if row[3] else 0,
                    "status": row[4] or 'N/A',
                    "is_primary": int(row[5]) if row[5] else 0,
                    "uptime": int(row[6]) if row[6] else 0
                })""",
     """                dsc_cluster_info.append({
                    "instance_name": row[0] or 'N/A',
                    "instance_id": int(row[1]) if row[1] else 0,
                    "host_name": row[2] or 'N/A',
                    "port_num": int(row[3]) if row[3] else 0,
                    "status": row[4] or 'N/A',
                    "is_primary": int(row[5]) if row[5] else 0,
                    "uptime": int(row[6]) if row[6] else 0
                })
            dsc_node_count = len(dsc_cluster_info)""", 1),
    # 20) V$DSC_INSTANCES 不存在 → 合法空集
    ("""            cur.execute(\"\"\"
                SELECT INST_ID, INST_NAME, CLUSTER_STATE, DB_MAGIC, 
                       RLOG_SEND_OFFSET, RLOG_PKG_SND_COUNT
                FROM V$DSC_INSTANCES
            \"\"\")""",
     """            # DM8 无 V$DSC_INSTANCES（实测 -2106）→ 合法空集，不伪造节点明细
            cur.execute(\"\"\"
                SELECT ARCH_SEQ, PATH, STATUS, CREATE_TIME, CLOSE_TIME, DB_MAGIC
                FROM V$ARCH_FILE WHERE 1=0
            \"\"\")""", 1),
    # 21/22) V$GLOBAL_LATCH 不存在 → V$LOCK 实际列
    ("""            cur.execute(\"\"\"
                SELECT LOCK_ID, LOCK_TYPE, OWNER_INST, OWNER_LRU,
                       BLOCKING_LRU, BLOCKING_INST
                FROM V$GLOBAL_LATCH
            \"\"\")""",
     """            # DM8 无 V$GLOBAL_LATCH（实测 -2106）→ 合法空集
            cur.execute(\"\"\"
                SELECT PATH, STATUS, CREATE_TIME, CLOSE_TIME, ARCH_SEQ, DB_MAGIC
                FROM V$ARCH_FILE WHERE 1=0
            \"\"\")""", 1),
    ("""            cur.execute(
                "SELECT COUNT(*) FROM V$GLOBAL_LATCH WHERE BLOCKING_LRU > 0"
            )
            dsc_lock_contention_count = int(cur.fetchone()[0])""",
     """            cur.execute(
                "SELECT COUNT(*) FROM V$LOCK WHERE BLOCKED = 1"
            )
            dsc_lock_contention_count = int(cur.fetchone()[0])""", 1),
    # 23) V$LOG 不存在 → V$ARCH_FILE（归档日志文件数）
    ("""            cur.execute("SELECT count(*) FROM V$LOG")
            log_count = int(cur.fetchone()[0])""",
     """            # DM8 无 V$LOG 视图（实测 -2106）→ 归档日志文件数口径
            cur.execute("SELECT count(*) FROM V$ARCH_FILE")
            log_count = int(cur.fetchone()[0])""", 1),
    # 24) V$INSTANCE 无 LOG_SIZE 列 → V$RLOG.TOTAL_SPACE
    ("""            cur.execute("SELECT LOG_SIZE FROM V$INSTANCE")
            log_size = int(cur.fetchone()[0])""",
     """            cur.execute("SELECT TOTAL_SPACE FROM V$RLOG")
            log_size = int(cur.fetchone()[0] or 0)""", 1),
    # 25) V$LOGIN 不存在 → 登录计数不可得，置空不伪造
    ("""            cur.execute("SELECT count(*) FROM V$LOGIN")
            login_count = int(cur.fetchone()[0])""",
     """            # DM8 无 V$LOGIN 视图（实测 -2106）→ 登录计数不可得，置空不伪造
            login_count = None""", 1),
    # 26) V$TRX 无 TRX_START_TIME/SQL_TEXT 列
    ("""            cur.execute(\"\"\"
                SELECT TRX_ID, SESS_ID, STATE,
                       (SYSDATE - TRX_START_TIME) * 86400 as DURATION_SEC,
                       SUBSTR(SQL_TEXT, 1, 200) as SQL_TEXT
                FROM V$TRX t LEFT JOIN V$SESSIONS s ON t.SESS_ID = s.SESS_ID
                WHERE (SYSDATE - TRX_START_TIME) * 86400 > 60
                ORDER BY DURATION_SEC DESC
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT t.ID as TRX_ID, t.SESS_ID, t.STATUS,
                       (SYSDATE - s.CREATE_TIME) * 86400 as DURATION_SEC,
                       SUBSTR(s.SQL_TEXT, 1, 200) as SQL_TEXT
                FROM V$TRX t LEFT JOIN V$SESSIONS s ON t.SESS_ID = s.SESS_ID
                WHERE (SYSDATE - s.CREATE_TIME) * 86400 > 60
                ORDER BY DURATION_SEC DESC
            \"\"\")""", 1),
    # 27) V$DEADLOCK_HISTORY 无 DEADLOCK_TIME 列
    ("""            cur.execute(\"\"\"
                SELECT * FROM (
                    SELECT SESS_ID, TRX_ID, SQL_TEXT, DEADLOCK_TIME
                    FROM V$DEADLOCK_HISTORY
                    ORDER BY DEADLOCK_TIME DESC
                ) WHERE ROWNUM <= 5
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT * FROM (
                    SELECT SESS_ID, TRX_ID, SQL_TEXT, HAPPEN_TIME
                    FROM V$DEADLOCK_HISTORY
                    ORDER BY HAPPEN_TIME DESC
                ) WHERE ROWNUM <= 5
            \"\"\")""", 1),
    # 28) V$RLOG 无 STATUS 列
    ("""            cur.execute("SELECT COUNT(*) FROM V$RLOG WHERE STATUS='ACTIVE'")""",
     """            cur.execute("SELECT COUNT(*) FROM V$RLOG WHERE ALMOST_FULL_STATUS <> 'SAFE'")""", 1),
    # 29) 双 WHERE 语法错误 → 子查询 + ROWNUM
    ("""            cur.execute(\"\"\"
                SELECT OWNER, TABLE_NAME, NUM_ROWS,
                       (SELECT ROUND(SUM(USED_SIZE * PAGE) / 1024 / 1024, 2)
                        FROM V$TABLESPACE WHERE NAME = t.TABLESPACE_NAME) as SIZE_MB
                FROM DBA_TABLES t
                WHERE OWNER NOT IN ('SYS', 'SYSTEM', 'SYSDBA', 'SYSAUDITOR', 'SYSSSO')
                ORDER BY NUM_ROWS DESC
                WHERE ROWNUM <= 20
            \"\"\")""",
     """            cur.execute(\"\"\"
                SELECT * FROM (
                    SELECT OWNER, TABLE_NAME, NUM_ROWS,
                           (SELECT ROUND(SUM(USED_SIZE * PAGE) / 1024 / 1024, 2)
                            FROM V$TABLESPACE WHERE NAME = t.TABLESPACE_NAME) as SIZE_MB
                    FROM DBA_TABLES t
                    WHERE OWNER NOT IN ('SYS', 'SYSTEM', 'SYSDBA', 'SYSAUDITOR', 'SYSSSO')
                    ORDER BY NUM_ROWS DESC
                ) WHERE ROWNUM <= 20
            \"\"\")""", 1),
]

SENTINEL_PATCHES = [
    # 1) golden_metrics：DM 会话视图为 V$SESSIONS，上限参数为 MAX_SESSIONS
    ("""        elif db_type in ('oracle', 'dm'):
            cur.execute("SELECT count(*) FROM v$session WHERE type='USER'")
            tc = cur.fetchone()[0]
            cur.execute("SELECT value FROM v$parameter WHERE name='sessions'")
            row = cur.fetchone()
            mc = int(row[0]) if row and row[0] else 0
            m['threads_connected'] = tc
            m['max_connections'] = mc
            m['conn_usage_pct'] = round(tc / mc * 100, 2) if mc else 0""",
     """        elif db_type == 'oracle':
            cur.execute("SELECT count(*) FROM v$session WHERE type='USER'")
            tc = cur.fetchone()[0]
            cur.execute("SELECT value FROM v$parameter WHERE name='sessions'")
            row = cur.fetchone()
            mc = int(row[0]) if row and row[0] else 0
            m['threads_connected'] = tc
            m['max_connections'] = mc
            m['conn_usage_pct'] = round(tc / mc * 100, 2) if mc else 0
        elif db_type == 'dm':
            # DM8 会话视图为 V$SESSIONS（复数），上限参数名为 MAX_SESSIONS
            cur.execute("SELECT count(*) FROM V$SESSIONS")
            tc = cur.fetchone()[0]
            cur.execute("SELECT value FROM v$parameter WHERE name='MAX_SESSIONS'")
            row = cur.fetchone()
            mc = int(row[0]) if row and row[0] else 0
            m['threads_connected'] = tc
            m['max_connections'] = mc
            m['conn_usage_pct'] = round(tc / mc * 100, 2) if mc else 0""", 1),
    # 2) ASH 分发：DM 走独立分支
    ("""    if db_type in ('oracle', 'dm'):
        return _ash_oracle(cur, db_type, config_id=config_id)""",
     """    if db_type == 'oracle':
        return _ash_oracle(cur, db_type, config_id=config_id)
    if db_type == 'dm':
        return _ash_dm(cur, config_id=config_id)""", 1),
    # 3) 新增 DM ASH 实现
    ("""def sample_sessions(cur, db_type: str, config_id=None) -> list:""",
     '''def _ash_dm(cur, config_id=None):
    """达梦 DM8 ASH 采样。

    DB-009 实测（<local-host>:<ephemeral-port> DM8）：DM8 无 v$session（单数）视图，也无
    V$SESSIONS.USER_ID 列；实际会话视图为 V$SESSIONS，列为
    SESS_ID / USER_NAME / STATE / CLNT_IP / CLNT_HOST / APPNAME /
    SQL_TEXT / SQL_ID / TRX_ID / CREATE_TIME。
    """
    cur.execute(
        "SELECT SESS_ID, USER_NAME, STATE, CLNT_IP, CLNT_HOST, APPNAME, "
        "SUBSTR(SQL_TEXT, 1, 500) as SQL_TEXT, SQL_ID, TRX_ID, CREATE_TIME "
        "FROM V$SESSIONS WHERE USER_NAME IS NOT NULL")
    cols = [d[0].lower() for d in cur.description]
    raw_rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    out = []
    for r in raw_rows:
        row = {
            'session_id': str(r.get('sess_id')), 'user_name': r.get('user_name'),
            'client_host': r.get('clnt_host'), 'db_name': None,
            'command': r.get('state'), 'state': r.get('state'),
            'raw_wait_class': None,
            'wait_event': None, 'active_secs': 0,
            'is_blocked': False,
            'blocker_id': None,
            'sql_text': r.get('sql_text'),
            'sql_id': r.get('sql_id'), 'program': r.get('appname'), 'module': None,
            'lock_type': None, 'lock_mode': None,
            'lock_object': None,
            'wait_secs': None,
            'trx_age_secs': None,
        }
        row['sql_digest'] = unified_digest('dm', row['sql_id'], row['sql_text'])
        row['wait_class'] = classify('dm', row)
        out.append(row)
    return out


def sample_sessions(cur, db_type: str, config_id=None) -> list:''', 1),
]

MONITOR_PATCHES = [
    # 1) DM 的 sql_stat 采集：v$sqlstats 不存在 → V$SQL_HISTORY 聚合
    ("""        elif config.db_type in ('oracle', 'dm'):
            cur.execute(
                "SELECT sql_id, executions, elapsed_time, rows_processed, buffer_gets, "
                "sql_text FROM v$sqlstats "
                f"ORDER BY elapsed_time DESC FETCH FIRST {self._SQLSTAT_TOPN} ROWS ONLY")
            for sql_id, execs, us, rows_, reads, txt in cur.fetchall():
                snap[str(sql_id)] = (int(execs or 0), int(us or 0) // 1000,
                                     int(rows_ or 0), int(reads or 0),
                                     (txt or '')[:500], None)""",
     """        elif config.db_type == 'oracle':
            cur.execute(
                "SELECT sql_id, executions, elapsed_time, rows_processed, buffer_gets, "
                "sql_text FROM v$sqlstats "
                f"ORDER BY elapsed_time DESC FETCH FIRST {self._SQLSTAT_TOPN} ROWS ONLY")
            for sql_id, execs, us, rows_, reads, txt in cur.fetchall():
                snap[str(sql_id)] = (int(execs or 0), int(us or 0) // 1000,
                                     int(rows_ or 0), int(reads or 0),
                                     (txt or '')[:500], None)
        elif config.db_type == 'dm':
            # DM8 无 v$sqlstats（DB-009 实测 -2106）→ 改用 V$SQL_HISTORY 按 SQL_ID 聚合
            cur.execute(
                "SELECT * FROM ("
                "SELECT SQL_ID, COUNT(*) as EXECUTIONS, SUM(TIME_USED) as ELAPSED_TIME, "
                "SUM(AFFECTED_ROWS) as ROWS_PROCESSED, "
                "SUM(N_LOGIC_READ + N_PHY_READ) as BUFFER_GETS, "
                "SUBSTR(TOP_SQL_TEXT, 1, 500) as SQL_TEXT "
                "FROM V$SQL_HISTORY GROUP BY SQL_ID, TOP_SQL_TEXT "
                f"ORDER BY EXECUTIONS DESC) WHERE ROWNUM <= {self._SQLSTAT_TOPN}")
            for sql_id, execs, us, rows_, reads, txt in cur.fetchall():
                snap[str(sql_id)] = (int(execs or 0), int(us or 0),
                                     int(rows_ or 0), int(reads or 0),
                                     (txt or '')[:500], None)""", 1),
]


def _tok(text):
    toks = re.split(r'(\s+)', text)
    out = []
    for t in toks:
        if t and t.strip() == '':
            out.append(r'\s+')
        else:
            out.append(re.escape(t))
    return ''.join(out)


def _flex_regex(old):
    """把 old 片段编译成容错正则。

    规则：
      * 行内空白 → [ \\t]+（**不含换行**，避免把 `try:` 之后的换行一起吃掉）
      * 行边界 → \\r?\\n
      * " or 'N/A'" → 可选组（源码里同一结构有时写、有时不写）
    """
    # NUL 标记两侧保留空格，使 NUL 成为独立 token（否则 re.split 不会把它切开）。
    old = old.replace(" or 'N/A'", " \x00 ")
    lines = old.split('\n')
    parts = []
    for i, ln in enumerate(lines):
        if i:
            parts.append(r'\r?\n')
        toks = re.split(r'([ \t]+)', ln)
        for t in toks:
            if t == '\x00':
                if parts and parts[-1] == r'[ \t]*':
                    parts[-1] = r"(?:[ \t]+or\s+'N/A')?"
                else:
                    parts.append(r"(?:[ \t]+or\s+'N/A')?")
            elif t and t.strip() == '':
                # 用 [ \t]* 而非 [ \t]+：可选组后面可能紧跟 ')' 等非空白字符，
                # 若此处强制要求空白会导致整段匹配失败。
                parts.append(r'[ \t]*')
            elif t:
                parts.append(re.escape(t))
    return re.compile(''.join(parts))


def apply(path, patches):
    src = io.open(path, encoding='utf-8', newline='').read()
    crlf = '\r\n' in src
    applied = 0
    for idx, (old, new, expect) in enumerate(patches, 1):
        # 正则自带 \r?\n 行边界，故 old 不做换行转换；只有替换文本需要跟随文件风格
        n = new.replace('\n', '\r\n') if crlf else new
        rx = _flex_regex(old)
        matches = rx.findall(src)
        cnt = len(matches)
        if cnt != expect:
            raise SystemExit('PATCH ABORT %s [patch #%d]: expected %d occurrence(s), found %d for:\n%s'
                             % (path, idx, expect, cnt, old[:200]))
        src = rx.sub(lambda _m: n, src)
        applied += 1
    io.open(path, 'w', encoding='utf-8', newline='').write(src)
    return applied


def main():
    n1 = apply(CHECKER, CHECKER_PATCHES)
    n2 = apply(SENTINEL, SENTINEL_PATCHES)
    n3 = apply(MONITOR, MONITOR_PATCHES)
    print('applied: checker=%d sentinel=%d monitor=%d' % (n1, n2, n3))
    return 0


if __name__ == '__main__':
    sys.exit(main())
