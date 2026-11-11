# -*- coding: utf-8 -*-
"""DB-005R 恢复轮 · 全纳管实例采集覆盖实测（只读）。

用途：回答独立核验提出的第 5 项出入 —— 「cid=2 无 sql_stat 行是否为采集中断」。
口径：sql_stat 是 MySQL 家族 performance_schema digest 表；collection_snapshot /
metric_point 才是跨方言的采集面。本脚本同时统计三张表近 30 分钟的行数与 MAX(time)，
用实测区分「表口径差异」与「真采集中断」。

纪律：只读（平台自身 _ts_cursor，纯 SELECT）；不驱动 workload、不改数据、不重启进程。
输出：与本脚本同目录的 g11_per_instance_coverage_20261111.json
"""
import json, os, sys
from datetime import datetime, timedelta, timezone as dt_tz

REPO = r'C:\DB-AIOps'
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'g11_per_instance_coverage_20261111.json')
LOCAL = dt_tz(timedelta(hours=8))
CIDS = (1, 2, 3, 9, 10, 11, 15, 16, 18, 19)
TABLES = ('sql_stat', 'metric_point', 'collection_snapshot')


def main():
    sys.path.insert(0, REPO)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
    import django
    django.setup()
    from monitor.api_views_perf import _ts_cursor
    now = datetime.now(LOCAL)
    since = now - timedelta(minutes=30)
    rec = {'event': 'g11_per_instance_coverage', 'at': now.isoformat(),
           'window_minutes': 30, 'tables': {}}
    with _ts_cursor() as cur:
        for tbl in TABLES:
            per = {}
            for cid in CIDS:
                cur.execute('SELECT COUNT(*), MAX(time) FROM %s WHERE db_config_id=%%s AND time >= %%s' % tbl,
                            [cid, since])
                n, mx = cur.fetchone()
                per[str(cid)] = {'rows_30min': int(n or 0),
                                 'max_time_local': mx.astimezone(LOCAL).isoformat() if mx else None}
            rec['tables'][tbl] = per
    rec['summary'] = {
        'sql_stat_instances_with_rows': sum(1 for v in rec['tables']['sql_stat'].values() if v['rows_30min'] > 0),
        'metric_point_instances_with_rows': sum(1 for v in rec['tables']['metric_point'].values() if v['rows_30min'] > 0),
        'collection_snapshot_instances_with_rows': sum(1 for v in rec['tables']['collection_snapshot'].values() if v['rows_30min'] > 0),
        'instances_total': len(CIDS),
        'note': 'sql_stat 为 MySQL 家族 performance_schema digest 表，PG/Oracle/DM/GBase 不产生该表行；'
                'collection_snapshot 覆盖全部实例，才是跨方言的采集面判据。',
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rec['summary'], ensure_ascii=False, indent=1))
    for tbl in TABLES:
        print(tbl, {k: v['rows_30min'] for k, v in rec['tables'][tbl].items()})
    return 0


if __name__ == '__main__':
    sys.exit(main())
