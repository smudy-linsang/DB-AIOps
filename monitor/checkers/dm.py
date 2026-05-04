# -*- coding: utf-8 -*-
"""
达梦数据库 (DM8) 检查器
支持 DW 集群模式（主备）和 DSC 集群模式（共享存储）
"""

import pyodbc
from monitor.checkers.base import BaseDBChecker


class DamengChecker(BaseDBChecker):
    """达梦数据库 (DM8) 监控检查器"""

    def get_connection(self, config):
        """达梦 ODBC 连接"""
        conn_str = (
            f"DRIVER={{DM8 ODBC DRIVER}};"
            f"SERVER={config.host}:{config.port};"
            f"UID={config.username};"
            f"PWD={config.get_password()};"
        )
        return pyodbc.connect(conn_str, timeout=5)

    def collect_metrics(self, config, conn):
        cur = conn.cursor()

        # =============================================
        # 1. 基础信息 (basic)
        # =============================================
        cur.execute("SELECT banner FROM v$version WHERE ROWNUM=1")
        version = cur.fetchone()[0]

        try:
            cur.execute(
                "SELECT INSTANCE_NAME, HOST_NAME, TO_CHAR(START_TIME, 'YYYY-MM-DD HH24:MI:SS') FROM V$INSTANCE"
            )
            inst_row = cur.fetchone()
            instance_name = inst_row[0]
            host_name = inst_row[1]
            startup_time = inst_row[2]
        except:
            instance_name = 'N/A'
            host_name = 'N/A'
            startup_time = 'N/A'

        try:
            cur.execute("SELECT MODE FROM V$INSTANCE")
            db_mode = cur.fetchone()[0]
        except:
            db_mode = 'N/A'

        try:
            cur.execute("SELECT (SYSDATE-START_TIME)*86400 FROM v$instance")
            uptime = int(cur.fetchone()[0])
        except:
            uptime = 0

        try:
            cur.execute("SELECT ARCH_MODE FROM V$DATABASE")
            arch_mode = cur.fetchone()[0]
        except:
            arch_mode = 'N/A'

        # =============================================
        # 2. 连接与会话 (session)
        # =============================================
        cur.execute("SELECT count(*) FROM v$sessions WHERE STATE='ACTIVE'")
        active_sessions = int(cur.fetchone()[0])

        cur.execute("SELECT count(*) FROM v$sessions")
        total_sessions = int(cur.fetchone()[0])

        cur.execute("SELECT VALUE FROM v$parameter WHERE name='MAX_SESSIONS'")
        max_sessions = int(cur.fetchone()[0])
        conn_usage_pct = round(
            (active_sessions / max_sessions) * 100, 2
        ) if max_sessions > 0 else 0

        try:
            cur.execute(
                "SELECT count(*) FROM V$SESSION_WAIT WHERE EVENT != 'Idle'"
            )
            session_wait_count = int(cur.fetchone()[0])
        except:
            session_wait_count = 0

        # =============================================
        # 3. 空间使用 (space)
        # =============================================
        # 表空间
        try:
            cur.execute("""
                SELECT 
                    NAME,
                    ROUND(TOTAL_SIZE * PAGE / 1024.0 / 1024.0, 2) as TOTAL_MB,
                    ROUND(USED_SIZE * PAGE / 1024.0 / 1024.0, 2) as USED_MB,
                    CASE WHEN TOTAL_SIZE > 0 
                         THEN ROUND(USED_SIZE * 100.0 / TOTAL_SIZE, 2)
                         ELSE 0 END as USED_PCT
                FROM (
                    SELECT 
                        t.NAME,
                        t.TOTAL_SIZE,
                        t.USED_SIZE,
                        (SELECT CAST(VALUE AS INT) FROM V$PARAMETER WHERE NAME = 'GLOBAL_PAGE_SIZE') as PAGE
                    FROM V$TABLESPACE t
                )
            """)
            tablespaces = []
            for row in cur.fetchall():
                tablespaces.append({
                    "name": row[0],
                    "total_mb": float(row[1]),
                    "used_mb": float(row[2]),
                    "used_pct": float(row[3])
                })
        except:
            tablespaces = []

        # 临时表空间
        try:
            cur.execute("""
                SELECT NAME, ROUND(TOTAL_SIZE * PAGE / 1024.0 / 1024.0, 2) as TOTAL_MB
                FROM V$TEMP_SPACE_USAGE
            """)
            temp_tablespaces = []
            for row in cur.fetchall():
                temp_tablespaces.append({
                    "name": row[0],
                    "size_mb": float(row[1])
                })
        except:
            temp_tablespaces = []

        # 数据文件统计
        try:
            cur.execute(
                "SELECT count(*), SUM(TOTAL_SIZE * PAGE) / 1024 / 1024 / 1024 FROM V$TABLESPACE"
            )
            tbs_row = cur.fetchone()
            datafile_count = tbs_row[0]
            datafile_size_total_gb = float(tbs_row[1])
        except:
            datafile_count = 0
            datafile_size_total_gb = 0

        # =============================================
        # 4. 性能指标 (performance)
        # =============================================
        try:
            cur.execute("SELECT VALUE FROM V$PARAMETER WHERE NAME='BUFFER'")
            buffer_size = int(cur.fetchone()[0])
            buffer_size_mb = round(buffer_size / 1024.0, 2)
        except:
            buffer_size_mb = 0

        try:
            cur.execute(
                "SELECT VALUE FROM V$SYSTEM_INFO WHERE NAME='SQL_COUNT'"
            )
            sql_count = int(cur.fetchone()[0])
        except:
            sql_count = 0

        try:
            cur.execute(
                "SELECT VALUE FROM V$SYSTEM_INFO WHERE NAME='TRAN_COUNT'"
            )
            tran_count = int(cur.fetchone()[0])
        except:
            tran_count = 0

        qps = round(sql_count / uptime, 2) if uptime > 0 else 0
        tps = round(tran_count / uptime, 2) if uptime > 0 else 0

        # 缓冲池命中率
        try:
            cur.execute("""
                SELECT 
                    ROUND((1 - A.CACHE_R_SORT / A.REQUEST_R_SORT) * 100, 2) as CACHE_HIT_RATIO
                FROM (
                    SELECT 
                        SUM(REQUEST_R_SORT) as REQUEST_R_SORT,
                        SUM(CACHE_R_SORT) as CACHE_R_SORT
                    FROM V$BUFFER
                ) A
            """)
            cache_hit_ratio = float(cur.fetchone()[0])
        except:
            cache_hit_ratio = 0

        # =============================================
        # 5. 锁等待 (wait) - 增强
        # =============================================
        lock_list = []
        lock_wait_count = 0
        try:
            cur.execute("""
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
            """)
            for row in cur.fetchall():
                lock_wait_count += 1
                lock_list.append({
                    "sess_id": str(row[0]),
                    "user_name": row[1] or 'N/A',
                    "transaction_id": str(row[2]) if row[2] else 'N/A',
                    "sql_text": (row[3] or 'N/A')[:100],
                    "lmode": str(row[4]),
                    "blocked": row[5]
                })
        except Exception:
            pass

        # 等待事件
        try:
            cur.execute("""
                SELECT EVENT, COUNT(*) as CNT
                FROM V$SESSION_WAIT
                WHERE EVENT != 'Idle'
                GROUP BY EVENT
                ORDER BY CNT DESC
                LIMIT 10
            """)
            wait_events = []
            for row in cur.fetchall():
                wait_events.append({
                    "event": row[0],
                    "count": int(row[1])
                })
        except:
            wait_events = []

        # =============================================
        # 6. 会话详情 (session_detail) - P0新增
        # =============================================
        session_list = []
        try:
            cur.execute("""
                SELECT 
                    SESS_ID, USER_NAME, STATE, PROGRAM, IP, HOST,
                    SUBSTR(SQL_TEXT, 1, 200) as SQL_TEXT,
                    TRX_ID, TRANSACTION_ID
                FROM V$SESSIONS
                WHERE USER_NAME IS NOT NULL
                ORDER BY STATE, SESS_ID
                LIMIT 100
            """)
            for row in cur.fetchall():
                session_list.append({
                    "sess_id": str(row[0]),
                    "user_name": row[1] or 'N/A',
                    "state": row[2] or 'N/A',
                    "program": row[3] or 'N/A',
                    "ip": row[4] or 'N/A',
                    "host": row[5] or 'N/A',
                    "sql_text": (row[6] or 'N/A')[:200],
                    "trx_id": str(row[7]) if row[7] else 'N/A',
                    "transaction_id": str(row[8]) if row[8] else 'N/A'
                })
        except Exception:
            pass

        # =============================================
        # 7. SQL统计 (sql) - P0新增
        # =============================================
        slow_queries = []
        try:
            cur.execute("""
                SELECT SQL_TEXT, EXECUTE_COUNT, AVG_TIME
                FROM (
                    SELECT SQL_TEXT, EXECUTE_COUNT, AVG_TIME
                    FROM V$SQL_HISTORY
                    ORDER BY EXECUTE_COUNT DESC
                )
                WHERE ROWNUM <= 10
            """)
            for row in cur.fetchall():
                slow_queries.append({
                    "sql_text": (row[0] or 'N/A')[:200],
                    "execute_count": int(row[1]),
                    "avg_time": int(row[2]) if row[2] else 0
                })
        except:
            pass

        top_sql = []
        try:
            cur.execute("""
                SELECT SQL_TEXT, EXECUTE_COUNT
                FROM V$SQLSTATS
                ORDER BY EXECUTE_COUNT DESC
                LIMIT 10
            """)
            for row in cur.fetchall():
                top_sql.append({
                    "sql_text": (row[0] or 'N/A')[:200],
                    "execute_count": int(row[1])
                })
        except:
            pass

        # =============================================
        # 8. 缓冲池 (buffer) - P0
        # =============================================
        buffer_pools = []
        try:
            cur.execute("""
                SELECT GROUP_ID, GROUP_NAME, NODE_NUM, BUFFER_SIZE, BUFFER_COUNT,
                       FREE_COUNT, ACTIVE_COUNT, DIRTY_COUNT
                FROM V$BUFFER
            """)
            for row in cur.fetchall():
                buffer_pools.append({
                    "group_id": int(row[0]),
                    "group_name": row[1],
                    "node_num": int(row[2]),
                    "buffer_size": int(row[3]),
                    "buffer_count": int(row[4]),
                    "free_count": int(row[5]),
                    "active_count": int(row[6]),
                    "dirty_count": int(row[7])
                })
        except:
            pass

        # =============================================
        # 9. 事务统计 (transaction) - P1
        # =============================================
        try:
            cur.execute("SELECT count(*) FROM V$TRX")
            active_transactions = int(cur.fetchone()[0])
        except:
            active_transactions = 0

        try:
            cur.execute("""
                SELECT count(*) FROM V$SESSIONS 
                WHERE STATE='INACTIVE' AND TRX_ID != 0
            """)
            idle_transactions = int(cur.fetchone()[0])
        except:
            idle_transactions = 0

        # =============================================
        # 10. 复制与集群 (replication) - P1 增强
        # =============================================
        try:
            cur.execute("SELECT ARCHIVE_MODE FROM V$DATABASE")
            archive_mode = cur.fetchone()[0]
        except:
            archive_mode = 'N/A'

        try:
            cur.execute(
                "SELECT DEST_ID, STATUS, DEST_NAME FROM V$ARCH_DEST"
            )
            archive_dest = []
            for row in cur.fetchall():
                archive_dest.append({
                    "dest_id": int(row[0]),
                    "status": row[1] or 'N/A',
                    "dest_name": row[2] or 'N/A'
                })
        except:
            archive_dest = []

        try:
            cur.execute("SELECT count(*) FROM V$ARCH_FILE")
            archive_file_count = int(cur.fetchone()[0])
        except:
            archive_file_count = 0

        # =============================================
        # 10.5 DW 集群（主备）监控 - P0 新增
        # =============================================
        # 数据库角色和模式
        try:
            cur.execute("SELECT MODE FROM V$INSTANCE")
            dm_instance_mode = cur.fetchone()[0]
        except:
            dm_instance_mode = 'N/A'

        try:
            cur.execute("SELECT MODE$ FROM V$DATABASE")
            dm_database_mode = cur.fetchone()[0]
        except:
            dm_database_mode = 'N/A'

        # 实时归档状态
        try:
            cur.execute("""
                SELECT DEST_ID, DEST_NAME, STATUS, ARCH_TYPE, ARCH_SEQ, ARCH_SPACE, 
                       ARCH_FILE, ARCH_TIMELY, IS_LOGICAL, SYNCHRONIZED
                FROM V$ARCH_DEST WHERE STATUS != 'INACTIVE'
            """)
            realtime_archive_dest = []
            for row in cur.fetchall():
                realtime_archive_dest.append({
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
                })
        except:
            realtime_archive_dest = []

        # 实时日志同步状态
        try:
            cur.execute("""
                SELECT SRC_INSTANCE, DEST_INSTANCE, ARCH_DEST, ARCH_SEQ, ARCH_SEQ_ALL,
                       APPLY_SEQ, APPLY_SEQ_ALL, SYNC_STATUS
                FROM V$RLOG
            """)
            rlog_sync_status = []
            for row in cur.fetchall():
                rlog_sync_status.append({
                    "src_instance": row[0] or 'N/A',
                    "dest_instance": row[1] or 'N/A',
                    "arch_dest": row[2] or 'N/A',
                    "arch_seq": int(row[3]) if row[3] else 0,
                    "arch_seq_all": int(row[4]) if row[4] else 0,
                    "apply_seq": int(row[5]) if row[5] else 0,
                    "apply_seq_all": int(row[6]) if row[6] else 0,
                    "sync_status": row[7] or 'N/A'
                })
        except:
            rlog_sync_status = []

        # 主备延迟
        try:
            cur.execute("""
                SELECT DST_INST_NAME, APPLY_STATUS, APPLY_DELAY, APPLY_RST_SEQ, 
                       APPLY_RST_TIME, LAST_ARCH_SEQ
                FROM V$DEST_PENDING
            """)
            dest_pending = []
            apply_delay_total = 0
            for row in cur.fetchall():
                delay = int(row[2]) if row[2] else 0
                apply_delay_total += delay
                dest_pending.append({
                    "dst_inst_name": row[0] or 'N/A',
                    "apply_status": row[1] or 'N/A',
                    "apply_delay": delay,
                    "apply_rst_seq": int(row[3]) if row[3] else 0,
                    "apply_rst_time": str(row[4]) if row[4] else 'N/A',
                    "last_arch_seq": int(row[5]) if row[5] else 0
                })
        except:
            dest_pending = []
            apply_delay_total = 0

        # DW 复制状态汇总
        dw_replication_health = 'UNKNOWN'
        dw_replication_issues = []
        if dm_instance_mode == 'PRIMARY' or dm_database_mode == 'PRIMARY':
            dw_replication_health = 'HEALTHY'
        elif dm_instance_mode == 'STANDBY' or dm_database_mode == 'STANDBY':
            if apply_delay_total > 1000:
                dw_replication_health = 'DEGRADED'
                dw_replication_issues.append(
                    f"备库延迟过高: {apply_delay_total}"
                )
            else:
                dw_replication_health = 'HEALTHY'
        else:
            if dm_instance_mode != 'N/A':
                dw_replication_health = 'UNKNOWN'

        # =============================================
        # 10.6 DSC 集群（共享存储）监控 - P0 新增
        # =============================================
        # DSC 集群信息
        try:
            cur.execute("""
                SELECT INSTANCE_NAME, INSTANCE_ID, HOST_NAME, PORT_NUM, 
                       STATUS, IS_PRIMARY, UPTIME
                FROM V$CLUSTER
            """)
            dsc_cluster_info = []
            dsc_node_count = 0
            dsc_primary_node = 'N/A'
            for row in cur.fetchall():
                dsc_node_count += 1
                if row[5] == 1:  # IS_PRIMARY
                    dsc_primary_node = row[0]
                dsc_cluster_info.append({
                    "instance_name": row[0] or 'N/A',
                    "instance_id": int(row[1]) if row[1] else 0,
                    "host_name": row[2] or 'N/A',
                    "port_num": int(row[3]) if row[3] else 0,
                    "status": row[4] or 'N/A',
                    "is_primary": int(row[5]) if row[5] else 0,
                    "uptime": int(row[6]) if row[6] else 0
                })
        except:
            dsc_cluster_info = []
            dsc_node_count = 0
            dsc_primary_node = 'N/A'

        # DSC 实例详情
        try:
            cur.execute("""
                SELECT INST_ID, INST_NAME, CLUSTER_STATE, DB_MAGIC, 
                       RLOG_SEND_OFFSET, RLOG_PKG_SND_COUNT
                FROM V$DSC_INSTANCES
            """)
            dsc_instances = []
            for row in cur.fetchall():
                dsc_instances.append({
                    "inst_id": int(row[0]) if row[0] else 0,
                    "inst_name": row[1] or 'N/A',
                    "cluster_state": row[2] or 'N/A',
                    "db_magic": int(row[3]) if row[3] else 0,
                    "rlog_send_offset": str(row[4]) if row[4] else 'N/A',
                    "rlog_pkg_snd_count": int(row[5]) if row[5] else 0
                })
        except:
            dsc_instances = []

        # DSC 全局锁
        try:
            cur.execute("""
                SELECT LOCK_ID, LOCK_TYPE, OWNER_INST, OWNER_LRU,
                       BLOCKING_LRU, BLOCKING_INST
                FROM V$GLOBAL_LATCH
            """)
            dsc_global_latches = []
            for row in cur.fetchall():
                dsc_global_latches.append({
                    "lock_id": str(row[0]) if row[0] else 'N/A',
                    "lock_type": row[1] or 'N/A',
                    "owner_inst": int(row[2]) if row[2] else 0,
                    "owner_lru": int(row[3]) if row[3] else 0,
                    "blocking_lru": int(row[4]) if row[4] else 0,
                    "blocking_inst": int(row[5]) if row[5] else 0
                })
        except:
            dsc_global_latches = []

        # DSC 锁状态统计
        try:
            cur.execute(
                "SELECT COUNT(*) FROM V$GLOBAL_LATCH WHERE BLOCKING_LRU > 0"
            )
            dsc_lock_contention_count = int(cur.fetchone()[0])
        except:
            dsc_lock_contention_count = 0

        # DSC 健康状态判断
        dsc_cluster_health = 'UNKNOWN'
        dsc_cluster_issues = []
        if dsc_node_count > 0:
            dsc_cluster_health = 'HEALTHY'
            for node in dsc_cluster_info:
                if node['status'] != 'OPEN':
                    dsc_cluster_health = 'DEGRADED'
                    dsc_cluster_issues.append(
                        f"节点 {node['instance_name']} 状态异常: {node['status']}"
                    )
            if dsc_lock_contention_count > 100:
                dsc_cluster_health = 'DEGRADED'
                dsc_cluster_issues.append(
                    f"全局锁竞争过多: {dsc_lock_contention_count}"
                )
        else:
            dsc_cluster_health = 'NOT_CLUSTER'

        # =============================================
        # 11. 配置参数 (config) - P1
        # =============================================
        config_params = {}
        config_keys = [
            'BUFFER', 'SORT_BUF_SIZE', 'MLOG_BUF_SIZE', 'MAX_SESSIONS',
            'MAX_TRX'
        ]
        for key in config_keys:
            try:
                cur.execute(f"SELECT VALUE FROM V$PARAMETER WHERE NAME='{key}'")
                config_params[key] = cur.fetchone()[0]
            except:
                pass

        # =============================================
        # 12. 日志统计 (log) - P1
        # =============================================
        try:
            cur.execute("SELECT count(*) FROM V$LOG")
            log_count = int(cur.fetchone()[0])
        except:
            log_count = 0

        try:
            cur.execute("SELECT LOG_SIZE FROM V$INSTANCE")
            log_size = int(cur.fetchone()[0])
        except:
            log_size = 0

        # =============================================
        # 13. 安全审计 (security) - P2
        # =============================================
        try:
            cur.execute("SELECT count(*) FROM V$LOGIN")
            login_count = int(cur.fetchone()[0])
        except:
            login_count = 0

        try:
            cur.execute("""
                SELECT USER_NAME, COUNT(*) as CNT
                FROM V$SESSIONS
                WHERE STATE = 'FAILED'
                GROUP BY USER_NAME
            """)
            failed_logins = []
            for row in cur.fetchall():
                failed_logins.append({
                    "user_name": row[0] or 'N/A',
                    "count": int(row[1])
                })
        except:
            failed_logins = []

        # =============================================
        # 14. 资源限制 (resource) - P2
        # =============================================
        resource_limits = []
        for resource in ['MAX_SESSIONS', 'MAX_TRX', 'MAX_LOCK_NUM']:
            try:
                cur.execute(
                    f"SELECT VALUE FROM V$PARAMETER WHERE NAME='{resource}'"
                )
                value = cur.fetchone()[0]
                resource_limits.append({
                    "resource_name": resource,
                    "value": value
                })
            except:
                pass

        cur.close()

        return {
            # 基础信息
            "version": str(version)[:50] + "...",
            "instance_name": instance_name,
            "host_name": host_name,
            "startup_time": startup_time,
            "db_mode": db_mode,
            "arch_mode": arch_mode,
            "uptime_seconds": uptime,

            # 连接会话
            "active_connections": active_sessions,
            "total_connections": total_sessions,
            "max_connections": max_sessions,
            "conn_usage_pct": conn_usage_pct,
            "session_wait_count": session_wait_count,

            # 空间
            "tablespaces": tablespaces,
            "temp_tablespaces": temp_tablespaces,
            "datafile_count": datafile_count,
            "datafile_size_total_gb": datafile_size_total_gb,

            # 性能
            "qps": qps,
            "tps": tps,
            "buffer_size_mb": buffer_size_mb,
            "cache_hit_ratio": cache_hit_ratio,
            "sql_count": sql_count,
            "tran_count": tran_count,

            # 等待事件
            "locks": lock_list,
            "lock_wait_count": lock_wait_count,
            "wait_events": wait_events,

            # 会话详情
            "session_list": session_list,

            # SQL统计
            "slow_queries": slow_queries,
            "top_sql": top_sql,

            # 缓冲池
            "buffer_pools": buffer_pools,

            # 事务统计
            "active_transactions": active_transactions,
            "idle_transactions": idle_transactions,

            # 复制集群
            "archive_mode": archive_mode,
            "archive_dest": archive_dest,
            "archive_file_count": archive_file_count,

            # DW 集群主备模式 (新增)
            "dm_instance_mode": dm_instance_mode,
            "dm_database_mode": dm_database_mode,
            "realtime_archive_dest": realtime_archive_dest,
            "rlog_sync_status": rlog_sync_status,
            "dest_pending": dest_pending,
            "apply_delay_total": apply_delay_total,
            "dw_replication_health": dw_replication_health,
            "dw_replication_issues": dw_replication_issues,

            # DSC 集群共享存储模式 (新增)
            "dsc_cluster_info": dsc_cluster_info,
            "dsc_node_count": dsc_node_count,
            "dsc_primary_node": dsc_primary_node,
            "dsc_instances": dsc_instances,
            "dsc_global_latches": dsc_global_latches,
            "dsc_lock_contention_count": dsc_lock_contention_count,
            "dsc_cluster_health": dsc_cluster_health,
            "dsc_cluster_issues": dsc_cluster_issues,

            # 配置参数
            "config_params": config_params,

            # 日志
            "log_count": log_count,
            "log_size": log_size,

            # 安全审计
            "login_count": login_count,
            "failed_logins": failed_logins,

            # 资源限制
            "resource_limits": resource_limits,
        }
