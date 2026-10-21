# -*- coding: utf-8 -*-
"""SQL 环比：自动基线、全量分类、日周期 SQL 识别与逐次运行比较。纯函数。

见 TDSQL_SLOWSQL_DESIGN.md §7；修 TDSQL_SLOWSQL_REVIEW.md T-08：
原期间对比只比 ASH 活跃秒的 Top 10、需手工选窗，不比执行次数 / 平均耗时，
Top 10 之外的 SQL 冲进前 10 会被误标"新出现"。
"""
import math
from datetime import timedelta
from statistics import median

STATUS_ORDER = {'regressed': 0, 'new': 1, 'improved': 2, 'gone': 3, 'stable': 4}

# 判定周期 SQL 的口径（设计 §7.2）
PERIODIC_HOUR_SHARE = 0.8      # 选出的小时承担 ≥80% 的执行
PERIODIC_MAX_HOURS = 6         # 一天里最多 6 个活跃小时，否则是持续型 SQL
PERIODIC_DAY_SHARE = 0.7       # ≥70% 的天里都出现


def baseline_window(fd, td, kind):
    """kind: prev（相邻上一窗口）| day（昨日同时段）| week（上周同时段）。"""
    span = td - fd
    if kind == 'prev':
        return fd - span, fd
    if kind == 'day':
        return fd - timedelta(days=1), td - timedelta(days=1)
    if kind == 'week':
        return fd - timedelta(days=7), td - timedelta(days=7)
    raise ValueError(f'未知基线类型: {kind}')


def _side(a):
    if not a:
        return None
    n = a.get('exec', 0)
    return {
        'exec': n,
        'total_ms': a.get('elapsed_ms', 0),
        'avg_ms': round(a.get('elapsed_ms', 0) / n, 3) if n else None,
        'rows_examined_per_exec': round(a.get('rows_examined', 0) / n, 1) if n else None,
        'lock_ms_per_exec': round(a.get('lock_ms', 0) / n, 3) if n else None,
    }


def classify(cur_aggs, base_aggs, avg_ratio=1.5, min_delta_ms=5.0,
             total_ratio=2.0, min_total_delta_ms=1000.0):
    """对两个窗口的**全量** digest 分类。返回按 (状态, 影响) 排序的列表。

    cur_aggs / base_aggs: {digest: agg}（sqlstat_query.window_by_digest 的结果）。
    调用方须保证 base_aggs 是"基线窗口中出现过的全部 digest"（至少对 cur 里的 digest 是全的），
    否则"新增"会误判 —— 这正是原实现只取 Top 10 的毛病。
    """
    out = []
    for d in set(cur_aggs) | set(base_aggs):
        c, b = _side(cur_aggs.get(d)), _side(base_aggs.get(d))
        if c and c['exec'] and not (b and b['exec']):
            status = 'new'
        elif b and b['exec'] and not (c and c['exec']):
            status = 'gone'
        else:
            status = 'stable'
            ac, ab = c['avg_ms'] or 0, b['avg_ms'] or 0
            tc, tb = c['total_ms'], b['total_ms']
            if (ab and ac >= ab * avg_ratio and ac - ab >= min_delta_ms) or \
                    (tb and tc >= tb * total_ratio and tc - tb >= min_total_delta_ms):
                status = 'regressed'
            elif ab and ac <= ab / avg_ratio and ab - ac >= min_delta_ms:
                status = 'improved'
        item = {'digest': d, 'status': status, 'current': c, 'baseline': b,
                'avg_ratio': None, 'exec_ratio': None,
                'impact_ms': (c['total_ms'] if c else 0) - (b['total_ms'] if b else 0)}
        if c and b and b['avg_ms']:
            item['avg_ratio'] = round((c['avg_ms'] or 0) / b['avg_ms'], 2)
        if c and b and b['exec']:
            item['exec_ratio'] = round(c['exec'] / b['exec'], 2)
        out.append(item)
    out.sort(key=lambda i: (STATUS_ORDER[i['status']], -abs(i['impact_ms'])))
    return out


def summarize_counts(items):
    counts = {k: 0 for k in STATUS_ORDER}
    for i in items:
        counts[i['status']] += 1
    return counts


# ------------------------------------------------------------------ 周期 SQL

def _local_hour(dt, tz):
    return dt.astimezone(tz) if tz is not None else dt


def detect_daily(hourly, days, tz=None):
    """hourly: [(hour_start, exec, elapsed_ms)]。判定是否为日周期 SQL 并给出逐次运行。

    返回 None（不是周期 SQL / 数据不足）或
    {'hours': [...], 'days_seen': n, 'days': days, 'runs': [...], 'latest': {...},
     'median_avg_ms': x, 'latest_vs_median': r, 'median_total_ms': y}
    """
    pts = [(_local_hour(h, tz), ex, ms) for h, ex, ms in hourly if ex > 0]
    total = sum(ex for _, ex, _ in pts)
    if total == 0 or days < 3:
        return None
    by_hour = {}
    for h, ex, _ in pts:
        by_hour[h.hour] = by_hour.get(h.hour, 0) + ex
    picked, acc = [], 0
    for hr, ex in sorted(by_hour.items(), key=lambda kv: kv[1], reverse=True):
        picked.append(hr)
        acc += ex
        if acc >= total * PERIODIC_HOUR_SHARE:
            break
    if len(picked) > PERIODIC_MAX_HOURS:
        return None
    # 运行跨午夜（如 23 点到 1 点）时，把"一天"的起点放在活跃小时之外最大空档之后
    hs = sorted(picked)
    gaps = [((hs[(i + 1) % len(hs)] - hs[i]) % 24 or 24, hs[(i + 1) % len(hs)]) for i in range(len(hs))]
    day_start = max(gaps)[1]
    runs = {}
    for h, ex, ms in pts:
        if h.hour not in picked:
            continue
        run_day = (h - timedelta(hours=day_start)).date()
        r = runs.setdefault(run_day, {'exec': 0, 'total_ms': 0})
        r['exec'] += ex
        r['total_ms'] += ms
    if len(runs) < math.ceil(days * PERIODIC_DAY_SHARE):
        return None
    ordered = []
    for day in sorted(runs):
        r = runs[day]
        ordered.append({'run_day': day.isoformat(), 'exec': r['exec'], 'total_ms': r['total_ms'],
                        'avg_ms': round(r['total_ms'] / r['exec'], 3) if r['exec'] else None})
    latest, history = ordered[-1], ordered[:-1]
    med_avg = median([r['avg_ms'] for r in history if r['avg_ms'] is not None]) if history else None
    med_total = median([r['total_ms'] for r in history]) if history else None
    return {
        'hours': sorted(picked, key=lambda x: (x - day_start) % 24),
        'days_seen': len(runs), 'days': days, 'runs': ordered, 'latest': latest,
        'median_avg_ms': round(med_avg, 3) if med_avg is not None else None,
        'median_total_ms': med_total,
        'latest_vs_median': (round(latest['avg_ms'] / med_avg, 2)
                             if med_avg and latest['avg_ms'] is not None else None),
        'latest_total_vs_median': (round(latest['total_ms'] / med_total, 2) if med_total else None),
    }
