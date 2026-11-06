#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-017 环境可得性实测探针（只读）。

测六件事，每件都留原始输出：
  1) ODBC 驱动列表（本机装的是哪些 DM 驱动）
  2) dmPython 可用性（find_spec / import 原始异常 / pip 声明）
  3) 检测器注册表（CHECKER_MAP 实际键、DM 是否注册、get_checker('dm'/'gbase') 返回值）
  4) DM 实例（id=9）TCP 可达性与平台连接器分支口径
  5) GBase 实例（id=11/id=15）TCP 可达性与 pymysql 连接结果
  6) 采集日志里 DM / GBase 的失败形态（原文摘录）

不写库、不改配置；凭据不落盘（DM/GBase 无凭据，只做可达性与失败形态探测）。
"""
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, r'C:\DB-AIOps')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')

OUT = r'C:\dispatcher\probe_out\db017_env.json'
LOGDIR = r'C:\DB-AIOps\logs'


def tcp_probe(host, port, timeout=5):
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return {'host': host, 'port': port, 'reachable': True,
                'ms': round((time.time() - t0) * 1000, 1)}
    except Exception as e:
        return {'host': host, 'port': port, 'reachable': False,
                'error': '%s: %s' % (type(e).__name__, e),
                'ms': round((time.time() - t0) * 1000, 1)}


def main():
    import django
    django.setup()
    res = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'checks': {}}

    # 1) ODBC 驱动
    odbc = {'pyodbc_import': None, 'drivers': None, 'error': None}
    try:
        import pyodbc
        odbc['pyodbc_import'] = 'ok'
        odbc['pyodbc_version'] = getattr(pyodbc, 'version', None)
        odbc['drivers'] = list(pyodbc.drivers())
        odbc['dm_drivers'] = [d for d in odbc['drivers'] if 'DM' in d.upper() or 'DM8' in d.upper()]
    except Exception as e:
        odbc['pyodbc_import'] = 'FAIL'
        odbc['error'] = '%s: %s' % (type(e).__name__, e)
    res['checks']['odbc_drivers'] = odbc

    # 2) dmPython
    dm = {}
    try:
        import importlib.util
        dm['find_spec'] = importlib.util.find_spec('dmPython') is not None
    except Exception as e:
        dm['find_spec'] = 'ERR %s' % e
    try:
        import dmPython  # noqa: F401
        dm['import'] = 'ok'
    except Exception as e:
        dm['import'] = 'FAIL'
        dm['import_error'] = '%s: %s' % (type(e).__name__, e)
    try:
        p = subprocess.run([r'C:\DB-AIOps\.venv\Scripts\python.exe', '-m', 'pip', 'show',
                            'dmPython'], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=60)
        dm['pip_show'] = (p.stdout or '').strip()[:400] or (p.stderr or '').strip()[:400]
    except Exception as e:
        dm['pip_show'] = 'ERR %s' % e
    req = r'C:\DB-AIOps\requirements.txt'
    try:
        with open(req, encoding='utf-8') as f:
            lines = [ln.rstrip() for ln in f if ln.strip()]
        dm['requirements_lines'] = [ln for ln in lines
                                    if any(k in ln.lower() for k in
                                           ('dm', 'gbase', 'mysql', 'pyodbc', 'tdsql'))]
    except Exception as e:
        dm['requirements_lines'] = ['ERR %s' % e]
    res['checks']['dm_python'] = dm

    # 3) 检测器注册表
    chk = {}
    try:
        from monitor import checkers
        chk['CHECKER_MAP_keys'] = sorted(checkers.CHECKER_MAP.keys())
        chk['DM_DRIVER_AVAILABLE'] = checkers.DM_DRIVER_AVAILABLE
        chk['DM_DRIVER_ERROR'] = checkers.DM_DRIVER_ERROR
        chk['is_dm_checker_available'] = checkers.is_dm_checker_available()
        chk['get_checker_dm'] = str(checkers.get_checker('dm'))
        chk['get_checker_gbase'] = str(checkers.get_checker('gbase'))
        chk['get_checker_mysql'] = str(checkers.get_checker('mysql'))
    except Exception as e:
        chk['error'] = '%s: %s' % (type(e).__name__, e)
    res['checks']['checker_registry'] = chk

    # 4/5) 实例清单 + 可达性
    from monitor.models import DatabaseConfig
    insts = []
    for c in DatabaseConfig.objects.filter(db_type__in=('dm', 'gbase')).order_by('id'):
        rec = {'id': c.id, 'name': c.name, 'db_type': c.db_type, 'host': c.host,
               'port': c.port, 'username': c.username, 'is_active': c.is_active,
               'tcp': tcp_probe(c.host, c.port)}
        if c.db_type == 'gbase':
            try:
                import pymysql
                conn = pymysql.connect(host=c.host, port=c.port, user=c.username,
                                       password=c.get_password(), connect_timeout=5)
                conn.close()
                rec['pymysql_connect'] = 'ok'
            except Exception as e:
                rec['pymysql_connect'] = '%s: %s' % (type(e).__name__, e)
        insts.append(rec)
    res['checks']['dm_gbase_instances'] = insts

    # 6) 采集日志摘录（DM / GBase 失败形态）
    logs = {}
    for fn in ('db_monitor.log', 'error.log'):
        p = os.path.join(LOGDIR, fn)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, 'rb') as f:
                raw = f.read()
            txt = None
            for enc in ('utf-8', 'gbk'):
                try:
                    txt = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if txt is None:
                txt = raw.decode('utf-8', errors='replace')
            picks = []
            for line in txt.splitlines():
                low = line.lower()
                if ('gbase' in low or '达梦' in line or 'dm8' in low or 'dm ' in low
                        or 'dameng' in low):
                    if len(picks) < 40:
                        picks.append(line.strip()[:220])
            logs[fn] = {'size': len(raw), 'matches': picks,
                        'mtime': time.strftime('%Y-%m-%dT%H:%M:%S',
                                              time.localtime(os.path.getmtime(p)))}
        except Exception as e:
            logs[fn] = {'error': '%s: %s' % (type(e).__name__, e)}
    res['checks']['collector_log'] = logs

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print('written', OUT)
    print(json.dumps(res['checks']['checker_registry'], ensure_ascii=False))
    print(json.dumps([{k: v for k, v in i.items() if k != 'tcp'} | {'tcp': i['tcp']['reachable']}
                      for i in insts], ensure_ascii=False))


if __name__ == '__main__':
    main()
