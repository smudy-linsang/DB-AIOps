#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · GBase 8a 目标库启动后连通与方言可跑范围实测。

只读：只执行 SELECT；凭据取自平台自身库存密、内存解密直用。
输出：docs/pm/reports/evidence/DB-009-m-team/db009_gbase_probe.json
"""
import json
import os
import socket
import sys
import time

sys.path.insert(0, r'C:\DB-AIOps')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
import pymysql  # noqa: E402
from monitor.models import DatabaseConfig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_gbase_probe.json')

PROBES = [
    ("select_1", "SELECT 1"),
    ("version", "SELECT VERSION()"),
    ("current_db", "SELECT DATABASE()"),
    ("show_databases", "SHOW DATABASES"),
    ("perf_schema_available", "SELECT COUNT(*) FROM performance_schema.events_statements_summary_by_digest"),
    ("perf_schema_digest_cols",
     "SELECT DIGEST, COUNT_STAR, SUM_TIMER_WAIT, SUM_ROWS_EXAMINED, LEFT(DIGEST_TEXT,200) "
     "FROM performance_schema.events_statements_summary_by_digest "
     "ORDER BY SUM_TIMER_WAIT DESC LIMIT 5"),
    ("information_schema_tables",
     "SELECT COUNT(*) FROM information_schema.tables"),
    ("session_list", "SELECT COUNT(*) FROM information_schema.processlist"),
    ("innodb_status", "SHOW ENGINE INNODB STATUS"),
]


def main():
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'instances': [], 'probes': []}
    cfgs = DatabaseConfig.objects.filter(db_type='gbase').order_by('id')
    conn = None
    used = None
    for cfg in cfgs:
        rec = {'id': cfg.id, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port,
               'user': cfg.username, 'is_active': cfg.is_active}
        s = socket.socket()
        s.settimeout(5)
        rec['tcp_connect'] = (s.connect_ex((cfg.host, cfg.port)) == 0)
        s.close()
        out['instances'].append(rec)
        if rec['tcp_connect'] and conn is None:
            try:
                conn = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.username,
                                       password=cfg.get_password(), connect_timeout=10)
                used = cfg
            except Exception as e:
                rec['connect_error'] = str(e)[:200]

    out['connected_instance'] = used.id if used else None
    if conn is not None:
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        out['server_version'] = str(cur.fetchone()[0])
        for name, sql in PROBES:
            rec = {'name': name, 'sql': sql}
            try:
                cur.execute(sql)
                rows = cur.fetchmany(3)
                rec['ok'] = True
                rec['rows'] = len(rows)
                rec['sample'] = [[str(c)[:40] for c in r] for r in rows]
            except Exception as e:
                rec['ok'] = False
                rec['error'] = str(e)[:200].replace('\n', ' ')
            out['probes'].append(rec)
        conn.close()
    out['summary'] = {
        'instances': len(out['instances']),
        'tcp_up': sum(1 for r in out['instances'] if r.get('tcp_connect')),
        'connected': bool(conn is not None),
        'probe_ok': sum(1 for r in out['probes'] if r['ok']),
        'probe_fail': sum(1 for r in out['probes'] if not r['ok']),
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out['summary'], ensure_ascii=False))
    print('server_version:', out.get('server_version'))
    for r in out['probes']:
        print('  %-30s ok=%s %s' % (r['name'], r['ok'], str(r.get('sample') or r.get('error'))[:130]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
