# -*- coding: utf-8 -*-
"""全量代码审计缺陷修复的回归测试（BUGFIX_DESIGN.md）。

每个用例对应一项缺陷，原则是「修复前失败、修复后通过」。
用例编号与 BUGFIX_DESIGN.md 的 BUG-1xx 一一对应，便于回溯。
"""
import json
import math
import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings, tag
from django.utils import timezone

from monitor import auth as auth_mod
from monitor.auth import Perm, RoleCode
from monitor.crypto import encrypt_password
from monitor.models import (
    AuditLog, DatabaseConfig, Role, RolePermission, UserProfile,
    UserProfileDatabase,
)


# =============================================================================
# 公共夹具
# =============================================================================
def make_role(code, perms):
    role, _ = Role.objects.get_or_create(
        code=code, defaults={'name': code, 'is_builtin': True})
    for p in perms:
        RolePermission.objects.get_or_create(role=role, permission_code=p)
    return role


def make_user(username, role_code, perms=(), allowed_dbs=None):
    user = User.objects.create_user(username=username, password='Pw!23456')  # noqa: secret 测试夹具
    profile = UserProfile.objects.create(user=user, role=make_role(role_code, perms))
    if allowed_dbs is not None:
        profile.allowed_databases = allowed_dbs
    return user


def make_db(name='db1', db_type='mysql', host='127.0.0.1', port=3306):
    return DatabaseConfig.objects.create(
        name=name, db_type=db_type, host=host, port=port,
        username='root', password=encrypt_password('pw'), is_active=True)


def login(client, user):
    """直接签发 token，跳过密码校验，专注被测逻辑。"""
    from monitor.auth import TokenManager
    token = TokenManager.generate_token(user.id)
    client.defaults['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    return token


# =============================================================================
# BUG-101 时序库连接池：并发安全 + 坏连接不回池
# =============================================================================
@tag('unit')
class Bug101TimeseriesPoolTests(TestCase):
    def _storage(self):
        from monitor.timeseries import TimeseriesStorage
        return TimeseriesStorage()

    def test_cursor_yields_none_when_disabled(self):
        st = self._storage()
        st.enabled = False
        with st.cursor() as cur:
            self.assertIsNone(cur)

    def test_connection_is_borrowed_and_returned(self):
        st = self._storage()
        st.enabled = True
        pool, conn = mock.MagicMock(), mock.MagicMock()
        pool.getconn.return_value = conn
        st._pool = pool
        with st.connection() as c:
            self.assertIs(c, conn)
        pool.putconn.assert_called_once_with(conn)

    def test_broken_connection_is_destroyed_not_returned(self):
        """坏连接必须 close=True 销毁，否则下一个借到它的线程继续报错。"""
        st = self._storage()
        st.enabled = True
        pool, conn = mock.MagicMock(), mock.MagicMock()
        pool.getconn.return_value = conn
        st._pool = pool
        with self.assertRaises(RuntimeError):
            with st.connection():
                raise RuntimeError('server closed the connection unexpectedly')
        pool.putconn.assert_called_once_with(conn, close=True)

    def test_concurrent_cursors_each_get_own_connection(self):
        """20 线程并发：每个线程必须拿到独立连接。

        修复前是进程级单例的唯一连接被所有线程共用 —— psycopg2 不允许，
        轻则 'another command is already in progress'，重则结果集串台。
        """
        st = self._storage()
        st.enabled = True
        pool = mock.MagicMock()
        pool.getconn.side_effect = lambda: mock.MagicMock()
        st._pool = pool

        seen, lock, errors = [], threading.Lock(), []

        def worker():
            try:
                with st.connection() as c:
                    with lock:
                        seen.append(id(c))
            except Exception as e:      # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(seen), 20)
        self.assertEqual(len(set(seen)), 20, '各线程必须持有互不相同的连接')

    def test_pool_created_once_under_concurrency(self):
        st = self._storage()
        st.enabled = True
        calls = []
        with mock.patch('psycopg2.pool.ThreadedConnectionPool') as mk:
            mk.side_effect = lambda *a, **k: calls.append(1) or mock.MagicMock()
            threads = [threading.Thread(target=st._get_pool) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 1, '连接池只应被创建一次')


# =============================================================================
# BUG-128 批量写：execute_values + 非法数值过滤
# =============================================================================
@tag('unit')
class Bug128BatchWriteTests(TestCase):
    def _storage_with_cursor(self):
        from monitor.timeseries import TimeseriesStorage
        st = TimeseriesStorage()
        st.enabled = True
        cur = mock.MagicMock()
        st.cursor = lambda: mock.MagicMock(
            __enter__=lambda s: cur, __exit__=lambda s, *a: False)
        return st, cur

    def test_metrics_batch_uses_single_roundtrip(self):
        st, cur = self._storage_with_cursor()
        metrics = {f'm{i}': float(i) for i in range(200)}
        with mock.patch('psycopg2.extras.execute_values') as ev:
            self.assertTrue(st.write_metrics_batch(1, metrics))
            self.assertEqual(ev.call_count, 1, '200 个指标应只发一次 execute_values')
            self.assertEqual(len(ev.call_args[0][2]), 200)

    def test_bool_and_non_finite_values_are_skipped(self):
        """bool 是 int 子类会被写成 1/0；NaN/Inf 会让 psycopg2 直接报错。"""
        st, _ = self._storage_with_cursor()
        metrics = {
            'ok': 1.5, 'flag': True, 'nan': float('nan'),
            'inf': float('inf'), 'none': None, 'text': 'abc',
        }
        with mock.patch('psycopg2.extras.execute_values') as ev:
            st.write_metrics_batch(1, metrics)
            written = {row[2] for row in ev.call_args[0][2]}
        self.assertEqual(written, {'ok'})

    def test_numeric_or_none_helper(self):
        from monitor.timeseries import TimeseriesStorage as T
        self.assertEqual(T._numeric_or_none(3), 3.0)
        self.assertIsNone(T._numeric_or_none(True))
        self.assertIsNone(T._numeric_or_none(None))
        self.assertIsNone(T._numeric_or_none(float('nan')))
        self.assertIsNone(T._numeric_or_none('5'))


# =============================================================================
# BUG-129 drop_hypertable 覆盖 7A 之后新增的对象
# =============================================================================
@tag('unit')
class Bug129DropHypertableTests(TestCase):
    def test_drops_all_objects_in_dependency_order(self):
        from monitor import timeseries as ts_mod
        cur = mock.MagicMock()
        storage = mock.MagicMock()
        storage.cursor.return_value = mock.MagicMock(
            __enter__=lambda s: cur, __exit__=lambda s, *a: False)
        with mock.patch.object(ts_mod, 'get_timeseries_storage', return_value=storage):
            self.assertTrue(ts_mod.drop_hypertable())
        stmts = [c[0][0] for c in cur.execute.call_args_list]
        joined = '\n'.join(stmts)
        for obj in ('session_ash_1m', 'metric_daily', 'metric_hourly',
                    'sql_stat', 'session_sample', 'collection_snapshot', 'metric_point'):
            self.assertIn(obj, joined, f'{obj} 未被清理')
        # 连续聚合必须先于其基表删除
        self.assertLess(joined.index('session_ash_1m'), joined.index('DROP TABLE'))


# =============================================================================
# BUG-112 Redis 客户端单例
# =============================================================================
@tag('unit')
class Bug112RedisBusTests(TestCase):
    def tearDown(self):
        from monitor.redis_bus import reset_bus
        reset_bus()

    def test_client_is_created_only_once(self):
        from monitor import redis_bus
        redis_bus.reset_bus()
        with mock.patch('redis.from_url', return_value=mock.MagicMock()) as mk:
            for _ in range(100):
                redis_bus.get_bus()
            self.assertEqual(mk.call_count, 1,
                             '100 次 emit 只应建立 1 个 Redis 客户端/连接池')

    def test_emit_event_reuses_singleton(self):
        from monitor import redis_bus
        redis_bus.reset_bus()
        fake = mock.MagicMock()
        fake.xadd.return_value = b'1-1'
        with mock.patch('redis.from_url', return_value=fake) as mk:
            for _ in range(20):
                redis_bus.emit_event({'config_id': 1, 'signal': 'x'})
            self.assertEqual(mk.call_count, 1)
            self.assertEqual(fake.xadd.call_count, 20)


# =============================================================================
# BUG-106 登录爆破防护不可被 X-Forwarded-For 绕过
# =============================================================================
@override_settings(
    TRUSTED_PROXY_IPS=[], LOGIN_MAX_ATTEMPTS=3,
    LOGIN_MAX_ATTEMPTS_PER_USER=100, LOGIN_LOCKOUT_SEC=900,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
@tag('unit')
class Bug106LoginThrottleTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = Client()
        User.objects.create_user(username='victim', password='CorrectPw!1')  # noqa: secret 测试夹具

    def _try(self, xff=None):
        headers = {'HTTP_X_FORWARDED_FOR': xff} if xff else {}
        return self.client.post(
            '/api/v1/auth/login/',
            data=json.dumps({'username': 'victim', 'password': 'wrong'}),
            content_type='application/json', **headers)

    def test_spoofed_xff_cannot_bypass_lockout(self):
        """每次换一个伪造 XFF —— 修复前计数器永远停在 1，锁定永不触发。"""
        for i in range(3):
            r = self._try(xff=f'10.1.1.{i}')
            self.assertEqual(r.status_code, 401)
        r = self._try(xff='10.1.1.99')
        self.assertEqual(r.status_code, 429, 'XFF 伪造不应绕过锁定')

    @override_settings(TRUSTED_PROXY_IPS=['127.0.0.1'], TRUSTED_PROXY_DEPTH=1)
    def test_xff_honored_behind_trusted_proxy(self):
        """请求确实来自可信代理时，XFF 应当生效（不同客户端各自计数）。"""
        for i in range(3):
            self.assertEqual(self._try(xff='203.0.113.7').status_code, 401)
        self.assertEqual(self._try(xff='203.0.113.7').status_code, 429)
        # 另一个真实客户端不受影响
        self.assertEqual(self._try(xff='203.0.113.8').status_code, 401)

    @override_settings(LOGIN_MAX_ATTEMPTS=1000, LOGIN_MAX_ATTEMPTS_PER_USER=4)
    def test_account_level_lockout_across_ips(self):
        """分布式换 IP 爆破：账号维度计数兜底。"""
        for i in range(4):
            self.assertEqual(self._try(xff=f'10.2.2.{i}').status_code, 401)
        self.assertEqual(self._try(xff='10.2.2.200').status_code, 429)

    def test_untrusted_xff_triggers_actionable_warning(self):
        """默认不信任 XFF 是安全的，但不该静默 —— 要告诉运维怎么配。"""
        from django.core.cache import cache
        cache.clear()
        req = mock.Mock(META={'REMOTE_ADDR': '172.18.0.5',
                              'HTTP_X_FORWARDED_FOR': '203.0.113.7'})
        with self.assertLogs('monitor.auth', level='WARNING') as cm:
            ip = auth_mod._client_ip(req)
        self.assertEqual(ip, '172.18.0.5', '未配置可信代理时必须按直连处理')
        joined = '\n'.join(cm.output)
        self.assertIn('DJANGO_TRUSTED_PROXY_IPS', joined, '提示必须可操作')
        self.assertIn('172.18.0.5', joined, '要告诉运维该把哪个 IP 加进白名单')

    def test_untrusted_xff_warning_is_deduplicated(self):
        """告警按来源去重，不能把日志刷爆。"""
        from django.core.cache import cache
        cache.clear()
        req = mock.Mock(META={'REMOTE_ADDR': '172.18.0.5',
                              'HTTP_X_FORWARDED_FOR': '203.0.113.7'})
        with self.assertLogs('monitor.auth', level='WARNING') as cm:
            for _ in range(50):
                auth_mod._client_ip(req)
        self.assertEqual(len(cm.output), 1, '同一来源 10 分钟内只告警一次')

    def test_no_warning_when_no_xff_present(self):
        """直连且无 XFF 是正常情况，不该产生噪音。"""
        from django.core.cache import cache
        import logging as _logging
        cache.clear()
        req = mock.Mock(META={'REMOTE_ADDR': '10.0.0.9'})
        with mock.patch.object(auth_mod.logger, 'warning') as w:
            auth_mod._client_ip(req)
        w.assert_not_called()

    def test_remaining_seconds_actually_counts_down(self):
        """返回值应是真实剩余秒，而非每次都报满额锁定时长。"""
        from django.core.cache import cache
        import time as _t
        cache.set(auth_mod._login_lock_key('victim', '127.0.0.1'),
                  _t.time() + 42, 900)
        req = mock.Mock(META={'REMOTE_ADDR': '127.0.0.1'})
        remaining = auth_mod.check_login_allowed(req, 'victim')
        self.assertIsNotNone(remaining)
        self.assertLessEqual(remaining, 42)
        self.assertGreater(remaining, 30)


# =============================================================================
# BUG-103 性能中心权限 + 数据范围隔离
# =============================================================================
@tag('integration')
class Bug103PerfAuthorizationTests(TestCase):
    def setUp(self):
        self.db1 = make_db('prod-db', port=3306)
        self.db2 = make_db('test-db', port=3307)
        # readonly: 有 metrics.view，无 sql_monitoring.view / tickets.create
        self.readonly = make_user('ro', RoleCode.READONLY, [Perm.METRICS_VIEW])
        # dba：权限齐全，但数据范围只到 db2
        self.scoped = make_user(
            'scoped', RoleCode.DBA,
            [Perm.METRICS_VIEW, Perm.SQL_MONITORING_VIEW, Perm.TICKETS_CREATE],
            allowed_dbs=[self.db2.id])

    def test_readonly_cannot_read_ash_details(self):
        """ASH 明细含 SQL 原文/登录用户/客户端 IP，需 sql_monitoring.view。"""
        c = Client()
        login(c, self.readonly)
        r = c.get(f'/api/v1/databases/{self.db1.id}/perf/ash-facets/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['code'], 'FORBIDDEN')

    def test_readonly_cannot_submit_kill_session(self):
        """修复前任何登录用户都能对任意实例提交 KILL_SESSION 高危工单。"""
        c = Client()
        login(c, self.readonly)
        r = c.post(f'/api/v1/databases/{self.db1.id}/perf/sessions/123/kill/',
                   data=json.dumps({'reason': 'x'}), content_type='application/json')
        self.assertEqual(r.status_code, 403)
        # 注意：AuditLogMiddleware 会为任何写请求留一条 API_CREATE 访问记录，
        # 这里要断言的是「没有产生 KILL_SESSION 运维工单」
        self.assertEqual(
            AuditLog.objects.filter(action_type='KILL_SESSION').count(), 0,
            '不得留下任何终止会话工单')

    def test_out_of_scope_instance_returns_404(self):
        """越权与不存在统一 404，避免实例存在性被枚举。"""
        c = Client()
        login(c, self.scoped)
        r = c.get(f'/api/v1/databases/{self.db1.id}/perf/aas/')
        self.assertEqual(r.status_code, 404)

    def test_existence_is_not_leaked_by_status_or_body(self):
        """越权实例 与 压根不存在的实例，对外响应必须完全一致。

        否则攻击者可以用 404/403 的差异把整个实例清单探出来。
        """
        c = Client()
        login(c, self.scoped)
        r_denied = c.get(f'/api/v1/databases/{self.db1.id}/perf/aas/')
        r_absent = c.get('/api/v1/databases/999999/perf/aas/')
        self.assertEqual(r_denied.status_code, r_absent.status_code)
        self.assertEqual(r_denied.json(), r_absent.json())

    def test_denial_reason_is_logged_server_side(self):
        """对外不区分，但服务端日志要能区分，否则排障无从下手。"""
        c = Client()
        login(c, self.scoped)
        with self.assertLogs('monitor.api_views_perf', level='WARNING') as cm:
            c.get(f'/api/v1/databases/{self.db1.id}/perf/aas/')
        self.assertTrue(any('超出用户数据范围' in m for m in cm.output),
                        f'越权应记录明确原因, 实际: {cm.output}')

        with self.assertLogs('monitor.api_views_perf', level='WARNING') as cm:
            c.get('/api/v1/databases/999999/perf/aas/')
        self.assertTrue(any('实例不存在' in m for m in cm.output),
                        f'不存在应记录明确原因, 实际: {cm.output}')

    def test_in_scope_instance_is_reachable(self):
        c = Client()
        login(c, self.scoped)
        r = c.get(f'/api/v1/databases/{self.db2.id}/perf/aas/')
        self.assertNotIn(r.status_code, (403, 404),
                         '范围内的实例不应被权限层挡住')

    def test_sql_plan_detail_enforces_scope(self):
        """该端点原先直接按 config_id 查 SqlPlan，完全绕过数据范围。"""
        c = Client()
        login(c, self.scoped)
        r = c.get(f'/api/v1/databases/{self.db1.id}/perf/sql/abc/plan/h1/')
        self.assertEqual(r.status_code, 404)


# =============================================================================
# BUG-111 阻塞树死锁环检测
# =============================================================================
@tag('unit')
class Bug111DeadlockCycleTests(TestCase):
    def _rows(self, edges, extra=None):
        rows = []
        for waiter, blocker in edges:
            rows.append({'session_id': waiter, 'is_blocked': True,
                         'blocker_id': blocker, 'active_secs': 5,
                         'wait_secs': 5, 'user_name': 'u'})
        for sid in (extra or []):
            rows.append({'session_id': sid, 'is_blocked': False,
                         'blocker_id': None, 'active_secs': 1, 'user_name': 'u'})
        return rows

    def test_two_node_cycle_is_reported(self):
        """A↔B 互等（死锁）—— 修复前 roots 为空，前端显示绿色"当前无阻塞链"。"""
        from monitor.api_views_perf import _build_blocking_tree
        tree = _build_blocking_tree(self._rows([('A', 'B'), ('B', 'A')]))
        self.assertTrue(tree, '死锁环必须被输出，不能返回空树')
        cycles = [n for n in tree if n.get('role') == 'deadlock_cycle']
        self.assertEqual(len(cycles), 1)
        self.assertEqual(set(cycles[0]['cycle_members']), {'A', 'B'})

    def test_three_node_cycle_is_reported(self):
        from monitor.api_views_perf import _build_blocking_tree
        tree = _build_blocking_tree(
            self._rows([('A', 'B'), ('B', 'C'), ('C', 'A')]))
        cycles = [n for n in tree if n.get('role') == 'deadlock_cycle']
        self.assertEqual(len(cycles), 1)
        self.assertEqual(set(cycles[0]['cycle_members']), {'A', 'B', 'C'})

    def test_cycle_and_normal_chain_coexist_cycle_first(self):
        from monitor.api_views_perf import _build_blocking_tree
        rows = self._rows([('A', 'B'), ('B', 'A'), ('W1', 'R'), ('W2', 'R')],
                          extra=['R'])
        tree = _build_blocking_tree(rows)
        roles = [n.get('role') for n in tree]
        self.assertIn('deadlock_cycle', roles)
        self.assertIn('root_blocker', roles)
        self.assertEqual(roles[0], 'deadlock_cycle', '死锁环应排最前')

    def test_acyclic_tree_unchanged(self):
        """回归保护：无环场景的输出与修复前保持一致。"""
        from monitor.api_views_perf import _build_blocking_tree
        tree = _build_blocking_tree(
            self._rows([('W1', 'R'), ('W2', 'R')], extra=['R']))
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]['session_id'], 'R')
        self.assertEqual(tree[0]['role'], 'root_blocker')
        self.assertEqual(tree[0]['subtree_waiters'], 2)

    def test_no_blocking_returns_empty(self):
        from monitor.api_views_perf import _build_blocking_tree
        self.assertEqual(_build_blocking_tree(self._rows([], extra=['S1'])), [])

    def test_find_cycles_helper(self):
        from monitor.api_views_perf import _find_cycles
        cycles = _find_cycles({'A': 'B', 'B': 'A', 'C': 'D'}, exclude=set())
        self.assertEqual(len(cycles), 1)
        self.assertEqual(set(cycles[0]), {'A', 'B'})


# =============================================================================
# BUG-126 AAS 堆叠序列补零点
# =============================================================================
@tag('unit')
class Bug126AasZeroFillTests(TestCase):
    def test_all_series_share_the_same_x_axis(self):
        """两个等待类落在不同时间桶 —— 缺零点会让 ECharts 堆叠错位。"""
        db = make_db()
        user = make_user('u1', RoleCode.DBA, [Perm.METRICS_VIEW])
        t0 = timezone.now().replace(second=0, microsecond=0) - timedelta(minutes=10)
        t1 = t0 + timedelta(minutes=1)
        cur = mock.MagicMock()
        cur.fetchall.return_value = [(t0, 'on_cpu', 30), (t1, 'user_io', 20)]
        with mock.patch('monitor.api_views_perf._ts_cursor',
                        return_value=mock.MagicMock(
                            __enter__=lambda s: cur, __exit__=lambda s, *a: False)):
            c = Client()
            login(c, user)
            r = c.get(f'/api/v1/databases/{db.id}/perf/aas/?window=1h')
        self.assertEqual(r.status_code, 200)
        series = r.json()['data']['series']
        self.assertEqual(len(series), 2)
        lengths = {len(s['points']) for s in series}
        self.assertEqual(lengths, {2}, '各系列点数必须一致（缺失桶补 0）')


# =============================================================================
# BUG-120 时序库异常降级为 502 而非 500
# =============================================================================
@tag('unit')
class Bug120TsdbGuardTests(TestCase):
    def test_query_failure_returns_structured_502(self):
        db = make_db()
        user = make_user('u2', RoleCode.DBA, [Perm.METRICS_VIEW])
        cur = mock.MagicMock()
        cur.execute.side_effect = RuntimeError('relation "session_ash_1m" does not exist')
        with mock.patch('monitor.api_views_perf._ts_cursor',
                        return_value=mock.MagicMock(
                            __enter__=lambda s: cur, __exit__=lambda s, *a: False)):
            c = Client()
            login(c, user)
            r = c.get(f'/api/v1/databases/{db.id}/perf/aas/?window=1h')
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()['code'], 'TSDB_ERROR')


# =============================================================================
# BUG-131 期间对比参数校验
# =============================================================================
@tag('unit')
class Bug131CompareValidationTests(TestCase):
    def setUp(self):
        self.db = make_db()
        self.user = make_user('u3', RoleCode.DBA, [Perm.METRICS_VIEW])
        self.c = Client()
        login(self.c, self.user)

    def test_reversed_range_rejected(self):
        """起点晚于终点时，旧代码会算出 avg_aas=0 —— 看起来像真实数据。"""
        r = self.c.get(f'/api/v1/databases/{self.db.id}/perf/compare/', {
            'a_from': '2026-01-02T00:00:00Z', 'a_to': '2026-01-01T00:00:00Z',
            'b_from': '2026-01-03T00:00:00Z', 'b_to': '2026-01-04T00:00:00Z'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('起点必须早于终点', r.json()['message'])

    def test_oversized_span_rejected(self):
        r = self.c.get(f'/api/v1/databases/{self.db.id}/perf/compare/', {
            'a_from': '2026-01-01T00:00:00Z', 'a_to': '2026-03-01T00:00:00Z',
            'b_from': '2026-03-01T00:00:00Z', 'b_to': '2026-03-02T00:00:00Z'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('不得超过', r.json()['message'])


# =============================================================================
# BUG-102 工单 dry-run 绝不执行原始 SQL
# =============================================================================
@tag('unit')
class Bug102DryRunTests(TestCase):
    def setUp(self):
        self.admin = make_user('adm', RoleCode.SUPER_ADMIN,
                               list(auth_mod.PERMISSION_META.keys()))
        self.c = Client()
        login(self.c, self.admin)

    def _audit(self, db_type, sql="ALTER SYSTEM KILL SESSION '12,34' IMMEDIATE"):
        cfg = make_db(f'{db_type}-db', db_type=db_type)
        return AuditLog.objects.create(
            config=cfg, action_type='KILL_SESSION', risk_level='high',
            status='pending', description='t', sql_command=sql, executor='someone')

    def _run(self, audit):
        cur = mock.MagicMock()
        conn = mock.MagicMock()
        conn.cursor.return_value = cur
        with mock.patch('monitor.db_connector.DbConnector.get_connection',
                        return_value=conn):
            r = self.c.post(f'/api/v1/auditlogs/{audit.id}/dry-run/')
        return r, cur

    def test_dm_uses_explain_not_raw_execution(self):
        """达梦此前落入 else 分支被**真实执行** —— 点"验证语法"会真的杀掉会话。"""
        r, cur = self._run(self._audit('dm'))
        self.assertEqual(r.status_code, 200)
        executed = [c[0][0] for c in cur.execute.call_args_list]
        self.assertTrue(executed, 'dm 应走 EXPLAIN PLAN FOR')
        for stmt in executed:
            self.assertTrue(stmt.startswith('EXPLAIN PLAN FOR '),
                            f'dry-run 执行了非 EXPLAIN 语句: {stmt}')
            self.assertNotEqual(stmt.strip(), 'ALTER SYSTEM KILL SESSION')

    def test_unknown_db_type_never_touches_database(self):
        audit = self._audit('mysql')
        audit.config.db_type = 'mongo'
        audit.config.save(update_fields=['db_type'])
        r, cur = self._run(audit)
        self.assertEqual(cur.execute.call_count, 0, '未知库类型不得执行任何语句')
        self.assertEqual(r.json()['parsed_commands'][0]['status'], 'unsupported')

    def test_sql_whitelist_enforced_before_connecting(self):
        """dry-run 此前完全绕过白名单，两条路径的安全策略漂移。"""
        audit = self._audit('mysql', sql='DROP TABLE users')
        with mock.patch('monitor.db_connector.DbConnector.get_connection') as gc:
            r = self.c.post(f'/api/v1/auditlogs/{audit.id}/dry-run/')
        self.assertEqual(r.status_code, 400)
        self.assertIn('安全校验失败', r.json()['message'])
        gc.assert_not_called()

    def test_mysql_prefixes_explain(self):
        r, cur = self._run(self._audit('mysql', sql='KILL 123'))
        executed = [c[0][0] for c in cur.execute.call_args_list]
        self.assertEqual(executed, ['EXPLAIN KILL 123'])


# =============================================================================
# BUG-105 工单职责分离 + 并发执行保护
# =============================================================================
@tag('integration')
class Bug105AuditWorkflowTests(TestCase):
    def setUp(self):
        perms = list(auth_mod.PERMISSION_META.keys())
        self.dba_a = make_user('dba_a', RoleCode.DBA, perms)
        self.dba_b = make_user('dba_b', RoleCode.DBA, perms)
        # 角色需为 dba 才能过 require_role
        self.cfg = make_db()

    def _audit(self, executor='dba_a', status='pending'):
        return AuditLog.objects.create(
            config=self.cfg, action_type='KILL_SESSION', risk_level='high',
            status=status, description='t', sql_command='KILL 123',
            executor=executor)

    def test_cannot_approve_own_request(self):
        audit = self._audit(executor='dba_a')
        c = Client()
        login(c, self.dba_a)
        r = c.post(f'/api/v1/auditlogs/{audit.id}/approve/')
        self.assertEqual(r.status_code, 403)
        self.assertIn('职责分离', r.json()['error'])
        audit.refresh_from_db()
        self.assertEqual(audit.status, 'pending')

    def test_other_dba_can_approve(self):
        audit = self._audit(executor='dba_a')
        c = Client()
        login(c, self.dba_b)
        r = c.post(f'/api/v1/auditlogs/{audit.id}/approve/')
        self.assertEqual(r.status_code, 200)
        audit.refresh_from_db()
        self.assertEqual(audit.status, 'approved')
        self.assertEqual(audit.approver, 'dba_b')

    @override_settings(AUDIT_REQUIRE_SEPARATE_APPROVER=False)
    def test_self_approval_allowed_when_switch_off(self):
        """单人运维场景可显式关闭四眼原则。"""
        audit = self._audit(executor='dba_a')
        c = Client()
        login(c, self.dba_a)
        r = c.post(f'/api/v1/auditlogs/{audit.id}/approve/')
        self.assertEqual(r.status_code, 200)

    def test_cannot_approve_non_pending(self):
        audit = self._audit(executor='dba_a', status='success')
        c = Client()
        login(c, self.dba_b)
        r = c.post(f'/api/v1/auditlogs/{audit.id}/approve/')
        self.assertEqual(r.status_code, 400)

    def test_cannot_reject_executed_ticket(self):
        audit = self._audit(executor='dba_a', status='success')
        c = Client()
        login(c, self.dba_b)
        r = c.post(f'/api/v1/auditlogs/{audit.id}/reject/')
        self.assertEqual(r.status_code, 400)
        audit.refresh_from_db()
        self.assertEqual(audit.status, 'success')

    def test_second_execute_is_rejected(self):
        """TOCTOU：抢占后状态即为 executing，第二个请求必须被拒。"""
        audit = self._audit(executor='dba_a', status='approved')
        c = Client()
        login(c, self.dba_b)
        conn = mock.MagicMock()
        with mock.patch('monitor.db_connector.DbConnector.get_connection',
                        return_value=conn), \
             mock.patch('monitor.auto_remediation_engine.AutoRemediationEngine'
                        '.execute_operation', return_value=(True, 'ok')):
            r1 = c.post(f'/api/v1/auditlogs/{audit.id}/execute/')
        self.assertEqual(r1.status_code, 200)
        # 第二次：状态已不是 approved
        r2 = c.post(f'/api/v1/auditlogs/{audit.id}/execute/')
        self.assertEqual(r2.status_code, 400)
        self.assertIn('只能执行已批准的工单', r2.json()['error'])


# =============================================================================
# BUG-104 执行链必须显式提交
# =============================================================================
@tag('unit')
class Bug104CommitTests(TestCase):
    def setUp(self):
        self.cfg = make_db()

    def _audit(self, sql='ALTER TABLESPACE users ADD DATAFILE'):
        return AuditLog.objects.create(
            config=self.cfg, action_type='RESIZE', risk_level='high',
            status='approved', description='t', sql_command=sql, executor='dba_a')

    def test_success_path_commits(self):
        """驱动默认 autocommit=False —— 不 commit 则连接关闭时整体 ROLLBACK，
        界面却显示"执行成功"。系统对操作者撒谎。"""
        from monitor.auto_remediation_engine import AutoRemediationEngine
        audit = self._audit()
        conn = mock.MagicMock()
        conn.cursor.return_value.description = None
        conn.cursor.return_value.rowcount = 1
        ok, _ = AutoRemediationEngine(self.cfg).execute_operation(
            audit.id, 'dba_b', conn)
        self.assertTrue(ok)
        conn.commit.assert_called_once()
        audit.refresh_from_db()
        self.assertEqual(audit.status, 'success')

    def test_failure_path_rolls_back(self):
        from monitor.auto_remediation_engine import AutoRemediationEngine
        audit = self._audit()
        conn = mock.MagicMock()
        conn.cursor.return_value.execute.side_effect = RuntimeError('boom')
        ok, _ = AutoRemediationEngine(self.cfg).execute_operation(
            audit.id, 'dba_b', conn)
        self.assertFalse(ok)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_executing_status_is_accepted(self):
        """调用方已在行锁内抢占为 executing，引擎需接受该状态。"""
        from monitor.auto_remediation_engine import AutoRemediationEngine
        audit = self._audit()
        audit.status = 'executing'
        audit.save(update_fields=['status'])
        conn = mock.MagicMock()
        conn.cursor.return_value.description = None
        conn.cursor.return_value.rowcount = 0
        ok, _ = AutoRemediationEngine(self.cfg).execute_operation(
            audit.id, 'dba_b', conn)
        self.assertTrue(ok)

    def test_unapproved_status_still_rejected(self):
        from monitor.auto_remediation_engine import AutoRemediationEngine
        audit = self._audit()
        audit.status = 'pending'
        audit.save(update_fields=['status'])
        ok, msg = AutoRemediationEngine(self.cfg).execute_operation(
            audit.id, 'dba_b', mock.MagicMock())
        self.assertFalse(ok)
        self.assertIn('未批准', msg)


# =============================================================================
# BUG-109/110 目标库连接：语句超时 + PG 不留 idle in transaction
# =============================================================================
@tag('unit')
class Bug109110ConnectionTests(TestCase):
    def setUp(self):
        self.cfg = make_db('pg', db_type='pgsql', port=5432)

    def test_pg_sets_statement_timeout_and_autocommit(self):
        from monitor.db_connector import DbConnector
        conn = mock.MagicMock()
        with mock.patch('psycopg2.connect', return_value=conn) as pc:
            DbConnector.get_connection(self.cfg, statement_timeout_ms=3000)
        self.assertIn('statement_timeout=3000', pc.call_args[1]['options'])
        self.assertTrue(conn.autocommit, 'PG 连接必须开 autocommit，'
                                         '否则 SELECT 会留下 idle in transaction 压住 xmin')

    def test_pg_readonly_only_when_requested(self):
        from monitor.db_connector import DbConnector
        conn = mock.MagicMock()
        with mock.patch('psycopg2.connect', return_value=conn):
            DbConnector.get_connection(self.cfg, readonly=True)
        conn.set_session.assert_called_once_with(readonly=True, autocommit=True)

        conn2 = mock.MagicMock()
        with mock.patch('psycopg2.connect', return_value=conn2):
            DbConnector.get_connection(self.cfg, readonly=False)
        conn2.set_session.assert_not_called()

    def test_mysql_sets_session_execution_limit(self):
        from monitor.db_connector import DbConnector
        cfg = make_db('my', db_type='mysql', port=3306)
        conn = mock.MagicMock()
        cur = mock.MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cur
        conn.cursor.return_value.__exit__ = lambda s, *a: False
        with mock.patch('pymysql.connect', return_value=conn):
            DbConnector.get_connection(cfg, statement_timeout_ms=4000)
        stmts = [c[0][0] for c in cur.execute.call_args_list]
        self.assertTrue(any('max_execution_time' in s for s in stmts))

    def test_default_timeout_from_settings(self):
        from monitor.db_connector import _stmt_timeout_ms
        with override_settings(TARGET_DB_STATEMENT_TIMEOUT_MS=7777):
            self.assertEqual(_stmt_timeout_ms(), 7777)
        self.assertEqual(_stmt_timeout_ms(1234), 1234)


# =============================================================================
# BUG-114 Oracle 对象名缓存按实例隔离
# =============================================================================
@tag('unit')
class Bug114OracleObjCacheTests(TestCase):
    def setUp(self):
        from monitor import sentinel
        sentinel._ORA_OBJ_CACHE.clear()

    def test_cache_does_not_leak_across_instances(self):
        """object_id 只在单库内唯一 —— 跨实例复用会显示另一套库的表名。"""
        from monitor.sentinel import _obj_cache_get, _obj_cache_put
        _obj_cache_put(1, 100, 'SCOTT.EMP')
        self.assertEqual(_obj_cache_get(1, 100), 'SCOTT.EMP')
        self.assertIsNone(_obj_cache_get(2, 100), '实例 2 不得命中实例 1 的缓存')

    def test_lru_evicts_oldest_not_everything(self):
        """原实现满了就整体 clear()，命中率周期性归零。"""
        from monitor import sentinel
        from monitor.sentinel import _obj_cache_get, _obj_cache_put
        cap = sentinel._ORA_OBJ_CACHE_MAX
        for i in range(cap + 10):
            _obj_cache_put(1, i, f'T{i}')
        self.assertEqual(len(sentinel._ORA_OBJ_CACHE), cap)
        self.assertIsNone(_obj_cache_get(1, 0), '最旧的应被淘汰')
        self.assertEqual(_obj_cache_get(1, cap + 9), f'T{cap + 9}', '最新的应保留')

    def test_concurrent_cache_access_is_safe(self):
        from monitor.sentinel import _obj_cache_get, _obj_cache_put
        errors = []

        def worker(n):
            try:
                for i in range(200):
                    _obj_cache_put(n, i, f'{n}.{i}')
                    _obj_cache_get(n, i)
            except Exception as e:      # pragma: no cover
                errors.append(e)

        ts = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])


# =============================================================================
# BUG-123 锁等待时长与事务年龄分离
# =============================================================================
@tag('unit')
class Bug123WaitSecsTests(TestCase):
    def test_mysql_wait_secs_is_lock_wait_not_trx_age(self):
        """跑了 2 小时的事务刚等锁 3 秒，不能显示"等待 7200 秒"。"""
        from monitor.sentinel import _ash_mysql_family
        cur = mock.MagicMock()
        cur.fetchall.side_effect = [
            # ① processlist
            [{'session_id': 11, 'user_name': 'app', 'client_host': 'h', 'db_name': 'd',
              'command': 'Query', 'state': 'updating', 'active_secs': 4,
              'sql_text': 'UPDATE t SET a=1'}],
            # ② 原生 digest
            [],
            # ③ 阻塞边
            [{'waiter': 11, 'blocker': 22, 'trx_age_secs': 7200, 'lock_wait_secs': 3}],
            # ④ 锁明细
            [],
        ]
        rows = _ash_mysql_family(cur, 'mysql')
        row = next(r for r in rows if r['session_id'] == '11')
        self.assertTrue(row['is_blocked'])
        self.assertEqual(row['wait_secs'], 3, '等待秒应为锁等待时长')
        self.assertEqual(row['trx_age_secs'], 7200, '事务年龄单独保留')

    def test_blocking_tree_prefers_wait_secs(self):
        from monitor.api_views_perf import _build_blocking_tree
        rows = [
            {'session_id': 'W', 'is_blocked': True, 'blocker_id': 'R',
             'active_secs': 7200, 'wait_secs': 3},
            {'session_id': 'R', 'is_blocked': False, 'blocker_id': None,
             'active_secs': 10, 'wait_secs': None},
        ]
        tree = _build_blocking_tree(rows)
        waiter = tree[0]['children'][0]
        self.assertEqual(waiter['wait_secs'], 3)

    def test_falls_back_to_active_secs_for_legacy_rows(self):
        """老数据没有 wait_secs 列，需回退 active_secs（不能显示 None）。"""
        from monitor.api_views_perf import _build_blocking_tree
        rows = [
            {'session_id': 'W', 'is_blocked': True, 'blocker_id': 'R',
             'active_secs': 42},
            {'session_id': 'R', 'is_blocked': False, 'blocker_id': None,
             'active_secs': 10},
        ]
        tree = _build_blocking_tree(rows)
        self.assertEqual(tree[0]['children'][0]['wait_secs'], 42)


# =============================================================================
# BUG-124 Oracle ASH 采集 sql_text
# =============================================================================
@tag('unit')
class Bug124OracleSqlTextTests(TestCase):
    def test_sql_text_is_backfilled_from_v_sqlstats(self):
        """Oracle 分支此前硬编码 sql_text=None，导致 SQL 详情页与优化建议全空。"""
        from monitor.sentinel import _ash_oracle
        cur = mock.MagicMock()
        cur.description = [
            ('SESSION_ID',), ('SQL_ID',), ('USER_NAME',), ('CLIENT_HOST',),
            ('PROGRAM',), ('MODULE',), ('COMMAND',), ('STATE',), ('WAIT_EVENT',),
            ('RAW_WAIT_CLASS',), ('ACTIVE_SECS',), ('BLOCKER_ID',),
            ('IS_BLOCKED',), ('WAIT_OBJNO',), ('SID_ONLY',),
        ]
        cur.fetchall.side_effect = [
            [('10,1', 'abc123', 'SCOTT', 'host', 'sqlplus', 'mod', 'ACTIVE',
              'WAITING', 'db file read', 'User I/O', 12, None, 0, 0, 10)],
            [('abc123', 'SELECT * FROM emp')],   # v$sqlstats 补查
        ]
        rows = _ash_oracle(cur, 'oracle', config_id=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sql_text'], 'SELECT * FROM emp')

    def test_uses_bind_variables_not_string_concat(self):
        from monitor.sentinel import _ash_oracle
        cur = mock.MagicMock()
        cur.description = [
            ('SESSION_ID',), ('SQL_ID',), ('USER_NAME',), ('CLIENT_HOST',),
            ('PROGRAM',), ('MODULE',), ('COMMAND',), ('STATE',), ('WAIT_EVENT',),
            ('RAW_WAIT_CLASS',), ('ACTIVE_SECS',), ('BLOCKER_ID',),
            ('IS_BLOCKED',), ('WAIT_OBJNO',), ('SID_ONLY',),
        ]
        cur.fetchall.side_effect = [
            [('10,1', 'abc123', 'S', 'h', 'p', 'm', 'ACTIVE', 'ON CPU',
              None, None, 1, None, 0, 0, 10)],
            [],
        ]
        _ash_oracle(cur, 'oracle', config_id=1)
        sqlstats_calls = [c for c in cur.execute.call_args_list
                          if 'v$sqlstats' in str(c[0][0])]
        self.assertTrue(sqlstats_calls)
        stmt, params = sqlstats_calls[0][0][0], sqlstats_calls[0][0][1]
        self.assertIn(':1', stmt, '应使用绑定变量而非字面量拼接')
        self.assertEqual(params, ['abc123'])


# =============================================================================
# BUG-115/134 告警聚合：窗口回收 + 线程安全 + 跨实例路由
# =============================================================================
@tag('unit')
class Bug115AggregationTests(TestCase):
    def setUp(self):
        from monitor.alert_manager import reset_aggregation
        reset_aggregation()
        self.cfg = make_db()

    def tearDown(self):
        from monitor.alert_manager import reset_aggregation
        reset_aggregation()

    def test_expired_window_is_reclaimed(self):
        """修复前：窗口过期且缓冲为空时时间戳永不清理，
        该 key 的聚合能力被永久关闭，_AGG_TS 单调增长。"""
        from monitor import alert_manager as am
        mgr = am.AlertManager(self.cfg)
        key = ('conn_high', 'threads_connected')
        am._AGG_TS[key] = timezone.now() - timedelta(
            seconds=am.AlertManager.AGGREGATION_WINDOW_SEC + 10)

        self.assertFalse(mgr._should_aggregate(*key))
        self.assertNotIn(key, am._AGG_TS, '过期窗口的时间戳必须被回收')

        # 回收后应能重新开窗
        am._AGG_TS[key] = timezone.now()
        self.assertTrue(mgr._should_aggregate(*key))

    def test_timestamps_do_not_grow_unboundedly(self):
        from monitor import alert_manager as am
        mgr = am.AlertManager(self.cfg)
        for i in range(50):
            key = ('t', f'm{i}')
            am._AGG_TS[key] = timezone.now() - timedelta(seconds=10_000)
            mgr._should_aggregate(*key)
        self.assertEqual(len(am._AGG_TS), 0)

    def test_flush_clears_empty_windows(self):
        from monitor import alert_manager as am
        mgr = am.AlertManager(self.cfg)
        key = ('t', 'm')
        am._AGG_TS[key] = timezone.now() - timedelta(
            seconds=am.AlertManager.AGGREGATION_WINDOW_SEC + 1)
        mgr.flush_expired_aggregations()
        self.assertNotIn(key, am._AGG_TS)

    def test_concurrent_add_never_double_sends(self):
        """8 线程各投 5 条：推送出去的总条数必须等于投入条数，不重不漏。"""
        from monitor import alert_manager as am
        from monitor.models import AlertLog
        mgr = am.AlertManager(self.cfg)
        key = ('t', 'm')
        am._AGG_TS[key] = timezone.now()

        sent, lock = [], threading.Lock()

        # patch 到类上会变成绑定方法，签名需带 self
        def fake_send(_self, _key, alerts):
            with lock:
                sent.extend(alerts)

        alerts = [AlertLog(config=self.cfg, alert_type='t', metric_key='m',
                           severity='warning', title=f'a{i}', status='active')
                  for i in range(40)]

        with mock.patch.object(am.AlertManager, '_send_aggregated_alert', fake_send):
            def worker(chunk):
                for a in chunk:
                    mgr._add_to_aggregation(a, key)

            threads = [threading.Thread(target=worker, args=(alerts[i * 5:(i + 1) * 5],))
                       for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            mgr.flush_expired_aggregations()
            # 冲刷残留
            with am._AGG_LOCK:
                leftover = am._AGG_BUFFER.pop(key, [])
            sent.extend(leftover)

        self.assertEqual(len(sent), 40, '聚合过程不得重复或丢失告警')
        self.assertEqual(len({id(a) for a in sent}), 40)

    def test_aggregated_title_reports_instance_count(self):
        from monitor import alert_manager as am
        from monitor.models import AlertLog
        cfg2 = make_db('db2', port=3307)
        cfg3 = make_db('db3', port=3308)
        alerts = [
            AlertLog(config=self.cfg, alert_type='t', metric_key='m',
                     severity='warning', title='x', status='active'),
            AlertLog(config=cfg2, alert_type='t', metric_key='m',
                     severity='warning', title='y', status='active'),
            AlertLog(config=cfg3, alert_type='t', metric_key='m',
                     severity='warning', title='z', status='active'),
        ]
        mgr = am.AlertManager(self.cfg)
        captured = {}
        with mock.patch.object(am.AlertManager, '_send_to_channels',
                               lambda s, t, b, ch, **kw: captured.update(title=t, body=b)), \
             mock.patch.object(am.AlertManager, '_log_notification', lambda *a, **k: None):
            mgr._send_aggregated_alert(('t', 'm'), alerts)
        self.assertIn('3条/3个实例', captured['title'])
        self.assertIn('db2', captured['body'])


# =============================================================================
# BUG-118 计划突变需比对 plan_hash
# =============================================================================
@tag('unit')
class Bug118PlanChangedTests(TestCase):
    def setUp(self):
        self.cfg = make_db()
        self.user = make_user('u4', RoleCode.DBA,
                              [Perm.METRICS_VIEW, Perm.SQL_MONITORING_VIEW])
        self.c = Client()
        login(self.c, self.user)

    def _mk_plan(self, plan_hash, minutes_ago):
        from monitor.models import SqlPlan
        p = SqlPlan.objects.create(
            config=self.cfg, sql_digest='d1', plan_hash=plan_hash,
            plan_json={}, plan_text='', source='auto', is_current=False)
        SqlPlan.objects.filter(id=p.id).update(
            captured_at=timezone.now() - timedelta(minutes=minutes_ago))
        return p

    def _detail(self):
        cur = mock.MagicMock()
        cur.fetchall.return_value = []
        with mock.patch('monitor.api_views_perf._ts_cursor',
                        return_value=mock.MagicMock(
                            __enter__=lambda s: cur, __exit__=lambda s, *a: False)), \
             mock.patch('monitor.api_views_perf._raw_sql_text',
                        return_value=(None, None)):
            r = self.c.get(f'/api/v1/databases/{self.cfg.id}/perf/sql/d1/')
        return r.json()['data']

    def test_same_hash_twice_is_not_a_change(self):
        """修复前只要有 >=2 条记录就报"计划已变更" —— 稳定 SQL 常年挂红标。"""
        self._mk_plan('H1', 60)
        self._mk_plan('H1', 10)
        data = self._detail()
        self.assertIsNone(data['plan_changed_at'])
        self.assertEqual(data['plan_hash_count'], 1)

    def test_different_hash_is_a_change(self):
        self._mk_plan('H1', 60)
        self._mk_plan('H2', 10)
        data = self._detail()
        self.assertIsNotNone(data['plan_changed_at'])
        self.assertEqual(data['plan_hash_count'], 2)

    def test_single_plan_is_not_a_change(self):
        self._mk_plan('H1', 10)
        self.assertIsNone(self._detail()['plan_changed_at'])


# =============================================================================
# BUG-119 SqlPlan is_current 并发唯一性
# =============================================================================
@tag('integration')
class Bug119PlanCurrentTests(TestCase):
    def test_only_one_current_plan_after_capture(self):
        from monitor.models import SqlPlan
        from monitor.plan_capture import capture
        cfg = make_db()
        SqlPlan.objects.create(config=cfg, sql_digest='d1', plan_hash='OLD',
                               plan_json={}, plan_text='', source='auto',
                               is_current=True)
        conn = mock.MagicMock()
        with mock.patch('monitor.plan_capture._capture_mysql',
                        return_value=({'query_block': {}}, 'txt', 1.0)), \
             mock.patch('monitor.plan_capture._maybe_emit_plan_change'):
            capture(cfg, 'd1', sql_text='SELECT 1', source='manual', conn=conn)
        self.assertEqual(
            SqlPlan.objects.filter(config=cfg, sql_digest='d1', is_current=True).count(),
            1, '同一 digest 只能有一个当前计划')

    def test_unchanged_plan_is_not_duplicated(self):
        from monitor.models import SqlPlan
        from monitor.plan_capture import capture, _structural_hash
        cfg = make_db()
        plan_json = {'query_block': {'table': 't'}}
        h = _structural_hash(plan_json)
        SqlPlan.objects.create(config=cfg, sql_digest='d1', plan_hash=h,
                               plan_json=plan_json, plan_text='', source='auto',
                               is_current=True)
        with mock.patch('monitor.plan_capture._capture_mysql',
                        return_value=(plan_json, 'txt', 1.0)):
            capture(cfg, 'd1', sql_text='SELECT 1', conn=mock.MagicMock())
        self.assertEqual(SqlPlan.objects.filter(config=cfg, sql_digest='d1').count(), 1)


# =============================================================================
# BUG-113/127 哨兵：崩溃自愈、配置变更重建、间隔常量统一
# =============================================================================
@tag('unit')
class Bug113SentinelLifecycleTests(TestCase):
    def test_ash_interval_default_is_consistent(self):
        """__init__ 用 5、启动日志打印 15 —— 同一常量两个值。"""
        from monitor import sentinel
        cfg = make_db()
        s = sentinel.InstanceSentinel(cfg)
        self.assertEqual(s.ash_interval_cfg, sentinel.ash_interval_sec())

    def test_run_loop_survives_probe_exception(self):
        """probe 抛异常时循环必须继续，否则线程死亡后永不重建 = 静默停止监控。"""
        from monitor import sentinel
        cfg = make_db()
        s = sentinel.InstanceSentinel(cfg)
        calls = []

        def boom():
            calls.append(1)
            if len(calls) >= 3:
                s.stop()
            raise RuntimeError('db unreachable')

        s.probe = boom
        s.ash_sample = lambda: None
        s.ash_interval_eff = 1
        # run_loop 每轮会 dj_conn.close()，那是**线程本地**的连接。
        # 必须像生产一样放到独立线程里跑，否则会关掉测试自己的连接。
        errors = []

        def runner():
            try:
                # W4 心跳上报是真线程副作用（独立连接提交），会污染后续
                # TestCase 的事务回滚隔离 —— 本用例只关心循环存活，mock 掉
                with mock.patch.object(sentinel, 'sentinel_interval_sec', return_value=0), \
                     mock.patch('monitor.self_monitor.report'):
                    s.run_loop()
            except Exception as e:      # pragma: no cover
                errors.append(e)

        t = threading.Thread(target=runner)
        t.start()
        t.join(timeout=10)
        self.assertFalse(t.is_alive(), 'run_loop 应已正常退出')
        self.assertEqual(errors, [], 'probe 异常不得让 run_loop 抛出')
        self.assertGreaterEqual(len(calls), 3, '异常后循环应继续')

    def test_refresh_restarts_dead_thread(self):
        from monitor import sentinel
        cfg = make_db()
        mgr = sentinel.SentinelManager()
        old = sentinel.InstanceSentinel(cfg)
        dead = mock.MagicMock()
        dead.is_alive.return_value = False
        mgr.sentinels[cfg.id] = old
        mgr.threads[cfg.id] = dead
        with mock.patch.object(threading, 'Thread') as T:
            T.return_value = mock.MagicMock()
            mgr._refresh()
        self.assertIsNot(mgr.sentinels[cfg.id], old, '线程已死应重建哨兵')

    def test_refresh_rebuilds_on_config_change(self):
        from monitor import sentinel
        cfg = make_db()
        mgr = sentinel.SentinelManager()
        old = sentinel.InstanceSentinel(cfg)
        alive = mock.MagicMock()
        alive.is_alive.return_value = True
        mgr.sentinels[cfg.id] = old
        mgr.threads[cfg.id] = alive

        cfg.host = '10.9.9.9'
        cfg.save(update_fields=['host'])

        with mock.patch.object(threading, 'Thread') as T:
            T.return_value = mock.MagicMock()
            mgr._refresh()
        self.assertIsNot(mgr.sentinels[cfg.id], old, '配置变更应重建哨兵')
        self.assertEqual(mgr.sentinels[cfg.id].config.host, '10.9.9.9')

    def test_refresh_keeps_healthy_sentinel(self):
        from monitor import sentinel
        cfg = make_db()
        mgr = sentinel.SentinelManager()
        s = sentinel.InstanceSentinel(cfg)
        alive = mock.MagicMock()
        alive.is_alive.return_value = True
        mgr.sentinels[cfg.id] = s
        mgr.threads[cfg.id] = alive
        mgr._refresh()
        self.assertIs(mgr.sentinels[cfg.id], s, '健康哨兵不应被无谓重建')

    def test_stale_connection_is_recycled(self):
        from monitor import sentinel
        import time as _t
        cfg = make_db()
        s = sentinel.InstanceSentinel(cfg)
        s.conn = mock.MagicMock()
        s._conn_created_at = _t.time() - 99999
        with override_settings(SENTINEL_CONN_MAX_AGE_SEC=60):
            s._recycle_if_stale()
        self.assertIsNone(s.conn, '超龄连接应被回收')


# =============================================================================
# BUG-130 UserProfileDatabase 外键级联
# =============================================================================
@tag('integration')
class Bug130AllowedDatabaseFkTests(TestCase):
    def test_deleting_instance_cascades_authorization(self):
        """修复前删库后授权残留；若新实例复用同一 ID 会造成静默越权。"""
        db1 = make_db('a', port=3306)
        db2 = make_db('b', port=3307)
        user = make_user('u5', RoleCode.DBA, [Perm.METRICS_VIEW],
                         allowed_dbs=[db1.id, db2.id])
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(sorted(profile.allowed_databases), sorted([db1.id, db2.id]))

        db1_id = db1.id
        db1.delete()

        self.assertFalse(
            UserProfileDatabase.objects.filter(config_id=db1_id).exists(),
            '实例删除后授权行必须级联清除')
        self.assertEqual(UserProfile.objects.get(user=user).allowed_databases, [db2.id])

    def test_allowed_databases_setter_roundtrip(self):
        db1 = make_db('a', port=3306)
        db2 = make_db('b', port=3307)
        user = make_user('u6', RoleCode.DBA, [Perm.METRICS_VIEW])
        p = UserProfile.objects.get(user=user)
        p.allowed_databases = [db1.id, db2.id]
        self.assertEqual(sorted(p.allowed_databases), sorted([db1.id, db2.id]))
        p.allowed_databases = [db2.id]
        self.assertEqual(p.allowed_databases, [db2.id])


# =============================================================================
# BUG-135 数据库列表包含已停用实例
# =============================================================================
@tag('unit')
class Bug135InactiveVisibilityTests(TestCase):
    def test_inactive_instance_still_listed(self):
        """停用后从列表消失 → 前端再也无法把它重新启用。"""
        db = make_db('paused')
        db.is_active = False
        db.save(update_fields=['is_active'])
        user = make_user('u7', RoleCode.DBA, [Perm.DATABASES_VIEW])
        c = Client()
        login(c, user)
        r = c.get('/api/v1/databases/')
        names = {d['name']: d for d in r.json()['databases']}
        self.assertIn('paused', names)
        self.assertFalse(names['paused']['is_active'])

    def test_include_inactive_can_be_disabled(self):
        db = make_db('paused')
        db.is_active = False
        db.save(update_fields=['is_active'])
        user = make_user('u8', RoleCode.DBA, [Perm.DATABASES_VIEW])
        c = Client()
        login(c, user)
        r = c.get('/api/v1/databases/?include_inactive=0')
        self.assertEqual([d['name'] for d in r.json()['databases']], [])


# =============================================================================
# BUG-122 EXPLAIN 端点拒绝与 digest 不匹配的 SQL
# =============================================================================
@tag('unit')
class Bug122ExplainFingerprintTests(TestCase):
    def setUp(self):
        self.cfg = make_db()
        self.user = make_user('u9', RoleCode.DBA,
                              [Perm.METRICS_VIEW, Perm.SQL_MONITORING_VIEW])
        self.c = Client()
        login(self.c, self.user)

    def test_mismatched_sql_rejected(self):
        """否则任何登录用户都能把任意 SQL 送到目标库 EXPLAIN 枚举表结构。"""
        r = self.c.post(
            f'/api/v1/databases/{self.cfg.id}/perf/sql/deadbeef/explain/',
            data=json.dumps({'sql_text': 'SELECT * FROM secret_table'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('指纹不匹配', r.json()['message'])

    def test_matching_sql_accepted(self):
        from monitor.sqlfingerprint import unified_digest
        sql = 'SELECT * FROM emp WHERE id = 1'
        digest = unified_digest(self.cfg.db_type, None, sql)
        with mock.patch('monitor.plan_capture.capture', return_value=None) as cap:
            r = self.c.post(
                f'/api/v1/databases/{self.cfg.id}/perf/sql/{digest}/explain/',
                data=json.dumps({'sql_text': sql}),
                content_type='application/json')
        cap.assert_called_once()
        self.assertEqual(r.status_code, 502)   # capture 返回 None → EXPLAIN_FAIL


# =============================================================================
# BUG-107 前端权限不再依赖可篡改的 role 短路（后端断言）
# =============================================================================
@tag('unit')
class Bug107BackendIsAuthoritativeTests(TestCase):
    def test_super_admin_permissions_are_complete(self):
        """前端移除 super_admin 短路的前提：服务端为超管下发全量权限。"""
        user = make_user('root', RoleCode.SUPER_ADMIN, [])
        perms = auth_mod.get_user_permissions(user)
        self.assertEqual(set(perms), set(auth_mod.PERMISSION_META.keys()))

    def test_tampered_client_cannot_escalate(self):
        """客户端无论怎么改 localStorage，后端仍按真实角色判定。"""
        db = make_db()
        user = make_user('lowpriv', RoleCode.READONLY, [Perm.METRICS_VIEW])
        c = Client()
        login(c, user)
        r = c.post(f'/api/v1/databases/{db.id}/perf/sessions/1/kill/',
                   data=json.dumps({'reason': 'x'}),
                   content_type='application/json')
        self.assertEqual(r.status_code, 403)


# =============================================================================
# BUG-132 API Key 有效期
# =============================================================================
@tag('unit')
class Bug132ApiKeyTtlTests(TestCase):
    def test_key_ttl_is_not_the_cache_timeout(self):
        from monitor.auth import APIKeyAuth
        self.assertGreater(APIKeyAuth._api_key_ttl_sec(), APIKeyAuth.CACHE_TIMEOUT)

    def test_generated_key_validates(self):
        from monitor.auth import APIKeyAuth
        with mock.patch('monitor.auth.cache') as c:
            APIKeyAuth.generate_api_key('sys', 1, ['metrics.view'])
            timeout = c.set.call_args[1]['timeout']
        self.assertEqual(timeout, APIKeyAuth._api_key_ttl_sec())


# =============================================================================
# REVIEW-02 自监控伪实例不得泄漏到面向用户的实例清单
# =============================================================================
class Review02SystemInstanceHiddenTests(TestCase):
    """`__system__` 靠 is_active=False 藏在列表外，而 BUG-135 的修复恰恰改成了
    "默认也返回停用实例" —— 两处改动叠加，伪实例就冒到了实例列表和导航树里。
    """

    def setUp(self):
        from monitor.self_monitor import SYSTEM_CONFIG_NAME
        self.sys_name = SYSTEM_CONFIG_NAME
        DatabaseConfig.objects.create(
            name=self.sys_name, db_type='mysql', host='localhost', port=0,
            username='-', password='', is_active=False)
        self.real = make_db('real-db')
        self.user = make_user('rev2', RoleCode.DBA, [Perm.DATABASES_VIEW])
        self.c = Client()
        login(self.c, self.user)

    def test_pseudo_instance_absent_from_list(self):
        names = [d['name'] for d in self.c.get('/api/v1/databases/').json()['databases']]
        self.assertNotIn(self.sys_name, names, '自监控伪实例不应出现在实例列表')
        self.assertIn('real-db', names, '真实实例必须仍然返回')

    def test_pseudo_instance_absent_even_with_include_inactive(self):
        names = [d['name'] for d in
                 self.c.get('/api/v1/databases/?include_inactive=1').json()['databases']]
        self.assertNotIn(self.sys_name, names)

    def test_停用的真实实例仍然可见(self):
        """回归保护：排除伪实例不能把 BUG-135 一起改回去。"""
        paused = make_db('paused-db', port=3307)
        paused.is_active = False
        paused.save(update_fields=['is_active'])
        rows = {d['name']: d for d in self.c.get('/api/v1/databases/').json()['databases']}
        self.assertIn('paused-db', rows)
        self.assertFalse(rows['paused-db']['is_active'])
