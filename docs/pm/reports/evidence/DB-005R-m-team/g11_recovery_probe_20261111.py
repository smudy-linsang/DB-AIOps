# -*- coding: utf-8 -*-
"""DB-005R 补充单《恢复采集与新窗口重证》· 恢复后采样前推核验（只读）。

背景：宿主主机 2026-11-11 09:10:45 重启致采集中断（08:53 断），MMMan 16:59 起恢复采集器与 Web。
本脚本在两次读数之间等待 65s，验证 sql_stat 的 MAX(time) 持续前推（采样在写）。

纪律：只读；不驱动 workload、不改数据、不重启任何进程、不写业务库。
输出：与本脚本同目录的 g11_recovery_probe_20261111.json
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone as dt_tz

REPO = r'C:\DB-AIOps'
LOCAL_TZ = dt_tz(timedelta(hours=8))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'g11_recovery_probe_20261111.json')
CIDS = (1, 10, 16, 18, 19)
sys.path.insert(0, REPO)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()
from monitor.api_views_perf import _ts_cursor


def read(cur):
    out = {}
    for cid in CIDS:
        cur.execute('SELECT MAX(time), COUNT(*) FROM sql_stat WHERE db_config_id=%s AND time >= %s',
                    [cid, datetime.now(LOCAL_TZ) - timedelta(minutes=5)])
        mx, n5 = cur.fetchone()
        cur.execute('SELECT COUNT(*) FROM sql_stat WHERE db_config_id=%s', [cid])
        tot = cur.fetchone()[0]
        out[str(cid)] = {
            'max_time_local': mx.astimezone(LOCAL_TZ).isoformat() if mx else None,
            'rows_last_5min': n5, 'rows_total': tot,
        }
    cur.execute('SELECT MAX(time), COUNT(*) FROM metric_point')
    mx, n = cur.fetchone()
    out['_metric_point'] = {'max_time_local': mx.astimezone(LOCAL_TZ).isoformat() if mx else None,
                            'rows_total': n}
    cur.execute('SELECT MAX(time), COUNT(*) FROM collection_snapshot')
    mx2, n2 = cur.fetchone()
    out['_collection_snapshot'] = {'max_time_local': mx2.astimezone(LOCAL_TZ).isoformat() if mx2 else None,
                                  'rows_total': n2}
    return out


def main():
    t0 = datetime.now(LOCAL_TZ)
    with _ts_cursor() as cur:
        a = read(cur)
    time.sleep(65)
    t1 = datetime.now(LOCAL_TZ)
    with _ts_cursor() as cur:
        b = read(cur)
    adv = {}
    for cid in list(a.keys()):
        x, y = a[cid].get('max_time_local'), b[cid].get('max_time_local')
        adv[cid] = {
            'read_a_max_time': x, 'read_b_max_time': y,
            'advanced_seconds': round((datetime.fromisoformat(y) - datetime.fromisoformat(x)).total_seconds(), 1)
            if (x and y) else None,
            'rows_last_5min': [a[cid].get('rows_last_5min'), b[cid].get('rows_last_5min')],
        }
    rec = {
        'event': 'g11_recovery_probe',
        'read_a_at': t0.isoformat(), 'read_b_at': t1.isoformat(),
        'interval_seconds': round((t1 - t0).total_seconds(), 1),
        'read_a': a, 'read_b': b, 'advance': adv,
        'verdict': '采样时间持续前推' if all(
            v.get('advanced_seconds') and v['advanced_seconds'] > 0 for k, v in adv.items()
            if k in ('18', '19')) else '存在未前推项，需复查',
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rec, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
