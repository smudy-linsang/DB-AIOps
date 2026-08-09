# -*- coding: utf-8 -*-
"""并发与竞态回归测试（BUGFIX_DESIGN.md §4.2 第 2 轮）。

这些用例针对的缺陷在单线程下全部"看起来正常"，只有并发才暴露：
  BUG-101 时序库单连接被多线程共用 → 游标结果集串台
  BUG-105 工单执行 TOCTOU → 同一工单被执行两次
  BUG-115 告警聚合共享状态无锁 → 重复推送 / 静默丢失
  BUG-119 SqlPlan is_current 并发双写 → 多个"当前计划"
  BUG-112 Redis 客户端每次新建 → 连接池爆炸
"""
import threading
from unittest import mock

from django.db import connection, connections
from django.test import TransactionTestCase, tag

from monitor.crypto import encrypt_password
from monitor.models import AuditLog, DatabaseConfig, SqlPlan


def make_db(name='cdb', port=3306):
    return DatabaseConfig.objects.create(
        name=name, db_type='mysql', host='127.0.0.1', port=port,
        username='root', password=encrypt_password('pw'), is_active=True)


def run_threads(target, n, *args):
    """启动 n 个线程跑 target，回收 Django 连接，返回收集到的异常。"""
    errors, lock = [], threading.Lock()

    def wrapper(idx):
        try:
            target(idx, *args)
        except Exception as e:                      # pragma: no cover
            with lock:
                errors.append(e)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapper, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return errors


# =============================================================================
# BUG-101 时序库连接池并发
# =============================================================================
@tag('integration')
class TimeseriesPoolConcurrencyTests(TransactionTestCase):
    def test_no_connection_shared_between_concurrent_borrowers(self):
        """并发借出的连接必须两两不同。

        修复前所有线程共用单例的唯一 psycopg2 连接 —— psycopg2 不支持在同一
        连接上并发执行语句，结果是 'another command is already in progress'
        或者 A 线程 fetchall() 拿到 B 线程的结果集（甲库图表显示乙库数据）。
        """
        from monitor.timeseries import TimeseriesStorage
        st = TimeseriesStorage()
        st.enabled = True
        st._pool = mock.MagicMock()
        st._pool.getconn.side_effect = lambda: mock.MagicMock()

        held, lock = [], threading.Lock()
        barrier = threading.Barrier(16)

        def worker(_i):
            with st.connection() as c:
                barrier.wait(timeout=20)     # 强制 16 个连接同时在手
                with lock:
                    held.append(id(c))

        errors = run_threads(worker, 16)
        self.assertEqual(errors, [])
        self.assertEqual(len(held), 16)
        self.assertEqual(len(set(held)), 16, '同时在手的连接必须互不相同')

    def test_cursor_contextmanager_always_returns_connection(self):
        from monitor.timeseries import TimeseriesStorage
        st = TimeseriesStorage()
        st.enabled = True
        pool = mock.MagicMock()
        pool.getconn.side_effect = lambda: mock.MagicMock()
        st._pool = pool

        def worker(_i):
            for _ in range(20):
                with st.cursor() as cur:
                    self.assertIsNotNone(cur)

        errors = run_threads(worker, 8)
        self.assertEqual(errors, [])
        # 每次借出都必须归还，否则池会耗尽
        self.assertEqual(pool.getconn.call_count, pool.putconn.call_count)


# =============================================================================
# BUG-105 工单执行 TOCTOU
# =============================================================================
@tag('integration')
class AuditExecuteConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.cfg = make_db('audit-db')
        self.audit = AuditLog.objects.create(
            config=self.cfg, action_type='RESIZE', risk_level='high',
            status='approved', description='t',
            sql_command='ALTER TABLESPACE users ADD DATAFILE', executor='dba_a')

    def test_only_one_thread_wins_the_claim(self):
        """10 线程同时抢占同一张已批准工单，只能有一个成功。

        修复前是 读状态 → 判断 → 执行 的 TOCTOU，两个并发请求都会执行，
        对 ADD DATAFILE 意味着加了两个数据文件。
        """
        from django.db import transaction
        winners, lock = [], threading.Lock()
        barrier = threading.Barrier(10)

        def claim(_i):
            barrier.wait(timeout=20)
            with transaction.atomic():
                a = AuditLog.objects.select_for_update().get(id=self.audit.id)
                if a.status != 'approved':
                    return
                a.status = 'executing'
                a.save(update_fields=['status'])
                with lock:
                    winners.append(_i)

        errors = run_threads(claim, 10)
        self.assertEqual(errors, [])
        self.assertEqual(len(winners), 1, '同一工单只能被抢占一次')
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.status, 'executing')


# =============================================================================
# BUG-119 SqlPlan is_current 并发唯一性
# =============================================================================
@tag('integration')
class PlanCaptureConcurrencyTests(TransactionTestCase):
    def test_at_most_one_current_plan(self):
        """10 线程并发采集不同 plan_hash，最终只能有一个 is_current=True。"""
        from monitor.plan_capture import capture
        cfg = make_db('plan-db')
        SqlPlan.objects.create(config=cfg, sql_digest='d1', plan_hash='H0',
                               plan_json={}, plan_text='', source='auto',
                               is_current=True)
        barrier = threading.Barrier(10)

        def worker(i):
            barrier.wait(timeout=20)
            with mock.patch('monitor.plan_capture._capture_mysql',
                            return_value=({'query_block': {'n': i}}, f'p{i}', 1.0)), \
                 mock.patch('monitor.plan_capture._maybe_emit_plan_change'):
                capture(cfg, 'd1', sql_text='SELECT 1', source='manual',
                        conn=mock.MagicMock())

        errors = run_threads(worker, 10)
        self.assertEqual(errors, [])
        current = SqlPlan.objects.filter(config=cfg, sql_digest='d1', is_current=True)
        self.assertEqual(current.count(), 1,
                         f'同一 digest 只能有一个当前计划，实际 {current.count()} 个')


# =============================================================================
# BUG-115 告警聚合共享状态
# =============================================================================
@tag('integration')
class AggregationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from monitor.alert_manager import reset_aggregation
        reset_aggregation()
        self.cfg = make_db('agg-db')

    def tearDown(self):
        from monitor.alert_manager import reset_aggregation
        reset_aggregation()

    def test_no_duplicate_and_no_loss_under_contention(self):
        """12 线程各投 10 条，总计 120 条：推送出去的必须恰好 120 条、无重复。"""
        from monitor import alert_manager as am
        from monitor.models import AlertLog
        from django.utils import timezone

        mgr = am.AlertManager(self.cfg)
        key = ('t', 'm')
        am._AGG_TS[key] = timezone.now()

        sent, lock = [], threading.Lock()

        def fake_send(_self, _key, alerts):
            with lock:
                sent.extend(alerts)

        alerts = [AlertLog(config=self.cfg, alert_type='t', metric_key='m',
                           severity='warning', title=f'a{i}', status='active')
                  for i in range(120)]
        barrier = threading.Barrier(12)

        with mock.patch.object(am.AlertManager, '_send_aggregated_alert', fake_send):
            def worker(i):
                barrier.wait(timeout=20)
                for a in alerts[i * 10:(i + 1) * 10]:
                    mgr._add_to_aggregation(a, key)

            errors = run_threads(worker, 12)
            self.assertEqual(errors, [])
            with am._AGG_LOCK:
                sent.extend(am._AGG_BUFFER.pop(key, []))

        self.assertEqual(len(sent), 120, '不得重复或丢失')
        self.assertEqual(len({id(a) for a in sent}), 120, '不得重复推送同一条告警')

    def test_should_aggregate_is_race_free(self):
        """并发调用 _should_aggregate 清理过期窗口时不得抛异常（字典并发改写）。"""
        from datetime import timedelta
        from django.utils import timezone
        from monitor import alert_manager as am

        mgr = am.AlertManager(self.cfg)
        for i in range(200):
            am._AGG_TS[('t', f'm{i}')] = timezone.now() - timedelta(seconds=10_000)

        def worker(_i):
            for i in range(200):
                mgr._should_aggregate('t', f'm{i}')

        errors = run_threads(worker, 8)
        self.assertEqual(errors, [])
        self.assertEqual(len(am._AGG_TS), 0, '过期窗口应全部回收')


# =============================================================================
# BUG-112 Redis 客户端单例
# =============================================================================
@tag('integration')
class RedisBusConcurrencyTests(TransactionTestCase):
    def tearDown(self):
        from monitor.redis_bus import reset_bus
        reset_bus()

    def test_single_client_under_concurrent_first_use(self):
        from monitor import redis_bus
        redis_bus.reset_bus()
        created, lock = [], threading.Lock()

        def make_client(*a, **k):
            with lock:
                created.append(1)
            return mock.MagicMock()

        barrier = threading.Barrier(16)
        with mock.patch('redis.from_url', side_effect=make_client):
            def worker(_i):
                barrier.wait(timeout=20)
                redis_bus.get_bus()

            errors = run_threads(worker, 16)
        self.assertEqual(errors, [])
        self.assertEqual(len(created), 1,
                         '并发首次使用也只能建立一个 Redis 客户端/连接池')


# =============================================================================
# BUG-114 Oracle 对象名缓存并发
# =============================================================================
@tag('integration')
class OracleObjCacheConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from monitor import sentinel
        sentinel._ORA_OBJ_CACHE.clear()

    def test_lru_stays_bounded_and_isolated(self):
        from monitor import sentinel
        from monitor.sentinel import _obj_cache_get, _obj_cache_put

        def worker(n):
            for i in range(500):
                _obj_cache_put(n, i, f'inst{n}.obj{i}')
                v = _obj_cache_get(n, i)
                if v is not None and not v.startswith(f'inst{n}.'):
                    raise AssertionError(f'缓存串味: 实例 {n} 读到 {v}')

        errors = run_threads(worker, 6)
        self.assertEqual(errors, [])
        self.assertLessEqual(len(sentinel._ORA_OBJ_CACHE),
                             sentinel._ORA_OBJ_CACHE_MAX,
                             'LRU 容量必须受控')
