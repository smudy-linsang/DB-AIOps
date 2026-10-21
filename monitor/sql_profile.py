# -*- coding: utf-8 -*-
"""SQL 耗时构成：语句层画像与分片层分布。纯函数，无 I/O。见 TDSQL_SLOWSQL_DESIGN.md §5.1、§5.2。

为什么需要它（T-07）：MySQL 8 / TXSQL 运行中的语句状态几乎恒为 ``executing``，
按会话状态分类只能得到"100% CPU"。真正能回答"每次执行平均花在哪"的数据就在
performance_schema digest 的累计计数器里（锁时间、CPU 时间、扫描行 / 返回行、
落盘临时表、归并排序……），这里把窗口内的增量换算成**每次执行的平均值**并给出结构化结论。
"""

# 结论阈值。改动前请先看 tests_sql_profile 里对应的边界用例。
SCAN_RATIO_WARN = 100          # 扫描行 / 返回行
SCAN_RATIO_GOOD = 10
LOCK_SHARE_WARN = 0.3          # 锁等待占平均耗时的比例
FULL_SCAN_SHARE_WARN = 0.5     # 全表扫描次数 / 执行次数
SKEW_WARN = 2.0                # 最热 set 份额 / 平均份额


def _div(a, b):
    return (a / b) if b else None


def _r(v, nd=3):
    return None if v is None else round(v, nd)


def profile(agg, slow_avg_ms=None, mysql_family=True):
    """agg: sqlstat_query 的聚合结果。返回画像 dict（含 breakdown 与 findings）。

    mysql_family=False（PG / Oracle / 达梦）时 sql_stat 的 reads_delta 是逻辑读块数而非扫描行，
    不能算"扫描行 / 返回行"，改报每次执行逻辑读。
    """
    exec_n = agg.get('exec', 0)
    if not exec_n:
        return {'exec': 0, 'available': False, 'findings': [],
                'note': '窗口内无执行记录'}
    elapsed = agg.get('elapsed_ms', 0)
    lock = agg.get('lock_ms', 0)
    cpu = agg.get('cpu_ms', 0)
    avg = elapsed / exec_n
    lock_avg = lock / exec_n
    # CPU 时间需要 events_statements_cpu consumer；没开时全为 0 —— 0 表示"未采集"而不是"没用 CPU"
    cpu_known = cpu > 0
    cpu_avg = cpu / exec_n if cpu_known else None
    other_avg = None
    if cpu_known:
        other_avg = max(avg - lock_avg - cpu_avg, 0.0)

    rows_sent = agg.get('rows_sent', 0)
    rows_exam = agg.get('rows_examined', 0)
    scan_ratio = _div(rows_exam, max(rows_sent, 1)) if rows_exam else 0.0
    tmp_tables = agg.get('tmp_tables', 0)
    if not mysql_family:
        p = {
            'available': True, 'exec': exec_n, 'total_ms': elapsed, 'avg_ms': _r(avg),
            'breakdown': {'lock_ms': None, 'cpu_ms': None, 'other_ms': None, 'cpu_known': False},
            'per_exec': {'rows_examined': None, 'rows_sent': _r(rows_sent / exec_n, 1),
                         'logical_reads': _r(rows_exam / exec_n, 1),
                         'sort_rows': None, 'sort_merge_passes': None},
            'ratios': {k: None for k in ('scan_efficiency', 'full_scan_share', 'full_join_share',
                                         'no_index_share', 'tmp_disk_share', 'error_share',
                                         'lock_share')},
            'is_slow': bool(slow_avg_ms is not None and avg >= slow_avg_ms),
            'note': '该库类型的 SQL 统计只有执行次数、耗时、返回行与逻辑读，无法拆分锁 / CPU / 扫描效率',
            'findings': [],
        }
        return p
    p = {
        'available': True,
        'exec': exec_n,
        'total_ms': elapsed,
        'avg_ms': _r(avg),
        'breakdown': {
            'lock_ms': _r(lock_avg),
            'cpu_ms': _r(cpu_avg),
            'other_ms': _r(other_avg),
            'cpu_known': cpu_known,
        },
        'per_exec': {
            'rows_examined': _r(rows_exam / exec_n, 1),
            'rows_sent': _r(rows_sent / exec_n, 1),
            'sort_rows': _r(agg.get('sort_rows', 0) / exec_n, 1),
            'sort_merge_passes': _r(agg.get('sort_merge', 0) / exec_n),
        },
        'ratios': {
            'scan_efficiency': _r(scan_ratio, 1),
            'full_scan_share': _r(agg.get('scan', 0) / exec_n),
            'full_join_share': _r(agg.get('full_join', 0) / exec_n),
            'no_index_share': _r(agg.get('no_index', 0) / exec_n),
            'tmp_disk_share': _r(_div(agg.get('tmp_disk', 0), tmp_tables)),
            'error_share': _r(agg.get('errors', 0) / exec_n),
            'lock_share': _r(_div(lock_avg, avg)),
        },
        'is_slow': bool(slow_avg_ms is not None and avg >= slow_avg_ms),
    }
    if not cpu_known:
        p['breakdown']['cpu_hint'] = (
            'CPU 时间未采集：需在目标库开启 performance_schema 的 events_statements_cpu consumer '
            "（UPDATE performance_schema.setup_consumers SET ENABLED='YES' "
            "WHERE NAME='events_statements_cpu'），开启后才能拆分 CPU 与 IO/等待")
    p['findings'] = findings(p)
    return p


def findings(p):
    """把画像翻译成结构化结论。每条都带触发它的数值，供界面与建议引擎共用。"""
    out = []
    r = p['ratios']
    pe = p['per_exec']
    if (r['scan_efficiency'] or 0) >= SCAN_RATIO_WARN and (pe['rows_examined'] or 0) >= 1000:
        out.append({'code': 'SCAN_INEFFICIENT', 'severity': 'high',
                    'text': f"每次执行平均扫描 {pe['rows_examined']:.0f} 行、只返回 {pe['rows_sent']:.0f} 行"
                            f"（{r['scan_efficiency']:.0f} 倍），大量无效扫描",
                    'evidence': {'rows_examined': pe['rows_examined'], 'rows_sent': pe['rows_sent']}})
    if (r['full_scan_share'] or 0) >= FULL_SCAN_SHARE_WARN:
        out.append({'code': 'FULL_SCAN', 'severity': 'high' if (r['no_index_share'] or 0) > 0 else 'medium',
                    'text': f"{r['full_scan_share'] * 100:.0f}% 的执行做了全表扫描",
                    'evidence': {'full_scan_share': r['full_scan_share']}})
    if (r['full_join_share'] or 0) > 0:
        out.append({'code': 'FULL_JOIN', 'severity': 'high',
                    'text': f"{r['full_join_share'] * 100:.0f}% 的执行出现无索引 JOIN（被驱动表全表扫描）",
                    'evidence': {'full_join_share': r['full_join_share']}})
    if (r['tmp_disk_share'] or 0) > 0:
        out.append({'code': 'TMP_DISK', 'severity': 'medium',
                    'text': f"{r['tmp_disk_share'] * 100:.0f}% 的内部临时表落盘（GROUP BY / ORDER BY / DISTINCT / UNION）",
                    'evidence': {'tmp_disk_share': r['tmp_disk_share']}})
    if (pe['sort_merge_passes'] or 0) > 0:
        out.append({'code': 'SORT_MERGE', 'severity': 'medium',
                    'text': f"每次执行平均 {pe['sort_merge_passes']} 轮归并排序，排序缓冲不足或排序行过多",
                    'evidence': {'sort_merge_passes': pe['sort_merge_passes']}})
    if (r['lock_share'] or 0) >= LOCK_SHARE_WARN:
        out.append({'code': 'LOCK_WAIT', 'severity': 'high',
                    'text': f"平均耗时中 {r['lock_share'] * 100:.0f}% 是锁等待（{p['breakdown']['lock_ms']}ms）",
                    'evidence': {'lock_ms': p['breakdown']['lock_ms'], 'avg_ms': p['avg_ms']}})
    if (r['error_share'] or 0) > 0:
        out.append({'code': 'ERRORS', 'severity': 'medium',
                    'text': f"{r['error_share'] * 100:.1f}% 的执行报错",
                    'evidence': {'error_share': r['error_share']}})
    return out


def set_distribution(by_set):
    """by_set: {set_name: agg}。返回按总耗时降序的分布与倾斜度（TDSQL 分片层）。"""
    items = [(s, a) for s, a in by_set.items() if a.get('exec', 0) > 0]
    total = sum(a.get('elapsed_ms', 0) for _, a in items)
    n = len([s for s, _ in items if s])
    rows = []
    for s, a in sorted(items, key=lambda kv: kv[1].get('elapsed_ms', 0), reverse=True):
        rows.append({
            'set_name': s or '(未区分 set)',
            'exec': a.get('exec', 0),
            'total_ms': a.get('elapsed_ms', 0),
            'avg_ms': _r(_div(a.get('elapsed_ms', 0), a.get('exec', 0))),
            'rows_examined_per_exec': _r(_div(a.get('rows_examined', 0), a.get('exec', 0)), 1),
            'share': _r(_div(a.get('elapsed_ms', 0), total)),
        })
    skew = None
    hottest = None
    if n >= 2 and total > 0:
        hottest = rows[0]['set_name']
        skew = _r(rows[0]['share'] * n, 2)
    return {'sets': rows, 'set_count': n, 'hottest_set': hottest, 'skew': skew,
            'is_skewed': bool(skew is not None and skew >= SKEW_WARN)}
