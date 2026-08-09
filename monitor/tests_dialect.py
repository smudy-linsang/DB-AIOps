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
        """BUG-109 回归：语句超时必须真的生效，而不只是"调用了 SET"。"""
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT @@SESSION.max_execution_time AS t")
            row = cur.fetchone()
            self.assertEqual(int(row['t']), 5000)
        finally:
            conn.close()

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
