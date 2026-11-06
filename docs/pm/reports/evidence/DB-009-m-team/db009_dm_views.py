#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM8 动态视图发现（第二版）：DM 的 V$ 动态视图不是字典对象，
ALL_OBJECTS/ALL_TAB_COLUMNS 查不到；改用「SELECT * FROM <v> WHERE 1=0」+
cursor.description 取得真实视图名与列名，并用 -2106/-2111 错误码区分
「视图不存在」与「视图存在但列名不对」。

只读：不落盘任何凭据。

输出：docs/pm/reports/evidence/DB-009-m-team/db009_dm_views.json
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

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db009_dm_views.json')

CANDIDATES = [
    # 会话 / 事务 / 锁
    'V$SESSIONS', 'V$SESSION', 'V$SESSION_WAIT', 'V$TRX', 'V$TRANSACTIONS',
    'V$LOCK', 'V$LOCKS', 'V$DEADLOCK_HISTORY', 'V$DEADLOCK', 'V$LOGIN',
    'V$LOGIN_HISTORY', 'V$SESS_STAT', 'V$SESSION_STAT',
    # SQL 统计 / 慢 SQL
    'V$SQLSTATS', 'V$SQL_STATS', 'V$SQL_HISTORY', 'V$SQL', 'V$SQLINFO',
    'V$SQL_PLAN', 'V$EXPLAIN_PLAN', 'V$CACHEPLN',
    # 实例 / 参数 / 数据库
    'V$INSTANCE', 'V$VERSION', 'V$DATABASE', 'V$PARAMETER', 'V$DM_INI',
    'V$SYSINFO', 'V$SYSTEM_INFO', 'V$RESOURCE_LIMIT', 'V$RESOURCE',
    # 存储 / 内存 / redo / 归档
    'V$TABLESPACE', 'V$TEMP_SPACE_USAGE', 'V$BUFFER', 'V$BUFFERPOOL',
    'V$RLOG', 'V$LOG', 'V$ARCH_FILE', 'V$ARCHIVE_LOG', 'V$ARCH_DEST',
    'V$DEST_PENDING', 'V$CHECKPOINT', 'V$IO_STAT',
    # 集群 / 数据仓库（DW / DSC 特性）
    'V$CLUSTER', 'V$DSC_INSTANCES', 'V$GLOBAL_LATCH', 'V$CLUSTER_INSTANCES',
    # 其它
    'V$WFLOW', 'V$LOCK_HISTORY', 'V$STAT_LIST', 'V$SYSSTAT', 'V$DBFILE',
]


def main():
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    conn = pyodbc.connect(
        'DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;' %
        (cfg.host, cfg.port, cfg.username, cfg.get_password()), timeout=15)
    cur = conn.cursor()

    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'target': {'id': cfg.id, 'host': cfg.host, 'port': cfg.port},
           'views': {}, 'missing': [], 'row_counts': {}}

    for v in CANDIDATES:
        try:
            cur.execute('SELECT * FROM %s WHERE 1=0' % v)
            cols = [d[0] for d in cur.description]
            out['views'][v] = cols
            try:
                cur.execute('SELECT count(*) FROM %s' % v)
                out['row_counts'][v] = int(cur.fetchone()[0])
            except Exception as e:
                out['row_counts'][v] = 'count-failed: %s' % str(e)[:80]
        except Exception as e:
            msg = str(e)
            out['missing'].append({'view': v, 'error': '%s: %s' % (type(e).__name__, msg[:150])})

    conn.close()
    with io.open(OUT, 'w', encoding='utf-8') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))

    print('=== EXISTS (%d) ===' % len(out['views']))
    for k in sorted(out['views']):
        print('  %-22s rows=%-6s cols=%s' % (k, out['row_counts'].get(k), ','.join(out['views'][k])))
    print('=== MISSING (%d) ===' % len(out['missing']))
    print('  ' + ','.join(m['view'] for m in out['missing']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
