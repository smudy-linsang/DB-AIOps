# -*- coding: utf-8 -*-
"""
MySQL 数据库检查器 (含主从复制)

从 start_monitor.py 中提取，v3.0 重构 + P0-2 指标补充。
采集 23 大类指标：基础信息、会话、空间、性能、等待事件、会话详情、
SQL统计、复制集群、配置参数、缓冲池、事务、日志、安全审计、对象统计、
Redo日志、行锁时间、临时表、排序统计、长事务、死锁记录、Top等待事件、
网络吞吐、Handler统计、表打开统计、Binlog缓存、Select类型、InnoDB页操作、
连接错误、Change Buffer、Adaptive Hash、Doublewrite、Top SQL扩展、
临时表空间、对象汇总、高可用状态、Buffer Pool详情、资源限制。
"""

import pymysql

from monitor.checkers.base import BaseDBChecker, LOCK_TIME_THRESHOLD


class MySQLChecker(BaseDBChecker):

    def get_connection(self, config):
        return pymysql.connect(
            host=config.host, port=config.port,
            user=config.username, password=config.get_password(),
            connect_timeout=5, cursorclass=pymysql.cursors.DictCursor
        )

    def collect_metrics(self, config, conn):
        with conn.cursor() as cursor:
            # =============================================
            # 1. 基础信息 (basic)
            # =============================================
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()['VERSION()']

            cursor.execute("SELECT @@server_id")
            try:
                server_id = int(cursor.fetchone()['@@server_id'])
            except Exception:
                server_id = 0

            cursor.execute("SELECT @@datadir")
            datadir = cursor.fetchone()['@@datadir']

            cursor.execute("SELECT @@port")
            port = int(cursor.fetchone()['@@port'])

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            uptime = int(cursor.fetchone()['Value'])

            cursor.execute("SELECT DATABASE()")
            current_db = cursor.fetchone()['DATABASE()']

            # =============================================
            # 2. 连接与会话 (session)
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
            threads_connected = int(cursor.fetchone()['Value'])

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_running'")
            try:
                threads_running = int(cursor.fetchone()['Value'])
            except Exception:
                threads_running = 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_cached'")
            try:
                threads_cached = int(cursor.fetchone()['Value'])
            except Exception:
                threads_cached = 0

            cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
            max_connections = int(cursor.fetchone()['Value'])
            conn_usage_pct = round((threads_connected / max_connections) * 100, 2) if max_connections > 0 else 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Aborted_connects'")
            aborted_connects = int(cursor.fetchone()['Value'])

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Aborted_clients'")
            aborted_clients = int(cursor.fetchone()['Value'])

            # =============================================
            # 3. 空间使用 (space)
            # =============================================
            cursor.execute("""
                SELECT 
                    table_schema as db_name,
                    ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) as size_mb,
                    COUNT(*) as table_count
                FROM information_schema.tables
                WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
                GROUP BY table_schema
                ORDER BY size_mb DESC
                LIMIT 10
            """)
            database_sizes = []
            for row in cursor.fetchall():
                database_sizes.append({
                    "name": row['db_name'],
                    "size_mb": float(row['size_mb']),
                    "table_count": int(row['table_count'])
                })

            # InnoDB 表空间
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_data_pages'")
            try:
                innodb_data_pages = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_data_pages = 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_data_reads'")
            try:
                innodb_data_reads = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_data_reads = 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_data_writes'")
            try:
                innodb_data_writes = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_data_writes = 0

            # =============================================
            # 4. 性能指标 (performance)
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Questions'")
            questions = int(cursor.fetchone()['Value'])
            qps = round(questions / uptime, 2) if uptime > 0 else 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Com_commit'")
            try:
                com_commit = int(cursor.fetchone()['Value'])
            except Exception:
                com_commit = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Com_rollback'")
            try:
                com_rollback = int(cursor.fetchone()['Value'])
            except Exception:
                com_rollback = 0
            tps = round((com_commit + com_rollback) / uptime, 2) if uptime > 0 else 0

            # 键缓存
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_read_requests'")
            try:
                key_read_requests = int(cursor.fetchone()['Value'])
            except Exception:
                key_read_requests = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_reads'")
            try:
                key_reads = int(cursor.fetchone()['Value'])
            except Exception:
                key_reads = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_write_requests'")
            try:
                key_write_requests = int(cursor.fetchone()['Value'])
            except Exception:
                key_write_requests = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_writes'")
            try:
                key_writes = int(cursor.fetchone()['Value'])
            except Exception:
                key_writes = 0

            # InnoDB 行列统计
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_read'")
            try:
                innodb_rows_read = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_rows_read = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_inserted'")
            try:
                innodb_rows_inserted = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_rows_inserted = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_updated'")
            try:
                innodb_rows_updated = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_rows_updated = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_deleted'")
            try:
                innodb_rows_deleted = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_rows_deleted = 0

            # 缓冲池
            cursor.execute("SHOW VARIABLES LIKE 'innodb_buffer_pool_size'")
            innodb_buffer_pool_size = int(cursor.fetchone()['Value'])
            innodb_buffer_pool_size_mb = round(innodb_buffer_pool_size / 1024 / 1024, 2)

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")
            try:
                innodb_buffer_pool_reads = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_buffer_pool_reads = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read_requests'")
            try:
                innodb_buffer_pool_read_requests = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_buffer_pool_read_requests = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_total'")
            try:
                innodb_buffer_pool_pages_total = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_buffer_pool_pages_total = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_free'")
            try:
                innodb_buffer_pool_pages_free = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_buffer_pool_pages_free = 0
            buffer_hit_ratio = round((1 - innodb_buffer_pool_reads / innodb_buffer_pool_read_requests) * 100, 2) if innodb_buffer_pool_read_requests > 0 else 0

            # =============================================
            # 5. 等待事件 (wait) - 增强
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_waits'")
            try:
                innodb_row_lock_waits = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_row_lock_waits = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_current_waits'")
            try:
                innodb_row_lock_current_waits = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_row_lock_current_waits = 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Table_locks_immediate'")
            try:
                table_locks_immediate = int(cursor.fetchone()['Value'])
            except Exception:
                table_locks_immediate = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Table_locks_waited'")
            try:
                table_locks_waited = int(cursor.fetchone()['Value'])
            except Exception:
                table_locks_waited = 0

            # 锁等待详情
            locks = []
            try:
                cursor.execute("""
                    SELECT 
                        b.trx_mysql_thread_id as blocker_thread,
                        r.trx_mysql_thread_id as blocked_thread,
                        TIMESTAMPDIFF(SECOND, r.trx_started, NOW()) as wait_sec,
                        b.trx_query as blocker_query,
                        r.trx_query as blocked_query
                    FROM performance_schema.data_lock_waits w
                    INNER JOIN information_schema.innodb_trx b ON w.BLOCKING_ENGINE_TRANSACTION_ID = b.trx_id
                    INNER JOIN information_schema.innodb_trx r ON w.REQUESTING_ENGINE_TRANSACTION_ID = r.trx_id
                    WHERE TIMESTAMPDIFF(SECOND, r.trx_started, NOW()) > %s
                """, (LOCK_TIME_THRESHOLD,))
                for row in cursor.fetchall():
                    locks.append({
                        "blocker_id": str(row['blocker_thread']),
                        "waiter_id": str(row['blocked_thread']),
                        "seconds": int(row['wait_sec']),
                        "blocker_query": str(row['blocker_query'] or 'N/A')[:100],
                        "blocked_query": str(row['blocked_query'] or 'N/A')[:100]
                    })
            except Exception:
                try:
                    cursor.execute("""
                        SELECT 
                            blocking_trx.trx_mysql_thread_id as blocker_thread,
                            blocked_trx.trx_mysql_thread_id as blocked_thread,
                            TIMESTAMPDIFF(SECOND, blocked_trx.trx_started, NOW()) as wait_sec
                        FROM information_schema.innodb_lock_waits w
                        INNER JOIN information_schema.innodb_trx blocking_trx ON w.blocking_trx_id = blocking_trx.trx_id
                        INNER JOIN information_schema.innodb_trx blocked_trx ON w.requesting_trx_id = blocked_trx.trx_id
                        WHERE TIMESTAMPDIFF(SECOND, blocked_trx.trx_started, NOW()) > %s
                    """, (LOCK_TIME_THRESHOLD,))
                    for row in cursor.fetchall():
                        locks.append({
                            "blocker_id": str(row['blocker_thread']),
                            "waiter_id": str(row['blocked_thread']),
                            "seconds": int(row['wait_sec'])
                        })
                except Exception:
                    pass

            # =============================================
            # 6. 会话详情 (session_detail) - P0
            # =============================================
            cursor.execute("""
                SELECT 
                    id, user, host, db, command, time, state, info
                FROM information_schema.processlist
                WHERE command != 'Daemon'
                ORDER BY time DESC
                LIMIT 100
            """)
            session_list = []
            for row in cursor.fetchall():
                session_list.append({
                    "id": str(row['id']),
                    "user": row['user'] or 'N/A',
                    "host": row['host'] or 'N/A',
                    "db": row['db'] or 'N/A',
                    "command": row['command'] or 'N/A',
                    "time": int(row['time']) if row['time'] else 0,
                    "state": row['state'] or 'N/A',
                    "info": (row['info'] or 'N/A')[:200]
                })

            # 按状态统计会话
            cursor.execute("""
                SELECT command, COUNT(*) as cnt
                FROM information_schema.processlist
                GROUP BY command
            """)
            session_by_state = []
            for row in cursor.fetchall():
                session_by_state.append({
                    "state": row['command'],
                    "count": int(row['cnt'])
                })

            # =============================================
            # 7. SQL统计 (sql) - P0
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Slow_queries'")
            slow_queries_total = int(cursor.fetchone()['Value'])

            cursor.execute("SHOW VARIABLES LIKE 'long_query_time'")
            long_query_time = float(cursor.fetchone()['Value'])

            # Top SQL (需要启用 performance_schema)
            top_sql_by_latency = []
            try:
                cursor.execute("""
                    SELECT 
                        DIGEST as digest,
                        DIGEST_TEXT as sql_text,
                        COUNT_STAR as exec_count,
                        SUM_TIMER_WAIT/1000000000000 as total_latency,
                        SUM_ROWS_EXAMINED as rows_examined,
                        SUM_ROWS_SENT as rows_sent
                    FROM performance_schema.events_statements_summary_by_digest
                    ORDER BY SUM_TIMER_WAIT DESC
                    LIMIT 10
                """)
                for row in cursor.fetchall():
                    top_sql_by_latency.append({
                        "digest": row['digest'] or 'N/A',
                        "sql_text": (row['sql_text'] or 'N/A')[:200],
                        "exec_count": int(row['exec_count']),
                        "total_latency_sec": round(float(row['total_latency']), 4),
                        "rows_examined": int(row['rows_examined']),
                        "rows_sent": int(row['rows_sent'])
                    })
            except Exception:
                pass

            # COM_* 统计
            com_stats = {}
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Com_%'")
            for row in cursor.fetchall():
                com_stats[row['Variable_name']] = row['Value']

            # =============================================
            # 8. 复制与集群 (replication) - P1 增强
            # =============================================
            cursor.execute("SHOW MASTER STATUS")
            try:
                master_status = cursor.fetchone()
                binlog_file = master_status['File'] if master_status else 'N/A'
                binlog_position = master_status['Position'] if master_status else 0
            except Exception:
                binlog_file = 'N/A'
                binlog_position = 0

            cursor.execute("SHOW VARIABLES LIKE 'binlog_format'")
            binlog_format = cursor.fetchone()['Value']

            # 主库基本信息
            cursor.execute("SHOW VARIABLES LIKE 'server_id'")
            try:
                server_id_var = int(cursor.fetchone()['Value'])
            except Exception:
                server_id_var = 0

            # GTID 模式
            cursor.execute("SHOW VARIABLES LIKE 'gtid_mode'")
            try:
                gtid_mode = cursor.fetchone()['Value']
            except Exception:
                gtid_mode = 'OFF'

            cursor.execute("SHOW VARIABLES LIKE 'gtid_purged'")
            try:
                gtid_purged = cursor.fetchone()['Value'] or 'N/A'
            except Exception:
                gtid_purged = 'N/A'

            cursor.execute("SHOW VARIABLES LIKE 'gtid_executed'")
            try:
                gtid_executed = cursor.fetchone()['Value'] or 'N/A'
            except Exception:
                gtid_executed = 'N/A'

            # 多线程复制配置
            cursor.execute("SHOW VARIABLES LIKE 'slave_parallel_workers'")
            try:
                slave_parallel_workers = int(cursor.fetchone()['Value'])
            except Exception:
                slave_parallel_workers = 0

            cursor.execute("SHOW VARIABLES LIKE 'slave_parallel_type'")
            try:
                slave_parallel_type = cursor.fetchone()['Value']
            except Exception:
                slave_parallel_type = 'N/A'

            cursor.execute("SHOW VARIABLES LIKE 'slave_preserve_commit_order'")
            try:
                slave_preserve_commit_order = cursor.fetchone()['Value']
            except Exception:
                slave_preserve_commit_order = 'N/A'

            # 复制延迟配置
            cursor.execute("SHOW VARIABLES LIKE 'slave_net_timeout'")
            try:
                slave_net_timeout = int(cursor.fetchone()['Value'])
            except Exception:
                slave_net_timeout = 0

            cursor.execute("SHOW VARIABLES LIKE 'slave_compressed_protocol'")
            try:
                slave_compressed_protocol = cursor.fetchone()['Value']
            except Exception:
                slave_compressed_protocol = 'N/A'

            # 主从复制状态详情
            cursor.execute("SHOW SLAVE STATUS")
            try:
                slave_status = cursor.fetchone()
                slave_io_running = slave_status['Slave_IO_Running'] if slave_status else 'NO'
                slave_sql_running = slave_status['Slave_SQL_Running'] if slave_status else 'NO'
                seconds_behind_master = int(slave_status['Seconds_Behind_Master']) if slave_status and slave_status['Seconds_Behind_Master'] is not None else -1
                relay_log_space = int(slave_status['Relay_Log_Space']) if slave_status else 0
                slave_last_error = slave_status['Last_Error'] if slave_status else 'N/A'
                # 主从复制位置信息
                master_log_file = slave_status['Master_Log_File'] if slave_status else 'N/A'
                read_master_log_pos = slave_status['Read_Master_Log_Pos'] if slave_status else 0
                # 中继日志信息
                relay_log_name = slave_status['Relay_Log_File'] if slave_status else 'N/A'
                relay_log_pos = slave_status['Relay_Log_Pos'] if slave_status else 0
                # 执行位置
                exec_master_log_pos = slave_status['Exec_Master_Log_Pos'] if slave_status else 0
                # SQL 线程最后错误
                last_sql_errno = slave_status['Last_SQL_Errno'] if slave_status else 0
                last_sql_error = slave_status['Last_SQL_Error'] if slave_status else 'N/A'
                # IO 线程最后错误
                last_io_errno = slave_status['Last_IO_Errno'] if slave_status else 0
                last_io_error = slave_status['Last_IO_Error'] if slave_status else 'N/A'
                # Master 信息
                master_host = slave_status['Master_Host'] if slave_status else 'N/A'
                master_port = slave_status['Master_Port'] if slave_status else 0
                master_user = slave_status['Master_User'] if slave_status else 'N/A'
                master_connect_retry = slave_status['Master_Connect_Retry'] if slave_status else 0
                # GTID 相关
                auto_position = slave_status['Auto_Position'] if slave_status else 0
                master_uuid = slave_status['Master_UUID'] if slave_status else 'N/A'
                master_server_id = slave_status['Master_Server_Id'] if slave_status else 0
                # 心跳信息
                heartbeat_period = slave_status['Heartbeat_Period'] if slave_status else 0
                last_heartbeat = slave_status['Last_HeartbeatTimestamp'] if slave_status else 'N/A'
                # 复制通道
                channel_name = slave_status['Channel_Name'] if slave_status else 'N/A'
                # 并行复制
                slave_parallel_workers_active = slave_status['Slave_Parallel_Workers'] if slave_status else 0
                slave_last_batch_timestamp = slave_status['Slave_Last_Batch_Timestamp'] if slave_status else 'N/A'
            except Exception:
                slave_io_running = 'NO'
                slave_sql_running = 'NO'
                seconds_behind_master = -1
                relay_log_space = 0
                slave_last_error = 'N/A'
                master_log_file = 'N/A'
                read_master_log_pos = 0
                relay_log_name = 'N/A'
                relay_log_pos = 0
                exec_master_log_pos = 0
                last_sql_errno = 0
                last_sql_error = 'N/A'
                last_io_errno = 0
                last_io_error = 'N/A'
                master_host = 'N/A'
                master_port = 0
                master_user = 'N/A'
                master_connect_retry = 0
                auto_position = 0
                master_uuid = 'N/A'
                master_server_id = 0
                heartbeat_period = 0
                last_heartbeat = 'N/A'
                channel_name = 'N/A'
                slave_parallel_workers_active = 0
                slave_last_batch_timestamp = 'N/A'

            # 检查复制通道 (多源复制支持)
            replication_channels = []
            try:
                cursor.execute("SHOW REPLICAS")
                for row in cursor.fetchall():
                    replication_channels.append({
                        "channel_name": row.get('Channel_Name', 'N/A'),
                        "host": row.get('Host', 'N/A'),
                        "port": row.get('Port', 0),
                        "user": row.get('User', 'N/A'),
                        "io_state": row.get('IO_State', 'N/A'),
                        "sql_state": row.get('SQL_State', 'N/A'),
                        "seconds_behind_source": row.get('Seconds_Behind_Source', -1)
                    })
            except Exception:
                # SHOW REPLICAS 在某些版本不支持，尝试旧语法
                try:
                    cursor.execute("SHOW SLAVE HOSTS")
                    for row in cursor.fetchall():
                        replication_channels.append({
                            "server_id": row.get('Server_id', 0),
                            "host": row.get('Host', 'N/A'),
                            "port": row.get('Port', 0),
                            "master_id": row.get('Master_id', 0)
                        })
                except Exception:
                    pass

            # 复制过滤规则
            cursor.execute("SHOW VARIABLES LIKE 'replicate_do_db'")
            try:
                replicate_do_db = cursor.fetchone()['Value'] or 'N/A'
            except Exception:
                replicate_do_db = 'N/A'

            cursor.execute("SHOW VARIABLES LIKE 'replicate_ignore_db'")
            try:
                replicate_ignore_db = cursor.fetchone()['Value'] or 'N/A'
            except Exception:
                replicate_ignore_db = 'N/A'

            cursor.execute("SHOW VARIABLES LIKE 'replicate_do_table'")
            try:
                replicate_do_table = cursor.fetchone()['Value'] or 'N/A'
            except Exception:
                replicate_do_table = 'N/A'

            cursor.execute("SHOW VARIABLES LIKE 'replicate_ignore_table'")
            try:
                replicate_ignore_table = cursor.fetchone()['Value'] or 'N/A'
            except Exception:
                replicate_ignore_table = 'N/A'

            # 复制健康状态判断
            replication_health = 'HEALTHY'
            replication_issues = []
            if slave_io_running != 'Yes':
                replication_health = 'UNHEALTHY'
                replication_issues.append(f"IO线程未运行: {slave_io_running}")
            if slave_sql_running != 'Yes':
                replication_health = 'UNHEALTHY'
                replication_issues.append(f"SQL线程未运行: {slave_sql_running}")
            if seconds_behind_master > 300:
                replication_health = 'DEGRADED'
                replication_issues.append(f"复制延迟过高: {seconds_behind_master}s")
            if slave_last_error and slave_last_error != 'N/A':
                replication_health = 'ERROR'
                replication_issues.append(f"复制错误: {slave_last_error}")

            # =============================================
            # 9. 配置参数 (config) - P1
            # =============================================
            config_params = {}
            config_keys = [
                'innodb_buffer_pool_size', 'innodb_log_file_size', 'innodb_flush_log_at_trx_commit',
                'sync_binlog', 'max_connections', 'table_open_cache', 'thread_cache_size',
                'key_buffer_size', 'query_cache_type', 'innodb_file_per_table'
            ]
            for key in config_keys:
                cursor.execute(f"SHOW VARIABLES LIKE '{key}'")
                try:
                    row = cursor.fetchone()
                    if row:
                        config_params[key] = row['Value']
                except Exception:
                    pass

            # =============================================
            # 10. 缓冲池 (buffer) - P0
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_dirty'")
            try:
                innodb_buffer_pool_pages_dirty = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_buffer_pool_pages_dirty = 0
            buffer_dirty_ratio = round(innodb_buffer_pool_pages_dirty / innodb_buffer_pool_pages_total * 100, 2) if innodb_buffer_pool_pages_total > 0 else 0

            # =============================================
            # 11. 事务统计 (transaction) - P1
            # =============================================
            cursor.execute("SELECT COUNT(*) FROM information_schema.innodb_trx")
            innodb_trx_count = int(cursor.fetchone()['COUNT(*)'])

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_trx_committed'")
            try:
                innodb_trx_committed = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_trx_committed = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_trx_rolled_back'")
            try:
                innodb_trx_rolled_back = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_trx_rolled_back = 0

            # =============================================
            # 12. 日志统计 (log) - P1
            # =============================================
            cursor.execute("SHOW MASTER LOGS")
            try:
                cursor.fetchall()
                binlog_count = cursor.rowcount
            except Exception:
                binlog_count = 0

            cursor.execute("SHOW VARIABLES LIKE 'slow_query_log_file'")
            slow_query_log = cursor.fetchone()['Value']

            cursor.execute("SHOW VARIABLES LIKE 'log_error'")
            log_error = cursor.fetchone()['Value']

            # =============================================
            # 13. 安全审计 (security) - P2
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Max_used_connections'")
            try:
                max_used_connections = int(cursor.fetchone()['Value'])
            except Exception:
                max_used_connections = 0

            cursor.execute("SHOW VARIABLES LIKE 'have_ssl'")
            try:
                have_ssl = cursor.fetchone()['Value']
            except Exception:
                have_ssl = 'DISABLED'

            # =============================================
            # 14. 对象统计 (object) - P2补全
            # =============================================
            # Top 20 表大小
            cursor.execute("""
                SELECT 
                    table_schema, table_name,
                    ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) as size_mb,
                    table_rows
                FROM information_schema.tables
                WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
                GROUP BY table_schema, table_name, table_rows
                ORDER BY size_mb DESC
                LIMIT 20
            """)
            table_size_top20 = []
            for row in cursor.fetchall():
                table_size_top20.append({
                    "schema": row[0],
                    "table_name": row[1],
                    "size_mb": float(row[2]),
                    "rows": int(row[3]) if row[3] else 0
                })

            # 未使用索引
            unused_indexes = []
            try:
                cursor.execute("""
                    SELECT object_schema, object_name, index_name
                    FROM performance_schema.table_io_waits_summary_by_index_usage
                    WHERE index_name IS NOT NULL
                    GROUP BY object_schema, object_name, index_name
                    HAVING SUM(count_star) = 0
                    LIMIT 20
                """)
                for row in cursor.fetchall():
                    unused_indexes.append({
                        "schema": row[0],
                        "table": row[1],
                        "index": row[2]
                    })
            except Exception:
                pass

            # 冗余索引
            redundant_indexes = []
            try:
                cursor.execute("""
                    SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
                    FROM information_schema.STATISTICS
                    WHERE SEQ_IN_INDEX = 1
                    GROUP BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
                    HAVING COUNT(*) > 1
                    LIMIT 20
                """)
                for row in cursor.fetchall():
                    redundant_indexes.append({
                        "schema": row[0],
                        "table": row[1],
                        "index": row[2]
                    })
            except Exception:
                pass

            # 表数量统计
            cursor.execute("""
                SELECT table_schema, COUNT(*) as table_count
                FROM information_schema.tables
                WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
                GROUP BY table_schema
            """)
            table_count_by_schema = []
            for row in cursor.fetchall():
                table_count_by_schema.append({
                    "schema": row[0],
                    "count": int(row[1] or 0)
                })

            # =============================================
            # 15. InnoDB Redo 日志 (redo_log) - P0
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_log_waits'")
            try:
                innodb_log_waits = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_log_waits = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_log_writes'")
            try:
                innodb_log_writes = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_log_writes = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_os_log_written'")
            try:
                innodb_os_log_written = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_os_log_written = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_os_log_fsyncs'")
            try:
                innodb_os_log_fsyncs = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_os_log_fsyncs = 0
            cursor.execute("SHOW VARIABLES LIKE 'innodb_log_file_size'")
            try:
                innodb_log_file_size = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_log_file_size = 0
            cursor.execute("SHOW VARIABLES LIKE 'innodb_log_files_in_group'")
            try:
                innodb_log_files_in_group = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_log_files_in_group = 0

            # =============================================
            # 16. InnoDB 行锁时间 (row_lock_time) - P0
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_time'")
            try:
                innodb_row_lock_time = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_row_lock_time = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_time_avg'")
            try:
                innodb_row_lock_time_avg = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_row_lock_time_avg = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_time_max'")
            try:
                innodb_row_lock_time_max = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_row_lock_time_max = 0

            # =============================================
            # 17. 临时表统计 (temp_table) - P0
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Created_tmp_tables'")
            try:
                created_tmp_tables = int(cursor.fetchone()['Value'])
            except Exception:
                created_tmp_tables = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Created_tmp_disk_tables'")
            try:
                created_tmp_disk_tables = int(cursor.fetchone()['Value'])
            except Exception:
                created_tmp_disk_tables = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Created_tmp_files'")
            try:
                created_tmp_files = int(cursor.fetchone()['Value'])
            except Exception:
                created_tmp_files = 0
            tmp_disk_ratio = round(created_tmp_disk_tables / created_tmp_tables * 100, 2) if created_tmp_tables > 0 else 0

            # =============================================
            # 18. 排序统计 (sort) - P0
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Sort_merge_passes'")
            try:
                sort_merge_passes = int(cursor.fetchone()['Value'])
            except Exception:
                sort_merge_passes = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Sort_range'")
            try:
                sort_range = int(cursor.fetchone()['Value'])
            except Exception:
                sort_range = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Sort_rows'")
            try:
                sort_rows = int(cursor.fetchone()['Value'])
            except Exception:
                sort_rows = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Sort_scan'")
            try:
                sort_scan = int(cursor.fetchone()['Value'])
            except Exception:
                sort_scan = 0

            # =============================================
            # 19. 长时间事务 (long_trx) - P0
            # =============================================
            long_transactions = []
            try:
                cursor.execute("""
                    SELECT
                        trx_id, trx_state, trx_mysql_thread_id,
                        trx_started, TIMESTAMPDIFF(SECOND, trx_started, NOW()) as duration_sec,
                        trx_query, trx_tables_locked, trx_rows_locked,
                        trx_lock_structs
                    FROM information_schema.innodb_trx
                    WHERE TIMESTAMPDIFF(SECOND, trx_started, NOW()) > 60
                    ORDER BY duration_sec DESC
                    LIMIT 20
                """)
                for row in cursor.fetchall():
                    long_transactions.append({
                        "trx_id": str(row['trx_id'] or ''),
                        "state": row['trx_state'] or 'N/A',
                        "thread_id": str(row['trx_mysql_thread_id'] or ''),
                        "started": str(row['trx_started'] or ''),
                        "duration_sec": int(row['duration_sec'] or 0),
                        "query": str(row['trx_query'] or 'N/A')[:200],
                        "tables_locked": int(row['trx_tables_locked'] or 0),
                        "rows_locked": int(row['trx_rows_locked'] or 0),
                        "lock_structs": int(row['trx_lock_structs'] or 0)
                    })
            except Exception:
                pass

            # =============================================
            # 20. 死锁记录 (deadlock) - P0
            # =============================================
            last_deadlock = {}
            try:
                cursor.execute("SHOW ENGINE INNODB STATUS")
                innodb_status_row = cursor.fetchone()
                if innodb_status_row and innodb_status_row.get('Status'):
                    status_text = innodb_status_row['Status']
                    deadlock_start = status_text.find('LATEST DETECTED DEADLOCK')
                    if deadlock_start >= 0:
                        deadlock_end = status_text.find('\n--------', deadlock_start + 30)
                        if deadlock_end < 0:
                            deadlock_end = min(deadlock_start + 2000, len(status_text))
                        deadlock_text = status_text[deadlock_start:deadlock_end]
                        last_deadlock = {"found": True, "detail": deadlock_text[:1500]}
                    else:
                        last_deadlock = {"found": False, "detail": "No deadlock detected"}
                else:
                    last_deadlock = {"found": False, "detail": "N/A"}
            except Exception:
                last_deadlock = {"found": False, "detail": "N/A"}

            # =============================================
            # 21. Top 等待事件 (top_waits) - P0
            # =============================================
            top_wait_events = []
            try:
                cursor.execute("""
                    SELECT
                        EVENT_NAME as event_name,
                        COUNT_STAR as total_waits,
                        SUM_TIMER_WAIT / 1000000000000 as total_latency_sec,
                        AVG_TIMER_WAIT / 1000000000 as avg_latency_us
                    FROM performance_schema.events_waits_summary_global_by_event_name
                    WHERE COUNT_STAR > 0
                      AND EVENT_NAME NOT LIKE 'wait/synch/mutex/%%'
                      AND EVENT_NAME NOT LIKE 'wait/synch/rwlock/%%'
                    ORDER BY SUM_TIMER_WAIT DESC
                    LIMIT 10
                """)
                for row in cursor.fetchall():
                    top_wait_events.append({
                        "event": row['event_name'] or 'N/A',
                        "total_waits": int(row['total_waits'] or 0),
                        "total_latency_sec": round(float(row['total_latency_sec'] or 0), 4),
                        "avg_latency_us": round(float(row['avg_latency_us'] or 0), 2)
                    })
            except Exception:
                pass

            # =============================================
            # 22. 网络吞吐 (network) - P1
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Bytes_received'")
            try:
                bytes_received = int(cursor.fetchone()['Value'])
            except Exception:
                bytes_received = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Bytes_sent'")
            try:
                bytes_sent = int(cursor.fetchone()['Value'])
            except Exception:
                bytes_sent = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Connections'")
            try:
                connections_total = int(cursor.fetchone()['Value'])
            except Exception:
                connections_total = 0

            # =============================================
            # 23. Handler 统计 (handler) - P1
            # =============================================
            handler_stats = {}
            handler_keys = [
                'Handler_read_first', 'Handler_read_key', 'Handler_read_last',
                'Handler_read_next', 'Handler_read_prev', 'Handler_read_rnd',
                'Handler_read_rnd_next', 'Handler_write', 'Handler_update',
                'Handler_delete', 'Handler_commit', 'Handler_rollback',
                'Handler_savepoint', 'Handler_savepoint_rollback'
            ]
            for key in handler_keys:
                try:
                    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        handler_stats[key] = int(row['Value'])
                except Exception:
                    pass

            # =============================================
            # 24. 表/文件打开统计 (open_tables) - P1
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Open_tables'")
            try:
                open_tables = int(cursor.fetchone()['Value'])
            except Exception:
                open_tables = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Opened_tables'")
            try:
                opened_tables = int(cursor.fetchone()['Value'])
            except Exception:
                opened_tables = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Open_files'")
            try:
                open_files = int(cursor.fetchone()['Value'])
            except Exception:
                open_files = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Opened_files'")
            try:
                opened_files = int(cursor.fetchone()['Value'])
            except Exception:
                opened_files = 0
            cursor.execute("SHOW VARIABLES LIKE 'table_open_cache'")
            try:
                table_open_cache = int(cursor.fetchone()['Value'])
            except Exception:
                table_open_cache = 0
            table_cache_hit_ratio = round(open_tables / (open_tables + opened_tables) * 100, 2) if (open_tables + opened_tables) > 0 else 0

            # =============================================
            # 25. Binlog 缓存 (binlog_cache) - P1
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Binlog_cache_use'")
            try:
                binlog_cache_use = int(cursor.fetchone()['Value'])
            except Exception:
                binlog_cache_use = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Binlog_cache_disk_use'")
            try:
                binlog_cache_disk_use = int(cursor.fetchone()['Value'])
            except Exception:
                binlog_cache_disk_use = 0
            binlog_cache_disk_ratio = round(binlog_cache_disk_use / binlog_cache_use * 100, 2) if binlog_cache_use > 0 else 0

            # =============================================
            # 26. Select 类型统计 (select_type) - P1
            # =============================================
            select_stats = {}
            select_keys = [
                'Select_full_join', 'Select_full_range_join', 'Select_range',
                'Select_range_check', 'Select_scan'
            ]
            for key in select_keys:
                try:
                    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        select_stats[key] = int(row['Value'])
                except Exception:
                    pass

            # =============================================
            # 27. InnoDB 页操作 (innodb_page_ops) - P1
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_pages_read'")
            try:
                innodb_pages_read = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_pages_read = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_pages_written'")
            try:
                innodb_pages_written = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_pages_written = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_pages_created'")
            try:
                innodb_pages_created = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_pages_created = 0

            # =============================================
            # 28. 连接错误细分 (conn_errors) - P2
            # =============================================
            connection_errors = {}
            conn_error_keys = [
                'Connection_errors_accept', 'Connection_errors_internal',
                'Connection_errors_max_connections', 'Connection_errors_peer_address',
                'Connection_errors_select', 'Connection_errors_tcpwrap'
            ]
            for key in conn_error_keys:
                try:
                    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        connection_errors[key] = int(row['Value'])
                except Exception:
                    pass

            # =============================================
            # 29. InnoDB Change Buffer (change_buffer) - P2
            # =============================================
            change_buffer_stats = {}
            cb_keys = [
                'Innodb_ibuf_size', 'Innodb_ibuf_merges',
                'Innodb_ibuf_merges_insert', 'Innodb_ibuf_merges_delete',
                'Innodb_ibuf_merges_delete_mark'
            ]
            for key in cb_keys:
                try:
                    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        change_buffer_stats[key] = int(row['Value'])
                except Exception:
                    pass

            # =============================================
            # 30. InnoDB Adaptive Hash Index (adaptive_hash) - P2
            # =============================================
            adaptive_hash_stats = {}
            ah_keys = [
                'Innodb_adaptive_hash_hash_searches',
                'Innodb_adaptive_hash_non_hash_searches'
            ]
            for key in ah_keys:
                try:
                    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        adaptive_hash_stats[key] = int(row['Value'])
                except Exception:
                    pass

            # =============================================
            # 31. InnoDB Doublewrite (doublewrite) - P2
            # =============================================
            doublewrite_stats = {}
            dw_keys = [
                'Innodb_dblwr_writes', 'Innodb_dblwr_pages_written'
            ]
            for key in dw_keys:
                try:
                    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        doublewrite_stats[key] = int(row['Value'])
                except Exception:
                    pass

            # =============================================
            # 32. Top SQL 扩展 (sql_extended) - P1
            # =============================================
            top_sql_by_rows_examined = []
            try:
                cursor.execute("""
                    SELECT
                        DIGEST as digest,
                        DIGEST_TEXT as sql_text,
                        COUNT_STAR as exec_count,
                        SUM_ROWS_EXAMINED as rows_examined,
                        SUM_ROWS_SENT as rows_sent
                    FROM performance_schema.events_statements_summary_by_digest
                    WHERE SUM_ROWS_EXAMINED > 0
                    ORDER BY SUM_ROWS_EXAMINED DESC
                    LIMIT 10
                """)
                for row in cursor.fetchall():
                    top_sql_by_rows_examined.append({
                        "digest": row['digest'] or 'N/A',
                        "sql_text": (row['sql_text'] or 'N/A')[:200],
                        "exec_count": int(row['exec_count'] or 0),
                        "rows_examined": int(row['rows_examined'] or 0),
                        "rows_sent": int(row['rows_sent'] or 0)
                    })
            except Exception:
                pass

            top_sql_by_exec_count = []
            try:
                cursor.execute("""
                    SELECT
                        DIGEST as digest,
                        DIGEST_TEXT as sql_text,
                        COUNT_STAR as exec_count,
                        SUM_TIMER_WAIT/1000000000000 as total_latency_sec,
                        SUM_ROWS_EXAMINED as rows_examined
                    FROM performance_schema.events_statements_summary_by_digest
                    WHERE COUNT_STAR > 0
                    ORDER BY COUNT_STAR DESC
                    LIMIT 10
                """)
                for row in cursor.fetchall():
                    top_sql_by_exec_count.append({
                        "digest": row['digest'] or 'N/A',
                        "sql_text": (row['sql_text'] or 'N/A')[:200],
                        "exec_count": int(row['exec_count'] or 0),
                        "total_latency_sec": round(float(row['total_latency_sec'] or 0), 4),
                        "rows_examined": int(row['rows_examined'] or 0)
                    })
            except Exception:
                pass

            # =============================================
            # 33. 临时表空间 (temp_tablespace) - P1
            # =============================================
            temp_tablespace = {}
            try:
                cursor.execute("SHOW VARIABLES LIKE 'innodb_temp_data_file_path'")
                row = cursor.fetchone()
                temp_tablespace['data_file_path'] = row['Value'] if row else 'N/A'
            except Exception:
                temp_tablespace['data_file_path'] = 'N/A'
            try:
                cursor.execute("SELECT COUNT(*) as cnt FROM information_schema.innodb_temp_table_info")
                row = cursor.fetchone()
                temp_tablespace['active_temp_tables'] = int(row['cnt'] or 0) if row else 0
            except Exception:
                temp_tablespace['active_temp_tables'] = 0

            # =============================================
            # 34. 对象汇总统计 (object_summary) - P1
            # =============================================
            object_summary = {}
            try:
                cursor.execute("""
                    SELECT
                        COUNT(*) as table_count,
                        SUM(CASE WHEN TABLE_TYPE = 'VIEW' THEN 1 ELSE 0 END) as view_count
                    FROM information_schema.tables
                    WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                """)
                row = cursor.fetchone()
                object_summary['table_count'] = int(row['table_count'] or 0) if row else 0
                object_summary['view_count'] = int(row['view_count'] or 0) if row else 0
            except Exception:
                object_summary['table_count'] = 0
                object_summary['view_count'] = 0
            try:
                cursor.execute("""
                    SELECT COUNT(DISTINCT CONCAT(table_schema,'.',table_name,index_name)) as cnt
                    FROM information_schema.statistics
                    WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                """)
                row = cursor.fetchone()
                object_summary['index_count'] = int(row['cnt'] or 0) if row else 0
            except Exception:
                object_summary['index_count'] = 0
            try:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM information_schema.triggers
                    WHERE trigger_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                """)
                row = cursor.fetchone()
                object_summary['trigger_count'] = int(row['cnt'] or 0) if row else 0
            except Exception:
                object_summary['trigger_count'] = 0
            try:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM information_schema.routines
                    WHERE routine_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                """)
                row = cursor.fetchone()
                object_summary['routine_count'] = int(row['cnt'] or 0) if row else 0
            except Exception:
                object_summary['routine_count'] = 0

            # =============================================
            # 35. 高可用状态汇总 (ha_status) - P2
            # =============================================
            ha_status = {
                "role": "STANDALONE",
                "replication_health": replication_health,
                "replication_issues": replication_issues,
                "slave_io_running": slave_io_running,
                "slave_sql_running": slave_sql_running,
                "seconds_behind_master": seconds_behind_master,
                "have_ssl": have_ssl,
            }
            if slave_io_running != 'NO' or slave_sql_running != 'NO':
                ha_status["role"] = "SLAVE"
                ha_status["master_host"] = master_host
            elif replication_channels:
                ha_status["role"] = "MASTER"

            # =============================================
            # 36. Buffer Pool 详情 (buffer_pool_detail) - P2
            # =============================================
            buffer_pool_stats = []
            try:
                cursor.execute("""
                    SELECT
                        POOL_ID, POOL_SIZE, FREE_BUFFERS, DATABASE_PAGES,
                        OLD_DATABASE_PAGES, MODIFIED_DATABASE_PAGES,
                        PAGES_READ, PAGES_CREATED, PAGES_WRITTEN
                    FROM information_schema.INNODB_BUFFER_POOL_STATS
                """)
                for row in cursor.fetchall():
                    buffer_pool_stats.append({
                        "pool_id": int(row['POOL_ID'] or 0),
                        "pool_size": int(row['POOL_SIZE'] or 0),
                        "free_buffers": int(row['FREE_BUFFERS'] or 0),
                        "database_pages": int(row['DATABASE_PAGES'] or 0),
                        "old_pages": int(row['OLD_DATABASE_PAGES'] or 0),
                        "modified_pages": int(row['MODIFIED_DATABASE_PAGES'] or 0),
                        "pages_read": int(row['PAGES_READ'] or 0),
                        "pages_created": int(row['PAGES_CREATED'] or 0),
                        "pages_written": int(row['PAGES_WRITTEN'] or 0),
                    })
            except Exception:
                pass

            # =============================================
            # 37. 资源限制 (resource_limits) - P1
            # =============================================
            resource_limits = [
                {
                    "resource_name": "max_connections",
                    "current_utilization": threads_connected,
                    "limit_value": max_connections,
                    "usage_pct": conn_usage_pct
                },
                {
                    "resource_name": "table_open_cache",
                    "current_utilization": open_tables,
                    "limit_value": table_open_cache,
                    "usage_pct": round(open_tables / table_open_cache * 100, 2) if table_open_cache > 0 else 0
                }
            ]
            if innodb_buffer_pool_pages_total > 0:
                buffer_pool_used = innodb_buffer_pool_pages_total - innodb_buffer_pool_pages_free
                resource_limits.append({
                    "resource_name": "innodb_buffer_pool_pages",
                    "current_utilization": buffer_pool_used,
                    "limit_value": innodb_buffer_pool_pages_total,
                    "usage_pct": round(buffer_pool_used / innodb_buffer_pool_pages_total * 100, 2)
                })

        return {
            # 基础信息
            "version": version[:50] + "...",
            "server_id": server_id,
            "datadir": datadir,
            "port": port,
            "current_database": current_db,
            "uptime_seconds": uptime,

            # 连接会话
            "threads_connected": threads_connected,
            "threads_running": threads_running,
            "threads_cached": threads_cached,
            "max_connections": max_connections,
            "conn_usage_pct": conn_usage_pct,
            "aborted_connects": aborted_connects,
            "aborted_clients": aborted_clients,
            # 兼容字段名
            "active_connections": threads_running,
            "total_connections": threads_connected,

            # 空间
            "database_sizes": database_sizes,
            "innodb_data_pages": innodb_data_pages,
            "innodb_data_reads": innodb_data_reads,
            "innodb_data_writes": innodb_data_writes,

            # 性能
            "qps": qps,
            "tps": tps,
            "key_read_requests": key_read_requests,
            "key_reads": key_reads,
            "key_write_requests": key_write_requests,
            "key_writes": key_writes,
            "innodb_rows_read": innodb_rows_read,
            "innodb_rows_inserted": innodb_rows_inserted,
            "innodb_rows_updated": innodb_rows_updated,
            "innodb_rows_deleted": innodb_rows_deleted,
            "innodb_buffer_pool_size_mb": innodb_buffer_pool_size_mb,
            "innodb_buffer_pool_pages_total": innodb_buffer_pool_pages_total,
            "innodb_buffer_pool_pages_free": innodb_buffer_pool_pages_free,
            "buffer_hit_ratio": buffer_hit_ratio,

            # 等待事件
            "innodb_row_lock_waits": innodb_row_lock_waits,
            "innodb_row_lock_current_waits": innodb_row_lock_current_waits,
            "table_locks_immediate": table_locks_immediate,
            "table_locks_waited": table_locks_waited,
            "locks": locks,
            "lock_wait_count": innodb_row_lock_waits,

            # 会话详情
            "session_list": session_list,
            "session_by_state": session_by_state,

            # SQL统计
            "slow_queries_total": slow_queries_total,
            "long_query_time_sec": long_query_time,
            "top_sql_by_latency": top_sql_by_latency,
            "com_stats": com_stats,

            # 复制集群
            "binlog_file": binlog_file,
            "binlog_position": binlog_position,
            "binlog_format": binlog_format,
            "slave_io_running": slave_io_running,
            "slave_sql_running": slave_sql_running,
            "seconds_behind_master": seconds_behind_master,
            "relay_log_space": relay_log_space,
            "slave_last_error": slave_last_error,
            "gtid_mode": gtid_mode,
            "master_log_file": master_log_file,
            "read_master_log_pos": read_master_log_pos,

            # 增强复制指标 (新增)
            "server_id_var": server_id_var,
            "gtid_purged": gtid_purged,
            "gtid_executed": gtid_executed,
            "slave_parallel_workers": slave_parallel_workers,
            "slave_parallel_type": slave_parallel_type,
            "slave_preserve_commit_order": slave_preserve_commit_order,
            "slave_net_timeout": slave_net_timeout,
            "slave_compressed_protocol": slave_compressed_protocol,
            "relay_log_name": relay_log_name,
            "relay_log_pos": relay_log_pos,
            "exec_master_log_pos": exec_master_log_pos,
            "last_sql_errno": last_sql_errno,
            "last_sql_error": last_sql_error,
            "last_io_errno": last_io_errno,
            "last_io_error": last_io_error,
            "master_host": master_host,
            "master_port": master_port,
            "master_user": master_user,
            "master_connect_retry": master_connect_retry,
            "auto_position": auto_position,
            "master_uuid": master_uuid,
            "master_server_id": master_server_id,
            "heartbeat_period": heartbeat_period,
            "last_heartbeat": str(last_heartbeat) if last_heartbeat else 'N/A',
            "channel_name": channel_name,
            "slave_parallel_workers_active": slave_parallel_workers_active,
            "slave_last_batch_timestamp": str(slave_last_batch_timestamp) if slave_last_batch_timestamp else 'N/A',
            "replication_channels": replication_channels,
            "replicate_do_db": replicate_do_db,
            "replicate_ignore_db": replicate_ignore_db,
            "replicate_do_table": replicate_do_table,
            "replicate_ignore_table": replicate_ignore_table,
            "replication_health": replication_health,
            "replication_issues": replication_issues,

            # 配置参数
            "config_params": config_params,

            # 缓冲池
            "innodb_buffer_pool_pages_dirty": innodb_buffer_pool_pages_dirty,
            "buffer_dirty_ratio": buffer_dirty_ratio,

            # 事务统计
            "innodb_trx_count": innodb_trx_count,
            "innodb_trx_committed": innodb_trx_committed,
            "innodb_trx_rolled_back": innodb_trx_rolled_back,
            "active_transactions": innodb_trx_count,

            # 日志
            "binlog_count": binlog_count,
            "slow_query_log": slow_query_log,
            "log_error": log_error,

            # 安全审计
            "max_used_connections": max_used_connections,
            "have_ssl": have_ssl,

            # 对象统计
            "table_size_top20": table_size_top20,
            "unused_indexes": unused_indexes,
            "redundant_indexes": redundant_indexes,
            "table_count_by_schema": table_count_by_schema,

            # InnoDB Redo 日志
            "innodb_log_waits": innodb_log_waits,
            "innodb_log_writes": innodb_log_writes,
            "innodb_os_log_written": innodb_os_log_written,
            "innodb_os_log_fsyncs": innodb_os_log_fsyncs,
            "innodb_log_file_size": innodb_log_file_size,
            "innodb_log_files_in_group": innodb_log_files_in_group,

            # InnoDB 行锁时间
            "innodb_row_lock_time_ms": round(innodb_row_lock_time / 1000, 2),
            "innodb_row_lock_time_avg_ms": round(innodb_row_lock_time_avg / 1000, 2),
            "innodb_row_lock_time_max_ms": round(innodb_row_lock_time_max / 1000, 2),

            # 临时表统计
            "created_tmp_tables": created_tmp_tables,
            "created_tmp_disk_tables": created_tmp_disk_tables,
            "created_tmp_files": created_tmp_files,
            "tmp_disk_ratio": tmp_disk_ratio,

            # 排序统计
            "sort_merge_passes": sort_merge_passes,
            "sort_range": sort_range,
            "sort_rows": sort_rows,
            "sort_scan": sort_scan,

            # 长时间事务
            "long_transactions": long_transactions,

            # 死锁记录
            "last_deadlock": last_deadlock,

            # Top 等待事件
            "top_wait_events": top_wait_events,

            # 网络吞吐
            "bytes_received": bytes_received,
            "bytes_sent": bytes_sent,
            "bytes_received_mb": round(bytes_received / 1024 / 1024, 2),
            "bytes_sent_mb": round(bytes_sent / 1024 / 1024, 2),
            "connections_total": connections_total,

            # Handler 统计
            "handler_stats": handler_stats,

            # 表打开统计
            "open_tables": open_tables,
            "opened_tables": opened_tables,
            "open_files": open_files,
            "opened_files": opened_files,
            "table_open_cache": table_open_cache,
            "table_cache_hit_ratio": table_cache_hit_ratio,

            # Binlog 缓存
            "binlog_cache_use": binlog_cache_use,
            "binlog_cache_disk_use": binlog_cache_disk_use,
            "binlog_cache_disk_ratio": binlog_cache_disk_ratio,

            # Select 类型统计
            "select_stats": select_stats,

            # InnoDB 页操作
            "innodb_pages_read": innodb_pages_read,
            "innodb_pages_written": innodb_pages_written,
            "innodb_pages_created": innodb_pages_created,

            # 连接错误细分
            "connection_errors": connection_errors,

            # Change Buffer
            "change_buffer_stats": change_buffer_stats,

            # Adaptive Hash
            "adaptive_hash_stats": adaptive_hash_stats,

            # Doublewrite
            "doublewrite_stats": doublewrite_stats,

            # Top SQL 扩展
            "top_sql_by_rows_examined": top_sql_by_rows_examined,
            "top_sql_by_exec_count": top_sql_by_exec_count,

            # 临时表空间
            "temp_tablespace": temp_tablespace,

            # 对象汇总统计
            "object_summary": object_summary,

            # 高可用状态
            "ha_status": ha_status,

            # Buffer Pool 详情
            "buffer_pool_stats": buffer_pool_stats,

            # 资源限制
            "resource_limits": resource_limits,
        }
