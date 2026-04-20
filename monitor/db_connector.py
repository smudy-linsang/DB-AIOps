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
from typing import Optional

logger = logging.getLogger(__name__)


class DbConnectionError(Exception):
    """数据库连接错误"""
    pass


class DbConnector:
    """数据库连接器"""
    
    @staticmethod
    def get_connection(config) -> any:
        """
        根据数据库配置获取连接
        
        参数:
            config: DatabaseConfig 模型实例
            
        返回:
            数据库连接对象
        """
        db_type = config.db_type.lower() if hasattr(config, 'db_type') else config.get('db_type', '').lower()
        
        if db_type == 'oracle':
            return DbConnector._connect_oracle(config)
        elif db_type in ['mysql', 'gbase', 'tdsql']:
            return DbConnector._connect_mysql(config)
        elif db_type in ['pgsql', 'postgresql']:
            return DbConnector._connect_postgresql(config)
        elif db_type == 'dm':
            return DbConnector._connect_dm(config)
        else:
            raise DbConnectionError(f"不支持的数据库类型: {db_type}")
    
    @staticmethod
    def _connect_oracle(config) -> any:
        """连接 Oracle 数据库"""
        try:
            import oracledb
            
            # 获取密码
            password = config.get_password() if hasattr(config, 'get_password') else config.get('password', '')
            
            # 确定服务名
            service_name = getattr(config, 'service_name', None) or 'orcl'
            
            # 确定端口
            port = getattr(config, 'port', 1521) or 1521
            
            dsn = oracledb.connect(
                user=config.username,
                password=password,
                dsn=f"{config.host}:{port}/{service_name}"
            )
            logger.info(f"Oracle 连接成功: {config.host}")
            return dsn
        except ImportError:
            raise DbConnectionError("需要安装 oracledb 库: pip install oracledb")
        except Exception as e:
            raise DbConnectionError(f"Oracle 连接失败: {str(e)}")
    
    @staticmethod
    def _connect_mysql(config) -> any:
        """连接 MySQL 系列数据库 (MySQL/GBase/TDSQL)"""
        try:
            import pymysql
            
            # 获取密码
            password = config.get_password() if hasattr(config, 'get_password') else config.get('password', '')
            
            # 确定端口
            port = getattr(config, 'port', 3306) or 3306
            
            conn = pymysql.connect(
                host=config.host,
                port=int(port),
                user=config.username,
                password=password,
                charset='utf8mb4',
                connect_timeout=10
            )
            logger.info(f"MySQL 连接成功: {config.host}:{port}")
            return conn
        except ImportError:
            raise DbConnectionError("需要安装 pymysql 库: pip install pymysql")
        except Exception as e:
            raise DbConnectionError(f"MySQL 连接失败: {str(e)}")
    
    @staticmethod
    def _connect_postgresql(config) -> any:
        """连接 PostgreSQL 数据库"""
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
                connect_timeout=10
            )
            logger.info(f"PostgreSQL 连接成功: {config.host}:{port}")
            return conn
        except ImportError:
            raise DbConnectionError("需要安装 psycopg2 库: pip install psycopg2-binary")
        except Exception as e:
            raise DbConnectionError(f"PostgreSQL 连接失败: {str(e)}")
    
    @staticmethod
    def _connect_dm(config) -> any:
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
                    version = result[0]
            elif db_type in ['mysql', 'gbase', 'tdsql']:
                cursor.execute("SELECT VERSION()")
                result = cursor.fetchone()
                if result:
                    version = result[0]
            elif db_type in ['pgsql', 'postgresql']:
                cursor.execute("SELECT version()")
                result = cursor.fetchone()
                if result:
                    version = result[0]
            
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
def get_db_connection(config) -> any:
    """获取数据库连接的便捷函数"""
    return DbConnector.get_connection(config)


def test_db_connection(config) -> dict:
    """测试数据库连接的便捷函数"""
    return DbConnector.test_connection(config)


def close_db_connection(conn) -> None:
    """关闭数据库连接的便捷函数"""
    return DbConnector.close_connection(conn)
