#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-005 · R-1 辅助：通过平台自身的 explain 端点采集执行计划（删索引前/后各一次）。

目的：让平台的 plan_changed_at 有真实依据（同一 digest 前后两次 plan_hash 不同），
而不是手工插 SqlPlan 行。只走平台公开 API，不改代码。

用法：
    set MTEAM_PW=...
    python db005_explain.py --label before --out C:\\dispatcher\\probe_out\\plan_before.json
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get('BASE', 'http://<local-host>:<platform-port>')
USER = os.environ.get('MTEAM_USER', 'mteam')
PW = os.environ.get('MTEAM_PW', '')
CID = int(os.environ.get('CID', '18'))

# 场景四类查询 → 识别特征（按 sql_example 文本匹配）
TARGETS = [
    ('slow_user_sort', 'order by created_at desc limit'),
    ('pk_lookup', 'where id ='),
    ('agg_no_shardkey', 'group by status'),
    ('range_no_key', 'created_at >='),
]


def req(path, method='GET', body=None, token=None, timeout=60):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--label', default='before')
    ap.add_argument('--out', default=r'C:\dispatcher\probe_out\plan.json')
    ap.add_argument('--time', default='60m')
    args = ap.parse_args()

    st, d = req('/api/v1/auth/login/', 'POST', {'username': USER, 'password': PW})
    if st != 200 or not isinstance(d, dict) or 'token' not in d:
        print('LOGIN FAILED', st, str(d)[:200])
        return 2
    token = d['token']

    st, slow = req('/api/v1/databases/%d/slow-queries/?time=%s&limit=50' % (CID, args.time),
                   token=token)
    data = slow.get('data') if isinstance(slow, dict) else None
    items = []
    if isinstance(data, dict):
        items = (data.get('slow_queries') or data.get('results') or data.get('items') or [])
    if not items and isinstance(slow, list):
        items = slow
    print('slow list http=%s items=%d' % (st, len(items)))

    out = {'label': args.label, 'base': BASE, 'db_config_id': CID,
           'captured_at_local': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
           'plans': []}
    for it in items:
        txt = (it.get('sql_example') or it.get('sql_text_sample') or it.get('query') or '')
        low = ' '.join(str(txt).lower().split())
        dg = it.get('digest') or it.get('sql_digest')
        if not dg:
            continue
        name = None
        for n, feat in TARGETS:
            if feat in low:
                name = n
                break
        if not name:
            continue
        st2, r = req('/api/v1/databases/%d/perf/sql/%s/explain/' % (CID, dg),
                     'POST', {}, token=token)
        ok = st2 == 200 and isinstance(r, dict) and isinstance(r.get('data'), dict)
        rec = {'name': name, 'digest': dg, 'sql': str(txt)[:160], 'http': st2,
               'plan_hash': (r.get('data') or {}).get('plan_hash') if ok else None,
               'cost_total': (r.get('data') or {}).get('cost_total') if ok else None,
               'captured_at': (r.get('data') or {}).get('captured_at') if ok else None,
               'plan_text': ((r.get('data') or {}).get('plan_text') or '')[:600] if ok else None,
               'error': None if ok else str(r)[:300]}
        out['plans'].append(rec)
        print('%-16s http=%s hash=%s cost=%s' % (name, st2, rec['plan_hash'], rec['cost_total']))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('saved:', args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
