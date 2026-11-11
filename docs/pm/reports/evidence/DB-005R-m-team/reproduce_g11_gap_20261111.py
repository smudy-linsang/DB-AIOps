# -*- coding: utf-8 -*-
"""G-11 2026-11-11 采集中断缺口量化（只读）。

量化主机重启导致的采集中断：11-11 各小时 rows 分布、最后采集时间与当前差值、
11-11 15:00 桶是否有任何 workload digest 记录（不限前缀）。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone as dt_tz

REPO = r'C:\DB-AIOps'
OUT = r'C:\dispatcher\probe_out\g11_gap_20261111.json'
LOCAL_TZ = dt_tz(timedelta(hours=8))

sys.path.insert(0, REPO)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()
from monitor.api_views_perf import _ts_cursor

now = datetime.now(LOCAL_TZ)
rec = {'at': now.isoformat(), 'date': now.date().isoformat(), 'event': 'collection_gap_audit'}
with _ts_cursor() as cur:
    for cid in (19, 18):
        cur.execute('SELECT time, COUNT(*) FROM sql_stat WHERE db_config_id=%s AND time >= %s'
                    ' GROUP BY 1 ORDER BY 1', [cid, now - timedelta(days=2)])
        rows = [(t, n) for t, n in cur.fetchall()]
        byhour = {}
        for t, n in rows:
            k = t.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:00')
            byhour[k] = byhour.get(k, 0) + n
        last = rows[-1][0].astimezone(LOCAL_TZ) if rows else None
        # 11-11 全天（本地）是否有任何 workload digest 落库
        cur.execute('SELECT time, SUM(exec_delta), COUNT(*) FROM sql_stat WHERE db_config_id=%s '
                    'AND time >= %s AND time < %s GROUP BY 1 ORDER BY 1',
                    [cid, datetime(2026, 11, 11, 0, 0, tzinfo=LOCAL_TZ),
                     datetime(2026, 11, 12, 0, 0, tzinfo=LOCAL_TZ)])
        day111 = [(t.astimezone(LOCAL_TZ).isoformat(), int(e or 0), int(c))
                  for t, e, c in cur.fetchall()]
        rec['instances'] = rec.get('instances', {})
        rec['instances'][str(cid)] = {
            'rows_by_local_hour': dict(sorted(byhour.items())),
            'last_sample_local': last.isoformat() if last else None,
            'minutes_since_last_sample': round((now - last).total_seconds() / 60, 1) if last else None,
            'samples_on_2026_11_11_local': len(day111),
            'last_5_samples_on_1111': day111[-5:],
        }
with open(OUT, 'w', encoding='utf-8') as fh:
    json.dump(rec, fh, ensure_ascii=False, indent=2)
print(json.dumps(rec, ensure_ascii=False, indent=2))
