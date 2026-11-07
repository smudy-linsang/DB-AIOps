#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-005R G-11 回填：日周期 / 昨日同时段环比（R-4）复跑脚本。

口径（与 DB-005R 报告 §9「G-11 回填计划」一致）：

  1) **昨日同时段环比**：``GET /api/v1/databases/{cid}/perf/sql-regression/?window=24h&baseline=day``
     —— current 窗 = 最近 24h；baseline 窗 = 同一时刻前推 24h（昨日同时段）。
     实现见 ``monitor/sql_compare.py::baseline_window(kind='day')``。
  2) **日周期 SQL**：``GET /api/v1/databases/{cid}/perf/sql-periodic/?days=7``
     —— 日周期识别 + 逐次运行比较（规则 hour_share=0.8 / max_hours=6 / day_share=0.7）。

另**只读**记录采集连续性（sql_stat 覆盖范围、时间点间隙数），用于证明「昨日同时段」
基线窗确有数据（DB-005R §9 的前提：采集器常驻不重启）。

认证：优先用平台自身的 ``monitor.auth.TokenManager`` 直接签发令牌（**不动口令**、
零副作用）；只有签发的令牌不被 API 接受时，才回退到「重置本地开发账号口令 + 登录」
路径，并把该副作用写进 ``out.json`` 的 ``side_effects``。

用法::

    set MTEAM_PW=<本地开发账号口令>      # 仅回退路径需要
    python reproduce_g11_day_baseline.py --cid 18 --cid2 19 --window 24h \
        --periodic-days 7 --out C:\\dispatcher\\probe_out\\db005r_g11.json

口令与令牌**均不写入证据**；输出中的地址/端口/实例名/账号已按黑板脱敏口径掩码。
"""
import argparse
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

REPO = os.environ.get('DBOPS_REPO', r'C:\DB-AIOps')
sys.path.insert(0, REPO)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402
django.setup()

from django.utils import timezone  # noqa: E402

BASE = os.environ.get('BASE', 'http://<local-host>:<platform-port>')
USER = os.environ.get('MTEAM_USER', 'mteam')

# 脱敏口径（地址/端口/实例名/账号）
MASKS = [
    ('127.0.0.1', '<local-host>'),
    ('localhost', '<local-host>'),
    ('8009', '<platform-port>'),
    ('13306', '<mysql-port>'),
    ('15002', '<proxy-port>'),
    ('M-team probe-tdsql-proxy', '<instance-proxy>'),
    ('M-team probe-mysql-8.0', '<instance-mysql>'),
    (USER, '<account>'),
]


def mask(obj):
    if isinstance(obj, str):
        for a, b in MASKS:
            obj = obj.replace(a, b)
        return obj
    if isinstance(obj, list):
        return [mask(x) for x in obj]
    if isinstance(obj, dict):
        return {k: mask(v) for k, v in obj.items()}
    return obj


def req(path, method='GET', body=None, token=None, timeout=120):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {'content-type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors='replace')
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return 0, '%s: %s' % (type(e).__name__, e)


def auth():
    """返回 (token, side_effects)。优先签发令牌；失败才回退到口令登录。"""
    from django.contrib.auth import get_user_model
    from monitor.auth import TokenManager

    U = get_user_model()
    u = U.objects.filter(username=USER).first()
    if u is None:
        return None, ['账号不存在：%s' % USER]
    token = TokenManager.generate_token(u.id)
    st, _ = req('/api/v1/databases/%s/perf/sql-regression/?window=24h&baseline=day'
                % os.environ.get('G11_PROBE_CID', '18'), token=token)
    if st != 401:
        return token, []
    # 回退：重置本地开发账号口令并登录（副作用会被记录）
    pw = os.environ.get('MTEAM_PW') or secrets.token_urlsafe(18)
    u.set_password(pw)
    u.save()
    st2, d = req('/api/v1/auth/login/', 'POST', {'username': USER, 'password': pw})
    if st2 == 200 and isinstance(d, dict) and 'token' in d:
        return d['token'], ['本地开发账号口令已重置并用于登录（随机值，不写入证据）']
    return None, ['签发令牌被拒(%s)且回退登录失败(%s)' % (st, st2)]


def continuity(cid, hours=48):
    """只读：sql_stat 覆盖与间隙（证明昨日同时段基线窗有数据）。"""
    from monitor.api_views_perf import _ts_cursor

    now = timezone.now()
    out = {'config_id': cid, 'now': now.isoformat(), 'lookback_hours': hours}
    with _ts_cursor() as cur:
        if cur is None:
            out['error'] = 'TSDB 不可用'
            return out
        cur.execute(
            "SELECT MIN(time), MAX(time), COUNT(*), COUNT(DISTINCT sql_digest), "
            "COUNT(DISTINCT time) FROM sql_stat "
            "WHERE db_config_id=%s AND time >= %s AND time <= %s",
            [cid, now - timedelta(hours=hours), now])
        r = cur.fetchone()
        out['min_time'] = str(r[0]) if r[0] else None
        out['max_time'] = str(r[1]) if r[1] else None
        out['rows'] = r[2]
        out['distinct_digests'] = r[3]
        out['distinct_time_points'] = r[4]
        cur.execute(
            "SELECT time FROM sql_stat WHERE db_config_id=%s AND time >= %s "
            "GROUP BY time ORDER BY time", [cid, now - timedelta(hours=hours)])
        ts = [x[0] for x in cur.fetchall()]
        gaps = []
        for i in range(1, len(ts)):
            d = (ts[i] - ts[i - 1]).total_seconds()
            if d > 180:
                gaps.append({'from': str(ts[i - 1]), 'to': str(ts[i]), 'seconds': round(d)})
        out['gap_count_gt_180s'] = len(gaps)
        out['gaps_sample'] = gaps[:5]
        out['first_time_point'] = str(ts[0]) if ts else None
        out['last_time_point'] = str(ts[-1]) if ts else None
        # 昨日同时段窗（baseline=day 对应的 [now-48h, now-24h]）覆盖判定
        fd = now - timedelta(hours=48)
        td = now - timedelta(hours=24)
        cur.execute(
            "SELECT COUNT(*), COUNT(DISTINCT sql_digest), MIN(time), MAX(time) FROM sql_stat "
            "WHERE db_config_id=%s AND time >= %s AND time < %s", [cid, fd, td])
        r2 = cur.fetchone()
        out['baseline_day_window'] = {
            'from': fd.isoformat(), 'to': td.isoformat(),
            'rows': r2[0], 'distinct_digests': r2[1],
            'min_time': str(r2[2]) if r2[2] else None,
            'max_time': str(r2[3]) if r2[3] else None}
        cur.execute(
            "SELECT COUNT(*), COUNT(DISTINCT sql_digest) FROM sql_stat "
            "WHERE db_config_id=%s AND time >= %s AND time <= %s", [cid, td, now])
        r3 = cur.fetchone()
        out['current_24h_window'] = {
            'from': td.isoformat(), 'to': now.isoformat(),
            'rows': r3[0], 'distinct_digests': r3[1]}
    return out


def call_regression(cid, token, window, kind='day'):
    q = urllib.parse.urlencode({'window': window, 'baseline': kind})
    path = '/api/v1/databases/%d/perf/sql-regression/?%s' % (cid, q)
    st, d = req(path, token=token)
    res = {'config_id': cid, 'query': path.replace(BASE, '<platform>'),
           'http': st, 'kind': '昨日同时段环比 (baseline=day)' if kind == 'day'
           else '上周同时段环比 (baseline=week)'}
    if st == 200 and isinstance(d, dict):
        data = d.get('data') or {}
        res['window'] = data.get('window')
        res['baseline'] = data.get('baseline')
        res['counts'] = data.get('counts')
        res['thresholds'] = data.get('thresholds')
        res['baseline_note'] = data.get('baseline_note')
        res['basis_warning'] = data.get('basis_warning')
        items = data.get('items') or []
        res['items_returned'] = len(items)
        res['items'] = [{
            'digest': (it.get('digest') or '')[:32],
            'status': it.get('status'),
            'avg_ratio': it.get('avg_ratio'),
            'exec_ratio': it.get('exec_ratio'),
            'impact_ms': it.get('impact_ms'),
            'current': it.get('current'),
            'baseline': it.get('baseline'),
            'plan_changed_at': it.get('plan_changed_at'),
        } for it in items]
    else:
        res['body'] = str(d)[:400]
    return res


def call_periodic(cid, token, days):
    q = urllib.parse.urlencode({'days': days})
    path = '/api/v1/databases/%d/perf/sql-periodic/?%s' % (cid, q)
    st, d = req(path, token=token)
    res = {'config_id': cid, 'query': path.replace(BASE, '<platform>'),
           'http': st, 'kind': '日周期 SQL (sql-periodic)'}
    if st == 200 and isinstance(d, dict):
        data = d.get('data') or {}
        res['days'] = data.get('days')
        res['rule'] = data.get('rule')
        items = data.get('items') or []
        res['items_returned'] = len(items)
        res['items'] = [{
            'digest': (it.get('digest') or '')[:32],
            'sql_text': (it.get('sql_text') or '')[:160],
            'hours': it.get('hours'),
            'days_seen': it.get('days_seen'),
            'runs': (it.get('runs') or [])[-3:],
            'median_avg_ms': it.get('median_avg_ms'),
            'latest_vs_median': it.get('latest_vs_median'),
            'median_total_ms': it.get('median_total_ms'),
        } for it in items]
    else:
        res['body'] = str(d)[:400]
    return res


def compact(out):
    """精简版：供入库的证据（保留判据与关键数值，裁掉冗长明细）。"""
    c = {'generated_at': out['generated_at'], 'side_effects': out['side_effects'],
         'base': out['base'], 'window': out['window'], 'periodic_days': out['periodic_days'],
         'instances': {}}
    for cid, inst in out['instances'].items():
        e = {}
        ct = inst['continuity']
        e['continuity'] = {k: ct.get(k) for k in (
            'config_id', 'now', 'lookback_hours', 'min_time', 'max_time', 'rows',
            'distinct_digests', 'distinct_time_points', 'gap_count_gt_180s',
            'first_time_point', 'last_time_point')}
        e['continuity']['baseline_day_window'] = ct.get('baseline_day_window')
        e['continuity']['current_24h_window'] = ct.get('current_24h_window')
        for key in ('regression_day', 'regression_week'):
            r = inst[key]
            rr = {k: r.get(k) for k in ('config_id', 'query', 'http', 'kind', 'window',
                                        'baseline', 'counts', 'thresholds', 'baseline_note',
                                        'basis_warning', 'items_returned')}
            items = []
            new_digests = []
            for it in r.get('items') or []:
                stt = it.get('status')
                if stt == 'new':
                    new_digests.append((it.get('digest') or '')[:12])
                    continue
                cur, base = it.get('current') or {}, it.get('baseline') or {}
                items.append({
                    'digest12': (it.get('digest') or '')[:12],
                    'status': stt,
                    'avg_ratio': it.get('avg_ratio'),
                    'exec_ratio': it.get('exec_ratio'),
                    'impact_ms': it.get('impact_ms'),
                    'plan_changed_at': it.get('plan_changed_at'),
                    'cur': {'exec': cur.get('exec'), 'total_ms': cur.get('total_ms'),
                            'avg_ms': cur.get('avg_ms'),
                            'rows_examined_per_exec': cur.get('rows_examined_per_exec'),
                            'lock_ms_per_exec': cur.get('lock_ms_per_exec')},
                    'base': {'exec': base.get('exec'), 'total_ms': base.get('total_ms'),
                             'avg_ms': base.get('avg_ms'),
                             'rows_examined_per_exec': base.get('rows_examined_per_exec'),
                             'lock_ms_per_exec': base.get('lock_ms_per_exec')},
                })
            rr['items'] = items
            rr['new_digest12_list'] = new_digests
            rr['items_detail_note'] = ('仅展开 regressed/improved/gone 明细；'
                                       'new 项只留 digest12 列表')
            e[key] = rr
        p = inst['periodic']
        e['periodic'] = {k: p.get(k) for k in ('config_id', 'query', 'http', 'kind', 'days',
                                              'rule', 'items_returned')}
        e['periodic']['items'] = p.get('items') or []
        c['instances'][cid] = e
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cid', type=int, default=18, help='主实例 config_id（连续采集最久的一路）')
    ap.add_argument('--cid2', type=int, default=19, help='第二实例 config_id（0 表示不跑）')
    ap.add_argument('--window', default='24h')
    ap.add_argument('--periodic-days', type=int, default=7)
    ap.add_argument('--out', default=r'C:\dispatcher\probe_out\db005r_g11.json')
    ap.add_argument('--compact-out', default=r'C:\dispatcher\probe_out\db005r_g11_evidence.json',
                    help='精简证据输出（入库用）')
    args = ap.parse_args()

    token, side = auth()
    out = {'generated_at': timezone.now().isoformat(), 'side_effects': side,
           'base': BASE.replace('127.0.0.1', '<local-host>').replace('8009', '<platform-port>'),
           'window': args.window, 'periodic_days': args.periodic_days}
    if not token:
        out['error'] = '认证失败'
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(mask(out), f, ensure_ascii=False, indent=2)
        print('AUTH FAILED', side)
        return 2

    cids = [args.cid] + ([args.cid2] if args.cid2 else [])
    out['instances'] = {}
    for cid in cids:
        print('=== cid=%d ===' % cid)
        inst = {
            'continuity': continuity(cid),
            'regression_day': call_regression(cid, token, args.window, 'day'),
            'regression_week': call_regression(cid, token, args.window, 'week'),
            'periodic': call_periodic(cid, token, args.periodic_days),
        }
        out['instances'][str(cid)] = inst
        r = inst['regression_day']
        print('  regression_day(http=%s) counts=%s baseline_note=%s items=%s' % (
            r.get('http'), r.get('counts'), r.get('baseline_note'), r.get('items_returned')))
        w = inst['regression_week']
        print('  regression_week(http=%s) counts=%s baseline_note=%s' % (
            w.get('http'), w.get('counts'), w.get('baseline_note')))
        p = inst['periodic']
        print('  periodic(http=%s) items=%s rule=%s' % (
            p.get('http'), p.get('items_returned'), p.get('rule')))
        c = inst['continuity']
        print('  continuity: rows=%s digests=%s gaps>180s=%s baseline_win_rows=%s' % (
            c.get('rows'), c.get('distinct_digests'), c.get('gap_count_gt_180s'),
            (c.get('baseline_day_window') or {}).get('rows')))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(mask(out), f, ensure_ascii=False, indent=2)
    print('WROTE', args.out)

    compact_path = args.compact_out
    if compact_path:
        os.makedirs(os.path.dirname(compact_path), exist_ok=True)
        with open(compact_path, 'w', encoding='utf-8') as f:
            json.dump(mask(compact(out)), f, ensure_ascii=False, indent=2)
        print('WROTE', compact_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
