#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 独立验收工具⑥：订正后的**检测器级**指标快照（DM + GBase）。

与 `db009_dm_checker_run.py`（CLI 会话那份）的区别：
  ① 那份是**订正前**代码跑的（17:27），本份是订正 6 处语义后重跑的；
  ② 那份的 DM 连接走 pyodbc，本份 DM 走**平台自己的连接器**
     （DbConnector.get_connection → _connect_dm → pyodbc），
     GBase 走 _connect_mysql（DictCursor），与运行时完全一致；
  ③ GBase 侧同时验证 `active_connections` 契约字段已补齐。

关于 dmPython 缺失（重要事实，勿误判为平台缺陷）：
  达梦官方 wheel **可得** —— 本机已装 dmPython 2.5.32，但它装在
  **用户级 Python 3.14**（cp314 ABI），而平台运行时（runserver /
  start_monitor）用的是 `.venv`（**Python 3.11**，cp311 ABI），
  cp314 的 .pyd 在 cp311 里加载不了 ⇒ `import dmPython` 失败 ⇒
  `monitor.checkers.dm` 整个模块导入失败 ⇒ DamengChecker 不注册。

  本脚本用**进程内 sys.modules 桩**绕过这个 import gate，让
  `DamengChecker.collect_metrics` 能对真 DM8 实跑。桩的 `connect()`
  显式抛错 —— 它**不提供任何假连接**，真连接一律走平台连接器。
  （官方驱动下的直连复验见 db009_dm_sql_audit_dmpython.py）

用法：
    python db009_checker_run_final.py

环境变量：
    DB_AIOPS_ROOT   仓库根（默认 C:\\DB-AIOps）
"""
import io
import json
import os
import sys
import time
import types

ROOT = os.environ.get('DB_AIOPS_ROOT', r'C:\DB-AIOps')
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()

# ---- 进程内 dmPython 桩：只为绕过 import gate，不提供任何假连接 ----
if 'dmPython' not in sys.modules:
    _stub = types.ModuleType('dmPython')

    def _stub_connect(*_a, **_k):
        raise RuntimeError(
            'dmPython 桩：本机未在项目虚拟环境(.venv/cp311)安装达梦官方驱动，'
            '本脚本不提供假连接；真连接走平台连接器或官方驱动直连脚本。')

    _stub.connect = _stub_connect
    _stub.__db009_stub__ = True
    sys.modules['dmPython'] = _stub

from monitor.models import DatabaseConfig  # noqa: E402
from monitor.db_connector import DbConnector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_checker_run_final.json')

CORE_KEYS = ('active_connections', 'max_connections', 'conn_usage_pct', 'version',
             'db_mode', 'uptime_seconds', 'cache_hit_ratio', 'sql_count', 'tran_count',
             'threads_connected', 'threads_running', 'session_wait_count',
             'lock_wait_count', 'qps', 'tps', 'buffer_size_mb', 'arch_mode',
             'login_count', 'dsc_node_count')


class _FakeCommand:
    def stdout_write(self, *_a, **_k):
        pass

    def style_success(self, *a, **k):
        pass

    def style_error(self, *a, **k):
        pass


def snap(data):
    return {
        'total_keys': len(data),
        'core': {k: data.get(k) for k in CORE_KEYS},
        'none_scalar_keys': sorted([k for k, v in data.items()
                                     if v is None and not isinstance(v, (list, dict))]),
        'none_scalar_count': len([k for k, v in data.items()
                                  if v is None and not isinstance(v, (list, dict))]),
        'empty_list_keys': sorted([k for k, v in data.items() if v == []]),
        'empty_list_count': len([k for k, v in data.items() if v == []]),
        'result': 'OK' if data else 'EMPTY',
    }


def run_dm(res):
    from monitor.checkers.dm import DamengChecker
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    pwd = cfg.get_password()
    res['dm'] = {'target': {'id': cfg.id, 'name': cfg.name, 'host': cfg.host,
                            'port': cfg.port, 'db_type': cfg.db_type},
                 'password': 'len=%d,head=***' % len(pwd or '')}
    conn = DbConnector.get_connection(cfg, statement_timeout_ms=20000, readonly=True)
    try:
        data = DamengChecker(_FakeCommand()).collect_metrics(cfg, conn)
    finally:
        conn.close()
    res['dm']['snapshot'] = snap(data)
    # 订正涉及的 6 个字段，单独留一份明文取值以便逐条核对
    res['dm']['corrected_fields'] = {
        'session_wait_count': data.get('session_wait_count'),
        'login_count': data.get('login_count'),
        'dsc_node_count': data.get('dsc_node_count'),
        'buffer_pools_len': len(data.get('buffer_pools') or []),
        'buffer_pools_first': (data.get('buffer_pools') or [None])[0],
        'rlog_sync_status': data.get('rlog_sync_status'),
        'long_transactions_len': len(data.get('long_transactions') or []),
        'cache_hit_ratio': data.get('cache_hit_ratio'),
    }
    print('[DM ] total_keys=%d none_scalar=%d empty_list=%d'
          % (res['dm']['snapshot']['total_keys'],
             res['dm']['snapshot']['none_scalar_count'],
             res['dm']['snapshot']['empty_list_count']))
    print('      corrected:', json.dumps(res['dm']['corrected_fields'], ensure_ascii=False))


def run_gbase(res):
    from monitor.checkers.gbase import GbaseChecker
    cfg = DatabaseConfig.objects.filter(db_type='gbase').order_by('id').first()
    pwd = cfg.get_password()
    res['gbase'] = {'target': {'id': cfg.id, 'name': cfg.name, 'host': cfg.host,
                               'port': cfg.port, 'db_type': cfg.db_type},
                    'password': 'len=%d,head=***' % len(pwd or '')}
    conn = DbConnector.get_connection(cfg, statement_timeout_ms=20000, readonly=True)
    try:
        data = GbaseChecker(_FakeCommand()).collect_metrics(cfg, conn)
    finally:
        conn.close()
    res['gbase']['snapshot'] = snap(data)
    res['gbase']['active_connections'] = data.get('active_connections')
    print('[GB ] total_keys=%d active_connections=%r none_scalar=%d empty_list=%d'
          % (res['gbase']['snapshot']['total_keys'], data.get('active_connections'),
             res['gbase']['snapshot']['none_scalar_count'],
             res['gbase']['snapshot']['empty_list_count']))
    print('      none_scalar_keys:', res['gbase']['snapshot']['none_scalar_keys'])


def main():
    out = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'dm_python_stub': True,
           'dm_python_note': '进程内桩，仅绕过 import gate；connect() 显式抛错，'
                             '不提供任何假连接。真连接走平台连接器。',
           'dm': {}, 'gbase': {}}
    try:
        run_dm(out)
    except Exception as e:
        out['dm'] = {'error': repr(e)[:500]}
        print('[ERR] DM →', repr(e)[:200])
    try:
        run_gbase(out)
    except Exception as e:
        out['gbase'] = {'error': repr(e)[:500]}
        print('[ERR] GBase →', repr(e)[:200])
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print('-> %s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
