#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 核对 V$BUFFERPOOL.RAT_HIT 与本方口径（1 - 物理读/(逻辑读+物理读)）

冻结输入语义要求「缓存命中率」口径正确。DM8 的 V$BUFFERPOOL 自带 RAT_HIT
真值列，必须与本方实现的公式对照，确认二者同量级/同口径。
"""
import io
import json
import os
import sys

ROOT = os.environ.get('DB_AIOPS_ROOT', r'C:\DB-AIOps')
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402
from monitor.db_connector import DbConnector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_dm_cachehit_ratio_check.json')


def main():
    out = {}
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    conn = DbConnector.get_connection(cfg, statement_timeout_ms=15000, readonly=True)
    cur = conn.cursor()
    cur.execute("SELECT ID, NAME, N_LOGIC_READS, N_PHY_READS, RAT_HIT "
                "FROM V$BUFFERPOOL ORDER BY ID")
    rows = cur.fetchall()
    pools = []
    tot_l = tot_p = 0
    for r in rows:
        pools.append({'id': r[0], 'name': r[1], 'n_logic_reads': r[2],
                      'n_phy_reads': r[3], 'rat_hit': r[4]})
        tot_l += int(r[2] or 0)
        tot_p += int(r[3] or 0)
    formula = round((1 - tot_p * 1.0 / (tot_l + tot_p)) * 100, 2) if (tot_l + tot_p) else 0
    # RAT_HIT 的总体口径（按读次数加权）
    weighted = 0.0
    for p in pools:
        d = int(p['n_logic_reads'] or 0) + int(p['n_phy_reads'] or 0)
        if d:
            weighted += float(p['rat_hit'] or 0) * d
    total_d = tot_l + tot_p
    rat_hit_overall = round(weighted / total_d, 4) if total_d else None
    out = {
        'captured_at': __import__('time').strftime('%Y-%m-%dT%H:%M:%S'),
        'pools': pools,
        'pool_count': len(pools),
        'total_n_logic_reads': tot_l,
        'total_n_phy_reads': tot_p,
        'formula_1_minus_phy_over_logic_plus_phy_pct': formula,
        'rat_hit_weighted_overall': rat_hit_overall,
        'note': 'RAT_HIT 是 DM8 自带的命中率真值列；本方实现用 '
                '1 - N_PHY_READS/(N_LOGIC_READS+N_PHY_READS)。两者对照即可判定口径。',
    }
    conn.close()
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print('pools=%d  logic=%d phy=%d' % (len(pools), tot_l, tot_p))
    print('formula(1 - phy/(logic+phy)) = %s%%' % formula)
    print('RAT_HIT 加权总体            = %s' % rat_hit_overall)
    for p in pools[:6]:
        print('  pool %-10s logic=%-8s phy=%-8s RAT_HIT=%s' %
              (p['name'], p['n_logic_reads'], p['n_phy_reads'], p['rat_hit']))
    print('-> %s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
