#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 订正前/订正后对照：原版 dm.py 的 SQL 逐条打真 DM8。

取 `git show HEAD:monitor/checkers/dm.py`（= 本轮改动前的原版），
AST 抽出全部 SQL，逐条在真实 DM8 上执行，统计通过/失败。
再对当前工作树版本做同样审计，得到「原本 N 条不可跑 → 现在全通」的对照。

用法：
    python db009_dm_before_after.py
"""
import ast
import io
import json
import os
import subprocess
import sys
import time

ROOT = os.environ.get('DB_AIOPS_ROOT', r'C:\DB-AIOps')
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_dm_before_after.json')
TMP_BEFORE = os.path.join(HERE, '_dm_before_tmp.py')


def extract_sql(path):
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
    seen = {}
    for ln, sql in out:
        # 与 db009_dm_sql_audit.py 同口径：空白归一化后去重
        # （只 trim 会把"仅缩进不同"的同一条 SQL 算成两条，计数对不上）
        key = ' '.join(sql.split())
        seen.setdefault(key, (ln, sql))
    return [seen[k] for k in sorted(seen, key=lambda k: seen[k][0])]


def audit(cur, path):
    items = extract_sql(path)
    results = []
    ok = fail = 0
    for ln, sql in items:
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            ok += 1
            results.append({'line': ln, 'ok': True, 'rows': len(rows),
                            'sql': ' '.join(sql.split())[:400]})
        except Exception as e:
            fail += 1
            results.append({'line': ln, 'ok': False, 'error': str(e)[:200],
                            'sql': ' '.join(sql.split())[:400]})
    return {'total': len(items), 'ok': ok, 'fail': fail, 'results': results}


def main():
    import pyodbc
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    conn = pyodbc.connect('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                           % (cfg.host, cfg.port, cfg.username, cfg.get_password()),
                           timeout=15)
    cur = conn.cursor()

    # 原版（HEAD）
    before_src = subprocess.run(
        ['git', '-C', ROOT, 'show', 'HEAD:monitor/checkers/dm.py'],
        capture_output=True, text=True, encoding='utf-8', errors='replace').stdout
    with io.open(TMP_BEFORE, 'w', encoding='utf-8') as f:
        f.write(before_src)
    before = audit(cur, TMP_BEFORE)

    after = audit(cur, os.path.join(ROOT, 'monitor', 'checkers', 'dm.py'))
    conn.close()
    try:
        os.remove(TMP_BEFORE)
    except OSError:
        pass

    out = {
        'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'target': {'id': cfg.id, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port,
                   'password': 'len=%d,head=***' % len(cfg.get_password() or '')},
        'before': {'source': 'git show HEAD:monitor/checkers/dm.py',
                   'total': before['total'], 'ok': before['ok'], 'fail': before['fail'],
                   'failed_lines': [r['line'] for r in before['results'] if not r['ok']],
                   'failed_detail': [{'line': r['line'], 'error': r['error'],
                                      'sql': r['sql']}
                                     for r in before['results'] if not r['ok']]},
        'after': {'source': 'working tree monitor/checkers/dm.py',
                  'total': after['total'], 'ok': after['ok'], 'fail': after['fail'],
                  'failed_detail': [{'line': r['line'], 'error': r['error'],
                                     'sql': r['sql']}
                                    for r in after['results'] if not r['ok']]},
    }
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('BEFORE  total=%d ok=%d fail=%d' % (before['total'], before['ok'], before['fail']))
    print('AFTER   total=%d ok=%d fail=%d' % (after['total'], after['ok'], after['fail']))
    print('-> %s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
