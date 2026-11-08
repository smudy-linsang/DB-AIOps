#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-005R G-11 (round 2): sql-periodic day-share gate diagnostics (read-only).

Question answered:
  * Why does GET /perf/sql-periodic/?days=7 return items=0 ?
  * Is that a logic defect, or purely "collection duration too short" ?
  * When does it become reachable (re-test condition)?

Method:
  1. sweep the platform endpoint with days = 3,4,5,6,7 (threshold = ceil(days*0.7)
     run-days required for a digest);
  2. replicate monitor/sql_compare.py::detect_daily WITHOUT the day-share threshold,
     using the SAME platform primitives (window_by_digest / hourly_series), to count
     how many run-days each digest actually has;
  3. full-retention coverage (MIN/MAX time) per instance to date the collection start.

Only SELECT on sql_stat + GET on the platform API. No writes, no restarts.
Auth: reuses reproduce_g11_day_baseline.py::auth(); when the platform rejects the
self-issued token it falls back to "reset local dev account password + login" and
that side effect is recorded in side_effects (never the secret itself).

Usage:
  set BASE=http://127.0.0.1:8009
  python reproduce_g11_periodic_gate.py --cid 18 --cid2 19 --out C:\...\g11_periodic_gate.json
"""
import argparse
import importlib.util
import json
import math
import os
import sys
from datetime import timedelta

REPO = os.environ.get('DBOPS_REPO', r'C:\DB-AIOps')
sys.path.insert(0, REPO)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402
django.setup()

from django.utils import timezone  # noqa: E402

MOD = os.path.join(REPO, 'docs', 'pm', 'reports', 'evidence', 'DB-005R-m-team',
                   'reproduce_g11_day_baseline.py')
_spec = importlib.util.spec_from_file_location('g11mod', MOD)
g11 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g11)

from monitor.api_views_perf import _ts_cursor  # noqa: E402
from monitor.sqlstat_query import hourly_series, texts_for, window_by_digest  # noqa: E402
from monitor.sql_compare import (PERIODIC_DAY_SHARE, PERIODIC_HOUR_SHARE,  # noqa: E402
                                 PERIODIC_MAX_HOURS, _local_hour)


def relaxed_detect(hourly, tz):
    """detect_daily 的同规则复刻，但**不施加** day_share 门槛。

    返回 {'hours': [...], 'run_days': n, 'runs': [...], 'total_exec': n}；
    若不满足小时集中度（>MAX_HOURS）则返回 None（说明该 digest 属持续型，非日周期）。
    """
    pts = [(_local_hour(h, tz), ex, ms) for h, ex, ms in hourly if ex > 0]
    total = sum(ex for _, ex, _ in pts)
    if total == 0:
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
    ordered = [{'run_day': d.isoformat(), 'exec': runs[d]['exec'],
                'total_ms': runs[d]['total_ms']} for d in sorted(runs)]
    return {'hours': sorted(picked, key=lambda x: (x - day_start) % 24),
            'day_start_hour': day_start,
            'run_days': len(runs),
            'runs': ordered,
            'total_exec': total}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cid', type=int, default=18)
    ap.add_argument('--cid2', type=int, default=19)
    ap.add_argument('--window-days', type=int, default=7)
    ap.add_argument('--out', default=r'C:\dispatcher\probe_out\g11_periodic_gate.json')
    args = ap.parse_args()
    cids = [c for c in (args.cid, args.cid2) if c]

    token, side = g11.auth()
    out = {'generated_at': timezone.now().isoformat(),
           'side_effects': side,
           'base': g11.BASE.replace('127.0.0.1', '<local-host>').replace('8009', '<platform-port>'),
           'window_days': args.window_days,
           'rule': {'hour_share': PERIODIC_HOUR_SHARE, 'max_hours': PERIODIC_MAX_HOURS,
                    'day_share': PERIODIC_DAY_SHARE},
           'threshold_by_days': {str(d): math.ceil(d * PERIODIC_DAY_SHARE)
                                 for d in (3, 4, 5, 6, 7)},
           'instances': {}}
    if not token:
        out['error'] = 'auth failed'
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(g11.mask(out), f, ensure_ascii=False, indent=2)
        print('AUTH FAILED')
        return 2

    tz = timezone.get_current_timezone()
    td = timezone.now()
    fd = td - timedelta(days=args.window_days)
    for cid in cids:
        print('=== cid=%d ===' % cid)
        inst = {}
        # 1) full retention coverage
        with _ts_cursor() as cur:
            if cur is None:
                inst['error'] = 'TSDB down'
                out['instances'][str(cid)] = inst
                continue
            cur.execute('SELECT MIN(time), MAX(time), COUNT(*), COUNT(DISTINCT sql_digest) '
                        'FROM sql_stat WHERE db_config_id=%s', [cid])
            r = cur.fetchone()
            inst['retention'] = {'min_time': str(r[0]) if r[0] else None,
                                  'max_time': str(r[1]) if r[1] else None,
                                  'rows': r[2], 'distinct_digests': r[3]}
            # 2) per local-date row counts over the window
            cur.execute('SELECT time FROM sql_stat WHERE db_config_id=%s AND time >= %s '
                        'AND time <= %s', [cid, fd, td])
            ts = [x[0] for x in cur.fetchall()]
            per_date = {}
            for t in ts:
                d = t.astimezone(tz).date().isoformat()
                per_date[d] = per_date.get(d, 0) + 1
            inst['rows_per_local_date'] = dict(sorted(per_date.items()))
            # 3) per-digest run-day count (relaxed detect_daily)
            cands = list(window_by_digest(cur, cid, fd, td, limit=200))
            series = hourly_series(cur, cid, fd, td, cands)
            texts = texts_for(cur, cid, cands, fd)
            rows = []
            for d in cands:
                rdet = relaxed_detect(series.get(d, []), tz)
                if rdet is None:
                    continue
                rows.append({'digest': d, 'run_days': rdet['run_days'],
                             'hours': rdet['hours'], 'day_start_hour': rdet['day_start_hour'],
                             'total_exec': rdet['total_exec'],
                             'runs': rdet['runs'],
                             'sql_text_sample': (texts.get(d) or (None, None))[0]})
            rows.sort(key=lambda x: (-x['run_days'], -x['total_exec']))
            inst['candidates'] = rows[:20]
            inst['candidate_total'] = len(rows)
            inst['max_run_days'] = max([x['run_days'] for x in rows], default=0)
        # 4) endpoint sweep
        sweep = []
        for dd in (3, 4, 5, 6, 7):
            res = g11.call_periodic(cid, token, dd)
            sweep.append({'days': dd,
                          'required_run_days': math.ceil(dd * PERIODIC_DAY_SHARE),
                          'http': res.get('http'),
                          'items_returned': res.get('items_returned')})
        inst['endpoint_sweep'] = sweep
        out['instances'][str(cid)] = inst
        print('  retention min=%s max=%s rows=%s' % (
            inst['retention']['min_time'], inst['retention']['max_time'], inst['retention']['rows']))
        print('  max_run_days=%s candidates=%s' % (inst.get('max_run_days'), inst.get('candidate_total')))
        for s in sweep:
            print('  days=%s required=%s http=%s items=%s' % (
                s['days'], s['required_run_days'], s['http'], s['items_returned']))

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(g11.mask(out), f, ensure_ascii=False, indent=2)
    print('WROTE %s' % args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
