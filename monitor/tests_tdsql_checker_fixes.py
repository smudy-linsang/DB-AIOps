# -*- coding: utf-8 -*-
"""TDSQL 采集器与周边清理：T-03（计数器后半）/ T-10 / T-11 / T-13 / T-14 / T-15。

见 TDSQL_SLOWSQL_REVIEW.md 与 TDSQL_SLOWSQL_DESIGN.md §2.4。
"""
import re
from unittest import mock

from django.test import SimpleTestCase, tag

from monitor.checkers.tdsql import TDSQLChecker, _SUM_VARS

MYSQL8_SLOWLOG = """/usr/sbin/mysqld, Version: 8.0.46 (Ubuntu). started with:
Tcp port: 3306  Unix socket: /var/run/mysqld/mysqld.sock
Time                 Id Command    Argument
# Time: 2026-10-21T07:25:58.643118Z
# User@Host: root[root] @ localhost []  Id:    10
# Query_time: 5.225409  Lock_time: 0.000008 Rows_sent: 0  Rows_examined: 400000
use shop;
SET timestamp=1792567553;
SELECT COUNT(*) FROM orders a JOIN orders b
  ON a.user_id < b.user_id;
# Time: 2026-10-21T07:26:01.000001Z
# User@Host: app[app] @  [10.0.0.8]  Id:    11
# Query_time: 0.300000  Lock_time: 0.120000 Rows_sent: 8  Rows_examined: 400008
SET timestamp=1792567561;
SELECT id FROM orders WHERE user_id = 1;
"""

MYSQL56_SLOWLOG = """# Time: 160101 12:00:00
# User@Host: root[root] @ localhost []  Id:     3
# Query_time: 2.000000  Lock_time: 0.000000 Rows_sent: 1  Rows_examined: 100
SET timestamp=1451649600;
SELECT 1;
"""


@tag('unit')
class SlowLogParserTests(SimpleTestCase):

    def test_T11_mysql8_format(self):
        from monitor.slow_query_engine import MySQLSlowQueryParser
        recs = MySQLSlowQueryParser(MYSQL8_SLOWLOG).parse()
        self.assertEqual(len(recs), 2, '原实现对 MySQL 8 日志解析出 0 条')
        self.assertAlmostEqual(recs[0].execution_time_ms, 5225.409, places=2)
        self.assertEqual(recs[0].rows_examined, 400000)
        self.assertEqual(recs[0].db_name, 'shop')
        self.assertIn('ON a.user_id < b.user_id', recs[0].query_text, '多行 SQL 要完整保留')
        self.assertNotIn('SET timestamp', recs[0].query_text)
        self.assertAlmostEqual(recs[1].lock_time_ms, 120.0)

    def test_mysql56_format_still_works(self):
        from monitor.slow_query_engine import MySQLSlowQueryParser
        recs = MySQLSlowQueryParser(MYSQL56_SLOWLOG).parse()
        self.assertEqual([r.query_text for r in recs], ['SELECT 1;'])


class _FakeCur:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.log.append(sql)

    def fetchall(self):
        return self.rows


class _FakeConn:
    def __init__(self, rows):
        self.rows, self.log = rows, []

    def cursor(self):
        return _FakeCur(self.rows, self.log)


def _status_rows(per_set):
    rows = []
    for s, vals in per_set.items():
        for k, v in vals.items():
            rows.append({'Variable_name': k, 'Value': str(v), 'info': s})
    return rows


@tag('unit')
class GroupshardCounterTests(SimpleTestCase):

    def test_allsets_status_is_per_set_and_passthrough(self):
        conn = _FakeConn(_status_rows({'set_1': {'Questions': 100, 'Uptime': 10},
                                       'set_2': {'Questions': 300, 'Uptime': 30}}))
        out = TDSQLChecker(mock.Mock())._allsets_status(conn)
        self.assertEqual(set(out), {'set_1', 'set_2'})
        self.assertTrue(conn.log[0].startswith('/*sets:allsets*/SHOW GLOBAL STATUS'))
        self.assertIn("'Slow_queries'", conn.log[0])

    def test_no_info_column_means_passthrough_not_effective(self):
        conn = _FakeConn([{'Variable_name': 'Questions', 'Value': '5'}])
        self.assertEqual(TDSQLChecker(mock.Mock())._allsets_status(conn), {})

    def test_rates_are_summed_per_set_uptime(self):
        qps, tps = TDSQLChecker._sum_set_rates({
            'set_1': {'Questions': '100', 'Uptime': '10', 'Com_commit': '10', 'Com_rollback': '0'},
            'set_2': {'Questions': '300', 'Uptime': '100', 'Com_commit': '50', 'Com_rollback': '50'}})
        self.assertEqual((qps, tps), (13.0, 2.0))

    def test_connection_gauges_are_not_summed(self):
        self.assertNotIn('Threads_connected', _SUM_VARS,
                         '求和后再除以单个 set 的 max_connections 会让连接使用率虚高')


@tag('unit')
class CounterDeltaTests(SimpleTestCase):

    def _cmd(self):
        from monitor.management.commands.start_monitor import Command
        return Command()

    class _Cfg:
        id = 1
        name = 'c'

    def test_T15_current_rate_uses_real_interval(self):
        cmd = self._cmd()
        with mock.patch('time.time', side_effect=[1000.0, 1060.0]):
            d1 = {'questions_total': 1000, 'com_commit_total': 10, 'com_rollback_total': 0}
            cmd._compute_counter_deltas(self._Cfg, d1)
            d2 = {'questions_total': 7000, 'com_commit_total': 610, 'com_rollback_total': 0}
            cmd._compute_counter_deltas(self._Cfg, d2)
        self.assertNotIn('qps_current', d1)
        self.assertEqual(d2['qps_current'], 100.0)
        self.assertEqual(d2['tps_current'], 10.0)

    def test_T03_unrouted_groupshard_counters_produce_no_delta(self):
        cmd = self._cmd()
        for v in (10, 0):   # 两轮分别落在不同 set 的实测现象
            d = {'tdsql_mode': 'groupshard', 'tdsql_counter_basis': 'single_set',
                 'slow_queries': v, 'questions_total': v}
            cmd._compute_counter_deltas(self._Cfg, d)
            self.assertNotIn('slow_queries_delta', d)
            self.assertNotIn('qps_current', d)

    def test_per_set_summed_counters_do_produce_delta(self):
        cmd = self._cmd()
        for v in (10, 25):
            d = {'tdsql_mode': 'groupshard', 'tdsql_counter_basis': 'per_set_sum', 'slow_queries': v}
            cmd._compute_counter_deltas(self._Cfg, d)
        self.assertEqual(d['slow_queries_delta'], 15)


@tag('unit')
class CheckerHygieneTests(SimpleTestCase):

    def test_T14_lock_wait_uses_wait_started(self):
        conn = _FakeConn([{'blocker_thread': 1, 'blocked_thread': 2, 'wait_sec': 3, 'trx_age_sec': 7200}])
        out = TDSQLChecker(mock.Mock())._locks(conn)
        self.assertIn('trx_wait_started', conn.log[0])
        self.assertEqual((out[0]['seconds'], out[0]['trx_age_seconds']), (3, 7200))

    def test_T13_failures_are_traced_not_silent(self):
        class Boom:
            def cursor(self):
                raise RuntimeError('performance_schema off')
        with mock.patch('monitor.checkers.tdsql.degrade.note') as note:
            self.assertEqual(TDSQLChecker(mock.Mock())._top_sql(Boom()), [])
        self.assertEqual(note.call_args.args[0], 'tdsql.top_sql')

    def test_no_bare_silent_except_left(self):
        import inspect
        import monitor.checkers.tdsql as mod
        src = inspect.getsource(mod)
        self.assertEqual(re.findall(r'except Exception:\s*\n\s*(?:pass|return)', src), [])


@tag('unit')
class ToolCatalogTdsqlTests(SimpleTestCase):

    def test_T10_tdsql_notes_use_proxy_set_terms(self):
        from monitor.oam import tool_catalog
        for tool, by_dialect in tool_catalog._DIALECT_NOTES.items():
            text = by_dialect.get('tdsql', '')
            with self.subTest(tool=tool):
                for wrong in ('协调节点', '数据节点', 'shard_id'):
                    self.assertNotIn(wrong, text, 'TDSQL MySQL 版是 proxy 网关 + set，没有该概念')
                if 'PGXC' in text:
                    self.assertIn('没有', text, 'TDSQL MySQL 版没有 PGXC_* 视图')


@tag('unit')
class GroupshardTableMetricTests(SimpleTestCase):

    def test_top_sql_merged_across_sets(self):
        rows = [
            {'digest': 'd1', 'sql_text': 'SELECT a', 'schema_name': 's', 'exec_count': 10,
             'total_latency_sec': 1.0, 'avg_latency_sec': 0.1, 'max_latency_sec': 0.3,
             'rows_examined': 100, 'rows_sent': 10, 'errors': 0, 'no_index_used': 1, 'info': 'set_1'},
            {'digest': 'd1', 'sql_text': 'SELECT a', 'schema_name': 's', 'exec_count': 30,
             'total_latency_sec': 9.0, 'avg_latency_sec': 0.3, 'max_latency_sec': 0.9,
             'rows_examined': 300, 'rows_sent': 30, 'errors': 0, 'no_index_used': 3, 'info': 'set_2'},
        ]
        out = TDSQLChecker(mock.Mock())._top_sql(_FakeConn(rows), '/*sets:allsets*/')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['exec_count'], 40)
        self.assertAlmostEqual(out[0]['avg_latency_sec'], 0.25)
        self.assertEqual(out[0]['max_latency_sec'], 0.9)
        self.assertEqual(out[0]['set_count'], 2)

    def test_session_list_labels_set_and_keeps_sql_text(self):
        conn = _FakeConn([{'id': 7, 'user': 'app', 'host': 'h', 'db': 'shop', 'command': 'Query',
                           'time': 3, 'state': 'executing', 'sql_info': 'SELECT 1', 'info': 'set_2'}])
        out = TDSQLChecker(mock.Mock())._session_list(conn, '/*sets:allsets*/')
        self.assertEqual((out[0]['info'], out[0]['set_name']), ('SELECT 1', 'set_2'))
        self.assertTrue(conn.log[0].startswith('/*sets:allsets*/'))
