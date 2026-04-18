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
from monitor.baseline_engine import BaselineEngine
from monitor.models import DatabaseConfig, MonitorLog

# 单次采集任务超时（秒）：超过此时间的采集视为失败，记 DOWN，不阻塞其他任务。
COLLECT_TIMEOUT_SEC = getattr(settings, "COLLECT_TIMEOUT_SEC", 15)
# 并发采集线程数。
COLLECT_WORKERS = getattr(settings, "COLLECT_WORKERS", 20)

# 阈值配置。
TBS_THRESHOLD = 90
LOCK_TIME_THRESHOLD = 10
CONN_THRESHOLD_PCT = 80

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
        
        # 1. 基础信息
        cursor.execute("SELECT BANNER FROM v$version WHERE ROWNUM = 1")
        version = cursor.fetchone()[0]
        
        # 只统计活跃会话（排除 INACTIVE 状态），避免空闲连接导致使用率虚高
        cursor.execute("SELECT count(*) FROM gv$session WHERE status = 'ACTIVE'")
        sessions = cursor.fetchone()[0]
        
        # 启动时间 (RAC 兼容)
        try:
            cursor.execute("SELECT (SYSDATE - startup_time) * 24 * 60 * 60 FROM gv$instance WHERE inst_id = 1")
            uptime = int(cursor.fetchone()[0])
        except:
            cursor.execute("SELECT (SYSDATE - startup_time) * 24 * 60 * 60 FROM v$instance")
            uptime = int(cursor.fetchone()[0])

        # 2. 表空间使用率
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
        tbs_list = []
        for row in cursor.fetchall():
            tbs_list.append({
                "name": row[0], 
                "total_mb": float(row[1]), 
                "used_mb": float(row[2]), 
                "used_pct": float(row[3])
            })

        # 3. 锁等(RAC 兼容)
        sql_lock = f"""
            SELECT
                blocker.inst_id || ':' || blocker.sid || ',' || blocker.serial# as blocker_info,
                blocker.username as blocker_user,
                waiter.inst_id || ':' || waiter.sid || ',' || waiter.serial# as waiter_info,
                waiter.username as waiter_user,
                waiter.seconds_in_wait as wait_sec
            FROM gv$session blocker, gv$session waiter
            WHERE waiter.blocking_session = blocker.sid
              AND waiter.blocking_instance = blocker.inst_id
              AND waiter.seconds_in_wait > {LOCK_TIME_THRESHOLD}
        """
        cursor.execute(sql_lock)
        lock_list = []
        for row in cursor.fetchall():
            lock_list.append({
                "blocker_id": row[0], 
                "blocker_user": row[1] or 'N/A',
                "waiter_id": row[2],
                "waiter_user": row[3] or 'N/A',
                "seconds": int(row[4])
            })

        # 4. 最大连接数限制
        cursor.execute("SELECT VALUE FROM v$parameter WHERE name = 'processes'")
        max_conn = int(cursor.fetchone()[0])
        conn_usage_pct = round((sessions / max_conn) * 100, 2) if max_conn > 0 else 0

        # 5. QPS（基于 execute count / uptime）
        try:
            cursor.execute("SELECT value FROM v$sysstat WHERE name = 'execute count'")
            exec_count = int(cursor.fetchone()[0])
            qps = round(exec_count / uptime, 2) if uptime > 0 else 0
        except:
            qps = 0

        # 6. 慢查询统计（当前活跃且运行时间超过 10 秒的 SQL）
        try:
            cursor.execute("""
                SELECT count(*)
                FROM gv$session s
                WHERE s.status = 'ACTIVE'
                  AND s.sql_id IS NOT NULL
                  AND s.last_call_et > 10
            """)
            slow_queries_active = int(cursor.fetchone()[0])
        except:
            slow_queries_active = 0

        return {
            "version": version[:50] + "...",
            "active_connections": sessions,
            "max_connections": max_conn,
            "conn_usage_pct": conn_usage_pct,
            "uptime_seconds": uptime,
            "qps": qps,
            "slow_queries_active": slow_queries_active,
            "tablespaces": tbs_list,
            "locks": lock_list
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
            # 1. 版本
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()['VERSION()']
            
            # 2. 连接数（使用 Threads_running 而非 Threads_connected，避免空闲连接导致使用率虚高）
            try:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_running'")
                result = cursor.fetchone()
                threads_connected = int(result['Value']) if result else 0
            except Exception:
                # 降级：使用 Threads_connected 作为近似值
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
                threads_connected = int(cursor.fetchone()['Value'])

            cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
            max_connections = int(cursor.fetchone()['Value'])
            conn_usage_pct = round((threads_connected / max_connections) * 100, 2) if max_connections > 0 else 0

            # 3. 运行时间
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            uptime = int(cursor.fetchone()['Value'])

            # 4. QPS
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Questions'")
            questions = int(cursor.fetchone()['Value'])
            qps = round(questions / uptime, 2) if uptime > 0 else 0
            
            # 5. 慢查询统计
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Slow_queries'")
            slow_queries = int(cursor.fetchone()['Value'])
            
            cursor.execute("SHOW VARIABLES LIKE 'long_query_time'")
            long_query_time = float(cursor.fetchone()['Value'])
            
            # 6. 表空(各数据库大小)
            cursor.execute("""
                SELECT 
                    table_schema as db_name,
                    ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) as size_mb
                FROM information_schema.tables
                GROUP BY table_schema
                ORDER BY size_mb DESC
                LIMIT 10
            """)
            db_sizes = []
            for row in cursor.fetchall():
                db_sizes.append({
                    "name": row['db_name'],
                    "size_mb": float(row['size_mb'])
                })
            
            # 7. 锁等(InnoDB) - 兼容 MySQL 5.7 8.0+
            lock_list = []
            try:
                # MySQL 8.0+ 使用 performance_schema
                cursor.execute("""
                    SELECT 
                        b.trx_mysql_thread_id as blocker_thread,
                        r.trx_mysql_thread_id as blocked_thread,
                        TIMESTAMPDIFF(SECOND, r.trx_started, NOW()) as wait_sec
                    FROM performance_schema.data_lock_waits w
                    INNER JOIN information_schema.innodb_trx b ON w.BLOCKING_ENGINE_TRANSACTION_ID = b.trx_id
                    INNER JOIN information_schema.innodb_trx r ON w.REQUESTING_ENGINE_TRANSACTION_ID = r.trx_id
                    WHERE TIMESTAMPDIFF(SECOND, r.trx_started, NOW()) > %s
                """, (LOCK_TIME_THRESHOLD,))
                for row in cursor.fetchall():
                    lock_list.append({
                        "blocker_id": str(row['blocker_thread']),
                        "waiter_id": str(row['blocked_thread']),
                        "seconds": int(row['wait_sec'])
                    })
            except Exception:
                try:
                    # MySQL 5.7 使用 information_schema.innodb_lock_waits
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
                        lock_list.append({
                            "blocker_id": str(row['blocker_thread']),
                            "waiter_id": str(row['blocked_thread']),
                            "seconds": int(row['wait_sec'])
                        })
                except Exception:
                    pass

        return {
            "version": version[:50] + "...",
            "active_connections": threads_connected,
            "max_connections": max_connections,
            "conn_usage_pct": conn_usage_pct,
            "uptime_seconds": uptime,
            "qps": qps,
            "slow_queries_total": slow_queries,
            "long_query_time_sec": long_query_time,
            "database_sizes": db_sizes,
            "locks": lock_list
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
        
        # 1. 版本
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]
        
        # 2. 连接数（只统计活跃连接，排除 idle 空闲连接，避免使用率虚高）
        # state = 'active' 表示正在执行 SQL 的会话
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
        active_connections = int(cur.fetchone()[0])
        
        cur.execute("SHOW max_connections")
        max_connections = int(cur.fetchone()[0])
        conn_usage_pct = round((active_connections / max_connections) * 100, 2) if max_connections > 0 else 0
        
        # 3. 运行时间
        cur.execute("SELECT extract(epoch from (now() - pg_postmaster_start_time()))")
        uptime = int(cur.fetchone()[0])
        
        # 4. 数据库大小
        cur.execute("""
            SELECT datname, pg_database_size(datname) / 1024 / 1024 as size_mb
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY size_mb DESC
            LIMIT 10
        """)
        db_sizes = []
        for row in cur.fetchall():
            db_sizes.append({
                "name": row[0],
                "size_mb": float(row[1])
            })
        
        # 5. 数据库大小作为容量指标（PostgreSQL 表空间无独立配额限制        #    used_pct 无法SQL 层获取磁盘容量计算，此处只报告绝对大小，
        #    used_pct 设为 None，避免前版本自比较算法导致的误报警）
        tbs_list = []
        for row in db_sizes:
            tbs_list.append({
                "name": row["name"],
                "total_mb": None,
                "used_mb": row["size_mb"],
                "used_pct": None
            })
        
        # 6. 锁等待
        cur.execute(f"""
            SELECT 
                blocked_locks.pid as blocked_pid,
                blocked_activity.usename as blocked_user,
                blocking_locks.pid as blocking_pid,
                blocking_activity.usename as blocking_user,
                EXTRACT(EPOCH FROM (NOW() - blocked_activity.query_start))::INTEGER as wait_sec
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
        lock_list = []
        for row in cur.fetchall():
            lock_list.append({
                "blocker_id": str(row[2]),
                "blocker_user": row[3] or 'N/A',
                "waiter_id": str(row[0]),
                "waiter_user": row[1] or 'N/A',
                "seconds": int(row[4])
            })
        
        # 7. 慢查(超过 10 秒的查询)
        cur.execute("""
            SELECT count(*) 
            FROM pg_stat_activity 
            WHERE state = 'active' 
              AND query_start < NOW() - INTERVAL '10 seconds'
        """)
        slow_queries = int(cur.fetchone()[0])

        cur.close()

        return {
            "version": version[:50] + "...",
            "active_connections": active_connections,
            "max_connections": max_connections,
            "conn_usage_pct": conn_usage_pct,
            "uptime_seconds": uptime,
            "database_sizes": db_sizes,
            "tablespaces": tbs_list,
            "locks": lock_list,
            "slow_queries_active": slow_queries
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
        
        # 1. 版本
        cur.execute("SELECT banner FROM v$version WHERE ROWNUM=1")
        version = cur.fetchone()[0]
        
        # 2. 连接数
        cur.execute("SELECT count(*) FROM v$sessions")
        sessions = int(cur.fetchone()[0])
        
        cur.execute("SELECT VALUE FROM v$parameter WHERE name='MAX_SESSIONS'")
        max_sessions = int(cur.fetchone()[0])
        conn_usage_pct = round((sessions / max_sessions) * 100, 2) if max_sessions > 0 else 0
        
        # 3. 运行时间
        cur.execute("SELECT (SYSDATE-START_TIME)*86400 FROM v$instance")
        uptime = int(cur.fetchone()[0])
        
        # 4. 表空间使用率 (DM8 兼容)
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
        tbs_list = []
        for row in cur.fetchall():
            tbs_list.append({
                "name": row[0],
                "total_mb": float(row[1]),
                "used_mb": float(row[2]),
                "used_pct": float(row[3])
            })
        
        # 5. 锁等(DM8 兼容)
        lock_list = []
        try:
            cur.execute("""
                SELECT 
                    s.SESS_ID,
                    s.USER_NAME,
                    l.BLOCKED,
                    l.LMODE
                FROM V$LOCK l
                JOIN V$SESSIONS s ON l.TRX_ID = s.TRX_ID
                WHERE l.BLOCKED = 1
            """)
            blocked_sessions = cur.fetchall()
            if blocked_sessions:
                for row in blocked_sessions:
                    lock_list.append({
                        "blocker_id": 'N/A',
                        "blocker_user": 'N/A',
                        "waiter_id": str(row[0]),
                        "waiter_user": row[1] or 'N/A',
                        "seconds": 0
                    })
        except Exception:
            pass
        
        cur.close()

        return {
            "version": str(version)[:50] + "...",
            "active_connections": sessions,
            "max_connections": max_sessions,
            "conn_usage_pct": conn_usage_pct,
            "uptime_seconds": uptime,
            "tablespaces": tbs_list,
            "locks": lock_list
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
        # Gbase8a MPP 架构，需要额外采集集群状态
        with conn.cursor() as cursor:
            # 1. 基础版本和连接数 (MySQL)
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()['VERSION()']

            # 2. 连接数（使用 Threads_running 而非 Threads_connected）
            try:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_running'")
                result = cursor.fetchone()
                threads_connected = int(result['Value']) if result else 0
            except Exception:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
                threads_connected = int(cursor.fetchone()['Value'])

            cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
            max_connections = int(cursor.fetchone()['Value'])
            conn_usage_pct = round((threads_connected / max_connections) * 100, 2) if max_connections > 0 else 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            uptime = int(cursor.fetchone()['Value'])

            # 2. Gbase 集群状态（如果支持）
            cluster_nodes = []
            try:
                # 尝试查询 Gbase 特有的集群视图
                cursor.execute("SELECT * FROM gcluster_v$node_status LIMIT 10")
                for row in cursor.fetchall():
                    cluster_nodes.append({
                        "node_id": str(row.get('NODE_ID', 'N/A')),
                        "status": row.get('STATUS', 'UNKNOWN'),
                        "role": row.get('ROLE', 'N/A')
                    })
            except:
                # 如果不支持，就留空
                pass

            # 3. 数据库大小
            cursor.execute("""
                SELECT 
                    table_schema as db_name,
                    ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) as size_mb
                FROM information_schema.tables
                GROUP BY table_schema
                ORDER BY size_mb DESC
                LIMIT 10
            """)
            db_sizes = []
            for row in cursor.fetchall():
                db_sizes.append({
                    "name": row['db_name'],
                    "size_mb": float(row['size_mb'])
                })

        return {
            "version": version[:50] + "...",
            "active_connections": threads_connected,
            "max_connections": max_connections,
            "conn_usage_pct": conn_usage_pct,
            "uptime_seconds": uptime,
            "database_sizes": db_sizes,
            "cluster_nodes": cluster_nodes,  # Gbase 特有
            "locks": []  # Gbase 暂不支持锁查询
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
        # TDSQL 基本复用 MySQL 逻辑，增加分布式特性监控
        with conn.cursor() as cursor:
            # 1. 版本 (TDSQL 会有特殊标识)
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()['VERSION()']

            # 2. 连接数（使用 Threads_running 而非 Threads_connected）
            try:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_running'")
                result = cursor.fetchone()
                threads_connected = int(result['Value']) if result else 0
            except Exception:
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
                threads_connected = int(cursor.fetchone()['Value'])

            cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
            max_connections = int(cursor.fetchone()['Value'])
            conn_usage_pct = round((threads_connected / max_connections) * 100, 2) if max_connections > 0 else 0

            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            uptime = int(cursor.fetchone()['Value'])

            # 3. TDSQL 分片信息 (如果支持)
            shards_info = []
            try:
                # 尝试查询 TDSQL 特有的分片视图
                cursor.execute("SELECT * FROM tdsql_shard_status LIMIT 10")
                for row in cursor.fetchall():
                    shards_info.append({
                        "shard_name": row.get('SHARD_NAME', 'N/A'),
                        "status": row.get('STATUS', 'UNKNOWN'),
                        "data_size_mb": float(row.get('DATA_SIZE', 0)) / 1024 / 1024
                    })
            except:
                pass
            
            # 4. 数据库大小
            cursor.execute("""
                SELECT
                    table_schema as db_name,
                    ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) as size_mb
                FROM information_schema.tables
                GROUP BY table_schema
                ORDER BY size_mb DESC
                LIMIT 10
            """)
            db_sizes = []
            for row in cursor.fetchall():
                db_sizes.append({
                    "name": row['db_name'],
                    "size_mb": float(row['size_mb'])
                })

        return {
            "version": version[:50] + "...",
            "active_connections": threads_connected,
            "max_connections": max_connections,
            "conn_usage_pct": conn_usage_pct,
            "uptime_seconds": uptime,
            "database_sizes": db_sizes,
            "shards": shards_info,  # TDSQL 特有
            "locks": []
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
    help = '全能数据库监控守护进(v0.1.0 - 并发隔离 + 告警去重 + 密码加密)'

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
        print(f"[{datetime.datetime.now()}] 全栈监控守护进程 v0.1.0 已启..")
        print(f">> 支持的数据库：Oracle, MySQL, PostgreSQL, 达梦，Gbase8a, TDSQL")
        print(f">> 架构模式：插件化检查器 + 基线异常检测")
        print(f">> 告警策略：基线偏离告警 + 锁等待 + 容量监控")

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
        """统一结果处理和告警逻辑（v0.1.0：基AlertLog 去重"""

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

            # D. 基线异常检测
            try:
                baseline_engine = BaselineEngine(config)
                anomalies = baseline_engine.check_current_against_baseline(data)

                # 当前异常的指标集合，用于后续解除已恢复指标的告警
                anomaly_keys = set()
                for metric_name, current_val, baseline, anomaly_type, sev in anomalies:
                    anomaly_keys.add(metric_name)
                    direction = '暴涨' if anomaly_type == 'high' else '骤降'
                    emoji = '🔴' if sev == 'critical' else '🟡'
                    body = (
                        f"指标：{metric_name}\n"
                        f"当前值：{current_val}\n"
                        f"基线均值：{baseline['mean']} ± {baseline['std']}\n"
                        f"正常范围：{baseline['normal_range'][0]} ~ {baseline['normal_range'][1]}\n"
                        f"P99：{baseline['p99']}\n"
                        f"偏离类型：{direction}\n"
                        f"建议：检查是否有异常业务行为或潜在故障"
                    )
                    am.fire(
                        alert_type='baseline', metric_key=metric_name,
                        title=f'{emoji} 基线异常：{metric_name}', description=body,
                        severity=sev,
                    )
                    print(f"  📊 [基线] {metric_name}={current_val} 偏离（{direction}）")

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

        # --- 3. 记录监控日志 ---
        MonitorLog.objects.create(
            config=config,
            status=current_status,
            message=json.dumps(data, ensure_ascii=False, default=str)
        )

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
