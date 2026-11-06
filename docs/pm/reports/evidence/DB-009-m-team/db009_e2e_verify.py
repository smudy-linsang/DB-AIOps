#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-009 · 独立验收工具②：端到端验收（全部走平台自己的连接器）。

为什么强调「走平台连接器」：
  平台 `_connect_mysql` 用 **DictCursor**、`_connect_dm` 用 pyodbc，
  而 MySQL 系取列值是 `r['Value']`。第一版脚手架用普通 tuple 游标去测
  `golden_metrics`，报 TypeError —— 那是**脚手架用错游标类型**，
  不是平台缺陷。教训：验收必须复刻平台真实的连接与游标路径。

为什么强调「AST 取源码 SQL」：
  第一版用正则把 start_monitor.py 里的 SQL 拼出来，拼错导致 TypeError，
  同样被误判成平台缺陷。第二版改用 AST 取源码片段原样 eval，
  并且**直接调用平台真实方法** `Command._snapshot_sqlstat`。

被测代码路径：
  ① monitor.sentinel.sample_sessions(cur, db_type)  —— DM 分支 / GBase MySQL 家族分支
  ② monitor.sentinel.golden_metrics(cur, db_type)   —— 黄金状态量
  ③ start_monitor 的 DM sql_stat 分支                —— 直接调用平台真实方法

用法：
    python db009_e2e_verify.py

环境变量：
    DB_AIOPS_ROOT   仓库根（默认 C:\\DB-AIOps）
"""
import ast
import io
import json
import os
import sys
import time
import traceback

ROOT = os.environ.get('DB_AIOPS_ROOT', r'C:\DB-AIOps')
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402

django.setup()
from monitor.models import DatabaseConfig  # noqa: E402
from monitor import sentinel  # noqa: E402
from monitor.db_connector import DbConnector  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'db009_e2e_verify.json')
SRC_START = os.path.join(ROOT, 'monitor', 'management', 'commands', 'start_monitor.py')


def dm_sql_from_source():
    """AST 解析 start_monitor.py，取 _snapshot_sqlstat 中 db_type=='dm' 分支的
    cur.execute(...) 实参源码片段（相邻字面量 + f-string 原样保留）。"""
    src = io.open(SRC_START, encoding='utf-8').read()
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_snapshot_sqlstat':
            fn = node
            break
    assert fn is not None, '_snapshot_sqlstat not found'
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Attribute)
                and test.left.attr == 'db_type'):
            continue
        if not (test.comparators and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == 'dm'):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr == 'execute' and sub.args:
                return ast.get_source_segment(src, sub.args[0])
    raise AssertionError('dm branch execute() not found')


class _Self:
    _SQLSTAT_TOPN = 20


def run_dm(res):
    cfg = DatabaseConfig.objects.filter(db_type='dm').order_by('id').first()
    conn = DbConnector.get_connection(cfg, statement_timeout_ms=15000, readonly=True)
    cur = conn.cursor()
    print('== DM 连接(平台连接器) == 游标类型:', type(cur).__name__)
    res['dm']['cursor_type'] = type(cur).__name__

    # ① AST 取源码真实 SQL → 直接执行
    try:
        seg = dm_sql_from_source()
        sql = eval(seg.replace('\n', ' '), {'self': _Self()})
        cur.execute(sql)
        rows = cur.fetchall()
        res['dm']['sql_from_source'] = {
            'ok': True, 'rows': len(rows),
            'sample': [str(tuple(r))[:160] for r in rows[:3]],
        }
        print('[OK ] AST 取源码 DM sql_stat SQL → 执行 %d 行' % len(rows))
    except Exception:
        res['dm']['sql_from_source'] = {'ok': False, 'error': traceback.format_exc()[-600:]}
        print('[ERR] AST SQL →', traceback.format_exc()[-300:])

    # ② 直接调平台真实方法（差分口径，首轮无增量属预期）
    try:
        from monitor.management.commands.start_monitor import Command
        cmd = Command()
        out = cmd._snapshot_sqlstat(cfg, cur)
        res['dm']['platform_method'] = {
            'ok': True, 'delta_rows': len(out),
            'note': '首轮差分无增量属预期（DeltaTracker 进程内状态）',
        }
        print('[OK ] 平台真实方法 _snapshot_sqlstat → 增量行 %d（首轮为 0 属预期）' % len(out))
    except Exception:
        res['dm']['platform_method'] = {'ok': False, 'error': traceback.format_exc()[-600:]}
        print('[ERR] 平台真实方法 →', traceback.format_exc()[-300:])

    # ③ sentinel DM 分支
    try:
        rows = sentinel.sample_sessions(cur, 'dm', config_id=cfg.id)
        res['dm']['sample_sessions'] = {
            'ok': True, 'count': len(rows),
            'first': {k: (str(v)[:70] if v is not None else None)
                      for k, v in (rows[0] if rows else {}).items()},
        }
        print('[OK ] dm.sample_sessions → %d 行' % len(rows))
    except Exception:
        res['dm']['sample_sessions'] = {'ok': False, 'error': traceback.format_exc()[-600:]}
        print('[ERR] dm.sample_sessions →', traceback.format_exc()[-300:])

    try:
        gm = sentinel.golden_metrics(cur, 'dm')
        res['dm']['golden_metrics'] = {'ok': True, 'value': gm}
        print('[OK ] dm.golden_metrics →', gm)
    except Exception:
        res['dm']['golden_metrics'] = {'ok': False, 'error': traceback.format_exc()[-600:]}
        print('[ERR] dm.golden_metrics →', traceback.format_exc()[-300:])
    conn.close()


def run_gbase(res):
    cfg = DatabaseConfig.objects.filter(db_type='gbase').order_by('id').first()
    conn = DbConnector.get_connection(cfg, statement_timeout_ms=15000, readonly=True)
    cur = conn.cursor()
    print('== GBase 连接(平台连接器) == 游标类型:', type(cur).__name__)
    res['gbase']['cursor_type'] = type(cur).__name__
    res['gbase']['instance'] = {
        'id': cfg.id, 'name': cfg.name, 'host': cfg.host, 'port': cfg.port,
    }

    # ① 逐句定位 golden_metrics 的失败点
    steps = {}
    for label, sql in (
        ('status_threads_connected', "SHOW GLOBAL STATUS LIKE 'Threads_connected'"),
        ('variables_max_connections', "SHOW VARIABLES LIKE 'max_connections'"),
        ('processlist', "SELECT id, user, host, db, command, state, time "
                        "FROM information_schema.processlist"),
    ):
        try:
            cur.execute(sql)
            row = cur.fetchone()
            steps[label] = {'ok': True, 'row': repr(row)[:200],
                            'row_type': type(row).__name__,
                            'keys': sorted(row.keys()) if isinstance(row, dict) else None}
            print('[OK ] %-26s → %r' % (label, row))
        except Exception:
            steps[label] = {'ok': False, 'error': traceback.format_exc()[-400:]}
            print('[ERR] %-26s → %s' % (label, traceback.format_exc()[-200:]))
    res['gbase']['steps'] = steps

    # ② 平台真实方法
    try:
        rows = sentinel.sample_sessions(cur, 'gbase', config_id=cfg.id)
        res['gbase']['sample_sessions'] = {
            'ok': True, 'count': len(rows),
            'note': '不抛错即达 DB-017 回归点（本机仅探针连接且空闲，故 0 行属预期；'
                    '真实会话采样见 db009_gbase_ash_live.json）',
        }
        print('[OK ] gb.sample_sessions → %d 行（不抛错）' % len(rows))
    except Exception:
        res['gbase']['sample_sessions'] = {'ok': False, 'error': traceback.format_exc()[-600:]}
        print('[ERR] gb.sample_sessions →', traceback.format_exc()[-300:])

    try:
        gm = sentinel.golden_metrics(cur, 'gbase')
        res['gbase']['golden_metrics'] = {'ok': True, 'value': gm}
        print('[OK ] gb.golden_metrics →', gm)
    except Exception:
        res['gbase']['golden_metrics'] = {'ok': False, 'error': traceback.format_exc()[-600:]}
        print('[ERR] gb.golden_metrics →', traceback.format_exc()[-300:])
    conn.close()


def main():
    res = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'dm': {}, 'gbase': {}}
    run_dm(res)
    run_gbase(res)
    with io.open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print('-> %s' % OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
