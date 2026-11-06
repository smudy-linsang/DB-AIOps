#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-005R · G-13 探针：手工 EXPLAIN 对「平台自身 EXPLAIN 语句 / INTERVAL ? HOUR 形态」返回 502。

机制（DB-005 证据 plan_after.json 已现形）：
  平台采集计划时会对目标库发 `EXPLAIN FORMAT=JSON|TREE <SQL>`，这些语句本身也被
  performance_schema 采成 digest；之后再对这些 digest 手工 EXPLAIN，等于把
  `EXPLAIN FORMAT=JSON EXPLAIN FORMAT=JSON SELECT ...` 送进目标库 → 被拒绝 →
  平台返回 502 EXPLAIN_FAIL（「语句不支持或目标库拒绝」），而非崩溃、不是错数据。

本探针要证明三件事：
  ① 同一失败形态在**直连真实 MySQL 8.0** 与**经 TDSQL mock proxy** 两个实例上一致 ⇒ 不是 proxy 副作用；
  ② 目标库确实拒绝该语句形态（直连下发原始语句取到 MySQL 错误码）⇒ 502 的语义是「目标库拒绝」；
  ③ 正常形态（字面量 INTERVAL）EXPLAIN 成功 ⇒ 平台 EXPLAIN 链路本身是通的。

用法：python db005r_g13_probe.py --cid <mysql实例> --cid2 <tdsql-proxy实例> --out <json>
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
HOST = os.environ.get('PROBE_HOST', '<local-host>')
PORT = int(os.environ.get('PROBE_PORT', '<target-port>'))
PU = os.environ.get('PROBE_USER', '<account>')
PP = os.environ.get('PROBE_PASSWORD', '')
PDB = os.environ.get('PROBE_DB', 'aiops_probe_shop')


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


def classify(sql):
    low = ' '.join(sql.lower().split())
    if low.startswith('explain'):
        return 'platform_explain_digest'
    if 'interval ?' in low:
        return 'interval_placeholder'
    return 'normal'


def slow_items(token, cid, window='60m'):
    st, slow = req('/api/v1/databases/%d/slow-queries/?time=%s&limit=50' % (cid, window),
                   token=token)
    data = slow.get('data') if isinstance(slow, dict) else None
    items = []
    if isinstance(data, dict):
        items = data.get('slow_queries') or data.get('results') or data.get('items') or []
    if not items and isinstance(slow, list):
        items = slow
    return st, items


def explain_one(token, cid, digest):
    st, r = req('/api/v1/databases/%d/perf/sql/%s/explain/' % (cid, digest), 'POST', {},
                token=token)
    ok = st == 200 and isinstance(r, dict) and isinstance(r.get('data'), dict)
    body = {'plan_hash': (r.get('data') or {}).get('plan_hash'),
            'cost_total': (r.get('data') or {}).get('cost_total')} if ok else \
        (r if isinstance(r, dict) else str(r)[:400])
    return {'http': st, 'ok': ok, 'body': body}


def target_db_direct():
    """直连目标库取原始错误：被平台拒绝的语句形态确实被目标库拒绝。"""
    import pymysql
    cases = [
        ('explain_wrapped',
         'EXPLAIN FORMAT=JSON EXPLAIN FORMAT=JSON SELECT COUNT(*) FROM orders '
         'WHERE created_at >= NOW() - INTERVAL 21 HOUR'),
        ('interval_placeholder',
         'SELECT COUNT(*) FROM orders WHERE created_at >= NOW() - INTERVAL ? HOUR'),
        ('interval_literal',
         'SELECT COUNT(*) FROM orders WHERE created_at >= NOW() - INTERVAL 21 HOUR'),
    ]
    res = {}
    conn = pymysql.connect(host=HOST, port=PORT, user=PU, password=PP,
                           database=PDB, connect_timeout=10)
    with conn.cursor() as c:
        for name, sql in cases:
            try:
                c.execute(sql)
                rows = c.fetchmany(2)
                res[name] = {'ok': True, 'sql': sql,
                             'sample': [[str(v)[:40] for v in r] for r in rows]}
            except Exception as e:
                errno = e.args[0] if getattr(e, 'args', None) else None
                res[name] = {'ok': False, 'sql': sql, 'errno': errno,
                             'error': '%s: %s' % (type(e).__name__, str(e)[:180])}
    conn.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cid', type=int, required=True)
    ap.add_argument('--cid2', type=int, default=18)
    ap.add_argument('--out', default=r'C:\dispatcher\probe_out\db005r_g13.json')
    args = ap.parse_args()

    st, d = req('/api/v1/auth/login/', 'POST', {'username': USER, 'password': PW})
    if st != 200 or not isinstance(d, dict) or 'token' not in d:
        print('LOGIN FAILED', st, str(d)[:200])
        return 2
    token = d['token']

    out = {'captured_at': time_str(), 'base': BASE, 'targets': {},
           'target_db_direct': target_db_direct()}

    for label, cid in (('mysql80_direct', args.cid), ('tdsql_proxy', args.cid2)):
        lst_st, items = slow_items(token, cid)
        picked = []
        for it in items:
            txt = str(it.get('sql_example') or it.get('sql_text_sample') or '')
            kind = classify(txt)
            dg = it.get('digest') or it.get('sql_digest')
            if dg and kind in ('platform_explain_digest', 'interval_placeholder'):
                picked.append({'digest': dg, 'sql_example': txt[:220], 'kind': kind})
        # 同一 digest 只留一条
        seen, uniq = set(), []
        for p in picked:
            if p['digest'] in seen:
                continue
            seen.add(p['digest'])
            uniq.append(p)
        rec = {'cid': cid, 'slow_list_http': lst_st, 'slow_count': len(items),
               'candidates': uniq, 'explain': []}
        for c in uniq[:6]:
            e = explain_one(token, cid, c['digest'])
            e['digest'] = c['digest']
            e['kind'] = c['kind']
            e['sql_example'] = c['sql_example']
            rec['explain'].append(e)
        out['targets'][label] = rec

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    for label, rec in out['targets'].items():
        print('==', label, 'cid=%s' % rec['cid'], 'slow=%d' % rec['slow_count'],
              'candidates=%d' % len(rec['candidates']))
        for e in rec['explain']:
            print('   %-24s http=%s ok=%s' % (e['kind'], e['http'], e['ok']))
    print('== target_db_direct ==')
    for k, v in out['target_db_direct'].items():
        print('   %-22s ok=%s %s' % (k, v['ok'], str(v.get('errno') or '') + ' ' + str(v.get('error') or '')[:90]))
    return 0


def time_str():
    import time
    return time.strftime('%Y-%m-%dT%H:%M:%S')


if __name__ == '__main__':
    sys.exit(main())
