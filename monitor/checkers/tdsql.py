# -*- coding: utf-8 -*-
"""
TDSQL 分布式数据库检查器
基于 MySQL 协议，增加腾讯 TDSQL 分布式特性
支持双活三中心高可用架构
采集 20 大类指标：基础信息、会话、空间、性能、会话详情、SQL统计、
复制集群、TDSQL集群监控、锁等待、配置参数、
InnoDB缓冲池、InnoDB行操作、临时表、排序统计、网络吞吐、
Handler统计、表打开统计、Top SQL、对象统计、高可用状态、资源限制。
"""

import pymysql
from monitor.checkers.base import BaseDBChecker


class TDSQLChecker(BaseDBChecker):
    """TDSQL检查器 - 基于MySQL协议，增加分布式特性"""

    def get_connection(self, config):
        """TDSQL MySQL 模式连接"""
        return pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=config.get_password(),
            connect_timeout=5,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def collect_metrics(self, config, conn):
        """TDSQL检查器 - 基于MySQL协议，增加分布式特性"""
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

            host_name = ''
            try:
                cursor.execute("SHOW VARIABLES LIKE 'hostname'")
                row = cursor.fetchone()
                if row:
                    host_name = row['Value'] if isinstance(row, dict) else row[1]
            except Exception:
                pass

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            uptime = int(cursor.fetchone()['Value'])

            cursor.execute("SELECT DATABASE()")
            current_db = cursor.fetchone()['DATABASE()']

            # TDSQL 特有信息
            cursor.execute("SHOW VARIABLES LIKE 'version_comment'")
            try:
                version_comment = cursor.fetchone()['Value']
            except Exception:
                version_comment = 'N/A'

            # SSL状态 (toolkit集成)
            have_ssl = 'DISABLED'
            ssl_cipher = ''
            try:
                cursor.execute("SHOW VARIABLES LIKE 'have_ssl'")
                row = cursor.fetchone()
                if row:
                    have_ssl = row['Value'] if isinstance(row, dict) else row[1]
            except Exception:
                pass
            try:
                cursor.execute("SHOW STATUS LIKE 'Ssl_cipher'")
                row = cursor.fetchone()
                if row:
                    ssl_cipher = row['Value'] if isinstance(row, dict) else row[1]
            except Exception:
                pass

            # Proxy连接检测 (toolkit集成: 通过/*proxy*/注释检测)
            tdsql_conn_type = 'direct'
            try:
                cursor.execute("/*proxy*/SHOW ROUTES")
                proxy_result = cursor.fetchall()
                if proxy_result and len(proxy_result) > 0:
                    tdsql_conn_type = 'proxy'
            except Exception:
                pass

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

            cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
            max_connections = int(cursor.fetchone()['Value'])
            conn_usage_pct = round(
                (threads_connected / max_connections) * 100, 2
            ) if max_connections > 0 else 0

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
            tps = round(
                (com_commit + com_rollback) / uptime, 2
            ) if uptime > 0 else 0

            # =============================================
            # 5. 会话详情 (session_detail)
            # =============================================
            cursor.execute("""
                SELECT 
                    id, user, host, db, command, time, state, info
                FROM information_schema.processlist
                WHERE command != 'Daemon'
                ORDER BY time DESC
                LIMIT 50
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

            # =============================================
            # 6. SQL统计 (sql)
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Slow_queries'")
            slow_queries = int(cursor.fetchone()['Value'])

            cursor.execute("SHOW VARIABLES LIKE 'long_query_time'")
            try:
                long_query_time = float(cursor.fetchone()['Value'])
            except Exception:
                long_query_time = 0

            # =============================================
            # 7. 复制与集群 (replication) - TDSQL高可用增强
            # =============================================
            try:
                cursor.execute("SHOW MASTER STATUS")
                master_status = cursor.fetchone()
                binlog_file = master_status['File'] if master_status else 'N/A'
                binlog_position = master_status['Position'] if master_status else 0
            except Exception:
                binlog_file = 'N/A'
                binlog_position = 0

            cursor.execute("SHOW VARIABLES LIKE 'gtid_mode'")
            try:
                gtid_mode = cursor.fetchone()['Value']
            except Exception:
                gtid_mode = 'OFF'

            cursor.execute("SHOW VARIABLES LIKE 'sync_binlog'")
            try:
                sync_binlog = cursor.fetchone()['Value']
            except Exception:
                sync_binlog = 'N/A'

            # =============================================
            # 7.5 TDSQL ZooKeeper 集群状态监控 - P0 新增
            # =============================================
            # ZK 节点状态 (3中心部署)
            tdsql_zk_nodes = []
            tdsql_zk_healthy_count = 0
            tdsql_zk_total_count = 3  # 默认3个ZK节点
            try:
                cursor.execute("""
                    SELECT NODE_ID, NODE_TYPE, HOST, PORT, STATUS, MODE, 
                           DATA_VERSION, LEADER_ELECT
                    FROM tdsql_zk_status
                """)
                for row in cursor.fetchall():
                    node_status = str(row.get('STATUS', 'UNKNOWN')).upper()
                    if (
                        'ONLINE' in node_status
                        or 'FOLLOWER' in node_status
                        or 'LEADER' in node_status
                    ):
                        tdsql_zk_healthy_count += 1
                    tdsql_zk_nodes.append({
                        "node_id": str(row.get('NODE_ID', 'N/A')),
                        "node_type": str(row.get('NODE_TYPE', 'ZK')),
                        "host": str(row.get('HOST', 'N/A')),
                        "port": int(row.get('PORT', 0)),
                        "status": node_status,
                        "mode": str(row.get('MODE', 'N/A')),
                        "data_version": str(row.get('DATA_VERSION', 'N/A')),
                        "leader_elect": str(row.get('LEADER_ELECT', 'N/A'))
                    })
            except Exception:
                # 备选：尝试其他视图或命令
                try:
                    cursor.execute("SHOW ZK STATUS")
                    zk_status = cursor.fetchall()
                    for row in zk_status:
                        node_status = str(row.get('Status', 'UNKNOWN')).upper()
                        if 'OK' in node_status or 'ONLINE' in node_status:
                            tdsql_zk_healthy_count += 1
                        tdsql_zk_nodes.append({
                            "node_id": str(row.get('Id', 'N/A')),
                            "host": str(row.get('Host', 'N/A')),
                            "status": node_status,
                            "mode": str(row.get('Mode', 'N/A'))
                        })
                except Exception:
                    pass

            # =============================================
            # 7.6 TDSQL Proxy 节点集群监控 - P0 新增
            # =============================================
            # Proxy 节点状态 (对等部署在A、B中心)
            tdsql_proxy_nodes = []
            tdsql_proxy_healthy_count = 0
            tdsql_proxy_total_count = 0
            tdsql_proxy_center_a_count = 0
            tdsql_proxy_center_b_count = 0
            try:
                cursor.execute("""
                    SELECT PROXY_ID, PROXY_IP, PROXY_PORT, STATUS, CENTER_ID,
                           ROLE, SESSION_COUNT, MAX_SESSION
                    FROM tdsql_proxy_status
                """)
                for row in cursor.fetchall():
                    tdsql_proxy_total_count += 1
                    node_status = str(row.get('STATUS', 'UNKNOWN')).upper()
                    if 'ONLINE' in node_status or 'ACTIVE' in node_status:
                        tdsql_proxy_healthy_count += 1

                    center_id = str(row.get('CENTER_ID', 'N/A'))
                    if center_id == 'A':
                        tdsql_proxy_center_a_count += 1
                    elif center_id == 'B':
                        tdsql_proxy_center_b_count += 1

                    max_sess = max(int(row.get('MAX_SESSION', 1)), 1)
                    tdsql_proxy_nodes.append({
                        "proxy_id": str(row.get('PROXY_ID', 'N/A')),
                        "proxy_ip": str(row.get('PROXY_IP', 'N/A')),
                        "proxy_port": int(row.get('PROXY_PORT', 0)),
                        "status": node_status,
                        "center_id": center_id,
                        "role": str(row.get('ROLE', 'N/A')),
                        "session_count": int(row.get('SESSION_COUNT', 0)),
                        "max_session": int(row.get('MAX_SESSION', 0)),
                        "session_usage_pct": round(
                            int(row.get('SESSION_COUNT', 0)) / max_sess * 100, 2
                        )
                    })
            except Exception:
                pass

            # =============================================
            # 7.7 TDSQL 数据节点集群监控 - P0 新增
            # =============================================
            # 数据节点状态 (对等部署在A、B中心，4副本模式)
            tdsql_dn_nodes = []
            tdsql_dn_healthy_count = 0
            tdsql_dn_total_count = 0
            tdsql_dn_center_a_count = 0
            tdsql_dn_center_b_count = 0
            tdsql_dn_primary_count = 0  # 主副本数量
            try:
                cursor.execute("""
                    SELECT NODE_ID, NODE_IP, NODE_PORT, STATUS, CENTER_ID,
                           REPLICA_ROLE, REPLICA_ID, SYNC_STATUS, APPLY_LAG,
                           DATA_SIZE_MB, TOTAL_SPACE_MB, FREE_SPACE_MB
                    FROM tdsql_datanode_status
                """)
                for row in cursor.fetchall():
                    tdsql_dn_total_count += 1
                    node_status = str(row.get('STATUS', 'UNKNOWN')).upper()
                    if 'ONLINE' in node_status or 'ACTIVE' in node_status:
                        tdsql_dn_healthy_count += 1

                    center_id = str(row.get('CENTER_ID', 'N/A'))
                    if center_id == 'A':
                        tdsql_dn_center_a_count += 1
                    elif center_id == 'B':
                        tdsql_dn_center_b_count += 1

                    replica_role = str(row.get('REPLICA_ROLE', 'N/A')).upper()
                    if 'PRIMARY' in replica_role or 'MASTER' in replica_role:
                        tdsql_dn_primary_count += 1

                    d_total = max(float(row.get('TOTAL_SPACE_MB', 1)), 1)
                    tdsql_dn_nodes.append({
                        "node_id": str(row.get('NODE_ID', 'N/A')),
                        "node_ip": str(row.get('NODE_IP', 'N/A')),
                        "node_port": int(row.get('NODE_PORT', 0)),
                        "status": node_status,
                        "center_id": center_id,
                        "replica_role": replica_role,
                        "replica_id": int(row.get('REPLICA_ID', 0)),
                        "sync_status": str(row.get('SYNC_STATUS', 'N/A')),
                        "apply_lag_ms": int(row.get('APPLY_LAG', 0)),
                        "data_size_mb": float(row.get('DATA_SIZE_MB', 0)),
                        "total_space_mb": float(row.get('TOTAL_SPACE_MB', 0)),
                        "free_space_mb": float(row.get('FREE_SPACE_MB', 0)),
                        "space_usage_pct": round(
                            float(row.get('DATA_SIZE_MB', 0)) / d_total * 100, 2
                        )
                    })
            except Exception:
                pass

            # =============================================
            # 7.8 TDSQL 副本同步状态监控 - P0 新增
            # =============================================
            # 4副本配置，主副本在A中心
            tdsql_replica_info = []
            tdsql_replica_healthy_count = 0
            tdsql_replica_total_count = 4  # 4副本模式
            tdsql_cross_center_sync_count = 0
            tdsql_local_sync_count = 0
            try:
                cursor.execute("""
                    SELECT REPLICA_ID, REPLICA_TYPE, CENTER_ID, NODE_IP,
                           SYNC_STATE, SYNC_LAG_MS, CONSISTENCY_STATUS,
                           LAST_SYNC_TIME, READABLE
                    FROM tdsql_replica_status
                """)
                for row in cursor.fetchall():
                    sync_state = str(row.get('SYNC_STATE', 'UNKNOWN')).upper()
                    replica_type = str(row.get('REPLICA_TYPE', 'N/A')).upper()
                    center_id = str(row.get('CENTER_ID', 'N/A'))

                    if 'SYNC' in sync_state or 'ONLINE' in sync_state:
                        tdsql_replica_healthy_count += 1
                        if center_id != 'A':  # 跨中心同步
                            tdsql_cross_center_sync_count += 1
                        else:  # 本中心同步
                            tdsql_local_sync_count += 1

                    tdsql_replica_info.append({
                        "replica_id": int(row.get('REPLICA_ID', 0)),
                        "replica_type": replica_type,
                        "center_id": center_id,
                        "node_ip": str(row.get('NODE_IP', 'N/A')),
                        "sync_state": sync_state,
                        "sync_lag_ms": int(row.get('SYNC_LAG_MS', 0)),
                        "consistency_status": str(
                            row.get('CONSISTENCY_STATUS', 'N/A')
                        ),
                        "last_sync_time": str(row.get('LAST_SYNC_TIME', 'N/A')),
                        "readable": str(row.get('READABLE', 'N/A'))
                    })
            except Exception:
                pass

            # =============================================
            # 7.9 TDSQL 双活三中心健康状态判断 - P0 新增
            # =============================================
            tdsql_cluster_health = 'UNKNOWN'
            tdsql_cluster_issues = []

            # ZK 健康检查
            if tdsql_zk_total_count > 0:
                if tdsql_zk_healthy_count < tdsql_zk_total_count:
                    tdsql_cluster_issues.append(
                        f"ZK节点异常: {tdsql_zk_healthy_count}/{tdsql_zk_total_count}"
                    )

            # Proxy 健康检查 (A、B中心对等)
            if tdsql_proxy_total_count > 0:
                if tdsql_proxy_center_a_count == 0 or tdsql_proxy_center_b_count == 0:
                    tdsql_cluster_issues.append(
                        f"Proxy单中心部署: A中心{tdsql_proxy_center_a_count}, B中心{tdsql_proxy_center_b_count}"
                    )
                if tdsql_proxy_healthy_count < tdsql_proxy_total_count:
                    tdsql_cluster_issues.append(
                        f"Proxy节点异常: {tdsql_proxy_healthy_count}/{tdsql_proxy_total_count}"
                    )

            # 数据节点健康检查 (4副本)
            if tdsql_dn_total_count > 0:
                if tdsql_dn_center_a_count == 0 or tdsql_dn_center_b_count == 0:
                    tdsql_cluster_issues.append(
                        f"数据节点单中心部署: A中心{tdsql_dn_center_a_count}, B中心{tdsql_dn_center_b_count}"
                    )
                if tdsql_dn_primary_count == 0:
                    tdsql_cluster_issues.append("无主副本可用")
                elif tdsql_dn_primary_count < tdsql_replica_total_count - 1:
                    tdsql_cluster_issues.append(
                        f"主副本异常: {tdsql_dn_primary_count} 个主副本"
                    )

            # 副本同步状态检查
            if tdsql_replica_healthy_count < tdsql_replica_total_count:
                tdsql_cluster_issues.append(
                    f"副本同步异常: {tdsql_replica_healthy_count}/{tdsql_replica_total_count} 正常"
                )

            # 整体健康状态判断
            if len(tdsql_cluster_issues) == 0:
                if (
                    tdsql_zk_healthy_count == tdsql_zk_total_count
                    and tdsql_proxy_healthy_count == tdsql_proxy_total_count
                    and tdsql_dn_healthy_count == tdsql_dn_total_count
                ):
                    tdsql_cluster_health = 'HEALTHY'
                else:
                    tdsql_cluster_health = 'DEGRADED'
            elif (
                'ZK节点异常' in str(tdsql_cluster_issues)
                or '无主副本可用' in str(tdsql_cluster_issues)
            ):
                tdsql_cluster_health = 'CRITICAL'
            else:
                tdsql_cluster_health = 'DEGRADED'

            # TDSQL 集群汇总信息
            tdsql_cluster_summary = {
                "zk_node_count": tdsql_zk_total_count,
                "zk_healthy_count": tdsql_zk_healthy_count,
                "proxy_node_count": tdsql_proxy_total_count,
                "proxy_healthy_count": tdsql_proxy_healthy_count,
                "proxy_center_a_count": tdsql_proxy_center_a_count,
                "proxy_center_b_count": tdsql_proxy_center_b_count,
                "dn_node_count": tdsql_dn_total_count,
                "dn_healthy_count": tdsql_dn_healthy_count,
                "dn_center_a_count": tdsql_dn_center_a_count,
                "dn_center_b_count": tdsql_dn_center_b_count,
                "replica_config": tdsql_replica_total_count,
                "replica_healthy_count": tdsql_replica_healthy_count,
                "primary_replica_count": tdsql_dn_primary_count,
                "cross_center_sync_count": tdsql_cross_center_sync_count,
                "local_sync_count": tdsql_local_sync_count,
            }

            # =============================================
            # 8. 锁等待
            # =============================================
            locks = []
            try:
                cursor.execute("""
                    SELECT 
                        b.trx_mysql_thread_id as blocker_thread,
                        r.trx_mysql_thread_id as blocked_thread,
                        TIMESTAMPDIFF(SECOND, r.trx_started, NOW()) as wait_sec
                    FROM performance_schema.data_lock_waits w
                    INNER JOIN information_schema.innodb_trx b ON w.BLOCKING_ENGINE_TRANSACTION_ID = b.trx_id
                    INNER JOIN information_schema.innodb_trx r ON w.REQUESTING_ENGINE_TRANSACTION_ID = r.trx_id
                """)
                for row in cursor.fetchall():
                    locks.append({
                        "blocker_id": str(row['blocker_thread']),
                        "waiter_id": str(row['blocked_thread']),
                        "seconds": int(row['wait_sec'])
                    })
            except Exception:
                pass

            # =============================================
            # 9. 配置参数
            # =============================================
            config_params = {}
            for key in [
                'max_connections', 'innodb_buffer_pool_size', 'gtid_mode',
                'sync_binlog'
            ]:
                try:
                    cursor.execute(f"SHOW VARIABLES LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        config_params[key] = row['Value']
                except Exception:
                    pass

            # =============================================
            # 10. InnoDB 缓冲池 (innodb_buffer) - P0
            # =============================================
            cursor.execute("SHOW VARIABLES LIKE 'innodb_buffer_pool_size'")
            try:
                innodb_buffer_pool_size = int(cursor.fetchone()['Value'])
                innodb_buffer_pool_size_mb = round(innodb_buffer_pool_size / 1024 / 1024, 2)
            except Exception:
                innodb_buffer_pool_size_mb = 0

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
            buffer_hit_ratio = round(
                (1 - innodb_buffer_pool_reads / innodb_buffer_pool_read_requests) * 100, 2
            ) if innodb_buffer_pool_read_requests > 0 else 0

            # InnoDB 缓冲池页面详情 (toolkit集成)
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
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_dirty'")
            try:
                innodb_buffer_pool_pages_dirty = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_buffer_pool_pages_dirty = 0
            buffer_dirty_ratio = round(
                innodb_buffer_pool_pages_dirty / innodb_buffer_pool_pages_total * 100, 2
            ) if innodb_buffer_pool_pages_total > 0 else 0

            # =============================================
            # 11. InnoDB 行操作 (innodb_rows) - P1
            # =============================================
            innodb_row_stats = {}
            innodb_rows_read = innodb_rows_inserted = innodb_rows_updated = innodb_rows_deleted = 0
            for key in ['Innodb_rows_read', 'Innodb_rows_inserted', 'Innodb_rows_updated', 'Innodb_rows_deleted']:
                try:
                    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        val = int(row['Value'])
                        innodb_row_stats[key] = val
                        if key == 'Innodb_rows_read':
                            innodb_rows_read = val
                        elif key == 'Innodb_rows_inserted':
                            innodb_rows_inserted = val
                        elif key == 'Innodb_rows_updated':
                            innodb_rows_updated = val
                        elif key == 'Innodb_rows_deleted':
                            innodb_rows_deleted = val
                except Exception:
                    pass

            # =============================================
            # 11.5 InnoDB IO & 日志详情 (toolkit集成)
            # =============================================
            innodb_data_reads = innodb_data_writes = innodb_data_fsyncs = 0
            innodb_os_log_written = innodb_log_waits = 0
            try:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_data_reads'")
                innodb_data_reads = int(cursor.fetchone()['Value'])
            except Exception:
                pass
            try:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_data_writes'")
                innodb_data_writes = int(cursor.fetchone()['Value'])
            except Exception:
                pass
            try:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_data_fsyncs'")
                innodb_data_fsyncs = int(cursor.fetchone()['Value'])
            except Exception:
                pass
            try:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_os_log_written'")
                innodb_os_log_written = int(cursor.fetchone()['Value'])
            except Exception:
                pass
            try:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_log_waits'")
                innodb_log_waits = int(cursor.fetchone()['Value'])
            except Exception:
                pass

            # =============================================
            # 11.6 死锁 & Key Buffer & 线程缓存 (toolkit集成)
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks'")
            try:
                innodb_deadlocks = int(cursor.fetchone()['Value'])
            except Exception:
                innodb_deadlocks = 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_reads'")
            try:
                key_reads = int(cursor.fetchone()['Value'])
            except Exception:
                key_reads = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_read_requests'")
            try:
                key_read_requests = int(cursor.fetchone()['Value'])
            except Exception:
                key_read_requests = 0
            key_buffer_hit_ratio = round(
                (1 - key_reads / key_read_requests) * 100, 2
            ) if key_read_requests > 0 else 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_created'")
            try:
                threads_created = int(cursor.fetchone()['Value'])
            except Exception:
                threads_created = 0
            thread_cache_hit_ratio = round(
                (1 - threads_created / (threads_connected + threads_created)) * 100, 2
            ) if (threads_connected + threads_created) > 0 else 0

            # =============================================
            # 12. 临时表统计 (temp_table) - P0
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
            tmp_disk_ratio = round(created_tmp_disk_tables / created_tmp_tables * 100, 2) if created_tmp_tables > 0 else 0

            # =============================================
            # 13. 排序统计 (sort) - P0
            # =============================================
            sort_stats = {}
            for key in ['Sort_merge_passes', 'Sort_range', 'Sort_rows', 'Sort_scan']:
                try:
                    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        sort_stats[key] = int(row['Value'])
                except Exception:
                    pass

            # =============================================
            # 14. 网络吞吐 (network) - P1
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

            # =============================================
            # 15. Handler 统计 (handler) - P1
            # =============================================
            handler_stats = {}
            for key in ['Handler_read_first', 'Handler_read_key', 'Handler_read_next',
                         'Handler_read_rnd', 'Handler_read_rnd_next',
                         'Handler_write', 'Handler_update', 'Handler_delete']:
                try:
                    cursor.execute(f"SHOW GLOBAL STATUS LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        handler_stats[key] = int(row['Value'])
                except Exception:
                    pass

            # =============================================
            # 16. 表打开统计 (open_tables) - P1
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
            cursor.execute("SHOW VARIABLES LIKE 'table_open_cache'")
            try:
                table_open_cache = int(cursor.fetchone()['Value'])
            except Exception:
                table_open_cache = 0
            table_cache_hit_ratio = round(open_tables / (open_tables + opened_tables) * 100, 2) if (open_tables + opened_tables) > 0 else 0

            # =============================================
            # 17. Top SQL (top_sql) - P1
            # =============================================
            top_sql_by_latency = []
            # SQL摘要统计 (toolkit集成: 从digest表采集更详细统计)
            sql_digest_stats = []
            try:
                cursor.execute("""
                    SELECT DIGEST_TEXT as sql_text, SCHEMA_NAME, COUNT_STAR as exec_count,
                           ROUND(SUM_TIMER_WAIT/1000000000000, 4) as total_latency_sec,
                           ROUND(AVG_TIMER_WAIT/1000000000000, 6) as avg_latency_sec,
                           ROUND(MAX_TIMER_WAIT/1000000000000, 4) as max_latency_sec,
                           SUM_ROWS_EXAMINED as rows_examined,
                           SUM_ROWS_SENT as rows_sent,
                           SUM_ERRORS as errors,
                           SUM_NO_INDEX_USED as no_index_used
                    FROM performance_schema.events_statements_summary_by_digest
                    WHERE SCHEMA_NAME IS NOT NULL AND COUNT_STAR > 0
                    ORDER BY SUM_TIMER_WAIT DESC LIMIT 20
                """)
                for row in cursor.fetchall():
                    item = {
                        "sql_text": (row['sql_text'] or 'N/A')[:200],
                        "schema_name": row.get('SCHEMA_NAME', 'N/A'),
                        "exec_count": int(row['exec_count'] or 0),
                        "total_latency_sec": round(float(row['total_latency_sec'] or 0), 4),
                        "avg_latency_sec": round(float(row['avg_latency_sec'] or 0), 6),
                        "max_latency_sec": round(float(row['max_latency_sec'] or 0), 4),
                        "rows_examined": int(row['rows_examined'] or 0),
                        "rows_sent": int(row['rows_sent'] or 0),
                        "errors": int(row['errors'] or 0),
                        "no_index_used": int(row['no_index_used'] or 0),
                    }
                    top_sql_by_latency.append(item)
                    sql_digest_stats.append(item)
            except Exception:
                pass

            # =============================================
            # 17.5 未使用索引分析 (toolkit集成)
            # =============================================
            unused_indexes = []
            try:
                cursor.execute("""
                    SELECT OBJECT_SCHEMA as schema_name, OBJECT_NAME as table_name,
                           INDEX_NAME as index_name
                    FROM performance_schema.table_io_waits_summary_by_index_usage
                    WHERE INDEX_NAME IS NOT NULL
                      AND INDEX_NAME != 'PRIMARY'
                      AND COUNT_STAR = 0
                      AND OBJECT_SCHEMA NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
                    ORDER BY OBJECT_SCHEMA, OBJECT_NAME
                    LIMIT 50
                """)
                for row in cursor.fetchall():
                    unused_indexes.append({
                        "schema_name": row['schema_name'],
                        "table_name": row['table_name'],
                        "index_name": row['index_name'],
                    })
            except Exception:
                pass

            # =============================================
            # 18. 对象统计 (object) - P1
            # =============================================
            table_size_top20 = []
            try:
                cursor.execute("""
                    SELECT table_schema, table_name,
                           ROUND((data_length + index_length) / 1024 / 1024, 2) as size_mb,
                           table_rows
                    FROM information_schema.tables
                    WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                    ORDER BY (data_length + index_length) DESC
                    LIMIT 20
                """)
                for row in cursor.fetchall():
                    table_size_top20.append({
                        "schema": row['table_schema'],
                        "table_name": row['table_name'],
                        "size_mb": float(row['size_mb'] or 0),
                        "rows": int(row['table_rows'] or 0)
                    })
            except Exception:
                pass

            object_summary = {}
            try:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM information_schema.tables
                    WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                      AND TABLE_TYPE = 'BASE TABLE'
                """)
                row = cursor.fetchone()
                object_summary['table_count'] = int(row['cnt'] or 0) if row else 0
            except Exception:
                object_summary['table_count'] = 0

            # =============================================
            # 19. 高可用状态 (ha_status) - P2
            # =============================================
            ha_status = {
                "cluster_health": tdsql_cluster_health,
                "cluster_issues": tdsql_cluster_issues,
                "zk_nodes": tdsql_zk_healthy_count,
                "proxy_nodes": tdsql_proxy_healthy_count,
                "dn_nodes": tdsql_dn_healthy_count,
                "gtid_mode": gtid_mode,
                "sync_binlog": sync_binlog,
            }

            # =============================================
            # 20. 资源限制 (resource_limits) - P1
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

        # BUGFIX: 原代码引用了未定义的 shards_info、shard_count、primary_shards，
        # 已替换为合理的备选值
        return {
            # 基础信息
            "version": version[:50] + "...",
            "server_id": server_id,
            "version_comment": version_comment,
            "current_database": current_db,
            "uptime_seconds": uptime,
            "host_name": host_name,
            "ssl_enabled": have_ssl not in ('DISABLED', 'NO', ''),
            "ssl_cipher": ssl_cipher,
            "tdsql_conn_type": tdsql_conn_type,

            # 连接会话
            "threads_connected": threads_connected,
            "threads_running": threads_running,
            "max_connections": max_connections,
            "conn_usage_pct": conn_usage_pct,

            # 空间
            "database_sizes": database_sizes,

            # 性能
            "qps": qps,
            "tps": tps,

            # 会话详情
            "session_list": session_list,

            # SQL统计
            "slow_queries_total": slow_queries,
            "slow_queries": slow_queries,
            "long_query_time_sec": long_query_time,

            # 复制集群 - TDSQL特有
            "binlog_file": binlog_file,
            "binlog_position": binlog_position,
            "gtid_mode": gtid_mode,
            "sync_binlog": sync_binlog,
            # BUGFIX: 原代码 shards_info/shard_count/primary_shards 未定义，
            # 使用 tdsql_dn_nodes 和 tdsql_cluster_summary 数据
            "shards_info": tdsql_dn_nodes,
            "shard_count": tdsql_dn_total_count,
            "primary_shards": tdsql_dn_primary_count,

            # TDSQL 双活三中心集群监控 (新增)
            "tdsql_zk_nodes": tdsql_zk_nodes,
            "tdsql_proxy_nodes": tdsql_proxy_nodes,
            "tdsql_dn_nodes": tdsql_dn_nodes,
            "tdsql_replica_info": tdsql_replica_info,
            "tdsql_cluster_health": tdsql_cluster_health,
            "tdsql_cluster_issues": tdsql_cluster_issues,
            "tdsql_cluster_summary": tdsql_cluster_summary,
            # 前端兼容字段
            "tdsql_zk_node_count": len(tdsql_zk_nodes) if tdsql_zk_nodes else 0,
            "tdsql_proxy_count": len(tdsql_proxy_nodes) if tdsql_proxy_nodes else 0,
            "tdsql_dn_total_count": tdsql_dn_total_count,
            "tdsql_dn_primary_count": tdsql_dn_primary_count,

            # 锁
            "locks": locks,

            # 配置
            "config_params": config_params,

            # InnoDB 缓冲池
            "innodb_buffer_pool_size_mb": innodb_buffer_pool_size_mb,
            "buffer_hit_ratio": buffer_hit_ratio,
            "innodb_buffer_pool_hit_ratio": buffer_hit_ratio,
            "innodb_buffer_pool_pages_total": innodb_buffer_pool_pages_total,
            "innodb_buffer_pool_pages_free": innodb_buffer_pool_pages_free,
            "innodb_buffer_pool_pages_dirty": innodb_buffer_pool_pages_dirty,
            "buffer_dirty_ratio": buffer_dirty_ratio,

            # InnoDB 行操作
            "innodb_row_stats": innodb_row_stats,

            # InnoDB 行操作速率 (toolkit集成)
            "innodb_rows_read_ps": round(innodb_rows_read / uptime, 2) if uptime > 0 else 0,
            "innodb_rows_inserted_ps": round(innodb_rows_inserted / uptime, 2) if uptime > 0 else 0,
            "innodb_rows_updated_ps": round(innodb_rows_updated / uptime, 2) if uptime > 0 else 0,
            "innodb_rows_deleted_ps": round(innodb_rows_deleted / uptime, 2) if uptime > 0 else 0,
            "innodb_deadlocks": innodb_deadlocks,

            # InnoDB IO (toolkit集成)
            "innodb_data_reads": innodb_data_reads,
            "innodb_data_writes": innodb_data_writes,
            "innodb_data_fsyncs": innodb_data_fsyncs,
            "innodb_data_reads_ps": round(innodb_data_reads / uptime, 2) if uptime > 0 else 0,
            "innodb_data_writes_ps": round(innodb_data_writes / uptime, 2) if uptime > 0 else 0,
            "innodb_log_waits": innodb_log_waits,
            "innodb_log_waits_ps": round(innodb_log_waits / uptime, 2) if uptime > 0 else 0,
            "innodb_os_log_written": innodb_os_log_written,
            "innodb_os_log_written_ps": round(innodb_os_log_written / uptime, 2) if uptime > 0 else 0,

            # 缓存效率 (toolkit集成)
            "key_buffer_hit_ratio": key_buffer_hit_ratio,
            "thread_cache_hit_ratio": thread_cache_hit_ratio,
            "table_open_cache_hit_ratio": table_cache_hit_ratio,
            "table_cache_hit_ratio": table_cache_hit_ratio,

            # 临时表
            "created_tmp_tables": created_tmp_tables,
            "created_tmp_disk_tables": created_tmp_disk_tables,
            "tmp_disk_ratio": tmp_disk_ratio,

            # 排序统计
            "sort_stats": sort_stats,

            # 网络吞吐
            "bytes_received_mb": round(bytes_received / 1024 / 1024, 2),
            "bytes_sent_mb": round(bytes_sent / 1024 / 1024, 2),

            # Handler 统计
            "handler_stats": handler_stats,

            # 表打开统计
            "open_tables": open_tables,
            "opened_tables": opened_tables,
            "table_open_cache": table_open_cache,
            "table_cache_hit_ratio": table_cache_hit_ratio,

            # Top SQL (增强版, toolkit集成)
            "top_sql_by_latency": top_sql_by_latency,
            "sql_digest_stats": sql_digest_stats,

            # 未使用索引 (toolkit集成)
            "unused_indexes": unused_indexes,

            # 对象统计
            "table_size_top20": table_size_top20,
            "object_summary": object_summary,

            # 高可用状态
            "ha_status": ha_status,

            # 资源限制
            "resource_limits": resource_limits,
        }
