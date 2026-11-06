#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 独立验收工具④：GBase 游标状态污染复现试验。

起因：第一版端到端脚本里 gbase.golden_metrics 返回过 {}（说明内部抛了异常
被 try/except 吞掉），而第二版却正常返回真实值 —— 疑似「前一次采样留下了
未消费的结果集/错误状态，污染了紧随其后的 execute」。

本脚本做三组对照试验：
    T1: sample_sessions → golden_metrics   （v1 的顺序，中间不插任何 SQL）
    T2: golden_metrics → sample_sessions   （反序对照）
    T3: sample_sessions → 裸执行            （探测是否真会 "Commands out of sync"）

结论（2026-11-06 实测）：三组全部正常，**未能复现** {}。
因此该现象**不定性为平台缺陷**，只记录为脚手架观察。

用法：
    python db009_gbase_cursor_state.py

环境变量：
    DB_AIOPS_ROOT   仓库根（默认 C:\\DB-AIOps）
"""
import io
import json
import os
import sys
import time
import traceback

ROOT = os.environ.get('DB_AIOPS_ROOT', r'C:\DB-AIOps')
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402
from monitor import sentinel  # noqa: E402
from monitor.db_connector import DbConnector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_gbase_cursor_state.json')


def gbase_conn():
    cfg = DatabaseConfig.objects.filter(db_type='gbase').order_by('id').first()
    conn = DbConnector.get_connection(cfg, statement_timeout_ms=15000, readonly=True)
    return cfg, conn, conn.cursor()


def trial(name, order, res):
    cfg, conn, cur = gbase_conn()
    print('\n--- 试验 %s（顺序: %s） ---' % (name, ' -> '.join(order)))
    r = {'order': order, 'steps': {}}
    for step in order:
        try:
            if step == 'ash':
                out = sentinel.sample_sessions(cur, 'gbase', config_id=cfg.id)
                r['steps']['sample_sessions'] = {'ok': True, 'count': len(out)}
                print('  [OK ] sample_sessions → %d 行' % len(out))
            elif step == 'golden':
                gm = sentinel.golden_metrics(cur, 'gbase')
                r['steps']['golden_metrics'] = {'ok': True, 'value': gm}
                print('  [OK ] golden_metrics →', gm)
            elif step == 'raw':
                cur.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
                row = cur.fetchone()
                r['steps']['raw_status'] = {'ok': True, 'row': repr(row)[:160]}
                print('  [OK ] 裸执行 SHOW GLOBAL STATUS →', repr(row)[:120])
        except Exception:
            r['steps'][step] = {'ok': False, 'error': traceback.format_exc()[-500:]}
            print('  [ERR] %s → %s' % (step, traceback.format_exc()[-260:]))
    conn.close()
    res['trials'].append(r)
    return r


def main():
    res = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'trials': []}
    trial('T1', ['ash', 'golden'], res)
    trial('T2', ['golden', 'ash'], res)
    trial('T3', ['ash', 'raw'], res)
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print('\n-> %s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
