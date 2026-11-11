# -*- coding: utf-8 -*-
"""DB-005R 补充单 · cid=19 采样节奏核验（只读）。

背景：digest 探针 80s 窗口内 cid=19 未见新行，而此前 17:06:24→17:08:27 期间 cid=19 自行 +21 行。
怀疑是各实例 collect_interval_sec 差异导致轮次间隔 > 80s。本脚本：
① 列出各纳管实例的 collect_interval_sec；② 对 cid=19 做 130s 间隔双读，观察是否落新行。
纪律：只读；不驱动 workload、不改数据、不重启任何进程。
输出：与本脚本同目录的 g11_recovery_cadence_20261111.json
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone as dt_tz

REPO = r'C:\DB-AIOps'
LOCAL_TZ = dt_tz(timedelta(hours=8))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'g11_recovery_cadence_20261111.json')
sys.path.insert(0, REPO)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()
from monitor.models import DatabaseConfig
from monitor.api_views_perf import _ts_cursor

WAIT = 130
# 入库脱敏（DB-005 同一口径）：实例名 / 端口统一掩码
MASK_NAME = {19: '<instance-mysql>', 18: '<instance-proxy>'}
MASK_PORT = {19: '<mysql-port>', 18: '<proxy-port>'}


def main():
    cfgs = []
    for c in DatabaseConfig.objects.all().order_by('id'):
        cfgs.append({'id': c.id,
                     'name': MASK_NAME.get(c.id, '<instance-%d>' % c.id),
                     'db_type': c.db_type,
                     'port': MASK_PORT.get(c.id, '<platform-port>'),
                     'collect_interval_sec': c.collect_interval_sec})
    t0 = datetime.now(LOCAL_TZ)
    with _ts_cursor() as cur:
        cur.execute('SELECT COUNT(*), MAX(time) FROM sql_stat WHERE db_config_id=19')
        n0, mx0 = cur.fetchone()
    time.sleep(WAIT)
    t1 = datetime.now(LOCAL_TZ)
    with _ts_cursor() as cur:
        cur.execute('SELECT COUNT(*), MAX(time) FROM sql_stat WHERE db_config_id=19')
        n1, mx1 = cur.fetchone()
        cur.execute('SELECT DISTINCT time FROM sql_stat WHERE db_config_id=19 AND time >= %s ORDER BY time',
                    [t0 - timedelta(minutes=5)])
        ts = [x[0] for x in cur.fetchall()]
    gaps = [(ts[i] - ts[i - 1]).total_seconds() for i in range(1, len(ts))]
    rec = {
        'event': 'g11_recovery_cadence',
        'configs': cfgs,
        'cid19': {
            'read_before_at': t0.isoformat(), 'read_after_at': t1.isoformat(),
            'wait_seconds': WAIT,
            'rows_before': n0, 'rows_after': n1, 'new_rows': n1 - n0,
            'max_time_before': mx0.astimezone(LOCAL_TZ).isoformat() if mx0 else None,
            'max_time_after': mx1.astimezone(LOCAL_TZ).isoformat() if mx1 else None,
            'sample_times_recent': [x.astimezone(LOCAL_TZ).isoformat() for x in ts],
            'intervals_sec': gaps,
            'max_interval_sec': max(gaps) if gaps else None,
        },
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rec, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
