# -*- coding: utf-8 -*-
"""L1 硬阈值检测 (phase6/10 §5.1/§5.2)。确定性规则, 无须基线, 秒级。

输入是采集器落库后的 metrics dict (collector 尾部调用) 或哨兵黄金量。
signal: conn_high / space_high / repl_broken / blocked_session。
(instance_down 由 sentinel 探活直接产生, deadlock 属 L3。)
"""
from django.utils import timezone

from monitor.detectors import thresholds as T
from monitor.detectors.metric_category import category_for_signal


def _evt(config, signal, metric_key, value, threshold, severity, occurred_at=None, detail=None, sub=None):
    dk = f"{config.id}:{signal}" + (f":{sub}" if sub else "")
    return {
        'config_id': config.id,
        'db_type': config.db_type,
        'source': 'collector',
        'signal': signal,
        'metric_key': metric_key,
        'value': float(value) if value is not None else 0.0,
        'threshold': float(threshold),
        'severity': severity,
        'occurred_at': (occurred_at or timezone.now()).isoformat(),
        'dedup_key': dk,
        'category': category_for_signal(signal, metric_key),
        'detail': detail or {},
    }


def detect_l1(config, metrics: dict) -> list:
    """对一份采集指标做 L1 判定, 返回 event dict 列表。"""
    events = []
    if not isinstance(metrics, dict):
        return events

    # --- 连接高水位 ---
    conn_pct = metrics.get('conn_usage_pct')
    if conn_pct is None:
        # 兜底: 用 threads_connected/max_connections 计算
        tc = metrics.get('threads_connected') or metrics.get('active_connections')
        mc = metrics.get('max_connections')
        if tc is not None and mc:
            conn_pct = T.helper_pct(tc, mc)
    if isinstance(conn_pct, (int, float)):
        if conn_pct >= T.CONN_CRIT_PCT:
            events.append(_evt(config, 'conn_high', 'conn_usage_pct', conn_pct,
                               T.CONN_CRIT_PCT, 'critical'))
        elif conn_pct >= T.CONN_WARN_PCT:
            events.append(_evt(config, 'conn_high', 'conn_usage_pct', conn_pct,
                               T.CONN_WARN_PCT, 'warning'))

    # --- 空间高水位 (表空间/库列表) ---
    # 兼容多种字段: tablespaces[], database_sizes[](无pct跳过), space_usage_pct
    tbs_list = metrics.get('tablespaces') or metrics.get('tablespace_usage') or []
    for tbs in tbs_list if isinstance(tbs_list, list) else []:
        if not isinstance(tbs, dict):
            continue
        pct = tbs.get('used_pct') or tbs.get('usage_pct')
        name = tbs.get('name') or tbs.get('tablespace_name') or 'unknown'
        if isinstance(pct, (int, float)):
            if pct >= T.SPACE_CRIT_PCT:
                events.append(_evt(config, 'space_high', 'tablespace_used_pct', pct,
                                   T.SPACE_CRIT_PCT, 'critical', sub=name,
                                   detail={'object': name}))
            elif pct >= T.SPACE_WARN_PCT:
                events.append(_evt(config, 'space_high', 'tablespace_used_pct', pct,
                                   T.SPACE_WARN_PCT, 'warning', sub=name,
                                   detail={'object': name}))
    # 单值型空间指标
    sp = metrics.get('space_usage_pct') or metrics.get('disk_usage_pct')
    if isinstance(sp, (int, float)) and sp >= T.SPACE_WARN_PCT:
        sev = 'critical' if sp >= T.SPACE_CRIT_PCT else 'warning'
        thr = T.SPACE_CRIT_PCT if sev == 'critical' else T.SPACE_WARN_PCT
        events.append(_evt(config, 'space_high', 'space_usage_pct', sp, thr, sev))

    # --- 复制中断 ---
    # 关键: 仅当"确实配置了复制"才判中断, 否则单机实例(master_host=N/A)会误报。
    repl_broken = False
    repl_detail = {}
    master_host = str(metrics.get('master_host', '') or '').strip()
    repl_configured = master_host not in ('', 'N/A', 'None', 'none', '0.0.0.0')
    if repl_configured and ('slave_io_running' in metrics or 'slave_sql_running' in metrics):
        io = str(metrics.get('slave_io_running', 'Yes'))
        sql = str(metrics.get('slave_sql_running', 'Yes'))
        if io.lower() not in ('yes', 'true', '1') or sql.lower() not in ('yes', 'true', '1'):
            repl_broken = True
            repl_detail = {'slave_io_running': io, 'slave_sql_running': sql,
                           'master_host': master_host}
    if metrics.get('replication_broken') is True:
        repl_broken = True
    if repl_broken:
        events.append(_evt(config, 'repl_broken', 'replication', 0, 1, 'critical',
                           detail=repl_detail))
    # 复制延迟
    lag = metrics.get('replication_lag_sec') or metrics.get('seconds_behind_master')
    if isinstance(lag, (int, float)) and lag >= 30:
        events.append(_evt(config, 'repl_lag', 'replication_lag_sec', lag, 30,
                           'warning' if lag < 300 else 'critical'))

    return events


def detect_blocked_from_ash(config, sample_rows: list, sample_time=None) -> list:
    """从 ASH 样本检测阻塞会话 (phase6/10 §5.2)。sentinel/ash 每次采样调用。"""
    if not sample_rows:
        return []
    blocked = [s for s in sample_rows if s.get('is_blocked')]
    if not blocked:
        return []
    max_wait = max((s.get('active_secs') or 0) for s in blocked)
    if max_wait >= T.BLOCK_WAIT_CRIT_SEC:
        sev = 'critical'
    elif max_wait >= T.BLOCK_WAIT_WARN_SEC:
        sev = 'warning'
    else:
        return []  # info 不产事故
    blockers = sorted({str(s.get('blocker_id')) for s in blocked if s.get('blocker_id')})
    chains = [{'blocker': str(s.get('blocker_id')), 'waiter': str(s.get('session_id')),
               'wait_sec': s.get('active_secs') or 0} for s in blocked]
    return [_evt(config, 'blocked_session', 'blocked_sessions', len(blocked),
                 0, sev, occurred_at=sample_time,
                 detail={'waiters': len(blocked), 'blockers': blockers,
                         'max_wait_sec': max_wait, 'chains': chains})]
