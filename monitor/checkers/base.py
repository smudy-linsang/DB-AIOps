# -*- coding: utf-8 -*-
"""
数据库检查器基类 (BaseDBChecker)

从 start_monitor.py 中提取，v3.0 重构。
提供统一的 check() 入口、连接管理和结果处理委托。
"""

from django.conf import settings

# ── 采集配置 ──────────────────────────────────────────────
# 单次采集任务超时（秒）：超过此时间的采集视为失败，记 DOWN，不阻塞其他任务。
COLLECT_TIMEOUT_SEC = getattr(settings, "COLLECT_TIMEOUT_SEC", 60)
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
            # W6-L2: 整轮采集失败（实例 DOWN），留痕供 /system/degradations 观察
            from monitor import degrade
            degrade.note(f'collect.{self.db_label()}', reason=config.name, exc=e)
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


# ── 安全采集助手（W6-B1 / REVIEW-08）────────────────────────
# 动因：checkers 里大量写法是
#     cursor.execute(SQL)                 # ← 在 try 外面
#     try: v = cursor.fetchone()['Value']
#     except: v = default
# 作者显然预见到"取不到值"，却没预见到"这条语句本身不被支持"。
# 实测：MariaDB 上 `SHOW MASTER LOGS` 在未开 binlog 时直接抛 1381，
# 异常越过整个 collect_metrics —— **该实例这一轮的所有指标全部丢失**，
# 而不只是丢掉 binlog 那一项。mysql.py 有 76 处、gbase.py 有 14 处同款写法。
#
# 这些助手把"执行 + 取值"合成一次安全调用：失败返回默认值并留痕（degrade.note），
# 既不让单项指标拖垮整轮采集，也不让降级变成无人知晓的静默。

def safe_execute(cursor, sql, scope, params=None) -> bool:
    """执行一条采集语句。成功 True；失败留痕并返回 False。"""
    from monitor import degrade
    try:
        cursor.execute(sql, params) if params else cursor.execute(sql)
        return True
    except Exception as e:
        degrade.note(f'checker.{scope}', f'语句不被支持或执行失败: {sql[:60]}', e)
        return False


def safe_scalar(cursor, sql, scope, default=None, column='Value', cast=None):
    """执行并取单值（默认取 SHOW 语句的 Value 列）。任何环节失败都返回 default。"""
    from monitor import degrade
    if not safe_execute(cursor, sql, scope):
        return default
    try:
        row = cursor.fetchone()
        if row is None:
            return default
        val = row[column] if isinstance(row, dict) else row[0]
        return cast(val) if (cast and val is not None) else val
    except Exception as e:
        degrade.note(f'checker.{scope}', f'取值失败: {sql[:60]}', e)
        return default


def safe_rows(cursor, sql, scope, default=None) -> list:
    """执行并取全部行。失败返回 default（默认空列表）。"""
    from monitor import degrade
    if not safe_execute(cursor, sql, scope):
        return [] if default is None else default
    try:
        return list(cursor.fetchall())
    except Exception as e:
        degrade.note(f'checker.{scope}', f'取行失败: {sql[:60]}', e)
        return [] if default is None else default
