# -*- coding: utf-8 -*-
"""SQL 环比与周期 SQL（monitor/sql_compare.py + /perf/sql-regression/ + /perf/sql-periodic/）。

见 TDSQL_SLOWSQL_DESIGN.md §7；修 TDSQL_SLOWSQL_REVIEW.md T-08。
"""
import contextlib
import unittest
from datetime import datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, tag
from django.utils import timezone

from monitor.sql_compare import baseline_window, classify, detect_daily, summarize_counts

UTC = dt_tz.utc


def _agg(exec_n, total_ms, rows=0, lock=0):
    return {'exec': exec_n, 'elapsed_ms': total_ms, 'rows_examined': rows, 'lock_ms': lock}


@tag('unit')
class ClassifyTests(SimpleTestCase):

    def test_statuses(self):
        cur = {'reg': _agg(100, 5000), 'new': _agg(1, 10), 'imp': _agg(100, 100),
               'stable': _agg(100, 110), 'burst': _agg(1000, 5000)}
        base = {'reg': _agg(100, 1000), 'gone': _agg(5, 50), 'imp': _agg(100, 1000),
                'stable': _agg(100, 100), 'burst': _agg(100, 500)}
        by = {i['digest']: i for i in classify(cur, base)}
        self.assertEqual(by['reg']['status'], 'regressed')
        self.assertEqual(by['reg']['avg_ratio'], 5.0)
        self.assertEqual(by['new']['status'], 'new')
        self.assertEqual(by['gone']['status'], 'gone')
        self.assertEqual(by['imp']['status'], 'improved')
        self.assertEqual(by['stable']['status'], 'stable')
        self.assertEqual(by['burst']['status'], 'regressed', '平均耗时不变但执行量暴涨、总耗时翻倍也算退化')

    def test_sub_millisecond_jitter_is_not_regression(self):
        by = {i['digest']: i for i in classify({'pk': _agg(1000, 200)}, {'pk': _agg(1000, 100)})}
        self.assertEqual(by['pk']['status'], 'stable', '0.1ms→0.2ms 虽然翻倍，但差值低于 5ms 阈值')

    def test_regressions_sorted_first_by_impact(self):
        items = classify({'a': _agg(10, 1000), 'b': _agg(10, 9000), 'c': _agg(10, 10)},
                         {'a': _agg(10, 100), 'b': _agg(10, 100), 'c': _agg(10, 10)})
        self.assertEqual([i['digest'] for i in items], ['b', 'a', 'c'])
        self.assertEqual(summarize_counts(items)['regressed'], 2)

    def test_baseline_windows(self):
        fd = datetime(2026, 10, 21, 9, tzinfo=UTC)
        td = datetime(2026, 10, 21, 10, tzinfo=UTC)
        self.assertEqual(baseline_window(fd, td, 'prev'), (fd - timedelta(hours=1), fd))
        self.assertEqual(baseline_window(fd, td, 'day')[0], fd - timedelta(days=1))
        self.assertEqual(baseline_window(fd, td, 'week')[1], td - timedelta(days=7))
        with self.assertRaises(ValueError):
            baseline_window(fd, td, 'month')


def _daily(days, hours, exec_per_hour, ms_per_exec, last_day_ms=None, noise=None):
    start = datetime(2026, 10, 14, tzinfo=UTC)
    pts = []
    for d in range(days):
        for h in hours:
            ms = (last_day_ms if (last_day_ms and d == days - 1) else ms_per_exec)
            t = start + timedelta(days=d, hours=h)
            pts.append((t, exec_per_hour, exec_per_hour * ms))
        for h in (noise or []):
            pts.append((start + timedelta(days=d, hours=h), 1, 1))
    return pts


@tag('unit')
class PeriodicTests(SimpleTestCase):

    def test_nightly_batch_regression_is_quantified(self):
        r = detect_daily(_daily(7, [2], 50, 100, last_day_ms=320), days=7, tz=UTC)
        self.assertEqual(r['hours'], [2])
        self.assertEqual(r['days_seen'], 7)
        self.assertEqual(r['median_avg_ms'], 100)
        self.assertEqual(r['latest_vs_median'], 3.2)

    def test_run_crossing_midnight_stays_one_run(self):
        r = detect_daily(_daily(7, [23, 24, 25], 10, 50), days=7, tz=UTC)
        self.assertEqual(r['hours'], [23, 0, 1])
        self.assertEqual(r['runs'][1]['exec'], 30, '23 点到 1 点应算同一次运行')

    def test_continuous_sql_is_not_periodic(self):
        self.assertIsNone(detect_daily(_daily(7, list(range(24)), 10, 5), days=7, tz=UTC))

    def test_rare_sql_is_not_periodic(self):
        self.assertIsNone(detect_daily(_daily(2, [2], 50, 100), days=7, tz=UTC))

    def test_small_noise_does_not_break_detection(self):
        r = detect_daily(_daily(7, [2], 100, 10, noise=[10, 15]), days=7, tz=UTC)
        self.assertEqual(r['hours'], [2])


class _DjangoPgStore:
    @contextlib.contextmanager
    def cursor(self):
        with connection.cursor() as c:
            yield c


@tag('integration')
@unittest.skipUnless(connection.vendor == 'postgresql', '需要 PostgreSQL')
class RegressionAndPeriodicApiTests(TestCase):
    """接口跑在真实 PostgreSQL 上（time_bucket 用 date_bin 垫片，TimescaleDB 生产环境自带）。"""

    def setUp(self):
        from monitor.auth import Perm
        from monitor.tests_bugfix import login, make_db, make_user
        from monitor.timeseries import TimeseriesStorage
        with connection.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS sql_stat (
                time TIMESTAMPTZ NOT NULL, db_config_id INTEGER NOT NULL,
                sql_digest VARCHAR(32) NOT NULL, db_name VARCHAR(100),
                exec_delta BIGINT DEFAULT 0, elapsed_ms_delta BIGINT DEFAULT 0,
                rows_delta BIGINT DEFAULT 0, reads_delta BIGINT DEFAULT 0, sql_text_sample TEXT)""")
            for ddl in TimeseriesStorage._SQLSTAT_EXT_DDL:
                c.execute(f"ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS {ddl}")
            c.execute("""CREATE OR REPLACE FUNCTION time_bucket(i interval, t timestamptz)
                         RETURNS timestamptz LANGUAGE sql IMMUTABLE AS
                         $$ SELECT date_bin(i, t, TIMESTAMPTZ '2000-01-01') $$""")
        self.cfg = make_db('cmp-mine', 'tdsql')
        self.other = make_db('cmp-other', 'tdsql')
        user = make_user('cmp-user', 'cmp_role', [Perm.SQL_MONITORING_VIEW], allowed_dbs=[self.cfg.id])
        self.client = Client()
        login(self.client, user)
        self.patch = mock.patch('monitor.timeseries.get_timeseries_storage',
                                return_value=_DjangoPgStore())
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def _ins(self, when, digest, exec_n, ms, set_name='set_1'):
        with connection.cursor() as c:
            c.execute("INSERT INTO sql_stat (time, db_config_id, sql_digest, set_name, exec_delta, "
                      "elapsed_ms_delta, sql_text_sample) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                      (when, self.cfg.id, digest, set_name, exec_n, ms, f'SELECT {digest}'))

    def test_regression_against_yesterday(self):
        now = timezone.now()
        for d, cur_ms, base_ms in (('slowdown', 5000, 1000), ('steady', 100, 100)):
            self._ins(now - timedelta(minutes=20), d, 10, cur_ms)
            self._ins(now - timedelta(days=1, minutes=20), d, 10, base_ms)
        self._ins(now - timedelta(minutes=10), 'brand_new', 3, 30)
        # 只在基线窗口出现过、但不在其 Top N 里的情况由 digests_present 兜底；这里覆盖常规路径
        r = self.client.get(f'/api/v1/databases/{self.cfg.id}/perf/sql-regression/?window=1h&baseline=day')
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()['data']
        by = {i['digest']: i for i in data['items']}
        self.assertEqual(by['slowdown']['status'], 'regressed')
        self.assertEqual(by['slowdown']['avg_ratio'], 5.0)
        self.assertEqual(by['brand_new']['status'], 'new')
        self.assertNotIn('steady', by, '稳定项不返回')
        self.assertEqual(data['counts']['stable'], 1)
        self.assertEqual(data['baseline']['kind'], 'day')
        self.assertIsNone(data['baseline_note'])

    def test_regression_empty_baseline_is_flagged(self):
        self._ins(timezone.now() - timedelta(minutes=10), 'only_now', 3, 30)
        r = self.client.get(f'/api/v1/databases/{self.cfg.id}/perf/sql-regression/?window=1h&baseline=week')
        data = r.json()['data']
        self.assertEqual(data['counts']['new'], 1)
        self.assertIn('新增', data['baseline_note'])

    def test_regression_scope(self):
        r = self.client.get(f'/api/v1/databases/{self.other.id}/perf/sql-regression/')
        self.assertEqual(r.status_code, 404)

    def test_periodic(self):
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        for d in range(7, 0, -1):
            day = now - timedelta(days=d)
            self._ins(day.replace(hour=2) + timedelta(minutes=5), 'nightly', 50,
                      50 * (320 if d == 1 else 100))
        r = self.client.get(f'/api/v1/databases/{self.cfg.id}/perf/sql-periodic/?days=7')
        self.assertEqual(r.status_code, 200, r.content)
        items = {i['digest']: i for i in r.json()['data']['items']}
        self.assertIn('nightly', items)
        self.assertEqual(items['nightly']['latest_vs_median'], 3.2)
