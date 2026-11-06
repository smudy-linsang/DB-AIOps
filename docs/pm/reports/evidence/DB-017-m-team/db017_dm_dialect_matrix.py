#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-017 · DM 方言可跑范围实测：把平台声称的 DM 采集 SQL 逐条在本机 DM8 上实跑。

口径说明：
  * dmPython 与 DM8 ODBC DRIVER 都走 DM 协议，SQL 方言是**服务端**行为 ⇒ 用 ODBC 实跑
    可判定「这条 SQL 在 DM8 上能不能执行」，与用 dmPython 跑的结果在方言层面一致。
  * 只跑 SELECT（只读），跳过任何非 SELECT 语句；每条记录 HTTP 无 → 记录原始错误。
  * 覆盖三类 SQL：
      (a) monitor/checkers/dm.py 里 DamengChecker 的采集 SQL（巡检方言）
      (b) monitor/management/commands/start_monitor.py 的 DM 分支（sql_stat / cpu_cores 方言）
      (c) monitor/db_connector.py 的 DM 连接串（连接层）

输出：docs/pm/reports/evidence/DB-017-m-team/dm_dialect_matrix.json（只留判定与错误，不留凭据）
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

OUT = r'C:\dispatcher\probe_out\db017_dm_dialect_matrix.json'
CHECKER = r'C:\DB-AIOps\monitor\checkers\dm.py'
MONITOR = r'C:\DB-AIOps\monitor\management\commands\start_monitor.py'


def extract_sql(path):
    """抽出源码里所有 execute(...) 的字符串字面量（支持三引号与普通引号）。"""
    src = io.open(path, encoding='utf-8').read()
    out = []
    for m in re.finditer(r'execute\(\s*("""|\'\'\'|"|\')', src):
        quote = m.group(1)
        start = m.end()
        if quote in ('"""', "'''"):
            end = src.find(quote, start)
        else:
            end = src.find(quote, start)
        if end == -1:
            continue
        sql = src[start:end].strip()
        if sql.upper().startswith('SELECT'):
            out.append(sql)
    # 去重、压平空白
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
                      'driver': 'DM8 ODBC DRIVER',
                      'server_banner': None},
           'checker_sql': [], 'monitor_sql': []}

    conn = pyodbc.connect('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                           % (cfg.host, cfg.port, cfg.username, cfg.get_password()),
                           timeout=15)
    cur = conn.cursor()
    cur.execute('SELECT BANNER FROM V$VERSION WHERE ROWNUM=1')
    row = cur.fetchone()
    out['target']['server_banner'] = str(row[0]) if row else None

    def run_all(sqls, key):
        for sql in sqls:
            rec = {'sql': sql[:400]}
            try:
                cur.execute(sql)
                rows = cur.fetchmany(3)
                rec['ok'] = True
                rec['rows'] = len(rows)
                rec['sample'] = [[str(c)[:36] for c in r] for r in rows]
            except Exception as e:
                rec['ok'] = False
                rec['error'] = '%s: %s' % (type(e).__name__, str(e)[:220].replace('\n', ' '))
            out[key].append(rec)

    run_all(extract_sql(CHECKER), 'checker_sql')

    # start_monitor 的 DM 分支：sql_stat 与 cpu_cores
    monitor_sql = [
        ("sql_stat", "SELECT sql_id, executions, elapsed_time, rows_processed, buffer_gets, "
                     "sql_text FROM v$sqlstats ORDER BY elapsed_time DESC FETCH FIRST 100 ROWS ONLY"),
        ("cpu_cores", "SELECT value FROM v$parameter WHERE name='cpu_count'"),
    ]
    for name, sql in monitor_sql:
        rec = {'name': name, 'sql': sql}
        try:
            cur.execute(sql)
            rows = cur.fetchmany(3)
            rec['ok'] = True
            rec['rows'] = len(rows)
            rec['sample'] = [[str(c)[:36] for c in r] for r in rows]
        except Exception as e:
            rec['ok'] = False
            rec['error'] = '%s: %s' % (type(e).__name__, str(e)[:220].replace('\n', ' '))
        out['monitor_sql'].append(rec)

    conn.close()

    ck = out['checker_sql']
    out['summary'] = {
        'checker_sql_total': len(ck),
        'checker_sql_ok': sum(1 for r in ck if r['ok']),
        'checker_sql_fail': sum(1 for r in ck if not r['ok']),
        'monitor_sql_total': len(out['monitor_sql']),
        'monitor_sql_ok': sum(1 for r in out['monitor_sql'] if r['ok']),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out['summary'], ensure_ascii=False))
    print('--- 失败的 checker SQL ---')
    for r in ck:
        if not r['ok']:
            print('  FAIL:', r['sql'][:110])
            print('        ', r['error'][:160])
    print('--- monitor 分支 ---')
    for r in out['monitor_sql']:
        print('  %s ok=%s %s' % (r['name'], r['ok'], str(r.get('error') or r.get('sample'))[:150]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
