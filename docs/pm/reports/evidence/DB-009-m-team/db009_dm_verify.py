#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 修复后复验：把补丁后的 DM 采集 SQL 逐条在实库 DM8 上重跑。

口径：与 DB-017 同一提取器（源码 execute() 字面量）→ 同一实库 → 可直接前后对比。
额外单列 start_monitor / sentinel 的 DM 专属 SQL。
输出：docs/pm/reports/evidence/DB-009-m-team/db009_dm_verify.json
"""
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, r'C:\DB-AIOps')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
import pyodbc  # noqa: E402
from monitor.models import DatabaseConfig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_dm_verify.json')
CHECKER = r'C:\DB-AIOps\monitor\checkers\dm.py'

EXTRA_DM_SQL = [
    ("start_monitor.sql_stat",
     "SELECT * FROM (SELECT SQL_ID, COUNT(*) as EXECUTIONS, SUM(TIME_USED) as ELAPSED_TIME, "
     "SUM(AFFECTED_ROWS) as ROWS_PROCESSED, SUM(N_LOGIC_READ + N_PHY_READ) as BUFFER_GETS, "
     "SUBSTR(TOP_SQL_TEXT, 1, 500) as SQL_TEXT FROM V$SQL_HISTORY GROUP BY SQL_ID, TOP_SQL_TEXT "
     "ORDER BY EXECUTIONS DESC) WHERE ROWNUM <= 100"),
    ("sentinel.golden_metrics.count",
     "SELECT count(*) FROM V$SESSIONS"),
    ("sentinel.golden_metrics.max",
     "SELECT value FROM v$parameter WHERE name='MAX_SESSIONS'"),
    ("sentinel.ash_dm",
     "SELECT SESS_ID, USER_NAME, STATE, CLNT_IP, CLNT_HOST, APPNAME, "
     "SUBSTR(SQL_TEXT, 1, 500) as SQL_TEXT, SQL_ID, TRX_ID, CREATE_TIME "
     "FROM V$SESSIONS WHERE USER_NAME IS NOT NULL"),
    ("checker.sysstat_names_trx",
     "SELECT NAME, STAT_VAL FROM V$SYSSTAT WHERE UPPER(NAME) LIKE '%TRX%' OR UPPER(NAME) LIKE '%COUNT%'"),
]


def extract_sql(path):
    src = io.open(path, encoding='utf-8').read()
    out = []
    for m in re.finditer(r'execute\(\s*("""|\'\'\'|"|\')', src):
        quote = m.group(1)
        start = m.end()
        end = src.find(quote, start)
        if end == -1:
            continue
        sql = src[start:end].strip()
        if sql.upper().startswith('SELECT'):
            out.append(sql)
    seen, res = set(), []
    for s in out:
        flat = ' '.join(s.split())
        if flat and flat not in seen:
            seen.add(flat)
            res.append(flat)
    return res


def main():
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'target': {'id': cfg.id, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port,
                      'driver': 'DM8 ODBC DRIVER'},
           'checker_sql': [], 'extra_dm_sql': []}
    conn = pyodbc.connect('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                           % (cfg.host, cfg.port, cfg.username, cfg.get_password()),
                           timeout=15)
    cur = conn.cursor()
    cur.execute('SELECT BANNER FROM V$VERSION WHERE ROWNUM=1')
    out['target']['server_banner'] = str(cur.fetchone()[0])

    def run(sql):
        try:
            cur.execute(sql)
            rows = cur.fetchmany(3)
            return True, len(rows), [[str(c)[:32] for c in r] for r in rows], None
        except Exception as e:
            return False, 0, None, str(e)[:200].replace('\n', ' ')

    for sql in extract_sql(CHECKER):
        ok, n, sample, err = run(sql)
        rec = {'sql': sql[:400], 'ok': ok, 'rows': n}
        if ok:
            rec['sample'] = sample
        else:
            rec['error'] = err
        out['checker_sql'].append(rec)

    for name, sql in EXTRA_DM_SQL:
        ok, n, sample, err = run(sql)
        rec = {'name': name, 'sql': sql[:400], 'ok': ok, 'rows': n}
        if ok:
            rec['sample'] = sample
        else:
            rec['error'] = err
        out['extra_dm_sql'].append(rec)

    conn.close()
    ck = out['checker_sql']
    out['summary'] = {
        'checker_sql_total': len(ck),
        'checker_sql_ok': sum(1 for r in ck if r['ok']),
        'checker_sql_fail': sum(1 for r in ck if not r['ok']),
        'extra_total': len(out['extra_dm_sql']),
        'extra_ok': sum(1 for r in out['extra_dm_sql'] if r['ok']),
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('checker: total=%d ok=%d fail=%d' % (out['summary']['checker_sql_total'],
                                              out['summary']['checker_sql_ok'],
                                              out['summary']['checker_sql_fail']))
    for r in ck:
        if not r['ok']:
            print('  FAIL:', r['sql'][:120])
            print('       ', r['error'][:150])
    for r in out['extra_dm_sql']:
        print('  %-32s ok=%s %s' % (r['name'], r['ok'], str(r.get('sample') or r.get('error'))[:150]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
