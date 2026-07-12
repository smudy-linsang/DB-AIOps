# -*- coding: utf-8 -*-
"""L3 复合规则检测 (phase6/10 §5.4)。多指标联合, 高置信直升事故。

在 collector 尾部调用: 传入本轮 metrics + 最近 ASH 阻塞信息 + 基线派生。
本期实现: 锁风暴(结合L1阻塞)、连接风暴、慢查询突增、死锁频发。
"""
from django.utils import timezone

from monitor.detectors import thresholds as T
from monitor.detectors.metric_category import category_for_signal


def _evt(config, signal, metric_key, value, threshold, severity, detail):
    return {
        'config_id': config.id, 'db_type': config.db_type, 'source': 'collector',
        'signal': signal, 'metric_key': metric_key,
        'value': float(value or 0), 'threshold': float(threshold), 'severity': severity,
        'occurred_at': timezone.now().isoformat(),
        'dedup_key': f"{config.id}:{signal}",
        'category': category_for_signal(signal, metric_key),
        'detail': dict(detail, composite=True),
    }


def detect_l3(config, metrics: dict, baseline_means: dict = None, blocked_info: dict = None) -> list:
    """baseline_means: {metric: mean}; blocked_info: 最近ASH阻塞 detail(可空)。"""
    events = []
    if not isinstance(metrics, dict):
        return events
    bm = baseline_means or {}

    def mean_of(k):
        v = bm.get(k)
        return v if isinstance(v, (int, float)) and v > 0 else None

    # --- 连接风暴: threads_connected 突增≥2×基线 ---
    tc = metrics.get('threads_connected') or metrics.get('active_connections')
    tc_mean = mean_of('threads_connected') or mean_of('active_connections')
    if isinstance(tc, (int, float)) and tc_mean and tc >= T.CONN_SPIKE_RATIO * tc_mean:
        events.append(_evt(config, 'conn_storm', 'threads_connected', tc,
                           T.CONN_SPIKE_RATIO * tc_mean, 'critical',
                           {'current': tc, 'baseline': tc_mean}))

    # --- 慢查询突增 ---
    slow = metrics.get('slow_queries') or metrics.get('slow_queries_total')
    slow_mean = mean_of('slow_queries') or mean_of('slow_queries_total')
    if isinstance(slow, (int, float)) and slow_mean and slow >= T.SLOW_SURGE_RATIO * slow_mean:
        events.append(_evt(config, 'slow_surge', 'slow_queries', slow,
                           T.SLOW_SURGE_RATIO * slow_mean, 'warning',
                           {'current': slow, 'baseline': slow_mean}))

    # --- 死锁频发 (增量需外部提供; 这里用绝对增量近似: innodb_deadlocks 与上轮差在collector算) ---
    dl_delta = metrics.get('_deadlock_delta_5min')
    if isinstance(dl_delta, (int, float)) and dl_delta >= T.DEADLOCK_SURGE_5MIN:
        events.append(_evt(config, 'deadlock_surge', 'innodb_deadlocks', dl_delta,
                           T.DEADLOCK_SURGE_5MIN, 'warning', {'delta_5min': dl_delta}))

    # --- 锁风暴强化: 已有阻塞 + threads_running 突增 → 提升 blocked_session 置信 ---
    tr = metrics.get('threads_running')
    tr_mean = mean_of('threads_running')
    if (blocked_info and blocked_info.get('waiters', 0) > 0 and
            isinstance(tr, (int, float)) and tr_mean and
            tr >= T.THREADS_RUNNING_SPIKE_RATIO * tr_mean):
        events.append(_evt(config, 'blocked_session', 'blocked_sessions',
                           blocked_info.get('waiters', 0), 0, 'critical',
                           dict(blocked_info, threads_running=tr, tr_baseline=tr_mean,
                                lock_storm=True)))
    return events
