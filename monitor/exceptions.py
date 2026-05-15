# -*- coding: utf-8 -*-
"""
统一异常体系
============

为整个 DB Monitor 平台提供分层的自定义异常类，
配合 ExceptionMiddleware 实现统一的 JSON 错误响应格式。

异常层级：
    DBMonitorError                     # 基类
    ├── CollectionError                # 指标采集阶段
    │   ├── ConnectionFailedError      #   连接失败
    │   └── QueryExecutionError        #   查询执行失败
    ├── EngineError                    # 引擎处理阶段
    │   ├── BaselineEngineError        #   基线引擎
    │   ├── AlertEngineError           #   告警引擎
    │   ├── ReportEngineError          #   报告引擎
    │   ├── ApprovalEngineError        #   审批引擎
    │   └── RemediationEngineError     #   自愈引擎
    ├── StorageError                   # 存储层
    │   ├── TimeSeriesWriteError       #   时序写入
    │   └── ElasticsearchError         #   ES 读写
    ├── SecurityError                  # 安全相关
    │   ├── EncryptionError            #   加解密
    │   └── SqlInjectionRiskError      #   SQL 注入风险
    └── ConfigError                    # 配置错误

使用示例：
    from monitor.exceptions import CollectionError, ConnectionFailedError

    try:
        conn = DbConnector.get_connection(config)
    except DbConnectionError as e:
        raise ConnectionFailedError(str(e), config_id=config.id) from e

中间件会自动将异常转为统一 JSON：
    {
        "error": "ConnectionFailedError",
        "message": "Oracle 连接失败: ORA-12514",
        "detail": {"config_id": 42},
        "status": 503
    }
"""

# ── HTTP 状态码映射 ──────────────────────────────────────
_STATUS_MAP = {
    'ConfigError':           400,
    'SecurityError':         403,
    'SqlInjectionRiskError': 403,
    'EncryptionError':       500,
    'CollectionError':       503,
    'ConnectionFailedError': 503,
    'QueryExecutionError':   503,
    'EngineError':           500,
    'BaselineEngineError':   500,
    'AlertEngineError':      500,
    'ReportEngineError':     500,
    'ApprovalEngineError':   500,
    'RemediationEngineError': 500,
    'StorageError':          500,
    'TimeSeriesWriteError':  500,
    'ElasticsearchError':    503,
}


# ── 基类 ─────────────────────────────────────────────────
class DBMonitorError(Exception):
    """
    所有 DB Monitor 自定义异常的基类。

    参数:
        message:   人类可读的错误描述
        detail:    可选的附加详情字典（如 config_id、metric_key 等）
        status:    建议的 HTTP 响应状态码（子类可覆盖）
    """

    status: int = 500

    def __init__(self, message: str = '', detail: dict = None, status: int = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        if status is not None:
            self.status = status

    def to_dict(self) -> dict:
        """转为统一 JSON 响应字典"""
        return {
            'error':   self.__class__.__name__,
            'message': self.message,
            'detail':  self.detail,
            'status':  self.status,
        }


# ── 采集层 ────────────────────────────────────────────────
class CollectionError(DBMonitorError):
    """指标采集阶段的通用错误"""
    status = 503


class ConnectionFailedError(CollectionError):
    """数据库连接失败"""
    status = 503


class QueryExecutionError(CollectionError):
    """在目标数据库上执行查询失败"""
    status = 503


# ── 引擎层 ────────────────────────────────────────────────
class EngineError(DBMonitorError):
    """引擎处理阶段的通用错误"""
    status = 500


class BaselineEngineError(EngineError):
    """基线引擎错误"""
    pass


class AlertEngineError(EngineError):
    """告警引擎错误"""
    pass


class ReportEngineError(EngineError):
    """报告引擎错误"""
    pass


class ApprovalEngineError(EngineError):
    """审批引擎错误"""
    pass


class RemediationEngineError(EngineError):
    """自愈引擎错误"""
    pass


# ── 存储层 ────────────────────────────────────────────────
class StorageError(DBMonitorError):
    """存储层通用错误"""
    status = 500


class TimeSeriesWriteError(StorageError):
    """时序数据写入失败"""
    pass


class ElasticsearchError(StorageError):
    """Elasticsearch 读写失败"""
    status = 503


# ── 安全层 ────────────────────────────────────────────────
class SecurityError(DBMonitorError):
    """安全相关错误"""
    status = 403


class EncryptionError(SecurityError):
    """加解密失败"""
    status = 500


class SqlInjectionRiskError(SecurityError):
    """检测到 SQL 注入风险"""
    status = 403


# ── 配置层 ────────────────────────────────────────────────
class ConfigError(DBMonitorError):
    """配置错误"""
    status = 400


# ── 工具函数 ──────────────────────────────────────────────
def get_status_for_exception(exc: Exception) -> int:
    """根据异常类型获取推荐的 HTTP 状态码"""
    if isinstance(exc, DBMonitorError):
        return exc.status
    cls_name = exc.__class__.__name__
    return _STATUS_MAP.get(cls_name, 500)
