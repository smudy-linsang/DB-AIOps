#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · DM 检测器级验证（B 批）：dmPython 已在本机可用，
于是 DamengChecker.collect_metrics 可以对真 DM8 实跑整段采集器。

这一项在接单回执里记为「不可跑、记缺口」（当时判断 dmPython 需官方授权包未到位）；
实跑发现本机已装有 dmpython 2.5.32，因此缺口可闭，本脚本据此复验。

口令只从平台本地库存密在内存解密，不落盘、不打印明文。
输出：docs/pm/reports/evidence/DB-009-m-team/db009_dm_checker_run.json
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
from monitor.models import DatabaseConfig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_dm_checker_run.json')


class _FakeCommand:
    def stdout_write(self, *_a, **_k):
        pass

    def style_success(self, *a, **k):
        pass

    def style_error(self, *a, **k):
        pass


def main():
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    from monitor.checkers import DM_DRIVER_AVAILABLE, DM_DRIVER_ERROR
    out['dm_driver_available'] = bool(DM_DRIVER_AVAILABLE)
    out['dm_driver_error'] = DM_DRIVER_ERROR
    if not DM_DRIVER_AVAILABLE:
        out['result'] = 'SKIP: dmPython 不可用'
        with io.open(OUT, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    out['target'] = {'id': cfg.id, 'name': cfg.name, 'host': cfg.host,
                     'port': cfg.port, 'db_type': cfg.db_type}
    pwd = cfg.get_password()
    out['target']['password'] = 'len=%d,head=***' % len(pwd or '')

    from monitor.checkers.dm import DamengChecker
    from monitor.db_connector import DbConnector
    conn = DbConnector.get_connection(cfg, statement_timeout_ms=20000, readonly=True)
    try:
        data = DamengChecker(_FakeCommand()).collect_metrics(cfg, conn)
    finally:
        conn.close()

    out['total_keys'] = len(data)
    out['core'] = {k: data.get(k) for k in
                   ('active_connections', 'max_connections', 'conn_usage_pct',
                    'version', 'db_mode', 'uptime_seconds', 'cache_hit_ratio',
                    'sql_count', 'tran_count')}
    # 采集失败会被静默吞掉（except: pass），所以用「None 率」当信号
    none_keys = sorted([k for k, v in data.items()
                        if v is None and not isinstance(v, (list, dict))])
    out['none_scalar_keys'] = none_keys
    out['none_scalar_count'] = len(none_keys)
    out['empty_list_keys'] = sorted([k for k, v in data.items() if v == []])
    out['empty_list_count'] = len(out['empty_list_keys'])
    out['result'] = 'OK' if data else 'EMPTY'

    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
