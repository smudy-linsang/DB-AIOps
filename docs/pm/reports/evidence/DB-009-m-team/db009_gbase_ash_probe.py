#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · GBase 端到端探针：ASH 采样 + GbaseChecker.collect_metrics + golden_metrics。

目的（对应任务单第 2 项「补当前版本用例与 ASH 回归的可跑范围」）：
1. 确认 sentinel.sample_sessions(cur, 'gbase') 在真 GBase 8a 上能不能跑通
   （DB-017 §四.6.3 记录此处曾直接抛 `innodb_trx` 不存在）；
2. 确认 GbaseChecker.collect_metrics 的核心契约字段（active_connections /
   max_connections）产出情况；
3. 确认 sentinel.golden_metrics(cur, 'gbase') 可用。

口令只从平台本地库存密在内存解密，不落盘、不打印明文。
输出：docs/pm/reports/evidence/DB-009-m-team/db009_gbase_ash_probe.json
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
OUT = os.path.join(HERE, 'db009_gbase_ash_probe.json')


class _FakeCommand:
    """GbaseChecker.collect_metrics 需要 command，只用到日志/输出接口。"""

    def stdout_write(self, *_a, **_k):
        pass

    def style_success(self, *a, **k):
        pass

    def style_error(self, *a, **k):
        pass


def main():
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    cfg = DatabaseConfig.objects.filter(db_type='gbase').order_by('id').first()
    out['target'] = {'id': cfg.id, 'name': cfg.name, 'host': cfg.host,
                     'port': cfg.port, 'db_type': cfg.db_type}
    pwd = cfg.get_password()
    out['target']['password'] = 'len=%d,head=***' % len(pwd or '')

    from monitor.db_connector import DbConnector
    from monitor import sentinel
    from monitor.checkers.gbase import GbaseChecker

    conn = DbConnector.get_connection(cfg, statement_timeout_ms=15000, readonly=True)
    out['connect'] = 'ok'
    try:
        cur = conn.cursor()
        cur.execute('SELECT VERSION()')
        row = cur.fetchone()
        out['server_version'] = str(list(row.values())[0])

        # ① ASH 采样
        try:
            rows = sentinel.sample_sessions(cur, 'gbase', config_id=cfg.id)
            out['ash'] = {'ok': True, 'rows': len(rows),
                          'sample': rows[:3] if rows else []}
        except Exception as e:
            out['ash'] = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}

        # ② golden_metrics
        try:
            gm = sentinel.golden_metrics(cur, 'gbase')
            out['golden_metrics'] = {'ok': True, 'value': gm}
        except Exception as e:
            out['golden_metrics'] = {'ok': False,
                                     'error': '%s: %s' % (type(e).__name__, e)}

        # ③ collect_metrics
        try:
            data = GbaseChecker(_FakeCommand()).collect_metrics(cfg, conn)
            core = {k: data.get(k) for k in
                    ('active_connections', 'max_connections', 'conn_usage_pct',
                     'threads_connected', 'threads_running', 'qps')}
            out['collect_metrics'] = {
                'ok': True, 'total_keys': len(data), 'core': core,
                'none_core': [k for k in ('active_connections', 'max_connections')
                              if data.get(k) is None],
            }
        except Exception as e:
            out['collect_metrics'] = {'ok': False,
                                      'error': '%s: %s' % (type(e).__name__, e)}
    finally:
        conn.close()

    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
