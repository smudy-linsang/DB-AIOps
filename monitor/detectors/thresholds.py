# -*- coding: utf-8 -*-
"""检测阈值常量 (phase6/10 §5.1)。可被 AlertThresholdTemplate 覆盖(未来)。"""

CONN_CRIT_PCT = 95
CONN_WARN_PCT = 80
SPACE_CRIT_PCT = 95
SPACE_WARN_PCT = 90
BLOCK_WAIT_CRIT_SEC = 30
BLOCK_WAIT_WARN_SEC = 10

# L3 复合规则
THREADS_RUNNING_SPIKE_RATIO = 1.5   # 较基线↑50%
CONN_SPIKE_RATIO = 2.0              # 连接数突增≥2×基线
SLOW_SURGE_RATIO = 3.0             # 慢查询增速≥基线3× (保留, 供未来增量基线)
SLOW_SURGE_MIN_DELTA = 10          # 单采集周期慢查询增量绝对阈值
DEADLOCK_SURGE_5MIN = 3            # 5min 死锁增量阈值

# 基线成熟度门槛 (§5.3)
BASELINE_MIN_SLOT_SAMPLES = 14     # 少于两周样本回退静态阈值


def helper_pct(used, total):
    try:
        return round(float(used) / float(total) * 100, 2) if float(total) > 0 else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
