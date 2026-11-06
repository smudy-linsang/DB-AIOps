#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM 方言「用例级」验证：在**能 import dmPython** 的解释器上实跑。

背景：平台运行时（runserver / start_monitor）用 `.venv`（Python 3.11 / cp311），
而达梦官方 dmPython 2.5.32 的 .pyd 是 **cp314** —— ABI 不匹配 ⇒ `.venv` 里
`import dmPython` 失败 ⇒ `DamengDialectTests` 永远 skip。

但本机的**用户级 Python 3.14** 里同时有 django 6.0.4 / dmPython 2.5.32 /
pyodbc(cp314) / pymysql，足够把这两条用例**真跑一遍** —— 于是
DB-017 §8 的 B 批（DM 方言用例实跑）在**不改平台运行环境**的前提下就能闭合。

口令只经环境变量 `DIALECT_DM_DSN` 传给子进程，不落盘、不打印、不入库。

用法：
    python db009_dm_dialect_tests_py314.py

环境变量：
    DB_AIOPS_ROOT   仓库根（默认 C:\\DB-AIOps）
"""
import io
import os
import subprocess
import sys

ROOT = os.environ.get('DB_AIOPS_ROOT', r'C:\DB-AIOps')
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_dm_dialect_tests_py314.txt')
PY314 = r'<user-home>\AppData\Local\Python\pythoncore-3.14-64\python.exe'


def main():
    if not os.path.exists(PY314):
        print('SKIP: 未找到 Python 3.14 (%s)' % PY314)
        return 0
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    pwd = cfg.get_password()
    dsn = 'dm://%s:%s@%s:%s/%s' % (cfg.username, pwd, cfg.host, cfg.port,
                                   cfg.service_name or 'DAMENG')
    env = dict(os.environ)
    env['DIALECT_DM_DSN'] = dsn
    env['DJANGO_SETTINGS_MODULE'] = 'dbmonitor.settings_test_unit'
    # 子进程日志里可能出现 DSN，回显前把口令抹掉
    cmd = [PY314, 'manage.py', 'test',
           'monitor.tests_dialect.DamengDialectTests', '-v', '2']
    p = subprocess.run(cmd, env=env, cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    out = (p.stdout or '') + (p.stderr or '')
    if pwd:
        out = out.replace(pwd, '***')
    with io.open(OUT, 'w', encoding='utf-8', newline='') as f:
        f.write('exit_code=%d\n' % p.returncode)
        f.write(out)
    print('exit_code =', p.returncode)
    tail = [ln for ln in out.splitlines()
            if ln.startswith(('test_', 'Ran ', 'OK', 'FAILED', 'ERROR')) or ' ... ' in ln]
    print('\n'.join(tail[-24:]))
    print('-> %s' % OUT)
    return p.returncode


if __name__ == '__main__':
    raise SystemExit(main())
