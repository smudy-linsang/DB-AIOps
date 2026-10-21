# -*- coding: utf-8 -*-
"""TDSQL 慢 SQL 专项评估 —— 可重跑的复现脚本（对应 TDSQL_SLOWSQL_REVIEW.md）。

放在 docs/ 下而不是 monitor/tests*：这些是**缺陷复现**，不是回归用例。
按仓库规矩，新增缺陷不得以 expectedFailure 换绿灯 —— 修复时请把对应断言
改写成正向回归用例并入 monitor/ 的测试，再删掉这里的条目。

前置：
  1. 一个 MySQL 8.0 / TXSQL 8.0 实例（不要用生产库），执行 workload.sql
  2. 连接参数走环境变量（遵守凭据通道约定，脚本里不出现明文）：
       PROBE_MYSQL_HOST / PROBE_MYSQL_PORT / PROBE_MYSQL_USER / PROBE_MYSQL_PASSWORD
     账号只需 SELECT + PROCESS 权限
  3. 在仓库根目录用项目虚拟环境运行：
       .venv/bin/python docs/review/tdsql/repro_slowsql.py all

子命令：api | units | advisor | groupshard | slowlog | llmtool | ash | all
"""
import dataclasses
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings_test_unit')

import django  # noqa: E402
django.setup()

import pymysql  # noqa: E402


def _conn_kwargs():
    missing = [k for k in ('PROBE_MYSQL_HOST', 'PROBE_MYSQL_USER', 'PROBE_MYSQL_PASSWORD')
               if not os.environ.get(k)]
    if missing:
        sys.exit(f'缺少环境变量: {", ".join(missing)}')
    return dict(host=os.environ['PROBE_MYSQL_HOST'],
                port=int(os.environ.get('PROBE_MYSQL_PORT', '3306')),
                user=os.environ['PROBE_MYSQL_USER'],
                password=os.environ['PROBE_MYSQL_PASSWORD'],
                database='shop')


def _pk_lookups(n=300):
    conn = pymysql.connect(**_conn_kwargs())
    with conn.cursor() as c:
        for i in range(n):
            c.execute('SELECT id FROM orders WHERE id = %s', (i * 1300 % 400000 + 1,))
            c.fetchall()
    conn.close()


def t01_api():
    """T-01：慢 SQL 接口 / 慢 SQL 告警对 tdsql 恒为空。"""
    from django.test.runner import DiscoverRunner
    runner = DiscoverRunner(verbosity=0)
    old = runner.setup_databases()
    try:
        from monitor.db_connector import DbConnector
        from monitor.models import DatabaseConfig
        from monitor.slow_query_engine import SlowQueryEngine
        kw = _conn_kwargs()
        cfg = DatabaseConfig(name='repro-tdsql', db_type='tdsql', host=kw['host'],
                             port=kw['port'], username=kw['user'], service_name='shop')
        cfg.set_password(kw['password'])
        cfg.save()
        eng = SlowQueryEngine(cfg)
        conn = DbConnector.get_connection(cfg)
        try:
            eng.collect_slow_queries(conn)
            print('[T-01] collect_slow_queries 未抛异常（缺陷可能已修复）')
        except KeyError as e:
            print(f'[T-01] collect_slow_queries 抛 KeyError({e})：DictCursor 下按下标取列')
        finally:
            conn.close()
        res = eng.collect_slow_queries_from_db(time_range='1h', limit=50)
        verdict = eng.analyze_query_pattern(res)['optimization_suggestions']
        print(f'[T-01] API 入口返回 {len(res)} 条；分析结论 = {verdict}')
    finally:
        runner.teardown_databases(old)


def t02_units():
    """T-02：时间单位放大 1000 倍 + 排序把最快的 SQL 排第一。"""
    from monitor.slow_query_engine import SlowQueryEngine
    _pk_lookups()
    conn = pymysql.connect(**_conn_kwargs())   # 元组游标：绕过 T-01 才看得到 T-02

    class _Cfg:
        db_type = 'tdsql'
    eng = SlowQueryEngine.__new__(SlowQueryEngine)
    eng.config = _Cfg()
    rows = [r for r in eng._collect_mysql_slow_queries(conn) if 'orders' in (r['query'] or '')]
    with conn.cursor() as c:
        c.execute("SELECT DIGEST_TEXT, AVG_TIMER_WAIT/1e12 FROM "
                  "performance_schema.events_statements_summary_by_digest "
                  "WHERE DIGEST_TEXT LIKE '%orders%'")
        truth = {t: float(a) for t, a in c.fetchall()}
    for r in rows:
        print(f"[T-02] 引擎 avg_time_sec={r['avg_time_sec']:>10.4f}  "
              f"真值 avg_sec={truth.get(r['query'], float('nan')):>8.4f}  {r['query'][:50]}")
    top = sorted(rows, key=lambda x: x['total_time_sec'] * x['exec_count'], reverse=True)
    print(f"[T-02] 引擎排第一的是: n={top[0]['exec_count']} {top[0]['query'][:50]}")
    conn.close()


def t05_advisor():
    """T-05：索引建议的错误形态。"""
    from monitor.index_advisor import IndexAdvisor
    from monitor.sqlfingerprint import normalize
    cases = [
        ('JOIN 列归错表', "SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id "
                          "WHERE u.city = 'SZ' AND o.status = 1"),
        ('前导通配 LIKE', "SELECT id FROM orders WHERE note LIKE '%abc%'"),
        ('函数包列', "SELECT id FROM orders WHERE DATE(created_at) = '2026-10-20'"),
    ]
    adv = IndexAdvisor()
    for name, sql in cases:
        norm = normalize(sql).replace('`', '')        # 与 SqlDetailView 完全相同的预处理
        out = adv.analyze_queries([{'query': norm, 'exec_count': 10}]) or []
        got = []
        for c in out[:3]:
            d = dataclasses.asdict(c)
            got.append(f"{d['table']}({', '.join(col['name'] for col in d['columns'])})")
        print(f'[T-05] {name}: {got or "（无建议、无提示）"}')


def t03_groupshard():
    """T-03：groupshard 下 proxy 每轮把 digest 查询路由到不同 set，增量被污染。"""
    from monitor.management.commands.start_monitor import Command
    digest = 'a' * 32

    class _Cur:
        def __init__(self, row):
            self.row = row

        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return [self.row]

    class _Cfg:
        id = 1
        db_type = 'tdsql'
    sets = {'set_1': [1000, 5000], 'set_2': [3000, 15000],
            'set_3': [500, 2500], 'set_4': [8000, 40000]}
    route = ['set_1', 'set_3', 'set_2', 'set_2', 'set_4', 'set_1']
    cmd = Command()
    print('[T-03] 真值：每轮全集群 exec_delta=400')
    for i, s in enumerate(route):
        for k in sets:
            sets[k][0] += 100
            sets[k][1] += 500
        row = {'DIGEST': digest, 'SCHEMA_NAME': 'shop', 'COUNT_STAR': sets[s][0],
               'SUM_TIMER_WAIT': sets[s][1] * 10 ** 9, 'SUM_ROWS_SENT': 0,
               'SUM_ROWS_EXAMINED': 0, 'txt': 'SELECT ...'}
        out = cmd._snapshot_sqlstat(_Cfg, _Cur(row))
        print(f'[T-03] 第{i}轮 路由到 {s}: '
              + (f"exec_delta={out[0]['exec_delta']}" if out else '（本轮无数据写入）'))


def t11_slowlog(path):
    """T-11：慢日志解析器与 MySQL 8 格式不匹配。"""
    from monitor.slow_query_engine import MySQLSlowQueryParser
    text = open(path, encoding='utf-8', errors='replace').read()
    print(f"[T-11] 日志条目 {text.count('# Query_time:')}，平台解析出 "
          f"{len(MySQLSlowQueryParser(text).parse())}")


def t09_llmtool():
    """T-09：AI 诊断的 slow_queries 工具引用不存在的模型。"""
    from monitor.llm.tools import _t_slow_queries
    from monitor.models import DatabaseConfig
    print(f"[T-09] {_t_slow_queries(DatabaseConfig(id=1, db_type='tdsql'), {'limit': 5})}")


def t07_ash():
    """T-07：在另一个会话里跑一条数秒的查询，再执行本子命令。"""
    from monitor.sentinel import _ash_mysql_family
    kw = _conn_kwargs()
    conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **kw)
    with conn.cursor() as cur:
        for r in _ash_mysql_family(cur, 'tdsql'):
            print(f"[T-07] state={r['state']!r} -> wait_class={r['wait_class']!r} "
                  f"sql={(r['sql_text'] or '')[:50]!r}")
    conn.close()


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    table = {'api': t01_api, 'units': t02_units, 'advisor': t05_advisor,
             'groupshard': t03_groupshard, 'llmtool': t09_llmtool, 'ash': t07_ash}
    if cmd == 'slowlog':
        t11_slowlog(sys.argv[2])
    elif cmd == 'all':
        for name in ('api', 'units', 'advisor', 'groupshard', 'llmtool'):
            table[name]()
    else:
        table[cmd]()
