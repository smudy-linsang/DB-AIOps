# -*- coding: utf-8 -*-
"""G-11 2026-11-11 当日核验（只读）：workload digest 落桶 + 采集连续性。

只读查询 TSDB sql_stat，不驱动负载、不改数据、不重启任何进程。
输出：C:\\dispatcher\\probe_out\\g11_bucket_verify_20261111.json
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone as dt_tz

REPO = r'C:\DB-AIOps'
OUT = r'C:\dispatcher\probe_out\g11_bucket_verify_20261111.json'
LOCAL_TZ = dt_tz(timedelta(hours=8))
WL_PREFIX = '1581b156e4'
CIDS = (19, 18)

sys.path.insert(0, REPO)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()
from monitor.api_views_perf import _ts_cursor

now = datetime.now(LOCAL_TZ)
rec = {'at': now.isoformat(), 'date': now.date().isoformat(), 'event': 'bucket_verify',
       'instances': {}, 'source': 'manual_readonly_recheck_after_host_reboot'}
with _ts_cursor() as cur:
    if cur is None:
        rec['error'] = 'TSDB 不可用'
    else:
        for cid in CIDS:
            cur.execute('SELECT time, SUM(exec_delta) FROM sql_stat WHERE db_config_id=%s '
                        'AND sql_digest LIKE %s AND time >= %s GROUP BY 1 ORDER BY 1',
                        [cid, WL_PREFIX + '%', now - timedelta(days=6)])
            buckets = {}
            for t, ex in cur.fetchall():
                loc = t.astimezone(LOCAL_TZ)
                key = loc.replace(minute=0, second=0, microsecond=0).isoformat()
                buckets[key] = buckets.get(key, 0) + int(ex or 0)
            cur.execute('SELECT MAX(time), COUNT(*) FROM sql_stat WHERE db_config_id=%s', [cid])
            mx, n = cur.fetchone()
            cur.execute('SELECT DISTINCT time FROM sql_stat WHERE db_config_id=%s AND time >= %s'
                        ' ORDER BY time', [cid, now - timedelta(hours=30)])
            ts = [x[0] for x in cur.fetchall()]
            gaps = sum(1 for i in range(1, len(ts))
                       if (ts[i] - ts[i - 1]).total_seconds() > 180)
            expect = now.replace(hour=15, minute=0, second=0, microsecond=0).isoformat()
            rec['instances'][str(cid)] = {
                'max_time': str(mx), 'rows_total': n,
                'gap_count_gt_180s_30min': gaps,
                'workload_digest_hour_buckets': dict(sorted(buckets.items())),
                'expected_15h_bucket': expect,
                'exec_in_expected_bucket': buckets.get(expect, 0)}

with open(OUT, 'w', encoding='utf-8') as fh:
    json.dump(rec, fh, ensure_ascii=False, indent=2)
print(json.dumps(rec, ensure_ascii=False, indent=2))
