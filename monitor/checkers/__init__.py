# -*- coding: utf-8 -*-
"""
数据库检查器模块
从 start_monitor.py 中提取，每种数据库类型独立一个文件，便于维护和扩展。

v3.0 重构：
- BaseDBChecker: 基类 + 公共常量
- OracleChecker: Oracle (含 RAC/ADG)
- MySQLChecker: MySQL (含主从复制)
- PostgreSQLChecker: PostgreSQL (含流复制)
- DamengChecker: 达梦 DM8 (含 DW 主备 / DSC 共享存储集群)
- GbaseChecker: Gbase8a (基于 MySQL 协议 + 管理/数据节点集群)
- TDSQLChecker: TDSQL (基于 MySQL 协议 + 双活三中心)
- RedisChecker: Redis (预留)
"""

from monitor.checkers.base import (
    BaseDBChecker,
    COLLECT_TIMEOUT_SEC,
    COLLECT_WORKERS,
    TBS_THRESHOLD,
    LOCK_TIME_THRESHOLD,
    CONN_THRESHOLD_PCT,
    ENABLE_PHASE2_ENGINES,
    CAPACITY_CHECK_INTERVAL_HOURS,
    HEALTH_CHECK_INTERVAL_HOURS,
)

from monitor.checkers.oracle import OracleChecker
from monitor.checkers.mysql import MySQLChecker
from monitor.checkers.pgsql import PostgreSQLChecker

# 达梦 DM8 驱动为可选依赖（需官方授权安装 dmPython）
try:
    from monitor.checkers.dm import DamengChecker
except ImportError:
    DamengChecker = None

from monitor.checkers.gbase import GbaseChecker
from monitor.checkers.tdsql import TDSQLChecker

# RedisChecker 保留在 start_monitor.py 中，因为实现尚未完成

# Checker 工厂映射 (v3.0)
# 注意：DamengChecker 仅在 dmPython 可用时才注册
CHECKER_MAP = {
    'oracle': OracleChecker,
    'mysql': MySQLChecker,
    'pgsql': PostgreSQLChecker,
    'gbase': GbaseChecker,
    'tdsql': TDSQLChecker,
}
if DamengChecker is not None:
    CHECKER_MAP['dm'] = DamengChecker


def get_checker(db_type: str):
    """
    工厂函数：根据数据库类型字符串返回对应的 Checker 类。

    Args:
        db_type: 数据库类型 ('oracle', 'mysql', 'pgsql', 'dm', 'gbase', 'tdsql')

    Returns:
        Checker class or None if unsupported
    """
    if not db_type:
        return None
    return CHECKER_MAP.get(db_type.lower())


__all__ = [
    'BaseDBChecker',
    'OracleChecker',
    'MySQLChecker',
    'PostgreSQLChecker',
    'DamengChecker',
    'GbaseChecker',
    'TDSQLChecker',
    'CHECKER_MAP',
    'get_checker',
    'COLLECT_TIMEOUT_SEC',
    'COLLECT_WORKERS',
    'TBS_THRESHOLD',
    'LOCK_TIME_THRESHOLD',
    'CONN_THRESHOLD_PCT',
    'ENABLE_PHASE2_ENGINES',
    'CAPACITY_CHECK_INTERVAL_HOURS',
    'HEALTH_CHECK_INTERVAL_HOURS',
]
