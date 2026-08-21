# -*- coding: utf-8 -*-
"""
请求关联上下文
==============

为每个 HTTP 请求注入关联 ID（request_id）并透传到全部日志行：

- RequestIdMiddleware（monitor/middleware.py）负责生成/透传关联 ID，
  写入本模块的 ContextVar；
- RequestIdFilter 在日志落盘时把当前 request_id 附加到 LogRecord
  （formatter 中以 {request_id} 引用），请求上下文之外输出 '-'；
- 同一请求期间产生的任意日志（视图、中间件、degrade 留痕）都能用
  同一个 ID 串联归因。
"""
import contextvars
import logging

# 请求上下文之外（管理命令、后台线程等）的占位值
MISSING = '-'

_REQUEST_ID: contextvars.ContextVar = contextvars.ContextVar(
    'db_monitor_request_id', default=MISSING
)


def get_request_id() -> str:
    """当前请求的关联 ID；不在请求上下文中时返回 '-'。"""
    return _REQUEST_ID.get()


def set_request_id(request_id: str):
    """设置关联 ID，返回 Token；调用方必须在 finally 中 reset，防止线程复用串号。"""
    return _REQUEST_ID.set(request_id)


def reset_request_id(token) -> None:
    _REQUEST_ID.reset(token)


class RequestIdFilter(logging.Filter):
    """把当前请求关联 ID 附加到每条日志记录（字段名 request_id）。"""

    def filter(self, record):
        record.request_id = _REQUEST_ID.get()
        return True
