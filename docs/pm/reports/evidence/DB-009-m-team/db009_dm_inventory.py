#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM8 实库视图/列清单盘点（为方言对齐提供事实依据）。

只读：只执行 SELECT；凭据取自平台自身库存密、内存解密直用，证据中只保留长度。
输出：docs/pm/reports/evidence/DB-009-m-team/db009_dm_inventory.json
"""
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

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db009_dm_inventory.json')

# 平台声称但实库不存在/列名不符的视图，本单重点盘点
KEY_VIEWS = [
    'V$INSTANCE', 'V$DATABASE', 'V$SESSIONS', 'V$TRX', 'V$LOCK', 'V$RLOG',
    'V$BUFFER', 'V$SQL_HISTORY', 'V$DEADLOCK_HISTORY', 'V$TABLESPACE',
    'V$ARCH_FILE', 'V$PARAMETER', 'V$VERSION', 'V$SESSION_WAIT',
    'V$TEMP_SPACE_USAGE', 'V$SYSTEM_INFO', 'V$SQLSTATS', 'V$ARCH_DEST',
    'V$DEST_PENDING', 'V$CLUSTER', 'V$DSC_INSTANCES', 'V$GLOBAL_LATCH',
    'V$LOG', 'V$LOGIN', 'V$TRX_WAIT', 'V$SYSSTAT', 'V$SYSFILES', 'V$SPACE',
    'V$BF_POOL', 'V$HUGE_TS', 'V$IO_STAT', 'V$DISK_STAT', 'V$ARCHIVE',
    'V$BACKUP', 'V$DM_INI', 'V$SESS_STAT', 'V$LOCK_USED', 'V$CONTENT',
]


def main():
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'target': {'id': cfg.id, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port,
                      'driver': 'DM8 ODBC DRIVER',
                      'password': 'len=%d,head=***' % len(cfg.get_password() or '')},
           'views': {}, 'all_v_views': [], 'notes': []}

    conn = pyodbc.connect('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                           % (cfg.host, cfg.port, cfg.username, cfg.get_password()),
                           timeout=15)
    cur = conn.cursor()
    cur.execute('SELECT BANNER FROM V$VERSION WHERE ROWNUM=1')
    out['target']['server_banner'] = str(cur.fetchone()[0])

    # 1) 全量 V$ 视图清单
    try:
        cur.execute("SELECT DISTINCT OBJECT_NAME FROM ALL_OBJECTS "
                    "WHERE UPPER(OBJECT_NAME) LIKE 'V$%' ORDER BY OBJECT_NAME")
        out['all_v_views'] = [str(r[0]) for r in cur.fetchall()]
    except Exception as e:
        out['notes'].append('ALL_OBJECTS 不可用: %s' % str(e)[:200])

    # 2) 关键视图的列清单（SELECT * WHERE 1=0 读 cursor.description）
    for v in KEY_VIEWS:
        rec = {'exists': False, 'columns': []}
        try:
            cur.execute('SELECT * FROM %s WHERE 1=0' % v)
            rec['exists'] = True
            rec['columns'] = [d[0] for d in cur.description]
        except Exception as e:
            rec['error'] = str(e)[:200].replace('\n', ' ')
        out['views'][v] = rec

    # 3) 补几个聚合视图（DM8 统计类）
    for v in ['V$SESSIONS', 'V$TRX', 'V$RLOG', 'V$BUFFER', 'V$SQL_HISTORY',
              'V$DEADLOCK_HISTORY', 'V$INSTANCE', 'V$DATABASE', 'V$LOCK']:
        try:
            cur.execute('SELECT * FROM %s' % v)
            rows = cur.fetchall()
            out['views'][v]['sample_rows'] = len(rows)
            out['views'][v]['sample'] = [[str(c)[:40] for c in r] for r in rows[:3]]
        except Exception as e:
            out['views'][v]['sample_error'] = str(e)[:200].replace('\n', ' ')

    # 4) DBA 目录视图列清单
    for v in ['DBA_TABLES', 'DBA_USERS', 'DBA_OBJECTS']:
        rec = {'exists': False, 'columns': []}
        try:
            cur.execute('SELECT * FROM %s WHERE 1=0' % v)
            rec['exists'] = True
            rec['columns'] = [d[0] for d in cur.description]
        except Exception as e:
            rec['error'] = str(e)[:200].replace('\n', ' ')
        out['views'][v] = rec

    conn.close()
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('ALL_V_VIEWS:', len(out['all_v_views']))
    print(json.dumps(out['all_v_views'], ensure_ascii=False))
    for v, r in out['views'].items():
        if r.get('exists'):
            print('EXISTS %-24s cols=%s' % (v, ','.join(r['columns'])))
        else:
            print('MISSING %-23s %s' % (v, (r.get('error') or '')[:90]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
