# -*- coding: utf-8 -*-
"""DB-AIOps 补充单《恢复采集与新窗口重证》· 纳管实例采集覆盖复算（只读）。

用途：核实回执 §15「10 个纳管实例中 8 个已有采集行」这一表述与实测是否一致，
并逐实例给出 sql_stat / metric_point 覆盖实况与「无行」归因。

纪律：全程只读——不驱动 workload、不改业务库、不重启任何进程、不改任何容器。
输出：与本脚本同目录的 g11_instance_coverage_20261111.json
入库脱敏（DB-005 同一口径）：实例名按 cid 掩码；不落任何端口/口令/地址。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone as dt_tz

REPO = r'C:\DB-AIOps'
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'g11_instance_coverage_20261111.json')
LOCAL = dt_tz(timedelta(hours=8))
MASK_NAME = {19: '<instance-mysql>', 18: '<instance-proxy>', 1: '<instance-mysql-test>',
             10: '<instance-tdsql-sim>', 16: '<instance-tdsql>', 2: '<instance-pg>',
             3: '<instance-oracle>', 9: '<instance-dm>', 11: '<instance-gbase>',
             15: '<instance-gbase-probe>'}
sys.path.insert(0, REPO)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()
from monitor.models import DatabaseConfig
from monitor.api_views_perf import _ts_cursor


def main():
    now = datetime.now(LOCAL)
    rec = {'event': 'g11_instance_coverage_20261111',
           'generated_at': now.isoformat(),
           'note': '全程只读：未驱动 workload、未改业务库、未重启任何进程、未改任何容器',
           'instances': []}
    with _ts_cursor() as cur:
        if cur is None:
            rec['error'] = 'TSDB 不可用'
        else:
            for c in DatabaseConfig.objects.all().order_by('id'):
                cur.execute('SELECT COUNT(*), MAX(time) FROM sql_stat WHERE db_config_id=%s', [c.id])
                tot, mx = cur.fetchone()
                cur.execute('SELECT COUNT(*) FROM sql_stat WHERE db_config_id=%s AND time >= %s',
                            [c.id, now - timedelta(hours=24)])
                h24 = cur.fetchone()[0]
                cur.execute('SELECT MAX(time) FROM metric_point WHERE db_config_id=%s', [c.id])
                mp = cur.fetchone()[0]
                rec['instances'].append({
                    'cid': c.id, 'name': MASK_NAME.get(c.id, '<instance-%d>' % c.id),
                    'db_type': c.db_type,
                    'sql_stat_rows_total': tot,
                    'sql_stat_max_time_local': mx.astimezone(LOCAL).isoformat() if mx else None,
                    'sql_stat_rows_24h': h24,
                    'metric_point_max_time_local': mp.astimezone(LOCAL).isoformat() if mp else None,
                    'has_sql_stat_rows': bool(tot),
                    'has_metric_point': bool(mp),
                })
    inst = rec.get('instances') or []
    with_rows = [i['cid'] for i in inst if i['has_sql_stat_rows']]
    without = [i['cid'] for i in inst if not i['has_sql_stat_rows']]
    rec['summary'] = {
        'total_instances': len(inst),
        'with_sql_stat_rows': len(with_rows),
        'with_sql_stat_rows_cids': with_rows,
        'without_sql_stat_rows': len(without),
        'without_sql_stat_rows_cids': without,
    }
    rec['reason_notes'] = {
        '2': '采集在推进（metric_point 前推），但本环境缺 pg_stat_statements 扩展 ⇒ 无 sql_stat digest 行（既有已知告警）',
        '3': '容器已 healthy，但容器 healthcheck 日志显示 common 用户口令已过期（ORA-65162）⇒ 认证失败，平台无任何采集行；且该 healthcheck 退出码仍为 0 ⇒ 报 healthy（双重误导）',
        '9': 'DM 检测器未注册（缺 dmPython）⇒ 采集跳过（G-D1 驱动批次已顺延）',
        '11': '采集在推进（metric_point 前推），但 GBase 无 performance_schema digest 口径 ⇒ 无 sql_stat 行',
        '15': '采集在推进（metric_point 前推），但 GBase 无 performance_schema digest 口径 ⇒ 无 sql_stat 行',
    }
    rec['healthcheck_false_negative'] = {
        'container': 'db-aiops-gbase',
        'docker_ps_status': 'unhealthy',
        'container_listening_port_verified': True,
        'healthcheck_cmd_shape': "<client> -u<user> -p<pwd> -e 'SELECT 1'（未 source ~/.bashrc ⇒ 客户端不在 PATH ⇒ exit 1）",
        'verdict': '配置假阴性：实例实际可用，非功能缺陷',
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rec, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
