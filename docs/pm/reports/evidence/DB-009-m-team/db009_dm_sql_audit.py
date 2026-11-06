#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 独立验收工具①：从修好的 checker 源码里 AST 抽出全部 SQL，逐条打到真实 DM8。

与实现方（另一个会话）完全独立：不读它的结论，只读**最终代码**，
用 DM8 实库执行每一条 SQL，把「能不能跑」当成唯一判据。

用法：
    python db009_dm_sql_audit.py [checker.py 路径] [输出 json]

环境变量：
    DB_AIOPS_ROOT   仓库根（默认 C:\\DB-AIOps）

口令不落盘：连接串里的口令来自平台库存密（DatabaseConfig.get_password()），
本脚本只把口令长度写进产物（len=N,head=***）。
"""
import ast
import io
import json
import os
import re
import sys
import time

ROOT = os.environ.get('DB_AIOPS_ROOT', r'C:\DB-AIOps')
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, 'db009_dm_sql_audit.json')


def extract_sql(path):
    """用 AST 抽出所有 cur.execute(<常量字符串>) 的 SQL。返回 [(行号, sql)]。"""
    src = io.open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr in ('execute', 'executemany')):
            continue
        if not node.args:
            continue
        a = node.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            out.append((a.lineno, a.value))
    # 去重（同一 SQL 在别处复用）并按行号排序
    seen = {}
    for ln, sql in out:
        key = re.sub(r'\s+', ' ', sql).strip()
        seen.setdefault(key, (ln, sql))
    return [seen[k] for k in sorted(seen, key=lambda k: seen[k][0])]


def main():
    src_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, 'monitor', 'checkers', 'dm.py')
    out_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    import pyodbc
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    conn = pyodbc.connect('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                           % (cfg.host, cfg.port, cfg.username, cfg.get_password()), timeout=15)
    cur = conn.cursor()
    items = extract_sql(src_path)
    results = []
    ok = fail = 0
    for ln, sql in items:
        one = re.sub(r'\s+', ' ', sql).strip()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            ok += 1
            results.append({'line': ln, 'ok': True, 'rows': len(rows),
                            'sample': [str(r)[:120] for r in rows[:2]], 'sql': one[:400]})
        except Exception as e:
            fail += 1
            results.append({'line': ln, 'ok': False, 'error': str(e)[:220], 'sql': one[:400]})
    res = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'source': src_path,
           'total': len(items), 'ok': ok, 'fail': fail, 'results': results}
    with io.open(out_path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(res, ensure_ascii=False, indent=1))
    print('source=%s total=%d ok=%d fail=%d -> %s' % (os.path.basename(src_path), len(items), ok, fail, out_path))
    for r in results:
        if not r['ok']:
            print('  [FAIL] L%-5s %s\n         %s' % (r['line'], r['error'][:140], r['sql'][:180]))
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
