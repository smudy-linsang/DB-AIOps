# -*- coding: utf-8 -*-
"""TDSQL 按 set 采集与差分（monitor/sqlstat_collect.py）。见 TDSQL_SLOWSQL_DESIGN.md §2。

本环境没有真实 TDSQL，这里用一个**按官方文档行为建模的假 proxy 游标**：
- ``/*sets:allsets*/<SQL>``：在每个 set 上执行，结果集多一列 ``info`` = set 名
- ``/*sets:<name>*/<SQL>``：只在该 set 上执行
- 不带透传：proxy 自行路由到某一个 set（可配置路由序列，复现 T-03）
- 可配置为拒绝透传、或像原生 MySQL 一样把注释当普通注释忽略
内网真实 TDSQL 上的对应验证项见设计文档 §8（V-1 ~ V-5）。
"""
import datetime as _dt
import re

from django.test import SimpleTestCase, tag

from monitor import sqlstat_collect
from monitor.sqlstat_collect import BASIS_PER_SET, BASIS_SINGLE, BASIS_UNROUTED, DeltaTracker

T0 = _dt.datetime(2026, 10, 21, 10, 0, 0)


class _Cfg:
    def __init__(self, cid=1, db_type='tdsql'):
        self.id = cid
        self.db_type = db_type
        self.name = f'cfg{cid}'


def _digest_row(digest, c, first_seen=T0, server_now=T0):
    row = {'digest': digest, 'schema_name': 'shop', 'digest_text': f'SELECT ... /*{digest}*/',
           'first_seen': first_seen, 'server_now': server_now,
           'sample_text': f"SELECT * FROM t WHERE k = 1 /*{digest}*/"}
    for out, col in sqlstat_collect._COUNTERS:
        row[col.lower()] = c.get(out, 0)
    return row


class FakeTdsqlProxy:
    """mode: allsets | perset_only | no_passthrough | plain_mysql"""

    def __init__(self, sets, mode='allsets', route=None, old_server=False):
        self.sets = sets            # {set_name: {digest: counters}}
        self.mode = mode
        self.route = list(route or [])
        self.old_server = old_server
        self.now = T0
        self.first_seen = {}        # (set, digest) -> datetime
        self.executed = []
        self._result = []

    def execute(self, sql):
        self.executed.append(sql)
        if self.old_server and 'SUM_CPU_TIME' in sql:
            raise Exception("(1054, \"Unknown column 'SUM_CPU_TIME' in 'field list'\")")
        m = re.match(r'^/\*sets:([^*]+)\*/', sql)
        if m and self.mode in ('no_passthrough',):
            raise Exception('(694, "Proxy ERROR: unsupported")')
        if m and m.group(1) == 'allsets' and self.mode == 'perset_only':
            raise Exception('(694, "Proxy ERROR: allsets unsupported")')
        if m and self.mode != 'plain_mysql':
            target = m.group(1)
            names = list(self.sets) if target == 'allsets' else target.split(',')
            out = []
            for n in names:
                for r in self._rows_of(n):
                    if target == 'allsets':
                        r['info'] = n
                    out.append(r)
            self._result = out
            return
        # 不带透传（或原生 MySQL 忽略注释）：proxy 路由到某一个 set
        n = self.route.pop(0) if self.route else next(iter(self.sets))
        self._result = self._rows_of(n)

    def _rows_of(self, n):
        return [_digest_row(d, c, self.first_seen.get((n, d), T0), self.now)
                for d, c in self.sets[n].items()]

    def fetchall(self):
        # 真实服务端只返回被 SELECT 的列：没选 SUM_CPU_TIME 就不应出现该键
        if self.executed and 'SUM_CPU_TIME' not in self.executed[-1]:
            for r in self._result:
                r.pop('sum_cpu_time', None)
        return self._result

    def tick(self, minutes=5):
        self.now = self.now + _dt.timedelta(minutes=minutes)


def _bump(sets, exec_n=100, ms=5):
    for name in sets:
        for c in sets[name].values():
            c['exec'] = c.get('exec', 0) + exec_n
            c['elapsed_ps'] = c.get('elapsed_ps', 0) + exec_n * ms * 10 ** 9


@tag('unit')
class TdsqlPerSetCollectionTests(SimpleTestCase):

    def setUp(self):
        sqlstat_collect.CAPS = sqlstat_collect._Caps()

    def _sets(self):
        # 四个 set 的累计值差异很大 —— 跨 set 相减必然得到垃圾
        return {'set_1': {'a' * 32: {'exec': 1000, 'elapsed_ps': 5000 * 10 ** 9}},
                'set_2': {'a' * 32: {'exec': 3000, 'elapsed_ps': 15000 * 10 ** 9}},
                'set_3': {'a' * 32: {'exec': 500, 'elapsed_ps': 2500 * 10 ** 9}},
                'set_4': {'a' * 32: {'exec': 8000, 'elapsed_ps': 40000 * 10 ** 9}}}

    def _round(self, proxy, tracker, cfg, data):
        rows, meta = sqlstat_collect.fetch_digest_rows(proxy, cfg, tdsql_sets=data.get('sets'),
                                                       tdsql_mode='groupshard')
        return tracker.compute(cfg.id, rows), meta

    def test_T03_allsets_gives_true_cluster_total_every_round(self):
        """T-03 的场景：每轮全集群真值 400 次执行；原实现写成 无/无/2600/100/5100/无。"""
        sets = self._sets()
        proxy = FakeTdsqlProxy(sets, mode='allsets')
        tracker, cfg = DeltaTracker(), _Cfg()
        self._round(proxy, tracker, cfg, {})
        totals = []
        for _ in range(5):
            _bump(sets)
            proxy.tick()
            deltas, meta = self._round(proxy, tracker, cfg, {})
            self.assertEqual(meta['basis'], BASIS_PER_SET)
            totals.append(sum(d['exec_delta'] for d in deltas))
            self.assertEqual({d['set_name'] for d in deltas}, set(sets))
            self.assertEqual(sum(d['elapsed_ms_delta'] for d in deltas), 2000)
        self.assertEqual(totals, [400] * 5)
        self.assertTrue(all(sql.startswith('/*sets:allsets*/') for sql in proxy.executed))

    def test_perset_fallback_when_allsets_rejected(self):
        sets = self._sets()
        proxy = FakeTdsqlProxy(sets, mode='perset_only')
        tracker, cfg = DeltaTracker(), _Cfg()
        names = list(sets)
        self._round(proxy, tracker, cfg, {'sets': names})
        _bump(sets)
        proxy.tick()
        deltas, meta = self._round(proxy, tracker, cfg, {'sets': names})
        self.assertEqual(meta['basis'], BASIS_PER_SET)
        self.assertEqual(sum(d['exec_delta'] for d in deltas), 400)
        self.assertTrue(any(sql.startswith('/*sets:set_3*/') for sql in proxy.executed))

    def test_unrouted_groupshard_is_flagged_not_silently_trusted(self):
        """透传完全不可用：数据只来自 proxy 随机路由的一个 set，必须带不可信告警。"""
        sets = self._sets()
        proxy = FakeTdsqlProxy(sets, mode='no_passthrough',
                               route=['set_1', 'set_3', 'set_2'])
        rows, meta = sqlstat_collect.fetch_digest_rows(
            proxy, _Cfg(), tdsql_sets=list(sets), tdsql_mode='groupshard')
        self.assertEqual(meta['basis'], BASIS_UNROUTED)
        self.assertIn('不可信', meta['warning'])
        self.assertEqual({r['set_name'] for r in rows}, {''})

    def test_plain_mysql_behind_tdsql_type_is_single_node(self):
        """测试环境的 tdsql-sim 是原生 MySQL：注释被忽略、没有 info 列 → 单节点，不告警。"""
        sets = {'only': {'b' * 32: {'exec': 10, 'elapsed_ps': 10 ** 9}}}
        proxy = FakeTdsqlProxy(sets, mode='plain_mysql')
        rows, meta = sqlstat_collect.fetch_digest_rows(proxy, _Cfg(), tdsql_sets=[],
                                                       tdsql_mode='unknown')
        self.assertEqual(meta['basis'], BASIS_SINGLE)
        self.assertIsNone(meta['warning'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(proxy.executed), 1, '原生 MySQL 不应为探测透传多发查询')

    def test_unsafe_set_names_are_not_interpolated(self):
        sets = {'set_1': {'c' * 32: {'exec': 1}}}
        proxy = FakeTdsqlProxy(sets, mode='perset_only')
        sqlstat_collect.fetch_digest_rows(proxy, _Cfg(), tdsql_sets=['set_1', "x*/ DROP TABLE t; /*"],
                                          tdsql_mode='groupshard')
        self.assertFalse(any('DROP' in sql for sql in proxy.executed))

    def test_old_server_falls_back_to_base_columns_once(self):
        sets = {'set_1': {'d' * 32: {'exec': 5, 'elapsed_ps': 5 * 10 ** 9}}}
        proxy = FakeTdsqlProxy(sets, mode='allsets', old_server=True)
        cfg = _Cfg(cid=7)
        rows, _ = sqlstat_collect.fetch_digest_rows(proxy, cfg, tdsql_mode='groupshard')
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['has_cpu'])
        n_before = len(proxy.executed)
        sqlstat_collect.fetch_digest_rows(proxy, cfg, tdsql_mode='groupshard')
        self.assertEqual(len(proxy.executed) - n_before, 1, '能力探测结果应被缓存')

    def test_non_tdsql_never_uses_passthrough(self):
        sets = {'n': {'e' * 32: {'exec': 1}}}
        proxy = FakeTdsqlProxy(sets, mode='plain_mysql')
        sqlstat_collect.fetch_digest_rows(proxy, _Cfg(db_type='mysql'))
        self.assertFalse(any('/*sets:' in sql for sql in proxy.executed))

    def test_query_selects_recently_active_digests_not_cumulative_top(self):
        """T-12：取数条件是 LAST_SEEN 落在活跃窗口内。"""
        proxy = FakeTdsqlProxy({'n': {}}, mode='plain_mysql')
        sqlstat_collect.fetch_digest_rows(proxy, _Cfg(db_type='mysql'), window_sec=900)
        self.assertIn('LAST_SEEN >= NOW() - INTERVAL 900 SECOND', proxy.executed[0])


@tag('unit')
class DeltaTrackerTests(SimpleTestCase):

    def _row(self, set_name, digest, first_seen=T0, server_now=T0, **c):
        base = {out: 0 for out, _ in sqlstat_collect._COUNTERS}
        base.update(c)
        return {'set_name': set_name, 'digest': digest, 'schema_name': 's',
                'digest_text': 'x', 'sample_text': None, 'first_seen': first_seen,
                'server_now': server_now, 'counters': base, 'has_cpu': True}

    def test_first_round_produces_nothing(self):
        t = DeltaTracker()
        self.assertEqual(t.compute(1, [self._row('s', 'd', exec=10)]), [])

    def test_counter_reset_skips_instead_of_writing_negative(self):
        t = DeltaTracker()
        t.compute(1, [self._row('s', 'd', exec=100, elapsed_ps=10 ** 12)])
        self.assertEqual(t.compute(1, [self._row('s', 'd', exec=3, elapsed_ps=10 ** 9)]), [])
        out = t.compute(1, [self._row('s', 'd', exec=5, elapsed_ps=2 * 10 ** 9)])
        self.assertEqual(out[0]['exec_delta'], 2)

    def test_digest_first_seen_after_last_poll_counts_fully(self):
        t = DeltaTracker()
        t.compute(1, [self._row('s', 'old', exec=1, server_now=T0)])
        later = T0 + _dt.timedelta(minutes=5)
        new = self._row('s', 'new', exec=7, elapsed_ps=7 * 10 ** 9,
                        first_seen=T0 + _dt.timedelta(minutes=2), server_now=later)
        out = t.compute(1, [new])
        self.assertEqual([(o['sql_digest'], o['exec_delta']) for o in out], [('new', 7)])

    def test_unknown_old_digest_is_not_counted_as_new(self):
        """缓存里没有、且 FIRST_SEEN 早于上一轮：无法证明增量，只记录不写。"""
        t = DeltaTracker()
        t.compute(1, [self._row('s', 'a', exec=1, server_now=T0)])
        old = self._row('s', 'b', exec=999, first_seen=T0 - _dt.timedelta(days=3),
                        server_now=T0 + _dt.timedelta(minutes=5))
        self.assertEqual(t.compute(1, [old]), [])

    def test_sub_millisecond_sql_is_not_rounded_to_zero(self):
        t = DeltaTracker()
        t.compute(1, [self._row('s', 'd', exec=0, elapsed_ps=0)])
        out = t.compute(1, [self._row('s', 'd', exec=100, elapsed_ps=int(60.6 * 10 ** 9))])
        self.assertEqual(out[0]['elapsed_ms_delta'], 61)

    def test_extended_counters_are_carried(self):
        t = DeltaTracker()
        t.compute(1, [self._row('s', 'd')])
        out = t.compute(1, [self._row('s', 'd', exec=2, elapsed_ps=4 * 10 ** 9, lock_ps=10 ** 9,
                                      rows_examined=800000, rows_sent=16, tmp_disk=1,
                                      sort_merge=3, scan=2, no_index=2)])[0]
        self.assertEqual((out['lock_ms_delta'], out['reads_delta'], out['rows_delta'],
                          out['tmp_disk_delta'], out['sort_merge_delta'], out['scan_delta'],
                          out['no_index_delta']), (1, 800000, 16, 1, 3, 2, 2))

    def test_top_per_set_ranks_by_interval_delta_not_cumulative(self):
        t = DeltaTracker()
        t.compute(1, [self._row('s', 'big_history', exec=10 ** 6, elapsed_ps=10 ** 18),
                      self._row('s', 'new_hot', exec=1, elapsed_ps=10 ** 9)])
        out = t.compute(1, [
            self._row('s', 'big_history', exec=10 ** 6 + 1, elapsed_ps=10 ** 18 + 10 ** 9),
            self._row('s', 'new_hot', exec=11, elapsed_ps=10 ** 9 + 50 * 10 ** 12)])
        top = sqlstat_collect.top_per_set(out, 1)
        self.assertEqual([r['sql_digest'] for r in top], ['new_hot'])
