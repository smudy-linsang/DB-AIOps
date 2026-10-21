# -*- coding: utf-8 -*-
"""慢 SQL 读取侧回归（T-01 / T-02 / T-09）。见 TDSQL_SLOWSQL_REVIEW.md 与 TDSQL_SLOWSQL_DESIGN.md §4。

T-01：DictCursor 下按下标取列必抛 KeyError，被吞后恒返回空。
T-02：皮秒 ÷1e9 当秒（放大 1000 倍）；total×count 排序把最快的 SQL 排第一。
T-09：AI 诊断工具读不存在的 SlowQueryLog 模型。
"""
import contextlib
import unittest
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, TestCase, tag
from django.utils import timezone

from monitor.slow_query_engine import SlowQueryEngine

PS = 10 ** 12   # 1 秒 = 1e12 皮秒


class _Cfg:
    def __init__(self, db_type='tdsql', cid=1):
        self.db_type = db_type
        self.id = cid
        self.name = 'c'


def _ps_row(digest, text, n, avg_sec, examined=0, sent=0, no_index=0, scan=0):
    return {'digest': digest, 'schema_name': 'shop', 'digest_text': text, 'first_seen': None,
            'server_now': None, 'sample_text': None, 'count_star': n,
            'sum_timer_wait': int(n * avg_sec * PS), 'sum_lock_time': 0, 'sum_cpu_time': 0,
            'sum_rows_sent': sent, 'sum_rows_examined': examined,
            'sum_created_tmp_tables': 0, 'sum_created_tmp_disk_tables': 0,
            'sum_sort_merge_passes': 0, 'sum_sort_rows': 0, 'sum_select_scan': scan,
            'sum_select_full_join': 0, 'sum_no_index_used': no_index, 'sum_errors': 0}


class _DictCursor:
    """和 DbConnector 对 MySQL 系返回的 DictCursor 一样：行是 dict，不能按下标取。"""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, *a):
        self.sql = sql

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _DictCursor(self.rows)


# 与 TDSQL_SLOWSQL_REVIEW.md 附录的实测数据同量级
ROWS = [
    _ps_row('pk', 'SELECT `id` FROM `orders` WHERE `id` = ?', 600, 0.0001, examined=600, sent=600),
    _ps_row('scan', 'SELECT `id` , `amount` FROM `orders` WHERE `user_id` = ? ORDER BY `created_at`',
            5, 0.0907, examined=2_000_000, sent=40, no_index=5, scan=5),
    _ps_row('join', 'SELECT COUNT ( * ) FROM `orders` `a` JOIN `orders` `b` ...', 1, 6.5004,
            examined=800_000, sent=1, no_index=1, scan=1),
]


@tag('unit')
class SlowSqlLiveCumulativeTests(SimpleTestCase):

    def _engine(self, db_type='tdsql'):
        eng = SlowQueryEngine(_Cfg(db_type))
        eng.last_meta = {}
        return eng

    def _run(self, db_type='tdsql'):
        eng = self._engine(db_type)
        with mock.patch('monitor.sqlstat_query.latest_collect_data', return_value={}):
            return eng._mysql_family_cumulative(_Conn(ROWS), 10, 3600, 1000)

    def test_T01_dict_cursor_rows_are_read_without_keyerror(self):
        out = self._run()
        self.assertEqual(len(out), 3)

    def test_T02_seconds_are_real_seconds(self):
        by = {r['digest']: r for r in self._run()}
        self.assertAlmostEqual(by['join']['avg_time_sec'], 6.5004, places=3)
        self.assertAlmostEqual(by['scan']['avg_time_sec'], 0.0907, places=4)
        self.assertAlmostEqual(by['pk']['avg_time_sec'], 0.0001, places=5)
        self.assertAlmostEqual(by['scan']['avg_ms'], 90.7, places=1)

    def test_T02_fastest_sql_is_not_ranked_first(self):
        order = [r['digest'] for r in self._run()]
        self.assertEqual(order[0], 'join')
        self.assertEqual(order[-1], 'pk')

    def test_is_slow_uses_average_not_cumulative(self):
        by = {r['digest']: r for r in self._run()}
        self.assertTrue(by['join']['is_slow'])
        self.assertFalse(by['pk']['is_slow'], '高频快 SQL 的累计耗时再大也不是慢 SQL')

    def test_findings_come_from_measured_counters(self):
        by = {r['digest']: r for r in self._run()}
        codes = {f['code'] for f in by['scan']['findings']}
        self.assertIn('SCAN_INEFFICIENT', codes)
        self.assertEqual(by['pk']['findings'], [])

    def test_no_fabricated_max(self):
        for r in self._run():
            self.assertIsNone(r['max_time_sec'])

    def test_api_entry_falls_back_and_labels_basis(self):
        eng = self._engine()
        with mock.patch.object(SlowQueryEngine, '_from_sqlstat', return_value=None), \
                mock.patch('monitor.db_connector.DbConnector.get_connection',
                           return_value=_Conn(ROWS)) as gc, \
                mock.patch('monitor.db_connector.DbConnector.close_connection'), \
                mock.patch('monitor.sqlstat_query.latest_collect_data', return_value={}):
            out = eng.collect_slow_queries_from_db('1h', 10)
        self.assertEqual(len(out), 3)
        self.assertEqual(eng.last_meta['basis'], 'cumulative')
        self.assertIn('累计', eng.last_meta['warning'])
        _, kwargs = gc.call_args
        self.assertTrue(kwargs.get('readonly'), 'T-16：取目标库连接必须只读')
        self.assertTrue(kwargs.get('statement_timeout_ms'), 'T-16：取目标库连接必须带语句超时')

    def test_analysis_uses_findings_not_keyword_guessing(self):
        eng = self._engine()
        rows = self._run()
        analysis = eng.analyze_query_pattern(rows)
        types = {p['type'] for p in analysis['patterns']}
        self.assertIn('scan_inefficient', types)
        self.assertNotIn('sort_operations', types, '原实现只要含 ORDER BY 就命中"排序模式"')
        self.assertEqual(analysis['top_slow_queries'][0]['digest'], 'join')


@tag('unit')
class LlmSlowQueryToolTests(SimpleTestCase):

    def test_T09_tool_returns_engine_rows(self):
        from monitor.llm.tools import _t_slow_queries
        rows = [{'digest': 'd1', 'query': 'SELECT 1', 'exec_count': 3, 'avg_ms': 1200.0,
                 'total_time_sec': 3.6, 'is_slow': True,
                 'findings': [{'text': '每次执行平均扫描 10000 行'}], 'hottest_set': 'set_2'}]
        with mock.patch.object(SlowQueryEngine, 'collect_slow_queries_from_db', return_value=rows):
            out = _t_slow_queries(_Cfg(), {'limit': 5})
        self.assertEqual(out['slow_queries'][0]['digest'], 'd1')
        self.assertEqual(out['slow_queries'][0]['hottest_set'], 'set_2')
        self.assertNotIn('慢查询表不可用', str(out))


class _DjangoPgStore:
    """借用 Django 测试库（CI 为 PostgreSQL）当时序库：验证真实 SQL，而不是 mock 掉它。"""

    @contextlib.contextmanager
    def cursor(self):
        with connection.cursor() as c:
            yield c


@tag('integration')
@unittest.skipUnless(connection.vendor == 'postgresql', '需要 PostgreSQL')
class SqlStatWindowIntegrationTests(TestCase):
    """sql_stat 的新列 DDL（对存量表幂等）+ 窗口聚合 SQL + 慢 SQL 主路径，跑在真实 PostgreSQL 上。"""

    def setUp(self):
        from monitor.timeseries import TimeseriesStorage
        with connection.cursor() as c:
            # 存量部署的表形态：改造前的 9 列
            c.execute("""CREATE TABLE IF NOT EXISTS sql_stat (
                time TIMESTAMPTZ NOT NULL, db_config_id INTEGER NOT NULL,
                sql_digest VARCHAR(32) NOT NULL, db_name VARCHAR(100),
                exec_delta BIGINT DEFAULT 0, elapsed_ms_delta BIGINT DEFAULT 0,
                rows_delta BIGINT DEFAULT 0, reads_delta BIGINT DEFAULT 0, sql_text_sample TEXT)""")
            c.execute("INSERT INTO sql_stat (time, db_config_id, sql_digest, exec_delta, "
                      "elapsed_ms_delta) VALUES (now() - interval '5 min', 1, 'legacy', 10, 50)")
            for _ in range(2):   # 幂等
                for ddl in TimeseriesStorage._SQLSTAT_EXT_DDL:
                    c.execute(f"ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS {ddl}")
        self.storage = TimeseriesStorage.__new__(TimeseriesStorage)
        self.storage.cursor = _DjangoPgStore().cursor

    def _write(self, rows):
        with mock.patch('monitor.timeseries.timezone.now',
                        return_value=timezone.now() - timedelta(minutes=2)):
            self.assertTrue(self.storage.write_sql_stats(1, rows))

    def test_window_sum_across_sets_and_legacy_rows(self):
        base = {'db_name': 'shop', 'sql_text_sample': 'SELECT x', 'rows_delta': 1}
        self._write([dict(base, sql_digest='hot', set_name='set_1', exec_delta=100,
                          elapsed_ms_delta=500, reads_delta=1000),
                     dict(base, sql_digest='hot', set_name='set_2', exec_delta=300,
                          elapsed_ms_delta=1500, reads_delta=3000)])
        from monitor.sqlstat_query import window_by_digest, window_by_set
        td = timezone.now()
        with connection.cursor() as c:
            aggs = window_by_digest(c, 1, td - timedelta(hours=1), td)
            by_set = window_by_set(c, 1, td - timedelta(hours=1), td, ['hot'])
        self.assertEqual(aggs['hot']['exec'], 400)
        self.assertEqual(aggs['hot']['elapsed_ms'], 2000)
        self.assertEqual(aggs['hot']['set_count'], 2)
        self.assertEqual(set(by_set['hot']), {'set_1', 'set_2'})
        self.assertEqual(aggs['legacy']['lock_ms'], 0, '存量行的新列读作 0')

    def test_engine_window_path(self):
        self._write([{'sql_digest': 'slow', 'set_name': 'set_1', 'exec_delta': 2,
                      'elapsed_ms_delta': 3000, 'rows_delta': 2, 'reads_delta': 400000,
                      'scan_delta': 2, 'no_index_delta': 2, 'sql_text_sample': 'SELECT slow'}])
        cfg = _Cfg('tdsql')
        eng = SlowQueryEngine(cfg)
        with mock.patch('monitor.timeseries.get_timeseries_storage', return_value=self.storage), \
                mock.patch('monitor.sqlstat_query.latest_collect_data',
                           return_value={'tdsql_mode': 'groupshard'}):
            out = eng.collect_slow_queries_from_db('1h', 10)
        by = {r['digest']: r for r in out}
        self.assertEqual(eng.last_meta['basis'], 'window')
        self.assertIsNone(eng.last_meta['warning'], '已按 set 存储，不应报不可信')
        self.assertAlmostEqual(by['slow']['avg_time_sec'], 1.5)
        self.assertTrue(by['slow']['is_slow'])
        self.assertEqual(out[0]['digest'], 'slow')
