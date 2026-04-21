from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import datetime
import json

from apscheduler.schedulers.blocking import BlockingScheduler
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
import oracledb
import psycopg2
import pymysql
import pyodbc

from monitor.alert_manager import AlertManager
from monitor.alert_engine import AlertEngine  # Phase 3 智能告警引擎
from monitor.baseline_engine import BaselineEngine
from monitor.rca_engine import RCAEngine
from monitor.capacity_engine import CapacityEngine
from monitor.health_engine import HealthEngine
from monitor.models import DatabaseConfig, MonitorLog
from monitor.pg_capacity import postgresql_db_used_pct

# 单次采集任务超时（秒）：超过此时间的采集视为失败，记 DOWN，不阻塞其他任务。
COLLECT_TIMEOUT_SEC = getattr(settings, "COLLECT_TIMEOUT_SEC", 15)
# 并发采集线程数。
COLLECT_WORKERS = getattr(settings, "COLLECT_WORKERS", 20)

# 阈值配置。
TBS_THRESHOLD = 90
LOCK_TIME_THRESHOLD = 10
CONN_THRESHOLD_PCT = 80

# Phase 2 智能引擎开关
ENABLE_PHASE2_ENGINES = getattr(settings, "ENABLE_PHASE2_ENGINES", True)
CAPACITY_CHECK_INTERVAL_HOURS = getattr(settings, "CAPACITY_CHECK_INTERVAL_HOURS", 24)  # 容量预测检查间隔
HEALTH_CHECK_INTERVAL_HOURS = getattr(settings, "HEALTH_CHECK_INTERVAL_HOURS", 1)  # 健康评分检查间隔

# ==========================================
# 通用监控数据采集器基# ==========================================
class BaseDBChecker:
    """数据库检查器基类"""
    
    def __init__(self, command_instance):
        self.cmd = command_instance
    
    def get_connection(self, config):
        """获取数据库连- 子类实现"""
        raise NotImplementedError
    
    def collect_metrics(self, config, conn):
        """采集指标 - 子类实现"""
        raise NotImplementedError
    
    def check(self, config):
        """统一检查入"""
        status = 'UP'
        result_data = {}
        conn = None
        
        try:
            conn = self.get_connection(config)
            result_data = self.collect_metrics(config, conn)
            print(f"  {self.db_label()} [{config.name}]: 正常")
        except Exception as e:
            status = 'DOWN'
            result_data = {"error": str(e)}
            print(f"  X {self.db_label()} [{config.name}]: 失败 - {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
        
        self.cmd.process_result(config, status, result_data)
    
    def db_label(self):
        """返回数据库类型标识"""
        return self.__class__.__name__.replace('Checker', '')


# ==========================================
# Oracle 检查器 (RAC 兼容)
# ==========================================
class OracleChecker(BaseDBChecker):
    
    def get_connection(self, config):
        target_service = config.service_name if config.service_name else 'orcl'
        return oracledb.connect(
            user=config.username, password=config.get_password(),
            host=config.host, port=config.port, service_name=target_service
        )
    
    def collect_metrics(self, config, conn):
        cursor = conn.cursor()
        
        # =============================================
        # 1. 基础信息 (basic)
        # =============================================
        cursor.execute("SELECT BANNER FROM v$version WHERE ROWNUM = 1")
        version = cursor.fetchone()[0]
        
        cursor.execute("SELECT instance_name, host_name, version, TO_CHAR(startup_time, 'YYYY-MM-DD HH24:MI:SS'), archiver FROM v$instance")
        inst_row = cursor.fetchone()
        instance_name = inst_row[0]
        host_name = inst_row[1]
        db_version = inst_row[2]
        startup_time = inst_row[3]
        archiver = inst_row[4]
        
        cursor.execute("SELECT name, db_unique_name, open_mode, database_role, log_mode FROM v$database")
        db_row = cursor.fetchone()
        db_name = db_row[0]
        db_unique_name = db_row[1]
        open_mode = db_row[2]
        database_role = db_row[3]
        log_mode = db_row[4]
        
        # 启动时间 (RAC 兼容)
        try:
            cursor.execute("SELECT (SYSDATE - startup_time) * 24 * 60 * 60 FROM gv$instance WHERE inst_id = 1")
            uptime = int(cursor.fetchone()[0])
        except:
            cursor.execute("SELECT (SYSDATE - startup_time) * 24 * 60 * 60 FROM v$instance")
            uptime = int(cursor.fetchone()[0])

        # =============================================
        # 2. 连接与会话 (session)
        # =============================================
        cursor.execute("SELECT count(*) FROM gv$session WHERE status = 'ACTIVE'")
        active_sessions = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM gv$session WHERE status = 'INACTIVE'")
        inactive_sessions = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM gv$session WHERE type = 'BACKGROUND'")
        background_sessions = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM gv$session")
        total_sessions = cursor.fetchone()[0]
        
        cursor.execute("SELECT VALUE FROM v$parameter WHERE name = 'processes'")
        max_conn = int(cursor.fetchone()[0])
        conn_usage_pct = round((active_sessions / max_conn) * 100, 2) if max_conn > 0 else 0

        # =============================================
        # 3. 空间使用 (space)
        # =============================================
        sql_tbs = """
            SELECT
                df.tablespace_name,
                ROUND(df.total_space / 1024 / 1024, 2),
                ROUND((df.total_space - NVL(fs.free_space, 0)) / 1024 / 1024, 2),
                ROUND((1 - NVL(fs.free_space, 0) / df.total_space) * 100, 2)
            FROM
                (SELECT tablespace_name, SUM(bytes) total_space FROM dba_data_files GROUP BY tablespace_name) df,
                (SELECT tablespace_name, SUM(bytes) free_space FROM dba_free_space GROUP BY tablespace_name) fs
            WHERE df.tablespace_name = fs.tablespace_name(+)
        """
        cursor.execute(sql_tbs)
        tablespaces = []
        for row in cursor.fetchall():
            tablespaces.append({
                "name": row[0], 
                "total_mb": float(row[1]), 
                "used_mb": float(row[2]), 
                "used_pct": float(row[3])
            })
        
        # 临时表空间
        cursor.execute("""
            SELECT tablespace_name, SUM(bytes) / 1024 / 1024 as size_mb
            FROM dba_temp_files GROUP BY tablespace_name
        """)
        temp_tablespaces = []
        for row in cursor.fetchall():
            temp_tablespaces.append({
                "name": row[0],
                "size_mb": float(row[1])
            })
        
        # UNDO表空间
        cursor.execute("""
            SELECT tablespace_name, status, SUM(bytes) / 1024 / 1024 as size_mb
            FROM dba_undo_extents GROUP BY tablespace_name, status
        """)
        undo_tablespaces = []
        for row in cursor.fetchall():
            undo_tablespaces.append({
                "name": row[0],
                "status": row[1],
                "size_mb": float(row[2])
            })
        
        # 数据文件统计
        cursor.execute("SELECT count(*), SUM(bytes) / 1024 / 1024 / 1024 FROM dba_data_files")
        df_row = cursor.fetchone()
        datafile_count = df_row[0]
        datafile_size_total_gb = float(df_row[1])

        # =============================================
        # 4. 性能指标 (performance)
        # =============================================
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'execute count'")
        exec_count = int(cursor.fetchone()[0])
        qps = round(exec_count / uptime, 2) if uptime > 0 else 0
        
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'user commits'")
        commits = int(cursor.fetchone()[0])
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'user rollbacks'")
        rollbacks = int(cursor.fetchone()[0])
        tps = round((commits + rollbacks) / uptime, 2) if uptime > 0 else 0
        
        # 逻辑读/物理读
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'session logical reads'")
        try:
            logical_reads = int(cursor.fetchone()[0])
        except:
            logical_reads = 0
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'physical reads'")
        try:
            physical_reads = int(cursor.fetchone()[0])
        except:
            physical_reads = 0
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'physical writes'")
        try:
            physical_writes = int(cursor.fetchone()[0])
        except:
            physical_writes = 0
        
        # Redo产生量
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'redo size'")
        try:
            redo_generation_bytes = int(cursor.fetchone()[0])
        except:
            redo_generation_bytes = 0
        
        # 解析统计
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'parse count (total)'")
        try:
            parse_count_total = int(cursor.fetchone()[0])
        except:
            parse_count_total = 0
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'parse count (hard)'")
        try:
            parse_count_hard = int(cursor.fetchone()[0])
        except:
            parse_count_hard = 0
        
        # =============================================
        # 5. 锁等待 (wait) - 增强
        # =============================================
        sql_lock = f"""
            SELECT
                blocker.inst_id || ':' || blocker.sid || ',' || blocker.serial# as blocker_info,
                blocker.username as blocker_user,
                waiter.inst_id || ':' || waiter.sid || ',' || waiter.serial# as waiter_info,
                waiter.username as waiter_user,
                waiter.seconds_in_wait as wait_sec,
                waiter.event as wait_event,
                blocker.event as blocker_event
            FROM gv$session blocker, gv$session waiter
            WHERE waiter.blocking_session = blocker.sid
              AND waiter.blocking_instance = blocker.inst_id
              AND waiter.seconds_in_wait > {LOCK_TIME_THRESHOLD}
        """
        cursor.execute(sql_lock)
        locks = []
        for row in cursor.fetchall():
            locks.append({
                "blocker_id": row[0], 
                "blocker_user": row[1] or 'N/A',
                "waiter_id": row[2],
                "waiter_user": row[3] or 'N/A',
                "seconds": int(row[4]),
                "wait_event": row[5] or 'N/A',
                "blocker_event": row[6] or 'N/A'
            })
        
        # 锁等待数量
        cursor.execute("SELECT count(*) FROM gv$session WHERE blocking_session IS NOT NULL AND seconds_in_wait > 0")
        lock_wait_count = int(cursor.fetchone()[0])
        
        # Top等待事件
        cursor.execute("""
            SELECT event, total_waits, time_waited, average_wait
            FROM v$system_event
            WHERE total_waits > 0
            ORDER BY time_waited DESC
            FETCH FIRST 10 ROWS ONLY
        """)
        top_wait_events = []
        for row in cursor.fetchall():
            top_wait_events.append({
                "event": row[0],
                "total_waits": int(row[1]),
                "time_waited": int(row[2]),
                "average_wait": round(float(row[3]), 2)
            })

        # =============================================
        # 6. 会话详情 (session_detail) - P0新增
        # =============================================
        cursor.execute("""
            SELECT 
                s.inst_id || ':' || s.sid || ',' || s.serial# as sid_serial,
                s.username,
                s.status,
                s.program,
                s.machine,
                s.terminal,
                s.event as wait_event,
                s.seconds_in_wait,
                s.last_call_et,
                s.sql_id,
                s.p1 || ',' || s.p2 || ',' || s.p3 as params
            FROM gv$session s
            WHERE s.type != 'BACKGROUND'
            ORDER BY s.status, s.last_call_et DESC
        """)
        session_list = []
        for row in cursor.fetchall():
            session_list.append({
                "sid_serial": row[0],
                "username": row[1] or 'N/A',
                "status": row[2],
                "program": row[3] or 'N/A',
                "machine": row[4] or 'N/A',
                "terminal": row[5] or 'N/A',
                "wait_event": row[6] or 'N/A',
                "seconds_in_wait": int(row[7]) if row[7] else 0,
                "last_call_et": int(row[8]) if row[8] else 0,
                "sql_id": row[9] or 'N/A',
                "params": row[10] or 'N/A'
            })
        
        # 被阻塞的会话
        cursor.execute("""
            SELECT 
                s.inst_id || ':' || s.sid || ',' || s.serial# as sid_serial,
                s.username,
                s.program,
                s.event,
                s.seconds_in_wait,
                s.sql_id,
                s.row_wait_obj#,
                s.p1text,
                s.p1,
                s.p2text,
                s.p2
            FROM gv$session s
            WHERE s.blocking_session IS NOT NULL
            ORDER BY s.seconds_in_wait DESC
        """)
        blocked_sessions = []
        for row in cursor.fetchall():
            blocked_sessions.append({
                "sid_serial": row[0],
                "username": row[1] or 'N/A',
                "program": row[2] or 'N/A',
                "event": row[3] or 'N/A',
                "seconds_in_wait": int(row[4]) if row[4] else 0,
                "sql_id": row[5] or 'N/A',
                "object": row[6],
                "p1text": row[7] or 'N/A',
                "p1": row[8],
                "p2text": row[9] or 'N/A',
                "p2": row[10]
            })

        # =============================================
        # 7. SQL统计 (sql) - P0新增
        # =============================================
        # 当前慢查询
        cursor.execute("""
            SELECT count(*)
            FROM gv$session s
            WHERE s.status = 'ACTIVE'
              AND s.sql_id IS NOT NULL
              AND s.last_call_et > 10
        """)
        slow_queries_active = int(cursor.fetchone()[0])
        
        # Top SQL by buffer gets
        cursor.execute("""
            SELECT sql_id, sql_text, buffer_gets, disk_reads, executions, 
                   ROUND(buffer_gets / DECODE(executions, 0, 1, executions), 2) as buffer_gets_per_exec
            FROM (SELECT * FROM v$sql ORDER BY buffer_gets DESC)
            WHERE ROWNUM <= 10
        """)
        top_sql_by_buffer_gets = []
        for row in cursor.fetchall():
            top_sql_by_buffer_gets.append({
                "sql_id": row[0],
                "sql_text": (row[1] or '')[:200],
                "buffer_gets": int(row[2]),
                "disk_reads": int(row[3]),
                "executions": int(row[4]),
                "buffer_gets_per_exec": float(row[5])
            })
        
        # Top SQL by disk reads
        cursor.execute("""
            SELECT sql_id, sql_text, buffer_gets, disk_reads, executions,
                   ROUND(disk_reads / DECODE(executions, 0, 1, executions), 2) as disk_reads_per_exec
            FROM (SELECT * FROM v$sql ORDER BY disk_reads DESC)
            WHERE ROWNUM <= 10
        """)
        top_sql_by_disk_reads = []
        for row in cursor.fetchall():
            top_sql_by_disk_reads.append({
                "sql_id": row[0],
                "sql_text": (row[1] or '')[:200],
                "buffer_gets": int(row[2]),
                "disk_reads": int(row[3]),
                "executions": int(row[4]),
                "disk_reads_per_exec": float(row[5])
            })
        
        # Top SQL by executions
        cursor.execute("""
            SELECT sql_id, sql_text, buffer_gets, executions,
                   ROUND(buffer_gets / DECODE(executions, 0, 1, executions), 2) as gets_per_exec
            FROM (SELECT * FROM v$sql ORDER BY executions DESC)
            WHERE ROWNUM <= 10
        """)
        top_sql_by_executions = []
        for row in cursor.fetchall():
            top_sql_by_executions.append({
                "sql_id": row[0],
                "sql_text": (row[1] or '')[:200],
                "buffer_gets": int(row[2]),
                "executions": int(row[3]),
                "gets_per_exec": float(row[4])
            })

        # =============================================
        # 8. 缓冲池 (buffer) - P0新增
        # =============================================
        cursor.execute("SELECT name, block_size, current_size, buffers FROM v$buffer_pool")
        buffer_pools = []
        for row in cursor.fetchall():
            buffer_pools.append({
                "name": row[0],
                "block_size": int(row[1]),
                "current_size_mb": float(row[2]),
                "buffers": int(row[3])
            })
        
        # SGA信息
        cursor.execute("SELECT name, ROUND(sum(bytes)/1024/1024, 2) FROM v$sgastat WHERE name = 'buffer cache' GROUP BY name")
        try:
            buffer_cache_mb = float(cursor.fetchone()[1])
        except:
            buffer_cache_mb = 0
        
        cursor.execute("SELECT name, ROUND(sum(bytes)/1024/1024, 2) FROM v$sgastat WHERE name = 'shared pool' GROUP BY name")
        try:
            shared_pool_mb = float(cursor.fetchone()[1])
        except:
            shared_pool_mb = 0
        
        cursor.execute("SELECT name, ROUND(sum(bytes)/1024/1024, 2) FROM v$sgastat WHERE name = 'java pool' GROUP BY name")
        try:
            java_pool_mb = float(cursor.fetchone()[1])
        except:
            java_pool_mb = 0
        
        cursor.execute("SELECT name, ROUND(sum(bytes)/1024/1024, 2) FROM v$sgastat WHERE name = 'large pool' GROUP BY name")
        try:
            large_pool_mb = float(cursor.fetchone()[1])
        except:
            large_pool_mb = 0
        
        # PGA信息 - 使用v$pgastat
        try:
            cursor.execute("SELECT ROUND(value/1024/1024, 2) FROM v$pgastat WHERE name = 'total memory allocated'")
            pga_used_mb = float(cursor.fetchone()[0])
        except:
            pga_used_mb = 0
        
        # 命中率统计
        try:
            cursor.execute("""
                SELECT 
                    ROUND((1 - physical_reads / (session_logical_reads + 1)) * 100, 2) as buffer_hit_ratio
                FROM v$sysstat WHERE name = 'session logical reads'
            """)
            buffer_hit_ratio = float(cursor.fetchone()[0])
        except:
            buffer_hit_ratio = 0
        
        try:
            cursor.execute("""
                SELECT ROUND(SUM(pins) / DECODE(SUM(pins) + SUM(reloads), 0, 1, SUM(pins) + SUM(reloads)) * 100, 2)
                FROM v$librarycache
            """)
            library_cache_hit_ratio = float(cursor.fetchone()[0])
        except:
            library_cache_hit_ratio = 0
        
        # CPU和DB Time
        try:
            cursor.execute("SELECT value FROM v$sys_time_model WHERE stat_name = 'DB CPU'")
            cpu_used_seconds = int(cursor.fetchone()[0]) / 1000000
        except:
            cpu_used_seconds = 0
        try:
            cursor.execute("SELECT value FROM v$sys_time_model WHERE stat_name = 'DB time'")
            db_time_seconds = int(cursor.fetchone()[0]) / 1000000
        except:
            db_time_seconds = 0

        # =============================================
        # 9. 事务统计 (transaction) - P1
        # =============================================
        cursor.execute("SELECT count(*) FROM v$transaction")
        active_transactions = int(cursor.fetchone()[0])
        
        cursor.execute("SELECT COUNT(DISTINCT s.sid) FROM gv$session s WHERE s.row_wait_obj# != -1")
        row_lock_contention = int(cursor.fetchone()[0])
        
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'user commits'")
        try:
            committed_transactions = int(cursor.fetchone()[0])
        except:
            committed_transactions = 0
        cursor.execute("SELECT value FROM v$sysstat WHERE name = 'user rollbacks'")
        try:
            rolled_back_transactions = int(cursor.fetchone()[0])
        except:
            rolled_back_transactions = 0

        # =============================================
        # 9.5 对象统计 (object) - P2补全
        # =============================================
        # 表数量
        cursor.execute("SELECT COUNT(*) FROM dba_tables WHERE owner NOT IN ('SYS', 'SYSTEM', 'OWB$', 'APPQOSSYS')")
        try:
            table_count = int(cursor.fetchone()[0])
        except:
            table_count = 0
        
        # 索引数量
        cursor.execute("SELECT COUNT(*) FROM dba_indexes WHERE owner NOT IN ('SYS', 'SYSTEM', 'OWB$', 'APPQOSSYS')")
        try:
            index_count = int(cursor.fetchone()[0])
        except:
            index_count = 0
        
        # Top 20 表大小
        cursor.execute("""
            SELECT owner, segment_name, ROUND(SUM(bytes)/1024/1024, 2) as size_mb
            FROM dba_segments
            WHERE segment_type = 'TABLE'
              AND owner NOT IN ('SYS', 'SYSTEM', 'OWB$', 'APPQOSSYS')
            GROUP BY owner, segment_name
            ORDER BY size_mb DESC
            FETCH FIRST 20 ROWS ONLY
        """)
        table_size_top20 = []
        for row in cursor.fetchall():
            table_size_top20.append({
                "owner": row[0],
                "table_name": row[1],
                "size_mb": float(row[2])
            })
        
        # Top 20 索引大小
        cursor.execute("""
            SELECT owner, segment_name, ROUND(SUM(bytes)/1024/1024, 2) as size_mb
            FROM dba_segments
            WHERE segment_type = 'INDEX'
              AND owner NOT IN ('SYS', 'SYSTEM', 'OWB$', 'APPQOSSYS')
            GROUP BY owner, segment_name
            ORDER BY size_mb DESC
            FETCH FIRST 20 ROWS ONLY
        """)
        index_size_top20 = []
        for row in cursor.fetchall():
            index_size_top20.append({
                "owner": row[0],
                "index_name": row[1],
                "size_mb": float(row[2])
            })
        
        # 统计信息过期对象
        cursor.execute("""
            SELECT owner, table_name, stale_stats
            FROM dba_tab_statistics
            WHERE stale_stats = 'YES'
              AND owner NOT IN ('SYS', 'SYSTEM', 'OWB$', 'APPQOSSYS')
            FETCH FIRST 20 ROWS ONLY
        """)
        stale_statistics = []
        for row in cursor.fetchall():
            stale_statistics.append({
                "owner": row[0],
                "table_name": row[1],
                "stale_stats": row[2]
            })
        
        # 分区数量
        cursor.execute("SELECT COUNT(*) FROM dba_part_tables WHERE owner NOT IN ('SYS', 'SYSTEM')")
        try:
            partition_count = int(cursor.fetchone()[0])
        except:
            partition_count = 0

        # =============================================
        # 10. 复制与集群 (replication) - P1
        # =============================================
        cursor.execute("SELECT COUNT(*) FROM gv$instance")
        rac_instance_count = int(cursor.fetchone()[0])
        
        cursor.execute("SELECT inst_id, instance_name, host_name, status FROM gv$instance ORDER BY inst_id")
        rac_instances = []
        for row in cursor.fetchall():
            rac_instances.append({
                "inst_id": int(row[0]),
                "instance_name": row[1],
                "host_name": row[2],
                "status": row[3]
            })
        
        # DataGuard状态
        cursor.execute("SELECT database_role, protection_mode, protection_level FROM v$database")
        try:
            dg_row = cursor.fetchone()
            dg_database_role = dg_row[0]
            dg_protection_mode = dg_row[1]
            dg_protection_level = dg_row[2]
        except:
            dg_database_role = 'N/A'
            dg_protection_mode = 'N/A'
            dg_protection_level = 'N/A'

        # =============================================
        # 10.5 RAC 互联网络 (Interconnect) - P1 新增
        # =============================================
        try:
            cursor.execute("""
                SELECT inst_id, name, ip_address, 
                       ic_bytes_sent, ic_bytes_received, 
                       ic_packets_sent, ic_packets_received
                FROM gv$cluster_interconnects
            """)
            rac_interconnects = []
            ic_bytes_sent_total = 0
            ic_bytes_received_total = 0
            ic_packets_sent_total = 0
            ic_packets_received_total = 0
            ic_errors_total = 0
            for row in cursor.fetchall():
                ic_bytes_sent_total += int(row[3]) if row[3] else 0
                ic_bytes_received_total += int(row[4]) if row[4] else 0
                ic_packets_sent_total += int(row[5]) if row[5] else 0
                ic_packets_received_total += int(row[6]) if row[6] else 0
                rac_interconnects.append({
                    "inst_id": int(row[0]) if row[0] else 0,
                    "name": row[1] or 'N/A',
                    "ip_address": row[2] or 'N/A',
                    "ic_bytes_sent": int(row[3]) if row[3] else 0,
                    "ic_bytes_received": int(row[4]) if row[4] else 0,
                    "ic_packets_sent": int(row[5]) if row[5] else 0,
                    "ic_packets_received": int(row[6]) if row[6] else 0,
                    "ic_errors": 0
                })
        except:
            rac_interconnects = []
            ic_bytes_sent_total = 0
            ic_bytes_received_total = 0
            ic_packets_sent_total = 0
            ic_packets_received_total = 0
            ic_errors_total = 0

        # =============================================
        # 10.6 RAC 缓存融合 (Cache Fusion) - P1 新增
        # =============================================
        # 全局缓存块统计
        try:
            cursor.execute("""
                SELECT inst_id, name, value
                FROM gv$sysstat 
                WHERE name LIKE 'gc%%' 
                   OR name LIKE 'global cache%%'
                ORDER BY inst_id, name
            """)
            cache_fusion_stats = []
            gc_buffer_busy_total = 0
            for row in cursor.fetchall():
                stat_name = row[1].lower() if row[1] else ''
                if 'buffer busy' in stat_name or 'gc buffer busy' in stat_name:
                    gc_buffer_busy_total += int(row[2]) if row[2] else 0
                cache_fusion_stats.append({
                    "inst_id": int(row[0]) if row[0] else 0,
                    "stat_name": row[1] or 'N/A',
                    "value": int(row[2]) if row[2] else 0
                })
        except:
            cache_fusion_stats = []
            gc_buffer_busy_total = 0

        # 全局缓存等待事件
        try:
            cursor.execute("""
                SELECT event, total_waits, time_waited
                FROM gv$system_event
                WHERE event LIKE 'gc%%' OR event LIKE 'global cache%%'
                ORDER BY time_waited DESC
            """)
            gc_wait_events = []
            for row in cursor.fetchall():
                gc_wait_events.append({
                    "event": row[0] or 'N/A',
                    "total_waits": int(row[1]) if row[1] else 0,
                    "time_waited": int(row[2]) if row[2] else 0
                })
        except:
            gc_wait_events = []

        # =============================================
        # 10.7 ADG 延迟指标 - P0 新增
        # =============================================
        cursor.execute("""
            SELECT name, value, unit, time_computed
            FROM v$dataguard_stats 
            WHERE name IN ('transport lag', 'apply lag')
        """)
        adg_lag_stats = {}
        for row in cursor.fetchall():
            name = row[0].lower().replace(' ', '_') if row[0] else 'unknown'
            adg_lag_stats[name] = {
                "value": row[1] or 'N/A',
                "unit": row[2] or 'N/A',
                "time_computed": str(row[3]) if row[3] else 'N/A'
            }
        apply_lag = adg_lag_stats.get('apply_lag', {}).get('value', 'N/A')
        transport_lag = adg_lag_stats.get('transport_lag', {}).get('value', 'N/A')

        # =============================================
        # 10.8 ADG Gap 检测 - P0 新增
        # =============================================
        cursor.execute("SELECT thread#, low_sequence#, high_sequence# FROM v$archive_gap")
        archive_gap_list = []
        archive_gap_count = 0
        for row in cursor.fetchall():
            archive_gap_count += 1
            archive_gap_list.append({
                "thread": int(row[0]) if row[0] else 0,
                "low_sequence": int(row[1]) if row[1] else 0,
                "high_sequence": int(row[2]) if row[2] else 0
            })

        # 归档目的地状态
        cursor.execute("""
            SELECT dest_id, destination, status, error, synchronized
            FROM v$archive_dest_status 
            WHERE destination IS NOT NULL
        """)
        archive_dest_status = []
        for row in cursor.fetchall():
            archive_dest_status.append({
                "dest_id": int(row[0]) if row[0] else 0,
                "destination": row[1] or 'N/A',
                "status": row[2] or 'N/A',
                "error": row[3] or 'N/A',
                "synchronized": row[4] or 'N/A'
            })

        # =============================================
        # 10.9 ADG 备库进程状态 - P0 新增
        # =============================================
        try:
            cursor.execute("""
                SELECT process, status, client_process, client_pid, sequence#,
                       BLOCK#, BLOCKS
                FROM v$managed_standby
                WHERE process IN ('MRP0', 'MRP1', 'MRP2', 'RFS', 'ARCH', 'LGWR')
                   OR process LIKE 'MRP%%'
                ORDER BY process
            """)
            adg_processes = []
            mrp_status = 'NOT_FOUND'
            rfs_status = 'NOT_FOUND'
            for row in cursor.fetchall():
                process_name = row[0] or 'N/A'
                status = row[1] or 'N/A'
                if process_name.startswith('MRP'):
                    mrp_status = status
                elif process_name == 'RFS':
                    rfs_status = status
                adg_processes.append({
                    "process": process_name,
                    "status": status,
                    "client_process": row[2] or 'N/A',
                    "client_pid": int(row[3]) if row[3] else 0,
                    "sequence": int(row[4]) if row[4] else 0,
                    "block": int(row[5]) if row[5] else 0,
                    "blocks": int(row[6]) if row[6] else 0
                })
        except:
            adg_processes = []
            mrp_status = 'NOT_FOUND'
            rfs_status = 'NOT_FOUND'

        # =============================================
        # 10.10 ADG Switchover/Failover 状态 - P1 新增
        # =============================================
        try:
            cursor.execute("SELECT switchover_status FROM v$database")
            dg_switchover_status = cursor.fetchone()[0]
        except:
            dg_switchover_status = 'N/A'

        try:
            cursor.execute("SELECT fs_failover_status, fs_failover_observed_target, fs_failover_replay_target FROM v$database")
            dg_failover_row = cursor.fetchone()
            dg_fs_failover_status = dg_failover_row[0] if dg_failover_row else 'N/A'
        except:
            dg_fs_failover_status = 'N/A'

        # =============================================
        # 11. 配置参数 (config) - P1
        # =============================================
        cursor.execute("SELECT name, value FROM v$parameter WHERE name IN ('sga_target', 'sga_max_size', 'pga_aggregate_target', 'shared_pool_size', 'db_cache_size', 'log_buffer', 'open_cursors', 'session_cached_cursors')")
        config_params = {}
        for row in cursor.fetchall():
            config_params[row[0]] = row[1]

        # =============================================
        # 12. 日志统计 (log) - P1
        # =============================================
        cursor.execute("SELECT COUNT(*) FROM v$log WHERE status = 'CURRENT'")
        log_current = int(cursor.fetchone()[0])
        cursor.execute("SELECT COUNT(*) FROM v$log WHERE status = 'ACTIVE'")
        log_active = int(cursor.fetchone()[0])
        cursor.execute("SELECT COUNT(*) FROM v$log WHERE status = 'INACTIVE'")
        log_inactive = int(cursor.fetchone()[0])
        
        cursor.execute("""
            SELECT COUNT(*) FROM v$archived_log 
            WHERE completion_time >= SYSDATE - 1
        """)
        archive_logs_1day = int(cursor.fetchone()[0])

        # =============================================
        # 13. 高可用 (ha) - P2
        # =============================================
        cursor.execute("SELECT flashback_on FROM v$database")
        try:
            flashback_on = cursor.fetchone()[0]
        except:
            flashback_on = 'N/A'

        # =============================================
        # 14. 资源限制 (resource) - P2
        # =============================================
        cursor.execute("SELECT resource_name, current_utilization, max_utilization, initial_allocation FROM v$resource_limit WHERE resource_name IN ('processes', 'sessions', 'transactions', 'locks')")
        resource_limits = []
        for row in cursor.fetchall():
            resource_limits.append({
                "resource_name": row[0],
                "current_utilization": int(row[1]),
                "max_utilization": int(row[2]),
                "initial_allocation": row[3]
            })

        # =============================================
        # 返回完整指标
        # =============================================
        return {
            # 基础信息
            "version": version[:50] + "...",
            "instance_name": instance_name,
            "host_name": host_name,
            "db_name": db_name,
            "db_unique_name": db_unique_name,
            "open_mode": open_mode,
            "database_role": database_role,
            "startup_time": startup_time,
            "archiver": archiver,
            "log_mode": log_mode,
            "uptime_seconds": uptime,
            
            # 连接会话
            "active_connections": active_sessions,
            "inactive_connections": inactive_sessions,
            "background_sessions": background_sessions,
            "total_sessions": total_sessions,
            "max_connections": max_conn,
            "conn_usage_pct": conn_usage_pct,
            
            # 空间
            "tablespaces": tablespaces,
            "temp_tablespaces": temp_tablespaces,
            "undo_tablespaces": undo_tablespaces,
            "datafile_count": datafile_count,
            "datafile_size_total_gb": datafile_size_total_gb,
            
            # 性能
            "qps": qps,
            "tps": tps,
            "logical_reads": logical_reads,
            "physical_reads": physical_reads,
            "physical_writes": physical_writes,
            "redo_generation_bytes": redo_generation_bytes,
            "parse_count_total": parse_count_total,
            "parse_count_hard": parse_count_hard,
            
            # 锁等待
            "locks": locks,
            "lock_wait_count": lock_wait_count,
            "top_wait_events": top_wait_events,
            
            # 会话详情
            "session_list": session_list,
            "blocked_sessions": blocked_sessions,
            
            # SQL统计
            "slow_queries_active": slow_queries_active,
            "top_sql_by_buffer_gets": top_sql_by_buffer_gets,
            "top_sql_by_disk_reads": top_sql_by_disk_reads,
            "top_sql_by_executions": top_sql_by_executions,
            
            # 缓冲池
            "buffer_pools": buffer_pools,
            "buffer_cache_mb": buffer_cache_mb,
            "shared_pool_mb": shared_pool_mb,
            "java_pool_mb": java_pool_mb,
            "large_pool_mb": large_pool_mb,
            "pga_used_mb": pga_used_mb,
            "buffer_hit_ratio": buffer_hit_ratio,
            "library_cache_hit_ratio": library_cache_hit_ratio,
            "cpu_used_seconds": cpu_used_seconds,
            "db_time_seconds": db_time_seconds,
            
            # 事务
            "active_transactions": active_transactions,
            "row_lock_contention": row_lock_contention,
            "committed_transactions": committed_transactions,
            "rolled_back_transactions": rolled_back_transactions,
            
            # 对象统计
            "table_count": table_count,
            "index_count": index_count,
            "table_size_top20": table_size_top20,
            "index_size_top20": index_size_top20,
            "stale_statistics": stale_statistics,
            "partition_count": partition_count,
            
            # 复制集群
            "rac_instance_count": rac_instance_count,
            "rac_instances": rac_instances,
            "dg_database_role": dg_database_role,
            "dg_protection_mode": dg_protection_mode,
            "dg_protection_level": dg_protection_level,
            
            # RAC 互联网络 (新增)
            "rac_interconnects": rac_interconnects,
            "ic_bytes_sent_total": ic_bytes_sent_total,
            "ic_bytes_received_total": ic_bytes_received_total,
            "ic_packets_sent_total": ic_packets_sent_total,
            "ic_packets_received_total": ic_packets_received_total,
            "ic_errors_total": ic_errors_total,
            
            # RAC 缓存融合 (新增)
            "cache_fusion_stats": cache_fusion_stats,
            "gc_wait_events": gc_wait_events,
            "gc_buffer_busy_total": gc_buffer_busy_total,
            
            # ADG 延迟指标 (新增)
            "apply_lag": apply_lag,
            "transport_lag": transport_lag,
            "adg_lag_stats": adg_lag_stats,
            
            # ADG Gap 检测 (新增)
            "archive_gap_count": archive_gap_count,
            "archive_gap_list": archive_gap_list,
            "archive_dest_status": archive_dest_status,
            
            # ADG 备库进程 (新增)
            "adg_processes": adg_processes,
            "mrp_status": mrp_status,
            "rfs_status": rfs_status,
            
            # ADG Switchover/Failover 状态 (新增)
            "dg_switchover_status": dg_switchover_status,
            "dg_fs_failover_status": dg_fs_failover_status,
            
            # 配置参数
            "config_params": config_params,
            
            # 日志
            "log_current": log_current,
            "log_active": log_active,
            "log_inactive": log_inactive,
            "archive_logs_1day": archive_logs_1day,
            
            # 高可用
            "flashback_on": flashback_on,
            
            # 资源限制
            "resource_limits": resource_limits
        }


# ==========================================
# MySQL 检查器 (支持 TDSQL MySQL 
# ==========================================
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
            except:
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
            except:
                threads_running = 0
            
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_cached'")
            try:
                threads_cached = int(cursor.fetchone()['Value'])
            except:
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
            except:
                innodb_data_pages = 0
                
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_data_reads'")
            try:
                innodb_data_reads = int(cursor.fetchone()['Value'])
            except:
                innodb_data_reads = 0
                
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_data_writes'")
            try:
                innodb_data_writes = int(cursor.fetchone()['Value'])
            except:
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
            except:
                com_commit = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Com_rollback'")
            try:
                com_rollback = int(cursor.fetchone()['Value'])
            except:
                com_rollback = 0
            tps = round((com_commit + com_rollback) / uptime, 2) if uptime > 0 else 0
            
            # 键缓存
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_read_requests'")
            try:
                key_read_requests = int(cursor.fetchone()['Value'])
            except:
                key_read_requests = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_reads'")
            try:
                key_reads = int(cursor.fetchone()['Value'])
            except:
                key_reads = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_write_requests'")
            try:
                key_write_requests = int(cursor.fetchone()['Value'])
            except:
                key_write_requests = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Key_writes'")
            try:
                key_writes = int(cursor.fetchone()['Value'])
            except:
                key_writes = 0
            
            # InnoDB 行列统计
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_read'")
            try:
                innodb_rows_read = int(cursor.fetchone()['Value'])
            except:
                innodb_rows_read = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_inserted'")
            try:
                innodb_rows_inserted = int(cursor.fetchone()['Value'])
            except:
                innodb_rows_inserted = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_updated'")
            try:
                innodb_rows_updated = int(cursor.fetchone()['Value'])
            except:
                innodb_rows_updated = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_deleted'")
            try:
                innodb_rows_deleted = int(cursor.fetchone()['Value'])
            except:
                innodb_rows_deleted = 0
            
            # 缓冲池
            cursor.execute("SHOW VARIABLES LIKE 'innodb_buffer_pool_size'")
            innodb_buffer_pool_size = int(cursor.fetchone()['Value'])
            innodb_buffer_pool_size_mb = round(innodb_buffer_pool_size / 1024 / 1024, 2)
            
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")
            try:
                innodb_buffer_pool_reads = int(cursor.fetchone()['Value'])
            except:
                innodb_buffer_pool_reads = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read_requests'")
            try:
                innodb_buffer_pool_read_requests = int(cursor.fetchone()['Value'])
            except:
                innodb_buffer_pool_read_requests = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_total'")
            try:
                innodb_buffer_pool_pages_total = int(cursor.fetchone()['Value'])
            except:
                innodb_buffer_pool_pages_total = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_free'")
            try:
                innodb_buffer_pool_pages_free = int(cursor.fetchone()['Value'])
            except:
                innodb_buffer_pool_pages_free = 0
            buffer_hit_ratio = round((1 - innodb_buffer_pool_reads / innodb_buffer_pool_read_requests) * 100, 2) if innodb_buffer_pool_read_requests > 0 else 0

            # =============================================
            # 5. 等待事件 (wait) - 增强
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_waits'")
            try:
                innodb_row_lock_waits = int(cursor.fetchone()['Value'])
            except:
                innodb_row_lock_waits = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_current_waits'")
            try:
                innodb_row_lock_current_waits = int(cursor.fetchone()['Value'])
            except:
                innodb_row_lock_current_waits = 0
            
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Table_locks_immediate'")
            try:
                table_locks_immediate = int(cursor.fetchone()['Value'])
            except:
                table_locks_immediate = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Table_locks_waited'")
            try:
                table_locks_waited = int(cursor.fetchone()['Value'])
            except:
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
            # 6. 会话详情 (session_detail) - P0新增
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
            # 7. SQL统计 (sql) - P0新增
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
                com_stats[row['Value']] = row['Value']

            # =============================================
            # 8. 复制与集群 (replication) - P1 增强
            # =============================================
            cursor.execute("SHOW MASTER STATUS")
            try:
                master_status = cursor.fetchone()
                binlog_file = master_status['File'] if master_status else 'N/A'
                binlog_position = master_status['Position'] if master_status else 0
            except:
                binlog_file = 'N/A'
                binlog_position = 0
            
            cursor.execute("SHOW VARIABLES LIKE 'binlog_format'")
            binlog_format = cursor.fetchone()['Value']
            
            # 主库基本信息
            cursor.execute("SHOW VARIABLES LIKE 'server_id'")
            try:
                server_id_var = int(cursor.fetchone()['Value'])
            except:
                server_id_var = 0
            
            # GTID 模式
            cursor.execute("SHOW VARIABLES LIKE 'gtid_mode'")
            try:
                gtid_mode = cursor.fetchone()['Value']
            except:
                gtid_mode = 'OFF'
            
            cursor.execute("SHOW VARIABLES LIKE 'gtid_purged'")
            try:
                gtid_purged = cursor.fetchone()['Value'] or 'N/A'
            except:
                gtid_purged = 'N/A'
            
            cursor.execute("SHOW VARIABLES LIKE 'gtid_executed'")
            try:
                gtid_executed = cursor.fetchone()['Value'] or 'N/A'
            except:
                gtid_executed = 'N/A'
            
            # 多线程复制配置
            cursor.execute("SHOW VARIABLES LIKE 'slave_parallel_workers'")
            try:
                slave_parallel_workers = int(cursor.fetchone()['Value'])
            except:
                slave_parallel_workers = 0
            
            cursor.execute("SHOW VARIABLES LIKE 'slave_parallel_type'")
            try:
                slave_parallel_type = cursor.fetchone()['Value']
            except:
                slave_parallel_type = 'N/A'
            
            cursor.execute("SHOW VARIABLES LIKE 'slave_preserve_commit_order'")
            try:
                slave_preserve_commit_order = cursor.fetchone()['Value']
            except:
                slave_preserve_commit_order = 'N/A'
            
            # 复制延迟配置
            cursor.execute("SHOW VARIABLES LIKE 'slave_net_timeout'")
            try:
                slave_net_timeout = int(cursor.fetchone()['Value'])
            except:
                slave_net_timeout = 0
            
            cursor.execute("SHOW VARIABLES LIKE 'slave_compressed_protocol'")
            try:
                slave_compressed_protocol = cursor.fetchone()['Value']
            except:
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
            except Exception as e:
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
            except:
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
                except:
                    pass
            
            # 复制过滤规则
            cursor.execute("SHOW VARIABLES LIKE 'replicate_do_db'")
            try:
                replicate_do_db = cursor.fetchone()['Value'] or 'N/A'
            except:
                replicate_do_db = 'N/A'
            
            cursor.execute("SHOW VARIABLES LIKE 'replicate_ignore_db'")
            try:
                replicate_ignore_db = cursor.fetchone()['Value'] or 'N/A'
            except:
                replicate_ignore_db = 'N/A'
            
            cursor.execute("SHOW VARIABLES LIKE 'replicate_do_table'")
            try:
                replicate_do_table = cursor.fetchone()['Value'] or 'N/A'
            except:
                replicate_do_table = 'N/A'
            
            cursor.execute("SHOW VARIABLES LIKE 'replicate_ignore_table'")
            try:
                replicate_ignore_table = cursor.fetchone()['Value'] or 'N/A'
            except:
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
                except:
                    pass

            # =============================================
            # 10. 缓冲池 (buffer) - P0
            # =============================================
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_dirty'")
            try:
                innodb_buffer_pool_pages_dirty = int(cursor.fetchone()['Value'])
            except:
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
            except:
                innodb_trx_committed = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_trx_rolled_back'")
            try:
                innodb_trx_rolled_back = int(cursor.fetchone()['Value'])
            except:
                innodb_trx_rolled_back = 0

            # =============================================
            # 12. 日志统计 (log) - P1
            # =============================================
            cursor.execute("SHOW MASTER LOGS")
            try:
                binlog_count = int(cursor.fetchone()['Log_name'])
            except:
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
            except:
                max_used_connections = 0
            
            cursor.execute("SHOW VARIABLES LIKE 'have_ssl'")
            try:
                have_ssl = cursor.fetchone()['Value']
            except:
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
            except:
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
            except:
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
                    "count": int(row[1])
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
            "table_count_by_schema": table_count_by_schema
        }


# ==========================================
# PostgreSQL 检查器
# ==========================================
class PostgreSQLChecker(BaseDBChecker):
    
    def get_connection(self, config):
        dbname = config.service_name if config.service_name else 'postgres'
        return psycopg2.connect(
            database=dbname, user=config.username, password=config.get_password(),
            host=config.host, port=config.port, connect_timeout=5
        )
    
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
        # 6. 会话详情 (session_detail) - P0新增
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
        # 7. SQL统计 (sql) - P0新增
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
        except:
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
        except:
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
        except:
            last_wal_receive_lsn = last_wal_replay_lsn = 'N/A'
            wal_lag = 0
        
        # 复制类型
        try:
            cur.execute("""
                SELECT replication_type
                FROM pg_stat_replication
                LIMIT 1
            """)
            physical_replication_type = cur.fetchone()[0] if cur.fetchone() else 'N/A'
        except:
            physical_replication_type = 'N/A'

        # =============================================
        # 9.5 对象统计 (object) - P2补全
        # =============================================
        # Top 20 表大小
        cur.execute("""
            SELECT schemaname, tablename, 
                   pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size_pretty,
                   pg_total_relation_size(schemaname||'.'||tablename) as size_bytes
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
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
                SELECT schemaname, tablename, indexname
                FROM pg_stat_user_indexes
                WHERE idx_scan = 0
                ORDER BY schemaname, tablename
                LIMIT 20
            """)
            for row in cur.fetchall():
                unused_indexes.append({
                    "schema": row[0],
                    "table": row[1],
                    "index": row[2]
                })
        except:
            pass
        
        # 需要VACUUM的表
        tables_needing_vacuum = []
        try:
            cur.execute("""
                SELECT schemaname, tablename, n_dead_tup, n_live_tup,
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
        except:
            pass
        
        # 序列使用
        cursor.execute("SELECT COUNT(*) FROM pg_sequences")
        try:
            sequence_count = int(cur.fetchone()[0])
        except:
            sequence_count = 0

        # =============================================
        # 9. 配置参数 (config) - P1
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
            except:
                pass

        # =============================================
        # 10. 缓冲池 (buffer) - P0
        # =============================================
        cur.execute("""
            SELECT buffers_checkpoint, buffers_clean, buffers_backend, buffers_alloc
            FROM pg_stat_bgwriter
        """)
        bgwriter = cur.fetchone()
        if bgwriter:
            buffers_checkpoint = bgwriter[0]
            buffers_clean = bgwriter[1]
            buffers_backend = bgwriter[2]
            buffers_alloc = bgwriter[3]
        else:
            buffers_checkpoint = buffers_clean = buffers_backend = buffers_alloc = 0

        # =============================================
        # 11. 事务统计 (transaction) - P1
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
        # 12. 日志统计 (log) - P1
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
        # 13. 高可用 (ha) - P2
        # =============================================
        cur.execute("SELECT pg_is_in_recovery()")
        is_in_recovery = cur.fetchone()[0]
        
        cur.execute("""
            SELECT pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn(),
                   pg_last_wal_receive_lsn() - pg_last_wal_replay_lsn() as replication_lag
        """)
        try:
            lag_info = cur.fetchone()
            last_wal_receive_lsn = str(lag_info[0]) if lag_info[0] else 'N/A'
            last_wal_replay_lsn = str(lag_info[1]) if lag_info[1] else 'N/A'
            replication_lag_bytes = lag_info[2] if lag_info[2] else 0
        except:
            last_wal_receive_lsn = last_wal_replay_lsn = 'N/A'
            replication_lag_bytes = 0

        # =============================================
        # 14. 资源限制 (resource) - P2
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
            except:
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
            "replication_lag_bytes": replication_lag_bytes,
            
            # 资源限制
            "resource_limits": resource_limits
        }


# ==========================================
# 达梦数据库检查器
# ==========================================
class DamengChecker(BaseDBChecker):
    
    def get_connection(self, config):
        # 达梦 ODBC 连接字符串
        conn_str = f"DRIVER={{DM8 ODBC DRIVER}};SERVER={config.host}:{config.port};UID={config.username};PWD={config.get_password()};"
        return pyodbc.connect(conn_str, timeout=5)
    
    def collect_metrics(self, config, conn):
        cur = conn.cursor()
        
        # =============================================
        # 1. 基础信息 (basic)
        # =============================================
        cur.execute("SELECT banner FROM v$version WHERE ROWNUM=1")
        version = cur.fetchone()[0]
        
        try:
            cur.execute("SELECT INSTANCE_NAME, HOST_NAME, TO_CHAR(START_TIME, 'YYYY-MM-DD HH24:MI:SS') FROM V$INSTANCE")
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
        cur.execute("SELECT count(*) FROM v$sessions WHERE STATUS='ACTIVE'")
        active_sessions = int(cur.fetchone()[0])
        
        cur.execute("SELECT count(*) FROM v$sessions")
        total_sessions = int(cur.fetchone()[0])
        
        cur.execute("SELECT VALUE FROM v$parameter WHERE name='MAX_SESSIONS'")
        max_sessions = int(cur.fetchone()[0])
        conn_usage_pct = round((active_sessions / max_sessions) * 100, 2) if max_sessions > 0 else 0
        
        try:
            cur.execute("SELECT count(*) FROM V$SESSION_WAIT WHERE EVENT != 'Idle'")
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
            cur.execute("SELECT count(*), SUM(TOTAL_SIZE * PAGE) / 1024 / 1024 / 1024 FROM V$TABLESPACE")
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
            cur.execute("SELECT VALUE FROM V$SYSTEM_INFO WHERE NAME='SQL_COUNT'")
            sql_count = int(cur.fetchone()[0])
        except:
            sql_count = 0
        
        try:
            cur.execute("SELECT VALUE FROM V$SYSTEM_INFO WHERE NAME='TRAN_COUNT'")
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
                    SESS_ID, USER_NAME, STATUS, PROGRAM, IP, HOST,
                    SUBSTR(SQL_TEXT, 1, 200) as SQL_TEXT,
                    TRX_ID, TRANSACTION_ID
                FROM V$SESSIONS
                WHERE USER_NAME IS NOT NULL
                ORDER BY STATUS, SESS_ID
                LIMIT 100
            """)
            for row in cur.fetchall():
                session_list.append({
                    "sess_id": str(row[0]),
                    "user_name": row[1] or 'N/A',
                    "status": row[2] or 'N/A',
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
            cur.execute("SELECT DEST_ID, STATUS, DEST_NAME FROM V$ARCH_DEST")
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
                dw_replication_issues.append(f"备库延迟过高: {apply_delay_total}")
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
            cur.execute("SELECT COUNT(*) FROM V$GLOBAL_LATCH WHERE BLOCKING_LRU > 0")
            dsc_lock_contention_count = int(cur.fetchone()[0])
        except:
            dsc_lock_contention_count = 0
        
        # DSC 健康状态判断
        dsc_cluster_health = 'UNKNOWN'
        dsc_cluster_issues = []
        if dsc_node_count > 0:
            dsc_cluster_health = 'HEALTHY'
            # 检查是否有节点异常
            for node in dsc_cluster_info:
                if node['status'] != 'OPEN':
                    dsc_cluster_health = 'DEGRADED'
                    dsc_cluster_issues.append(f"节点 {node['instance_name']} 状态异常: {node['status']}")
            if dsc_lock_contention_count > 100:
                dsc_cluster_health = 'DEGRADED'
                dsc_cluster_issues.append(f"全局锁竞争过多: {dsc_lock_contention_count}")
        else:
            dsc_cluster_health = 'NOT_CLUSTER'
        
        # =============================================
        # 11. 配置参数 (config) - P1
        # =============================================
        config_params = {}
        config_keys = ['BUFFER', 'SORT_BUF_SIZE', 'MLOG_BUF_SIZE', 'MAX_SESSIONS', 'MAX_TRX']
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
                WHERE STATUS = 'FAILED'
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
                cur.execute(f"SELECT VALUE FROM V$PARAMETER WHERE NAME='{resource}'")
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
            "resource_limits": resource_limits
        }


# ==========================================
# Gbase8a 检查器 (基于 MySQL 协议)
# ==========================================
class GbaseChecker(BaseDBChecker):
    """Gbase8a 使用 MySQL 协议，复MySQLChecker 大部分逻辑"""
    
    def get_connection(self, config):
        return pymysql.connect(
            host=config.host, port=config.port, 
            user=config.username, password=config.get_password(),
            connect_timeout=5, cursorclass=pymysql.cursors.DictCursor
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
            conn_usage_pct = round((threads_connected / max_connections) * 100, 2) if max_connections > 0 else 0

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
            tps = round((com_commit + com_rollback) / uptime, 2) if uptime > 0 else 0
            
            # InnoDB 相关
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_rows_read'")
            try:
                innodb_rows_read = int(cursor.fetchone()['Value'])
            except:
                innodb_rows_read = 0
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")
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
                    if 'ONLINE' in node_status or 'ACTIVE' in node_status or 'HEALTHY' in node_status:
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
                    cursor.execute("SELECT * FROM gcluster_v$node_status LIMIT 50")
                    for row in cursor.fetchall():
                        gbase_cm_total_count += 1
                        node_status = str(row.get('STATUS', 'UNKNOWN')).upper()
                        if 'ONLINE' in node_status or 'ACTIVE' in node_status:
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
                    if 'ONLINE' in node_status or 'ACTIVE' in node_status or 'HEALTHY' in node_status:
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
                    cursor.execute("SELECT * FROM gnode_v$dnodetatus LIMIT 50")
                    for row in cursor.fetchall():
                        gbase_dn_total_count += 1
                        node_status = str(row.get('STATUS', 'UNKNOWN')).upper()
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
                                gbase_cluster_issues.append(f"副本异常: {replica_healthy_count}/{replica_total_count} 正常")
                        else:
                            gbase_cluster_health = 'DEGRADED'
                            gbase_cluster_issues.append(f"数据节点异常: {gbase_dn_healthy_count}/{gbase_dn_total_count} 正常")
                    else:
                        gbase_cluster_health = 'DEGRADED'
                        gbase_cluster_issues.append("未检测到数据节点")
                else:
                    gbase_cluster_health = 'UNHEALTHY'
                    gbase_cluster_issues.append(f"管理节点异常: {gbase_cm_healthy_count}/{gbase_cm_total_count} 正常")
            else:
                gbase_cluster_health = 'NOT_CLUSTER'
                gbase_cluster_issues.append("未检测到 Gbase8A 集群")
            
            # 节点故障告警阈值检查
            if gbase_cm_healthy_count < gbase_cm_total_count:
                if gbase_cm_total_count - gbase_cm_healthy_count >= 1:
                    gbase_cluster_health = 'CRITICAL'
                    gbase_cluster_issues.append(f"管理节点离线数量: {gbase_cm_total_count - gbase_cm_healthy_count}")
            
            if gbase_dn_total_count > 0:
                failed_dn = gbase_dn_total_count - gbase_dn_healthy_count
                if failed_dn >= 1:
                    if gbase_cluster_health not in ['CRITICAL', 'UNHEALTHY']:
                        gbase_cluster_health = 'CRITICAL'
                    gbase_cluster_issues.append(f"数据节点离线数量: {failed_dn}")
            
            # Gbase 集群汇总信息
            gbase_cluster_summary = {
                "cm_node_count": gbase_cm_total_count,
                "cm_healthy_count": gbase_cm_healthy_count,
                "dn_node_count": gbase_dn_total_count,
                "dn_healthy_count": gbase_dn_healthy_count,
                "replica_count": replica_total_count,
                "replica_healthy_count": replica_healthy_count,
                "replica_config": gbase_dn_replica_count
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
            "cluster_nodes": cluster_nodes,
            "cluster_info": cluster_info,
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
            "config_params": config_params
        }


# ==========================================
# TDSQL 检查器 (腾讯分布式数据库)
# ==========================================
class TDSQLChecker(BaseDBChecker):
    """
    TDSQL 有两种模式：MySQL 兼容PostgreSQL 兼容    这里默认MySQL 兼容版处理，如需 PG 版请切换PostgreSQLChecker
    """
    
    def get_connection(self, config):
        # TDSQL MySQL 模式
        return pymysql.connect(
            host=config.host, port=config.port, 
            user=config.username, password=config.get_password(),
            connect_timeout=5, cursorclass=pymysql.cursors.DictCursor
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
            except:
                server_id = 0
            
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            uptime = int(cursor.fetchone()['Value'])
            
            cursor.execute("SELECT DATABASE()")
            current_db = cursor.fetchone()['DATABASE()']
            
            # TDSQL 特有信息
            cursor.execute("SHOW VARIABLES LIKE 'version_comment'")
            try:
                version_comment = cursor.fetchone()['Value']
            except:
                version_comment = 'N/A'

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
            conn_usage_pct = round((threads_connected / max_connections) * 100, 2) if max_connections > 0 else 0

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
            tps = round((com_commit + com_rollback) / uptime, 2) if uptime > 0 else 0

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
            # 7. 复制与集群 (replication) - TDSQL双活三中心高可用增强
            # =============================================
            cursor.execute("SHOW MASTER STATUS")
            try:
                master_status = cursor.fetchone()
                binlog_file = master_status['File'] if master_status else 'N/A'
                binlog_position = master_status['Position'] if master_status else 0
            except:
                binlog_file = 'N/A'
                binlog_position = 0
            
            cursor.execute("SHOW VARIABLES LIKE 'gtid_mode'")
            try:
                gtid_mode = cursor.fetchone()['Value']
            except:
                gtid_mode = 'OFF'
            
            cursor.execute("SHOW VARIABLES LIKE 'sync_binlog'")
            try:
                sync_binlog = cursor.fetchone()['Value']
            except:
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
                    if 'ONLINE' in node_status or 'FOLLOWER' in node_status or 'LEADER' in node_status:
                        tdsql_zk_healthy_count += 1
                    tdsql_zk_nodes.append({
                        "node_id": str(row.get('NODE_ID', 'N/A')),
                        "node_type": str(row.get('NODE_TYPE', 'ZK')),
                        "host": str(row.get('HOST', 'N/A')),
                        "port": int(row.get('PORT', 0)),
                        "status": node_status,
                        "mode": str(row.get('MODE', 'N/A')),  # LEADER/FOLLOWER
                        "data_version": str(row.get('DATA_VERSION', 'N/A')),
                        "leader_elect": str(row.get('LEADER_ELECT', 'N/A'))
                    })
            except:
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
                except:
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
                    
                    tdsql_proxy_nodes.append({
                        "proxy_id": str(row.get('PROXY_ID', 'N/A')),
                        "proxy_ip": str(row.get('PROXY_IP', 'N/A')),
                        "proxy_port": int(row.get('PROXY_PORT', 0)),
                        "status": node_status,
                        "center_id": center_id,
                        "role": str(row.get('ROLE', 'N/A')),
                        "session_count": int(row.get('SESSION_COUNT', 0)),
                        "max_session": int(row.get('MAX_SESSION', 0)),
                        "session_usage_pct": round(int(row.get('SESSION_COUNT', 0)) / max(int(row.get('MAX_SESSION', 1), 1) * 100, 2))
                    })
            except:
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
            tdsql_dn_primary_count = 0  # 主副本数量 (应在A中心)
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
                        "space_usage_pct": round(float(row.get('DATA_SIZE_MB', 0)) / max(float(row.get('TOTAL_SPACE_MB', 1)), 1) * 100, 2)
                    })
            except:
                pass
            
            # =============================================
            # 7.8 TDSQL 副本同步状态监控 - P0 新增
            # =============================================
            # 4副本配置，主副本在A中心
            tdsql_replica_info = []
            tdsql_replica_healthy_count = 0
            tdsql_replica_total_count = 4  # 4副本模式
            tdsql_cross_center_sync_count = 0  # 跨中心同步副本数
            tdsql_local_sync_count = 0  # 本中心同步副本数
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
                        "consistency_status": str(row.get('CONSISTENCY_STATUS', 'N/A')),
                        "last_sync_time": str(row.get('LAST_SYNC_TIME', 'N/A')),
                        "readable": str(row.get('READABLE', 'N/A'))
                    })
            except:
                pass
            
            # =============================================
            # 7.9 TDSQL 双活三中心健康状态判断 - P0 新增
            # =============================================
            tdsql_cluster_health = 'UNKNOWN'
            tdsql_cluster_issues = []
            
            # ZK 健康检查
            if tdsql_zk_total_count > 0:
                if tdsql_zk_healthy_count < tdsql_zk_total_count:
                    tdsql_cluster_issues.append(f"ZK节点异常: {tdsql_zk_healthy_count}/{tdsql_zk_total_count}")
            
            # Proxy 健康检查 (A、B中心对等)
            if tdsql_proxy_total_count > 0:
                if tdsql_proxy_center_a_count == 0 or tdsql_proxy_center_b_count == 0:
                    tdsql_cluster_issues.append(f"Proxy单中心部署: A中心{tdsql_proxy_center_a_count}, B中心{tdsql_proxy_center_b_count}")
                if tdsql_proxy_healthy_count < tdsql_proxy_total_count:
                    tdsql_cluster_issues.append(f"Proxy节点异常: {tdsql_proxy_healthy_count}/{tdsql_proxy_total_count}")
            
            # 数据节点健康检查 (4副本)
            if tdsql_dn_total_count > 0:
                if tdsql_dn_center_a_count == 0 or tdsql_dn_center_b_count == 0:
                    tdsql_cluster_issues.append(f"数据节点单中心部署: A中心{tdsql_dn_center_a_count}, B中心{tdsql_dn_center_b_count}")
                if tdsql_dn_primary_count == 0:
                    tdsql_cluster_issues.append("无主副本可用")
                elif tdsql_dn_primary_count < tdsql_replica_total_count - 1:  # 允许部分副本故障
                    tdsql_cluster_issues.append(f"主副本异常: {tdsql_dn_primary_count} 个主副本")
            
            # 副本同步状态检查
            if tdsql_replica_healthy_count < tdsql_replica_total_count:
                tdsql_cluster_issues.append(f"副本同步异常: {tdsql_replica_healthy_count}/{tdsql_replica_total_count} 正常")
            
            # 整体健康状态判断
            if len(tdsql_cluster_issues) == 0:
                if tdsql_zk_healthy_count == tdsql_zk_total_count and tdsql_proxy_healthy_count == tdsql_proxy_total_count and tdsql_dn_healthy_count == tdsql_dn_total_count:
                    tdsql_cluster_health = 'HEALTHY'
                else:
                    tdsql_cluster_health = 'DEGRADED'
            elif 'ZK节点异常' in str(tdsql_cluster_issues) or '无主副本可用' in str(tdsql_cluster_issues):
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
                "replica_config": tdsql_replica_total_count,  # 4副本
                "replica_healthy_count": tdsql_replica_healthy_count,
                "primary_replica_count": tdsql_dn_primary_count,
                "cross_center_sync_count": tdsql_cross_center_sync_count,
                "local_sync_count": tdsql_local_sync_count
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
            for key in ['max_connections', 'innodb_buffer_pool_size', 'gtid_mode', 'sync_binlog']:
                try:
                    cursor.execute(f"SHOW VARIABLES LIKE '{key}'")
                    row = cursor.fetchone()
                    if row:
                        config_params[key] = row['Value']
                except:
                    pass

        return {
            # 基础信息
            "version": version[:50] + "...",
            "server_id": server_id,
            "version_comment": version_comment,
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
            
            # 会话详情
            "session_list": session_list,
            
            # SQL统计
            "slow_queries_total": slow_queries,
            "long_query_time_sec": long_query_time,
            
            # 复制集群 - TDSQL特有
            "binlog_file": binlog_file,
            "binlog_position": binlog_position,
            "gtid_mode": gtid_mode,
            "sync_binlog": sync_binlog,
            "shards_info": shards_info,
            "shard_count": shard_count,
            "primary_shards": primary_shards,
            
            # TDSQL 双活三中心集群监控 (新增)
            "tdsql_zk_nodes": tdsql_zk_nodes,
            "tdsql_proxy_nodes": tdsql_proxy_nodes,
            "tdsql_dn_nodes": tdsql_dn_nodes,
            "tdsql_replica_info": tdsql_replica_info,
            "tdsql_cluster_health": tdsql_cluster_health,
            "tdsql_cluster_issues": tdsql_cluster_issues,
            "tdsql_cluster_summary": tdsql_cluster_summary,
            
            # 锁
            "locks": locks,
            
            # 配置
            "config_params": config_params
        }


# ==========================================
# Redis 检查器 (预留)
# ==========================================
class RedisChecker(BaseDBChecker):
    """Redis 监控 - 预留实现"""
    
    def check(self, config):
        # TODO: 需要安redis-py
        self.cmd.process_result(config, 'DOWN', {
            "error": "Redis 监控尚未实现，需要安装 redis-py"
        })


# ==========================================
# 主命令类
# ==========================================
class Command(BaseCommand):
    help = '全能数据库监控守护进(v0.2.0 - Phase 2 智能增强版)'

    # 数据库类-> 检查器映射
    CHECKER_MAP = {
        'oracle': OracleChecker,
        'mysql': MySQLChecker,
        'pgsql': PostgreSQLChecker,
        'dm': DamengChecker,
        'gbase': GbaseChecker,
        'tdsql': TDSQLChecker,
        'redis': RedisChecker,
        'mongo': None,  # TODO: MongoDB 支持
    }

    def handle(self, *args, **options):
        print(f"[{datetime.datetime.now()}] 全栈监控守护进程 v0.2.0 (Phase 2 智能增强版) 已启动")
        print(f">> 支持的数据库：Oracle, MySQL, PostgreSQL, 达梦，Gbase8a, TDSQL")
        print(f">> Phase 2 智能特性：168时间槽基线 | RCA根因分析 | 容量预测 | 健康评分")
        
        if ENABLE_PHASE2_ENGINES:
            print(f">> Phase 2 引擎: 已启用")
        else:
            print(f">> Phase 2 引擎: 已禁用 (设置 ENABLE_PHASE2_ENGINES=True 启用)")

        scheduler = BlockingScheduler()
        scheduler.add_job(self.monitor_job, 'interval', seconds=60)

        # 立即执行一次
        self.monitor_job()
        
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("\n监控进程已停止")

    def _run_single_check(self, config):
        """在独立线程中执行单个数据库的采集，超时后自动记录 DOWN"""
        # 每个线程需要独立关闭复用的 Django DB 连接，避免跨线程复用问题
        connection.close_if_unusable_or_obsolete()
        checker_class = self.CHECKER_MAP.get(config.db_type)
        if checker_class:
            checker = checker_class(self)
            checker.check(config)
        elif config.db_type == 'mongo':
            print(f"  -- 跳过暂不支持的类型：{config.name} (MongoDB)")
        else:
            print(f"  -- 跳过未知类型：{config.name} ({config.db_type})")

    def monitor_job(self):
        print(f"\n[{datetime.datetime.now()}] --- 开始新一轮巡检 ---")
        connection.close_if_unusable_or_obsolete()

        configs = list(DatabaseConfig.objects.filter(is_active=True))
        if not configs:
            print("  没有活跃的数据库配置，跳过本轮巡检")
            return

        with ThreadPoolExecutor(max_workers=min(COLLECT_WORKERS, len(configs))) as executor:
            futures = {executor.submit(self._run_single_check, cfg): cfg for cfg in configs}
            for future, cfg in futures.items():
                try:
                    future.result(timeout=COLLECT_TIMEOUT_SEC)
                except FuturesTimeoutError:
                    print(f"  ⏱️ [{cfg.name}] 采集超时{COLLECT_TIMEOUT_SEC}s），记录 DOWN")
                    self.process_result(cfg, 'DOWN', {'error': f'采集超时{COLLECT_TIMEOUT_SEC}s'})
                except Exception as e:
                    print(f"  [{cfg.name}] 采集线程异常：{e}")
                    self.process_result(cfg, 'DOWN', {'error': f'采集线程异常：{str(e)}'})

    def process_result(self, config, current_status, data):
        """统一结果处理和告警逻辑（v0.2.0：Phase 2 智能引擎集成）"""

        def notify(title, body):
            self.send_alert(config, title, body)

        am = AlertManager(config, notify)

        # --- 1. 连通性告---
        am.fire_or_resolve(
            condition=(current_status == 'DOWN'),
            alert_type='down', metric_key='',
            fire_title='🔴 故障告警',
            fire_body=f"数据库无法连接\n错误：{data.get('error', '未知错误')}",
            resolve_title='🟢 恢复通知',
            resolve_body='数据库已重新恢复连接',
            severity='critical',
        )

        # --- 2. 业务监控（仅 UP 状态）---
        if current_status == 'UP':

            # A. 表空间容量告警
            tbs_warn = [t['name'] for t in data.get('tablespaces', [])
                        if (t.get('used_pct') or 0) > TBS_THRESHOLD]
            am.fire_or_resolve(
                condition=bool(tbs_warn),
                alert_type='tablespace', metric_key='',
                fire_title='🟠 容量告警',
                fire_body=f"表空间使用率超过 {TBS_THRESHOLD}%：\n{', '.join(tbs_warn)}",
                resolve_title='🟢 容量恢复',
                resolve_body='所有表空间使用率已降至阈值以下',
            )

            # B. 连接数使用率告警
            conn_usage = data.get('conn_usage_pct', 0)
            am.fire_or_resolve(
                condition=(conn_usage > CONN_THRESHOLD_PCT),
                alert_type='connection', metric_key='conn_usage_pct',
                fire_title='🟠 连接数告警',
                fire_body=(f"连接数使用率已达 {conn_usage}%\n"
                           f"当前连接：{data.get('active_connections', 0)}\n"
                           f"最大连接：{data.get('max_connections', 0)}"),
                resolve_title='🟢 连接数恢复',
                resolve_body=f"连接数使用率已恢复正常（当前 {conn_usage}%）",
            )

            # C. 锁等待告警
            current_locks = data.get('locks', [])
            am.fire_or_resolve(
                condition=bool(current_locks),
                alert_type='lock', metric_key='',
                fire_title='🔴 性能告警：锁等待',
                fire_body=self._build_lock_msg(current_locks),
                resolve_title='🟢 锁等待解除',
                resolve_body='数据库阻塞已全部解除',
                severity='critical',
            )
            if current_locks:
                print(f"  🛑 [锁等待] {len(current_locks)} 个阻塞会话")

            # ======================================
            # D. Phase 2: 智能引擎分析
            # ======================================
            if ENABLE_PHASE2_ENGINES:
                self._run_phase2_analysis(config, data, am)

        # --- 3. 记录监控日志 ---
        MonitorLog.objects.create(
            config=config,
            status=current_status,
            message=json.dumps(data, ensure_ascii=False, default=str)
        )

    def _run_phase2_analysis(self, config, data, am):
        """
        Phase 2 智能引擎分析
        
        包含:
        - 168时间槽动态基线异常检测
        - RCA根因分析
        - 容量预测 (定期)
        - 健康评分 (定期)
        """
        
        # --- D1. 基线异常检测 (168时间槽 + 三重条件 + Phase 3智能告警收敛) ---
        try:
            # Phase 3: 初始化智能告警引擎
            alert_engine = AlertEngine(config)
            
            baseline_engine = BaselineEngine(config)
            anomalies = baseline_engine.check_current_against_baseline(data)

            anomaly_keys = set()
            for metric_name, current_val, baseline, anomaly_type, sev in anomalies:
                anomaly_keys.add(metric_name)
                
                # Phase 3: 使用 AlertEngine.should_alert() 进行收敛判断
                # 只在满足三重条件(幅度+方向+持续性)且通过收敛窗口时发送告警
                direction_str = 'up' if anomaly_type == 'high' else 'down'
                alert_result = alert_engine.should_alert(metric_name, current_val, direction_str)
                
                if alert_result['should_fire']:
                    # 计算正常范围
                    normal_range = f"{baseline.normal_min:.2f} ~ {baseline.normal_max:.2f}"
                    direction_label = '暴涨' if anomaly_type == 'high' else '骤降'
                    emoji = '🔴' if alert_result['severity'] == 'critical' else '🟡'
                    body = (
                        f"指标：{metric_name}\n"
                        f"当前值：{current_val}\n"
                        f"基线均值：{baseline.mean:.2f} ± {baseline.std:.2f}\n"
                        f"正常范围：{normal_range}\n"
                        f"P99：{baseline.p99:.2f}\n"
                        f"偏离类型：{direction_label}\n"
                        f"告警等级：{alert_result['severity']} (连续{alert_result['consecutive_count']}次)\n"
                        f"建议：检查是否有异常业务行为或潜在故障"
                    )
                    am.fire(
                        alert_type='baseline', metric_key=metric_name,
                        title=f'{emoji} 基线异常：{metric_name}', description=body,
                        severity=alert_result['severity'],
                    )
                    print(f"  📊 [基线] {metric_name}={current_val} 偏离（{direction_label}） [{alert_result['severity']}]")
                else:
                    print(f"  📊 [基线-收敛] {metric_name}={current_val} 检测到异常但处于收敛窗口内")

            # 对本轮已恢复的基线异常发送恢复通知
            from monitor.models import AlertLog
            active_baseline = AlertLog.objects.filter(
                config=config, alert_type='baseline', status='active'
            )
            for al in active_baseline:
                if al.metric_key not in anomaly_keys:
                    am.resolve(
                        alert_type='baseline', metric_key=al.metric_key,
                        recovery_title=f'🟢 基线恢复：{al.metric_key}',
                        recovery_body=f'指标 {al.metric_key} 已恢复至正常范围',
                    )

        except Exception as e:
            print(f"  ⚠️ 基线检测异常：{e}")

        # --- D2. RCA 根因分析 ---
        try:
            rca_engine = RCAEngine(config)
            rca_report = rca_engine.analyze(data)

            if rca_report.get('diagnoses'):
                for diag in rca_report['diagnoses']:
                    if diag['severity'] == 'critical':
                        body = (
                            f"规则ID：{diag['rule_id']}\n"
                            f"问题描述：{diag['description']}\n\n"
                            f"建议措施：\n" + "\n".join(f"• {s}" for s in diag['suggestions'])
                        )
                        am.fire(
                            alert_type='rca', metric_key=diag['rule_id'],
                            title=f"🔴 RCA根因：{diag['name']}",
                            description=body,
                            severity='critical',
                        )
                        print(f"  🔍 [RCA] {diag['rule_id']} - {diag['name']}")

            # 复合故障告警
            if rca_report.get('compound_diagnoses'):
                for compound in rca_report['compound_diagnoses']:
                    body = (
                        f"复合故障：{compound['name']}\n"
                        f"关联规则：{', '.join(compound['requires'])}\n\n"
                        f"建议措施：\n" + "\n".join(f"• {s}" for s in compound['suggestions'])
                    )
                    am.fire(
                        alert_type='rca_compound', metric_key=compound['id'],
                        title=f"🚨 复合故障：{compound['name']}",
                        description=body,
                        severity='critical',
                    )
                    print(f"  🚨 [RCA复合] {compound['id']} - {compound['name']}")

        except Exception as e:
            print(f"  ⚠️ RCA分析异常：{e}")

        # --- D3. 健康评分 (每小时一次) ---
        try:
            from django.core.cache import cache
            health_cache_key = f"health_score_{config.id}"
            last_health_check = cache.get(health_cache_key)

            if last_health_check is None:  # 首次检查或缓存过期
                health_engine = HealthEngine(config)
                health_report = health_engine.calculate(data)

                # 缓存1小时
                cache.set(health_cache_key, health_report, 3600)

                # 评分低于C级发送告警
                if health_report['grade'] in ('D', 'F'):
                    emoji = '🔴' if health_report['grade'] == 'F' else '🟠'
                    body = (
                        f"健康评分：{health_report['overall_score']} 分\n"
                        f"等级：{health_report['grade']} ({health_report['grade_description']})\n\n"
                        f"各维度得分：\n" + "\n".join(
                            f"• {dim}: {d['score']}" 
                            for dim, d in health_report['dimensions'].items()
                        ) + "\n\n"
                        f"改进建议：\n" + "\n".join(f"• {r}" for r in health_report['recommendations'])
                    )
                    am.fire(
                        alert_type='health', metric_key='health_score',
                        title=f"{emoji} 数据库健康评分 {health_report['grade']}级",
                        description=body,
                        severity='critical' if health_report['grade'] == 'F' else 'warning',
                    )
                    print(f"  💚 [健康] 评分={health_report['overall_score']} {health_report['grade']}级")
                else:
                    print(f"  💚 [健康] 评分={health_report['overall_score']} {health_report['grade']}级 (正常)")

        except Exception as e:
            print(f"  ⚠️ 健康评分异常：{e}")

        # --- D4. 容量预测 (每天一次) ---
        try:
            from django.core.cache import cache
            capacity_cache_key = f"capacity_forecast_{config.id}"
            last_capacity_check = cache.get(capacity_cache_key)

            if last_capacity_check is None:  # 首次检查或缓存过期
                capacity_engine = CapacityEngine(config)
                capacity_report = capacity_engine.analyze_all_metrics()

                # 缓存24小时
                cache.set(capacity_cache_key, capacity_report, 86400)

                if capacity_report.get('alerts'):
                    for alert in capacity_report['alerts']:
                        emoji = '🚨' if alert['severity'] == 'emergency' else \
                                '🔴' if alert['severity'] == 'critical' else '🟠'
                        body = (
                            f"类型：{alert['type']}\n"
                            f"当前值：{alert['current']}%\n"
                            f"预测值：{alert['predicted']}%\n"
                            f"消息：{alert['message']}"
                        )
                        am.fire(
                            alert_type='capacity', metric_key=alert['type'],
                            title=f"{emoji} 容量预测告警",
                            description=body,
                            severity=alert['severity'],
                        )
                        print(f"  📈 [容量] {alert['type']} - {alert['message']}")

        except Exception as e:
            print(f"  ⚠️ 容量预测异常：{e}")

    def _build_lock_msg(self, locks):
        """构建锁等待告警消"""
        msg = "检测到严重的数据库阻塞（Lock Wait）：\n\n"
        for l in locks:
            msg += (
                f"--------------------------------\n"
                f"凶手 (Blocker): {l.get('blocker_user', 'N/A')} ({l.get('blocker_id', 'N/A')})\n"
                f"受害 (Waiter) : {l.get('waiter_user', 'N/A')} ({l.get('waiter_id', 'N/A')})\n"
                f"已阻塞时   : {l.get('seconds', 0)} 秒\n"
            )
        msg += "--------------------------------\n注意：时长仍在增加，DBA 立即检查！"
        return msg

    def send_alert(self, config, title, body):
        """统一告警出口：邮+ 钉钉（如已配置）"""
        from monitor.notifications import send_email_alert, send_dingtalk_alert

        full_body = (
            f"数据库：{config.name}\n"
            f"地址：{config.host}:{config.port}\n"
            f"类型：{config.get_db_type_display()}\n"
            f"时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"\n{body}"
        )
        send_email_alert(title, full_body)
        send_dingtalk_alert(title, full_body)

    # 保留旧名称兼容性
    def send_alert_email(self, config, title_prefix, error_msg):
        self.send_alert(config, title_prefix, error_msg)
