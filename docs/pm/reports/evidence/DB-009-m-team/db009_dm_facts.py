#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM8 补充事实：TABLESPACE 原始值 / 页大小参数 / 事务累计计数是否存在。"""
import os
import sys

sys.path.insert(0, r'C:\DB-AIOps')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
import pyodbc  # noqa: E402
from monitor.models import DatabaseConfig  # noqa: E402

cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
conn = pyodbc.connect('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                       % (cfg.host, cfg.port, cfg.username, cfg.get_password()), timeout=15)
cur = conn.cursor()
SQLS = [
    "SELECT NAME, TYPE$, TOTAL_SIZE, USED_SIZE, MAX_SIZE, FILE_NUM FROM V$TABLESPACE",
    "SELECT VALUE FROM V$PARAMETER WHERE NAME='GLOBAL_PAGE_SIZE'",
    "SELECT NAME, STAT_VAL FROM V$SYSSTAT WHERE UPPER(NAME) LIKE '%TRX%' "
    "OR UPPER(NAME) LIKE '%COMMIT%' OR UPPER(NAME) LIKE '%TRANS%' "
    "OR UPPER(NAME) LIKE '%ROLLBACK%' OR UPPER(NAME) LIKE '%LOCK%'",
    "SELECT NAME, STAT_VAL FROM V$SYSSTAT WHERE UPPER(NAME) LIKE '%SQL%'",
]
for sql in SQLS:
    try:
        cur.execute(sql)
        print('OK  ', sql[:70])
        for r in cur.fetchall()[:12]:
            print('      ', [str(x)[:30] for x in r])
    except Exception as e:
        print('ERR ', sql[:70], '->', str(e)[:140])
conn.close()
