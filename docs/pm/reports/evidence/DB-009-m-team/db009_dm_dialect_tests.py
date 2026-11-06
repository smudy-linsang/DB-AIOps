#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM 方言「检测器级」验证（B 批）：dmPython 已在本机安装，
于是 monitor/tests_dialect.py 的 DamengDialectTests 可以不再 skip，实跑一遍。

口令只从平台本地库存密在内存解密，直接拼进子进程环境变量 DIALECT_DM_DSN；
不落盘、不打印明文、不入库。

输出：docs/pm/reports/evidence/DB-009-m-team/db009_dm_dialect_tests.txt
"""
import io
import os
import subprocess
import sys

sys.path.insert(0, r'C:\DB-AIOps')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_dm_dialect_tests.txt')


def main():
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    pwd = cfg.get_password()
    dsn = 'dm://%s:%s@%s:%s/%s' % (cfg.username, pwd, cfg.host, cfg.port,
                                   cfg.service_name or 'DAMENG')
    env = dict(os.environ)
    env['DIALECT_DM_DSN'] = dsn
    env['DJANGO_SETTINGS_MODULE'] = 'dbmonitor.settings_test_unit'
    # 子进程日志里可能出现 DSN，回显前把口令抹掉
    cmd = [sys.executable, 'manage.py', 'test',
           'monitor.tests_dialect.DamengDialectTests', '-v', '2']
    p = subprocess.run(cmd, env=env, cwd=r'C:\DB-AIOps',
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    out = (p.stdout or '') + (p.stderr or '')
    out = out.replace(pwd, '***')
    with io.open(OUT, 'w', encoding='utf-8') as f:
        f.write('exit_code=%d\n' % p.returncode)
        f.write(out)
    print('exit_code =', p.returncode)
    print(out[-3000:])
    return p.returncode


if __name__ == '__main__':
    sys.exit(main())
