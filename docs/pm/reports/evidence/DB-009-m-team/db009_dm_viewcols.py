#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 独立验收工具⑤：DM8 动态视图真实列名。

背景：DM8 的**数据字典不列 V$ 动态视图** —— 查 ALL_TAB_COLUMNS / DBA_OBJECTS
都拿不到 V$BUFFERPOOL、V$TRX、V$LOCK 这些视图的列（实测 DBA_OBJECTS 只列 10 个
V$ 对象），所以列结构只能靠 `SELECT * ... WHERE 1=0` + cursor.description 取。

这份列清单是 DB-009 报告里「每处改法选了哪些列」的**唯一依据**，
不允许凭记忆或按 MySQL 习惯推断。

用法：
    python db009_dm_viewcols.py

环境变量：
    DB_AIOPS_ROOT   仓库根（默认 C:\\DB-AIOps）
"""
import io
import json
import os
import sys
import time

ROOT = os.environ.get('DB_AIOPS_ROOT', r'C:\DB-AIOps')
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402
from monitor.db_connector import DbConnector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_dm_viewcols.json')
VIEWS = ['V$BUFFERPOOL', 'V$TRX', 'V$LOCK', 'V$ARCH_STATUS', 'V$SESSIONS',
         'V$WAIT_HISTORY', 'V$RLOG', 'V$PARAMETER', 'V$ARCH_FILE', 'V$SYSSTAT',
         'V$TABLESPACE', 'V$SQL_HISTORY', 'V$INSTANCE', 'V$DATABASE']


def main():
    res = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'views': {}}
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    conn = DbConnector.get_connection(cfg, statement_timeout_ms=15000, readonly=True)
    cur = conn.cursor()
    for v in VIEWS:
        try:
            cur.execute("SELECT * FROM %s WHERE 1=0" % v)
            cols = [d[0] for d in cur.description]
            res['views'][v] = cols
            print('%-16s (%d) %s' % (v, len(cols), ','.join(cols)))
        except Exception as e:
            res['views'][v] = {'error': repr(e)[:200]}
            print('[ERR] %-14s %s' % (v, repr(e)[:140]))
    conn.close()
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print('-> %s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
