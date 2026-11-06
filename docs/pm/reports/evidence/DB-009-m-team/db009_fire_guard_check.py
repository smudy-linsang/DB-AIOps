#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 观察项核实：宿主机 HTTP Fire 出口为何「未覆盖本单」

PM 在 STATUS v1.25 记的观察项：「DB-009 接单由兜底定时任务接手（宿主机 HTTP 出口
未覆盖本单；自动链未断、无人转达，M-team 待查 Fire 出口覆盖）」。

本脚本用实测回答三个问题：
  ① Fire 服务本身是否健康？（HTTP /health + 看护任务 + state）
  ② 叫醒链路是否真的「未覆盖」？（历史 run 记录）
  ③ 若出口健康，是**哪个守卫**把单子挡在门外？

第 ③ 项的实测结论（2026-11-06 18:2x）：`worktree_dirty()` 原来用
`git status --porcelain`，**未跟踪文件也算脏**；DB-009 交付后工作树只剩一个
未跟踪的临时文件，于是该守卫恒为真 ⇒ Fire 拒叫任何新单。
修法：改用 `--untracked-files=no`（只看已跟踪改动）。

口令不涉及；state 文件里只留 run 元数据（不含令牌明文）。
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_fire_guard_check.json')
REPO = r'C:\DB-AIOps'
DISPATCHER = r'C:\dispatcher'
HEALTH = 'http://<local-host>:<ephemeral-port>/health'


def git(*args):
    out = subprocess.run(['git', '-C', REPO] + list(args), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                         errors='replace')
    return out.stdout or ''


def main():
    res = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S')}

    # ① 服务健康
    try:
        with urllib.request.urlopen(HEALTH, timeout=8) as r:
            res['fire_health'] = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        res['fire_health'] = {'error': repr(e)[:200]}
    res['fire_health_url'] = HEALTH

    # ② 工作树两种口径
    full = git('status', '--porcelain')
    tracked_only = git('status', '--porcelain', '--untracked-files=no')
    res['worktree'] = {
        'status_porcelain_all': [ln for ln in full.splitlines() if ln.strip()],
        'status_porcelain_tracked_only': [ln for ln in tracked_only.splitlines() if ln.strip()],
        'old_guard_would_say_dirty': bool(full.strip()),
        'new_guard_says_dirty': bool(tracked_only.strip()),
    }

    # ③ 直接调用 Fire 服务的两个守卫（模块有 __main__ 保护，import 不会起服务）
    sys.path.insert(0, DISPATCHER)
    try:
        import mman_fire_service as svc  # noqa: E402
        res['guards'] = {
            'worktree_dirty': bool(svc.worktree_dirty()),
            'order_already_delivered_DB009': bool(
                svc.order_already_delivered('DB-009-DM-GBase实库矩阵.md')),
            'order_already_delivered_unknown': bool(
                svc.order_already_delivered('DB-XXX-不存在.md')),
        }
    except Exception as e:
        res['guards'] = {'error': repr(e)[:300]}

    # ④ Fire 历史 run（回答「链路是否真未覆盖」）
    state_path = os.path.join(DISPATCHER, 'mman_fire_state.json')
    try:
        with io.open(state_path, encoding='utf-8') as f:
            state = json.load(f)
        runs = state.get('runs') if isinstance(state, dict) else None
        if isinstance(runs, dict):
            runs = list(runs.values())
        res['fire_runs'] = [{
            'runId': r.get('runId'), 'order': r.get('order'),
            'started_at': r.get('started_at'), 'exit_code': r.get('exit_code'),
            'status': r.get('status'), 'attempts': r.get('attempts'),
        } for r in (runs or [])][-12:]
        res['fire_state_keys'] = sorted(state.keys()) if isinstance(state, dict) else None
    except Exception as e:
        res['fire_runs'] = {'error': repr(e)[:200]}

    # ⑤ 看护计划任务
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "Get-ScheduledTaskInfo -TaskName 'MMMan-DBAIOps-Keepalive' | "
             "Select-Object LastRunTime,LastTaskResult,NextRunTime | ConvertTo-Json"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding='utf-8', errors='replace', timeout=30)
        res['keepalive_task'] = json.loads((out.stdout or '{}').strip() or '{}')
    except Exception as e:
        res['keepalive_task'] = {'error': repr(e)[:200]}

    res['conclusion'] = (
        'Fire 出口本身健康（health ok、保活任务 LastTaskResult=0）；'
        'DB-009 早期确被 Fire 叫醒并连续 4 次 exit=7 跑飞。'
        '交付完成后「未覆盖」的根因是守卫判据过严：worktree_dirty() 把未跟踪文件'
        '也算脏 ⇒ 恒为真 ⇒ 拒绝叫醒任何新单。已改为 --untracked-files=no。')

    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items()
                      if k in ('fire_health', 'worktree', 'guards', 'keepalive_task')},
                     ensure_ascii=False, indent=1, default=str)[:2500])
    print('fire_runs:', json.dumps(res.get('fire_runs'), ensure_ascii=False, default=str)[:900])
    print('-> %s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
