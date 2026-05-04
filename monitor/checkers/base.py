# -*- coding: utf-8 -*-
"""
数据库检查器基类 (BaseDBChecker)

从 start_monitor.py 中提取，v3.0 重构。
提供统一的 check() 入口、连接管理和结果处理委托。
"""

from django.conf import settings

# ── 采集配置 ──────────────────────────────────────────────
# 单次采集任务超时（秒）：超过此时间的采集视为失败，记 DOWN，不阻塞其他任务。
COLLECT_TIMEOUT_SEC = getattr(settings, "COLLECT_TIMEOUT_SEC", 15)
# 并发采集线程数。
COLLECT_WORKERS = getattr(settings, "COLLECT_WORKERS", 20)

# ── 阈值配置 ──────────────────────────────────────────────
TBS_THRESHOLD = 90
LOCK_TIME_THRESHOLD = 10
CONN_THRESHOLD_PCT = 80

# ── Phase 2 智能引擎开关 ──────────────────────────────────
ENABLE_PHASE2_ENGINES = getattr(settings, "ENABLE_PHASE2_ENGINES", True)
CAPACITY_CHECK_INTERVAL_HOURS = getattr(settings, "CAPACITY_CHECK_INTERVAL_HOURS", 24)
HEALTH_CHECK_INTERVAL_HOURS = getattr(settings, "HEALTH_CHECK_INTERVAL_HOURS", 1)


class BaseDBChecker:
    """数据库检查器基类

    所有数据库类型的 Checker 继承此类，实现：
    - get_connection(config): 建立数据库连接
    - collect_metrics(config, conn): 采集指标并返回 dict
    """

    def __init__(self, command_instance):
        """
        Args:
            command_instance: start_monitor.Command 实例，
                              提供 process_result()、send_alert() 等方法
        """
        self.cmd = command_instance

    def get_connection(self, config):
        """获取数据库连接 - 子类实现"""
        raise NotImplementedError

    def collect_metrics(self, config, conn):
        """采集指标 - 子类实现，返回 dict"""
        raise NotImplementedError

    def check(self, config):
        """统一检查入口

        流程：
        1. 调用 get_connection() 建立连接
        2. 调用 collect_metrics() 采集指标
        3. 委托 cmd.process_result() 处理结果（存储 + 告警）
        4. 关闭连接
        异常时记录 DOWN 状态。
        """
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
                except Exception:
                    pass

        self.cmd.process_result(config, status, result_data)

    def db_label(self):
        """返回数据库类型标识"""
        return self.__class__.__name__.replace('Checker', '')
