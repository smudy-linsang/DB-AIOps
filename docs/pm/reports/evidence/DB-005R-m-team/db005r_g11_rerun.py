#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DB-005R 补充单：复跑（同一脚本、同一接口、同一命令）。

固定命令（与 v1.1 / v1.2 轮次完全一致）：
  reproduce_g11_day_baseline.py --cid 18 --cid2 19 --window 24h --periodic-days 7
  reproduce_g11_periodic_gate.py --cid 18 --cid2 19

时段标签（--slot 显式给出；由计划任务参数决定，不用时间推断）：
  d1_d3      11-12 日周期复判（days=3 判定，必须在 11-12 15:00 前完成）
  week_c18   11-12 20:35 上周同时段复跑（cid=18 采集满 7 天）
  week_c19   11-13 15:35 上周同时段复跑（cid=19 采集满 7 天）
  week_mature 11-14 16:00 周基线成熟轮（两路整天覆盖）
  d2_d3      11-15 日周期复判（days=3，新窗口 11-12/13/14 三个 15 点桶，
            判定窗 [11-15 09:50 - 3d, 11-15 09:50)）

口令：只在本进程内随机生成并经环境变量传给子进程，不落盘、不入库、不入证据；
      平台本地开发账号 mteam 会被重置为该随机值（副作用如实记录，不隐瞒）。
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone as dt_tz

REPO = r'C:\DB-AIOps'
VENV_PY = r'C:\DB-AIOps\.venv\Scripts\python.exe'
EV_DIR = os.path.join(REPO, 'docs', 'pm', 'reports', 'evidence', 'DB-005R-m-team')
OUT_DIR = r'C:\dispatcher\probe_out'
LOG = r'C:\dispatcher\db005r_g11_rerun.log'
BASE = 'http://127.0.0.1:8009'
LOCAL_TZ = dt_tz(timedelta(hours=8))


def log(msg):
    line = '%s %s' % (datetime.now().isoformat(timespec='seconds'), msg)
    try:
        with open(LOG, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except Exception:
        pass


def run(cmd, env, log_path):
    with open(log_path, 'w', encoding='utf-8') as fh:
        proc = subprocess.run(cmd, env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=1800,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return proc.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slot', required=True)
    ap.add_argument('--cid', type=int, default=18)
    ap.add_argument('--cid2', type=int, default=19)
    ap.add_argument('--window', default='24h')
    ap.add_argument('--periodic-days', type=int, default=7)
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    now = datetime.now(LOCAL_TZ)
    tag = args.slot
    day_out = os.path.join(OUT_DIR, 'db005r_g11_%s.json' % tag)
    day_compact = os.path.join(OUT_DIR, 'db005r_g11_%s_compact.json' % tag)
    gate_out = os.path.join(OUT_DIR, 'g11_periodic_gate_%s.json' % tag)
    pw = secrets.token_urlsafe(18)
    env = dict(os.environ)
    env.update({'BASE': BASE, 'MTEAM_USER': 'mteam', 'MTEAM_PW': pw, 'PYTHONIOENCODING': 'utf-8'})
    cmds = [
        [VENV_PY, os.path.join(EV_DIR, 'reproduce_g11_day_baseline.py'),
         '--cid', str(args.cid), '--cid2', str(args.cid2), '--window', args.window,
         '--periodic-days', str(args.periodic_days), '--out', day_out, '--compact-out', day_compact],
        [VENV_PY, os.path.join(EV_DIR, 'reproduce_g11_periodic_gate.py'),
         '--cid', str(args.cid), '--cid2', str(args.cid2), '--out', gate_out],
    ]
    rcs = []
    for i, cmd in enumerate(cmds):
        rc = run(cmd, env, os.path.join(OUT_DIR, 'db005r_g11_%s_cmd%d.log' % (tag, i)))
        rcs.append(rc)
        log('slot=%s cmd%d rc=%s' % (tag, i, rc))
    summary = {'slot': tag, 'generated_at': now.isoformat(), 'rcs': rcs,
               'command_line': ' '.join(cmds[0]),
               'note': '口令随机值只经环境变量传入，未落盘/未入库/未入证据'}
    for name, path in (('day_baseline', day_out), ('periodic_gate', gate_out)):
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
            if name == 'day_baseline':
                for cid, inst in (data.get('instances') or {}).items():
                    summary.setdefault('day_baseline', {})[cid] = {
                        'side_effects': data.get('side_effects'),
                        'regression_day': {k: (inst.get('regression_day') or {}).get(k)
                                           for k in ('http', 'counts', 'baseline_note', 'items_returned')},
                        'regression_week': {k: (inst.get('regression_week') or {}).get(k)
                                            for k in ('http', 'counts', 'baseline_note', 'items_returned')},
                        'periodic': {k: (inst.get('periodic') or {}).get(k)
                                     for k in ('http', 'days', 'items_returned', 'rule')},
                        'continuity': {k: (inst.get('continuity') or {}).get(k)
                                       for k in ('rows', 'distinct_digests', 'gap_count_gt_180s',
                                                 'first_time_point', 'last_time_point')}}
            else:
                for cid, inst in (data.get('instances') or {}).items():
                    summary.setdefault('periodic_gate', {})[cid] = {
                        'max_run_days': inst.get('max_run_days'),
                        'retention': inst.get('retention'),
                        'endpoint_sweep': inst.get('endpoint_sweep'),
                        'top_candidates': [{'digest': (c.get('digest') or '')[:12],
                                            'run_days': c.get('run_days'),
                                            'hours': c.get('hours'),
                                            'total_exec': c.get('total_exec'),
                                            'runs': c.get('runs'),
                                            'sql_text_sample': (c.get('sql_text_sample') or '')[:120]}
                                           for c in (inst.get('candidates') or [])[:5]]}
        except Exception as exc:
            summary.setdefault('errors', {})[name] = '%s: %s' % (type(exc).__name__, str(exc)[:120])
    sum_path = os.path.join(OUT_DIR, 'db005r_g11_%s_summary.json' % tag)
    with open(sum_path, 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)
    log('slot=%s DONE rcs=%s -> %s' % (tag, rcs, sum_path))
    try:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str)[:3000])
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
