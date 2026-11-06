#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 独立验收工具⑦：用**达梦官方 dmPython 驱动**直连复验。

为什么单独做：平台运行时（runserver / start_monitor）用的是 `.venv`
（Python 3.11，cp311 ABI），而本机装好的 dmPython 2.5.32 是 **cp314** 的
.pyd —— ABI 不匹配 ⇒ `import dmPython` 失败 ⇒ DamengChecker 不注册。
于是「修复后的 dm.py 在官方驱动下能不能跑」这件事，在平台运行环境里验不了。

本脚本绕开该限制：
  父进程（.venv / cp311）：从平台库存密在内存解密口令 → **只经环境变量**
                          传给子进程（不落盘、不打印、不入库）；
  子进程（pythoncore-3.14 / cp314）：用**官方 dmPython 驱动**直连真 DM8，
                          把 dm.py 里 AST 抽出的全部 SQL 逐条执行。

子进程只依赖标准库 + dmPython，不需要 Django。

用法：
    python db009_dm_sql_audit_dmpython.py [dm.py 路径]

环境变量：
    DB_AIOPS_ROOT   仓库根（默认 C:\\DB-AIOps）
"""
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
OUT = os.path.join(HERE, 'db009_dm_sql_audit_dmpython.json')

# 能 import dmPython 的解释器（用户级 Python 3.14，cp314 ABI）
PY314 = r'<user-home>\AppData\Local\Python\pythoncore-3.14-64\python.exe'

CHILD = r'''
import ast, io, json, os, re, sys

def extract_sql(path):
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr in ("execute", "executemany")):
            continue
        if not node.args:
            continue
        a = node.args[0]
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            out.append((a.lineno, a.value))
    seen = {}
    for ln, sql in out:
        seen.setdefault(re.sub(r"\s+", " ", sql).strip(), (ln, sql))
    return [seen[k] for k in sorted(seen, key=lambda k: seen[k][0])]

import dmPython
res = {"driver": "dmPython", "driver_version": getattr(dmPython, "version", None),
       "driver_file": getattr(dmPython, "__file__", None)}
try:
    conn = dmPython.connect(user=os.environ["DB009_DM_USER"],
                            password=os.environ["DB009_DM_PASSWORD"],
                            server=os.environ["DB009_DM_HOST"],
                            port=int(os.environ["DB009_DM_PORT"]))
    res["connect"] = "ok"
except Exception as e:
    res["connect"] = "failed: " + repr(e)[:300]
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(2)

cur = conn.cursor()
items = extract_sql(os.environ["DB009_DM_SRC"])
results = []
ok = fail = 0
for ln, sql in items:
    one = re.sub(r"\s+", " ", sql).strip()
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        ok += 1
        results.append({"line": ln, "ok": True, "rows": len(rows),
                        "sample": [str(r)[:120] for r in rows[:2]], "sql": one[:400]})
    except Exception as e:
        fail += 1
        results.append({"line": ln, "ok": False, "error": str(e)[:220], "sql": one[:400]})
res["total"] = len(items)
res["ok"] = ok
res["fail"] = fail
res["results"] = results
print(json.dumps(res, ensure_ascii=False, indent=1))
'''


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, 'monitor', 'checkers', 'dm.py')
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'driver_python': PY314,
           'source': src}
    if not os.path.exists(PY314):
        out['result'] = 'SKIP: 未找到可 import dmPython 的 Python 3.14'
        with io.open(OUT, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
        print(out['result'])
        return 0

    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    pwd = cfg.get_password()
    env = dict(os.environ)
    env.update({
        'DB009_DM_USER': cfg.username,
        'DB009_DM_PASSWORD': pwd or '',
        'DB009_DM_HOST': cfg.host,
        'DB009_DM_PORT': str(cfg.port),
        'DB009_DM_SRC': src,
    })
    p = subprocess.run([PY314, '-c', CHILD], env=env, capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    raw = (p.stdout or '') + (p.stderr or '')
    # 口令不出现在子进程输出里，仍做一次兜底抹除
    if pwd:
        raw = raw.replace(pwd, '***')
    try:
        out['child'] = json.loads(raw)
        out['result'] = ('PASS: %d/%d 通过' % (out['child']['ok'], out['child']['total'])
                         if out['child'].get('fail') == 0 else 'FAIL')
    except Exception:
        out['raw'] = raw[-4000:]
        out['result'] = 'PARSE_FAIL'
    out['target'] = {'id': cfg.id, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port,
                     'password': 'len=%d,head=***' % len(pwd or '')}
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print('driver=%s' % out.get('result'))
    child = out.get('child') or {}
    if child:
        print('connect=%s  driver_version=%s  total=%s ok=%s fail=%s'
              % (child.get('connect'), child.get('driver_version'),
                 child.get('total'), child.get('ok'), child.get('fail')))
        for r in child.get('results', []):
            if not r['ok']:
                print('  [FAIL] L%-5s %s' % (r['line'], r['error'][:150]))
    print('-> %s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
