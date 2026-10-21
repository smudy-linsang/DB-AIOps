# -*- coding: utf-8 -*-
"""优化建议（sql_advisor）、耗时构成（sql_profile）与 EXPLAIN ANALYZE（sql_explain）。

见 TDSQL_SLOWSQL_DESIGN.md §5、§6；对应 TDSQL_SLOWSQL_REVIEW.md 的 T-05 / T-06 / T-07。
"""
import json
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase, override_settings, tag

from monitor import sql_explain
from monitor.sql_advisor import advise, parse, shard_meta_from_collect
from monitor.sql_profile import profile, set_distribution

# 本地 MySQL 8.0.46 上实测的 EXPLAIN ANALYZE 输出（TDSQL_SLOWSQL_REVIEW.md T-07 的同一条 SQL）
PLAN_SCAN = """-> Limit: 10 row(s)  (cost=40660 rows=10) (actual time=100..100 rows=8 loops=1)
    -> Sort: orders.created_at DESC, limit input to 10 row(s) per chunk  (cost=40660 rows=395784) (actual time=100..100 rows=8 loops=1)
        -> Filter: (orders.user_id = 4242)  (cost=40660 rows=395784) (actual time=15.8..100 rows=8 loops=1)
            -> Table scan on orders  (cost=40660 rows=395784) (actual time=0.868..83.9 rows=400000 loops=1)
"""
# 500ms 超时被掐断时的输出：不报错，根节点 rows=0、actual time 恰好卡在超时上
PLAN_TRUNCATED = r"""-> Aggregate: count(0)  (cost=21.1e+6 rows=1) (actual time=497..497 rows=0 loops=1)\n    -> Filter: (a.user_id < b.user_id)  (cost=15.8e+6 rows=52.6e+6) (actual time=1.39..410 rows=2.15e+6 loops=1)\n        -> Inner hash join (no condition)  (cost=15.8e+6 rows=52.6e+6) (actual time=1.38..167 rows=4.25e+6 loops=1)\n            -> Table scan on b  (cost=35.8 rows=395784) (actual time=0.963..4.39 rows=10642 loops=1)\n            -> Hash\n                -> Filter: (a.id < 400)  (cost=80.2 rows=399) (actual time=0.189..0.251 rows=399 loops=1)\n                    -> Index range scan on a using PRIMARY over (id < 400)  (cost=80.2 rows=399) (actual time=0.148..0.19 rows=399 loops=1)\n"""


def _codes(res):
    return [a['code'] for a in res['advice']]


def _ddls(res):
    return [a['ddl'] for a in res['advice'] if a.get('ddl')]


@tag('unit')
class AdvisorT05Tests(SimpleTestCase):
    """T-05：原 IndexAdvisor 的错误形态逐条反转。"""

    def test_join_columns_are_attributed_to_their_own_table(self):
        res = advise("SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id "
                     "WHERE u.city = 'SZ' AND o.status = 1")
        ddls = ' '.join(_ddls(res))
        self.assertNotRegex(ddls, r'`orders`[^;]*`city`', '原实现把 users.city 建到 orders 上')
        self.assertIn('ALTER TABLE `users`', ddls)
        self.assertIn('`city`', ddls)

    def test_leading_wildcard_like_gets_no_btree_index(self):
        res = advise("SELECT id FROM orders WHERE note LIKE '%abc%'")
        self.assertIn('R-02', _codes(res))
        self.assertEqual(_ddls(res), [], '前导通配 LIKE 不能推 B-tree 索引')

    def test_function_wrapped_column_gets_rewrite_hint(self):
        res = advise("SELECT id FROM orders WHERE DATE(created_at) = '2026-10-20'")
        a = next(a for a in res['advice'] if a['code'] == 'R-01')
        self.assertIn('created_at >= ? AND created_at < ?', a['detail'])

    def test_single_composite_instead_of_redundant_singles(self):
        res = advise("SELECT `id` , `amount` FROM `orders` WHERE `user_id` = ? "
                     "ORDER BY `created_at` DESC LIMIT ?")
        i01 = [a for a in res['advice'] if a['code'] == 'I-01']
        self.assertEqual(len(i01), 1)
        self.assertEqual(i01[0]['evidence']['columns'], ['user_id', 'created_at'])

    def test_covering_index_does_not_repeat_primary_key(self):
        res = advise("SELECT `id` , `amount` FROM `orders` WHERE `user_id` = ? "
                     "ORDER BY `created_at` DESC LIMIT ?")
        i02 = next(a for a in res['advice'] if a['code'] == 'I-02')
        self.assertEqual(i02['evidence']['columns'], ['user_id', 'created_at', 'amount'])

    def test_existing_index_suppresses_duplicate_advice(self):
        res = advise("SELECT id FROM orders WHERE user_id = 1 ORDER BY created_at DESC LIMIT 10",
                     existing_indexes={'orders': [{'name': 'idx_u_c',
                                                   'columns': ['user_id', 'created_at', 'x']}]})
        self.assertNotIn('I-01', _codes(res))
        self.assertIn('I-03', _codes(res))

    def test_efficient_sql_gets_no_index_advice(self):
        prof = profile({'exec': 100, 'elapsed_ms': 10, 'rows_examined': 100, 'rows_sent': 100})
        res = advise("SELECT id FROM orders WHERE id = 1", profile=prof)
        self.assertEqual(_ddls(res), [])
        self.assertIn('I-00', _codes(res))

    def test_unqualified_columns_in_join_are_not_guessed(self):
        res = advise("SELECT a.id FROM t1 a JOIN t2 b ON a.k = b.k WHERE flag = 1")
        self.assertIn('I-09', _codes(res))
        self.assertFalse(any('`flag`' in d for d in _ddls(res)))

    def test_range_after_sort_only_with_limit(self):
        res = advise("SELECT id FROM orders WHERE user_id = 1 AND created_at > ? "
                     "ORDER BY created_at DESC LIMIT 20")
        i01 = next(a for a in res['advice'] if a['code'] == 'I-01')
        self.assertEqual(i01['evidence']['columns'], ['user_id', 'created_at'])

    def test_interval_arithmetic_is_not_mistaken_for_a_column(self):
        p = parse("SELECT status FROM orders WHERE created_at >= ? - INTERVAL 1 DAY")
        self.assertEqual([(x['col'], x['kind']) for x in p.preds], [('created_at', 'range')])

    def test_rewrite_rules(self):
        res = advise("SELECT * FROM orders WHERE user_id = 5 OR status = 2 LIMIT 100000, 20")
        self.assertTrue({'R-03', 'R-04', 'R-06'} <= set(_codes(res)))

    def test_digest_text_with_backticks_parses(self):
        p = parse("SELECT `o` . `id` FROM `shop` . `orders` `o` WHERE `o` . `user_id` IN ( ... ) ")
        self.assertEqual(p.tables[0]['name'], 'orders')
        self.assertEqual(p.tables[0]['alias'], 'o')


SHARD = {'orders': {'type': 'shard', 'shardkey': 'user_id', 'db': 'shop'},
         'users': {'type': 'shard', 'shardkey': 'id', 'db': 'shop'},
         'region': {'type': 'broadcast', 'shardkey': None, 'db': 'shop'}}


def _tdsql(sql, **kw):
    return advise(sql, db_type='tdsql', tdsql_mode='groupshard', shard_meta=SHARD, set_count=4, **kw)


@tag('unit')
class AdvisorTdsqlTests(SimpleTestCase):
    """T-06：分布式规则（依据官方文档：不带 shardkey 路由到所有节点；分表间按分表键相等 JOIN）。"""

    def test_D01_broadcast_without_shardkey(self):
        a = next(a for a in _tdsql("SELECT id FROM orders WHERE status = 1")['advice']
                 if a['code'] == 'D-01')
        self.assertIn('4 个 set', a['title'])

    def test_routed_query_has_no_distributed_advice(self):
        self.assertFalse([c for c in _codes(_tdsql("SELECT id FROM orders WHERE user_id = 42"))
                          if c.startswith('D')])

    def test_shardkey_routing_propagates_through_join(self):
        """同 shardkey 值必在同一 set：users.id = orders.user_id 且 orders.user_id 已定值 → 不广播。"""
        res = _tdsql("SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id WHERE o.user_id = 42")
        self.assertFalse([c for c in _codes(res) if c.startswith('D')])

    def test_D02_join_not_on_shardkeys(self):
        self.assertIn('D-02', _codes(_tdsql(
            "SELECT o.id FROM orders o JOIN users u ON u.city = o.note WHERE o.user_id = 42")))

    def test_D03_shard_joined_with_single_table(self):
        self.assertIn('D-03', _codes(_tdsql(
            "SELECT o.id FROM orders o JOIN audit_log l ON l.oid = o.id WHERE o.user_id = 1")))

    def test_broadcast_table_join_is_fine(self):
        res = _tdsql("SELECT o.id FROM orders o JOIN region r ON r.id = o.region_id WHERE o.user_id = 1")
        self.assertNotIn('D-03', _codes(res))

    def test_D04_cross_set_order_limit(self):
        a = next(a for a in _tdsql("SELECT * FROM orders WHERE status = 1 "
                                   "ORDER BY created_at DESC LIMIT 2000, 20")['advice']
                 if a['code'] == 'D-04')
        self.assertEqual(a['severity'], 'high')

    def test_D05_cross_set_count_distinct(self):
        self.assertIn('D-05', _codes(_tdsql("SELECT COUNT(DISTINCT user_id) FROM orders WHERE status = 1")))

    def test_D06_dml_without_shardkey(self):
        self.assertIn('D-06', _codes(_tdsql("UPDATE orders SET status = 2 WHERE created_at < '2026-01-01'")))

    def test_D07_shardkey_wrapped_in_function(self):
        self.assertIn('D-07', _codes(_tdsql("SELECT id FROM orders WHERE MOD(user_id, 10) = 3")))

    def test_D08_skew_from_set_distribution(self):
        dist = set_distribution({
            'set_1': {'exec': 10, 'elapsed_ms': 100}, 'set_2': {'exec': 10, 'elapsed_ms': 100},
            'set_3': {'exec': 10, 'elapsed_ms': 2000}, 'set_4': {'exec': 10, 'elapsed_ms': 100}})
        self.assertTrue(dist['is_skewed'])
        self.assertIn('D-08', _codes(_tdsql("SELECT id FROM orders WHERE user_id = 1",
                                            distribution=dist)))

    def test_noshard_instance_gets_no_distributed_rules(self):
        res = advise("SELECT id FROM orders WHERE status = 1", db_type='tdsql',
                     tdsql_mode='noshard', shard_meta=SHARD)
        self.assertFalse([c for c in _codes(res) if c.startswith('D')])

    def test_shard_meta_from_collector_output(self):
        meta = shard_meta_from_collect({'tdsql_shard_tables': [
            {'db_table': 'shop.orders', 'shardkey': 'user_id'},
            {'db_table': 'shop.region', 'shardkey': 'noshardkey_allset'}]})
        self.assertEqual(meta['orders'], {'type': 'shard', 'shardkey': 'user_id', 'db': 'shop'})
        self.assertEqual(meta['region']['type'], 'broadcast')


@tag('unit')
class ProfileTests(SimpleTestCase):

    def test_breakdown_and_findings(self):
        p = profile({'exec': 4, 'elapsed_ms': 400, 'lock_ms': 200, 'cpu_ms': 80,
                     'rows_examined': 1_600_000, 'rows_sent': 32, 'scan': 4, 'no_index': 4,
                     'tmp_tables': 4, 'tmp_disk': 2, 'sort_merge': 8})
        self.assertEqual(p['avg_ms'], 100)
        self.assertEqual(p['breakdown'], {'lock_ms': 50, 'cpu_ms': 20, 'other_ms': 30,
                                          'cpu_known': True})
        codes = {f['code'] for f in p['findings']}
        self.assertTrue({'SCAN_INEFFICIENT', 'FULL_SCAN', 'TMP_DISK', 'SORT_MERGE', 'LOCK_WAIT'} <= codes)

    def test_cpu_zero_means_not_collected(self):
        p = profile({'exec': 1, 'elapsed_ms': 100, 'cpu_ms': 0})
        self.assertFalse(p['breakdown']['cpu_known'])
        self.assertIsNone(p['breakdown']['other_ms'], 'CPU 未采集时不能把全部时间算作"其他"')
        self.assertIn('events_statements_cpu', p['breakdown']['cpu_hint'])

    def test_non_mysql_family_does_not_fake_scan_rows(self):
        p = profile({'exec': 2, 'elapsed_ms': 10, 'rows_examined': 999, 'rows_sent': 2},
                    mysql_family=False)
        self.assertIsNone(p['per_exec']['rows_examined'])
        self.assertEqual(p['per_exec']['logical_reads'], 499.5)

    def test_set_distribution_single_set_has_no_skew(self):
        d = set_distribution({'': {'exec': 3, 'elapsed_ms': 30}})
        self.assertIsNone(d['skew'])


@tag('unit')
class ExplainParseAndGuardTests(SimpleTestCase):

    def test_parse_real_tree_and_self_time(self):
        tree = sql_explain.parse_tree(PLAN_SCAN)
        s = sql_explain.summarize(tree, timeout_ms=5000)
        self.assertEqual(s['hotspots'][0]['op'], 'Table scan on orders')
        self.assertAlmostEqual(s['hotspots'][0]['self_ms'], 83.9, places=1)
        self.assertAlmostEqual(s['total_ms'], 100.0)
        self.assertFalse(s['truncated'])

    def test_truncated_plan_is_flagged(self):
        s = sql_explain.summarize(sql_explain.parse_tree(PLAN_TRUNCATED), timeout_ms=500)
        self.assertTrue(s['truncated'])
        self.assertIn('截断', s['truncated_note'])

    def test_guard(self):
        bad = ["UPDATE orders SET s=1", "SELECT * FROM t FOR UPDATE", "SELECT SLEEP(10)",
               "SELECT 1; DROP TABLE x", "SELECT * FROM t WHERE id = ?",
               "WITH c AS (SELECT 1) DELETE FROM t", "SELECT * INTO OUTFILE '/tmp/x' FROM t",
               "SELECT * FROM t LOCK IN SHARE MODE", "INSERT INTO t SELECT * FROM s"]
        for sql in bad:
            with self.subTest(sql=sql):
                self.assertFalse(sql_explain.guard_select(sql)[0])
        for sql in ["SELECT 'it''s; update' AS s FROM t", "/* c */ SELECT a FROM t WHERE b = 'x?' ;",
                    "WITH c AS (SELECT 1 AS x) SELECT x FROM c"]:
            with self.subTest(sql=sql):
                self.assertTrue(sql_explain.guard_select(sql)[0])

    def test_time_hint_injected_for_select_only(self):
        self.assertIn('MAX_EXECUTION_TIME(500)', sql_explain.with_time_hint('SELECT 1', 500))
        self.assertNotIn('MAX_EXECUTION_TIME', sql_explain.with_time_hint('WITH c AS (SELECT 1) SELECT 1', 500))


class _RecCursor:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def execute(self, sql, *a):
        self.log.append(sql)

    def fetchall(self):
        return self.rows if 'EXPLAIN ANALYZE' in self.log[-1] else []

    def close(self):
        pass


class _RecConn:
    def __init__(self, rows):
        self.log, self.rows = [], rows

    def cursor(self):
        return _RecCursor(self.log, self.rows)


class _Cfg:
    db_type = 'tdsql'
    id = 1


@tag('unit')
class ExplainRunTests(SimpleTestCase):

    def _run(self, set_name='set_3', rows=None):
        conn = _RecConn(rows or [{'EXPLAIN': PLAN_SCAN, 'info': 'set_3'}])
        with mock.patch('monitor.db_connector.DbConnector.get_connection', return_value=conn) as gc, \
                mock.patch('monitor.db_connector.DbConnector.close_connection'):
            out = sql_explain.run(_Cfg(), "SELECT id FROM orders WHERE user_id = 4242 LIMIT 10",
                                  set_name=set_name, timeout_ms=3000)
        return out, conn.log, gc

    def test_read_only_transaction_and_single_set_passthrough(self):
        out, log, gc = self._run()
        self.assertEqual(log[0], '/*sets:set_3*/START TRANSACTION READ ONLY')
        self.assertTrue(log[1].startswith('/*sets:set_3*/EXPLAIN ANALYZE SELECT /*+ MAX_EXECUTION_TIME(3000) */'))
        self.assertEqual(log[-1], '/*sets:set_3*/ROLLBACK')
        self.assertEqual(gc.call_args.kwargs, {'statement_timeout_ms': 3000, 'readonly': True})
        self.assertEqual(out['summary']['hotspots'][0]['op'], 'Table scan on orders')

    def test_bad_set_name_refused(self):
        with self.assertRaises(sql_explain.ExplainRefused):
            sql_explain.run(_Cfg(), "SELECT 1", set_name="x*/ DROP TABLE t; /*")

    def test_refused_sql_never_reaches_target(self):
        with mock.patch('monitor.db_connector.DbConnector.get_connection') as gc:
            with self.assertRaises(sql_explain.ExplainRefused):
                sql_explain.run(_Cfg(), "DELETE FROM orders")
        gc.assert_not_called()


@tag('unit')
class ExplainAnalyzeViewGateTests(TestCase):
    """权限闸与数据范围闸分开测（AGENTS.md）：范围用"有权限但范围受限"的账号。"""

    def setUp(self):
        from monitor.auth import Perm
        from monitor.tests_bugfix import login, make_db, make_user
        self.mine = make_db('ea-mine', 'tdsql')
        self.other = make_db('ea-other', 'tdsql')
        self.executor = make_user('ea-exec', 'ea_exec_role', [Perm.TICKETS_EXECUTE],
                                  allowed_dbs=[self.mine.id])
        self.viewer = make_user('ea-view', 'ea_view_role', [Perm.SQL_MONITORING_VIEW],
                                allowed_dbs=[self.mine.id])
        self.c_exec, self.c_view = Client(), Client()
        login(self.c_exec, self.executor)
        login(self.c_view, self.viewer)

    def _post(self, client, cfg, body=None):
        return client.post(f'/api/v1/databases/{cfg.id}/perf/sql/{"a" * 32}/explain-analyze/',
                           data=json.dumps(body or {}), content_type='application/json')

    def test_disabled_by_default(self):
        r = self._post(self.c_exec, self.mine)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['code'], 'DISABLED')

    @override_settings(PERF_EXPLAIN_ANALYZE_ENABLED=True)
    def test_view_permission_is_not_enough(self):
        r = self._post(self.c_view, self.mine)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['code'], 'FORBIDDEN')

    @override_settings(PERF_EXPLAIN_ANALYZE_ENABLED=True)
    def test_out_of_scope_instance_is_404(self):
        with mock.patch('monitor.sql_explain.run') as run:
            r = self._post(self.c_exec, self.other)
        self.assertEqual(r.status_code, 404)
        run.assert_not_called()

    @override_settings(PERF_EXPLAIN_ANALYZE_ENABLED=True)
    def test_success_is_audited(self):
        from monitor.models import AuditLog
        with mock.patch('monitor.api_views_perf._raw_sql_text', return_value=('SELECT 1', None)), \
                mock.patch.object(__import__('monitor.api_views_perf', fromlist=['x']).SqlExplainAnalyzeView,
                                  '_hottest_set', return_value='set_2'), \
                mock.patch('monitor.sql_explain.run', return_value={'summary': {}, 'set_name': 'set_2'}) as run:
            r = self._post(self.c_exec, self.mine)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(run.call_args.kwargs['set_name'], 'set_2')
        log = AuditLog.objects.get(config=self.mine, action_type='EXECUTE_SQL')
        self.assertEqual(log.status, 'success')

    @override_settings(PERF_EXPLAIN_ANALYZE_ENABLED=True)
    def test_mismatched_user_sql_rejected(self):
        r = self._post(self.c_exec, self.mine, {'sql_text': 'SELECT * FROM somewhere_else'})
        self.assertEqual(r.status_code, 400)
