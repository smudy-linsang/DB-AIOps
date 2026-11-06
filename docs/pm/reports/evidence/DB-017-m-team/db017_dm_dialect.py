#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-017 · DM 方言取数清单（只读，DM8 本机实例）。"""
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

OUT = r'C:\dispatcher\probe_out\db017_dm_dialect.json'

QUERIES = [
    ('V_SESSIONS_columns', "SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE TABLE_NAME='V$SESSIONS' ORDER BY COLUMN_ID"),
    ('V_SESSIONS_row', 'SELECT * FROM V$SESSIONS'),
    ('V_VERSION_banner', 'SELECT BANNER FROM V$VERSION'),
    ('current_user', 'SELECT USER FROM DUAL'),
    ('V_DATABASE', 'SELECT * FROM V$DATABASE'),
    ('dba_tables_sample', 'SELECT OWNER, TABLE_NAME FROM DBA_TABLES WHERE ROWNUM <= 5'),
    ('dba_tables_count', 'SELECT COUNT(*) FROM DBA_TABLES'),
    ('dba_views_count', "SELECT COUNT(*) FROM DBA_VIEWS WHERE OWNER='SYSDBA'"),
    ('dba_indexes_count', "SELECT COUNT(*) FROM DBA_INDEXES WHERE OWNER='SYSDBA'"),
    ('perf_view_probe', 'SELECT COUNT(*) FROM V$SESSIONS'),
    ('dm_dynamic_view', "SELECT COUNT(*) FROM V$DM_SESSIONS"),
]


def main():
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'instance': {'id': cfg.id, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port,
                        'username': cfg.username},
           'results': {}}
    conn = pyodbc.connect('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                           % (cfg.host, cfg.port, cfg.username, cfg.get_password()),
                           timeout=15)
    cur = conn.cursor()
    for name, sql in QUERIES:
        rec = {'sql': sql}
        try:
            cur.execute(sql)
            rows = cur.fetchmany(5)
            rec['ok'] = True
            rec['rows'] = [[str(c)[:48] for c in r] for r in rows]
        except Exception as e:
            rec['ok'] = False
            rec['error'] = '%s: %s' % (type(e).__name__, str(e)[:200])
        out['results'][name] = rec
    conn.close()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    for k, v in out['results'].items():
        print(k, v['ok'], str(v.get('rows') or v.get('error'))[:220])
    return 0


if __name__ == '__main__':
    sys.exit(main())
