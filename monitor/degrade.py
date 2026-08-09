# -*- coding: utf-8 -*-
"""降级留痕：让"静默降级"变成"有记录的降级"。

169 处 except-pass 里，多数本身是合理的（可选字段补查失败不该让整轮采集崩）。
问题不在于捕获异常，而在于**捕获之后没有任何痕迹** —— 一个功能可能已经
降级运行了几个月，没人知道。这里提供统一的计数与查询入口。
"""
import logging
import threading
from collections import Counter

logger = logging.getLogger(__name__)

_COUNTS = Counter()
_LOCK = threading.Lock()


def note(scope: str, reason: str = '', exc: Exception = None) -> None:
    """记一次降级。scope 形如 'ash.oracle_objname'、'notify.dingtalk'。"""
    with _LOCK:
        _COUNTS[scope] += 1
        count = _COUNTS[scope]
    # 前 3 次与每 100 次打 warning，其余 debug —— 既不淹没日志也不彻底静默
    level = logging.WARNING if count <= 3 or count % 100 == 0 else logging.DEBUG
    logger.log(level, '[degrade] %s (第 %d 次)%s%s', scope, count,
               f' 原因={reason}' if reason else '',
               f' 异常={exc.__class__.__name__}: {exc}' if exc else '')


def snapshot() -> dict:
    """当前累计降级计数，供 /api/v1/system/degradations 暴露。"""
    with _LOCK:
        return dict(_COUNTS)


def reset() -> None:
    with _LOCK:
        _COUNTS.clear()
