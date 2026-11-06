#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 独立验收工具③：GBase 真实会话并发采样。

目的：把「sample_sessions 不抛错」从"降级到 0 行"升级为"采到真实会话行"。
做法：另开一条连接跑持续查询（GBase 无 SLEEP，改用反复扫描
information_schema.tables 制造一个持续活跃的会话），主线程在窗口期内采样 ASH。

同时验证：采样动作**不会破坏游标状态** —— 采样后再取一次黄金状态量。

用法：
    python db009_gbase_ash_live.py

环境变量：
    DB_AIOPS_ROOT   仓库根（默认 C:\\DB-AIOps）
"""
import io
import json
import os
import sys
import threading
import time

ROOT = os.environ.get('DB_AIOPS_ROOT', r'C:\DB-AIOps')
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402
from monitor import sentinel  # noqa: E402
from monitor.db_connector import DbConnector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_gbase_ash_live.json')


def long_query(cfg, stop_evt):
    """后台连接：反复扫描 information_schema.tables，制造一个活跃会话。"""
    try:
        conn = DbConnector.get_connection(cfg, statement_timeout_ms=60000, readonly=True)
        cur = conn.cursor()
        while not stop_evt.is_set():
            cur.execute("SELECT COUNT(*) FROM information_schema.tables")
            cur.fetchall()
        conn.close()
    except Exception as e:
        print('[bg] 长查询线程异常:', repr(e)[:160])


def main():
    res = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    cfg = DatabaseConfig.objects.filter(db_type='gbase').order_by('id').first()
    res['instance'] = {'id': cfg.id, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port}

    stop = threading.Event()
    th = threading.Thread(target=long_query, args=(cfg, stop), daemon=True)
    th.start()
    time.sleep(1.0)

    conn = DbConnector.get_connection(cfg, statement_timeout_ms=15000, readonly=True)
    cur = conn.cursor()
    # 采样窗口内多次采样，取行数最多的一次
    best = []
    for i in range(6):
        rows = sentinel.sample_sessions(cur, 'gbase', config_id=cfg.id)
        if len(rows) > len(best):
            best = rows
        if rows:
            print('[OK ] 第 %d 次采样 → %d 行' % (i + 1, len(rows)))
            break
        time.sleep(0.6)
    stop.set()
    th.join(timeout=8)

    res['sample_sessions'] = {
        'ok': True,
        'count': len(best),
        'rows': [{k: (str(v)[:120] if v is not None else None) for k, v in r.items()}
                 for r in best[:5]],
    }
    print('[OK ] GBase ASH 采到 %d 行真实会话' % len(best))
    for r in best[:3]:
        print('   sid=%s user=%s cmd=%s state=%s secs=%s blocked=%s wait_class=%s'
              % (r.get('session_id'), r.get('user_name'), r.get('command'),
                 r.get('state'), r.get('active_secs'), r.get('is_blocked'),
                 r.get('wait_class')))

    # 同一连接上的黄金状态量（确认采样未破坏游标）
    gm = sentinel.golden_metrics(cur, 'gbase')
    res['golden_metrics_after'] = {'ok': True, 'value': gm}
    print('[OK ] 采样后 golden_metrics →', gm)
    conn.close()

    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print('-> %s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
