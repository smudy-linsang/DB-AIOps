"""
数据库连接工厂模块
=================

提供统一的数据库连接获取接口，支持多种数据库类型：
- Oracle (oracledb)
- MySQL (pymysql)
- PostgreSQL (psycopg2)
- DM (达梦) (pyodbc)
- GBase (pymysql)
- TDSQL (pymysql)

Author: DB-AIOps Team
"""

import logging
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# BUG-109: 目标库连接必须带语句超时。api_views_perf 的注释承诺了"3s 语句超时",
# 但此前连接层和端点层都没有实现 —— 被监控库负载高时 v$session / processlist
# 查询可能挂住几十秒, 占满 Web worker, 监控系统在最需要观察的时刻先于被监控库挂掉。
DEFAULT_STATEMENT_TIMEOUT_MS = 5000


def _stmt_timeout_ms(override=None) -> int:
    if override:
        return int(override)
    from monitor import appconf
    return appconf.get('TARGET_DB_STATEMENT_TIMEOUT_MS')


class DbConnectionError(Exception):
    """数据库连接错误"""
    pass


class DbConnector:
    """数据库连接器"""

    @staticmethod
    def get_connection(config, statement_timeout_ms: Optional[int] = None,
                       readonly: bool = False) -> Any:
        """
        根据数据库配置获取连接

        参数:
            config: DatabaseConfig 模型实例
            statement_timeout_ms: 语句超时(毫秒)，None 则取 settings 默认值。
                实时端点（性能中心直连）应显式传更短的预算，如 3000。
            readonly: 采集类调用应传 True，把会话置为只读，
                即便某条采集 SQL 写错也无法在被监控库上产生写入。
                运维执行链（AuditLogExecuteView）必须保持 False。

        返回:
            数据库连接对象
        """
        db_type = config.db_type.lower() if hasattr(config, 'db_type') else config.get('db_type', '').lower()
        timeout_ms = _stmt_timeout_ms(statement_timeout_ms)

        if db_type == 'oracle':
            return DbConnector._connect_oracle(config, timeout_ms)
        elif db_type in ['mysql', 'gbase', 'tdsql']:
            return DbConnector._connect_mysql(config, timeout_ms)
        elif db_type in ['pgsql', 'postgresql']:
            return DbConnector._connect_postgresql(config, timeout_ms, readonly)
        elif db_type == 'dm':
            return DbConnector._connect_dm(config, timeout_ms)
        else:
            raise DbConnectionError(f"不支持的数据库类型: {db_type}")

    @staticmethod
    def _connect_oracle(config, timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS) -> Any:
        """连接 Oracle 数据库"""
        try:
            import oracledb

            # 获取密码
            password = config.get_password() if hasattr(config, 'get_password') else config.get('password', '')

            # 确定服务名
            service_name = getattr(config, 'service_name', None) or 'orcl'

            # 确定端口
            port = getattr(config, 'port', 1521) or 1521

            conn = oracledb.connect(
                user=config.username,
                password=password,
                dsn=f"{config.host}:{port}/{service_name}"
            )
            # 驱动级调用超时(毫秒)：单条语句超时即抛错，不再拖住调用线程
            try:
                conn.call_timeout = int(timeout_ms)
            except Exception as e:
                logger.debug(f"Oracle 设置 call_timeout 失败(驱动版本较老): {e}")
            logger.info(f"Oracle 连接成功: {config.host}")
            return conn
        except ImportError:
            raise DbConnectionError("需要安装 oracledb 库: pip install oracledb")
        except Exception as e:
            raise DbConnectionError(f"Oracle 连接失败: {str(e)}")
    
    @staticmethod
    def _connect_mysql(config, timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS) -> Any:
        """连接 MySQL 系列数据库 (MySQL/GBase/TDSQL)

        6A-02: gbase/tdsql 走 pymysql; TDSQL 经 proxy 走广域网, 超时放宽;
        service_name 复用为默认库名(TDSQL/gbase 可留空)。DictCursor 便于 ASH 按列名取值。
        """
        try:
            import pymysql
        except ImportError:
            raise DbConnectionError("需要安装 pymysql 库: pip install pymysql")

        import time as _time
        db_type = (config.db_type.lower() if hasattr(config, 'db_type')
                   else config.get('db_type', '').lower())
        password = config.get_password() if hasattr(config, 'get_password') else config.get('password', '')
        port = getattr(config, 'port', 3306) or 3306
        database = getattr(config, 'service_name', None) or None
        connect_timeout = 15 if db_type == 'tdsql' else 10
        # socket 读超时留出语句超时之上的余量，让服务端超时先生效（错误信息更准确）
        read_timeout = max(10, int(timeout_ms) // 1000 + 5)
        # TDSQL 经互联网, SYN 偶发丢包, 重试; 本地库不重试
        retries = 5 if db_type == 'tdsql' else 1

        last_err = None
        for attempt in range(retries):
            try:
                conn = pymysql.connect(
                    host=config.host,
                    port=int(port),
                    user=config.username,
                    password=password,
                    database=database,
                    charset='utf8mb4',
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                    cursorclass=pymysql.cursors.DictCursor,
                )
                DbConnector._apply_mysql_session_limits(conn, timeout_ms)
                logger.info(f"{db_type or 'mysql'} 连接成功: {config.host}:{port}")
                return conn
            except Exception as e:
                last_err = e
                if attempt < retries - 1:
                    _time.sleep(2)
        raise DbConnectionError(f"MySQL 连接失败: {str(last_err)}")

    @staticmethod
    def _apply_mysql_session_limits(conn, timeout_ms: int) -> None:
        """会话级语句超时。

        REVIEW-06: 原实现只尝试 `max_execution_time`（MySQL 5.7.8+，单位毫秒），
        失败就静默跳过。而 **MariaDB 根本没有这个变量** —— 它用
        `max_statement_time`（单位秒，浮点）。于是在整个 MariaDB 系
        （含部分 GBase/TDSQL 部署）上，BUG-109 的语句超时保护**完全不存在**，
        而且毫无痕迹：目标库一慢就会把 Web worker 拖死，正是 BUG-109 的原状。
        这个缺陷是拿真实 MariaDB 10.11 跑 W3 方言测试才暴露的。
        """
        from monitor import degrade
        applied = None
        try:
            with conn.cursor() as c:
                # MySQL 5.7.8+ / Percona：毫秒
                try:
                    c.execute("SET SESSION max_execution_time=%s", (int(timeout_ms),))
                    applied = 'max_execution_time'
                except Exception:
                    # MariaDB：秒（浮点），最小 0.001
                    try:
                        c.execute("SET SESSION max_statement_time=%s",
                                  (max(0.001, timeout_ms / 1000.0),))
                        applied = 'max_statement_time'
                    except Exception as e2:
                        degrade.note('db_connector.mysql_statement_timeout',
                                     '两种语句超时变量均不支持，目标库慢查询无法被中断', e2)
                try:
                    c.execute("SET SESSION innodb_lock_wait_timeout=5")
                except Exception:
                    pass
        except Exception as e:
            degrade.note('db_connector.mysql_session_limits', '会话限制设置失败', e)
        if applied:
            logger.debug("MySQL 语句超时已生效: %s", applied)

    @staticmethod
    def _connect_postgresql(config, timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
                            readonly: bool = False) -> Any:
        """连接 PostgreSQL 数据库

        BUG-110: psycopg2 默认 autocommit=False，第一条 SELECT 就隐式 BEGIN。
        哨兵持有的长连接从不 COMMIT，在被监控库上表现为 `idle in transaction`，
        其事务快照压住 xmin → VACUUM 无法回收死元组 → 被监控库表膨胀、逐日变慢。
        监控工具反过来损害被监控对象。这里一律开启 autocommit；
        readonly 由调用方按用途选择（采集只读，运维执行链需可写）。
        """
        try:
            import psycopg2

            # 获取密码
            password = config.get_password() if hasattr(config, 'get_password') else config.get('password', '')

            # 确定数据库名
            database = getattr(config, 'service_name', None) or getattr(config, 'database_name', None) or 'postgres'

            # 确定端口
            port = getattr(config, 'port', 5432) or 5432

            conn = psycopg2.connect(
                host=config.host,
                port=int(port),
                user=config.username,
                password=password,
                database=database,
                connect_timeout=10,
                options=f'-c statement_timeout={int(timeout_ms)}',
            )
            conn.autocommit = True
            if readonly:
                try:
                    conn.set_session(readonly=True, autocommit=True)
                except Exception as e:
                    logger.debug(f"PostgreSQL 设置只读会话失败: {e}")
            logger.info(f"PostgreSQL 连接成功: {config.host}:{port}")
            return conn
        except ImportError:
            raise DbConnectionError("需要安装 psycopg2 库: pip install psycopg2-binary")
        except Exception as e:
            raise DbConnectionError(f"PostgreSQL 连接失败: {str(e)}")

    @staticmethod
    def _connect_dm(config, timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS) -> Any:
        """连接达梦数据库"""
        try:
            import pyodbc

            # 获取密码
            password = config.get_password() if hasattr(config, 'get_password') else config.get('password', '')

            # 确定端口
            port = getattr(config, 'port', 5236) or 5236

            # 构建 ODBC 连接字符串
            conn_str = (
                f"DRIVER={{DM8 ODBC DRIVER}};"
                f"SERVER={config.host}:{port};"
                f"UID={config.username};"
                f"PWD={password};"
            )

            conn = pyodbc.connect(conn_str, timeout=10)
            # pyodbc 的 timeout 属性是查询超时(秒)，0 表示不限
            try:
                conn.timeout = max(1, int(timeout_ms) // 1000)
            except Exception as e:
                logger.debug(f"达梦设置查询超时失败: {e}")
            logger.info(f"达梦连接成功: {config.host}:{port}")
            return conn
        except ImportError:
            raise DbConnectionError("需要安装 pyodbc 库: pip install pyodbc")
        except Exception as e:
            raise DbConnectionError(f"达梦连接失败: {str(e)}")
    
    @staticmethod
    def close_connection(conn) -> None:
        """关闭数据库连接"""
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.warning(f"关闭连接时出错: {str(e)}")
    
    @staticmethod
    def _first_column(row):
        """
        从游标 fetchone() 结果中安全取出第一列值。

        兼容两种游标：
        - DictCursor（MySQL 系，见 _connect_mysql）返回 dict，如 {'VERSION()': '8.0.x'}
        - 默认游标（Oracle/PG/DM）返回 tuple/list，如 ('8.0.x',)
        """
        if row is None:
            return None
        if isinstance(row, dict):
            return next(iter(row.values())) if row else None
        return row[0]

    @staticmethod
    def test_connection(config) -> dict:
        """
        测试数据库连接
        
        参数:
            config: DatabaseConfig 模型实例
            
        返回:
            dict: {
                'success': bool,
                'message': str,
                'version': str (可选)
            }
        """
        conn = None
        try:
            conn = DbConnector.get_connection(config)
            
            # 获取版本信息
            cursor = conn.cursor()
            db_type = config.db_type.lower() if hasattr(config, 'db_type') else ''
            
            version = "未知"
            if db_type == 'oracle':
                cursor.execute("SELECT banner FROM v$version WHERE ROWNUM = 1")
                result = cursor.fetchone()
                if result:
                    version = DbConnector._first_column(result)
            elif db_type in ['mysql', 'gbase', 'tdsql']:
                cursor.execute("SELECT VERSION()")
                result = cursor.fetchone()
                if result:
                    version = DbConnector._first_column(result)
            elif db_type in ['pgsql', 'postgresql']:
                cursor.execute("SELECT version()")
                result = cursor.fetchone()
                if result:
                    version = DbConnector._first_column(result)
            
            cursor.close()
            
            return {
                'success': True,
                'message': '连接成功',
                'version': version
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"连接失败: {str(e)}",
                'version': None
            }
        finally:
            DbConnector.close_connection(conn)


# 便捷函数
def get_db_connection(config, statement_timeout_ms=None) -> Any:
    """获取数据库连接的便捷函数"""
    return DbConnector.get_connection(config, statement_timeout_ms)


def test_db_connection(config) -> dict:
    """测试数据库连接的便捷函数"""
    return DbConnector.test_connection(config)


def close_db_connection(conn) -> None:
    """关闭数据库连接的便捷函数"""
    return DbConnector.close_connection(conn)
