# -*- coding: utf-8 -*-
"""
PostgreSQL 数据库检查器 (含流复制)

从 start_monitor.py 中提取，v3.0 重构 + P0-2 指标补充。
采集 14 大类指标：基础信息、会话、空间、性能、等待事件、会话详情、
SQL统计、复制集群、对象统计、配置参数、缓冲池、事务、日志、高可用、资源限制。
"""

import psycopg2

from monitor.checkers.base import BaseDBChecker, LOCK_TIME_THRESHOLD
from monitor.pg_capacity import postgresql_db_used_pct


class PostgreSQLChecker(BaseDBChecker):

    def get_connection(self, config):
        dbname = config.service_name if config.service_name else 'postgres'
        conn = psycopg2.connect(
            database=dbname, user=config.username, password=config.get_password(),
            host=config.host, port=config.port, connect_timeout=5
        )
        conn.autocommit = True  # Avoid "current transaction aborted" errors
        return conn

    def collect_metrics(self, config, conn):
        cur = conn.cursor()

        # =============================================
        # 1. 基础信息 (basic)
        # =============================================
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]

        cur.execute("SHOW server_version_num")
        server_version_num = cur.fetchone()[0]

        cur.execute("SHOW data_directory")
        data_directory = cur.fetchone()[0]

        cur.execute("SHOW port")
        port = int(cur.fetchone()[0])

        cur.execute("SELECT extract(epoch from (now() - pg_postmaster_start_time()))")
        uptime = int(cur.fetchone()[0])

        cur.execute("SELECT current_database()")
        current_database = cur.fetchone()[0]

        cur.execute("SELECT inet_server_addr()")
        server_addr = str(cur.fetchone()[0])

        # =============================================
        # 2. 连接与会话 (session)
        # =============================================
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
        active_connections = int(cur.fetchone()[0])

        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'idle'")
        idle_connections = int(cur.fetchone()[0])

        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction'")
        idle_in_transaction = int(cur.fetchone()[0])

        cur.execute("SELECT count(*) FROM pg_stat_activity")
        total_connections = int(cur.fetchone()[0])

        cur.execute("SHOW max_connections")
        max_connections = int(cur.fetchone()[0])
        conn_usage_pct = round((active_connections / max_connections) * 100, 2) if max_connections > 0 else 0

        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type IS NOT NULL")
        waiting_connections = int(cur.fetchone()[0])

        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE query_start < NOW() - INTERVAL '5 minutes' AND state = 'active'")
        long_running_queries = int(cur.fetchone()[0])

        # =============================================
        # 3. 空间使用 (space)
        # =============================================
        cur.execute("SELECT COALESCE(SUM(pg_tablespace_size(oid)), 0) FROM pg_tablespace")
        total_tablespace_bytes = int(cur.fetchone()[0])
        total_tablespace_mb = round(total_tablespace_bytes / 1024.0 / 1024.0, 2) if total_tablespace_bytes > 0 else 0
        total_tablespace_gb = round(total_tablespace_bytes / 1024.0 / 1024.0 / 1024.0, 2)

        cur.execute("""
            SELECT datname, pg_database_size(datname) AS size_bytes
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY size_bytes DESC
            LIMIT 10
        """)
        database_sizes = []
        tablespaces = []
        for row in cur.fetchall():
            name = row[0]
            size_bytes = int(row[1])
            size_mb = round(size_bytes / 1024.0 / 1024.0, 2)
            database_sizes.append({"name": name, "size_mb": float(size_mb), "size_gb": round(size_mb / 1024, 2)})
            used_pct = postgresql_db_used_pct(size_bytes, total_tablespace_bytes)
            tablespaces.append({
                "name": name,
                "total_mb": total_tablespace_mb,
                "used_mb": size_mb,
                "used_pct": used_pct,
            })

        # 表空间大小
        cur.execute("""
            SELECT spcname, pg_tablespace_size(oid) / 1024 / 1024 as size_mb
            FROM pg_tablespace ORDER BY size_mb DESC
        """)
        tablespace_sizes = []
        for row in cur.fetchall():
            tablespace_sizes.append({
                "name": row[0],
                "size_mb": round(float(row[1]), 2)
            })

        # =============================================
        # 4. 性能指标 (performance)
        # =============================================
        cur.execute("""
            SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, 
                   tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted,
                   conflicts, temp_files, temp_bytes, deadlocks
            FROM pg_stat_database WHERE datname = current_database()
        """)
        db_stats = cur.fetchone()
        if db_stats:
            numbackends = db_stats[0]
            xact_commit = db_stats[1]
            xact_rollback = db_stats[2]
            blks_read = db_stats[3]
            blks_hit = db_stats[4]
            tup_returned = db_stats[5]
            tup_fetched = db_stats[6]
            tup_inserted = db_stats[7]
            tup_updated = db_stats[8]
            tup_deleted = db_stats[9]
            conflicts = db_stats[10]
            temp_files = db_stats[11]
            temp_bytes = db_stats[12]
            deadlocks = db_stats[13]
        else:
            numbackends = xact_commit = xact_rollback = blks_read = blks_hit = 0
            tup_returned = tup_fetched = tup_inserted = tup_updated = tup_deleted = 0
            conflicts = temp_files = temp_bytes = deadlocks = 0

        tps = round((xact_commit + xact_rollback) / uptime, 2) if uptime > 0 else 0
        cache_hit_ratio = round(blks_hit / (blks_hit + blks_read) * 100, 2) if (blks_hit + blks_read) > 0 else 0

        # 共享缓冲区
        cur.execute("SHOW shared_buffers")
        shared_buffers = cur.fetchone()[0]
        cur.execute("SHOW effective_cache_size")
        effective_cache_size = cur.fetchone()[0]
        cur.execute("SHOW work_mem")
        work_mem = cur.fetchone()[0]
        cur.execute("SHOW maintenance_work_mem")
        maintenance_work_mem = cur.fetchone()[0]
        cur.execute("SHOW checkpoint_completion_target")
        checkpoint_completion_target = cur.fetchone()[0]

        # BGWriter 统计
        cur.execute("""
            SELECT checkpoints_timed, checkpoints_req, checkpoint_write_time, 
                   checkpoint_sync_time, buffers_checkpoint, buffers_clean, buffers_backend
            FROM pg_stat_bgwriter
        """)
        bgwriter_stats = cur.fetchone()
        if bgwriter_stats:
            checkpoints_timed = bgwriter_stats[0]
            checkpoints_req = bgwriter_stats[1]
            checkpoint_write_time = bgwriter_stats[2]
            checkpoint_sync_time = bgwriter_stats[3]
            buffers_checkpoint = bgwriter_stats[4]
            buffers_clean = bgwriter_stats[5]
            buffers_backend = bgwriter_stats[6]
        else:
            checkpoints_timed = checkpoints_req = checkpoint_write_time = 0
            checkpoint_sync_time = buffers_checkpoint = buffers_clean = buffers_backend = 0

        # =============================================
        # 5. 等待事件 (wait) - 增强
        # =============================================
        cur.execute(f"""
            SELECT 
                blocked_locks.pid as blocked_pid,
                blocked_activity.usename as blocked_user,
                blocking_locks.pid as blocking_pid,
                blocking_activity.usename as blocking_user,
                EXTRACT(EPOCH FROM (NOW() - blocked_activity.query_start))::INTEGER as wait_sec,
                blocked_locks.locktype,
                blocked_locks.relation::regclass as relation
            FROM pg_catalog.pg_locks blocked_locks
            JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
            JOIN pg_catalog.pg_locks blocking_locks 
                ON blocking_locks.locktype = blocked_locks.locktype
                AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
                AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
                AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
                AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
                AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
                AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
                AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
                AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
                AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
                AND blocking_locks.pid != blocked_locks.pid
            JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
            WHERE NOT blocked_locks.GRANTED
              AND EXTRACT(EPOCH FROM (NOW() - blocked_activity.query_start)) > %s
        """, (LOCK_TIME_THRESHOLD,))
        locks = []
        for row in cur.fetchall():
            locks.append({
                "blocker_id": str(row[2]),
                "blocker_user": row[3] or 'N/A',
                "waiter_id": str(row[0]),
                "waiter_user": row[1] or 'N/A',
                "seconds": int(row[4]),
                "locktype": row[5] or 'N/A',
                "relation": str(row[6]) if row[6] else 'N/A'
            })

        lock_wait_count = len(locks)

        # 等待事件类型
        cur.execute("""
            SELECT wait_event_type, wait_event, COUNT(*)
            FROM pg_stat_activity
            WHERE wait_event IS NOT NULL
            GROUP BY wait_event_type, wait_event
            ORDER BY COUNT(*) DESC
            LIMIT 10
        """)
        wait_events_by_type = []
        for row in cur.fetchall():
            wait_events_by_type.append({
                "wait_event_type": row[0] or 'N/A',
                "wait_event": row[1] or 'N/A',
                "count": int(row[2])
            })

        # =============================================
        # 6. 会话详情 (session_detail) - P0
        # =============================================
        cur.execute("""
            SELECT 
                pid, usename, application_name, client_addr, client_hostname,
                backend_start, query_start, state, wait_event_type, wait_event,
                query, LEFT(query, 200) as query_preview
            FROM pg_stat_activity
            WHERE pid != pg_backend_pid()
            ORDER BY state, query_start DESC
            LIMIT 100
        """)
        session_list = []
        for row in cur.fetchall():
            session_list.append({
                "pid": row[0],
                "usename": row[1] or 'N/A',
                "application_name": row[2] or 'N/A',
                "client_addr": str(row[3]) if row[3] else 'N/A',
                "client_hostname": row[4] or 'N/A',
                "backend_start": str(row[5]),
                "query_start": str(row[6]) if row[6] else 'N/A',
                "state": row[7] or 'N/A',
                "wait_event_type": row[8] or 'N/A',
                "wait_event": row[9] or 'N/A',
                "query": (row[10] or 'N/A')[:200]
            })

        # 按状态统计
        cur.execute("SELECT state, COUNT(*) FROM pg_stat_activity GROUP BY state")
        session_by_state = []
        for row in cur.fetchall():
            session_by_state.append({
                "state": row[0] or 'N/A',
                "count": int(row[1])
            })

        # 按应用统计
        cur.execute("SELECT application_name, COUNT(*) FROM pg_stat_activity GROUP BY application_name")
        session_by_application = []
        for row in cur.fetchall():
            session_by_application.append({
                "application": row[0] or 'N/A',
                "count": int(row[1])
            })

        # =============================================
        # 7. SQL统计 (sql) - P0
        # =============================================
        cur.execute("""
            SELECT count(*) 
            FROM pg_stat_activity 
            WHERE state = 'active' 
              AND query_start < NOW() - INTERVAL '10 seconds'
        """)
        slow_queries_active = int(cur.fetchone()[0])

        # pg_stat_statements 统计 (需要扩展)
        top_sql_by_calls = []
        top_sql_by_total_time = []
        try:
            cur.execute("""
                SELECT query, calls, total_time, rows, shared_blks_hit, shared_blks_read
                FROM pg_stat_statements
                ORDER BY calls DESC
                LIMIT 10
            """)
            for row in cur.fetchall():
                top_sql_by_calls.append({
                    "query": (row[0] or 'N/A')[:200],
                    "calls": int(row[1]),
                    "total_time_ms": round(float(row[2]), 2),
                    "rows": int(row[3]),
                    "shared_blks_hit": int(row[4]),
                    "shared_blks_read": int(row[5])
                })
        except Exception:
            pass

        try:
            cur.execute("""
                SELECT query, calls, total_time, rows
                FROM pg_stat_statements
                ORDER BY total_time DESC
                LIMIT 10
            """)
            for row in cur.fetchall():
                top_sql_by_total_time.append({
                    "query": (row[0] or 'N/A')[:200],
                    "calls": int(row[1]),
                    "total_time_ms": round(float(row[2]), 2),
                    "rows": int(row[3])
                })
        except Exception:
            pass

        # 表扫描统计
        cur.execute("""
            SELECT relname, seq_scan, idx_scan, n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, n_dead_tup
            FROM pg_stat_user_tables
            ORDER BY seq_scan DESC
            LIMIT 10
        """)
        table_scan_stats = []
        for row in cur.fetchall():
            table_scan_stats.append({
                "relname": row[0],
                "seq_scan": int(row[1]),
                "idx_scan": int(row[2]),
                "n_tup_ins": int(row[3]),
                "n_tup_upd": int(row[4]),
                "n_tup_del": int(row[5]),
                "n_live_tup": int(row[6]),
                "n_dead_tup": int(row[7])
            })

        # =============================================
        # 8. 复制与集群 (replication) - P1
        # =============================================
        cur.execute("SELECT pg_is_in_recovery()")
        is_in_recovery = cur.fetchone()[0]

        cur.execute("""
            SELECT client_addr, state, sent_lsn, write_lsn, flush_lsn, replay_lsn,
                   sync_state, reply_time
            FROM pg_stat_replication
            ORDER BY reply_time DESC
        """)
        replication_slots = []
        for row in cur.fetchall():
            replication_slots.append({
                "client_addr": str(row[0]) if row[0] else 'N/A',
                "state": row[1] or 'N/A',
                "sent_lsn": str(row[2]) if row[2] else 'N/A',
                "write_lsn": str(row[3]) if row[3] else 'N/A',
                "flush_lsn": str(row[4]) if row[4] else 'N/A',
                "replay_lsn": str(row[5]) if row[5] else 'N/A',
                "sync_state": row[6] or 'N/A',
                "reply_time": str(row[7]) if row[7] else 'N/A'
            })

        cur.execute("SELECT COUNT(*) FROM pg_stat_replication")
        replication_count = int(cur.fetchone()[0])

        cur.execute("SHOW wal_level")
        wal_level = cur.fetchone()[0]
        cur.execute("SHOW max_wal_senders")
        max_wal_senders = int(cur.fetchone()[0])

        # WAL延迟
        try:
            cur.execute("""
                SELECT pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn(),
                       pg_last_wal_receive_lsn() - pg_last_wal_replay_lsn() as replication_lag
            """)
            lag_info = cur.fetchone()
            last_wal_receive_lsn = str(lag_info[0]) if lag_info[0] else 'N/A'
            last_wal_replay_lsn = str(lag_info[1]) if lag_info[1] else 'N/A'
            wal_lag = lag_info[2] if lag_info[2] else 0
        except Exception:
            last_wal_receive_lsn = last_wal_replay_lsn = 'N/A'
            wal_lag = 0

        # 复制类型
        try:
            cur.execute("""
                SELECT replication_type
                FROM pg_stat_replication
                LIMIT 1
            """)
            row = cur.fetchone()
            physical_replication_type = row[0] if row else 'N/A'
        except Exception:
            physical_replication_type = 'N/A'

        # =============================================
        # 9. 对象统计 (object) - P2补全
        # =============================================
        # Top 20 表大小
        cur.execute("""
            SELECT schemaname, relname, 
                   pg_size_pretty(pg_total_relation_size(schemaname||'.'||relname)) as size_pretty,
                   pg_total_relation_size(schemaname||'.'||relname) as size_bytes
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(schemaname||'.'||relname) DESC
            LIMIT 20
        """)
        table_size_top20 = []
        for row in cur.fetchall():
            table_size_top20.append({
                "schema": row[0],
                "table": row[1],
                "size_pretty": row[2],
                "size_bytes": int(row[3])
            })

        # 未使用索引
        unused_indexes = []
        try:
            cur.execute("""
                SELECT schemaname, relname, indexname
                FROM pg_stat_user_indexes
                WHERE idx_scan = 0
                ORDER BY schemaname, relname
                LIMIT 20
            """)
            for row in cur.fetchall():
                unused_indexes.append({
                    "schema": row[0],
                    "table": row[1],
                    "index": row[2]
                })
        except Exception:
            pass

        # 需要VACUUM的表
        tables_needing_vacuum = []
        try:
            cur.execute("""
                SELECT schemaname, relname, n_dead_tup, n_live_tup,
                       ROUND(n_dead_tup::float / NULLIF(n_live_tup + n_dead_tup, 0) * 100, 2) as dead_pct
                FROM pg_stat_user_tables
                WHERE n_dead_tup > 1000
                ORDER BY n_dead_tup DESC
                LIMIT 20
            """)
            for row in cur.fetchall():
                tables_needing_vacuum.append({
                    "schema": row[0],
                    "table": row[1],
                    "n_dead_tup": int(row[2]),
                    "n_live_tup": int(row[3]),
                    "dead_pct": float(row[4]) if row[4] else 0
                })
        except Exception:
            pass

        # 序列使用
        try:
            cur.execute("SELECT COUNT(*) FROM pg_sequences")
            sequence_count = int(cur.fetchone()[0])
        except Exception:
            sequence_count = 0

        # =============================================
        # 10. 配置参数 (config) - P1
        # =============================================
        config_params = {}
        config_keys = [
            'shared_buffers', 'effective_cache_size', 'maintenance_work_mem',
            'work_mem', 'max_connections', 'max_worker_processes', 'wal_level',
            'archive_mode', 'checkpoint_timeout', 'random_page_cost',
            'effective_io_concurrency', 'max_prepared_transactions'
        ]
        for key in config_keys:
            try:
                cur.execute(f"SHOW {key}")
                config_params[key] = cur.fetchone()[0]
            except Exception:
                pass

        # =============================================
        # 11. 缓冲池 (buffer) - P0
        # =============================================
        cur.execute("""
            SELECT buffers_checkpoint, buffers_clean, buffers_backend, buffers_alloc
            FROM pg_stat_bgwriter
        """)
        bgwriter = cur.fetchone()
        if bgwriter:
            buffers_checkpoint_val = bgwriter[0]
            buffers_clean_val = bgwriter[1]
            buffers_backend_val = bgwriter[2]
            buffers_alloc = bgwriter[3]
        else:
            buffers_checkpoint_val = buffers_clean_val = buffers_backend_val = buffers_alloc = 0

        # =============================================
        # 12. 事务统计 (transaction) - P1
        # =============================================
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state LIKE '%transaction%'")
        active_transactions = int(cur.fetchone()[0])

        cur.execute("""
            SELECT EXTRACT(EPOCH FROM NOW() - xact_start)::integer as duration
            FROM pg_stat_activity
            WHERE xact_start IS NOT NULL AND state = 'idle in transaction'
            ORDER BY duration DESC
            LIMIT 10
        """)
        idle_in_transaction_long = []
        for row in cur.fetchall():
            idle_in_transaction_long.append({
                "duration_sec": int(row[0])
            })

        # =============================================
        # 13. 日志统计 (log) - P1
        # =============================================
        cur.execute("SHOW log_directory")
        log_directory = cur.fetchone()[0]
        cur.execute("SHOW log_filename")
        log_filename = cur.fetchone()[0]
        cur.execute("SHOW logging_collector")
        logging_collector = cur.fetchone()[0]
        cur.execute("SHOW log_connections")
        log_connections = cur.fetchone()[0]
        cur.execute("SHOW log_disconnections")
        log_disconnections = cur.fetchone()[0]

        # =============================================
        # 14. 高可用 (ha) - P2
        # =============================================
        # (is_in_recovery 和 wal 相关指标已在上面采集)

        # =============================================
        # 15. 资源限制 (resource) - P2
        # =============================================
        resource_limits = []
        for resource in ['max_connections', 'max_prepared_transactions', 'max_locks_per_transaction']:
            try:
                cur.execute(f"SHOW {resource}")
                value = cur.fetchone()[0]
                resource_limits.append({
                    "resource_name": resource,
                    "value": value
                })
            except Exception:
                pass

        cur.close()

        return {
            # 基础信息
            "version": version[:50] + "...",
            "server_version_num": server_version_num,
            "data_directory": data_directory,
            "port": port,
            "current_database": current_database,
            "server_addr": server_addr,
            "uptime_seconds": uptime,

            # 连接会话
            "active_connections": active_connections,
            "idle_connections": idle_connections,
            "idle_in_transaction": idle_in_transaction,
            "total_connections": total_connections,
            "max_connections": max_connections,
            "conn_usage_pct": conn_usage_pct,
            "waiting_connections": waiting_connections,
            "long_running_queries": long_running_queries,

            # 空间
            "database_sizes": database_sizes,
            "tablespaces": tablespaces,
            "tablespace_sizes": tablespace_sizes,
            "total_tablespace_gb": total_tablespace_gb,

            # 性能
            "tps": tps,
            "xact_commit": xact_commit,
            "xact_rollback": xact_rollback,
            "blks_read": blks_read,
            "blks_hit": blks_hit,
            "cache_hit_ratio": cache_hit_ratio,
            "tup_returned": tup_returned,
            "tup_fetched": tup_fetched,
            "tup_inserted": tup_inserted,
            "tup_updated": tup_updated,
            "tup_deleted": tup_deleted,
            "conflicts": conflicts,
            "temp_files": temp_files,
            "temp_bytes": temp_bytes,
            "deadlocks": deadlocks,
            "shared_buffers": shared_buffers,
            "effective_cache_size": effective_cache_size,
            "work_mem": work_mem,
            "maintenance_work_mem": maintenance_work_mem,
            "checkpoint_completion_target": checkpoint_completion_target,
            "bgwriter_checkpoints_timed": checkpoints_timed,
            "bgwriter_checkpoints_req": checkpoints_req,
            "buffers_checkpoint": buffers_checkpoint,
            "buffers_clean": buffers_clean,
            "buffers_backend": buffers_backend,

            # 等待事件
            "locks": locks,
            "lock_wait_count": lock_wait_count,
            "wait_events_by_type": wait_events_by_type,

            # 会话详情
            "session_list": session_list,
            "session_by_state": session_by_state,
            "session_by_application": session_by_application,

            # SQL统计
            "slow_queries_active": slow_queries_active,
            "top_sql_by_calls": top_sql_by_calls,
            "top_sql_by_total_time": top_sql_by_total_time,
            "table_scan_stats": table_scan_stats,

            # 对象统计
            "table_size_top20": table_size_top20,
            "unused_indexes": unused_indexes,
            "tables_needing_vacuum": tables_needing_vacuum,
            "sequence_count": sequence_count,

            # 复制集群
            "is_in_recovery": is_in_recovery,
            "replication_slots": replication_slots,
            "replication_count": replication_count,
            "wal_level": wal_level,
            "wal_lag": wal_lag,
            "last_wal_receive_lsn": last_wal_receive_lsn,
            "last_wal_replay_lsn": last_wal_replay_lsn,
            "physical_replication_type": physical_replication_type,
            "max_wal_senders": max_wal_senders,

            # 配置参数
            "config_params": config_params,

            # 缓冲池
            "buffers_alloc": buffers_alloc,

            # 事务统计
            "active_transactions": active_transactions,
            "idle_in_transaction_long": idle_in_transaction_long,

            # 日志
            "log_directory": log_directory,
            "log_filename": log_filename,
            "logging_collector": logging_collector,
            "log_connections": log_connections,
            "log_disconnections": log_disconnections,

            # 高可用
            "last_wal_receive_lsn": last_wal_receive_lsn,
            "last_wal_replay_lsn": last_wal_replay_lsn,
            "replication_lag_bytes": wal_lag,

            # 资源限制
            "resource_limits": resource_limits
        }
