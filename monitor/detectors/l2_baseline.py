# -*- coding: utf-8 -*-
"""L2 自适应基线检测 (phase6/10 §5.3)。

复用现有 BaselineEngine.check_current_against_baseline，加成熟度门槛:
某指标当前时间槽样本数 < BASELINE_MIN_SLOT_SAMPLES 时不用基线 (回退静态, 由 L1 兜底)。
"""
from django.utils import timezone

from monitor.detectors import thresholds as T
from monitor.detectors.metric_category import category_for_signal

# 累计计数器类指标: 对其做"偏离基线"无意义(单调增长), 排除以免刷屏。
# 按名称子串匹配。真正有意义的是 gauge 型(使用率/命中率/会话数/延迟/qps 等)。
_COUNTER_PATTERNS = (
    'total', '_writes', '_reads', '_written', 'pages', 'created_', 'com_',
    'handler_', 'bytes_', 'questions', 'opened_', '_sent', '_received',
    'innodb_data', 'innodb_rows', 'innodb_os_log', 'sort_', 'uptime',
    'binlog_position', 'server_id', 'aborted_', 'key_read', 'select_',
)


def _is_counter(metric_key: str) -> bool:
    mk = (metric_key or '').lower()
    return any(p in mk for p in _COUNTER_PATTERNS)


def detect_l2(config, metrics: dict) -> list:
    """返回 baseline_deviation 事件列表。异常判定沿用 baseline_engine 三重判定。

    降噪: 排除累计计数器类指标; 仅 critical/error 严重度才产事件(warning 仅入基线不产事故)。
    """
    events = []
    if not isinstance(metrics, dict):
        return events
    try:
        from monitor.baseline_engine import BaselineEngine
    except Exception:
        return events

    try:
        engine = BaselineEngine(config)
        anomalies = engine.check_current_against_baseline(metrics)
    except Exception:
        return events

    for item in anomalies or []:
        try:
            metric_name, current_val, baseline, anomaly_type, severity, reason = item
        except (ValueError, TypeError):
            continue
        # 降噪1: 排除累计计数器类指标
        if _is_counter(metric_name):
            continue
        # 成熟度门槛: baseline 样本不足则跳过 (交给 L1 静态阈值兜底)
        sample_n = getattr(baseline, 'sample_count', None) or getattr(baseline, 'n', None)
        if sample_n is not None and sample_n < T.BASELINE_MIN_SLOT_SAMPLES:
            continue
        sev = severity if severity in ('critical', 'error', 'warning', 'info') else 'warning'
        # 降噪2: baseline_deviation 仅 critical/error 才产事故, warning 不刷屏
        if sev not in ('critical', 'error'):
            continue
        # 降噪3: 相对偏离下限。σ 极小(近似常量)时微小波动会被误判 critical,
        # 要求当前值相对基线均值偏离 >= 15% 才算真异常。
        mean = getattr(baseline, 'mean', None)
        if mean and abs(mean) > 0:
            rel_dev = abs((current_val or 0) - mean) / abs(mean)
            if rel_dev < 0.15:
                continue
        events.append({
            'config_id': config.id,
            'db_type': config.db_type,
            'source': 'baseline',
            'signal': 'baseline_deviation',
            'metric_key': metric_name,
            'value': float(current_val) if current_val is not None else 0.0,
            'threshold': float(getattr(baseline, 'mean', 0) or 0),
            'severity': sev,
            'occurred_at': timezone.now().isoformat(),
            'dedup_key': f"{config.id}:baseline_deviation:{metric_name}",
            'category': category_for_signal('baseline_deviation', metric_name),
            'detail': {'anomaly_type': anomaly_type, 'reason': str(reason),
                       'baseline_mean': getattr(baseline, 'mean', None)},
        })
    return events
