#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · GBase 自动化用例实跑（monitor.tests_dialect.GbaseDialectTests）。

GBase 目标库已在 <local-host>:<ephemeral-port> 运行（容器 <gbase-container>，<gbase-image>，
版本 8.6.2.43-R7-free.110605）。口令从平台本地库存密在内存解密，
拼进子进程环境变量 DIALECT_GBASE_DSN，不落盘、不打印明文。

输出：docs/pm/reports/evidence/DB-009-m-team/db009_gbase_dialect_tests.txt
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
OUT = os.path.join(HERE, 'db009_gbase_dialect_tests.txt')


def main():
    cfg = DatabaseConfig.objects.filter(db_type='gbase').order_by('id').first()
    pwd = cfg.get_password()
    dsn = 'mysql://%s:%s@%s:%s/%s' % (cfg.username, pwd, cfg.host, cfg.port,
                                       cfg.service_name or 'gbase')
    env = dict(os.environ)
    env['DIALECT_GBASE_DSN'] = dsn
    env['DJANGO_SETTINGS_MODULE'] = 'dbmonitor.settings_test_unit'
    cmd = [sys.executable, 'manage.py', 'test',
           'monitor.tests_dialect.GbaseDialectTests', '-v', '2']
    p = subprocess.run(cmd, env=env, cwd=r'C:\DB-AIOps',
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
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
