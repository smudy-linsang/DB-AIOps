"""
巡检执行器 - Phase 5 P1-2
=========================

负责:
1. 执行单个/批量巡检项
2. 调用检测方法(内联实现 + 复用 checkers)
3. 结果持久化到 InspectionRun + InspectionFinding
4. 与上下文聚合器、影响评估、方案生成器对接

设计原则:
- 检测方法标准化 (async 友好,带超时)
- 失败隔离 (单项失败不影响整体)
- 并行/串行可配置
- 完整的快照记录

文件: monitor/inspection_executor.py
参考: PHASE5_DEVELOPMENT_DESIGN.md 第三部分 P1-2
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from django.db import close_old_connections, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class DetectionContext:
    """单个巡检项的检测上下文"""
    db_config: Any = None            # DatabaseConfig 实例
    db_connector: Any = None         # DBConnector 实例(已建立连接)
    instance_id: str = ""            # 数据库实例 ID
    db_type: str = ""                # oracle/mysql/pgsql/dm/gbase/tdsql
    item: Dict[str, Any] = field(default_factory=dict)  # 巡检项定义
    timeout_sec: int = 30            # 检测超时


@dataclass
class DetectionResult:
    """单次检测结果"""
    item_id: str = ""
    item_title: str = ""
    status: str = "ok"               # ok / warning / critical / error / skip
    findings: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    error: Optional[str] = None
    detection_method: str = ""
    auto_fixable: bool = False
    auto_fix_hint: Optional[str] = None
    confidence: float = 1.0          # 检测置信度 0~1
    severity: str = "info"           # info/warning/critical
    raw_data: Optional[Dict[str, Any]] = None


# ============================================================================
# 内联检测方法 - 通用
# ============================================================================

class GenericDetector:
    """通用检测方法集(不依赖具体数据库)"""

    @staticmethod
    def detect_monitoring_coverage(ctx: DetectionContext) -> DetectionResult:
        """检测监控覆盖率:配置项是否都已在采集"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="metric_presence"
        )
        try:
            from monitor.models import MetricDefinition
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                # 通用健康指标
                expected_metrics = ["cpu_usage", "memory_usage", "disk_usage",
                                    "session_count", "tps", "qps"]
                cur.execute("SELECT 1")
                present = len(MetricDefinition.objects.filter(
                    db_config=ctx.db_config, is_active=True
                ).values_list("metric_key", flat=True))
                coverage = min(100.0, present / max(len(expected_metrics), 1) * 100)
                result.metrics["coverage_pct"] = round(coverage, 1)
                result.metrics["active_metrics"] = present
                if coverage < 60:
                    result.status = "critical"
                    result.severity = "critical"
                    result.findings.append({
                        "type": "low_coverage",
                        "message": f"监控覆盖率仅 {coverage:.1f}%,建议补齐核心指标"
                    })
                elif coverage < 85:
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "low_coverage",
                        "message": f"监控覆盖率 {coverage:.1f}%,部分指标未采集"
                    })
                else:
                    result.status = "ok"
                result.summary = f"监控覆盖率 {coverage:.1f}% ({present} 项活跃)"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
            result.summary = f"覆盖率检测失败: {e}"
        return result

    @staticmethod
    def detect_alert_fatigue(ctx: DetectionContext) -> DetectionResult:
        """检测告警疲劳:近期告警风暴"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="alert_frequency_analysis"
        )
        try:
            from monitor.models import AlertLog
            since = timezone.now() - timedelta(hours=1)
            one_hour_count = AlertLog.objects.filter(
                db_config=ctx.db_config, fired_at__gte=since
            ).count()
            result.metrics["alerts_last_hour"] = one_hour_count
            if one_hour_count >= 50:
                result.status = "critical"
                result.severity = "critical"
                result.findings.append({
                    "type": "alert_storm",
                    "message": f"近 1 小时产生 {one_hour_count} 条告警,存在告警风暴"
                })
            elif one_hour_count >= 20:
                result.status = "warning"
                result.severity = "warning"
                result.findings.append({
                    "type": "alert_storm",
                    "message": f"近 1 小时告警 {one_hour_count} 条,建议优化告警阈值"
                })
            else:
                result.status = "ok"
            result.summary = f"近 1 小时告警 {one_hour_count} 条"
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_long_transactions(ctx: DetectionContext) -> DetectionResult:
        """检测长事务"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="long_transaction_scan"
        )
        sql = ""
        try:
            if ctx.db_type == "oracle":
                sql = """
                    SELECT s.sid, s.serial#, s.username, s.status,
                           s.logon_time, t.start_date,
                           EXTRACT(SECOND FROM (SYSTIMESTAMP - t.start_date)) +
                           EXTRACT(MINUTE FROM (SYSTIMESTAMP - t.start_date)) * 60 AS duration_sec,
                           s.sql_id
                    FROM v$transaction t
                    JOIN v$session s ON t.session_addr = s.saddr
                    WHERE SYSTIMESTAMP - t.start_date > INTERVAL '5' MINUTE
                    ORDER BY t.start_date
                """
            elif ctx.db_type == "mysql":
                sql = """
                    SELECT trx_id, trx_state, trx_started,
                           TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS duration_sec,
                           trx_mysql_thread_id
                    FROM information_schema.innodb_trx
                    WHERE TIMESTAMPDIFF(SECOND, trx_started, NOW()) > 300
                """
            elif ctx.db_type == "pgsql":
                sql = """
                    SELECT pid, datname, usename, state, xact_start,
                       EXTRACT(EPOCH FROM (now() - xact_start))::int AS duration_sec,
                       query
                    FROM pg_stat_activity
                    WHERE xact_start IS NOT NULL
                      AND now() - xact_start > INTERVAL '5 minutes'
                """
            elif ctx.db_type == "dm":
                sql = """
                    SELECT SESS_ID, SQL_TEXT, SF_GET_SESSION_SQL_TEXT(SESS_ID) AS SQL_TXT
                    FROM V$SESSIONS
                    WHERE STATE = 'ACTIVE' AND LAST_SEND_TIME < SYSDATE - 5/1440
                """
            else:
                result.status = "skip"
                result.summary = f"暂不支持 {ctx.db_type} 长事务检测"
                return result
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                cur.execute(sql)
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["long_txn_count"] = len(rows)
                if rows:
                    max_dur = max((r.get("duration_sec") or 0) for r in rows)
                    result.metrics["max_duration_sec"] = max_dur
                    if max_dur >= 3600:
                        result.status = "critical"
                        result.severity = "critical"
                    elif max_dur >= 1800:
                        result.status = "warning"
                        result.severity = "warning"
                    result.findings.append({
                        "type": "long_transaction",
                        "count": len(rows),
                        "max_duration_sec": max_dur,
                        "details": rows[:5],
                        "message": f"发现 {len(rows)} 个长事务,最长 {max_dur:.0f} 秒"
                    })
                result.summary = f"长事务 {len(rows)} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_connections_usage(ctx: DetectionContext) -> DetectionResult:
        """检测连接数使用率"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="connection_usage"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("SELECT count(*), value FROM v$parameter WHERE name='sessions' GROUP BY value")
                    row = cur.fetchone()
                    max_conn = int(row[1]) if row and row[1] else 0
                    cur.execute("SELECT count(*) FROM v$session")
                    cur_conn = cur.fetchone()[0]
                elif ctx.db_type == "mysql":
                    cur.execute("SHOW VARIABLES LIKE 'max_connections'")
                    row = cur.fetchone()
                    max_conn = int(row[1]) if row else 0
                    cur.execute("SHOW STATUS LIKE 'Threads_connected'")
                    row = cur.fetchone()
                    cur_conn = int(row[1]) if row else 0
                elif ctx.db_type == "pgsql":
                    cur.execute("SHOW max_connections")
                    max_conn = int(cur.fetchone()[0])
                    cur.execute("SELECT count(*) FROM pg_stat_activity")
                    cur_conn = cur.fetchone()[0]
                else:
                    result.status = "skip"
                    result.summary = f"暂不支持 {ctx.db_type}"
                    return result
                pct = (cur_conn / max_conn * 100) if max_conn else 0
                result.metrics["current"] = cur_conn
                result.metrics["max"] = max_conn
                result.metrics["usage_pct"] = round(pct, 1)
                if pct >= 90:
                    result.status = "critical"
                    result.severity = "critical"
                elif pct >= 75:
                    result.status = "warning"
                    result.severity = "warning"
                result.findings.append({
                    "type": "connection_usage",
                    "usage_pct": round(pct, 1),
                    "current": cur_conn,
                    "max": max_conn,
                    "message": f"连接使用率 {pct:.1f}% ({cur_conn}/{max_conn})"
                })
                result.summary = f"连接使用率 {pct:.1f}%"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_slow_query_burst(ctx: DetectionContext) -> DetectionResult:
        """检测慢查询爆发"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="slow_query_stats"
        )
        try:
            from monitor.models import MonitorLog
            since = timezone.now() - timedelta(hours=1)
            slow_count = MonitorLog.objects.filter(
                db_config=ctx.db_config,
                metric_key__icontains="slow_query",
                collected_at__gte=since
            ).count()
            result.metrics["slow_queries_last_hour"] = slow_count
            if slow_count >= 100:
                result.status = "critical"
                result.severity = "critical"
            elif slow_count >= 30:
                result.status = "warning"
                result.severity = "warning"
            if result.status != "ok":
                result.findings.append({
                    "type": "slow_query_burst",
                    "count": slow_count,
                    "message": f"近 1 小时慢查询 {slow_count} 次,需关注"
                })
            result.summary = f"近 1 小时慢查询 {slow_count} 次"
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_failed_logins(ctx: DetectionContext) -> DetectionResult:
        """检测异常登录失败"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="audit_log_scan"
        )
        try:
            from monitor.models import AuditLog
            since = timezone.now() - timedelta(hours=24)
            failed = AuditLog.objects.filter(
                db_config=ctx.db_config,
                action__icontains="login",
                result="failure",
                created_at__gte=since
            ).count()
            result.metrics["failed_logins_24h"] = failed
            if failed >= 50:
                result.status = "critical"
                result.severity = "critical"
                result.findings.append({
                    "type": "brute_force_suspect",
                    "count": failed,
                    "message": f"24h 登录失败 {failed} 次,可能存在暴力破解"
                })
            elif failed >= 20:
                result.status = "warning"
                result.severity = "warning"
                result.findings.append({
                    "type": "login_failure",
                    "count": failed,
                    "message": f"24h 登录失败 {failed} 次"
                })
            result.summary = f"24h 登录失败 {failed} 次"
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_backup_status(ctx: DetectionContext) -> DetectionResult:
        """检测备份状态"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="backup_record_check"
        )
        try:
            from monitor.models import MonitorLog
            last = MonitorLog.objects.filter(
                db_config=ctx.db_config,
                metric_key__icontains="backup"
            ).order_by("-collected_at").first()
            if not last:
                result.status = "warning"
                result.severity = "warning"
                result.findings.append({
                    "type": "no_backup_record",
                    "message": "未发现备份相关采集记录,需确认备份任务是否启用"
                })
                result.summary = "无备份记录"
                return result
            age_hours = (timezone.now() - last.collected_at).total_seconds() / 3600
            result.metrics["last_backup_age_hours"] = round(age_hours, 1)
            if age_hours > 48:
                result.status = "critical"
                result.severity = "critical"
            elif age_hours > 24:
                result.status = "warning"
                result.severity = "warning"
            result.findings.append({
                "type": "backup_age",
                "age_hours": round(age_hours, 1),
                "last_at": last.collected_at.isoformat(),
                "message": f"距离最近备份 {age_hours:.1f} 小时"
            })
            result.summary = f"距最近备份 {age_hours:.1f}h"
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_replication_lag(ctx: DetectionContext) -> DetectionResult:
        """检测复制延迟(主从/ADG)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="replication_lag"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                lag_sec = None
                if ctx.db_type == "mysql":
                    cur.execute("SHOW SLAVE STATUS")
                    cols = [c[0].lower() for c in cur.description]
                    row = cur.fetchone()
                    if row:
                        d = dict(zip(cols, row))
                        lag_sec = d.get("seconds_behind_master")
                elif ctx.db_type == "pgsql":
                    cur.execute("SELECT EXTRACT(EPOCH FROM now() - replay_lag)::int FROM pg_stat_replication")
                    row = cur.fetchone()
                    if row:
                        lag_sec = row[0]
                elif ctx.db_type == "oracle":
                    cur.execute("SELECT DATUM_TIME, SCN_TO_TIMESTAMP(APPLIED_SCN) FROM V$STREAMS_APPLIED_SCN WHERE ROWNUM=1")
                if lag_sec is not None:
                    result.metrics["lag_sec"] = int(lag_sec)
                    if lag_sec >= 300:
                        result.status = "critical"
                        result.severity = "critical"
                    elif lag_sec >= 60:
                        result.status = "warning"
                        result.severity = "warning"
                    result.findings.append({
                        "type": "replication_lag",
                        "lag_sec": int(lag_sec),
                        "message": f"复制延迟 {lag_sec}s"
                    })
                    result.summary = f"复制延迟 {lag_sec}s"
                else:
                    result.summary = "无复制数据"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_disk_space(ctx: DetectionContext) -> DetectionResult:
        """检测磁盘空间"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="tablespace_disk_check"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT df.tablespace_name,
                               df.bytes total,
                               nvl(fs.bytes, 0) used,
                               df.bytes - nvl(fs.bytes, 0) free
                        FROM (SELECT tablespace_name, SUM(bytes) bytes
                              FROM dba_data_files GROUP BY tablespace_name) df
                        LEFT JOIN (SELECT tablespace_name, SUM(bytes) bytes
                                   FROM dba_free_space GROUP BY tablespace_name) fs
                          ON df.tablespace_name = fs.tablespace_name
                    """)
                elif ctx.db_type == "mysql":
                    cur.execute("""
                        SELECT table_schema AS name,
                               SUM(data_length+index_length) AS used,
                               SUM(data_length+index_length) AS total
                        FROM information_schema.tables
                        GROUP BY table_schema
                    """)
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT spcname AS name, pg_tablespace_size(oid) AS used
                        FROM pg_tablespace
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                warnings = []
                for r in rows:
                    total = r.get("total") or 0
                    used = r.get("used") or 0
                    if total:
                        pct = used / total * 100
                        r["usage_pct"] = round(pct, 1)
                        if pct >= 90:
                            warnings.append(r)
                result.metrics["tablespaces"] = rows
                if warnings:
                    if any(w["usage_pct"] >= 95 for w in warnings):
                        result.status = "critical"
                        result.severity = "critical"
                    else:
                        result.status = "warning"
                        result.severity = "warning"
                    result.findings.append({
                        "type": "tablespace_high",
                        "count": len(warnings),
                        "details": warnings,
                        "message": f"{len(warnings)} 个表空间使用率 ≥ 90%"
                    })
                result.summary = f"表空间告警 {len(warnings)} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_table_fragmentation(ctx: DetectionContext) -> DetectionResult:
        """检测表碎片"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="table_fragmentation"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "mysql":
                    cur.execute("""
                        SELECT table_name, data_free
                        FROM information_schema.tables
                        WHERE data_free > 10 * 1024 * 1024
                        ORDER BY data_free DESC LIMIT 20
                    """)
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT schemaname||'.'||relname AS tblname,
                               pg_relation_size(c.oid) AS size
                        FROM pg_stat_user_tables t
                        JOIN pg_class c ON c.relname=t.relname
                        WHERE pg_relation_size(c.oid) > 10*1024*1024
                        ORDER BY size DESC LIMIT 20
                    """)
                elif ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT owner, table_name, num_rows, blocks, empty_blocks
                        FROM dba_tables
                        WHERE blocks > 100 AND empty_blocks/blocks > 0.3
                        ORDER BY empty_blocks DESC FETCH FIRST 20 ROWS ONLY
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["fragmented_tables"] = rows
                if rows:
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "table_fragmentation",
                        "count": len(rows),
                        "message": f"发现 {len(rows)} 张碎片较多表"
                    })
                result.summary = f"碎片表 {len(rows)} 张"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_index_usage(ctx: DetectionContext) -> DetectionResult:
        """检测未使用索引"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="index_usage_scan"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "mysql":
                    cur.execute("""
                        SELECT object_schema, object_name, index_name
                        FROM performance_schema.table_io_waits_summary_by_index_usage
                        WHERE index_name IS NOT NULL
                          AND count_star = 0
                          AND object_schema NOT IN ('mysql','sys','performance_schema')
                        LIMIT 20
                    """)
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT schemaname||'.'||relname AS tblname, indexrelname AS idxname
                        FROM pg_stat_user_indexes
                        WHERE idx_scan = 0
                        LIMIT 20
                    """)
                elif ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT owner, object_name, index_name
                        FROM dba_indexes
                        WHERE owner NOT IN ('SYS','SYSTEM')
                          AND last_analyzed IS NOT NULL
                        MINUS
                        SELECT owner, table_name, index_name
                        FROM dba_hist_sql_plan
                        WHERE timestamp > SYSDATE - 30
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["unused_indexes"] = rows
                if rows:
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "unused_index",
                        "count": len(rows),
                        "details": rows[:5],
                        "message": f"发现 {len(rows)} 个未使用索引"
                    })
                result.summary = f"未使用索引 {len(rows)} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_stale_stats(ctx: DetectionContext) -> DetectionResult:
        """检测统计信息过期"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="stale_statistics"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT owner, table_name, last_analyzed, stale_stats
                        FROM dba_tab_statistics
                        WHERE last_analyzed < SYSDATE - 7
                          AND owner NOT IN ('SYS','SYSTEM')
                          AND num_rows > 1000
                        ORDER BY last_analyzed NULLS FIRST
                        FETCH FIRST 20 ROWS ONLY
                    """)
                elif ctx.db_type == "mysql":
                    cur.execute("""
                        SELECT table_schema, table_name,
                               update_time, table_rows
                        FROM information_schema.tables
                        WHERE update_time < NOW() - INTERVAL 7 DAY
                          AND table_rows > 1000
                        LIMIT 20
                    """)
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT schemaname||'.'||relname AS tblname,
                               last_analyze, last_autoanalyze
                        FROM pg_stat_user_tables
                        WHERE (last_analyze IS NULL OR last_analyze < now() - INTERVAL '7 days')
                          AND (n_live_tup > 1000)
                        LIMIT 20
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["stale_stats_tables"] = rows
                if rows:
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "stale_stats",
                        "count": len(rows),
                        "message": f"发现 {len(rows)} 张表统计信息过期"
                    })
                result.summary = f"过期统计 {len(rows)} 张"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_invalid_objects(ctx: DetectionContext) -> DetectionResult:
        """检测无效对象"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="invalid_object_count"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT owner, object_type, COUNT(*) cnt
                        FROM dba_objects
                        WHERE status = 'INVALID'
                        GROUP BY owner, object_type
                        ORDER BY cnt DESC
                    """)
                elif ctx.db_type == "dm":
                    cur.execute("""
                        SELECT owner, object_type, COUNT(*) cnt
                        FROM all_objects
                        WHERE status = 'INVALID'
                        GROUP BY owner, object_type
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                total = sum(r.get("cnt", 0) for r in rows)
                result.metrics["invalid_objects"] = rows
                if total > 50:
                    result.status = "critical"
                    result.severity = "critical"
                elif total > 0:
                    result.status = "warning"
                    result.severity = "warning"
                if total:
                    result.findings.append({
                        "type": "invalid_object",
                        "count": total,
                        "details": rows[:5],
                        "message": f"发现 {total} 个无效对象"
                    })
                result.summary = f"无效对象 {total} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_recyclebin(ctx: DetectionContext) -> DetectionResult:
        """检测回收站(Oracle)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="recyclebin_scan"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT owner, original_name, type, space
                        FROM dba_recyclebin
                        ORDER BY space DESC
                        FETCH FIRST 20 ROWS ONLY
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["recyclebin"] = rows
                if len(rows) > 10:
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "recyclebin_large",
                        "count": len(rows),
                        "message": f"回收站有 {len(rows)} 个对象,建议清理"
                    })
                result.summary = f"回收站 {len(rows)} 项"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_scn_headroom(ctx: DetectionContext) -> DetectionResult:
        """检测 SCN Headroom(Oracle 关键指标)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="scn_headroom"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT current_scn,
                               (SCN_TO_TIMESTAMP(MAX(SCN_WRAP*4294967296+SCN_BASIS))
                                - SYSTIMESTAMP) * 86400 AS headroom_sec
                        FROM v$database, v$thread
                    """)
                else:
                    result.status = "skip"
                    return result
                row = cur.fetchone()
                if row:
                    headroom_sec = row[1] or 0
                    result.metrics["headroom_sec"] = int(headroom_sec)
                    if headroom_sec < 86400:
                        result.status = "critical"
                        result.severity = "critical"
                        result.findings.append({
                            "type": "scn_headroom_low",
                            "value": int(headroom_sec),
                            "message": f"SCN Headroom 仅 {headroom_sec/3600:.1f} 小时"
                        })
                    elif headroom_sec < 86400 * 7:
                        result.status = "warning"
                        result.severity = "warning"
                    result.summary = f"SCN Headroom {headroom_sec/3600:.1f}h"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_auto_task_status(ctx: DetectionContext) -> DetectionResult:
        """检测 Oracle 自动任务状态"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="auto_task_status"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT client_name, status, last_good_date
                        FROM dba_autotask_client
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["auto_tasks"] = rows
                disabled = [r for r in rows if r.get("status") == "DISABLED"]
                if disabled:
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "auto_task_disabled",
                        "tasks": [r.get("client_name") for r in disabled],
                        "message": f"{len(disabled)} 个自动任务未启用"
                    })
                result.summary = f"自动任务 {len(rows)} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_log_switch_freq(ctx: DetectionContext) -> DetectionResult:
        """检测日志切换频率"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="log_switch_frequency"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT to_char(first_time, 'YYYY-MM-DD HH24') AS hour_slot,
                               COUNT(*) AS switches
                        FROM v$log_history
                        WHERE first_time > SYSDATE - 1
                        GROUP BY to_char(first_time, 'YYYY-MM-DD HH24')
                        ORDER BY hour_slot
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["log_switches"] = rows
                if rows:
                    avg = sum(r.get("switches", 0) for r in rows) / len(rows)
                    result.metrics["avg_per_hour"] = round(avg, 1)
                    if avg > 20:
                        result.status = "warning"
                        result.severity = "warning"
                        result.findings.append({
                            "type": "log_switch_frequent",
                            "avg_per_hour": round(avg, 1),
                            "message": f"日志切换频繁 {avg:.1f} 次/小时,可能影响性能"
                        })
                result.summary = f"日志切换 {len(rows)} 个时段"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_sparse_table(ctx: DetectionContext) -> DetectionResult:
        """检测稀疏表(高水位线)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="sparse_table_scan"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT owner, table_name,
                               blocks, empty_blocks,
                               ROUND(empty_blocks/GREATEST(blocks,1)*100, 1) AS sparse_pct
                        FROM dba_tables
                        WHERE blocks > 100
                          AND empty_blocks/blocks > 0.5
                        ORDER BY empty_blocks DESC
                        FETCH FIRST 20 ROWS ONLY
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["sparse_tables"] = rows
                if rows:
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "sparse_table",
                        "count": len(rows),
                        "message": f"发现 {len(rows)} 张稀疏表,可考虑表压缩或重组"
                    })
                result.summary = f"稀疏表 {len(rows)} 张"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_index_health(ctx: DetectionContext) -> DetectionResult:
        """检测索引健康度(高 BLEVEL/聚簇因子)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="index_blevel_check"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT owner, index_name, blevel, leaf_blocks, clustering_factor
                        FROM dba_indexes
                        WHERE blevel >= 4
                           OR clustering_factor > 10000
                        ORDER BY blevel DESC, clustering_factor DESC
                        FETCH FIRST 20 ROWS ONLY
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["unhealthy_indexes"] = rows
                if rows:
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "index_unhealthy",
                        "count": len(rows),
                        "message": f"{len(rows)} 个索引 BLEVEL 过高或聚簇因子差"
                    })
                result.summary = f"不健康索引 {len(rows)} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_fk_no_index(ctx: DetectionContext) -> DetectionResult:
        """检测外键无索引"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="fk_no_index_scan"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT ac.owner, ac.table_name, ac.constraint_name, ac.column_name
                        FROM all_cons_columns ac
                        JOIN all_constraints co
                          ON ac.owner = co.owner AND ac.constraint_name = co.constraint_name
                        WHERE co.constraint_type = 'R'
                          AND ac.owner NOT IN ('SYS','SYSTEM')
                          AND NOT EXISTS (
                              SELECT 1 FROM all_ind_columns ic
                              WHERE ic.table_owner = ac.owner
                                AND ic.table_name = ac.table_name
                                AND ic.column_name = ac.column_name
                          )
                        FETCH FIRST 20 ROWS ONLY
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["fk_no_index"] = rows
                if rows:
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "fk_no_index",
                        "count": len(rows),
                        "message": f"{len(rows)} 个外键无索引,可能引发锁竞争"
                    })
                result.summary = f"外键无索引 {len(rows)} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_high_concurrency_tables(ctx: DetectionContext) -> DetectionResult:
        """检测高并发表"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="high_concurrency_table"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "mysql":
                    cur.execute("""
                        SELECT object_schema, object_name, count_star
                        FROM performance_schema.table_io_waits_summary_by_table
                        WHERE object_schema NOT IN ('mysql','sys','performance_schema')
                        ORDER BY count_star DESC LIMIT 10
                    """)
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT schemaname||'.'||relname AS tblname,
                               n_tup_ins+n_tup_upd+n_tup_del AS writes
                        FROM pg_stat_user_tables
                        ORDER BY writes DESC LIMIT 10
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["hot_tables"] = rows
                if rows:
                    result.status = "ok"
                    result.findings.append({
                        "type": "hot_tables",
                        "details": rows[:5],
                        "message": f"识别出 {len(rows)} 张高并发表"
                    })
                result.summary = f"高并发表 {len(rows)} 张"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_sequence_usage(ctx: DetectionContext) -> DetectionResult:
        """检测序列使用率"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="sequence_usage_check"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT sequence_owner, sequence_name,
                               last_number, increment_by
                        FROM dba_sequences
                        WHERE sequence_owner NOT IN ('SYS','SYSTEM')
                    """)
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT schemaname||'.'||sequencename AS seqname,
                               last_value, increment_by
                        FROM pg_sequences
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["sequences"] = len(rows)
                result.summary = f"序列 {len(rows)} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_awr_config(ctx: DetectionContext) -> DetectionResult:
        """检测 AWR 快照配置"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="awr_config_check"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT dbid, retention, topnsql, interval_seconds
                        FROM dba_hist_wr_control
                    """)
                else:
                    result.status = "skip"
                    return result
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["awr_config"] = rows
                if rows:
                    retention = rows[0].get("retention", 0)
                    interval = rows[0].get("interval_seconds", 0)
                    if retention < 7 * 24 * 3600:
                        result.status = "warning"
                        result.severity = "warning"
                        result.findings.append({
                            "type": "awr_retention_low",
                            "retention_days": retention / 86400,
                            "message": f"AWR 保留仅 {retention/86400:.1f} 天,建议 7+ 天"
                        })
                    result.summary = f"AWR 保留 {retention/86400:.1f} 天,间隔 {interval/60:.0f}min"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result


# ============================================================================
# 检测方法注册表
# ============================================================================

DETECTOR_REGISTRY: Dict[str, Callable] = {
    # COMMON
    "I001": GenericDetector.detect_monitoring_coverage,
    "I002": GenericDetector.detect_alert_fatigue,
    "I003": GenericDetector.detect_long_transactions,
    "I004": GenericDetector.detect_connections_usage,
    "I005": GenericDetector.detect_slow_query_burst,
    "I006": GenericDetector.detect_failed_logins,
    "I007": GenericDetector.detect_backup_status,
    "I008": GenericDetector.detect_replication_lag,
    "I009": GenericDetector.detect_disk_space,
    "I010": GenericDetector.detect_table_fragmentation,
    "I011": GenericDetector.detect_index_usage,
    "I012": GenericDetector.detect_stale_stats,
    # ORACLE 专项
    "I101": GenericDetector.detect_invalid_objects,
    "I102": GenericDetector.detect_recyclebin,
    "I103": GenericDetector.detect_scn_headroom,
    "I104": GenericDetector.detect_auto_task_status,
    "I105": GenericDetector.detect_log_switch_freq,
    "I106": GenericDetector.detect_sparse_table,
    "I107": GenericDetector.detect_index_health,
    "I108": GenericDetector.detect_fk_no_index,
    "I109": GenericDetector.detect_high_concurrency_tables,
    "I110": GenericDetector.detect_sequence_usage,
    "I111": GenericDetector.detect_awr_config,
}


# ============================================================================
# 巡检执行器
# ============================================================================

class InspectionExecutor:
    """巡检执行器

    用法:
        executor = InspectionExecutor()
        run = executor.run_for_db(db_config, level="daily")
    """

    DEFAULT_TIMEOUT = 30
    MAX_WORKERS = 4

    def __init__(self, max_workers: int = MAX_WORKERS, default_timeout: int = DEFAULT_TIMEOUT):
        self.max_workers = max_workers
        self.default_timeout = default_timeout
        self._logger = logging.getLogger("monitor.inspection_executor")

    def run_for_db(self, db_config, level: str = "daily",
                   item_ids: Optional[List[str]] = None) -> str:
        """针对单个数据库执行一次巡检

        参数:
            db_config: DatabaseConfig 实例
            level: 巡检级别 daily/weekly/monthly
            item_ids: 指定要执行的项;None 表示按 level 选所有

        返回:
            run_id (InspectionRun.run_id)
        """
        from monitor.models import InspectionItem as ItemModel, InspectionRun, InspectionFinding
        from monitor.inspection_registry import ALL_ITEMS

        # 1) 选出要执行的项
        if item_ids:
            items = [it for it in ALL_ITEMS if it.get("item_id") in item_ids]
        else:
            items = [it for it in ALL_ITEMS
                     if it.get("level") == level
                     and db_config.db_type in it.get("applicable_db_types", [])]

        # 2) 同步 ItemModel(把内存定义持久化,方便查询)
        self._sync_items_to_db(items)

        # 3) 创建 Run
        run = InspectionRun.objects.create(
            run_id=f"INSP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{db_config.id}",
            level=level,
            db_config=db_config,
            status="running",
            started_at=timezone.now(),
            total_items=len(items),
        )

        # 4) 准备检测上下文
        results: List[DetectionResult] = []
        start_ts = time.time()
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {
                    pool.submit(self._run_single, db_config, item, level): item
                    for item in items
                }
                for fut in as_completed(futures):
                    try:
                        r = fut.result(timeout=self.default_timeout + 5)
                    except Exception as e:
                        item = futures[fut]
                        r = DetectionResult(
                            item_id=item.get("item_id", ""),
                            item_title=item.get("title", ""),
                            status="error",
                            error=f"执行异常: {e}",
                        )
                    results.append(r)
        except Exception as e:
            self._logger.exception("巡检执行异常: %s", e)

        # 5) 写 Finding + 统计
        crit = warn = ok = err = 0
        for r in results:
            InspectionFinding.objects.create(
                finding_id=f"FD-{run.run_id}-{r.item_id}",
                run=run,
                item_code=r.item_id,
                item_title=r.item_title,
                status=r.status,
                severity=r.severity,
                summary=r.summary,
                details={
                    "findings": r.findings,
                    "metrics": r.metrics,
                    "error": r.error,
                },
                detection_method=r.detection_method,
                duration_ms=r.duration_ms,
                confidence=r.confidence,
            )
            if r.status == "critical":
                crit += 1
            elif r.status == "warning":
                warn += 1
            elif r.status == "ok":
                ok += 1
            else:
                err += 1

        # 6) 收尾 Run
        run.status = "completed"
        run.completed_at = timezone.now()
        run.critical_count = crit
        run.warning_count = warn
        run.ok_count = ok
        run.error_count = err
        run.duration_sec = round(time.time() - start_ts, 1)
        run.health_score = self._calc_health_score(ok, warn, crit, err)
        run.save()

        return run.run_id

    def run_for_all_dbs(self, level: str = "daily") -> List[str]:
        """对所有启用的数据库执行巡检"""
        from monitor.models import DatabaseConfig
        run_ids = []
        dbs = DatabaseConfig.objects.filter(is_active=True)
        for db in dbs:
            try:
                rid = self.run_for_db(db, level=level)
                run_ids.append(rid)
            except Exception as e:
                self._logger.error("数据库 %s 巡检失败: %s", db, e)
            finally:
                close_old_connections()
        return run_ids

    def _run_single(self, db_config, item: Dict[str, Any], level: str) -> DetectionResult:
        """执行单个巡检项"""
        item_id = item.get("item_id", "")
        method = DETECTOR_REGISTRY.get(item_id)
        if not method:
            r = DetectionResult(
                item_id=item_id,
                item_title=item.get("title", ""),
                status="skip",
                summary="未实现检测方法",
            )
            return r
        ctx = DetectionContext(
            db_config=db_config,
            instance_id=str(db_config.id),
            db_type=db_config.db_type,
            item=item,
            timeout_sec=self.default_timeout,
        )
        start = time.time()
        try:
            r = method(ctx)
        except Exception as e:
            r = DetectionResult(
                item_id=item_id,
                item_title=item.get("title", ""),
                status="error",
                error=f"{type(e).__name__}: {e}",
                summary=f"检测失败: {e}",
            )
        r.duration_ms = int((time.time() - start) * 1000)
        r.detection_method = method.__name__
        r.auto_fixable = item.get("auto_fixable", False)
        r.auto_fix_hint = item.get("auto_fix_method")
        return r

    @staticmethod
    def _calc_health_score(ok: int, warn: int, crit: int, err: int) -> float:
        """计算健康分(0-100)"""
        total = max(ok + warn + crit + err, 1)
        score = (ok * 1.0 + warn * 0.6 + crit * 0.2 + err * 0.0) / total * 100
        return round(score, 1)

    @staticmethod
    def _sync_items_to_db(items: List[Dict[str, Any]]):
        """把内存中的巡检项定义同步到 DB"""
        from monitor.models import InspectionItem as ItemModel
        for it in items:
            obj, created = ItemModel.objects.update_or_create(
                item_code=it.get("item_id"),
                defaults={
                    "title": it.get("title", ""),
                    "category": it.get("category", ""),
                    "level": it.get("level", "daily"),
                    "severity": it.get("severity", "info"),
                    "applicable_db_types": it.get("applicable_db_types", []),
                    "detect_method": it.get("detect_method", ""),
                    "threshold": it.get("threshold", {}),
                    "recommendation": it.get("recommendation", ""),
                    "auto_fixable": it.get("auto_fixable", False),
                    "auto_fix_method": it.get("auto_fix_method", ""),
                },
            )


# ============================================================================
# 便捷函数
# ============================================================================

def run_inspection(db_config, level: str = "daily") -> str:
    """对指定数据库执行一次巡检"""
    return InspectionExecutor().run_for_db(db_config, level)


def run_inspection_all(level: str = "daily") -> List[str]:
    """对所有数据库执行一次巡检"""
    return InspectionExecutor().run_for_all_dbs(level)


def get_detector_count() -> int:
    """返回已注册的检测方法数量"""
    return len(DETECTOR_REGISTRY)
