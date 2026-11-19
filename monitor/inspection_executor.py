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
import uuid
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
            elif ctx.db_type in ("mysql", "tdsql"):
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
            from django.db.models import Q
            since = timezone.now() - timedelta(hours=24)
            # 兼容 AuditLog 字段：关联配置字段为 config，时间为 create_time
            failed = AuditLog.objects.filter(
                config=ctx.db_config,
                status="failed",
                create_time__gte=since
            ).filter(
                Q(action_type__icontains="login") | Q(description__icontains="login")
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
            # MonitorLog 字段为 config, message, create_time
            last = MonitorLog.objects.filter(
                config=ctx.db_config,
                message__icontains="backup"
            ).order_by("-create_time").first()
            if not last:
                result.status = "warning"
                result.severity = "warning"
                result.findings.append({
                    "type": "no_backup_record",
                    "message": "未发现备份相关采集记录,需确认备份任务是否启用"
                })
                result.summary = "无备份记录"
                return result
            age_hours = (timezone.now() - last.create_time).total_seconds() / 3600
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
                "last_at": last.create_time.isoformat(),
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
        if ctx.db_type not in ("oracle", "mysql", "tdsql", "pgsql"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle, mysql, tdsql, pgsql)"
            return result
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
                elif ctx.db_type in ("mysql", "tdsql"):
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
        if ctx.db_type not in ("oracle", "dm"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle, dm)"
            return result
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
                    result.summary = f"不适用当前方言: {ctx.db_type}"
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
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle)"
            return result
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
                    result.summary = f"不适用当前方言: {ctx.db_type}"
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
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle)"
            return result
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
                    result.summary = f"不适用当前方言: {ctx.db_type}"
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
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle)"
            return result
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
                    result.summary = f"不适用当前方言: {ctx.db_type}"
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


    @staticmethod
    def _degrade_note(scope: str, reason: str, exc: Optional[Exception] = None) -> None:
        """按 monitor.degrade 分级留痕，禁止静默吞异常。"""
        from monitor.degrade import note as degrade_note
        degrade_note(scope, reason=reason, exc=exc)

    @staticmethod
    def _row_values(row: Any) -> tuple:
        """兼容 tuple/pyodbc.Row 与 pymysql DictCursor dict，按列顺序返回值元组。"""
        if row is None:
            return ()
        if isinstance(row, dict):
            return tuple(row.values())
        return tuple(row)

    @staticmethod
    def _row_col(row: Any, index: int = 0, *names: str) -> Any:
        """按列名（大小写不敏感）或列位置读取单行字段。"""
        if row is None:
            return None
        if isinstance(row, dict):
            lower_map = {str(k).lower(): v for k, v in row.items()}
            for name in names:
                if name in row:
                    return row[name]
                lname = str(name).lower()
                if lname in lower_map:
                    return lower_map[lname]
            vals = list(row.values())
            return vals[index] if 0 <= index < len(vals) else None
        vals = tuple(row)
        return vals[index] if 0 <= index < len(vals) else None

    @staticmethod
    def _scalar(cur: Any, default: Any = None, index: int = 0, *names: str) -> Any:
        """读取单行标量结果，兼容 tuple 与 DictCursor。"""
        row = cur.fetchone()
        if row is None:
            return default
        val = GenericDetector._row_col(row, index, *names)
        return default if val is None else val

    @staticmethod
    def _show_var_value(cur: Any) -> Any:
        """读取 SHOW GLOBAL STATUS / SHOW VARIABLES / SHOW <param> 单行值。"""
        row = cur.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            for key in ("Value", "value"):
                if key in row:
                    return row[key]
            vals = list(row.values())
            return vals[1] if len(vals) >= 2 else (vals[0] if vals else None)
        vals = tuple(row)
        return vals[1] if len(vals) >= 2 else (vals[0] if vals else None)

    @staticmethod
    def _show_vars_map(cur: Any) -> Dict[str, Any]:
        """读取多行 SHOW GLOBAL STATUS / VARIABLES 到 {Variable_name: Value} 映射。"""
        mapping: Dict[str, Any] = {}
        for r in (cur.fetchall() or []):
            k = GenericDetector._row_col(r, 0, "Variable_name", "variable_name")
            v = GenericDetector._row_col(r, 1, "Value", "value")
            if k is not None:
                mapping[str(k)] = v
        return mapping

    @staticmethod
    def _rows_to_dicts(cur: Any, rows: Any) -> List[Dict[str, Any]]:
        """将结果集统一转为小写键 dict 列表，避免 DictCursor 下 zip(cols, dict) 错配。"""
        if not rows:
            return []
        cols = [str(c[0]).lower() for c in (getattr(cur, "description", None) or [])]
        out: List[Dict[str, Any]] = []
        for r in rows:
            if isinstance(r, dict):
                out.append({str(k).lower(): v for k, v in r.items()})
            else:
                vals = GenericDetector._row_values(r)
                if cols:
                    out.append(dict(zip(cols, vals)))
                else:
                    out.append({f"col_{i}": v for i, v in enumerate(vals)})
        return out

    @staticmethod
    def _mysql_major_version(cur: Any) -> int:
        """查询 MySQL/MariaDB/TDSQL 主版本号，未识别时返回 0。"""
        cur.execute("SELECT VERSION()")
        ver_str = str(GenericDetector._scalar(cur, "") or "").strip()
        if not ver_str:
            return 0
        head = ver_str.split(".", 1)[0]
        digits = "".join(ch for ch in head if ch.isdigit())
        return int(digits) if digits else 0

    @staticmethod
    def _extract_capacity_point(msg_raw: str, db_type: str) -> Optional[Dict[str, float]]:
        """从 MonitorLog.message JSON 中提取总已用容量(MB)与表空间峰值使用率(%)。"""
        try:
            msg = json.loads(msg_raw) if isinstance(msg_raw, str) else msg_raw
        except Exception:
            return None
        if not isinstance(msg, dict):
            return None

        db_sizes = msg.get("database_sizes")
        tablespaces = msg.get("tablespaces")
        used_mb: Optional[float] = None
        total_mb: Optional[float] = None
        max_ts_pct: Optional[float] = None

        if isinstance(tablespaces, list) and tablespaces:
            ts_pcts = [
                float(t["used_pct"])
                for t in tablespaces
                if isinstance(t, dict) and t.get("used_pct") is not None
            ]
            if ts_pcts:
                max_ts_pct = max(ts_pcts)
            if db_type in ("dm", "oracle") or not (isinstance(db_sizes, list) and db_sizes):
                ts_used = [
                    float(t["used_mb"])
                    for t in tablespaces
                    if isinstance(t, dict) and t.get("used_mb") is not None
                ]
                ts_tot = [
                    float(t["total_mb"])
                    for t in tablespaces
                    if isinstance(t, dict) and t.get("total_mb") is not None
                ]
                if ts_used:
                    used_mb = sum(ts_used)
                if ts_tot:
                    total_mb = sum(ts_tot)

        if isinstance(db_sizes, list) and db_sizes and (used_mb is None or db_type not in ("dm", "oracle")):
            sz = [
                float(d["size_mb"])
                for d in db_sizes
                if isinstance(d, dict) and d.get("size_mb") is not None
            ]
            if sz:
                used_mb = sum(sz)

        if used_mb is None:
            return None
        point: Dict[str, float] = {"used_mb": round(used_mb, 2)}
        if total_mb is not None and total_mb > 0:
            point["total_mb"] = round(total_mb, 2)
        if max_ts_pct is not None:
            point["max_ts_pct"] = round(max_ts_pct, 2)
        return point

    @staticmethod
    def _evaluate_capacity_prediction(
        ctx: DetectionContext,
        *,
        item_id: str,
        item_title: str,
        method_name: str,
        lookback_days: int,
        min_samples: int,
        horizon_days: int,
    ) -> DetectionResult:
        """基于 status='UP' 的历史 MonitorLog 计算容量线性增长斜率与未来预测。"""
        result = DetectionResult(
            item_id=item_id,
            item_title=item_title,
            detection_method=method_name,
        )
        cfg = ctx.db_config
        cfg_pk = getattr(cfg, "pk", None) if cfg is not None else None
        if not cfg_pk:
            result.status = "skip"
            result.summary = "未关联已纳管 DatabaseConfig 实例（如平台元数据库），跳过历史容量预测"
            return result

        try:
            from monitor.models import MonitorLog

            since = timezone.now() - timedelta(days=lookback_days)
            base_qs = MonitorLog.objects.filter(config_id=cfg_pk, create_time__gte=since)
            up_qs = base_qs.filter(status="UP").order_by("create_time")
            total_all = base_qs.count()
            up_count = up_qs.count()
            non_up_count = max(0, total_all - up_count)

            result.metrics["samples_count"] = up_count
            result.metrics["total_samples_in_window"] = total_all
            result.metrics["non_up_samples_excluded"] = non_up_count
            result.metrics["lookback_days"] = lookback_days
            result.metrics["horizon_days"] = horizon_days

            if up_count < min_samples:
                result.status = "skip"
                result.summary = (
                    f"有效 UP 时序采样仅 {up_count} 点（不足 {min_samples} 点，"
                    f"已过滤非 UP 记录 {non_up_count} 条），暂无法预测 {horizon_days} 天容量"
                )
                return result

            id_times = list(up_qs.values_list("id", "create_time"))
            n = len(id_times)
            if n <= 25:
                picked_ids = [row_id for row_id, _ in id_times]
            else:
                step = (n - 1) / 24.0
                indices = sorted({int(round(i * step)) for i in range(25)} | {0, n - 1})
                picked_ids = [id_times[idx][0] for idx in indices]

            sampled_rows = list(
                MonitorLog.objects.filter(id__in=picked_ids)
                .order_by("create_time")
                .values_list("create_time", "message")
            )

            points: List[Tuple[float, float]] = []
            latest_meta: Dict[str, float] = {}
            t0 = sampled_rows[0][0]
            for ts, msg_raw in sampled_rows:
                pt = GenericDetector._extract_capacity_point(msg_raw, ctx.db_type)
                if pt is not None:
                    day_x = (ts - t0).total_seconds() / 86400.0
                    points.append((day_x, pt["used_mb"]))
                    latest_meta = pt

            if len(points) < 2:
                result.status = "skip"
                result.summary = f"UP 采样 {up_count} 点中缺少有效容量指标字段，暂无法计算 {horizon_days} 天趋势"
                return result

            span_days = points[-1][0] - points[0][0]
            current_mb = points[-1][1]
            result.metrics["analyzed_points"] = len(points)
            result.metrics["span_days"] = round(span_days, 2)
            result.metrics["current_used_mb"] = current_mb
            if "total_mb" in latest_meta:
                result.metrics["current_total_mb"] = latest_meta["total_mb"]
            if "max_ts_pct" in latest_meta:
                result.metrics["max_tablespace_used_pct"] = latest_meta["max_ts_pct"]

            if span_days < 0.01:
                result.status = "skip"
                result.summary = (
                    f"有效 UP 采样 {up_count} 点时间跨度仅 {round(span_days * 24, 2)} 小时，"
                    f"不足以进行 {horizon_days} 天线性外推"
                )
                return result

            x_mean = sum(p[0] for p in points) / len(points)
            y_mean = sum(p[1] for p in points) / len(points)
            denom = sum((p[0] - x_mean) ** 2 for p in points)
            slope_mb_per_day = (
                sum((p[0] - x_mean) * (p[1] - y_mean) for p in points) / denom
                if denom > 0
                else 0.0
            )
            predicted_mb = round(max(0.0, current_mb + slope_mb_per_day * horizon_days), 2)
            delta_mb = round(predicted_mb - current_mb, 2)
            growth_pct = round((delta_mb / current_mb) * 100.0, 2) if current_mb > 0 else 0.0

            result.metrics["daily_growth_mb"] = round(slope_mb_per_day, 2)
            result.metrics["predicted_used_mb"] = predicted_mb
            result.metrics["predicted_growth_mb"] = delta_mb
            result.metrics["predicted_growth_pct"] = growth_pct

            predicted_ts_pct = None
            if latest_meta.get("total_mb"):
                predicted_ts_pct = round(predicted_mb / latest_meta["total_mb"] * 100.0, 1)
                result.metrics["predicted_total_used_pct"] = predicted_ts_pct

            is_critical = predicted_ts_pct is not None and predicted_ts_pct >= 95.0
            is_warning = (
                (predicted_ts_pct is not None and predicted_ts_pct >= 85.0)
                or (growth_pct >= 50.0 and delta_mb >= 512.0)
            )
            if is_critical:
                result.status = "critical"
                result.severity = "critical"
                result.findings.append({
                    "type": "capacity_forecast_critical",
                    "current_used_mb": current_mb,
                    "predicted_used_mb": predicted_mb,
                    "daily_growth_mb": round(slope_mb_per_day, 2),
                    "predicted_growth_pct": growth_pct,
                    "message": f"按近 {round(span_days, 1)} 天趋势预计 {horizon_days} 天后总表空间使用率将达 {predicted_ts_pct}%",
                })
            elif is_warning:
                result.status = "warning"
                result.severity = "warning"
                result.findings.append({
                    "type": "capacity_forecast_warning",
                    "current_used_mb": current_mb,
                    "predicted_used_mb": predicted_mb,
                    "daily_growth_mb": round(slope_mb_per_day, 2),
                    "predicted_growth_pct": growth_pct,
                    "message": (
                        f"容量增长较快：当前 {current_mb} MB，日均 {slope_mb_per_day:+.2f} MB/天，"
                        f"预计 {horizon_days} 天后达 {predicted_mb} MB ({growth_pct:+.1f}%)"
                    ),
                })

            result.summary = (
                f"{horizon_days}天容量预测(UP采样 {up_count} 点): 当前 {current_mb} MB，"
                f"日均 {slope_mb_per_day:+.2f} MB/天，预计{horizon_days}天后 {predicted_mb} MB ({growth_pct:+.1f}%)"
            )
        except Exception as e:
            GenericDetector._degrade_note(f"inspection.{method_name}", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result


# =========================================================================
    # DB-022 010B 补实现上半 23 项新检测器
    # =========================================================================

    @staticmethod
    def detect_locked_users(ctx: DetectionContext) -> DetectionResult:
        """检测被锁定的账号 (INS-COM-USER-LOCKED)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-USER-LOCKED"),
            item_title=ctx.item.get("title", "账号锁定检查"),
            detection_method="detect_locked_users"
        )
        if ctx.db_type == "pgsql":
            # PG 核心无原生 account_locked 字段，检查有效期限已过期的账号
            try:
                from monitor.db_connector import get_connector
                if not ctx.db_connector:
                    ctx.db_connector = get_connector(ctx.db_config)
                conn = ctx.db_connector.connect()
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT rolname FROM pg_roles WHERE rolvaliduntil IS NOT NULL AND rolvaliduntil < NOW()")
                    rows = [r[0] for r in cur.fetchall()]
                    result.metrics["expired_or_locked_users"] = rows
                    if rows:
                        result.status = "warning"
                        result.severity = "warning"
                        result.findings.append({
                            "type": "locked_users",
                            "users": rows,
                            "count": len(rows),
                            "message": f"发现 {len(rows)} 个已过期/受限账号: {', '.join(rows[:5])}"
                        })
                    result.summary = f"锁定/过期账号 {len(rows)} 个 (PG 采用有效期检查)"
                finally:
                    ctx.db_connector.close()
            except Exception as e:
                result.status = "error"
                result.error = str(e)
            return result

        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type in ("oracle", "dm"):
                    cur.execute("SELECT username, account_status FROM dba_users WHERE account_status LIKE '%LOCKED%'")
                    rows = [
                        {
                            "username": GenericDetector._row_col(r, 0, "username", "USERNAME"),
                            "status": GenericDetector._row_col(r, 1, "account_status", "ACCOUNT_STATUS"),
                        }
                        for r in (cur.fetchall() or [])
                    ]
                elif ctx.db_type in ("mysql", "tdsql"):
                    cur.execute("SELECT user, host, account_locked FROM mysql.user WHERE account_locked = 'Y'")
                    rows = [
                        {
                            "user": GenericDetector._row_col(r, 0, "user", "User"),
                            "host": GenericDetector._row_col(r, 1, "host", "Host"),
                            "status": "LOCKED",
                        }
                        for r in (cur.fetchall() or [])
                    ]
                elif ctx.db_type == "gbase":
                    cur.execute("SELECT TRIM(User) AS user, TRIM(Host) AS host, locked AS status FROM gbase.user_check WHERE locked = 'Y'")
                    rows = [
                        {
                            "user": GenericDetector._row_col(r, 0, "user", "User"),
                            "host": GenericDetector._row_col(r, 1, "host", "Host"),
                            "status": str(GenericDetector._row_col(r, 2, "status", "locked") or "LOCKED").strip(),
                        }
                        for r in (cur.fetchall() or [])
                    ]
                else:
                    result.status = "skip"
                    result.summary = f"不适用当前方言: {ctx.db_type}"
                    return result

                result.metrics["locked_users"] = rows
                cnt = len(rows)
                if cnt >= 10:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt:
                    result.findings.append({
                        "type": "locked_users",
                        "count": cnt,
                        "details": rows[:5],
                        "message": f"发现 {cnt} 个被锁定的账号"
                    })
                result.summary = f"锁定账号 {cnt} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_locked_users", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_expiring_passwords(ctx: DetectionContext) -> DetectionResult:
        """检测即将过期的密码 (INS-COM-PWD-EXPIRE)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-PWD-EXPIRE"),
            item_title=ctx.item.get("title", "密码即将过期"),
            detection_method="detect_expiring_passwords"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                if ctx.db_type in ("oracle", "dm"):
                    cur.execute("SELECT username, expiry_date FROM dba_users WHERE expiry_date IS NOT NULL AND expiry_date BETWEEN SYSDATE AND SYSDATE + 7")
                    rows = [
                        {
                            "user": GenericDetector._row_col(r, 0, "username", "USERNAME"),
                            "expiry": str(GenericDetector._row_col(r, 1, "expiry_date", "EXPIRY_DATE")),
                        }
                        for r in (cur.fetchall() or [])
                    ]
                elif ctx.db_type == "pgsql":
                    cur.execute("SELECT rolname, rolvaliduntil FROM pg_roles WHERE rolvaliduntil IS NOT NULL AND rolvaliduntil BETWEEN NOW() AND NOW() + INTERVAL '7 days'")
                    rows = [
                        {
                            "user": GenericDetector._row_col(r, 0, "rolname"),
                            "expiry": str(GenericDetector._row_col(r, 1, "rolvaliduntil")),
                        }
                        for r in (cur.fetchall() or [])
                    ]
                elif ctx.db_type in ("mysql", "tdsql"):
                    cur.execute("""
                        SELECT user, host, password_expired,
                               DATE_ADD(password_last_changed, INTERVAL COALESCE(NULLIF(password_lifetime, 0), @@default_password_lifetime) DAY) AS expiry_date
                        FROM mysql.user
                        WHERE password_expired = 'Y'
                           OR (COALESCE(password_lifetime, @@default_password_lifetime) > 0
                               AND password_last_changed IS NOT NULL
                               AND DATE_ADD(password_last_changed, INTERVAL COALESCE(NULLIF(password_lifetime, 0), @@default_password_lifetime) DAY) <= DATE_ADD(NOW(), INTERVAL 7 DAY))
                    """)
                    rows = [
                        {
                            "user": GenericDetector._row_col(r, 0, "user", "User"),
                            "host": GenericDetector._row_col(r, 1, "host", "Host"),
                            "password_expired": GenericDetector._row_col(r, 2, "password_expired"),
                            "expiry": str(GenericDetector._row_col(r, 3, "expiry_date") or ""),
                        }
                        for r in (cur.fetchall() or [])
                    ]
                elif ctx.db_type == "gbase":
                    cur.execute("""
                        SELECT TRIM(User) AS user, TRIM(Host) AS host, password_expired,
                               DATE_ADD(password_last_changed, INTERVAL COALESCE(NULLIF(password_life_time, 0), @@password_life_time) DAY) AS expiry_date
                        FROM gbase.user_check
                        WHERE password_expired = 'Y'
                           OR (COALESCE(NULLIF(password_life_time, 0), @@password_life_time) > 0
                               AND password_last_changed IS NOT NULL
                               AND DATE_ADD(password_last_changed, INTERVAL COALESCE(NULLIF(password_life_time, 0), @@password_life_time) DAY) <= DATE_ADD(NOW(), INTERVAL 7 DAY))
                    """)
                    rows = [
                        {
                            "user": GenericDetector._row_col(r, 0, "user", "User"),
                            "host": GenericDetector._row_col(r, 1, "host", "Host"),
                            "password_expired": GenericDetector._row_col(r, 2, "password_expired"),
                            "expiry": str(GenericDetector._row_col(r, 3, "expiry_date") or ""),
                        }
                        for r in (cur.fetchall() or [])
                    ]
                else:
                    result.status = "skip"
                    result.summary = f"不适用当前方言: {ctx.db_type}"
                    return result

                result.metrics["expiring_users"] = rows
                cnt = len(rows)
                if cnt >= 10:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt:
                    result.findings.append({
                        "type": "expiring_passwords",
                        "count": cnt,
                        "details": rows[:5],
                        "message": f"发现 {cnt} 个账号密码在 7 天内即将过期"
                    })
                result.summary = f"即将过期密码账号 {cnt} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_expiring_passwords", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_7d_capacity(ctx: DetectionContext) -> DetectionResult:
        """检测 7 天容量预测 (INS-COM-CAP-7D)"""
        return GenericDetector._evaluate_capacity_prediction(
            ctx,
            item_id=ctx.item.get("item_id", "INS-COM-CAP-7D"),
            item_title=ctx.item.get("title", "7天容量预测"),
            method_name="detect_7d_capacity",
            lookback_days=7,
            min_samples=3,
            horizon_days=7,
        )

    @staticmethod
    def detect_30d_capacity(ctx: DetectionContext) -> DetectionResult:
        """检测 30 天容量预测 (INS-COM-CAP-30D)"""
        return GenericDetector._evaluate_capacity_prediction(
            ctx,
            item_id=ctx.item.get("item_id", "INS-COM-CAP-30D"),
            item_title=ctx.item.get("title", "30天容量预测"),
            method_name="detect_30d_capacity",
            lookback_days=14,
            min_samples=5,
            horizon_days=30,
        )

    @staticmethod
    def detect_deadlock_count(ctx: DetectionContext) -> DetectionResult:
        """检测 24h 内死锁发生次数 (INS-COM-DEADLOCK-24H)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-DEADLOCK-24H"),
            item_title=ctx.item.get("title", "24h 死锁次数"),
            detection_method="detect_deadlock_count"
        )
        if ctx.db_type == "gbase":
            result.status = "skip"
            result.summary = "不适用当前方言: gbase 使用 EXPRESS 引擎，无 InnoDB 死锁统计 (能力边界 skip)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                deadlock_cnt = 0
                if ctx.db_type == "pgsql":
                    cur.execute("SELECT COALESCE(SUM(deadlocks), 0) AS deadlock_cnt FROM pg_stat_database")
                    deadlock_cnt = int(GenericDetector._scalar(cur, 0, 0, "deadlock_cnt") or 0)
                elif ctx.db_type in ("mysql", "tdsql"):
                    cur.execute("SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks'")
                    val = GenericDetector._show_var_value(cur)
                    if val is not None:
                        deadlock_cnt = int(val)
                    else:
                        cur.execute("SELECT COUNT FROM information_schema.INNODB_METRICS WHERE NAME = 'lock_deadlocks'")
                        metric_val = GenericDetector._scalar(cur, None, 0, "COUNT", "count")
                        if metric_val is None:
                            raise RuntimeError("未获取到 Innodb_deadlocks 或 INNODB_METRICS.lock_deadlocks 指标")
                        deadlock_cnt = int(metric_val)
                elif ctx.db_type == "oracle":
                    cur.execute("SELECT value FROM v$sysstat WHERE name = 'deadlocks'")
                    val = GenericDetector._scalar(cur, None, 0, "value", "VALUE")
                    if val is None:
                        raise RuntimeError("v$sysstat 未返回 deadlocks 指标")
                    deadlock_cnt = int(val)
                elif ctx.db_type == "dm":
                    cur.execute("SELECT COUNT(*) AS deadlock_cnt FROM V$DEADLOCK_HISTORY")
                    deadlock_cnt = int(GenericDetector._scalar(cur, 0, 0, "deadlock_cnt") or 0)
                else:
                    result.status = "skip"
                    result.summary = f"不适用当前方言: {ctx.db_type}"
                    return result

                result.metrics["deadlock_count"] = deadlock_cnt
                if deadlock_cnt >= 50:
                    result.status = "critical"
                    result.severity = "critical"
                elif deadlock_cnt >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if deadlock_cnt:
                    result.findings.append({
                        "type": "deadlock_detected",
                        "count": deadlock_cnt,
                        "message": f"监测到死锁累计发生 {deadlock_cnt} 次"
                    })
                result.summary = f"死锁次数 {deadlock_cnt} 次"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_deadlock_count", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_missing_indexes(ctx: DetectionContext) -> DetectionResult:
        """检测高全表扫描的缺失索引推荐 (INS-COM-MISSING-INDEX)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-MISSING-INDEX"),
            item_title=ctx.item.get("title", "缺失索引检查"),
            detection_method="detect_missing_indexes"
        )
        if ctx.db_type not in ("oracle", "mysql", "pgsql", "dm"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle, mysql, pgsql, dm)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                rows = []
                if ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT schemaname||'.'||relname AS tblname, seq_scan, COALESCE(idx_scan, 0) AS idx_scan
                        FROM pg_stat_user_tables
                        WHERE seq_scan > 1000 AND (idx_scan IS NULL OR idx_scan = 0 OR seq_scan > idx_scan * 5)
                        ORDER BY seq_scan DESC LIMIT 10
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "mysql":
                    cur.execute("""
                        SELECT object_schema, object_name, count_read, count_write
                        FROM performance_schema.table_io_waits_summary_by_table
                        WHERE count_star > 1000 AND count_read > count_write * 10
                          AND object_schema NOT IN ('mysql', 'sys', 'performance_schema', 'information_schema')
                        LIMIT 10
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT t.owner, t.table_name, t.num_rows
                        FROM dba_tables t
                        WHERE t.owner NOT IN ('SYS','SYSTEM') AND t.num_rows > 10000
                          AND NOT EXISTS (
                              SELECT 1 FROM dba_indexes i
                              WHERE i.owner = t.owner AND i.table_name = t.table_name
                          )
                        FETCH FIRST 10 ROWS ONLY
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "dm":
                    cur.execute("""
                        SELECT t.owner, t.table_name, t.num_rows
                        FROM all_tables t
                        WHERE t.owner NOT IN ('SYS','SYSTEM','SYSSSO','SYSDBA','CTISYS','SYSJOB') AND t.num_rows > 10000
                          AND NOT EXISTS (
                              SELECT 1 FROM all_indexes i
                              WHERE i.owner = t.owner AND i.table_name = t.table_name
                          )
                        LIMIT 10
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())

                result.metrics["missing_index_candidates"] = rows
                cnt = len(rows)
                if cnt >= 10:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt:
                    result.findings.append({
                        "type": "missing_indexes",
                        "count": cnt,
                        "details": rows[:5],
                        "message": f"发现 {cnt} 张表存在高全表扫描且可能缺失索引"
                    })
                result.summary = f"建议补索引表 {cnt} 张"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_missing_indexes", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_large_table_not_partitioned(ctx: DetectionContext) -> DetectionResult:
        """检测单表数据量过大且未实施分区的表 (INS-COM-PART-NEEDED)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-PART-NEEDED"),
            item_title=ctx.item.get("title", "大表未分区"),
            detection_method="detect_large_table_not_partitioned"
        )
        if ctx.db_type == "tdsql":
            result.status = "skip"
            result.summary = "TDSQL 为分布式 Shard 架构，不适用单机大表分区检查"
            return result
        if ctx.db_type not in ("oracle", "mysql", "pgsql", "dm", "gbase"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type}"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                rows = []
                if ctx.db_type in ("mysql", "gbase"):
                    cur.execute("""
                        SELECT table_schema, table_name, ROUND((data_length + index_length)/1024/1024/1024, 2) AS size_gb
                        FROM information_schema.tables
                        WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys')
                          AND (data_length + index_length) > 10 * 1024 * 1024 * 1024
                          AND table_name NOT IN (SELECT DISTINCT table_name FROM information_schema.partitions WHERE partition_name IS NOT NULL)
                        LIMIT 10
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT owner, segment_name, ROUND(SUM(bytes)/1024/1024/1024, 2) AS size_gb
                        FROM dba_segments
                        WHERE segment_type = 'TABLE' AND owner NOT IN ('SYS','SYSTEM')
                        GROUP BY owner, segment_name
                        HAVING SUM(bytes) > 10 * 1024 * 1024 * 1024
                          AND segment_name NOT IN (SELECT table_name FROM dba_part_tables)
                        FETCH FIRST 10 ROWS ONLY
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "dm":
                    cur.execute("""
                        SELECT s.owner, s.segment_name, ROUND(SUM(s.bytes)/1024/1024/1024, 2) AS size_gb
                        FROM dba_segments s
                        WHERE s.segment_type = 'TABLE'
                          AND s.owner NOT IN ('SYS','SYSTEM','SYSSSO','SYSDBA')
                          AND NOT EXISTS (
                              SELECT 1 FROM dba_part_tables p
                              WHERE p.owner = s.owner AND p.table_name = s.segment_name
                          )
                        GROUP BY s.owner, s.segment_name
                        HAVING SUM(s.bytes) > 10737418240
                        FETCH FIRST 10 ROWS ONLY
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT schemaname||'.'||relname AS tblname, ROUND(pg_total_relation_size(relid)/1024/1024/1024, 2) AS size_gb
                        FROM pg_catalog.pg_statio_user_tables
                        WHERE pg_total_relation_size(relid) > 10::bigint * 1024 * 1024 * 1024
                          AND relid NOT IN (SELECT inhrelid FROM pg_inherits UNION SELECT partrelid FROM pg_partitioned_table)
                        LIMIT 10
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())

                result.metrics["unpartitioned_large_tables"] = rows
                cnt = len(rows)
                if cnt >= 5:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt:
                    result.findings.append({
                        "type": "unpartitioned_large_tables",
                        "count": cnt,
                        "details": rows[:5],
                        "message": f"发现 {cnt} 张单表大于 10GB 且未进行分区"
                    })
                result.summary = f"大表未分区 {cnt} 张"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_large_table_not_partitioned", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_sparse_index(ctx: DetectionContext) -> DetectionResult:
        """检测低区分度稀疏索引 (INS-COM-SPARSE-INDEX)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-SPARSE-INDEX"),
            item_title=ctx.item.get("title", "稀疏索引"),
            detection_method="detect_sparse_index"
        )
        if ctx.db_type not in ("oracle", "mysql", "pgsql", "dm"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle, mysql, pgsql, dm)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                rows = []
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT owner, index_name, table_name, distinct_keys, num_rows,
                               ROUND(distinct_keys/NULLIF(num_rows,0)*100, 2) AS sel_pct
                        FROM dba_indexes
                        WHERE owner NOT IN ('SYS','SYSTEM') AND num_rows > 1000
                          AND distinct_keys > 0 AND distinct_keys/NULLIF(num_rows,0) < 0.05
                        FETCH FIRST 10 ROWS ONLY
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "dm":
                    cur.execute("""
                        SELECT owner, index_name, table_name, distinct_keys, num_rows,
                               ROUND(distinct_keys/NULLIF(num_rows,0)*100, 2) AS sel_pct
                        FROM all_indexes
                        WHERE owner NOT IN ('SYS','SYSTEM') AND num_rows > 1000
                          AND distinct_keys > 0 AND distinct_keys/NULLIF(num_rows,0) < 0.05
                        LIMIT 10
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "mysql":
                    cur.execute("""
                        SELECT s.table_schema, s.table_name, s.index_name, MAX(s.cardinality) AS cardinality, t.table_rows
                        FROM information_schema.statistics s
                        JOIN information_schema.tables t ON s.table_schema=t.table_schema AND s.table_name=t.table_name
                        WHERE s.table_schema NOT IN ('mysql','information_schema','performance_schema','sys')
                          AND t.table_rows > 1000
                        GROUP BY s.table_schema, s.table_name, s.index_name, t.table_rows
                        HAVING cardinality > 0 AND cardinality/NULLIF(table_rows,0) < 0.05
                        LIMIT 10
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT schemaname||'.'||tablename AS tbl, attname, n_distinct
                        FROM pg_stats
                        WHERE n_distinct > 0 AND n_distinct < 0.05
                          AND schemaname NOT IN ('pg_catalog', 'information_schema')
                        LIMIT 10
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())

                result.metrics["sparse_indexes"] = rows
                cnt = len(rows)
                if cnt >= 50:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 5:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt:
                    result.findings.append({
                        "type": "sparse_indexes",
                        "count": cnt,
                        "details": rows[:5],
                        "message": f"发现 {cnt} 个选择性低于 5% 的稀疏索引"
                    })
                result.summary = f"稀疏索引 {cnt} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_sparse_index", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_param_deviation(ctx: DetectionContext) -> DetectionResult:
        """检测参数偏离最佳实践基线 (INS-COM-PARAM-DEV)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-PARAM-DEV"),
            item_title=ctx.item.get("title", "参数偏离最佳实践"),
            detection_method="detect_param_deviation"
        )
        if ctx.db_type not in ("oracle", "mysql", "pgsql", "dm", "gbase"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type}"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                deviations = []
                if ctx.db_type in ("mysql", "gbase"):
                    cur.execute("SHOW VARIABLES LIKE 'max_connections'")
                    val = GenericDetector._show_var_value(cur)
                    if val is None:
                        raise RuntimeError("SHOW VARIABLES LIKE 'max_connections' 未返回结果")
                    if int(val) < 100:
                        deviations.append({"param": "max_connections", "val": val, "rec": ">=100"})
                elif ctx.db_type == "oracle":
                    cur.execute("SELECT name, value FROM v$parameter WHERE name IN ('open_cursors', 'processes')")
                    for r in (cur.fetchall() or []):
                        p_name = str(GenericDetector._row_col(r, 0, "name", "NAME") or "").lower()
                        p_val = GenericDetector._row_col(r, 1, "value", "VALUE")
                        if p_name == 'open_cursors' and p_val is not None and int(p_val) < 300:
                            deviations.append({"param": "open_cursors", "val": p_val, "rec": ">=300"})
                        elif p_name == 'processes' and p_val is not None and int(p_val) < 150:
                            deviations.append({"param": "processes", "val": p_val, "rec": ">=150"})
                elif ctx.db_type == "dm":
                    cur.execute("SELECT NAME, VALUE FROM V$PARAMETER WHERE NAME IN ('MAX_SESSIONS', 'BUFFER')")
                    for r in (cur.fetchall() or []):
                        p_name = str(GenericDetector._row_col(r, 0, "NAME", "name") or "").upper()
                        p_val = GenericDetector._row_col(r, 1, "VALUE", "value")
                        if p_name == 'MAX_SESSIONS' and p_val is not None and int(p_val) < 100:
                            deviations.append({"param": "MAX_SESSIONS", "val": p_val, "rec": ">=100"})
                elif ctx.db_type == "pgsql":
                    cur.execute("SHOW max_connections")
                    val = GenericDetector._show_var_value(cur)
                    if val is None:
                        raise RuntimeError("SHOW max_connections 未返回结果")
                    if int(val) < 100:
                        deviations.append({"param": "max_connections", "val": val, "rec": ">=100"})

                result.metrics["deviations"] = deviations
                cnt = len(deviations)
                if cnt >= 20:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt:
                    result.findings.append({
                        "type": "param_deviation",
                        "count": cnt,
                        "details": deviations,
                        "message": f"发现 {cnt} 个关键参数偏离最佳实践推荐值"
                    })
                result.summary = f"偏离参数 {cnt} 项"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_param_deviation", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_archive_gap(ctx: DetectionContext) -> DetectionResult:
        """检测归档间隙 (INS-COM-ARCH-GAP)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-ARCH-GAP"),
            item_title=ctx.item.get("title", "归档间隙"),
            detection_method="detect_archive_gap"
        )
        if ctx.db_type not in ("oracle", "dm"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle, dm 归档架构)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                gaps = []
                if ctx.db_type == "oracle":
                    cur.execute("SELECT thread#, low_sequence#, high_sequence# FROM v$archive_gap")
                    gaps = [
                        {
                            "thread": GenericDetector._row_col(r, 0, "thread#"),
                            "low": GenericDetector._row_col(r, 1, "low_sequence#"),
                            "high": GenericDetector._row_col(r, 2, "high_sequence#"),
                        }
                        for r in (cur.fetchall() or [])
                    ]
                elif ctx.db_type == "dm":
                    cur.execute("SELECT ARCH_MODE FROM V$DATABASE")
                    arch_mode = str(GenericDetector._scalar(cur, "", 0, "ARCH_MODE", "arch_mode") or "").strip().upper()
                    if not arch_mode:
                        raise RuntimeError("V$DATABASE 未返回 ARCH_MODE")
                    result.metrics["arch_mode"] = arch_mode
                    if arch_mode != "Y":
                        result.status = "skip"
                        result.summary = f"实例未开启归档模式 (ARCH_MODE={arch_mode})，不适用归档间隙检查"
                        return result

                    cur.execute("SELECT ARCH_TYPE, ARCH_DEST, ARCH_STATUS, ARCH_SRC FROM V$ARCH_STATUS")
                    status_rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                    for sr in status_rows:
                        st = str(sr.get("arch_status") or "").strip().upper()
                        if st not in ("VALID", "ACTIVE", "OK", "OPEN"):
                            gaps.append({
                                "type": "arch_dest_unhealthy",
                                "arch_type": sr.get("arch_type"),
                                "dest": sr.get("arch_dest"),
                                "status": sr.get("arch_status"),
                            })

                    cur.execute("SELECT DB_MAGIC, ARCH_SEQ, NEXT_SEQ, STATUS FROM V$ARCH_FILE ORDER BY DB_MAGIC, ARCH_SEQ")
                    file_rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                    last_by_magic: Dict[Any, int] = {}
                    for fr in file_rows:
                        magic = fr.get("db_magic")
                        curr_seq = fr.get("arch_seq")
                        next_seq = fr.get("next_seq")
                        prev_next = last_by_magic.get(magic)
                        if prev_next is not None and curr_seq is not None and int(curr_seq) > int(prev_next) + 1:
                            gaps.append({
                                "type": "arch_seq_gap",
                                "db_magic": magic,
                                "low": int(prev_next),
                                "high": int(curr_seq),
                            })
                        if next_seq is not None:
                            last_by_magic[magic] = int(next_seq)

                result.metrics["archive_gaps"] = gaps
                cnt = len(gaps)
                if cnt >= 20:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt:
                    result.findings.append({
                        "type": "archive_gap",
                        "count": cnt,
                        "details": gaps,
                        "message": f"发现 {cnt} 处归档日志存在间隙"
                    })
                result.summary = f"归档间隙 {cnt} 处"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_archive_gap", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_lock_waits(ctx: DetectionContext) -> DetectionResult:
        """检测当前锁等待 (INS-COM-LOCK-WAIT)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-LOCK-WAIT"),
            item_title=ctx.item.get("title", "当前锁等待"),
            detection_method="detect_lock_waits"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                rows = []
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT sid, serial#, username, event, seconds_in_wait
                        FROM v$session
                        WHERE wait_class = 'Application' AND event LIKE 'enq: TX%' AND seconds_in_wait > 5
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "dm":
                    cur.execute("""
                        SELECT t.SESS_ID AS sess_id, s.USER_NAME AS user_name,
                               t.ID AS transaction_id, SUBSTR(s.SQL_TEXT, 1, 200) AS sql_text,
                               l.LMODE AS lmode, l.BLOCKED AS blocked
                        FROM V$LOCK l
                        LEFT JOIN V$TRX t ON l.TRX_ID = t.ID
                        LEFT JOIN V$SESSIONS s ON t.SESS_ID = s.SESS_ID
                        WHERE l.BLOCKED = 1
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type in ("mysql", "tdsql"):
                    ver = GenericDetector._mysql_major_version(cur)
                    if ver >= 8:
                        cur.execute("""
                            SELECT r.trx_id AS waiting_trx_id, r.trx_mysql_thread_id AS waiting_thread,
                                   b.trx_id AS blocking_trx_id, b.trx_mysql_thread_id AS blocking_thread
                            FROM performance_schema.data_lock_waits w
                            JOIN information_schema.innodb_trx b ON b.trx_id = w.BLOCKING_ENGINE_TRANSACTION_ID
                            JOIN information_schema.innodb_trx r ON r.trx_id = w.REQUESTING_ENGINE_TRANSACTION_ID
                        """)
                    else:
                        cur.execute("""
                            SELECT r.trx_id AS waiting_trx_id, r.trx_mysql_thread_id AS waiting_thread,
                                   b.trx_id AS blocking_trx_id, b.trx_mysql_thread_id AS blocking_thread
                            FROM information_schema.innodb_lock_waits w
                            JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id
                            JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id
                        """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "gbase":
                    cur.execute("""
                        SELECT 'processlist' AS source, ID AS pid, USER AS user, HOST AS host, DB AS db,
                               TIME AS wait_sec, STATE AS state, LEFT(INFO, 120) AS sql_text
                        FROM information_schema.PROCESSLIST
                        WHERE LOWER(COALESCE(STATE, '')) LIKE '%lock%'
                           OR LOWER(COALESCE(STATE, '')) LIKE '%waiting for%'
                    """)
                    proc_rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                    cur.execute("""
                        SELECT 'table_locks' AS source, DB AS db, TableName AS table_name,
                               ReadLocks AS read_locks, WriteLocks AS write_locks,
                               ReceiveWriteLocks AS receive_write_locks, ExcusiveLocks AS exclusive_locks,
                               ExcusiveWriteLocks AS exclusive_write_locks
                        FROM information_schema.TABLE_LOCKS
                        WHERE ReceiveWriteLocks > 0 OR ExcusiveWriteLocks > 0
                    """)
                    tbl_rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                    rows = proc_rows + tbl_rows
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT pid, wait_event_type, wait_event, state
                        FROM pg_stat_activity
                        WHERE wait_event_type = 'Lock' AND state = 'active'
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                else:
                    result.status = "skip"
                    result.summary = f"不适用当前方言: {ctx.db_type}"
                    return result

                result.metrics["lock_waits"] = rows
                cnt = len(rows)
                if cnt >= 50:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 5:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt:
                    result.findings.append({
                        "type": "lock_waits",
                        "count": cnt,
                        "details": rows[:5],
                        "message": f"发现 {cnt} 个会话处于锁等待状态"
                    })
                result.summary = f"锁等待会话 {cnt} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_lock_waits", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_blocked_sessions(ctx: DetectionContext) -> DetectionResult:
        """检测被阻塞超过 5 分钟的会话 (INS-COM-BLOCK-SESSION)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-BLOCK-SESSION"),
            item_title=ctx.item.get("title", "被阻塞会话"),
            detection_method="detect_blocked_sessions"
        )
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                rows = []
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT sid, serial#, username, blocking_session, seconds_in_wait
                        FROM v$session
                        WHERE blocking_session IS NOT NULL AND seconds_in_wait >= 300
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "dm":
                    cur.execute("""
                        SELECT w.ID AS waiting_trx_id, tr.SESS_ID AS sess_id, s.USER_NAME AS user_name,
                               w.WAIT_FOR_ID AS blocking_trx_id, tb.SESS_ID AS blocker_sess_id,
                               ROUND(w.WAIT_TIME / 1000.0, 1) AS wait_sec
                        FROM V$TRXWAIT w
                        LEFT JOIN V$TRX tr ON tr.ID = w.ID
                        LEFT JOIN V$TRX tb ON tb.ID = w.WAIT_FOR_ID
                        LEFT JOIN V$SESSIONS s ON s.SESS_ID = tr.SESS_ID
                        WHERE w.WAIT_TIME >= 300000
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type in ("mysql", "tdsql"):
                    cur.execute("""
                        SELECT r.trx_mysql_thread_id, TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_sec
                        FROM information_schema.innodb_trx r
                        WHERE r.trx_wait_started IS NOT NULL AND TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) >= 300
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "gbase":
                    cur.execute("""
                        SELECT ID AS pid, USER AS user, HOST AS host, DB AS db,
                               TIME AS wait_sec, STATE AS state, LEFT(INFO, 120) AS sql_text
                        FROM information_schema.PROCESSLIST
                        WHERE TIME >= 300
                          AND (LOWER(COALESCE(STATE, '')) LIKE '%lock%' OR LOWER(COALESCE(STATE, '')) LIKE '%waiting for%')
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                elif ctx.db_type == "pgsql":
                    cur.execute("""
                        SELECT pid, usename, EXTRACT(EPOCH FROM (now() - query_start)) AS duration_sec
                        FROM pg_stat_activity
                        WHERE wait_event_type = 'Lock' AND (now() - query_start) >= INTERVAL '5 minutes'
                    """)
                    rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                else:
                    result.status = "skip"
                    result.summary = f"不适用当前方言: {ctx.db_type}"
                    return result

                result.metrics["blocked_sessions"] = rows
                cnt = len(rows)
                if cnt >= 20:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt:
                    result.findings.append({
                        "type": "blocked_sessions",
                        "count": cnt,
                        "details": rows[:5],
                        "message": f"发现 {cnt} 个被阻塞超过 5 分钟的会话"
                    })
                result.summary = f"长阻塞会话 {cnt} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_blocked_sessions", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_open_cursors(ctx: DetectionContext) -> DetectionResult:
        """检测打开游标数 (INS-COM-OPEN-CURSOR)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-OPEN-CURSOR"),
            item_title=ctx.item.get("title", "打开游标数"),
            detection_method="detect_open_cursors"
        )
        if ctx.db_type not in ("oracle", "dm"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle, dm)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                current_open, max_open = 0, 0
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT a.value current_open, b.value max_open
                        FROM (SELECT sum(value) value FROM v$sesstat WHERE statistic# = (SELECT statistic# FROM v$statname WHERE name = 'opened cursors current')) a,
                             (SELECT value FROM v$parameter WHERE name = 'open_cursors') b
                    """)
                    row = cur.fetchone()
                    if row is None:
                        raise RuntimeError("Oracle v$sesstat/v$parameter 未返回打开游标数据")
                    current_open = int(GenericDetector._row_col(row, 0, "current_open") or 0)
                    max_open = int(GenericDetector._row_col(row, 1, "max_open") or 0)
                elif ctx.db_type == "dm":
                    cur.execute("SELECT COUNT(*) AS current_open FROM V$OPEN_STMT")
                    row_stmt = cur.fetchone()
                    if row_stmt is None:
                        raise RuntimeError("DM V$OPEN_STMT 未返回打开语句统计")
                    current_open = int(GenericDetector._row_col(row_stmt, 0, "current_open") or 0)

                    cur.execute("SELECT COALESCE(MAX(N_USED_STMT), 0) AS max_sess_open FROM V$SESSIONS")
                    max_sess_open = int(GenericDetector._scalar(cur, 0, 0, "max_sess_open") or 0)

                    cur.execute("SELECT VALUE FROM V$PARAMETER WHERE NAME = 'MAX_SESSION_STATEMENT'")
                    max_val = GenericDetector._scalar(cur, None, 0, "VALUE", "value")
                    if max_val is None:
                        raise RuntimeError("DM V$PARAMETER 未返回 MAX_SESSION_STATEMENT 参数")
                    max_open = int(max_val)
                    result.metrics["max_session_open"] = max_sess_open
                    if max_open > 0:
                        result.metrics["session_usage_pct"] = round(max_sess_open / max_open * 100.0, 1)

                result.metrics["current_open"] = current_open
                result.metrics["max_open"] = max_open
                session_ratio = (result.metrics.get("session_usage_pct", 0.0) / 100.0) if max_open > 0 else 0.0
                if current_open >= 10000 or session_ratio >= 0.95:
                    result.status = "critical"
                    result.severity = "critical"
                elif current_open >= 1000 or session_ratio >= 0.80:
                    result.status = "warning"
                    result.severity = "warning"
                if result.status in ("warning", "critical"):
                    result.findings.append({
                        "type": "high_open_cursors",
                        "current": current_open,
                        "max": max_open,
                        "message": f"当前打开游标数较高: {current_open} (上限 {max_open})"
                    })
                result.summary = f"打开游标数 {current_open} (上限 {max_open})"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_open_cursors", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_temp_usage(ctx: DetectionContext) -> DetectionResult:
        """检测临时表空间使用率 (INS-COM-TEMP-USAGE)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-TEMP-USAGE"),
            item_title=ctx.item.get("title", "临时表空间使用"),
            detection_method="detect_temp_usage"
        )
        if ctx.db_type == "tdsql":
            result.status = "skip"
            result.summary = "TDSQL 为分布式 Shard 架构，不适用单机临时表空间检查"
            return result
        if ctx.db_type not in ("oracle", "mysql", "pgsql", "dm", "gbase"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type}"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                pct_used = 0.0
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT tablespace_name, ROUND(SUM(bytes)/1024/1024, 2) AS total_mb,
                               ROUND(SUM(user_bytes)/1024/1024, 2) AS used_mb
                        FROM dba_temp_files
                        GROUP BY tablespace_name
                    """)
                    dict_rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                    temp_files = [
                        {
                            "name": r.get("tablespace_name"),
                            "total_mb": float(r.get("total_mb") or 0),
                            "used_mb": float(r.get("used_mb") or 0),
                        }
                        for r in dict_rows
                    ]
                    result.metrics["temp_files"] = temp_files
                    total_mb_sum = sum(t["total_mb"] for t in temp_files)
                    used_mb_sum = sum(t["used_mb"] for t in temp_files)
                    if total_mb_sum > 0:
                        pct_used = round(used_mb_sum / total_mb_sum * 100.0, 1)
                elif ctx.db_type == "dm":
                    cur.execute("""
                        SELECT t.NAME AS tablespace_name,
                               ROUND(t.TOTAL_SIZE * CAST(p.VALUE AS BIGINT) / 1024.0 / 1024.0, 2) AS total_mb,
                               ROUND(t.USED_SIZE * CAST(p.VALUE AS BIGINT) / 1024.0 / 1024.0, 2) AS used_mb
                        FROM V$TABLESPACE t
                        CROSS JOIN (SELECT VALUE FROM V$PARAMETER WHERE NAME = 'GLOBAL_PAGE_SIZE') p
                        WHERE t.TYPE$ = 2
                    """)
                    dict_rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                    if not dict_rows:
                        raise RuntimeError("DM V$TABLESPACE 未返回 TYPE$=2 临时表空间")
                    temp_files = [
                        {
                            "name": r.get("tablespace_name"),
                            "total_mb": float(r.get("total_mb") or 0),
                            "used_mb": float(r.get("used_mb") or 0),
                        }
                        for r in dict_rows
                    ]
                    result.metrics["temp_files"] = temp_files
                    total_mb_sum = sum(t["total_mb"] for t in temp_files)
                    used_mb_sum = sum(t["used_mb"] for t in temp_files)
                    if total_mb_sum > 0:
                        pct_used = round(used_mb_sum / total_mb_sum * 100.0, 1)
                elif ctx.db_type == "pgsql":
                    cur.execute("SELECT ROUND(SUM(temp_bytes)/1024/1024, 2) AS temp_mb FROM pg_stat_database")
                    temp_mb = float(GenericDetector._scalar(cur, 0, 0, "temp_mb") or 0)
                    result.metrics["temp_mb"] = temp_mb
                    pct_used = min(100.0, round(temp_mb / 1024.0 * 10.0, 1))
                elif ctx.db_type == "mysql":
                    ver = GenericDetector._mysql_major_version(cur)
                    session_temp_mb = 0.0
                    if ver >= 8:
                        cur.execute("""
                            SELECT ROUND(COALESCE(SUM(SIZE), 0) / 1024.0 / 1024.0, 2) AS session_temp_mb
                            FROM information_schema.INNODB_SESSION_TEMP_TABLESPACES
                            WHERE STATE = 'ACTIVE'
                        """)
                        session_temp_mb = float(GenericDetector._scalar(cur, 0, 0, "session_temp_mb") or 0)
                    cur.execute("""
                        SELECT ROUND(COALESCE(SUM((TOTAL_EXTENTS - FREE_EXTENTS) * EXTENT_SIZE), 0) / 1024.0 / 1024.0, 2) AS used_mb,
                               ROUND(COALESCE(SUM(TOTAL_EXTENTS * EXTENT_SIZE), 0) / 1024.0 / 1024.0, 2) AS alloc_mb,
                               COALESCE(SUM(CASE WHEN MAXIMUM_SIZE IS NULL OR MAXIMUM_SIZE <= 0 THEN 0 ELSE MAXIMUM_SIZE END), 0) AS max_bytes,
                               COALESCE(MIN(CASE WHEN MAXIMUM_SIZE IS NULL OR MAXIMUM_SIZE <= 0 THEN 0 ELSE 1 END), 0) AS all_bounded
                        FROM information_schema.FILES
                        WHERE FILE_TYPE = 'TEMPORARY'
                    """)
                    f_row = cur.fetchone()
                    ibtmp_used_mb = float(GenericDetector._row_col(f_row, 0, "used_mb") or 0)
                    ibtmp_alloc_mb = float(GenericDetector._row_col(f_row, 1, "alloc_mb") or 0)
                    max_bytes = float(GenericDetector._row_col(f_row, 2, "max_bytes") or 0)
                    all_bounded = int(GenericDetector._row_col(f_row, 3, "all_bounded") or 0)
                    cur.execute("SHOW VARIABLES LIKE 'temptable_max_ram'")
                    ram_val = GenericDetector._show_var_value(cur)
                    temptable_max_mb = round(float(ram_val) / 1024.0 / 1024.0, 2) if ram_val else 1024.0
                    temp_mb = round(max(session_temp_mb, ibtmp_used_mb), 2)
                    result.metrics["temp_mb"] = temp_mb
                    result.metrics["session_temp_mb"] = round(session_temp_mb, 2)
                    result.metrics["ibtmp_used_mb"] = round(ibtmp_used_mb, 2)
                    result.metrics["ibtmp_alloc_mb"] = round(ibtmp_alloc_mb, 2)
                    result.metrics["temptable_max_mb"] = temptable_max_mb
                    if all_bounded == 1 and max_bytes > 0:
                        pct_used = round(ibtmp_used_mb / (max_bytes / 1024.0 / 1024.0) * 100.0, 1)
                    elif temptable_max_mb > 0:
                        pct_used = min(100.0, round(temp_mb / temptable_max_mb * 100.0, 1))
                elif ctx.db_type == "gbase":
                    cur.execute("SHOW GLOBAL STATUS WHERE Variable_name IN ('Meminfo_temp_heap_total_size', 'Meminfo_temp_heap_used_size')")
                    status_map = GenericDetector._show_vars_map(cur)
                    if "Meminfo_temp_heap_total_size" not in status_map:
                        raise RuntimeError("GBase 未返回 Meminfo_temp_heap_total_size 状态变量")
                    heap_total = float(status_map.get("Meminfo_temp_heap_total_size") or 0)
                    heap_used = float(status_map.get("Meminfo_temp_heap_used_size") or 0)
                    cur.execute("SELECT COALESCE(SUM(TEMP_SPACE), 0) AS temp_bytes FROM performance_schema.SESSION_MEMORY_USAGE_INFO")
                    sess_temp_bytes = float(GenericDetector._scalar(cur, 0, 0, "temp_bytes") or 0)
                    used_bytes = max(heap_used, sess_temp_bytes)
                    temp_mb = round(used_bytes / 1024.0 / 1024.0, 2)
                    result.metrics["temp_mb"] = temp_mb
                    result.metrics["temp_heap_total_mb"] = round(heap_total / 1024.0 / 1024.0, 2)
                    result.metrics["session_temp_mb"] = round(sess_temp_bytes / 1024.0 / 1024.0, 2)
                    pct_used = round(used_bytes / heap_total * 100.0, 1) if heap_total > 0 else 0.0

                result.metrics["pct_used"] = pct_used
                if pct_used >= 95:
                    result.status = "critical"
                    result.severity = "critical"
                elif pct_used >= 70:
                    result.status = "warning"
                    result.severity = "warning"
                if pct_used >= 70:
                    result.findings.append({
                        "type": "temp_usage_high",
                        "pct_used": pct_used,
                        "message": f"临时表空间使用率较高: {pct_used}%"
                    })
                result.summary = f"临时表空间使用率 {pct_used}%"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_temp_usage", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_undo_usage(ctx: DetectionContext) -> DetectionResult:
        """检测 Undo 表空间使用率 (INS-COM-UNDO-USAGE)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-COM-UNDO-USAGE"),
            item_title=ctx.item.get("title", "Undo 使用率"),
            detection_method="detect_undo_usage"
        )
        if ctx.db_type == "pgsql":
            result.status = "skip"
            result.summary = "不适用当前方言: pgsql 采用堆内 MVCC 行版本机制，无独立 Undo 表空间"
            return result
        if ctx.db_type not in ("oracle", "dm"):
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle, dm)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                pct_used = 0.0
                if ctx.db_type == "oracle":
                    cur.execute("""
                        SELECT df.tablespace_name,
                               ROUND(df.total_bytes / 1024 / 1024, 2) AS total_mb,
                               ROUND((df.total_bytes - COALESCE(fs.free_bytes, 0)) / 1024 / 1024, 2) AS used_mb
                        FROM (
                            SELECT tablespace_name, SUM(bytes) AS total_bytes
                            FROM dba_data_files
                            WHERE tablespace_name IN (SELECT tablespace_name FROM dba_tablespaces WHERE contents = 'UNDO')
                            GROUP BY tablespace_name
                        ) df
                        LEFT JOIN (
                            SELECT tablespace_name, SUM(bytes) AS free_bytes
                            FROM dba_free_space
                            GROUP BY tablespace_name
                        ) fs ON df.tablespace_name = fs.tablespace_name
                    """)
                    dict_rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                    undo_rows = [
                        {
                            "name": r.get("tablespace_name"),
                            "total_mb": float(r.get("total_mb") or 0),
                            "used_mb": float(r.get("used_mb") or 0),
                        }
                        for r in dict_rows
                    ]
                    result.metrics["undo_tablespaces"] = undo_rows
                    total_sum = sum(item["total_mb"] for item in undo_rows)
                    used_sum = sum(item["used_mb"] for item in undo_rows)
                    if total_sum > 0:
                        pct_used = round(used_sum / total_sum * 100.0, 1)
                elif ctx.db_type == "dm":
                    cur.execute("""
                        SELECT t.NAME AS tablespace_name,
                               ROUND(t.TOTAL_SIZE * CAST(p.VALUE AS BIGINT) / 1024.0 / 1024.0, 2) AS total_mb,
                               ROUND(t.USED_SIZE * CAST(p.VALUE AS BIGINT) / 1024.0 / 1024.0, 2) AS used_mb
                        FROM V$TABLESPACE t
                        CROSS JOIN (SELECT VALUE FROM V$PARAMETER WHERE NAME = 'GLOBAL_PAGE_SIZE') p
                        WHERE UPPER(t.NAME) = 'ROLL'
                    """)
                    dict_rows = GenericDetector._rows_to_dicts(cur, cur.fetchall())
                    if not dict_rows:
                        raise RuntimeError("DM V$TABLESPACE 未返回 ROLL 回滚表空间")
                    undo_rows = []
                    for r in dict_rows:
                        t_mb = float(r.get("total_mb") or 0)
                        u_mb = float(r.get("used_mb") or 0)
                        undo_rows.append({
                            "name": r.get("tablespace_name"),
                            "total_mb": t_mb,
                            "used_mb": u_mb,
                            "free_mb": round(max(0.0, t_mb - u_mb), 2),
                        })
                    result.metrics["undo_tablespaces"] = undo_rows
                    total_sum = sum(item["total_mb"] for item in undo_rows)
                    used_sum = sum(item["used_mb"] for item in undo_rows)
                    if total_sum > 0:
                        pct_used = round(used_sum / total_sum * 100.0, 1)

                result.metrics["pct_used"] = pct_used
                if pct_used >= 95:
                    result.status = "critical"
                    result.severity = "critical"
                elif pct_used >= 70:
                    result.status = "warning"
                    result.severity = "warning"
                if pct_used >= 70:
                    result.findings.append({
                        "type": "undo_usage_high",
                        "pct_used": pct_used,
                        "message": f"Undo 使用率过高: {pct_used}%"
                    })
                    result.summary = f"Undo 使用率过高: {pct_used}%"
                else:
                    result.summary = f"Undo 使用率 {pct_used}% 状态正常"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            GenericDetector._degrade_note("inspection.detect_undo_usage", str(e), exc=e)
            result.status = "error"
            result.error = str(e)
        return result

    # -------------------------------------------------------------------------
    # Oracle 专项新检测器
    # -------------------------------------------------------------------------

    @staticmethod
    def detect_awr_top_event(ctx: DetectionContext) -> DetectionResult:
        """检测业务高峰 Top 等待事件 (INS-ORA-AWR-TOP-EVT)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-ORA-AWR-TOP-EVT"),
            item_title=ctx.item.get("title", "业务高峰 Top Event"),
            detection_method="detect_awr_top_event"
        )
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle 专属特性)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                try:
                    cur.execute("""
                        SELECT event, total_waits, time_waited_micro
                        FROM dba_hist_system_event
                        WHERE wait_class != 'Idle'
                        ORDER BY time_waited_micro DESC
                        FETCH FIRST 5 ROWS ONLY
                    """)
                except Exception:
                    cur.execute("""
                        SELECT event, total_waits, time_waited_micro
                        FROM v$system_event
                        WHERE wait_class != 'Idle'
                        ORDER BY time_waited_micro DESC
                        FETCH FIRST 5 ROWS ONLY
                    """)
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["top_events"] = rows
                if rows:
                    result.findings.append({
                        "type": "awr_top_event",
                        "top_event": rows[0].get("event"),
                        "message": f"高峰主要等待事件为 {rows[0].get('event')}"
                    })
                result.summary = f"AWR Top 等待事件 {len(rows)} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_awr_top_sql(ctx: DetectionContext) -> DetectionResult:
        """检测业务高峰 Top SQL (INS-ORA-AWR-TOP-SQL)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-ORA-AWR-TOP-SQL"),
            item_title=ctx.item.get("title", "业务高峰 Top SQL"),
            detection_method="detect_awr_top_sql"
        )
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle 专属特性)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                try:
                    cur.execute("""
                        SELECT sql_id, executions_delta, elapsed_time_delta
                        FROM dba_hist_sqlstat
                        ORDER BY elapsed_time_delta DESC
                        FETCH FIRST 5 ROWS ONLY
                    """)
                except Exception:
                    cur.execute("""
                        SELECT sql_id, executions, elapsed_time
                        FROM v$sql
                        WHERE executions > 0
                        ORDER BY elapsed_time DESC
                        FETCH FIRST 5 ROWS ONLY
                    """)
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["top_sql"] = rows
                result.summary = f"AWR Top SQL {len(rows)} 条"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_sta_recommendations(ctx: DetectionContext) -> DetectionResult:
        """检测 SQL Tuning Advisor 结果建议 (INS-ORA-STA-OUTPUT)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-ORA-STA-OUTPUT"),
            item_title=ctx.item.get("title", "SQL Tuning Advisor 结果"),
            detection_method="detect_sta_recommendations"
        )
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle 专属特性)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                try:
                    cur.execute("""
                        SELECT task_name, recommendation_id, type
                        FROM dba_advisor_recommendations
                        WHERE task_name LIKE '%SQL%'
                        FETCH FIRST 10 ROWS ONLY
                    """)
                    cols = [c[0].lower() for c in cur.description]
                    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                except Exception:
                    rows = []
                result.metrics["sta_recommendations"] = rows
                if len(rows) >= 50:
                    result.status = "critical"
                    result.severity = "critical"
                elif len(rows) >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if rows:
                    result.findings.append({
                        "type": "sta_advice",
                        "count": len(rows),
                        "message": f"发现 {len(rows)} 条 SQL Tuning Advisor 优化建议"
                    })
                result.summary = f"STA 优化建议 {len(rows)} 条"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_segment_advisor(ctx: DetectionContext) -> DetectionResult:
        """检测 Segment Advisor 空间回收建议 (INS-ORA-SEG-ADV)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-ORA-SEG-ADV"),
            item_title=ctx.item.get("title", "Segment Advisor 结果"),
            detection_method="detect_segment_advisor"
        )
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle 专属特性)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                try:
                    cur.execute("""
                        SELECT task_name, segment_name, segment_type, message
                        FROM dba_advisor_findings
                        WHERE task_name LIKE '%SEGMENT%'
                        FETCH FIRST 10 ROWS ONLY
                    """)
                    cols = [c[0].lower() for c in cur.description]
                    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                except Exception:
                    rows = []
                result.metrics["segment_recommendations"] = rows
                if len(rows) >= 50:
                    result.status = "critical"
                    result.severity = "critical"
                elif len(rows) >= 1:
                    result.status = "warning"
                    result.severity = "warning"
                if rows:
                    result.findings.append({
                        "type": "segment_shrink_advice",
                        "count": len(rows),
                        "message": f"发现 {len(rows)} 个段顾问推荐收缩对象"
                    })
                result.summary = f"段顾问建议 {len(rows)} 条"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_fra_usage(ctx: DetectionContext) -> DetectionResult:
        """检测快速恢复区 FRA 使用率 (INS-ORA-FRA)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-ORA-FRA"),
            item_title=ctx.item.get("title", "FRA 使用率"),
            detection_method="detect_fra_usage"
        )
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle 专属特性)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT name, space_limit, space_used, space_reclaimable,
                           ROUND(space_used/NULLIF(space_limit,0)*100, 1) pct_used
                    FROM v$recovery_file_dest
                """)
                row = cur.fetchone()
                if row:
                    name, limit, used, reclaimable, pct = row
                    result.metrics["fra_name"] = name
                    result.metrics["space_limit_bytes"] = limit
                    result.metrics["space_used_bytes"] = used
                    result.metrics["pct_used"] = pct or 0.0
                    pct_val = pct or 0.0
                    if pct_val >= 95:
                        result.status = "critical"
                        result.severity = "critical"
                    elif pct_val >= 70:
                        result.status = "warning"
                        result.severity = "warning"
                    if pct_val >= 70:
                        result.findings.append({
                            "type": "fra_usage_high",
                            "pct_used": pct_val,
                            "message": f"FRA 空间使用率达到 {pct_val}%, 需警惕归档撑爆"
                        })
                    result.summary = f"FRA 使用率 {pct_val}%"
                else:
                    result.summary = "未启用 FRA (快速恢复区未配置)"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_ctrlfile_backup(ctx: DetectionContext) -> DetectionResult:
        """检测控制文件自动备份状态 (INS-ORA-CTRL-BAK)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-ORA-CTRL-BAK"),
            item_title=ctx.item.get("title", "控制文件备份"),
            detection_method="detect_ctrlfile_backup"
        )
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle 专属特性)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                try:
                    cur.execute("SELECT value FROM v$rman_configuration WHERE name = 'CONFIGURE CONTROLFILE AUTOBACKUP'")
                    row = cur.fetchone()
                    auto_bk = row[0] if row else "OFF"
                except Exception:
                    auto_bk = "OFF"
                result.metrics["autobackup_status"] = auto_bk
                if auto_bk != "ON":
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "controlfile_autobackup_off",
                        "status": auto_bk,
                        "message": "控制文件自动备份未开启 (CONTROLFILE AUTOBACKUP OFF)"
                    })
                result.summary = f"控制文件自动备份: {auto_bk}"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_arch_deletion_policy(ctx: DetectionContext) -> DetectionResult:
        """检测归档日志清理策略 (INS-ORA-ARCH-DELETE)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-ORA-ARCH-DELETE"),
            item_title=ctx.item.get("title", "归档日志清理策略"),
            detection_method="detect_arch_deletion_policy"
        )
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle 专属特性)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                try:
                    cur.execute("SELECT value FROM v$rman_configuration WHERE name LIKE '%ARCHIVELOG DELETION POLICY%'")
                    row = cur.fetchone()
                    policy = row[0] if row else "NONE"
                except Exception:
                    policy = "NONE"
                result.metrics["deletion_policy"] = policy
                if policy in ("NONE", "OFF", ""):
                    result.status = "warning"
                    result.severity = "warning"
                    result.findings.append({
                        "type": "arch_deletion_policy_none",
                        "policy": policy,
                        "message": "未配置合规的归档日志自动删除策略，存在磁盘撑爆风险"
                    })
                result.summary = f"归档删除策略: {policy}"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_dba_users(ctx: DetectionContext) -> DetectionResult:
        """检测 DBA 权限账号 (INS-ORA-DBA-USERS)"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", "INS-ORA-DBA-USERS"),
            item_title=ctx.item.get("title", "DBA 权限账号"),
            detection_method="detect_dba_users"
        )
        if ctx.db_type != "oracle":
            result.status = "skip"
            result.summary = f"不适用当前方言: {ctx.db_type} (仅支持 oracle 专属特性)"
            return result
        try:
            from monitor.db_connector import get_connector
            if not ctx.db_connector:
                ctx.db_connector = get_connector(ctx.db_config)
            conn = ctx.db_connector.connect()
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT grantee, admin_option
                    FROM dba_role_privs
                    WHERE granted_role = 'DBA' AND grantee NOT IN ('SYS', 'SYSTEM')
                """)
                cols = [c[0].lower() for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                result.metrics["dba_users"] = rows
                cnt = len(rows)
                if cnt >= 20:
                    result.status = "critical"
                    result.severity = "critical"
                elif cnt >= 10:
                    result.status = "warning"
                    result.severity = "warning"
                if cnt >= 10:
                    result.findings.append({
                        "type": "excessive_dba_users",
                        "count": cnt,
                        "details": rows[:5],
                        "message": f"拥有 DBA 角色特权的普通账号过多 ({cnt} 个)"
                    })
                result.summary = f"普通 DBA 账号 {cnt} 个"
            finally:
                ctx.db_connector.close()
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result


# ============================================================================
# TDSQL 专项检测 (基于真实 proxy + set 架构, 2026-07)
# 数据来源: 采集器写入 MonitorLog 的最新拓扑快照 (checkers/tdsql.py),
# 避免巡检再走一次高延迟互联网链路。
# ============================================================================

class TDSQLDetector:
    """TDSQL 专项巡检: set 主备健康 / proxy 网关 / 分片表配置"""

    @staticmethod
    def _latest_snapshot(db_config):
        """读取该实例最近一次采集快照, 返回 (log, data_dict)"""
        from monitor.models import MonitorLog
        log = MonitorLog.objects.filter(config=db_config).order_by('-create_time').first()
        if not log:
            return None, {}
        data = log.message
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        return log, data if isinstance(data, dict) else {}

    @staticmethod
    def detect_tdsql_set_health(ctx: DetectionContext) -> DetectionResult:
        """set 主备健康: 主节点缺失/状态异常 → critical; 无备节点单点 → warning"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="tdsql_set_health",
        )
        try:
            log, data = TDSQLDetector._latest_snapshot(ctx.db_config)
            if log is None:
                result.status = "skip"
                result.summary = "无采集快照, 请先运行 start_monitor"
                return result
            if log.status == 'DOWN':
                result.status = "critical"
                result.severity = "critical"
                result.summary = "实例最近一次采集为 DOWN"
                result.findings.append({"type": "instance_down",
                                        "message": "实例连接失败"})
                return result

            sets = data.get('tdsql_sets') or []
            health = data.get('tdsql_cluster_health', 'UNKNOWN')
            result.metrics["set_count"] = len(sets)
            result.metrics["cluster_health"] = health

            if not sets:
                result.status = "warning"
                result.severity = "warning"
                result.summary = "未获取到 set 拓扑 (网关命令不可用?)"
                return result

            for s in sets:
                label = s.get('alias') or s.get('set_name', '')
                if not s.get('master_ip'):
                    result.status = "critical"
                    result.severity = "critical"
                    result.findings.append({
                        "type": "no_master", "set": label,
                        "message": f"set {label} 无主节点",
                    })
                elif str(s.get('status', '0')) not in ('', '0'):
                    if result.status != "critical":
                        result.status = "warning"
                        result.severity = "warning"
                    result.findings.append({
                        "type": "set_abnormal", "set": label,
                        "message": f"set {label} 状态异常({s.get('status')})",
                    })
                if s.get('slave_count', 0) == 0:
                    if result.status == "ok":
                        result.status = "warning"
                        result.severity = "warning"
                    result.findings.append({
                        "type": "single_point", "set": label,
                        "message": f"set {label} 无备节点, 存在单点风险",
                    })
            healthy = sum(1 for s in sets if s.get('is_healthy'))
            result.summary = (
                f"集群 {health}: {healthy}/{len(sets)} 个 set 健康"
                + (f", {len(result.findings)} 项发现" if result.findings else "")
            )
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_tdsql_proxy(ctx: DetectionContext) -> DetectionResult:
        """proxy 网关: 网关命令可用性 / 网关节点信息"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="tdsql_proxy",
        )
        try:
            log, data = TDSQLDetector._latest_snapshot(ctx.db_config)
            if log is None:
                result.status = "skip"
                result.summary = "无采集快照"
                return result
            conn_type = data.get('tdsql_conn_type', 'direct')
            endpoints = data.get('tdsql_proxy_endpoints') or []
            result.metrics["conn_type"] = conn_type
            result.metrics["proxy_version"] = data.get('tdsql_proxy_version', 'N/A')
            result.metrics["endpoint_count"] = len(endpoints)
            result.metrics["rw_split"] = data.get('tdsql_rw_split')
            if conn_type != 'proxy':
                result.status = "warning"
                result.severity = "warning"
                result.findings.append({
                    "type": "proxy_unavailable",
                    "message": "/*proxy*/ 网关命令不可用, 无法获取集群拓扑",
                })
                result.summary = "网关命令不可用"
            else:
                eps = ', '.join(e.get('endpoint', '') for e in endpoints) or '未观测到'
                result.summary = (
                    f"网关正常 ({data.get('tdsql_proxy_version', 'N/A')}), "
                    f"观测到网关节点: {eps}"
                )
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    @staticmethod
    def detect_tdsql_shardkey(ctx: DetectionContext) -> DetectionResult:
        """分片表配置 (仅 groupshard): 广播表占比 / 分片键覆盖"""
        result = DetectionResult(
            item_id=ctx.item.get("item_id", ""),
            item_title=ctx.item.get("title", ""),
            detection_method="tdsql_shardkey",
        )
        try:
            log, data = TDSQLDetector._latest_snapshot(ctx.db_config)
            if log is None:
                result.status = "skip"
                result.summary = "无采集快照"
                return result
            if data.get('tdsql_mode') != 'groupshard':
                result.status = "skip"
                result.summary = "非分布式(groupshard)实例, 跳过"
                return result
            tables = data.get('tdsql_shard_tables') or []
            if not tables:
                result.status = "warning"
                result.severity = "warning"
                result.summary = "未获取到分片表配置 (show shardkey 无数据)"
                return result
            sharded = [t for t in tables if t.get('table_type') == '分片表']
            broadcast = [t for t in tables if t.get('table_type') == '广播表']
            result.metrics["table_count"] = len(tables)
            result.metrics["sharded_count"] = len(sharded)
            result.metrics["broadcast_count"] = len(broadcast)
            broadcast_pct = round(len(broadcast) / len(tables) * 100, 1)
            result.metrics["broadcast_pct"] = broadcast_pct
            # 广播表全量存在于每个 set, 占比过高说明分片设计可能有问题
            if broadcast_pct >= 50 and len(tables) >= 5:
                result.status = "warning"
                result.severity = "warning"
                result.findings.append({
                    "type": "broadcast_heavy",
                    "message": f"广播表占比 {broadcast_pct}% 偏高, "
                               f"数据会在每个 set 全量冗余",
                })
            result.summary = (
                f"共 {len(tables)} 张表: 分片表 {len(sharded)}, "
                f"广播表 {len(broadcast)} ({broadcast_pct}%)"
            )
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
    # TDSQL 专项 (基于真实 proxy + set 架构)
    "INS-TDSQL-SHARD": TDSQLDetector.detect_tdsql_set_health,
    "INS-TDSQL-PROXY": TDSQLDetector.detect_tdsql_proxy,
    "INS-TDSQL-SHARDKEY": TDSQLDetector.detect_tdsql_shardkey,

    # DB-016 010B 首批别名接入 (8 项同名未注册方法接入)
    "INS-COM-INVALID-OBJ": GenericDetector.detect_invalid_objects,
    "INS-COM-RECYCLEBIN": GenericDetector.detect_recyclebin,
    "INS-COM-LONG-TXN": GenericDetector.detect_long_transactions,
    "INS-COM-FK-NO-INDEX": GenericDetector.detect_fk_no_index,
    "INS-COM-STAT-STALE": GenericDetector.detect_stale_stats,
    "INS-COM-BACKUP": GenericDetector.detect_backup_status,
    "INS-COM-FAILED-LOGIN": GenericDetector.detect_failed_logins,
    "INS-ORA-SCN-HEADROOM": GenericDetector.detect_scn_headroom,
# DB-022 010B 补实现上半 23 项
    "INS-COM-USER-LOCKED": GenericDetector.detect_locked_users,
    "INS-COM-PWD-EXPIRE": GenericDetector.detect_expiring_passwords,
    "INS-COM-CAP-7D": GenericDetector.detect_7d_capacity,
    "INS-COM-CAP-30D": GenericDetector.detect_30d_capacity,
    "INS-COM-DEADLOCK-24H": GenericDetector.detect_deadlock_count,
    "INS-COM-MISSING-INDEX": GenericDetector.detect_missing_indexes,
    "INS-COM-PART-NEEDED": GenericDetector.detect_large_table_not_partitioned,
    "INS-COM-SPARSE-INDEX": GenericDetector.detect_sparse_index,
    "INS-COM-PARAM-DEV": GenericDetector.detect_param_deviation,
    "INS-COM-ARCH-GAP": GenericDetector.detect_archive_gap,
    "INS-COM-LOCK-WAIT": GenericDetector.detect_lock_waits,
    "INS-COM-BLOCK-SESSION": GenericDetector.detect_blocked_sessions,
    "INS-COM-OPEN-CURSOR": GenericDetector.detect_open_cursors,
    "INS-COM-TEMP-USAGE": GenericDetector.detect_temp_usage,
    "INS-COM-UNDO-USAGE": GenericDetector.detect_undo_usage,
    "INS-ORA-AWR-TOP-EVT": GenericDetector.detect_awr_top_event,
    "INS-ORA-AWR-TOP-SQL": GenericDetector.detect_awr_top_sql,
    "INS-ORA-STA-OUTPUT": GenericDetector.detect_sta_recommendations,
    "INS-ORA-SEG-ADV": GenericDetector.detect_segment_advisor,
    "INS-ORA-FRA": GenericDetector.detect_fra_usage,
    "INS-ORA-CTRL-BAK": GenericDetector.detect_ctrlfile_backup,
    "INS-ORA-ARCH-DELETE": GenericDetector.detect_arch_deletion_policy,
    "INS-ORA-DBA-USERS": GenericDetector.detect_dba_users,
}


def _register_db023_detectors():
    """登记 DB-023 下半 34 项。

    放在字面量外面，避免和 DB-022 同时改上面这张表时互相覆盖。
    执行入口仍是 InspectionExecutor._run_single，不新开通道。
    """
    from monitor.inspection_detectors_010b import DB023_DETECTORS

    overlap = sorted(set(DETECTOR_REGISTRY).intersection(DB023_DETECTORS))
    if overlap:
        raise RuntimeError(f'DB-023 注册键与既有检测器冲突: {overlap}')
    DETECTOR_REGISTRY.update(DB023_DETECTORS)


_register_db023_detectors()


# ============================================================================
# 巡检执行器
# ============================================================================

class InspectionExecutor:
    """巡检执行器

    用法:
        # 方式 1: 直接指定实例执行
        executor = InspectionExecutor()
        run_id = executor.run_for_db(db_config, level="daily")

        # 方式 2: 面向对象契约执行
        executor = InspectionExecutor(db_config, level="daily")
        result = executor.run()

        # 方式 3: 批量执行
        run_ids = InspectionExecutor().run_for_all_dbs(level="daily")
    """

    DEFAULT_TIMEOUT = 30
    MAX_WORKERS = 4

    def __init__(self, db_config=None, level: str = "daily",
                 max_workers: int = MAX_WORKERS, default_timeout: int = DEFAULT_TIMEOUT,
                 triggered_by: str = "manual", **kwargs):
        # 兼容旧签名 InspectionExecutor(max_workers, default_timeout)
        if isinstance(db_config, int):
            self.max_workers = db_config
            self.default_timeout = level if isinstance(level, int) else default_timeout
            self.db_config = None
            self.level = "daily"
            self.triggered_by = triggered_by
        else:
            self.db_config = db_config
            self.level = level
            self.max_workers = kwargs.get("max_workers", max_workers)
            self.default_timeout = kwargs.get("default_timeout", default_timeout)
            self.triggered_by = triggered_by
        self._logger = logging.getLogger("monitor.inspection_executor")

    def run(self) -> Dict[str, Any]:
        """针对构造时传入的 db_config 执行巡检并返回结果字典"""
        if not self.db_config:
            raise ValueError("InspectionExecutor.run() 需要 db_config 实例，或改用 run_for_db(db_config)")
        run_id = self.run_for_db(self.db_config, level=self.level, triggered_by=self.triggered_by)
        from monitor.models import InspectionRun
        run = InspectionRun.objects.get(run_id=run_id)
        ret = {
            'run_id': run.run_id,
            'status': run.status,
            'total_items': run.total_items,
            'executed_items': run.executed_items,
            'passed_items': run.passed_items,
            'failed_items': run.failed_items,
            'error_items': run.error_items,
            'critical_count': run.critical_count,
            'error_count': run.error_count,
            'warn_count': run.warn_count,
            'info_count': run.info_count,
            'total_risk_score': run.total_risk_score,
            'summary': run.summary or {},
        }
        if (run.summary or {}).get("conclusion"):
            ret['conclusion'] = run.summary["conclusion"]
        return ret

    def run_for_db(self, db_config, level: str = "daily",
                   item_ids: Optional[List[str]] = None,
                   triggered_by: str = "scheduler") -> str:
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
        run_id = f"INSP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}-{db_config.id}"
        run = InspectionRun.objects.create(
            run_id=run_id,
            level=level,
            db_config=db_config,
            status="running",
            started_at=timezone.now(),
            total_items=len(items),
            triggered_by=triggered_by,
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
        # item_id -> 定义(dict) 与 -> ItemModel 实例, 用于补 category/recommendation/FK
        item_def = {it.get("item_id"): it for it in items}
        item_objs = {o.item_id: o for o in ItemModel.objects.filter(
            item_id__in=list(item_def.keys()))}
        # 单项风险权重: 严重 10 / 警告 3 / 错误 5
        risk_weight = {"critical": 10.0, "warning": 3.0, "error": 5.0}

        crit = warn = ok = err = skip = 0
        total_risk = 0.0
        for r in results:
            if r.status == "critical":
                crit += 1
            elif r.status == "warning":
                warn += 1
            elif r.status == "ok":
                ok += 1
            elif r.status == "skip":
                skip += 1
            else:
                err += 1

            # 跳过项不落库(没有实际执行)
            if r.status == "skip":
                continue

            defn = item_def.get(r.item_id, {})
            score = risk_weight.get(r.status, 0.0)
            total_risk += score
            InspectionFinding.objects.create(
                run=run,
                item=item_objs.get(r.item_id),
                item_code=r.item_id,
                title=r.item_title or defn.get("title", ""),
                category=defn.get("category", ""),
                severity=r.severity,
                risk_score=score,
                raw_data={
                    "summary": r.summary,
                    "findings": r.findings,
                    "metrics": r.metrics,
                    "error": r.error,
                    "detection_method": r.detection_method,
                    "duration_ms": r.duration_ms,
                    "confidence": r.confidence,
                },
                recommendation=defn.get("recommendation", ""),
                auto_fixable=r.auto_fixable,
                status=r.status,
            )

        # 6) 收尾 Run
        executed = ok + warn + crit + err
        run.status = "completed"
        run.finished_at = timezone.now()
        run.executed_items = executed
        run.passed_items = ok
        run.failed_items = warn + crit
        run.error_items = err
        run.critical_count = crit
        run.warn_count = warn
        run.error_count = err
        run.info_count = skip
        run.total_risk_score = round(total_risk, 1)
        run.duration_sec = round(time.time() - start_ts, 1)
        coverage_status, coverage_rate = self._calc_coverage_status(ok, warn, crit, err, skip)
        health_score = self._calc_health_score(ok, warn, crit, err, skip)
        run.summary = {
            "health_score": health_score,
            "coverage_status": coverage_status,
            "coverage_rate": coverage_rate,
            "total_items": executed + skip,
            "executed_items": executed,
            "skipped": skip,
        }

        # 针对达梦实例，缺 dmPython 驱动时的可见信号：在巡检结论和 findings 中明确注明
        if db_config.db_type == "dm":
            dm_driver_missing = False
            try:
                import dmPython
            except ImportError:
                dm_driver_missing = True

            if dm_driver_missing:
                msg = "DM 检测器未注册：缺 dmPython"
                run.summary["conclusion"] = msg
                run.summary["warning"] = msg
                run.summary["driver_status"] = "missing_dmPython"
                if not run.error_message:
                    run.error_message = msg
                InspectionFinding.objects.create(
                    run=run,
                    item=None,
                    item_code="INS-DM-DRIVER-MISSING",
                    title="DM 检测器未注册",
                    category="environment",
                    severity="warning",
                    risk_score=3.0,
                    raw_data={"summary": msg, "findings": [], "error": msg},
                    recommendation="达梦检测器需要 dmPython 原生驱动（需达梦官方授权安装包安装）。请联系管理员获取并安装 dmPython。",
                    status="warning",
                )
                warn += 1
                total_risk += 3.0
                run.warn_count = warn
                run.failed_items = warn + crit
                run.total_risk_score = round(total_risk, 1)

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
    def _calc_coverage_status(ok: int, warn: int, crit: int, err: int, skip: int) -> Tuple[str, float]:
        """根据已执行项与跳过项计算覆盖状态与覆盖率

        返回值: (status, rate_percentage)
        - status 取值:
          - "unassessed": 未执行任何项 (executed == 0)
          - "insufficient_coverage": 执行项不足 (skip > 0 且 skip / (executed + skip) > 0.5)
          - "normal": 正常执行
        """
        executed = ok + warn + crit + err
        total = executed + skip
        if total == 0:
            return "unassessed", 0.0
        rate = round(executed / total * 100, 1)
        if executed == 0:
            return "unassessed", 0.0
        if skip > 0 and (skip / total) > 0.5:
            return "insufficient_coverage", rate
        return "normal", rate

    @staticmethod
    def _calc_health_score(ok: int, warn: int, crit: int, err: int, skip: int = 0) -> float:
        """计算健康分(0-100)"""
        executed = ok + warn + crit + err
        if executed == 0:
            return 0.0
        score = (ok * 1.0 + warn * 0.6 + crit * 0.2 + err * 0.0) / executed * 100
        return round(score, 1)

    @staticmethod
    def _sync_items_to_db(items: List[Dict[str, Any]]):
        """把内存中的巡检项定义同步到 DB"""
        from monitor.models import InspectionItem as ItemModel
        for it in items:
            db_types = it.get("applicable_db_types", [])
            obj, created = ItemModel.objects.update_or_create(
                item_id=it.get("item_id"),
                defaults={
                    "title": it.get("title", ""),
                    "category": it.get("category", ""),
                    "level": it.get("level", "daily"),
                    "severity": it.get("severity", "warn"),
                    "description": it.get("description", ""),
                    "detect_method": it.get("detect_method", ""),
                    "threshold": it.get("threshold", {}),
                    "recommendation": it.get("recommendation", ""),
                    "auto_fixable": it.get("auto_fixable", False),
                    "auto_fix_sql": it.get("auto_fix_sql", ""),
                },
            )
            # 4NF: 子表关联属性在主模型落库后单独赋值，避免 setter 在 __init__ 中提前 save 触发重复插入冲突
            if db_types:
                obj.applicable_db_types = db_types


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
