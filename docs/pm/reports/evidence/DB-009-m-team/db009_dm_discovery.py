#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM8 实库字典发现：把实库真实存在的动态视图与列名列出来，作为方言对齐的事实依据。

只读：仅 SELECT 元数据与行数，不做任何写操作；口令只从平台本地库密文内存解密，
不落盘、不打印明文。

输出：docs/pm/reports/evidence/DB-009-m-team/db009_dm_discovery.json
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

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db009_dm_discovery.json')


def main():
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    conn = pyodbc.connect(
        'DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;' %
        (cfg.host, cfg.port, cfg.username, cfg.get_password()), timeout=15)
    cur = conn.cursor()

    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'target': {'id': cfg.id, 'host': cfg.host, 'port': cfg.port,
                      'driver': 'DM8 ODBC DRIVER'},
           'views': {}, 'columns': {}, 'errors': {}}

    def q(sql):
        cur.execute(sql)
        return cur.fetchall()

    # 1) 实库真实存在的动态视图（含 V$ 家族）
    try:
        rows = q("SELECT OWNER, OBJECT_NAME FROM ALL_OBJECTS "
                 "WHERE OBJECT_TYPE = 'VIEW' AND OBJECT_NAME LIKE 'V$%' ORDER BY OBJECT_NAME")
        out['views']['all_v_dollar'] = sorted(set(r[1] for r in rows))
    except Exception as e:
        out['errors']['all_v_dollar'] = '%s: %s' % (type(e).__name__, str(e)[:200])

    # 2) 其它候选视图（动态性能视图也可能落在 SYS 用户下）
    try:
        rows = q("SELECT OWNER, OBJECT_NAME FROM ALL_OBJECTS "
                 "WHERE OBJECT_TYPE = 'VIEW' AND "
                 "(OBJECT_NAME LIKE '%SESSION%' OR OBJECT_NAME LIKE '%SQL%' "
                 " OR OBJECT_NAME LIKE '%LOCK%' OR OBJECT_NAME LIKE '%RLOG%' "
                 " OR OBJECT_NAME LIKE '%ARCH%' OR OBJECT_NAME LIKE '%TEMP%' "
                 " OR OBJECT_NAME LIKE '%BUFFER%' OR OBJECT_NAME LIKE '%WAIT%' "
                 " OR OBJECT_NAME LIKE '%SYSTEM%' OR OBJECT_NAME LIKE '%TRX%' "
                 " OR OBJECT_NAME LIKE '%DEADLOCK%' OR OBJECT_NAME LIKE '%LOG%') "
                 "ORDER BY OWNER, OBJECT_NAME")
        out['views']['candidates'] = sorted(set('%s.%s' % (r[0], r[1]) for r in rows))
    except Exception as e:
        out['errors']['candidates'] = '%s: %s' % (type(e).__name__, str(e)[:200])

    # 3) 关键视图的真实列名
    key_views = ['V$SESSIONS', 'V$TRX', 'V$LOCK', 'V$RLOG', 'V$DEADLOCK_HISTORY',
                 'V$ARCH_FILE', 'V$TABLESPACE', 'V$PARAMETER', 'V$DATABASE',
                 'V$INSTANCE', 'V$VERSION', 'V$SQL_HISTORY', 'V$SQLSTATS',
                 'V$BUFFER', 'V$SESSION_WAIT', 'V$TEMP_SPACE_USAGE', 'V$SYSTEM_INFO',
                 'V$LOG', 'V$ARCH_DEST', 'V$DEST_PENDING', 'V$CLUSTER',
                 'V$DSC_INSTANCES', 'V$GLOBAL_LATCH', 'V$LOGIN', 'V$DM_INI']
    for v in key_views:
        try:
            rows = q("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE TABLE_NAME = '%s' "
                     "ORDER BY COLUMN_ID" % v)
            if rows:
                out['columns'][v] = [r[0] for r in rows]
            else:
                out['columns'][v] = []
        except Exception as e:
            out['errors']['cols:' + v] = '%s: %s' % (type(e).__name__, str(e)[:200])

    conn.close()
    with io.open(OUT, 'w', encoding='utf-8') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    print('V$ views count:', len(out['views'].get('all_v_dollar', [])))
    print('V$ views:', ','.join(out['views'].get('all_v_dollar', [])))
    print('--- candidates ---')
    for v in out['views'].get('candidates', []):
        print('  ', v)
    print('--- key columns ---')
    for k, v in out['columns'].items():
        print('  %-22s %s' % (k, ','.join(v) if v else '<NOT FOUND>'))
    print('errors:', json.dumps(out['errors'], ensure_ascii=False)[:400])
    return 0


if __name__ == '__main__':
    sys.exit(main())
