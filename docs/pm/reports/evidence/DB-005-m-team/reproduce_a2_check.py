#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-005 · A-2 专项验证：索引存在时平台不应重复建议建同一索引。

时序约束（重要）：必须在正式验收（慢 SQL 窗口取样）**之后**执行，
因为 SQL 详情接口的优化建议是按**目标库当前索引状态**实时生成的 ——
在场景 B（索引已删）窗口取样前若把索引加回去，A-1 的「I-01 建议」会消失。

流程：
  1. 查详情（索引已删）⇒ 期望给出 I-01（缺失索引，建议补建）
  2. index add（补回索引）⇒ 查详情 ⇒ 期望不再建议同一索引（I-03/I-04 或无 I-01）
  3. index drop（复原场景 B 末态）⇒ 查详情 ⇒ 期望回到 I-01

用法：
    set MTEAM_PW=...
    python db005_a2_check.py --digest <digest> --out C:\\dispatcher\\probe_out\\db005_a2.json
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BASE = os.environ.get('BASE', 'http://<local-host>:<platform-port>')
USER = os.environ.get('MTEAM_USER', 'mteam')
PW = os.environ.get('MTEAM_PW', '')
CID = int(os.environ.get('CID', '18'))
PY = r'C:\DB-AIOps\.venv\Scripts\python.exe'
WL = r'C:\DB-AIOps\scripts\tdsql_workload.py'


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


def advice_of(token, digest):
    st, raw = req('/api/v1/databases/%d/perf/sql/%s/' % (CID, digest), token=token)
    det = raw.get('data') if isinstance(raw, dict) and isinstance(raw.get('data'), dict) else raw
    adv = det.get('advice') if isinstance(det, dict) else None
    if isinstance(adv, dict):
        adv = adv.get('items') or []
    adv = adv or []
    sugg = (det.get('advisor') or {}).get('index_suggestions') if isinstance(det, dict) else None
    return {'http': st,
            'codes': [a.get('code') if isinstance(a, dict) else str(a) for a in adv],
            'advice': [{'code': a.get('code'), 'severity': a.get('severity'),
                        'title': a.get('title'), 'detail': (a.get('detail') or '')[:180],
                        'ddl': a.get('ddl')} for a in adv if isinstance(a, dict)][:6],
            'index_suggestions': sugg}


def index_cmd(action):
    env = dict(os.environ)
    r = subprocess.run([PY, WL, 'index', action], capture_output=True, text=True,
                       env=env, timeout=120)
    return {'action': action, 'exit': r.returncode,
            'stdout': (r.stdout or '')[-200:], 'stderr': (r.stderr or '')[-200:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--digest', required=True)
    ap.add_argument('--out', default=r'C:\dispatcher\probe_out\db005_a2.json')
    args = ap.parse_args()

    st, d = req('/api/v1/auth/login/', 'POST', {'username': USER, 'password': PW})
    if st != 200 or 'token' not in d:
        print('LOGIN FAILED', st)
        return 2
    token = d['token']

    out = {'digest': args.digest, 'steps': []}
    out['steps'].append({'phase': '1_索引已删（场景B末态）', 'advice': advice_of(token, args.digest)})
    out['steps'].append({'phase': '2_index_add', 'cmd': index_cmd('add')})
    out['steps'].append({'phase': '3_索引已存在', 'advice': advice_of(token, args.digest)})
    out['steps'].append({'phase': '4_index_drop（复原）', 'cmd': index_cmd('drop')})
    out['steps'].append({'phase': '5_复原后', 'advice': advice_of(token, args.digest)})

    c1 = out['steps'][0]['advice']['codes']
    c3 = out['steps'][2]['advice']['codes']
    c5 = out['steps'][4]['advice']['codes']
    out['verdict'] = {
        'A-1_索引缺失时给出I-01': 'I-01' in c1,
        'A-2_索引存在时不重复建议同一索引': ('I-01' not in c3) and bool(c3),
        '复原后回到I-01': 'I-01' in c5,
        'codes_索引缺失': c1, 'codes_索引存在': c3, 'codes_复原': c5,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    for s in out['steps']:
        if 'advice' in s:
            print('%-26s codes=%s' % (s['phase'], s['advice']['codes']))
        else:
            print('%-26s %s exit=%s' % (s['phase'], s['cmd']['action'], s['cmd']['exit']))
    print('\nverdict:', json.dumps(out['verdict'], ensure_ascii=False))
    print('saved:', args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
