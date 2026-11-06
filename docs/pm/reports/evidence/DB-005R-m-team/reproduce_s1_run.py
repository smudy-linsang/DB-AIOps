#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-005R 第一段 · S-1：在 db_type=mysql 的真实 MySQL 8.0 实例上跑同口径链路级回归。

复用 DB-005 的脚本与口径（同一 workload、同一验收脚本、同一检查项编号），
唯一差别：目标实例是「真实 MySQL 8.0 直连」，不经 TDSQL mock proxy。

阶段：
  P0 建实例 + 复位本地开发账号
  P1 场景A：索引在位 → workload A（基线窗）
  P2 计划采集（before）
  P3 删索引 → workload B（退化窗）
  P4 计划采集（after）
  P5 API 链路级回归（慢列表/详情/建议/耗时构成/环比/告警）
  P6 A-2 专项（索引存在时不重复建议）
  P7 G-13 探针（INTERVAL ? HOUR → 502；两侧实例对照 + 目标库直证）

凭据：mock 靶场 <account> 口令与本地开发账号口令一律走环境变量，不落盘、不入库。
"""
import json
import os
import subprocess
import sys
import time

PY_VENV = r'C:\DB-AIOps\.venv\Scripts\python.exe'
PY_ANY = r'C:\Python314\python.exe'
WL = r'C:\DB-AIOps\scripts\tdsql_workload.py'
CHECK3 = r'C:\dispatcher\db005_api_check3.py'
EXPLAIN = r'C:\dispatcher\db005_explain.py'
A2 = r'C:\dispatcher\db005_a2_check.py'
OUT = r'C:\dispatcher\probe_out'
LOG = os.path.join(OUT, 'db005r_s1_log.json')

HOST = os.environ.get('TDSQL_PROBE_HOST', '<local-host>')
PORT = os.environ.get('TDSQL_PROBE_PORT', '<target-port>')
USER = os.environ.get('TDSQL_PROBE_USER', '<account>')
PASS = os.environ['TDSQL_PROBE_PASSWORD']
DB = os.environ.get('TDSQL_PROBE_DB', 'aiops_probe_shop')
MINUTES = float(os.environ.get('S1_MINUTES', '12'))
LOCKWAIT_AT = float(os.environ.get('S1_LOCKWAIT_AT', '120'))

BASE = 'http://<local-host>:<platform-port>'
STATE = {'log': [], 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S')}


def save():
    os.makedirs(OUT, exist_ok=True)
    with open(LOG, 'w', encoding='utf-8') as f:
        json.dump(STATE, f, ensure_ascii=False, indent=2)


def phase(name, ok, detail=None):
    STATE['log'].append({'phase': name, 'ok': bool(ok), 'detail': detail,
                          'at': time.strftime('%H:%M:%S')})
    save()
    print('[%s] %s ok=%s %s' % (time.strftime('%H:%M:%S'), name, ok,
                                (str(detail)[:160] if detail else '')), flush=True)


def run(cmd, env, stdout_path, timeout=3600):
    with open(stdout_path, 'w', encoding='utf-8') as f:
        p = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT,
                           timeout=timeout)
    return p.returncode


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S+08:00')


def main():
    os.makedirs(OUT, exist_ok=True)
    wl_env = dict(os.environ)
    wl_env.update({'TDSQL_WL_HOST': HOST, 'TDSQL_WL_PORT': PORT,
                   'TDSQL_WL_USER': USER, 'TDSQL_WL_PASSWORD': PASS,
                   'TDSQL_WL_DB': DB, 'PYTHONIOENCODING': 'utf-8'})
    wl_env.setdefault('TDSQL_PROBE_DB', DB)
    wl_env.setdefault('TDSQL_PROBE_PASSWORD', PASS)
    api_env = dict(os.environ)
    api_env.update({'BASE': BASE, 'MTEAM_USER': 'mteam',
                    'MTEAM_PW': os.environ['MTEAM_PW'],
                    'PROBE_HOST': HOST, 'PROBE_PORT': PORT,
                    'PROBE_USER': USER, 'PROBE_PASSWORD': PASS,
                    'PROBE_DB': DB, 'PYTHONIOENCODING': 'utf-8'})
    api_env.setdefault('TDSQL_PROBE_DB', DB)
    api_env.setdefault('TDSQL_PROBE_PASSWORD', PASS)

    # ---------- P0 建实例 ----------
    setup_code = (
        "import os,sys,django\n"
        "sys.path.insert(0,r'C:\\\\DB-AIOps')\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','dbmonitor.settings')\n"
        "django.setup()\n"
        "from django.contrib.auth import get_user_model\n"
        "from monitor.models import DatabaseConfig\n"
        "from monitor.crypto import encrypt_password\n"
        "U=get_user_model()\n"
        "u=U.objects.filter(username='mteam').first()\n"
        "assert u is not None, 'mteam user missing'\n"
        "u.set_password(os.environ['MTEAM_PW']); u.save()\n"
        "name='M-team probe-mysql-8.0'\n"
        "cfg=DatabaseConfig.objects.filter(name=name).first()\n"
        "if cfg is None:\n"
        "    cfg=DatabaseConfig(name=name, db_type='mysql', host='<local-host>', port=<target-port>,\n"
        "        username='<account>', password=encrypt_password(os.environ['TDSQL_PROBE_PASSWORD']),\n"
        "        service_name=os.environ['TDSQL_PROBE_DB'], is_active=True, collect_interval_sec=60)\n"
        "    cfg.save()\n"
        "else:\n"
        "    cfg.db_type='mysql'; cfg.host='<local-host>'; cfg.port=<target-port>; cfg.username='<account>'\n"
        "    cfg.password=encrypt_password(os.environ['TDSQL_PROBE_PASSWORD'])\n"
        "    cfg.service_name=os.environ['TDSQL_PROBE_DB']; cfg.is_active=True; cfg.collect_interval_sec=60\n"
        "    cfg.save()\n"
        "print('CID=%d' % cfg.id)\n"
    )
    r = subprocess.run([PY_VENV, '-c', setup_code], env=wl_env, capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    cid = None
    for line in (r.stdout or '').splitlines():
        if line.startswith('CID='):
            cid = int(line.split('=')[1])
    phase('P0 建实例(mysql/真实MySQL8.0)+账号复位', cid is not None,
          'cid=%s stdout=%s err=%s' % (cid, (r.stdout or '').strip()[:120],
                                       (r.stderr or '')[-200:]))
    if cid is None:
        return 2
    api_env['CID'] = str(cid)
    STATE['cid'] = cid
    save()

    # ---------- P1 场景A：有索引 ----------
    rc = run([PY_VENV, WL, 'index', 'add'], wl_env, os.path.join(OUT, 's1_index_add.log'))
    phase('P1a 索引补回(idx_user_created)', rc == 0, 'rc=%s' % rc)
    tA = {}
    tA['from'] = now_iso()
    rc = run([PY_VENV, WL, 'run', '--minutes', str(MINUTES), '--qps', '5'], wl_env,
             os.path.join(OUT, 's1_run_A.log'))
    tA['to'] = now_iso()
    STATE['window_A'] = tA
    phase('P1b 场景A workload(%smin, 有索引)', rc == 0, 'rc=%s window=%s' % (rc, tA))

    # ---------- P2 计划采集(before) ----------
    rc = run([PY_ANY, EXPLAIN, '--label', 'before', '--out',
              os.path.join(OUT, 's1_plan_before.json'), '--time', '30m'],
             api_env, os.path.join(OUT, 's1_plan_before.log'))
    phase('P2 计划采集 before(索引在位)', rc == 0, 'rc=%s' % rc)

    # ---------- P3 场景B：删索引 ----------
    rc = run([PY_VENV, WL, 'index', 'drop'], wl_env, os.path.join(OUT, 's1_index_drop.log'))
    phase('P3a 索引删除', rc == 0, 'rc=%s' % rc)
    tB = {}
    tB['from'] = now_iso()
    lock_log = os.path.join(OUT, 's1_lockwait.log')
    # workload B 与锁等待并行：锁等待单独起一个进程，在 B 窗开始后 LOCKWAIT_AT 秒触发
    p = subprocess.Popen([PY_VENV, WL, 'run', '--minutes', str(MINUTES), '--qps', '5'],
                         env=wl_env, stdout=open(os.path.join(OUT, 's1_run_B.log'), 'w',
                                                 encoding='utf-8'),
                         stderr=subprocess.STDOUT)
    time.sleep(LOCKWAIT_AT)
    rc2 = run([PY_VENV, WL, 'lockwait', '--seconds', '20'], wl_env, lock_log, timeout=180)
    phase('P3b 锁等待注入(场景B窗内)', rc2 == 0, 'rc=%s' % rc2)
    rc = p.wait(timeout=3600)
    tB['to'] = now_iso()
    STATE['window_B'] = tB
    phase('P3c 场景B workload(%smin, 索引已删)', rc == 0, 'rc=%s window=%s' % (rc, tB))
    save()

    # ---------- P4 计划采集(after) ----------
    rc = run([PY_ANY, EXPLAIN, '--label', 'after', '--out',
              os.path.join(OUT, 's1_plan_after.json'), '--time', '30m'],
             api_env, os.path.join(OUT, 's1_plan_after.log'))
    phase('P4 计划采集 after(索引已删)', rc == 0, 'rc=%s' % rc)

    # ---------- P5 API 链路级回归 ----------
    out_json = os.path.join(OUT, 'db005r_s1_api.json')
    cmd = [PY_ANY, CHECK3, '--cid', str(cid), '--from', tB['from'], '--to', tB['to'],
           '--base-from', tA['from'], '--base-to', tA['to'], '--slow-time', '30m',
           '--label', 'S1-mysql80', '--out', out_json]
    rc = run(cmd, api_env, os.path.join(OUT, 's1_api_check.log'))
    phase('P5 API 链路级回归(慢列表/详情/建议/耗时构成/环比/告警)', rc == 0, 'rc=%s out=%s' % (rc, out_json))

    # ---------- P6 A-2 专项 ----------
    digest = ''
    try:
        with open(out_json, encoding='utf-8') as f:
            data = json.load(f)
        for k, v in (data.get('notes', {}).get('digests') or {}).items():
            if k == 'slow_user_sort':
                digest = v.get('digest32') or ''
    except Exception as e:
        phase('P6 前置：取 slow_user_sort digest', False, repr(e))
    if digest:
        rc = run([PY_ANY, A2, '--digest', digest, '--out',
                  os.path.join(OUT, 'db005r_s1_a2.json')], api_env,
                 os.path.join(OUT, 's1_a2.log'))
        phase('P6 A-2 专项(索引存在时不重复建议)', rc == 0, 'rc=%s digest=%s' % (rc, digest))
    else:
        phase('P6 A-2 专项', False, '未取到 digest')

    # ---------- P7 G-13 探针 ----------
    g13 = r'C:\dispatcher\db005r_g13_probe.py'
    rc = run([PY_ANY, g13, '--cid', str(cid), '--cid2', '18',
              '--out', os.path.join(OUT, 'db005r_g13.json')], api_env,
             os.path.join(OUT, 's1_g13.log'))
    phase('P7 G-13 探针(INTERVAL ? HOUR → 502)', rc == 0, 'rc=%s' % rc)

    STATE['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    save()
    print('DONE', json.dumps(STATE['log'], ensure_ascii=False)[:400])
    return 0


if __name__ == '__main__':
    sys.exit(main())
