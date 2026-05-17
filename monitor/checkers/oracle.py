# -*- coding: utf-8 -*-
"""
Oracle 数据库检查器 (含 RAC / ADG 支持)

从 start_monitor.py 中提取，v3.0 重构。
采集 14 大类指标：基础信息、会话、空间、性能、锁、会话详情、
SQL统计、缓冲池、事务、对象、RAC、ADG、配置参数、日志、HA、资源限制。
"""

import datetime
import oracledb

from monitor.checkers.base import BaseDBChecker, LOCK_TIME_THRESHOLD


class OracleChecker(BaseDBChecker):

    def get_connection(self, config):
        host = config.host
        port = config.port or 1521
        service_name = config.service_name
        if service_name:
            dsn = f"{host}:{port}/{service_name}"
        else:
            dsn = f"{host}:{port}"
        return oracledb.connect(
            user=config.username,
            password=config.get_password(),
            dsn=dsn,
        )

    def collect_metrics(self, config, conn):
        cursor = conn.cursor()

        # =============================================
        # 1. 基础信息 (basic)
        # =============================================
        cursor.execute("SELECT banner FROM v$version WHERE ROWNUM=1")
        version = cursor.fetchone()[0]

        cursor.execute("SELECT instance_name, host_name FROM v$instance")
        inst = cursor.fetchone()
        instance_name = inst[0]
        host_name = inst[1]

        cursor.execute("SELECT database_role FROM v$database")
        database_role = cursor.fetchone()[0]

        cursor.execute("SELECT name, open_mode FROM v$database")
        db_info = cursor.fetchone()
        database_name = db_info[0]
        open_mode = db_info[1]

        cursor.execute("SELECT startup_time FROM v$instance")
        startup_time = cursor.fetchone()[0]
        uptime = (datetime.datetime.now() - startup_time).total_seconds()

        # =============================================
        # 2. 连接与会话 (session)
        # =============================================
        cursor.execute("SELECT count(*) FROM v$session WHERE status='ACTIVE'")
        active_connections = int(cursor.fetchone()[0])

        cursor.execute("SELECT count(*) FROM v$session")
        total_connections = int(cursor.fetchone()[0])

        cursor.execute("SELECT count(*) FROM v$session WHERE status='INACTIVE'")
        inactive_connections = int(cursor.fetchone()[0])

        cursor.execute("SELECT value FROM v$parameter WHERE name='processes'")
        max_connections = int(cursor.fetchone()[0])
        conn_usage_pct = round((active_connections / max_connections) * 100, 2) if max_connections > 0 else 0

        cursor.execute("SELECT count(*) FROM v$session WHERE blocking_session IS NOT NULL")
        blocked_sessions = int(cursor.fetchone()[0])

        # =============================================
        # 3. 空间使用 (space)
        # =============================================
        tablespaces = []
        try:
            cursor.execute("""
                SELECT
                    a.tablespace_name,
                    ROUND(a.total_mb, 2) as total_mb,
                    ROUND(a.total_mb - NVL(b.free_mb, 0), 2) as used_mb,
                    ROUND(NVL(b.free_mb, 0), 2) as free_mb,
                    ROUND((a.total_mb - NVL(b.free_mb, 0)) / NULLIF(a.total_mb, 0) * 100, 2) as used_pct
                FROM
                    (SELECT tablespace_name, SUM(bytes)/1024/1024 as total_mb
                     FROM dba_data_files GROUP BY tablespace_name) a
                LEFT JOIN
                    (SELECT tablespace_name, SUM(bytes)/1024/1024 as free_mb
                     FROM dba_free_space GROUP BY tablespace_name) b
                ON a.tablespace_name = b.tablespace_name
                ORDER BY used_pct DESC
            """)
            for row in cursor.fetchall():
                tablespaces.append({
                    "name": row[0],
                    "total_mb": float(row[1]),
                    "used_mb": float(row[2]),
                    "free_mb": float(row[3]),
                    "used_pct": float(row[4])
                })
        except Exception:
            pass

        # 临时表空间
        try:
            cursor.execute("""
                SELECT tablespace_name, ROUND(SUM(bytes)/1024/1024, 2) as size_mb
                FROM dba_temp_files GROUP BY tablespace_name
            """)
            temp_tablespaces = []
            for row in cursor.fetchall():
                temp_tablespaces.append({
                    "name": row[0],
                    "size_mb": float(row[1])
                })
        except Exception:
            temp_tablespaces = []

        # =============================================
        # 4. 性能指标 (performance)
        # =============================================
        cursor.execute("""
            SELECT value FROM v$sysstat WHERE name='user calls'
        """)
        user_calls = int(cursor.fetchone()[0])
        qps = round(user_calls / uptime, 2) if uptime > 0 else 0

        cursor.execute("""
            SELECT value FROM v$sysstat WHERE name='user commits'
        """)
        user_commits = int(cursor.fetchone()[0])
        cursor.execute("""
            SELECT value FROM v$sysstat WHERE name='user rollbacks'
        """)
        user_rollbacks = int(cursor.fetchone()[0])
        tps = round((user_commits + user_rollbacks) / uptime, 2) if uptime > 0 else 0

        # 缓冲区缓存命中率
        try:
            cursor.execute("""
                SELECT
                    ROUND((1 - SUM(DECODE(name, 'physical reads', value, 0)) /
                           NULLIF(SUM(DECODE(name, 'db block gets', value, 0)) +
                                  SUM(DECODE(name, 'consistent gets', value, 0)), 0)) * 100, 2)
                FROM v$sysstat
                WHERE name IN ('physical reads', 'db block gets', 'consistent gets')
            """)
            buffer_cache_hit_ratio = float(cursor.fetchone()[0])
        except Exception:
            buffer_cache_hit_ratio = 0

        # SGA 相关信息
        try:
            cursor.execute("SELECT ROUND(SUM(value)/1024/1024, 2) FROM v$sga")
            sga_size_mb = float(cursor.fetchone()[0])
        except Exception:
            sga_size_mb = 0

        try:
            cursor.execute("SELECT ROUND(SUM(value)/1024/1024, 2) FROM v$sgainfo WHERE name='Buffer Cache Size'")
            buffer_cache_size_mb = float(cursor.fetchone()[0])
        except Exception:
            buffer_cache_size_mb = 0

        try:
            cursor.execute("SELECT ROUND(SUM(value)/1024/1024, 2) FROM v$sgainfo WHERE name='Shared Pool Size'")
            shared_pool_size_mb = float(cursor.fetchone()[0])
        except Exception:
            shared_pool_size_mb = 0

        # =============================================
        # 17. 扩展性能指标 (extended_perf)
        # =============================================
        try:
            cursor.execute("SELECT SUM(value) FROM v$sysstat WHERE name IN ('session logical reads', 'consistent gets', 'db block gets')")
            logical_reads = int(cursor.fetchone()[0]) or 0
        except Exception:
            logical_reads = 0

        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name='physical reads'")
            physical_reads = int(cursor.fetchone()[0])
        except Exception:
            physical_reads = 0

        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name='physical writes'")
            physical_writes = int(cursor.fetchone()[0])
        except Exception:
            physical_writes = 0

        try:
            cursor.execute("""
                SELECT ROUND((1 - (SUM(pins - reloads) / NULLIF(SUM(pins), 0))) * 100, 2)
                FROM v$librarycache
            """)
            library_cache_hit_ratio = float(cursor.fetchone()[0])
        except Exception:
            library_cache_hit_ratio = 0

        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name='redo size'")
            redo_generation_bytes = int(cursor.fetchone()[0])
        except Exception:
            redo_generation_bytes = 0

        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name='execute count'")
            exec_count = int(cursor.fetchone()[0])
        except Exception:
            exec_count = 0

        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name='parse count (total)'")
            parse_count_total = int(cursor.fetchone()[0])
        except Exception:
            parse_count_total = 0

        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name='parse count (hard)'")
            parse_count_hard = int(cursor.fetchone()[0])
        except Exception:
            parse_count_hard = 0

        try:
            cursor.execute("SELECT value FROM v$sys_time_model WHERE stat_name='DB time'")
            db_time_seconds = round(int(cursor.fetchone()[0]) / 1000000, 2)
        except Exception:
            db_time_seconds = 0

        try:
            cursor.execute("SELECT ROUND(SUM(value)/1024/1024, 2) FROM v$sgainfo WHERE name='Java Pool Size'")
            java_pool_size_mb = float(cursor.fetchone()[0])
        except Exception:
            java_pool_size_mb = 0

        try:
            cursor.execute("SELECT ROUND(SUM(value)/1024/1024, 2) FROM v$sgainfo WHERE name='Large Pool Size'")
            large_pool_size_mb = float(cursor.fetchone()[0])
        except Exception:
            large_pool_size_mb = 0

        try:
            cursor.execute("SELECT ROUND(value/1024/1024, 2) FROM v$pgastat WHERE name = 'total PGA in use'")
            pga_used_mb = float(cursor.fetchone()[0])
        except Exception:
            pga_used_mb = 0

        try:
            cursor.execute("SELECT COUNT(*), ROUND(SUM(bytes)/1024/1024/1024, 2) FROM dba_data_files")
            row = cursor.fetchone()
            datafile_count = int(row[0])
            datafile_size_total_gb = float(row[1])
        except Exception:
            datafile_count = 0
            datafile_size_total_gb = 0

        try:
            cursor.execute("SELECT COUNT(*) FROM dba_tables WHERE owner NOT IN ('SYS','SYSTEM','DBSNMP','XDB','OUTLN','APPQOSSYS','ORACLE_OCM')")
            table_count = int(cursor.fetchone()[0])
        except Exception:
            table_count = 0

        try:
            cursor.execute("SELECT COUNT(*) FROM dba_indexes WHERE owner NOT IN ('SYS','SYSTEM','DBSNMP','XDB','OUTLN','APPQOSSYS','ORACLE_OCM')")
            index_count = int(cursor.fetchone()[0])
        except Exception:
            index_count = 0

        try:
            cursor.execute("SELECT COUNT(*) FROM dba_part_tables WHERE owner NOT IN ('SYS','SYSTEM','DBSNMP','XDB','OUTLN','APPQOSSYS','ORACLE_OCM')")
            partition_count = int(cursor.fetchone()[0])
        except Exception:
            partition_count = 0

        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name='row lock waits'")
            row_lock_contention = int(cursor.fetchone()[0])
        except Exception:
            row_lock_contention = 0

        try:
            cursor.execute("""
                SELECT event, total_waits, time_waited_micro,
                       ROUND(time_waited_micro / NULLIF(total_waits, 0)) as avg_wait
                FROM v$system_event
                WHERE wait_class != 'Idle' AND total_waits > 0
                ORDER BY total_waits DESC
                FETCH FIRST 10 ROWS ONLY
            """)
            top_wait_events = []
            for row in cursor.fetchall():
                top_wait_events.append({
                    "event": row[0],
                    "total_waits": int(row[1]),
                    "time_waited": int(row[2]),
                    "average_wait": int(row[3]) if row[3] else 0
                })
        except Exception:
            top_wait_events = []

        # =============================================
        # 5. 等待事件 (wait)
        # =============================================
        cursor.execute(f"""
            SELECT
                blocking_session as blocker_id,
                sid as waiter_id,
                seconds_in_wait as wait_sec
            FROM v$session
            WHERE blocking_session IS NOT NULL
              AND seconds_in_wait > {LOCK_TIME_THRESHOLD}
        """)
        locks = []
        lock_rows = cursor.fetchall()
        # 查询阻塞者和等待者的用户名
        lock_sid_set = set()
        for row in lock_rows:
            lock_sid_set.add(str(row[0]))
            lock_sid_set.add(str(row[1]))
        lock_user_map = {}
        if lock_sid_set:
            sid_list_str = ",".join(lock_sid_set)
            try:
                cursor.execute(f"SELECT sid, username FROM v$session WHERE sid IN ({sid_list_str})")
                for r in cursor.fetchall():
                    lock_user_map[str(r[0])] = r[1] or 'N/A'
            except Exception:
                pass
        for row in lock_rows:
            blocker_sid = str(row[0])
            waiter_sid = str(row[1])
            locks.append({
                "blocker_id": blocker_sid,
                "blocker_user": lock_user_map.get(blocker_sid, 'N/A'),
                "waiter_id": waiter_sid,
                "waiter_user": lock_user_map.get(waiter_sid, 'N/A'),
                "seconds": int(row[2]),
                "wait_event": "enq: TX - row lock contention"
            })
        lock_wait_count = len(locks)

        # 等待事件统计
        cursor.execute("""
            SELECT event, COUNT(*) as cnt
            FROM v$session WHERE wait_class != 'Idle'
            GROUP BY event ORDER BY cnt DESC
            FETCH FIRST 10 ROWS ONLY
        """)
        wait_events_by_type = []
        for row in cursor.fetchall():
            wait_events_by_type.append({
                "wait_event": row[0],
                "count": int(row[1])
            })

        # =============================================
        # 6. 会话详情 (session_detail) - P0
        # =============================================
        cursor.execute("""
            SELECT sid, serial#, username, status, osuser, machine,
                   program, sql_id, seconds_in_wait, state, event
            FROM v$session
            WHERE username IS NOT NULL
            ORDER BY status, sid
        """)
        session_list = []
        for row in cursor.fetchall():
            sid = row[0]
            serial = row[1]
            session_list.append({
                "sid": sid,
                "serial": serial,
                "sid_serial": f"{sid}/{serial}",
                "username": row[2] or 'N/A',
                "status": row[3] or 'N/A',
                "osuser": row[4] or 'N/A',
                "machine": row[5] or 'N/A',
                "program": row[6] or 'N/A',
                "sql_id": row[7] or 'N/A',
                "seconds_in_wait": int(row[8]) if row[8] else 0,
                "state": row[9] or 'N/A',
                "event": row[10] or 'N/A',
                "wait_event": row[10] or 'N/A'
            })

        # 按状态统计
        cursor.execute("SELECT status, COUNT(*) FROM v$session GROUP BY status")
        session_by_state = []
        for row in cursor.fetchall():
            session_by_state.append({
                "state": row[0],
                "count": int(row[1])
            })

        # =============================================
        # 7. SQL统计 (sql) - P0
        # =============================================
        top_sql_by_latency = []
        try:
            cursor.execute("""
                SELECT sql_id, sql_text, executions, elapsed_time/1000000 as elapsed_sec,
                       disk_reads, buffer_gets
                FROM v$sql
                WHERE executions > 0
                ORDER BY elapsed_time DESC
                FETCH FIRST 10 ROWS ONLY
            """)
            for row in cursor.fetchall():
                top_sql_by_latency.append({
                    "sql_id": row[0] or 'N/A',
                    "sql_text": (row[1] or 'N/A')[:200],
                    "executions": int(row[2]),
                    "elapsed_sec": round(float(row[3]), 4),
                    "disk_reads": int(row[4]),
                    "buffer_gets": int(row[5])
                })
        except Exception:
            pass

        cursor.execute("SELECT value FROM v$sysstat WHERE name='sorts (disk)'")
        sorts_disk = int(cursor.fetchone()[0])

        # =============================================
        # 8. 缓冲池 (buffer) - P0
        # =============================================
        try:
            cursor.execute("""
                SELECT name, block_size, ROUND(current_size/1024/1024, 2) as size_mb,
                       buffers, target_buffers
                FROM v$buffer_pool
            """)
            buffer_pools = []
            for row in cursor.fetchall():
                buffer_pools.append({
                    "name": row[0],
                    "block_size": int(row[1]),
                    "size_mb": float(row[2]),
                    "buffers": int(row[3]),
                    "target_buffers": int(row[4])
                })
        except Exception:
            buffer_pools = []

        # =============================================
        # 9. 事务统计 (transaction) - P1
        # =============================================
        try:
            cursor.execute("SELECT count(*) FROM v$transaction")
            active_transactions = int(cursor.fetchone()[0])
        except Exception:
            active_transactions = 0

        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name='user commits'")
            total_commits = int(cursor.fetchone()[0])
        except Exception:
            total_commits = 0

        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name='user rollbacks'")
            total_rollbacks = int(cursor.fetchone()[0])
        except Exception:
            total_rollbacks = 0

        # =============================================
        # 10. 对象统计 (object) - P2
        # =============================================
        table_size_top20 = []
        try:
            cursor.execute("""
                SELECT owner, segment_name, ROUND(SUM(bytes)/1024/1024, 2) as size_mb
                FROM dba_segments
                WHERE segment_type = 'TABLE'
                GROUP BY owner, segment_name
                ORDER BY SUM(bytes) DESC
                FETCH FIRST 20 ROWS ONLY
            """)
            for row in cursor.fetchall():
                table_size_top20.append({
                    "owner": row[0],
                    "segment_name": row[1],
                    "table_name": row[1],
                    "size_mb": float(row[2])
                })
        except Exception:
            pass

        unused_indexes = []
        try:
            cursor.execute("""
                SELECT owner, index_name, table_name
                FROM dba_indexes
                WHERE status = 'UNUSABLE'
                FETCH FIRST 20 ROWS ONLY
            """)
            for row in cursor.fetchall():
                unused_indexes.append({
                    "schema": row[0],
                    "index": row[1],
                    "table": row[2]
                })
        except Exception:
            pass

        # =============================================
        # 11. RAC 集群
        # =============================================
        rac_instances = []
        rac_node_count = 0
        try:
            cursor.execute("SELECT COUNT(*) FROM gv$instance")
            rac_node_count = int(cursor.fetchone()[0])
        except Exception:
            pass

        try:
            cursor.execute("""
                SELECT inst_id, instance_name, host_name, status
                FROM gv$instance
            """)
            for row in cursor.fetchall():
                rac_instances.append({
                    "inst_id": int(row[0]),
                    "instance_name": row[1] or 'N/A',
                    "host_name": row[2] or 'N/A',
                    "status": row[3] or 'N/A'
                })
        except Exception:
            pass

        # =============================================
        # 12. ADG 备用数据库
        # =============================================
        adg_lag_seconds = 0
        try:
            cursor.execute("""
                SELECT value FROM v$dataguard_stats WHERE name='apply lag'
            """)
            adg_lag_seconds = int(cursor.fetchone()[0])
        except Exception:
            pass

        try:
            cursor.execute("""
                SELECT value FROM v$dataguard_stats WHERE name='transport lag'
            """)
            adg_transport_lag = int(cursor.fetchone()[0])
        except Exception:
            adg_transport_lag = 0

        try:
            cursor.execute("SELECT database_role FROM v$database")
            adg_role = cursor.fetchone()[0]
        except Exception:
            adg_role = 'N/A'

        # =============================================
        # 13. 配置参数 (config)
        # =============================================
        config_params = {}
        config_keys = [
            'sga_target', 'pga_aggregate_target', 'processes', 'sessions',
            'open_cursors', 'db_block_size', 'db_cache_size', 'shared_pool_size',
            'log_buffer', 'java_pool_size', 'streams_pool_size',
            'undo_retention', 'optimizer_mode', 'statistics_level'
        ]
        for key in config_keys:
            try:
                cursor.execute(f"SELECT value FROM v$parameter WHERE name='{key}'")
                config_params[key] = cursor.fetchone()[0]
            except Exception:
                pass

        # =============================================
        # 14. 日志统计 (log)
        # =============================================
        try:
            cursor.execute("SELECT count(*) FROM v$log")
            log_count = int(cursor.fetchone()[0])
        except Exception:
            log_count = 0

        try:
            cursor.execute("SELECT count(*) FROM v$log WHERE status='CURRENT'")
            log_current_count = int(cursor.fetchone()[0])
        except Exception:
            log_current_count = 0

        # =============================================
        # 15. 高可用 (ha)
        # =============================================
        ha_status = {
            "database_role": database_role,
            "open_mode": open_mode,
            "rac_node_count": rac_node_count,
            "adg_lag_seconds": adg_lag_seconds,
        }

        # =============================================
        # 16. 资源限制 (resource)
        # =============================================
        resource_limits = []
        for resource in ['processes', 'sessions', 'transactions']:
            try:
                cursor.execute(f"""
                    SELECT resource_name, current_utilization, limit_value
                    FROM v$resource_limit
                    WHERE resource_name = '{resource}'
                """)
                row = cursor.fetchone()
                if row:
                    resource_limits.append({
                        "resource_name": row[0],
                        "current_utilization": int(row[1]),
                        "limit_value": int(row[2])
                    })
            except Exception:
                pass

        cursor.close()

        return {
            # 基础信息
            "version": str(version)[:50] + "...",
            "instance_name": instance_name,
            "host_name": host_name,
            "database_name": database_name,
            "database_role": database_role,
            "open_mode": open_mode,
            "uptime_seconds": int(uptime),

            # 连接会话
            "active_connections": active_connections,
            "total_connections": total_connections,
            "inactive_connections": inactive_connections,
            "max_connections": max_connections,
            "conn_usage_pct": conn_usage_pct,
            "blocked_sessions": blocked_sessions,
            # 前端兼容字段
            "active_sessions": active_connections,
            "total_sessions": total_connections,

            # 空间
            "tablespaces": tablespaces,
            "temp_tablespaces": temp_tablespaces,

            # 性能
            "qps": qps,
            "tps": tps,
            "buffer_cache_hit_ratio": buffer_cache_hit_ratio,
            "buffer_hit_ratio": buffer_cache_hit_ratio,
            "sga_size_mb": sga_size_mb,
            "buffer_cache_size_mb": buffer_cache_size_mb,
            "buffer_cache_mb": buffer_cache_size_mb,
            "shared_pool_size_mb": shared_pool_size_mb,
            "shared_pool_mb": shared_pool_size_mb,

            # === 新增扩展性能指标 ===
            "logical_reads": logical_reads,
            "physical_reads": physical_reads,
            "physical_writes": physical_writes,
            "library_cache_hit_ratio": library_cache_hit_ratio,
            "redo_generation_bytes": redo_generation_bytes,
            "exec_count": exec_count,
            "parse_count_total": parse_count_total,
            "parse_count_hard": parse_count_hard,
            "db_time_seconds": db_time_seconds,

            # === 新增内存池指标 ===
            "java_pool_size_mb": java_pool_size_mb,
            "java_pool_mb": java_pool_size_mb,
            "large_pool_size_mb": large_pool_size_mb,
            "large_pool_mb": large_pool_size_mb,
            "pga_used_mb": pga_used_mb,

            # === 新增数据文件统计 ===
            "datafile_count": datafile_count,
            "datafile_size_total_gb": datafile_size_total_gb,

            # === 新增对象统计 ===
            "table_count": table_count,
            "index_count": index_count,
            "partition_count": partition_count,

            # === 扩展事务统计 ===
            "row_lock_contention": row_lock_contention,
            "committed_transactions": total_commits,
            "rolled_back_transactions": total_rollbacks,

            # 等待事件 (同时保留两种格式)
            "locks": locks,
            "lock_wait_count": lock_wait_count,
            "wait_events_by_type": wait_events_by_type,
            "top_wait_events": top_wait_events,

            # 会话详情
            "session_list": session_list,
            "session_by_state": session_by_state,

            # SQL统计
            "top_sql_by_latency": top_sql_by_latency,
            "sorts_disk": sorts_disk,

            # 缓冲池
            "buffer_pools": buffer_pools,

            # 事务统计
            "active_transactions": active_transactions,
            "total_commits": total_commits,
            "total_rollbacks": total_rollbacks,
            "commits": total_commits,
            "rollbacks": total_rollbacks,

            # 对象统计
            "table_size_top20": table_size_top20,
            "unused_indexes": unused_indexes,

            # RAC 集群
            "rac_instances": rac_instances,
            "rac_node_count": rac_node_count,
            "rac_instance_count": rac_node_count,

            # ADG
            "adg_role": adg_role,
            "adg_lag_seconds": adg_lag_seconds,
            "adg_transport_lag": adg_transport_lag,
            "dg_database_role": adg_role,
            "dg_protection_mode": "MAXIMUM PERFORMANCE",

            # 配置参数
            "config_params": config_params,

            # 日志
            "log_count": log_count,
            "log_current_count": log_current_count,

            # 高可用
            "ha_status": ha_status,

            # 资源限制
            "resource_limits": resource_limits,
        }
