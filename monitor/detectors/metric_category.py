# -*- coding: utf-8 -*-
"""指标/信号 → 事故类别映射 (phase6/00 §4, phase6/10 §5.3)。"""

# signal → category
SIGNAL_CATEGORY = {
    'instance_down': 'availability',
    'blocked_session': 'lock',
    'deadlock_surge': 'lock',
    'conn_high': 'connection',
    'conn_storm': 'connection',
    'space_high': 'capacity',
    'repl_broken': 'replication',
    'repl_lag': 'replication',
    'slow_surge': 'performance',
    'config_drift': 'config',
    'plan_change': 'performance',       # Phase 7D-01: 执行计划突变
    'long_transaction': 'performance',  # Phase 7D-02: 长事务
    'baseline_deviation': 'performance',  # 默认, 可被 metric 细化
}

# metric_key 关键字 → category (用于 baseline_deviation 细化)
_METRIC_CATEGORY_HINT = [
    (('conn', 'threads_connected', 'session'), 'connection'),
    (('lock', 'blocked', 'deadlock'), 'lock'),
    (('space', 'tablespace', 'disk', 'undo', 'temp'), 'capacity'),
    (('repl', 'slave', 'lag', 'apply'), 'replication'),
    (('buffer', 'hit', 'qps', 'tps', 'slow', 'latency', 'io'), 'performance'),
]


def category_for_signal(signal: str, metric_key: str = '') -> str:
    cat = SIGNAL_CATEGORY.get(signal)
    if cat and signal != 'baseline_deviation':
        return cat
    mk = (metric_key or '').lower()
    for keys, c in _METRIC_CATEGORY_HINT:
        if any(k in mk for k in keys):
            return c
    return cat or 'other'
