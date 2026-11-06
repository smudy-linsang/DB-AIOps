#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-017 · DM 实库可达性实测（本机 DM8 服务）。

事实核查：本机 127.0.0.1:5236 有 DM8 服务在跑（Windows 服务 DmServiceDAMENG Running，
进程 dmsvc）。此脚本按平台自身连接器口径（monitor/db_connector.py::_connect_dm：
pyodbc + "DRIVER={DM8 ODBC DRIVER};SERVER=host:port;UID=..;PWD=.."）验证：
  ① 连接层是否真能建连（连接层用 pyodbc，不依赖 dmPython）
  ② 基础只读方言能否取数（版本 / 实例 / 表枚举 / 系统视图）
  ③ 与巡检检测器层（dmPython 缺失 ⇒ DamengChecker 未注册）的对照

口令取自平台本地开发库中该实例已存密的密文，内存解密后直接使用；**不落盘、不打印明文**。
"""
import json
import os
import sys
import time

sys.path.insert(0, r'C:\DB-AIOps')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402

OUT = r'C:\dispatcher\probe_out\db017_dm_probe.json'


def mask(s):
    if not s:
        return '(empty)'
    return 'len=%d,head=***' % len(s)


def main():
    import pyodbc
    res = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'probe': {}}
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    if cfg is None:
        res['probe']['error'] = 'no dm instance in platform'
        _dump(res)
        return 2
    pwd = cfg.get_password()
    res['probe']['instance'] = {'id': cfg.id, 'name': cfg.name, 'host': cfg.host,
                                'port': cfg.port, 'username': cfg.username,
                                'is_active': cfg.is_active,
                                'credential': mask(pwd), 'credential_source': 'platform local dev DB (decrypted in memory)'}
    conn_str = ('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=%s;'
                % (cfg.host, cfg.port, cfg.username, pwd))
    res['probe']['conn_str_template'] = ('DRIVER={DM8 ODBC DRIVER};SERVER=%s:%s;UID=%s;PWD=<hidden>'
                                         % (cfg.host, cfg.port, cfg.username))
    t0 = time.time()
    conn = None
    try:
        conn = pyodbc.connect(conn_str, timeout=15)
        res['probe']['connect'] = {'ok': True, 'ms': round((time.time() - t0) * 1000, 1)}
    except Exception as e:
        res['probe']['connect'] = {'ok': False, 'ms': round((time.time() - t0) * 1000, 1),
                                   'error': '%s: %s' % (type(e).__name__, e)}
        _dump(res)
        return 1

    cur = conn.cursor()
    checks = [
        ('select_1', 'SELECT 1 FROM DUAL'),
        ('version', 'SELECT * FROM V$VERSION'),
        ('current_schema', 'SELECT USER FROM DUAL'),
        ('db_name', 'SELECT NAME FROM V$DATABASE'),
        ('table_count', "SELECT COUNT(*) FROM DBA_TABLES WHERE OWNER = (SELECT USER FROM DUAL)"),
        ('session_info', 'SELECT SESSID, USER_NAME, HOST_NAME, CLNT_IP, TYPE FROM V$SESSIONS'),
        ('dm_sys_table', "SELECT COUNT(*) FROM DBA_TABLES WHERE TABLE_NAME LIKE 'V$%'"),
    ]
    res['probe']['queries'] = []
    for name, sql in checks:
        rec = {'name': name, 'sql': sql}
        try:
            cur.execute(sql)
            rows = cur.fetchmany(5)
            rec['ok'] = True
            rec['rowcount'] = len(rows)
            rec['sample'] = [[str(c)[:60] for c in r] for r in rows]
        except Exception as e:
            rec['ok'] = False
            rec['error'] = '%s: %s' % (type(e).__name__, e)
        res['probe']['queries'].append(rec)

    # 驱动层对照：巡检检测器链是否可用（dmPython）
    try:
        from monitor import checkers
        res['probe']['detector_layer'] = {
            'DM_DRIVER_AVAILABLE': checkers.DM_DRIVER_AVAILABLE,
            'DM_DRIVER_ERROR': checkers.DM_DRIVER_ERROR,
            'get_checker_dm': str(checkers.get_checker('dm')),
            'CHECKER_MAP_keys': sorted(checkers.CHECKER_MAP.keys()),
        }
    except Exception as e:
        res['probe']['detector_layer'] = {'error': '%s: %s' % (type(e).__name__, e)}

    try:
        conn.close()
    except Exception:
        pass
    _dump(res)
    return 0


def _dump(res):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res['probe'].get('connect'), ensure_ascii=False))
    for q in res['probe'].get('queries', []):
        print(q['name'], q.get('ok'), q.get('rowcount'), str(q.get('error'))[:120])


if __name__ == '__main__':
    sys.exit(main())
