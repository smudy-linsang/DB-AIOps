#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-017 · DM 采集链 SQL 全口径实跑：sentinel（ASH / golden_metrics）+ 连接层 ping。

背景：sentinel.py 把 'dm' 归入 Oracle 家族分支（_ash_oracle + v$session），
而 checkers/dm.py 用的是 V$SESSIONS（复数）。两套口径必须分别实跑，
才能说清「DM 的平台侧采集链哪一段能跑、哪一段不能跑」。
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, r'C:\DB-AIOps')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
import pyodbc  # noqa: E402
from monitor.models import DatabaseConfig  # noqa: E402

OUT = r'C:\dispatcher\probe_out\db017_dm_ash_probe.json'

CASES = [
    ('sentinel.ping.dm', "SELECT 1 FROM DUAL"),
    ('sentinel.golden.v_session', "SELECT count(*) FROM v$session WHERE type='USER'"),
    ('sentinel.golden.v_parameter', "SELECT value FROM v$parameter WHERE name='sessions'"),
    ('sentinel.ash_oracle.probe', "SELECT * FROM (SELECT SESS_ID, USER_ID, SQL_TEXT, STATE, "
                                  "WAIT_TYPE, WAIT_TIME FROM V$SESSIONS) WHERE ROWNUM<=1"),
    ('sentinel.ash_oracle.v_session', "SELECT count(*) FROM v$session"),
    ('checker.v_sessions.state', "SELECT count(*) FROM v$sessions WHERE STATE='ACTIVE'"),
    ('checker.v_session_wait', "SELECT count(*) FROM V$SESSION_WAIT WHERE EVENT != 'Idle'"),
    ('checker.v_instance.log_size', "SELECT LOG_SIZE FROM V$INSTANCE"),
    ('checker.v_deadlock', "SELECT COUNT(*) FROM V$DEADLOCK_HISTORY"),
    ('checker.v_trx', "SELECT count(*) FROM V$TRX"),
    ('checker.v_arch_file', "SELECT count(*) FROM V$ARCH_FILE"),
    ('checker.v_rlog', "SELECT COUNT(*) FROM V$RLOG WHERE STATUS='ACTIVE'"),
    ('checker.v_rlog_plain', "SELECT COUNT(*) FROM V$RLOG"),
]


def main():
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'instance': {'id': cfg.id, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port},
           'results': []}
    conn = pyodbc.connect('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                           % (cfg.host, cfg.port, cfg.username, cfg.get_password()),
                           timeout=15)
    cur = conn.cursor()
    for name, sql in CASES:
        rec = {'name': name, 'sql': sql}
        try:
            cur.execute(sql)
            rows = cur.fetchmany(2)
            rec['ok'] = True
            rec['rows'] = [[str(c)[:40] for c in r] for r in rows]
        except Exception as e:
            rec['ok'] = False
            rec['error'] = '%s: %s' % (type(e).__name__, str(e)[:180].replace('\n', ' '))
        out['results'].append(rec)
    conn.close()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    for r in out['results']:
        print('%-34s ok=%-5s %s' % (r['name'], r['ok'], str(r.get('rows') or r.get('error'))[:130]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
