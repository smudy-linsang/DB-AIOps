# -*- coding: utf-8 -*-
"""DB-005R 补充单 · G11 目标实例（cid=19/18）采集通路端到端证明（只读）。

背景：恢复采集后，cid=19/18（两个连驱目标探针库）在非驱动窗口内空闲，
performance_schema 无新 digest ⇒ sql_stat 无新行；仅凭 metric_point 前推不足以证明
「15:00 驱动窗口的 digest 一定能落进 TSDB」。

做法：对 cid=19/18 各执行 **一条只读 SELECT 1**（不改表、不改数据、不建索引），
随后等待一个采集轮次（60s），检查 sql_stat 是否为这两个实例落出新行。
落出新行 ⇒ 采集通路对 G11 目标实例端到端可用。

纪律：只读；不跑 workload（不产生 15 点桶执行量）、不重启任何进程、不改业务库。
输出：与本脚本同目录的 g11_recovery_digest_probe_20261111.json
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone as dt_tz

REPO = r'C:\DB-AIOps'
LOCAL_TZ = dt_tz(timedelta(hours=8))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'g11_recovery_digest_probe_20261111.json')
CIDS = (19, 18)
sys.path.insert(0, REPO)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()
from monitor.models import DatabaseConfig
from monitor.crypto import decrypt_password
from monitor.api_views_perf import _ts_cursor
import pymysql


# 入库脱敏（DB-005 同一口径）：实例名 / 本地地址 / 端口统一掩码，口令从不落盘
MASK_NAME = {19: '<instance-mysql>', 18: '<instance-proxy>'}
MASK_PORT = {19: '<mysql-port>', 18: '<proxy-port>'}


def targets():
    out = []
    for cid in CIDS:
        cfg = DatabaseConfig.objects.filter(id=cid).first()
        out.append({'cid': cid, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port,
                    'user': cfg.username, 'db': cfg.service_name or 'aiops_probe_shop',
                    'password': decrypt_password(cfg.password) if cfg.password else ''})
    return out


def snapshot(cur):
    snap = {}
    for cid in CIDS:
        cur.execute('SELECT COUNT(*), MAX(time) FROM sql_stat WHERE db_config_id=%s', [cid])
        n, mx = cur.fetchone()
        snap[str(cid)] = {'rows_total': n, 'max_time_local': mx.astimezone(LOCAL_TZ).isoformat() if mx else None}
    return snap


def main():
    ts = targets()
    probe = []
    for t in ts:
        rec = {'cid': t['cid'], 'name': MASK_NAME.get(t['cid'], '<instance>'),
               'host': '<local-host>', 'port': MASK_PORT.get(t['cid'], '<platform-port>')}
        try:
            conn = pymysql.connect(host=t['host'], port=int(t['port']), user=t['user'],
                                   password=t['password'], database=t['db'],
                                   connect_timeout=10, autocommit=True)
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                cur.fetchone()
            conn.close()
            rec['select_1'] = 'ok'
        except Exception as exc:
            rec['select_1'] = 'FAIL: %s: %s' % (type(exc).__name__, exc)
        probe.append(rec)
        time.sleep(2)

    t0 = datetime.now(LOCAL_TZ)
    with _ts_cursor() as cur:
        before = snapshot(cur)
    time.sleep(80)
    t1 = datetime.now(LOCAL_TZ)
    with _ts_cursor() as cur:
        after = snapshot(cur)

    delta = {}
    for cid in CIDS:
        k = str(cid)
        delta[k] = {
            'rows_before': before[k]['rows_total'], 'rows_after': after[k]['rows_total'],
            'new_rows': after[k]['rows_total'] - before[k]['rows_total'],
            'max_time_before': before[k]['max_time_local'], 'max_time_after': after[k]['max_time_local'],
        }
    rec = {
        'event': 'g11_recovery_digest_probe',
        'probe_select_1': probe,
        'read_before_at': t0.isoformat(), 'read_after_at': t1.isoformat(),
        'wait_seconds': round((t1 - t0).total_seconds(), 1),
        'before': before, 'after': after, 'delta': delta,
        'verdict': 'PASS：cid=19/18 采集通路端到端可用（新行落库）'
        if all(delta[str(c)]['new_rows'] > 0 for c in CIDS) else
        'FAIL/需复查：未见新行落库',
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rec, ensure_ascii=False, indent=1))
    return 0 if all(delta[str(c)]['new_rows'] > 0 for c in CIDS) else 1


if __name__ == '__main__':
    sys.exit(main())
