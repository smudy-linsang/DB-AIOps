# -*- coding: utf-8 -*-
"""
TDSQL 检查器（2026-07 基于真实环境验证全面重写）

真实 TDSQL (MySQL版) 架构: proxy 网关 + set (1主N备)。proxy 有两种模式:
- "no shard"    集中式实例, 单 set
- "group shard" 分布式实例, 多 set 按 shardkey hash 范围分片

已在真实环境 (内核 TXSQL 8.0.33-v24, proxy-22.3.5) 验证的接口:
- /*proxy*/ 网关命令 —— 只认小写! 大写会报 694 错误甚至被网关直接断开连接:
    help / show config / show status / show routes / show processlist
    groupshard 专有: show shardkey / show table with shardkey 等
    不支持: show sqlstat / show slowsql / show version (694)
- 标准 MySQL 接口 (SHOW GLOBAL STATUS/VARIABLES, information_schema,
  performance_schema, SHOW MASTER STATUS) 经 proxy 全部可用
- 集中式实例的 performance_schema digest 可能为空 (consumer 未开),
  Top SQL 需容忍空结果

工程约束:
- 互联网链路偶发 TCP 超时 → get_connection 带重试
- 网关命令万一异常可能断开连接 → 拓扑采集使用独立连接, 与指标主连接隔离
- 广域网延迟高 → SHOW GLOBAL STATUS / VARIABLES 各一次性全量拉取,
  避免逐个 LIKE 查询造成几十次往返
- groupshard 模式下 SHOW GLOBAL STATUS 等语句由 proxy 路由到某一个后端 set,
  返回的是单 set 的值而非全集群聚合, 不同轮次可能落在不同 set 上
  (实测同一实例两轮 slow_queries 分别为 10 和 0)。
  → 已改为 /*sets:allsets*/ 透传按 set 取计数器后求和（TDSQL_SLOWSQL_DESIGN.md §2.4，
    需内网验证 V-4）；透传不可用时 tdsql_counter_basis='single_set' 并留痕。
"""

import re
import time

import pymysql

from monitor import degrade
from monitor.checkers.base import BaseDBChecker

# ── 连接参数 ──────────────────────────────────────────────
# 互联网链路 SYN 丢包率较高, 重试要足够多, 否则会产生 DOWN 误报
CONNECT_RETRIES = 5
CONNECT_TIMEOUT_SEC = 15
READ_TIMEOUT_SEC = 30
RETRY_BACKOFF_SEC = 2

# 节点描述解析: "10.206.0.4:4002@100@0[@0]" → (ip, port, 其余)
# groupshard 下需要按 set 求和的累计计数器 / 瞬时量
_SUM_VARS = (
    'Questions', 'Com_commit', 'Com_rollback', 'Slow_queries',
    'Innodb_rows_read', 'Innodb_rows_inserted', 'Innodb_rows_updated', 'Innodb_rows_deleted',
    'Innodb_data_reads', 'Innodb_data_writes', 'Innodb_data_fsyncs', 'Innodb_os_log_written',
    'Innodb_log_waits', 'Innodb_deadlocks', 'Created_tmp_tables', 'Created_tmp_disk_tables',
    'Bytes_received', 'Bytes_sent',
)
# 只按 set 展示、不求和：连接数要和"单个 set 的 max_connections"比，求和后再比会虚高
_PER_SET_ONLY_VARS = ('Uptime', 'Threads_connected', 'Threads_running')

_NODE_RE = re.compile(r'^(?P<ip>[\d.]+):(?P<port>\d+)')
_IDC_RE = re.compile(r'@(IDC[^@;,\s"]+)')


def _i(mapping, key, default=0):
    """从 dict 安全取 int"""
    try:
        return int(mapping.get(key, default))
    except (TypeError, ValueError):
        return default


def _f(mapping, key, default=0.0):
    """从 dict 安全取 float"""
    try:
        return float(mapping.get(key, default))
    except (TypeError, ValueError):
        return default


def _parse_node(descriptor):
    """解析节点描述串 "ip:port@weight@flag..." → {'ip','port'} 或 None"""
    if not descriptor:
        return None
    m = _NODE_RE.match(descriptor.strip())
    if not m:
        return None
    return {"ip": m.group('ip'), "port": int(m.group('port'))}


class TDSQLChecker(BaseDBChecker):
    """TDSQL 检查器 - MySQL 协议指标 + proxy 网关拓扑"""

    # ==================================================
    # 连接管理
    # ==================================================
    def get_connection(self, config):
        """带重试的 proxy 网关连接 (互联网链路偶发 TCP 超时)"""
        last_err = None
        for attempt in range(CONNECT_RETRIES):
            try:
                return pymysql.connect(
                    host=config.host,
                    port=config.port,
                    user=config.username,
                    password=config.get_password(),
                    # service_name 复用为 TDSQL 默认库名 (可留空)
                    database=getattr(config, 'service_name', None) or None,
                    connect_timeout=CONNECT_TIMEOUT_SEC,
                    read_timeout=READ_TIMEOUT_SEC,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                )
            except Exception as e:
                last_err = e
                if attempt < CONNECT_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_SEC)
        raise last_err

    # ==================================================
    # 主采集入口
    # ==================================================
    def collect_metrics(self, config, conn):
        # 全量状态/变量各拉一次, 后续全部本地计算 (广域网优化)
        gstatus = self._fetch_kv(conn, "SHOW GLOBAL STATUS")
        gvars = self._fetch_kv(conn, "SHOW GLOBAL VARIABLES")

        # ── proxy 网关拓扑 (独立连接, 隔离断连风险) ──
        # 提前到计数器计算之前：groupshard 下 SHOW GLOBAL STATUS 只落在 proxy 随机路由到的
        # 一个 set（文件头已记录实测现象），计数器必须改为按 set 透传后求和（TDSQL_SLOWSQL_DESIGN.md §2.4）
        topo = self._collect_proxy_topology(config)
        counter_basis = 'single'
        per_set_counters = {}
        set_rates = None
        if topo['mode'] == 'groupshard':
            per_set_counters = self._allsets_status(conn)
            if per_set_counters:
                counter_basis = 'per_set_sum'
                set_rates = self._sum_set_rates(per_set_counters)
                for var in _SUM_VARS:
                    gstatus[var] = sum(_i(c, var) for c in per_set_counters.values())
            else:
                counter_basis = 'single_set'
                degrade.note('tdsql.counter_basis',
                             'groupshard 实例透传 SHOW GLOBAL STATUS 不可用，计数器只来自某一个 set')

        # ── 基础信息 ─────────────────────────────
        version = gvars.get('version', '')
        uptime = _i(gstatus, 'Uptime')
        threads_connected = _i(gstatus, 'Threads_connected')
        threads_running = _i(gstatus, 'Threads_running')
        max_connections = _i(gvars, 'max_connections')
        conn_usage_pct = round(threads_connected / max_connections * 100, 2) \
            if max_connections > 0 else 0

        current_db = self._scalar(conn, "SELECT DATABASE() AS v")
        server_id = _i(gvars, 'server_id')
        ssl_cipher = ''
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW STATUS LIKE 'Ssl_cipher'")
                row = cur.fetchone()
                if row:
                    ssl_cipher = row.get('Value', '')
        except Exception as e:
            degrade.note('tdsql.ssl_cipher', 'Ssl_cipher 读取失败', e)
        have_ssl = gvars.get('have_ssl', 'DISABLED')

        # ── 性能指标 (累计值 / uptime, 与平台其他 checker 口径一致) ──
        questions = _i(gstatus, 'Questions')
        if set_rates is not None:
            # 各 set 的 uptime 不同：先按 set 求"累计 / uptime"，再求和
            qps, tps = set_rates
        else:
            qps = round(questions / uptime, 2) if uptime > 0 else 0
            tps = round(
                (_i(gstatus, 'Com_commit') + _i(gstatus, 'Com_rollback')) / uptime, 2
            ) if uptime > 0 else 0

        # ── InnoDB 缓冲池 ─────────────────────────
        bp_size_mb = round(_i(gvars, 'innodb_buffer_pool_size') / 1024 / 1024, 2)
        bp_reads = _i(gstatus, 'Innodb_buffer_pool_reads')
        bp_read_requests = _i(gstatus, 'Innodb_buffer_pool_read_requests')
        buffer_hit_ratio = round((1 - bp_reads / bp_read_requests) * 100, 2) \
            if bp_read_requests > 0 else 0
        bp_pages_total = _i(gstatus, 'Innodb_buffer_pool_pages_total')
        bp_pages_free = _i(gstatus, 'Innodb_buffer_pool_pages_free')
        bp_pages_dirty = _i(gstatus, 'Innodb_buffer_pool_pages_dirty')
        buffer_dirty_ratio = round(bp_pages_dirty / bp_pages_total * 100, 2) \
            if bp_pages_total > 0 else 0

        # ── InnoDB 行操作 / IO / 日志 ──────────────
        innodb_row_stats = {
            k: _i(gstatus, k) for k in (
                'Innodb_rows_read', 'Innodb_rows_inserted',
                'Innodb_rows_updated', 'Innodb_rows_deleted',
            )
        }
        innodb_data_reads = _i(gstatus, 'Innodb_data_reads')
        innodb_data_writes = _i(gstatus, 'Innodb_data_writes')
        innodb_data_fsyncs = _i(gstatus, 'Innodb_data_fsyncs')
        innodb_os_log_written = _i(gstatus, 'Innodb_os_log_written')
        innodb_log_waits = _i(gstatus, 'Innodb_log_waits')
        # Innodb_deadlocks 为 Percona/TXSQL 扩展, 原生 8.0 可能没有
        innodb_deadlocks = _i(gstatus, 'Innodb_deadlocks')

        def _ps(value):
            return round(value / uptime, 2) if uptime > 0 else 0

        # ── 缓存效率 ─────────────────────────────
        key_reads = _i(gstatus, 'Key_reads')
        key_read_requests = _i(gstatus, 'Key_read_requests')
        key_buffer_hit_ratio = round((1 - key_reads / key_read_requests) * 100, 2) \
            if key_read_requests > 0 else 0
        threads_created = _i(gstatus, 'Threads_created')
        thread_cache_hit_ratio = round(
            (1 - threads_created / (threads_connected + threads_created)) * 100, 2
        ) if (threads_connected + threads_created) > 0 else 0
        open_tables = _i(gstatus, 'Open_tables')
        opened_tables = _i(gstatus, 'Opened_tables')
        table_open_cache = _i(gvars, 'table_open_cache')
        table_cache_hit_ratio = round(
            open_tables / (open_tables + opened_tables) * 100, 2
        ) if (open_tables + opened_tables) > 0 else 0

        # ── 临时表 / 排序 / 网络 / Handler ─────────
        created_tmp_tables = _i(gstatus, 'Created_tmp_tables')
        created_tmp_disk_tables = _i(gstatus, 'Created_tmp_disk_tables')
        tmp_disk_ratio = round(
            created_tmp_disk_tables / created_tmp_tables * 100, 2
        ) if created_tmp_tables > 0 else 0
        sort_stats = {
            k: _i(gstatus, k) for k in (
                'Sort_merge_passes', 'Sort_range', 'Sort_rows', 'Sort_scan',
            )
        }
        handler_stats = {
            k: _i(gstatus, k) for k in (
                'Handler_read_first', 'Handler_read_key', 'Handler_read_next',
                'Handler_read_rnd', 'Handler_read_rnd_next',
                'Handler_write', 'Handler_update', 'Handler_delete',
            )
        }

        # ── 复制 / GTID ──────────────────────────
        gtid_mode = gvars.get('gtid_mode', 'OFF')
        sync_binlog = gvars.get('sync_binlog', 'N/A')
        binlog_file, binlog_position = self._master_status(conn)

        # ── 表格型指标 (每项独立容错) ───────────────
        database_sizes = self._database_sizes(conn)
        # groupshard 下会话、锁、Top SQL 同样只落在 proxy 路由到的一个 set：
        # 透传可用时（counter_basis 已证实）改为按 set 取，结果带 set_name
        pt = '/*sets:allsets*/' if counter_basis == 'per_set_sum' else ''
        session_list = self._session_list(conn, pt)
        locks = self._locks(conn, pt)
        slow_queries = _i(gstatus, 'Slow_queries')
        long_query_time = _f(gvars, 'long_query_time')
        top_sql_by_latency = self._top_sql(conn, pt)
        unused_indexes = self._unused_indexes(conn)
        table_size_top20, object_summary = self._object_stats(conn)

        cluster_health = topo['cluster_health']
        cluster_issues = topo['cluster_issues']
        tdsql_sets = topo['sets']
        set_count = len(tdsql_sets)
        master_count = sum(1 for s in tdsql_sets if s.get('master_ip'))
        slave_total = sum(s.get('slave_count', 0) for s in tdsql_sets)
        healthy_set_count = sum(1 for s in tdsql_sets if s.get('is_healthy'))

        ha_status = {
            "cluster_health": cluster_health,
            "cluster_issues": cluster_issues,
            "mode": topo['mode'],
            "set_count": set_count,
            "master_count": master_count,
            "slave_total": slave_total,
            "gtid_mode": gtid_mode,
            "sync_binlog": sync_binlog,
        }

        resource_limits = [
            {
                "resource_name": "max_connections",
                "current_utilization": threads_connected,
                "limit_value": max_connections,
                "usage_pct": conn_usage_pct,
            },
            {
                "resource_name": "table_open_cache",
                "current_utilization": open_tables,
                "limit_value": table_open_cache,
                "usage_pct": round(open_tables / table_open_cache * 100, 2)
                if table_open_cache > 0 else 0,
            },
        ]

        return {
            # 基础信息
            "version": version,
            "server_id": server_id,
            "version_comment": gvars.get('version_comment', 'N/A'),
            "current_database": current_db,
            "uptime_seconds": uptime,
            "host_name": gvars.get('hostname', ''),
            "ssl_enabled": have_ssl not in ('DISABLED', 'NO', ''),
            "ssl_cipher": ssl_cipher,
            "tdsql_conn_type": topo['conn_type'],

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

            # 计数器口径（T-03 / T-15）：qps / tps 为自启动以来的均值；当前速率由
            # start_monitor 用 *_total 的增量计算（qps_current / tps_current）
            "tdsql_counter_basis": counter_basis,
            "tdsql_set_counters": {
                s: {k: _i(c, k) for k in ('Questions', 'Slow_queries', 'Threads_connected',
                                          'Threads_running')}
                for s, c in per_set_counters.items()},
            "questions_total": questions,
            "com_commit_total": _i(gstatus, 'Com_commit'),
            "com_rollback_total": _i(gstatus, 'Com_rollback'),

            # SQL 统计
            "slow_queries_total": slow_queries,
            "slow_queries": slow_queries,
            "long_query_time_sec": long_query_time,

            # 复制
            "binlog_file": binlog_file,
            "binlog_position": binlog_position,
            "gtid_mode": gtid_mode,
            "sync_binlog": sync_binlog,

            # ── TDSQL 真实拓扑 (proxy + set 架构) ──
            "tdsql_mode": topo['mode'],                    # noshard / groupshard / unknown
            "tdsql_mode_display": topo['mode_display'],
            "tdsql_cluster_name": topo['cluster_name'],
            "tdsql_proxy_version": topo['proxy_version'],
            "tdsql_rw_split": topo['rw_split'],
            "tdsql_slow_log_ms": topo['slow_log_ms'],
            "tdsql_sets": tdsql_sets,
            "tdsql_set_count": set_count,
            "tdsql_set_healthy_count": healthy_set_count,
            "tdsql_master_count": master_count,
            "tdsql_slave_total": slave_total,
            "tdsql_proxy_sessions": topo['proxy_sessions'],
            "tdsql_proxy_session_count": topo['proxy_session_count'],
            "tdsql_proxy_endpoints": topo['proxy_endpoints'],
            "tdsql_shard_tables": topo['shard_tables'],    # groupshard 专有
            "tdsql_cluster_health": cluster_health,
            "tdsql_cluster_issues": cluster_issues,
            "tdsql_cluster_notes": topo['cluster_notes'],
            # 问题 + 提示合并为结构化列表, 供前端表格渲染
            "tdsql_cluster_events": (
                [{"level": "问题", "description": s} for s in cluster_issues]
                + [{"level": "提示", "description": s} for s in topo['cluster_notes']]
            ),

            # 兼容字段 (老前端/引擎按分片语义读取)
            "shards_info": tdsql_sets,
            "shard_count": set_count,
            "primary_shards": master_count,

            # 锁
            "locks": locks,

            # 配置
            "config_params": {
                'max_connections': gvars.get('max_connections'),
                'innodb_buffer_pool_size': gvars.get('innodb_buffer_pool_size'),
                'gtid_mode': gtid_mode,
                'sync_binlog': sync_binlog,
            },

            # InnoDB 缓冲池
            "innodb_buffer_pool_size_mb": bp_size_mb,
            "buffer_hit_ratio": buffer_hit_ratio,
            "innodb_buffer_pool_hit_ratio": buffer_hit_ratio,
            "innodb_buffer_pool_pages_total": bp_pages_total,
            "innodb_buffer_pool_pages_free": bp_pages_free,
            "innodb_buffer_pool_pages_dirty": bp_pages_dirty,
            "buffer_dirty_ratio": buffer_dirty_ratio,

            # InnoDB 行操作
            "innodb_row_stats": innodb_row_stats,
            "innodb_rows_read_ps": _ps(innodb_row_stats['Innodb_rows_read']),
            "innodb_rows_inserted_ps": _ps(innodb_row_stats['Innodb_rows_inserted']),
            "innodb_rows_updated_ps": _ps(innodb_row_stats['Innodb_rows_updated']),
            "innodb_rows_deleted_ps": _ps(innodb_row_stats['Innodb_rows_deleted']),
            "innodb_deadlocks": innodb_deadlocks,

            # InnoDB IO
            "innodb_data_reads": innodb_data_reads,
            "innodb_data_writes": innodb_data_writes,
            "innodb_data_fsyncs": innodb_data_fsyncs,
            "innodb_data_reads_ps": _ps(innodb_data_reads),
            "innodb_data_writes_ps": _ps(innodb_data_writes),
            "innodb_log_waits": innodb_log_waits,
            "innodb_log_waits_ps": _ps(innodb_log_waits),
            "innodb_os_log_written": innodb_os_log_written,
            "innodb_os_log_written_ps": _ps(innodb_os_log_written),

            # 缓存效率
            "key_buffer_hit_ratio": key_buffer_hit_ratio,
            "thread_cache_hit_ratio": thread_cache_hit_ratio,
            "table_open_cache_hit_ratio": table_cache_hit_ratio,
            "table_cache_hit_ratio": table_cache_hit_ratio,

            # 临时表
            "created_tmp_tables": created_tmp_tables,
            "created_tmp_disk_tables": created_tmp_disk_tables,
            "tmp_disk_ratio": tmp_disk_ratio,

            # 排序 / 网络 / Handler
            "sort_stats": sort_stats,
            "bytes_received_mb": round(_i(gstatus, 'Bytes_received') / 1024 / 1024, 2),
            "bytes_sent_mb": round(_i(gstatus, 'Bytes_sent') / 1024 / 1024, 2),
            "handler_stats": handler_stats,

            # 表打开
            "open_tables": open_tables,
            "opened_tables": opened_tables,
            "table_open_cache": table_open_cache,

            # Top SQL / 索引
            "top_sql_by_latency": top_sql_by_latency,
            "sql_digest_stats": top_sql_by_latency,
            "unused_indexes": unused_indexes,

            # 对象
            "table_size_top20": table_size_top20,
            "object_summary": object_summary,

            # 高可用 / 资源
            "ha_status": ha_status,
            "resource_limits": resource_limits,
        }

    # ==================================================
    # groupshard：按 set 透传取计数器
    # ==================================================
    def _allsets_status(self, conn):
        """/*sets:allsets*/ SHOW GLOBAL STATUS → {set_name: {var: value}}；不可用返回 {}。"""
        names = ', '.join(f"'{v}'" for v in _SUM_VARS + _PER_SET_ONLY_VARS)
        try:
            with conn.cursor() as cur:
                cur.execute(f"/*sets:allsets*/SHOW GLOBAL STATUS WHERE Variable_name IN ({names})")
                rows = cur.fetchall() or []
        except Exception as e:
            degrade.note('tdsql.allsets_status', '透传 SHOW GLOBAL STATUS 失败', e)
            return {}
        out = {}
        for r in rows:
            set_name = r.get('info') or r.get('INFO')
            if not set_name:
                return {}   # 没有 info 列：透传未生效
            out.setdefault(str(set_name), {})[r.get('Variable_name')] = r.get('Value')
        return out

    @staticmethod
    def _sum_set_rates(per_set):
        qps = tps = 0.0
        for c in per_set.values():
            up = _i(c, 'Uptime')
            if up > 0:
                qps += _i(c, 'Questions') / up
                tps += (_i(c, 'Com_commit') + _i(c, 'Com_rollback')) / up
        return round(qps, 2), round(tps, 2)

    # ==================================================
    # 标准 MySQL 接口辅助
    # ==================================================
    def _fetch_kv(self, conn, sql):
        """SHOW GLOBAL STATUS/VARIABLES → {Variable_name: Value}"""
        with conn.cursor() as cur:
            cur.execute(sql)
            return {r['Variable_name']: r['Value'] for r in cur.fetchall()}

    def _scalar(self, conn, sql):
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return list(row.values())[0] if row else None
        except Exception as e:
            degrade.note('tdsql.scalar', sql[:60], e)
            return None

    def _master_status(self, conn):
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW MASTER STATUS")
                row = cur.fetchone()
                if row:
                    return row.get('File', 'N/A'), row.get('Position', 0)
        except Exception as e:
            degrade.note('tdsql.master_status', 'SHOW MASTER STATUS 失败（权限或 proxy 不支持）', e)
        return 'N/A', 0

    def _database_sizes(self, conn):
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT table_schema AS db_name,
                           ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS size_mb,
                           COUNT(*) AS table_count
                    FROM information_schema.tables
                    WHERE table_schema NOT IN
                          ('information_schema', 'mysql', 'performance_schema', 'sys')
                    GROUP BY table_schema
                    ORDER BY size_mb DESC
                    LIMIT 10
                """)
                return [{
                    "name": r['db_name'],
                    "size_mb": float(r['size_mb'] or 0),
                    "table_count": int(r['table_count'] or 0),
                } for r in cur.fetchall()]
        except Exception as e:
            # 返回空列表会在界面上显示为"无数据"，必须留痕区分"采集不可用"与"确实没有"
            degrade.note('tdsql.database_sizes', '采集失败，界面将显示为空', e)
            return []

    def _session_list(self, conn, prefix=''):
        try:
            with conn.cursor() as cur:
                cur.execute(prefix + """
                    SELECT id, user, host, db, command, time, state, info AS sql_info
                    FROM information_schema.processlist
                    WHERE command != 'Daemon'
                    ORDER BY time DESC
                    LIMIT 50
                """)
                return [{
                    "id": str(r['id']),
                    "user": r['user'] or 'N/A',
                    "host": r['host'] or 'N/A',
                    "db": r['db'] or 'N/A',
                    "command": r['command'] or 'N/A',
                    "time": int(r['time']) if r['time'] else 0,
                    "time_seconds": int(r['time']) if r['time'] else 0,
                    "state": r['state'] or 'N/A',
                    "info": (r['sql_info'] or 'N/A')[:200],
                    # 透传结果的 info 列是 set 名（官方文档），SQL 原文已改名为 sql_info 避免冲突
                    "set_name": r.get('info') if prefix else None,
                } for r in cur.fetchall()]
        except Exception as e:
            # 返回空列表会在界面上显示为"无数据"，必须留痕区分"采集不可用"与"确实没有"
            degrade.note('tdsql.session_list', '采集失败，界面将显示为空', e)
            return []

    def _locks(self, conn, prefix=''):
        try:
            with conn.cursor() as cur:
                cur.execute(prefix + """
                    SELECT
                        b.trx_mysql_thread_id AS blocker_thread,
                        r.trx_mysql_thread_id AS blocked_thread,
                        TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_sec,
                        TIMESTAMPDIFF(SECOND, r.trx_started, NOW()) AS trx_age_sec
                    FROM performance_schema.data_lock_waits w
                    INNER JOIN information_schema.innodb_trx b
                        ON w.BLOCKING_ENGINE_TRANSACTION_ID = b.trx_id
                    INNER JOIN information_schema.innodb_trx r
                        ON w.REQUESTING_ENGINE_TRANSACTION_ID = r.trx_id
                """)
                return [{
                    "blocker_id": str(r['blocker_thread']),
                    "waiter_id": str(r['blocked_thread']),
                    # T-14：等待秒取 trx_wait_started（与哨兵侧 BUG-123 同口径），事务年龄单列
                    "seconds": int(r['wait_sec'] or 0),
                    "trx_age_seconds": int(r.get('trx_age_sec') or 0),
                    "set_name": r.get('info') if prefix else None,
                } for r in cur.fetchall()]
        except Exception as e:
            # 返回空列表会在界面上显示为"无数据"，必须留痕区分"采集不可用"与"确实没有"
            degrade.note('tdsql.locks', '采集失败，界面将显示为空', e)
            return []

    def _top_sql(self, conn, prefix=''):
        """Top SQL by latency; 集中式实例 digest 可能为空 → 返回 []。

        prefix='/*sets:allsets*/' 时各 set 分别返回 Top 20，这里按 DIGEST 跨 set 合并后再取 Top 20。
        """
        try:
            with conn.cursor() as cur:
                cur.execute(prefix + """
                    SELECT DIGEST AS digest, DIGEST_TEXT AS sql_text, SCHEMA_NAME AS schema_name,
                           COUNT_STAR AS exec_count,
                           ROUND(SUM_TIMER_WAIT/1000000000000, 4) AS total_latency_sec,
                           ROUND(AVG_TIMER_WAIT/1000000000000, 6) AS avg_latency_sec,
                           ROUND(MAX_TIMER_WAIT/1000000000000, 4) AS max_latency_sec,
                           SUM_ROWS_EXAMINED AS rows_examined,
                           SUM_ROWS_SENT AS rows_sent,
                           SUM_ERRORS AS errors,
                           SUM_NO_INDEX_USED AS no_index_used
                    FROM performance_schema.events_statements_summary_by_digest
                    WHERE SCHEMA_NAME IS NOT NULL AND COUNT_STAR > 0
                    ORDER BY SUM_TIMER_WAIT DESC LIMIT 20
                """)
                rows = cur.fetchall()
                if prefix:
                    rows = self._merge_digest_rows(rows)
                return [{
                    "sql_text": (r['sql_text'] or 'N/A')[:200],
                    "schema_name": r['schema_name'] or 'N/A',
                    "exec_count": int(r['exec_count'] or 0),
                    "total_latency_sec": float(r['total_latency_sec'] or 0),
                    "avg_latency_sec": float(r['avg_latency_sec'] or 0),
                    "max_latency_sec": float(r['max_latency_sec'] or 0),
                    "rows_examined": int(r['rows_examined'] or 0),
                    "rows_sent": int(r['rows_sent'] or 0),
                    "errors": int(r['errors'] or 0),
                    "no_index_used": int(r['no_index_used'] or 0),
                    "set_count": r.get('_set_count'),
                } for r in rows]
        except Exception as e:
            # 返回空列表会在界面上显示为"无数据"，必须留痕区分"采集不可用"与"确实没有"
            degrade.note('tdsql.top_sql', '采集失败，界面将显示为空', e)
            return []

    @staticmethod
    def _merge_digest_rows(rows):
        merged = {}
        for r in rows:
            key = (r.get('digest') or r.get('sql_text'), r.get('schema_name'))
            m = merged.get(key)
            if m is None:
                m = merged[key] = dict(r)
                m['_sets'] = set()
                for k in ('exec_count', 'total_latency_sec', 'rows_examined', 'rows_sent',
                          'errors', 'no_index_used'):
                    m[k] = 0
                m['max_latency_sec'] = 0
            for k in ('exec_count', 'total_latency_sec', 'rows_examined', 'rows_sent',
                      'errors', 'no_index_used'):
                m[k] += float(r.get(k) or 0)
            m['max_latency_sec'] = max(float(m['max_latency_sec'] or 0), float(r.get('max_latency_sec') or 0))
            if r.get('info'):
                m['_sets'].add(r['info'])
        out = []
        for m in merged.values():
            m['avg_latency_sec'] = m['total_latency_sec'] / m['exec_count'] if m['exec_count'] else 0
            m['_set_count'] = len(m.pop('_sets'))
            out.append(m)
        out.sort(key=lambda m: m['total_latency_sec'], reverse=True)
        return out[:20]

    def _unused_indexes(self, conn):
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT OBJECT_SCHEMA AS schema_name, OBJECT_NAME AS table_name,
                           INDEX_NAME AS index_name
                    FROM performance_schema.table_io_waits_summary_by_index_usage
                    WHERE INDEX_NAME IS NOT NULL
                      AND INDEX_NAME != 'PRIMARY'
                      AND COUNT_STAR = 0
                      AND OBJECT_SCHEMA NOT IN
                          ('mysql', 'performance_schema', 'information_schema', 'sys',
                           'sysdb', 'query_rewrite', 'xa')
                    ORDER BY OBJECT_SCHEMA, OBJECT_NAME
                    LIMIT 50
                """)
                return [{
                    "schema_name": r['schema_name'],
                    "table_name": r['table_name'],
                    "index_name": r['index_name'],
                } for r in cur.fetchall()]
        except Exception as e:
            # 返回空列表会在界面上显示为"无数据"，必须留痕区分"采集不可用"与"确实没有"
            degrade.note('tdsql.unused_indexes', '采集失败，界面将显示为空', e)
            return []

    def _object_stats(self, conn):
        table_size_top20 = []
        object_summary = {'table_count': 0}
        try:
            with conn.cursor() as cur:
                # 注意: MySQL 8 的 information_schema 未加别名时返回大写列名
                cur.execute("""
                    SELECT table_schema AS table_schema, table_name AS table_name,
                           ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb,
                           table_rows AS table_rows,
                           table_type AS table_type
                    FROM information_schema.tables
                    WHERE table_schema NOT IN
                          ('information_schema','mysql','performance_schema','sys')
                    ORDER BY (data_length + index_length) DESC
                """)
                rows = cur.fetchall()
            base_tables = [r for r in rows if r.get('table_type') == 'BASE TABLE']
            object_summary['table_count'] = len(base_tables)
            table_size_top20 = [{
                "schema": r['table_schema'],
                "table_name": r['table_name'],
                "size_mb": float(r['size_mb'] or 0),
                "rows": int(r['table_rows'] or 0),
            } for r in rows[:20]]
        except Exception as e:
            degrade.note('tdsql.object_stats', '对象统计采集失败', e)
        return table_size_top20, object_summary

    # ==================================================
    # proxy 网关拓扑采集 (独立连接)
    # ==================================================
    def _collect_proxy_topology(self, config):
        """通过 /*proxy*/ 网关命令采集集群拓扑。

        使用独立连接: 网关命令异常时可能被 proxy 断开连接,
        不能危及主采集连接。命令必须全小写。
        """
        result = {
            'conn_type': 'direct',
            'mode': 'unknown',
            'mode_display': '未知',
            'cluster_name': 'N/A',
            'proxy_version': 'N/A',
            'rw_split': None,
            'slow_log_ms': None,
            'sets': [],
            'proxy_sessions': [],
            'proxy_session_count': 0,
            'proxy_endpoints': [],
            'shard_tables': [],
            'cluster_health': 'UNKNOWN',
            'cluster_issues': [],
            'cluster_notes': [],
        }

        state = {'conn': None}

        def ensure_conn():
            if state['conn'] is None:
                state['conn'] = self.get_connection(config)
            return state['conn']

        def proxy_rows(cmd, retries=1):
            """执行网关命令; 连接断开时重建并重试一次; 不支持的命令返回 None"""
            for _ in range(retries + 1):
                try:
                    with ensure_conn().cursor() as cur:
                        cur.execute(cmd)
                        return cur.fetchall()
                except (pymysql.err.InterfaceError, pymysql.err.OperationalError):
                    # 连接可能已被网关断开或链路抖动, 重建后重试
                    if state['conn'] is not None:
                        try:
                            state['conn'].close()
                        except Exception as ce:
                            degrade.note('tdsql.proxy_conn_close', '网关连接关闭失败', ce)
                    state['conn'] = None
                except Exception as e:
                    # 694 等"网关不支持该命令"属预期，留痕但不中断拓扑采集
                    degrade.note('tdsql.proxy_cmd', cmd, e)
                    return None
            return None

        try:
            # 1. show config → 模式 / proxy 版本 / 读写分离
            config_rows = proxy_rows("/*proxy*/show config")
            proxy_ok = bool(config_rows)
            if config_rows:
                cfg = {r.get('config_name'): r.get('value') for r in config_rows}
                mode_raw = (cfg.get('mode') or '').strip()
                if mode_raw == 'no shard':
                    result['mode'] = 'noshard'
                    result['mode_display'] = '集中式 (noshard)'
                elif mode_raw == 'group shard':
                    result['mode'] = 'groupshard'
                    result['mode_display'] = '分布式 (groupshard)'
                result['proxy_version'] = (cfg.get('version') or 'N/A').strip('"')
                result['rw_split'] = _i(cfg, 'rw_split', None)
                result['slow_log_ms'] = _i(cfg, 'slow_log_ms', None)

            # 2. show status → 集群名 / set 别名 / hash 范围 / IDC 信息
            status_rows = proxy_rows("/*proxy*/show status") or []
            proxy_ok = proxy_ok or bool(status_rows)
            set_meta = {}   # set_name → {alias, hash_range, idc_list}
            for r in status_rows:
                name = str(r.get('status_name', ''))
                value = str(r.get('value', ''))
                if name == 'cluster':
                    result['cluster_name'] = value
                elif ':' in name:
                    set_name, attr = name.split(':', 1)
                    meta = set_meta.setdefault(set_name, {})
                    if attr == 'alias':
                        meta['alias'] = value
                    elif attr == 'hash_range':
                        meta['hash_range'] = value.replace('---', '-')
                elif name.startswith('set_'):
                    # noshard 形态: set_xxx → "master;slave@ip:port@weight@IDC@..."
                    meta = set_meta.setdefault(name, {})
                    meta['idc_list'] = _IDC_RE.findall(value)

            # 3. show routes → 每个 set 的主备节点与状态 (权威来源)
            route_rows = proxy_rows("/*proxy*/show routes") or []
            proxy_ok = proxy_ok or bool(route_rows)
            sets = []
            for r in route_rows:
                set_name = r.get('set_name') or r.get('cluster_name') or 'N/A'
                status = str(
                    r.get('set_status', r.get('cluster_status', ''))
                ).strip()
                master = _parse_node(r.get('master_ip', ''))
                slaves = []
                for item in str(r.get('slave_iplist') or '').split(';'):
                    node = _parse_node(item)
                    if node:
                        slaves.append(node)
                meta = set_meta.get(set_name, {})
                is_healthy = status in ('', '0') and master is not None
                slave_addrs = [f"{s['ip']}:{s['port']}" for s in slaves]
                idc_list = meta.get('idc_list', [])
                sets.append({
                    "set_name": set_name,
                    "alias": r.get('alias') or meta.get('alias', ''),
                    "status": status or '0',
                    "is_healthy": is_healthy,
                    "health_display": '正常' if is_healthy else '异常',
                    "master_ip": f"{master['ip']}:{master['port']}" if master else '',
                    "slaves": slave_addrs,
                    "slaves_display": ', '.join(slave_addrs) or '无',
                    "slave_count": len(slaves),
                    "shardkey_range": r.get('shardkey', '') or meta.get('hash_range', ''),
                    "partition_num": _i(r, 'partition_num', 0),
                    "idc_list": idc_list,
                    "idc_display": ', '.join(idc_list),
                })
            if result['cluster_name'] == 'N/A' and sets:
                result['cluster_name'] = sets[0]['set_name']
            result['sets'] = sets

            # 4. show processlist → 网关会话与 proxy 节点
            plist_rows = proxy_rows("/*proxy*/show processlist") or []
            proxy_ok = proxy_ok or bool(plist_rows)
            endpoints = {}
            sessions = []
            for r in plist_rows:
                pip = str(r.get('proxy_ip_port', ''))
                node = _parse_node(pip)
                if node:
                    key = f"{node['ip']}:{node['port']}"
                    idc = _IDC_RE.findall(pip)
                    endpoints[key] = {
                        "endpoint": key,
                        "idc": idc[0] if idc else '',
                    }
                sessions.append({
                    "id": str(r.get('Id', '')),
                    "user": r.get('User', ''),
                    "host": r.get('Host', ''),
                    "db": r.get('db', ''),
                    "state": r.get('State', ''),
                    "time_ms": str(r.get('Time(ms)', '')),
                    "query": str(r.get('Query', ''))[:200],
                    "backend_info": str(r.get('backend_info', ''))[:300],
                })
            result['proxy_sessions'] = sessions[:50]
            result['proxy_session_count'] = len(plist_rows)
            result['proxy_endpoints'] = list(endpoints.values())

            # 5. groupshard 专有: shardkey 分布
            if result['mode'] == 'groupshard':
                sk_rows = proxy_rows("/*proxy*/show shardkey") or []
                shard_tables = []
                for r in sk_rows:
                    info = str(r.get('info', ''))
                    attrs = {}
                    for part in info.split(';'):
                        if ':' in part:
                            k, v = part.split(':', 1)
                            attrs[k.strip()] = v.strip()
                    shardkey = attrs.get('shardkey', '')
                    shard_tables.append({
                        "db_table": r.get('db_table', ''),
                        "shardkey": shardkey,
                        "table_type": '广播表' if shardkey == 'noshardkey_allset' else '分片表',
                        "auto_increment": attrs.get('auto_increment', ''),
                    })
                result['shard_tables'] = shard_tables

            # 6. 健康判定 (基于真实 set 状态)
            if proxy_ok:
                result['conn_type'] = 'proxy'
            if not sets:
                result['cluster_health'] = 'UNKNOWN' if not proxy_ok else 'DEGRADED'
                if proxy_ok:
                    result['cluster_issues'].append('未能从网关获取 set 路由信息')
            else:
                critical = False
                degraded = False
                for s in sets:
                    label = s['alias'] or s['set_name']
                    if not s['master_ip']:
                        critical = True
                        result['cluster_issues'].append(f"set {label} 无主节点")
                    if s['status'] not in ('', '0'):
                        degraded = True
                        result['cluster_issues'].append(
                            f"set {label} 状态异常({s['status']})"
                        )
                    if s['slave_count'] == 0:
                        result['cluster_notes'].append(
                            f"set {label} 无备节点 (单点风险)"
                        )
                if critical:
                    result['cluster_health'] = 'CRITICAL'
                elif degraded:
                    result['cluster_health'] = 'DEGRADED'
                else:
                    result['cluster_health'] = 'HEALTHY'
        finally:
            if state['conn'] is not None:
                try:
                    state['conn'].close()
                except Exception as e:
                    degrade.note('tdsql.proxy_conn_close', '网关连接关闭失败', e)

        return result
