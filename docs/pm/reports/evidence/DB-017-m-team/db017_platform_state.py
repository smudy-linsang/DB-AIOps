#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-017 · 平台侧 DM/GBase 纳管实况：时序库产出、实例状态、采集日志信号。"""
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

OUT = r'C:\dispatcher\probe_out\db017_platform_state.json'


def main():
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S')}

    # 1) 时序库：各实例的采集产出
    try:
        from monitor.timeseries import get_timeseries_storage
        st = get_timeseries_storage()
        cur = st._get_connection().cursor()
        ts = {}
        for sql, key in [
            ('SELECT db_config_id, COUNT(*), MAX(time) FROM metric_point GROUP BY db_config_id ORDER BY 2 DESC', 'metric_point'),
            ('SELECT db_config_id, COUNT(*), MAX(time) FROM session_sample GROUP BY db_config_id ORDER BY 2 DESC', 'session_sample'),
            ('SELECT db_config_id, COUNT(*), MAX(time) FROM sql_stat GROUP BY db_config_id ORDER BY 2 DESC', 'sql_stat'),
        ]:
            try:
                cur.execute(sql)
                ts[key] = [[str(v) for v in r] for r in cur.fetchall()]
            except Exception as e:
                ts[key] = 'ERR %s' % str(e)[:160]
        cur.close()
        out['timeseries_by_config'] = ts
    except Exception as e:
        out['timeseries_by_config'] = 'ERR %s: %s' % (type(e).__name__, e)

    # 2) 实例当前状态字段
    insts = []
    for c in DatabaseConfig.objects.all().order_by('id'):
        insts.append({'id': c.id, 'name': c.name, 'db_type': c.db_type,
                      'host': c.host, 'port': c.port, 'is_active': c.is_active,
                      'cpu_cores': c.cpu_cores,
                      'status': getattr(c, 'status', None),
                      'last_check': str(getattr(c, 'last_check_time', None)),
                      'last_error': str(getattr(c, 'last_error', None))[:160]})
    out['instances'] = insts

    # 3) 采集日志里的 DM / 达梦 信号（含 DB-018 修复后的可见性）
    sig = []
    p = r'C:\DB-AIOps\logs\db_monitor.log'
    if os.path.isfile(p):
        with open(p, 'rb') as f:
            raw = f.read()
        txt = raw.decode('utf-8', errors='replace')
        if '达梦' not in txt and '\u8fbe\u68a6' not in txt:
            txt = raw.decode('gbk', errors='replace')
        for line in txt.splitlines():
            if ('达梦' in line or 'missing_driver' in line or 'DM 检测器' in line
                    or 'dmsvc' in line.lower() or 'DamengChecker' in line):
                sig.append(line.strip()[:240])
    out['dm_log_signals'] = sig[-25:]
    out['dm_log_signal_total'] = len(sig)

    io.open(OUT, 'w', encoding='utf-8').write(json.dumps(out, ensure_ascii=False, indent=2))
    print('TS metric_point:', json.dumps(out['timeseries_by_config'].get('metric_point'), ensure_ascii=False)[:400])
    print('TS session_sample:', json.dumps(out['timeseries_by_config'].get('session_sample'), ensure_ascii=False)[:300])
    print('TS sql_stat:', json.dumps(out['timeseries_by_config'].get('sql_stat'), ensure_ascii=False)[:300])
    print('DM log signals:', out['dm_log_signal_total'])
    for s in out['dm_log_signals'][-6:]:
        print('   ', s[:200])
    return 0


if __name__ == '__main__':
    sys.exit(main())
