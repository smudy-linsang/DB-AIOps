# -*- coding: utf-8 -*-
"""
Gbase8a 数据库检查器
基于 MySQL 协议，增加南大通用 Gbase8a 集群特性
"""

import pymysql
from monitor.checkers.base import BaseDBChecker


class GbaseChecker(BaseDBChecker):
    """Gbase8a 检查器 - 基于MySQL协议，增加集群特性"""

    def get_connection(self, config):
        """Gbase8a 使用 MySQL 协议连接"""
        return pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=config.get_password(),
            connect_timeout=5,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def collect_metrics(self, config, conn):
        """Gbase8a 检查器 - 基于MySQL协议，增加集群特性"""
        with conn.cursor() as cursor:
            # =============================================
            # 1. 基础信息 (basic)
            # =============================================
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()['VERSION()']

            cursor.execute("SELECT @@server_id")
            try:
                server_id = int(cursor.fetchone()['@@server_id'])
            except:
                server_id = 0

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
            except:
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
            except:
                com_commit = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Com_rollback'")
            try:
                com_rollback = int(cursor.fetchone()['Value'])
            except:
                com_rollback = 0
            tps = round(
                (com_commit + com_rollback) / uptime, 2
            ) if uptime > 0 else 0

            # InnoDB 相关
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_read'")
            try:
                innodb_rows_read = int(cursor.fetchone()['Value'])
            except:
                innodb_rows_read = 0
            cursor.execute(
                "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'"
            )
            try:
                innodb_buffer_pool_reads = int(cursor.fetchone()['Value'])
            except:
                innodb_buffer_pool_reads = 0

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
            except:
                long_query_time = 0

            # =============================================
            # 7. 复制与集群 (replication) - Gbase8A 集群增强
            # =============================================
            cursor.execute("SHOW MASTER STATUS")
            try:
                master_status = cursor.fetchone()
                binlog_file = master_status['File'] if master_status else 'N/A'
                binlog_position = master_status['Position'] if master_status else 0
            except:
                binlog_file = 'N/A'
                binlog_position = 0

            # =============================================
            # 7.5 Gbase8A 管理节点集群监控 - P0 新增
            # =============================================
            # 管理节点 (Cluster Manager) 状态
            gbase_cm_nodes = []
            gbase_cm_healthy_count = 0
            gbase_cm_total_count = 0
            try:
                cursor.execute("""
                    SELECT NODEID, NODENAME, NODEIP, NODETYPE, STATUS, 
                           MASTER_SLAVE, ONLINE_TIME, CPU_USED, MEM_USED
                    FROM gcluster_v$node_info
                    WHERE NODETYPE IN ('CM', 'MGR', 'MANAGER', 'CLUSTER_MANAGER')
                """)
                for row in cursor.fetchall():
                    gbase_cm_total_count += 1
                    node_status = str(row[4]).upper() if row[4] else 'UNKNOWN'
                    if (
                        'ONLINE' in node_status
                        or 'ACTIVE' in node_status
                        or 'HEALTHY' in node_status
                    ):
                        gbase_cm_healthy_count += 1
                    gbase_cm_nodes.append({
                        "node_id": str(row[0]) if row[0] else 'N/A',
                        "node_name": str(row[1]) if row[1] else 'N/A',
                        "node_ip": str(row[2]) if row[2] else 'N/A',
                        "node_type": str(row[3]) if row[3] else 'N/A',
                        "status": node_status,
                        "master_slave": str(row[5]) if row[5] else 'N/A',
                        "online_time": str(row[6]) if row[6] else 'N/A',
                        "cpu_used": float(row[7]) if row[7] else 0,
                        "mem_used": float(row[8]) if row[8] else 0
                    })
            except:
                # 备选查询 - 尝试其他视图
                try:
                    cursor.execute(
                        "SELECT * FROM gcluster_v$node_status LIMIT 50"
                    )
                    for row in cursor.fetchall():
                        gbase_cm_total_count += 1
                        node_status = str(
                            row.get('STATUS', 'UNKNOWN')
                        ).upper()
                        if (
                            'ONLINE' in node_status
                            or 'ACTIVE' in node_status
                        ):
                            gbase_cm_healthy_count += 1
                        gbase_cm_nodes.append({
                            "node_id": str(row.get('NODE_ID', 'N/A')),
                            "node_name": str(row.get('NODE_NAME', 'N/A')),
                            "node_ip": str(row.get('HOST', 'N/A')),
                            "node_type": str(row.get('ROLE', 'N/A')),
                            "status": node_status,
                            "role": str(row.get('ROLE', 'N/A'))
                        })
                except:
                    pass

            # =============================================
            # 7.6 Gbase8A 数据节点集群监控 - P0 新增
            # =============================================
            # 数据节点 (Data Node) 状态
            gbase_dn_nodes = []
            gbase_dn_healthy_count = 0
            gbase_dn_total_count = 0
            gbase_dn_replica_count = 3  # 3副本配置
            try:
                cursor.execute("""
                    SELECT NODEID, NODENAME, NODEIP, NODETYPE, STATUS,
                           REPLICA_NUM, DATANODE_NUM, TOTAL_SPACE, FREE_SPACE,
                           READ_COUNT, WRITE_COUNT, SYNC_STATUS
                    FROM gcluster_v$node_info
                    WHERE NODETYPE IN ('DN', 'DATA', 'DATANODE', 'SN', 'STORAGENODE')
                """)
                for row in cursor.fetchall():
                    gbase_dn_total_count += 1
                    node_status = str(row[4]).upper() if row[4] else 'UNKNOWN'
                    if (
                        'ONLINE' in node_status
                        or 'ACTIVE' in node_status
                        or 'HEALTHY' in node_status
                    ):
                        gbase_dn_healthy_count += 1
                    gbase_dn_nodes.append({
                        "node_id": str(row[0]) if row[0] else 'N/A',
                        "node_name": str(row[1]) if row[1] else 'N/A',
                        "node_ip": str(row[2]) if row[2] else 'N/A',
                        "node_type": str(row[3]) if row[3] else 'N/A',
                        "status": node_status,
                        "replica_num": int(row[5]) if row[5] else 0,
                        "datanode_num": int(row[6]) if row[6] else 0,
                        "total_space_gb": float(row[7]) / 1024 if row[7] else 0,
                        "free_space_gb": float(row[8]) / 1024 if row[8] else 0,
                        "read_count": int(row[9]) if row[9] else 0,
                        "write_count": int(row[10]) if row[10] else 0,
                        "sync_status": str(row[11]) if row[11] else 'N/A'
                    })
            except:
                # 备选查询 - 尝试其他视图
                try:
                    cursor.execute(
                        "SELECT * FROM gnode_v$dnodetatus LIMIT 50"
                    )
                    for row in cursor.fetchall():
                        gbase_dn_total_count += 1
                        node_status = str(
                            row.get('STATUS', 'UNKNOWN')
                        ).upper()
                        if 'ONLINE' in node_status or 'ACTIVE' in node_status:
                            gbase_dn_healthy_count += 1
                        gbase_dn_nodes.append({
                            "node_id": str(row.get('NODE_ID', 'N/A')),
                            "node_name": str(row.get('NODE_NAME', 'N/A')),
                            "node_ip": str(row.get('HOST', 'N/A')),
                            "node_type": "DN",
                            "status": node_status,
                            "sync_status": str(row.get('SYNC_STATUS', 'N/A'))
                        })
                except:
                    pass

            # =============================================
            # 7.7 Gbase8A 副本一致性检查 - P0 新增
            # =============================================
            # 副本状态详情
            gbase_replica_info = []
            try:
                cursor.execute("""
                    SELECT GROUP_ID, NODE_ID, REPLICA_TYPE, REPLICA_STATUS,
                           REPLICA_SEQ, REPLICA_OFFSET, SYNC_MODE
                    FROM gcluster_v$replica_status
                """)
                for row in cursor.fetchall():
                    gbase_replica_info.append({
                        "group_id": int(row[0]) if row[0] else 0,
                        "node_id": str(row[1]) if row[1] else 'N/A',
                        "replica_type": str(row[2]) if row[2] else 'N/A',
                        "replica_status": str(row[3]) if row[3] else 'N/A',
                        "replica_seq": int(row[4]) if row[4] else 0,
                        "replica_offset": str(row[5]) if row[5] else 'N/A',
                        "sync_mode": str(row[6]) if row[6] else 'N/A'
                    })
            except:
                pass

            # 副本健康统计
            replica_healthy_count = 0
            replica_total_count = len(gbase_replica_info)
            for replica in gbase_replica_info:
                status = str(replica['replica_status']).upper()
                if 'SYNC' in status or 'ONLINE' in status or 'ACTIVE' in status:
                    replica_healthy_count += 1

            # =============================================
            # 7.8 Gbase8A 集群健康状态判断 - P0 新增
            # =============================================
            gbase_cluster_health = 'UNKNOWN'
            gbase_cluster_issues = []

            if gbase_cm_total_count > 0:
                if gbase_cm_healthy_count == gbase_cm_total_count:
                    if gbase_dn_total_count > 0:
                        if gbase_dn_healthy_count == gbase_dn_total_count:
                            if replica_healthy_count == replica_total_count:
                                gbase_cluster_health = 'HEALTHY'
                            else:
                                gbase_cluster_health = 'DEGRADED'
                                gbase_cluster_issues.append(
                                    f"副本异常: {replica_healthy_count}/{replica_total_count} 正常"
                                )
                        else:
                            gbase_cluster_health = 'DEGRADED'
                            gbase_cluster_issues.append(
                                f"数据节点异常: {gbase_dn_healthy_count}/{gbase_dn_total_count} 正常"
                            )
                    else:
                        gbase_cluster_health = 'DEGRADED'
                        gbase_cluster_issues.append("未检测到数据节点")
                else:
                    gbase_cluster_health = 'UNHEALTHY'
                    gbase_cluster_issues.append(
                        f"管理节点异常: {gbase_cm_healthy_count}/{gbase_cm_total_count} 正常"
                    )
            else:
                gbase_cluster_health = 'NOT_CLUSTER'
                gbase_cluster_issues.append("未检测到 Gbase8A 集群")

            # 节点故障告警阈值检查
            if gbase_cm_healthy_count < gbase_cm_total_count:
                if gbase_cm_total_count - gbase_cm_healthy_count >= 1:
                    gbase_cluster_health = 'CRITICAL'
                    gbase_cluster_issues.append(
                        f"管理节点离线数量: {gbase_cm_total_count - gbase_cm_healthy_count}"
                    )

            if gbase_dn_total_count > 0:
                failed_dn = gbase_dn_total_count - gbase_dn_healthy_count
                if failed_dn >= 1:
                    if gbase_cluster_health not in ['CRITICAL', 'UNHEALTHY']:
                        gbase_cluster_health = 'CRITICAL'
                    gbase_cluster_issues.append(
                        f"数据节点离线数量: {failed_dn}"
                    )

            # Gbase 集群汇总信息
            gbase_cluster_summary = {
                "cm_node_count": gbase_cm_total_count,
                "cm_healthy_count": gbase_cm_healthy_count,
                "dn_node_count": gbase_dn_total_count,
                "dn_healthy_count": gbase_dn_healthy_count,
                "replica_count": replica_total_count,
                "replica_healthy_count": replica_healthy_count,
                "replica_config": gbase_dn_replica_count,
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
            except:
                pass

            # =============================================
            # 9. 配置参数
            # =============================================
            config_params = {}
            for key in ['max_connections', 'innodb_buffer_pool_size']:
                try:
                    cursor.execute(f"SHOW VARIABLES LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        config_params[key] = row['Value']
                except:
                    pass

        # BUGFIX: 原代码引用了未定义的 cluster_nodes 和 cluster_info，
        # 已替换为 gbase_cm_nodes 和 gbase_cluster_summary
        return {
            # 基础信息
            "version": version[:50] + "...",
            "server_id": server_id,
            "current_database": current_db,
            "uptime_seconds": uptime,

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
            "innodb_rows_read": innodb_rows_read,
            "innodb_buffer_pool_reads": innodb_buffer_pool_reads,

            # 会话详情
            "session_list": session_list,

            # SQL统计
            "slow_queries_total": slow_queries,
            "long_query_time_sec": long_query_time,

            # 复制集群 - Gbase特有
            "cluster_nodes": gbase_cm_nodes,
            "cluster_info": gbase_cluster_summary,
            "binlog_file": binlog_file,
            "binlog_position": binlog_position,

            # Gbase8A 集群监控 (新增)
            "gbase_cm_nodes": gbase_cm_nodes,
            "gbase_dn_nodes": gbase_dn_nodes,
            "gbase_replica_info": gbase_replica_info,
            "gbase_cluster_health": gbase_cluster_health,
            "gbase_cluster_issues": gbase_cluster_issues,
            "gbase_cluster_summary": gbase_cluster_summary,

            # 锁
            "locks": locks,

            # 配置
            "config_params": config_params,
        }
