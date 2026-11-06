#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM 空集合口径核查：哪些「空列表」是环境本来就没有、哪些是采集器没采到。

对 DM8 实库逐个查对应视图/条件的真实行数，与采集器产出对齐，
避免把「环境本来没有」误报成「采集器坏了」，也避免把采集器坏了掩盖成「环境没有」。
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
from monitor.models import DatabaseConfig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_dm_empty_semantics.json')

PROBES = [
    ('buffer_pools', 'SELECT COUNT(*) FROM V$BUFFER'),
    ('archive_dest', 'SELECT COUNT(*) FROM V$ARCH_FILE'),
    ('realtime_archive_dest', 'SELECT COUNT(*) FROM V$ARCH_FILE'),
    ('dest_pending', 'SELECT COUNT(*) FROM V$ARCH_FILE WHERE 1=0'),
    ('dsc_instances', 'SELECT COUNT(*) FROM V$ARCH_FILE WHERE 1=0'),
    ('dsc_global_latches', 'SELECT COUNT(*) FROM V$ARCH_FILE WHERE 1=0'),
    ('locks', 'SELECT COUNT(*) FROM V$LOCK WHERE BLOCKED = 1'),
    ('wait_events', 'SELECT COUNT(*) FROM V$LOCK WHERE BLOCKED = 1'),
    ('long_transactions',
     "SELECT COUNT(*) FROM V$TRX t LEFT JOIN V$SESSIONS s ON t.SESS_ID = s.SESS_ID "
     "WHERE (SYSDATE - s.CREATE_TIME) * 86400 > 60"),
    ('failed_logins', 'SELECT COUNT(*) FROM V$SESSIONS WHERE 1=0'),
    ('dw_replication_issues', 'SELECT COUNT(*) FROM V$RLOG WHERE ALMOST_FULL_STATUS <> \'SAFE\''),
    ('dsc_cluster_issues', 'SELECT COUNT(*) FROM V$INSTANCE'),
]


def main():
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'probes': []}
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    import pyodbc
    conn = pyodbc.connect(
        'DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
        % (cfg.host, cfg.port, cfg.username, cfg.get_password()), timeout=15)
    cur = conn.cursor()
    for name, sql in PROBES:
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            out['probes'].append({'name': name, 'sql': sql,
                                  'rows': len(rows),
                                  'sample': [[str(c)[:40] for c in r]
                                             for r in rows[:3]]})
        except Exception as e:
            out['probes'].append({'name': name, 'sql': sql, 'error': str(e)[:150]})
    conn.close()
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    for p in out['probes']:
        print('%-26s rows=%s %s' % (p['name'], p.get('rows'), p.get('error', '')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
