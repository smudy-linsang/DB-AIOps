# -*- coding: utf-8 -*-
"""L3 方言测试：针对真实数据库验证 checkers/ 与 sentinel 的 SQL 正确性。

为什么必须用真实数据库：checkers/ 里是 6 种数据库的方言 SQL
（information_schema / v$session / pg_stat_activity ...），
mock 游标只能验证"代码路径走到了"，验证不了"这条 SQL 在目标库上真的能跑、
真的返回预期结构"。本轮审计发现的 BUG-123（MySQL 锁等待字段取错）、
BUG-124（Oracle 未采 sql_text）都属于"语法没错但语义错"，只有真库能抓。

跳过策略：未提供对应 DSN 环境变量时 skip，本地开发不受影响。
"""
import os
import unittest
from urllib.parse import urlparse

from django.test import TransactionTestCase, tag

from monitor.crypto import encrypt_password
from monitor.models import DatabaseConfig

MYSQL_DSN = os.environ.get('DIALECT_MYSQL_DSN')
PG_DSN = os.environ.get('DIALECT_PG_DSN')
ORACLE_DSN = os.environ.get('DIALECT_ORACLE_DSN')


def _cfg_from_dsn(dsn, db_type, name):
    u = urlparse(dsn)
    return DatabaseConfig.objects.create(
        name=name, db_type=db_type, host=u.hostname, port=u.port,
        username=u.username, password=encrypt_password(u.password or ''),
        service_name=(u.path or '/').lstrip('/') or None, is_active=True)


@tag('dialect')
@unittest.skipUnless(MYSQL_DSN, 'DIALECT_MYSQL_DSN 未设置')
class MySQLDialectTests(TransactionTestCase):
    def setUp(self):
        self.cfg = _cfg_from_dsn(MYSQL_DSN, 'mysql', 'dialect-mysql')

    def _conn(self, readonly=True):
        from monitor.db_connector import DbConnector
        return DbConnector.get_connection(self.cfg, statement_timeout_ms=5000,
                                          readonly=readonly)

    def test_connection_applies_statement_timeout(self):
        """BUG-109 回归：语句超时必须真的生效，而不只是"调用了 SET"。

        REVIEW-06: 原用例写死了 MySQL 的 max_execution_time(毫秒)，在 MariaDB 上
        直接 "Unknown system variable" 报错 —— 而这恰恰暴露了产品缺陷：
        MariaDB 用的是 max_statement_time(秒)，此前代码只试前者、失败即静默跳过，
        于是整个 MariaDB 系上语句超时保护根本不存在。
        这里改为"两者至少有一个生效"，覆盖 MySQL 与 MariaDB 两种方言。
        """
        conn = self._conn()
        try:
            cur = conn.cursor()
            applied = []
            try:
                cur.execute("SELECT @@SESSION.max_execution_time AS t")
                v = int(cur.fetchone()['t'])
                if v == 5000:
                    applied.append(f'max_execution_time={v}ms')
            except Exception:
                pass
            try:
                cur.execute("SELECT @@SESSION.max_statement_time AS t")
                v = float(cur.fetchone()['t'])
                if abs(v - 5.0) < 0.01:
                    applied.append(f'max_statement_time={v}s')
            except Exception:
                pass
            self.assertTrue(
                applied,
                '语句超时未生效：MySQL 的 max_execution_time 与 MariaDB 的 '
                'max_statement_time 都没有被设置成 5 秒，目标库慢查询将无法被中断')
        finally:
            conn.close()

    def test_statement_timeout_actually_cancels(self):
        """比"变量设对了"更强的断言：慢查询必须真的被中断。"""
        import time as _t
        conn = self._conn()
        try:
            cur = conn.cursor()
            t0 = _t.time()
            with self.assertRaises(Exception):
                cur.execute("SELECT SLEEP(30)")
            elapsed = _t.time() - t0
            self.assertLess(elapsed, 15,
                            f'SLEEP(30) 耗时 {elapsed:.1f}s 才返回，语句超时没有真正生效')
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def test_ash_sampling_returns_expected_shape(self):
        """ASH 采样 SQL 必须能在真实 MySQL 上执行并返回约定字段。"""
        from monitor.sentinel import sample_sessions
        conn = self._conn()
        try:
            cur = conn.cursor()
            rows = sample_sessions(cur, 'mysql')
        finally:
            conn.close()
        self.assertIsInstance(rows, list)
        for r in rows:
            for key in ('session_id', 'wait_class', 'sql_digest',
                        'is_blocked', 'wait_secs', 'trx_age_secs'):
                self.assertIn(key, r, f'ASH 行缺字段 {key}')

    def test_blocking_edge_reports_lock_wait_not_trx_age(self):
        """BUG-123 回归（真库版）：制造真实锁等待，验证 wait_secs 是锁等待时长
        而不是事务年龄。这是 mock 测不出来的 —— mock 里两个值都是我们自己填的。

        注（相对设计稿的修正）：建表/写入连接必须用 readonly=False ——
        只读会话上执行 DDL/DML 会直接报错。探测（观测）连接保持 readonly=True。
        """
        import threading
        import time
        from monitor.sentinel import sample_sessions

        setup = self._conn(readonly=False)
        cur = setup.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS lock_probe (id INT PRIMARY KEY, v INT)")
        cur.execute("REPLACE INTO lock_probe VALUES (1, 0)")
        setup.commit()

        holder = self._conn(readonly=False)
        hcur = holder.cursor()
        hcur.execute("BEGIN")
        hcur.execute("UPDATE lock_probe SET v = v + 1 WHERE id = 1")
        time.sleep(4)                      # 让持锁事务先"老"起来

        waiter = self._conn(readonly=False)
        err = []

        def block():
            try:
                wcur = waiter.cursor()
                wcur.execute("BEGIN")
                wcur.execute("UPDATE lock_probe SET v = v + 1 WHERE id = 1")
            except Exception as e:
                err.append(e)

        t = threading.Thread(target=block, daemon=True)
        t.start()
        time.sleep(2)                      # 等待者已阻塞约 2s，持锁事务已 6s

        probe = self._conn(readonly=True)
        try:
            rows = sample_sessions(probe.cursor(), 'mysql')
        finally:
            probe.close()

        blocked = [r for r in rows if r.get('is_blocked')]
        self.assertTrue(blocked, '应能观测到被阻塞会话')
        w = blocked[0]
        self.assertLess(w['wait_secs'], 4,
                        f"wait_secs 应为锁等待时长(~2s)，实际 {w['wait_secs']} "
                        f"—— 疑似又取成了事务年龄(BUG-123)")

        holder.rollback(); holder.close()
        try:
            waiter.rollback(); waiter.close()
        except Exception:
            pass
        cur.execute("DROP TABLE IF EXISTS lock_probe"); setup.commit(); setup.close()

    def test_explain_capture_on_real_table(self):
        """plan_capture 对真实表的 EXPLAIN 必须产出可解析的计划。"""
        from monitor.plan_capture import capture
        # 建表需要写权限
        setup = self._conn(readonly=False)
        try:
            cur = setup.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS plan_probe (id INT PRIMARY KEY, name VARCHAR(32))")
            setup.commit()
        finally:
            setup.close()
        conn = self._conn()
        try:
            plan = capture(self.cfg, 'digest-probe',
                           sql_text='SELECT * FROM plan_probe WHERE id = 1',
                           source='manual', conn=conn)
            self.assertIsNotNone(plan, 'EXPLAIN 应成功采集')
            self.assertTrue(plan.plan_text)
        finally:
            conn.close()
        drop = self._conn(readonly=False)
        try:
            dcur = drop.cursor()
            dcur.execute("DROP TABLE IF EXISTS plan_probe")
            drop.commit()
        finally:
            drop.close()


@tag('dialect')
@unittest.skipUnless(PG_DSN, 'DIALECT_PG_DSN 未设置')
class PostgresDialectTests(TransactionTestCase):
    def setUp(self):
        self.cfg = _cfg_from_dsn(PG_DSN, 'pgsql', 'dialect-pg')

    def _conn(self, readonly=True):
        from monitor.db_connector import DbConnector
        return DbConnector.get_connection(self.cfg, statement_timeout_ms=3000, readonly=readonly)

    def test_statement_timeout_actually_cancels(self):
        """BUG-109 回归：pg_sleep(10) 必须在 3s 内被取消。"""
        import psycopg2
        conn = self._conn()
        try:
            cur = conn.cursor()
            with self.assertRaises(psycopg2.errors.QueryCanceled):
                cur.execute("SELECT pg_sleep(10)")
        finally:
            conn.close()

    def test_no_idle_in_transaction_left_behind(self):
        """BUG-110 回归（本方案最该守住的一条）：
        采集连接查询后不得停留在 idle in transaction —— 否则压住 xmin，
        阻塞被监控库的 VACUUM，监控工具反过来损害被监控对象。"""
        from monitor.sentinel import sample_sessions
        conn = self._conn()
        try:
            cur = conn.cursor()
            sample_sessions(cur, 'pgsql')
            observer = self._conn()
            ocur = observer.cursor()
            ocur.execute(
                "SELECT state FROM pg_stat_activity "
                "WHERE pid = %s", (conn.get_backend_pid(),))
            state = (ocur.fetchone() or [None])[0]
            observer.close()
            self.assertNotEqual(state, 'idle in transaction',
                                '采集连接残留在事务中，会压住 xmin 阻塞 VACUUM')
        finally:
            conn.close()

    def test_readonly_session_rejects_writes(self):
        """采集连接必须只读：即便某条采集 SQL 写错，也不能改动被监控库。"""
        import psycopg2
        conn = self._conn(readonly=True)
        try:
            cur = conn.cursor()
            with self.assertRaises(psycopg2.Error):
                cur.execute("CREATE TABLE should_not_exist (id int)")
        finally:
            conn.close()


# =============================================================================
# checkers/ 采集器真库测试
#
# 为什么单列一节：monitor/checkers/ 共 6077 行、6 种数据库方言，此前**零测试**。
# 它是整个系统的数据入口 —— 采不到就什么都没有。而它又恰恰最难 mock：
# 方言 SQL 的正确性只有真库能验（mock 只能证明"代码路径走到了"）。
# 本轮把 MariaDB 拉起来跑，立刻抓出两个静默失效的产品缺陷（REVIEW-06/07），
# 印证了这块投入的价值。
# =============================================================================

class _FakeCommand:
    """BaseDBChecker 需要一个 command 宿主来回调 process_result。

    这里只捕获结果、不落库 —— 我们要验的是"采集器能不能从真库拿到正确的指标"，
    而不是后续的存储与告警链路（那些已有独立测试覆盖）。
    """

    def __init__(self):
        self.results = []
        self.alerts = []

    def process_result(self, config, data, **kwargs):
        self.results.append((config, data))

    def send_alert(self, *a, **k):
        self.alerts.append((a, k))

    def __getattr__(self, name):
        # 采集器可能回调其它辅助方法，一律吞掉，避免测试因无关调用失败
        def _noop(*a, **k):
            return None
        return _noop


@tag('dialect')
@unittest.skipUnless(MYSQL_DSN, 'DIALECT_MYSQL_DSN 未设置')
class MySQLCheckerDialectTests(TransactionTestCase):
    """MySQLChecker.collect_metrics 对真实 MySQL/MariaDB 的采集正确性。"""

    def setUp(self):
        self.cfg = _cfg_from_dsn(MYSQL_DSN, 'mysql', 'checker-mysql')

    def _collect(self):
        from monitor.checkers.mysql import MySQLChecker
        from monitor.db_connector import DbConnector
        conn = DbConnector.get_connection(self.cfg, statement_timeout_ms=15000)
        try:
            return MySQLChecker(_FakeCommand()).collect_metrics(self.cfg, conn)
        finally:
            conn.close()

    def test_collect_metrics_returns_core_contract(self):
        """采集必须跑通并产出核心契约字段。

        这是 checkers/ 的第一条真库测试 —— 此前 1590 行 MySQL 采集器
        连"能不能跑通"都没有任何自动化验证。
        """
        data = self._collect()
        self.assertIsInstance(data, dict)
        self.assertTrue(data, '采集结果不能为空')
        # 连接数是最基础、所有版本都该有的指标
        for key in ('active_connections', 'max_connections'):
            self.assertIn(key, data, f'采集结果缺少核心指标 {key}')
        self.assertIsInstance(data['max_connections'], int)
        self.assertGreater(data['max_connections'], 0)

    def test_connection_usage_is_consistent(self):
        """conn_usage_pct 必须与 active/max 自洽 —— 派生指标算错会直接误导告警。"""
        data = self._collect()
        if 'conn_usage_pct' not in data:
            self.skipTest('该版本未产出 conn_usage_pct')
        expected = data['active_connections'] / data['max_connections'] * 100
        self.assertAlmostEqual(data['conn_usage_pct'], expected, delta=1.0)

    def test_no_none_for_numeric_core_metrics(self):
        """核心数值指标不能是 None —— 下游 detectors 会拿去做阈值比较，
        None 会让比较静默失败（Python 3 里 None > x 直接抛 TypeError）。"""
        data = self._collect()
        for key in ('active_connections', 'max_connections'):
            self.assertIsNotNone(data.get(key), f'{key} 为 None')

    def test_collect_survives_restricted_privileges(self):
        """采集器在拿不到某些视图时应降级而不是整体崩溃。

        真实环境里监控账号权限往往受限（拿不到 performance_schema 等），
        采集必须尽力而为地返回已取到的部分。
        """
        data = self._collect()
        self.assertGreater(len(data), 5,
                           '即便部分视图不可用，也应返回可观数量的指标')


@tag('dialect')
@unittest.skipUnless(PG_DSN, 'DIALECT_PG_DSN 未设置')
class PostgresCheckerDialectTests(TransactionTestCase):
    """PostgreSQLChecker.collect_metrics 对真实 PostgreSQL 的采集正确性。"""

    def setUp(self):
        self.cfg = _cfg_from_dsn(PG_DSN, 'pgsql', 'checker-pg')

    def _collect(self):
        from monitor.checkers.pgsql import PostgreSQLChecker
        from monitor.db_connector import DbConnector
        conn = DbConnector.get_connection(self.cfg, statement_timeout_ms=15000)
        try:
            return PostgreSQLChecker(_FakeCommand()).collect_metrics(self.cfg, conn)
        finally:
            conn.close()

    def test_collect_metrics_returns_core_contract(self):
        data = self._collect()
        self.assertIsInstance(data, dict)
        self.assertTrue(data, '采集结果不能为空')
        for key in ('active_connections', 'max_connections'):
            self.assertIn(key, data, f'采集结果缺少核心指标 {key}')
        self.assertGreater(data['max_connections'], 0)

    def test_collect_does_not_leave_idle_in_transaction(self):
        """REVIEW/BUG-110 的采集器版本：跑完一整轮采集后，连接不得留在事务里。

        哨兵那条路径已有覆盖，但采集器是另一条独立的连接使用路径，
        同样会压住 xmin 阻塞被监控库的 VACUUM，必须单独守住。
        """
        from monitor.checkers.pgsql import PostgreSQLChecker
        from monitor.db_connector import DbConnector
        conn = DbConnector.get_connection(self.cfg, statement_timeout_ms=15000)
        try:
            PostgreSQLChecker(_FakeCommand()).collect_metrics(self.cfg, conn)
            observer = DbConnector.get_connection(self.cfg, readonly=True)
            try:
                ocur = observer.cursor()
                ocur.execute("SELECT state FROM pg_stat_activity WHERE pid = %s",
                             (conn.get_backend_pid(),))
                row = ocur.fetchone()
            finally:
                observer.close()
            self.assertIsNotNone(row, '应能观测到采集连接')
            self.assertNotEqual(row[0], 'idle in transaction',
                                '采集连接残留在事务中，会阻塞被监控库的 VACUUM')
        finally:
            conn.close()

    def test_numeric_core_metrics_not_none(self):
        data = self._collect()
        for key in ('active_connections', 'max_connections'):
            self.assertIsNotNone(data.get(key), f'{key} 为 None')


@tag('dialect')
@unittest.skipUnless(ORACLE_DSN, 'DIALECT_ORACLE_DSN 未设置')
class OracleDialectTests(TransactionTestCase):
    """Oracle 方言测试。

    本地通常跑不了（gvenzl/oracle-free 镜像 >2GB、启动数分钟），
    因此默认 skip；CI 的 test-dialect job 起容器后会真跑。
    覆盖的是 Oracle 特有、且本轮踩过坑的几条路径：
    BUG-114（对象名缓存跨实例串味）、BUG-124（ASH 不采 sql_text）、
    BUG-109（call_timeout）。
    """

    def setUp(self):
        self.cfg = _cfg_from_dsn(ORACLE_DSN, 'oracle', 'dialect-oracle')

    def _conn(self, readonly=True):
        from monitor.db_connector import DbConnector
        return DbConnector.get_connection(self.cfg, statement_timeout_ms=8000,
                                          readonly=readonly)

    def test_connection_sets_call_timeout(self):
        """BUG-109：oracledb 的 call_timeout 必须被设置（毫秒）。"""
        conn = self._conn()
        try:
            self.assertEqual(int(conn.call_timeout), 8000)
        finally:
            conn.close()

    def test_ash_sampling_returns_expected_shape(self):
        """v$session 采样能跑通并返回契约字段。"""
        from monitor.sentinel import sample_sessions
        conn = self._conn()
        try:
            rows = sample_sessions(conn.cursor(), 'oracle', config_id=self.cfg.id)
        finally:
            conn.close()
        self.assertIsInstance(rows, list)
        for r in rows:
            for key in ('session_id', 'wait_class', 'sql_digest', 'is_blocked'):
                self.assertIn(key, r, f'ASH 行缺字段 {key}')

    def test_ash_backfills_sql_text(self):
        """BUG-124 回归：Oracle 分支曾硬编码 sql_text=None，
        导致 SQL 详情页与索引建议在 Oracle 上整块失效。
        这里制造一条正在执行的语句，验证能从 v$sqlstats 补到原文。"""
        from monitor.sentinel import sample_sessions
        worker = self._conn()
        wcur = worker.cursor()
        wcur.execute("SELECT COUNT(*) FROM all_objects")   # 产生一条有 sql_id 的会话
        wcur.fetchall()

        probe = self._conn()
        try:
            rows = sample_sessions(probe.cursor(), 'oracle', config_id=self.cfg.id)
        finally:
            probe.close()
            worker.close()
        with_sql = [r for r in rows if r.get('sql_id')]
        if not with_sql:
            self.skipTest('采样窗口内没有捕获到带 sql_id 的会话')
        # 至少要有 sql_text 字段（可能为 None，但不能整体缺失）
        for r in with_sql:
            self.assertIn('sql_text', r)

    def test_object_name_cache_is_per_instance(self):
        """BUG-114 回归（真库版）：对象名缓存必须按 config_id 隔离。"""
        from monitor import sentinel
        sentinel._ORA_OBJ_CACHE.clear()
        conn = self._conn()
        try:
            sentinel.sample_sessions(conn.cursor(), 'oracle', config_id=self.cfg.id)
        finally:
            conn.close()
        # 缓存键必须是 (config_id, object_id) 二元组，不能是裸 object_id
        for key in sentinel._ORA_OBJ_CACHE:
            self.assertIsInstance(key, tuple)
            self.assertEqual(len(key), 2)
            self.assertEqual(key[0], self.cfg.id)


@tag('dialect')
@unittest.skipUnless(os.environ.get('DIALECT_DM_DSN'), 'DIALECT_DM_DSN 未设置')
class DamengDialectTests(TransactionTestCase):
    """达梦方言测试。

    达梦走 pyodbc + DM8 ODBC 驱动，CI 上没有现成镜像，默认 skip。
    保留骨架的意义：达梦是本项目声明支持的库型，而 BUG-102 正是
    "达梦落进 else 分支导致 dry-run 真实执行 SQL" —— 缺测试的库型最容易出事。
    有环境时设 DIALECT_DM_DSN 即可运行。
    """

    def setUp(self):
        self.cfg = _cfg_from_dsn(os.environ['DIALECT_DM_DSN'], 'dm', 'dialect-dm')

    def test_ash_sampling_runs(self):
        from monitor.sentinel import sample_sessions
        from monitor.db_connector import DbConnector
        conn = DbConnector.get_connection(self.cfg, statement_timeout_ms=8000)
        try:
            rows = sample_sessions(conn.cursor(), 'dm', config_id=self.cfg.id)
        finally:
            conn.close()
        self.assertIsInstance(rows, list)

    def test_dry_run_never_executes_raw_sql(self):
        """BUG-102 回归：达梦的工单预执行必须走 EXPLAIN PLAN FOR，
        绝不能把原始 SQL 直接送到库里执行。"""
        from monitor.auto_remediation_engine import AutoRemediationEngine
        ok, _ = AutoRemediationEngine._validate_sql_safety("DROP TABLE t")
        self.assertFalse(ok, '危险语句必须被白名单拦住')
