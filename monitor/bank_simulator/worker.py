# -*- coding: utf-8 -*-
"""银行业务模拟器 - 单库 worker。

一个 BankWorker 实例对应一个 DatabaseConfig, 在独立线程中运行:
  1. 建表 (幂等)
  2. 种子数据 (幂等)
  3. 调度循环: 柜面交易 / 转账 / 报表 / 日终 / 偶发 DDL
"""
import datetime as _dt
import logging
import random
import threading
import time
import traceback
from decimal import Decimal

from monitor.bank_simulator.dialect import get_dialect

logger = logging.getLogger('bank_simulator')

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _tx_no():
    """生成全局唯一交易号: 时间戳(17位) + 随机(6位)。"""
    return _dt.datetime.now().strftime('%Y%m%d%H%M%S%f') + f'{random.randint(0,999999):06d}'


def _acct_no(prefix, n):
    return f"{prefix}{n:012d}"


def _row_to_dict(cursor, row):
    """将 (cursor, row) 转为 dict; DictCursor 返回的 dict 直接返回。"""
    if isinstance(row, dict):
        return row
    if row is None:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _fetchone_dict(cursor):
    row = cursor.fetchone()
    return _row_to_dict(cursor, row)


def _fetchall_dict(cursor):
    rows = cursor.fetchall()
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class BankWorker:
    """单库模拟 worker。"""

    # 调度权重 (累计权重)
    SCHEDULE = [
        ('teller',   3, 8,  60),   # 3~8s, 60%
        ('transfer', 5, 15, 20),   # 5~15s, 20%
        ('report',   10, 30, 15),
        ('batch',    60, 90, 4),
        ('ddl',      300, 600, 1),
    ]

    def __init__(self, db_config, stop_event: threading.Event, dry_run: bool = False):
        self.cfg = db_config
        self.stop = stop_event
        self.dry_run = dry_run
        self.db_type = (db_config.db_type or '').lower()
        self.dialect = get_dialect(self.db_type)
        self.conn = None
        self.stats = {k: 0 for k in ('ok', 'fail', 'ddl')}
        self.account_ids = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 连接
    def _connect(self):
        from monitor.db_connector import DbConnector
        return DbConnector.get_connection(self.cfg)

    def _ensure_conn(self):
        if self.conn is None:
            self.conn = self._connect()
            # 关闭自动提交 (mysql pymysql 默认 autocommit=0, 显式设一下)
            try:
                if self.db_type in ('mysql', 'tdsql', 'gbase'):
                    self.conn.autocommit(False)
            except Exception:
                pass
        return self.conn

    def _reconnect(self):
        try:
            if self.conn is not None:
                try: self.conn.close()
                except Exception: pass
        except Exception:
            pass
        self.conn = None
        return self._connect()

    def _close(self):
        if self.conn:
            try: self.conn.close()
            except Exception: pass
            self.conn = None

    # ------------------------------------------------------------------ DDL
    def _exec_ddl_safe(self, sql, ignore_exists=True):
        """执行 DDL, 忽略 'already exists' 类异常。"""
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
            try: self.conn.commit()
            except Exception: pass
            return True, None
        except Exception as e:
            msg = str(e).lower()
            if ignore_exists and any(k in msg for k in (
                    'already exists', 'exists', 'ora-00955', '42p07', '1050',
                    'duplicate key', 'duplicate table')):
                try: self.conn.rollback()
                except Exception: pass
                return True, 'exists'
            try: self.conn.rollback()
            except Exception: pass
            return False, str(e)
        finally:
            cur.close()

    def setup_schema(self):
        """建表 + 索引 (幂等)。"""
        for ddl in self.dialect.all_ddl():
            ok, err = self._exec_ddl_safe(ddl)
            if not ok:
                logger.warning("[%s] DDL 失败: %s\n  SQL: %s",
                               self.cfg.name, err, ddl.strip()[:200])
        for idx in self.dialect.indexes():
            self._exec_ddl_safe(idx, ignore_exists=True)
        logger.info("[%s] schema 就绪", self.cfg.name)

    # ------------------------------------------------------------------ 种子
    def seed(self):
        """幂等种子: 10 客户 + 20 账户 + 5 贷款。"""
        cur = self.conn.cursor()
        try:
            # 客户
            cur.execute("SELECT COUNT(*) FROM bsim_customer")
            n = cur.fetchone()
            n = n[0] if isinstance(n, (list, tuple)) else list(n.values())[0]
            if n == 0:
                now = _dt.datetime.now()
                for i in range(1, 11):
                    cur.execute(
                        "INSERT INTO bsim_customer "
                        "(customer_no, name, id_type, id_no, mobile, cust_level, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (f'C{2026000000+i}', f'客户{i}', 1,
                         f'ID{i:015d}', f'138{random.randint(10000000,99999999)}',
                         random.choice(['NORMAL','NORMAL','NORMAL','VIP','PRIVATE']),
                         now))
                logger.info("[%s] 种子: 10 客户", self.cfg.name)

            # 账户
            cur.execute("SELECT COUNT(*) FROM bsim_account")
            n = cur.fetchone()
            n = n[0] if isinstance(n, (list, tuple)) else list(n.values())[0]
            if n == 0:
                now = _dt.datetime.now()
                for i in range(1, 21):
                    cust_id = ((i - 1) % 10) + 1
                    atype = 'SAVINGS' if i % 3 != 0 else 'CHECKING'
                    bal = Decimal(str(random.randint(10000, 1000000)))
                    cur.execute(
                        "INSERT INTO bsim_account "
                        "(account_no, customer_id, account_type, currency, balance, "
                        "available_balance, status, opened_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (_acct_no('622', i), cust_id, atype, 'CNY', bal, bal, 1, now, now))
                logger.info("[%s] 种子: 20 账户", self.cfg.name)

            # 账户 ID 缓存
            cur.execute("SELECT account_id FROM bsim_account WHERE status=1")
            rows = _fetchall_dict(cur)
            self.account_ids = [r['account_id'] for r in rows]

            # 贷款
            cur.execute("SELECT COUNT(*) FROM bsim_loan")
            n = cur.fetchone()
            n = n[0] if isinstance(n, (list, tuple)) else list(n.values())[0]
            if n == 0 and self.account_ids:
                today = _dt.date.today()
                for i in range(1, 6):
                    acct = random.choice(self.account_ids)
                    cur.execute(
                        "INSERT INTO bsim_loan "
                        "(loan_no, account_id, principal, rate, start_date, term_days, "
                        "accrued_interest, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (f'LN{2026000000+i}', acct,
                         Decimal(str(random.randint(50000, 500000))),
                         Decimal(str(round(random.uniform(3.5, 7.5), 4))),
                         today - _dt.timedelta(days=random.randint(30, 365)),
                         random.choice([180, 360, 720]),
                         Decimal('0'), 'ACTIVE'))
                logger.info("[%s] 种子: 5 贷款", self.cfg.name)

            self.conn.commit()
        except Exception as e:
            try: self.conn.rollback()
            except Exception: pass
            logger.warning("[%s] 种子失败: %s", self.cfg.name, e)
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------ 事务
    def _cur(self):
        return self.conn.cursor()

    def _do_teller(self):
        """柜面交易: 存款 / 取款 / 手续费。"""
        kind = random.choice(['deposit', 'withdraw', 'fee'])
        acct = random.choice(self.account_ids)
        amount = Decimal(str(round(random.uniform(100, 50000), 2)))
        channel = random.choice(['COUNTER', 'ATM', 'MOBILE', 'WEB'])
        now = _dt.datetime.now()
        tx_no = _tx_no()
        cur = self._cur()
        try:
            if kind == 'deposit':
                cur.execute(
                    "UPDATE bsim_account SET balance=balance+%s, "
                    "available_balance=available_balance+%s, updated_at=%s "
                    "WHERE account_id=%s AND status=1",
                    (amount, amount, now, acct))
                cur.execute(
                    "SELECT balance FROM bsim_account WHERE account_id=%s", (acct,))
                row = cur.fetchone()
                bal = row[0] if isinstance(row, (list, tuple)) else list(row.values())[0]
                cur.execute(
                    "INSERT INTO bsim_transaction "
                    "(tx_no, account_id, tx_type, amount, balance_after, channel, remark, tx_time) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (tx_no, acct, 'DEPOSIT', amount, bal, channel, '柜面存款', now))
            elif kind == 'withdraw':
                cur.execute(
                    "SELECT balance, available_balance FROM bsim_account "
                    "WHERE account_id=%s AND status=1", (acct,))
                row = cur.fetchone()
                if row is None:
                    return
                bal, avail = (row[0], row[1]) if isinstance(row, (list, tuple)) \
                    else (row['balance'], row['available_balance'])
                if avail < amount:
                    return  # 余额不足, 跳过
                cur.execute(
                    "UPDATE bsim_account SET balance=balance-%s, "
                    "available_balance=available_balance-%s, updated_at=%s "
                    "WHERE account_id=%s",
                    (amount, amount, now, acct))
                new_bal = Decimal(str(bal)) - amount
                cur.execute(
                    "INSERT INTO bsim_transaction "
                    "(tx_no, account_id, tx_type, amount, balance_after, channel, remark, tx_time) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (tx_no, acct, 'WITHDRAW', amount, new_bal, channel, '柜面取款', now))
            else:  # fee
                fee = Decimal(str(round(random.uniform(5, 200), 2)))
                cur.execute(
                    "UPDATE bsim_account SET balance=balance-%s, "
                    "available_balance=available_balance-%s, updated_at=%s "
                    "WHERE account_id=%s AND balance>=%s",
                    (fee, fee, now, acct, fee))
                cur.execute(
                    "SELECT balance FROM bsim_account WHERE account_id=%s", (acct,))
                row = cur.fetchone()
                bal = row[0] if isinstance(row, (list, tuple)) else list(row.values())[0]
                cur.execute(
                    "INSERT INTO bsim_transaction "
                    "(tx_no, account_id, tx_type, amount, balance_after, channel, remark, tx_time) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (tx_no, acct, 'FEE', fee, bal, channel, '手续费', now))
            self.conn.commit()
            self.stats['ok'] += 1
        except Exception as e:
            try: self.conn.rollback()
            except Exception: pass
            self.stats['fail'] += 1
            logger.debug("[%s] teller 失败: %s", self.cfg.name, e)
        finally:
            cur.close()

    def _do_transfer(self):
        """内部转账 (事务)。"""
        if len(self.account_ids) < 2:
            return
        a, b = random.sample(self.account_ids, 2)
        amount = Decimal(str(round(random.uniform(500, 100000), 2)))
        now = _dt.datetime.now()
        cur = self._cur()
        try:
            cur.execute(
                "SELECT balance FROM bsim_account WHERE account_id=%s AND status=1", (a,))
            row = cur.fetchone()
            if row is None:
                return
            bal = row[0] if isinstance(row, (list, tuple)) else list(row.values())[0]
            if bal < amount:
                return
            cur.execute(
                "UPDATE bsim_account SET balance=balance-%s, "
                "available_balance=available_balance-%s, updated_at=%s "
                "WHERE account_id=%s",
                (amount, amount, now, a))
            cur.execute(
                "UPDATE bsim_account SET balance=balance+%s, "
                "available_balance=available_balance+%s, updated_at=%s "
                "WHERE account_id=%s",
                (amount, amount, now, b))
            cur.execute(
                "SELECT balance FROM bsim_account WHERE account_id=%s", (a,))
            row = cur.fetchone()
            bal_a = row[0] if isinstance(row, (list, tuple)) else list(row.values())[0]
            cur.execute(
                "SELECT balance FROM bsim_account WHERE account_id=%s", (b,))
            row = cur.fetchone()
            bal_b = row[0] if isinstance(row, (list, tuple)) else list(row.values())[0]
            cur.execute(
                "INSERT INTO bsim_transaction "
                "(tx_no, account_id, tx_type, amount, balance_after, channel, "
                "counterparty_account, remark, tx_time) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (_tx_no(), a, 'TRANSFER_OUT', amount, bal_a, 'MOBILE',
                 _acct_no('622', b), '转账出', now))
            cur.execute(
                "INSERT INTO bsim_transaction "
                "(tx_no, account_id, tx_type, amount, balance_after, channel, "
                "counterparty_account, remark, tx_time) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (_tx_no(), b, 'TRANSFER_IN', amount, bal_b, 'MOBILE',
                 _acct_no('622', a), '转账入', now))
            self.conn.commit()
            self.stats['ok'] += 1
        except Exception as e:
            try: self.conn.rollback()
            except Exception: pass
            self.stats['fail'] += 1
            logger.debug("[%s] transfer 失败: %s", self.cfg.name, e)
        finally:
            cur.close()

    def _do_report(self):
        """报表查询 (只读)。"""
        cur = self._cur()
        try:
            kind = random.choice(['level_stat', 'tx_trend', 'large_tx'])
            if kind == 'level_stat':
                cur.execute(
                    "SELECT c.cust_level, COUNT(*), SUM(a.balance) "
                    "FROM bsim_account a JOIN bsim_customer c "
                    "ON a.customer_id=c.customer_id GROUP BY c.cust_level")
                _fetchall_dict(cur)
            elif kind == 'tx_trend':
                cur.execute(
                    "SELECT COUNT(*), SUM(amount) FROM bsim_transaction "
                    "WHERE tx_time >= %s",
                    (_dt.datetime.now() - _dt.timedelta(hours=1),))
                cur.fetchone()
            else:
                cur.execute(
                    "SELECT * FROM bsim_transaction "
                    "WHERE amount > %s ORDER BY tx_time DESC",
                    (Decimal('10000'),))
                _fetchall_dict(cur)
            self.stats['ok'] += 1
        except Exception as e:
            self.stats['fail'] += 1
            logger.debug("[%s] report 失败: %s", self.cfg.name, e)
        finally:
            cur.close()

    def _do_batch(self):
        """日终批处理: 贷款利息计提 + 日终汇总。"""
        cur = self._cur()
        try:
            # 利息计提
            cur.execute(
                "SELECT loan_id, principal, rate, accrued_interest "
                "FROM bsim_loan WHERE status='ACTIVE'")
            loans = _fetchall_dict(cur)
            for ln in loans:
                daily = Decimal(str(ln['principal'])) * Decimal(str(ln['rate'])) / Decimal('360')
                new_acc = Decimal(str(ln['accrued_interest'])) + daily
                cur.execute(
                    "UPDATE bsim_loan SET accrued_interest=%s WHERE loan_id=%s",
                    (new_acc, ln['loan_id']))
            # 日终汇总 (简化: 当日交易按账户聚合)
            today = _dt.date.today()
            cur.execute(
                "SELECT account_id, "
                "SUM(CASE WHEN tx_type='DEPOSIT' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN tx_type='DEPOSIT' THEN amount ELSE 0 END), "
                "SUM(CASE WHEN tx_type='WITHDRAW' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN tx_type='WITHDRAW' THEN amount ELSE 0 END) "
                "FROM bsim_transaction WHERE tx_time >= %s GROUP BY account_id",
                (_dt.datetime.combine(today, _dt.time.min),))
            rows = _fetchall_dict(cur)
            for r in rows:
                cur.execute(
                    "SELECT balance FROM bsim_account WHERE account_id=%s",
                    (r['account_id'],))
                b = cur.fetchone()
                bal = b[0] if isinstance(b, (list, tuple)) else list(b.values())[0]
                cur.execute(
                    "INSERT INTO bsim_daily_summary "
                    "(summary_date, account_id, deposit_count, deposit_amount, "
                    "withdraw_count, withdraw_amount, eod_balance) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (today, r['account_id'],
                     r.get(list(r.keys())[1], 0) or 0,
                     r.get(list(r.keys())[2], 0) or 0,
                     r.get(list(r.keys())[3], 0) or 0,
                     r.get(list(r.keys())[4], 0) or 0,
                     bal))
            # 审计日志
            cur.execute(
                "INSERT INTO bsim_audit_log (op_type, op_user, op_time, detail) "
                "VALUES (%s,%s,%s,%s)",
                ('EOD_BATCH', 'simulator', _dt.datetime.now(),
                 f'日终批处理: 利息计提 {len(loans)} 笔, 汇总 {len(rows)} 账户'))
            self.conn.commit()
            self.stats['ok'] += 1
        except Exception as e:
            try: self.conn.rollback()
            except Exception: pass
            self.stats['fail'] += 1
            logger.debug("[%s] batch 失败: %s", self.cfg.name, e)
        finally:
            cur.close()

    def _do_ddl(self):
        """偶发 DDL: 建/删索引, 写审计。"""
        cur = self._cur()
        try:
            # 探测 idx_bsim_tx_time 是否存在
            exists = False
            try:
                if self.db_type in ('mysql', 'tdsql', 'gbase'):
                    cur.execute("SHOW INDEX FROM bsim_transaction WHERE Key_name='idx_bsim_tx_time'")
                    exists = bool(cur.fetchall())
                elif self.db_type in ('pgsql', 'postgresql'):
                    cur.execute(
                        "SELECT 1 FROM pg_indexes WHERE indexname='idx_bsim_tx_time'")
                    exists = bool(cur.fetchone())
                elif self.db_type == 'oracle':
                    cur.execute(
                        "SELECT 1 FROM user_indexes WHERE index_name='IDX_BSIM_TX_TIME'")
                    exists = bool(cur.fetchone())
            except Exception:
                exists = False

            if exists:
                for sql in self.dialect.drop_indexes():
                    self._exec_ddl_safe(sql, ignore_exists=False)
                op = 'DROP INDEX idx_bsim_tx_time'
            else:
                for sql in self.dialect.indexes():
                    if 'idx_bsim_tx_time' in sql:
                        self._exec_ddl_safe(sql, ignore_exists=True)
                        break
                op = 'CREATE INDEX idx_bsim_tx_time'

            cur.execute(
                "INSERT INTO bsim_audit_log (op_type, op_user, op_time, detail) "
                "VALUES (%s,%s,%s,%s)",
                ('SCHEMA_CHANGE', 'simulator', _dt.datetime.now(), op))
            self.conn.commit()
            self.stats['ddl'] += 1
        except Exception as e:
            try: self.conn.rollback()
            except Exception: pass
            logger.debug("[%s] ddl 失败: %s", self.cfg.name, e)
        finally:
            cur.close()

    # ------------------------------------------------------------------ 调度
    def _pick_task(self):
        """按权重随机选任务, 返回 (task_name, interval_range)。"""
        total = sum(w for _, _, _, w in self.SCHEDULE)
        r = random.randint(1, total)
        acc = 0
        for name, lo, hi, w in self.SCHEDULE:
            acc += w
            if r <= acc:
                return name, (lo, hi)
        return 'teller', (3, 8)

    def run(self):
        """主循环。"""
        tag = f"[{self.cfg.name}]"
        logger.info("%s worker 启动 (db_type=%s, dry_run=%s)",
                    tag, self.db_type, self.dry_run)
        try:
            self._ensure_conn()
        except Exception as e:
            logger.error("%s 首次连接失败: %s", tag, e)
            return
        try:
            self.setup_schema()
            self.seed()
        except Exception as e:
            logger.error("%s 初始化失败: %s", tag, e)
            self._close()
            return

        if self.dry_run:
            logger.info("%s dry-run 模式, 跳过调度循环", tag)
            self._close()
            return

        last_stats_log = time.time()
        while not self.stop.is_set():
            task, (lo, hi) = self._pick_task()
            try:
                if task == 'teller':   self._do_teller()
                elif task == 'transfer': self._do_transfer()
                elif task == 'report':   self._do_report()
                elif task == 'batch':    self._do_batch()
                elif task == 'ddl':      self._do_ddl()
            except Exception as e:
                logger.warning("%s 任务 %s 异常: %s\n%s",
                               tag, task, e, traceback.format_exc())
                # 连接可能坏了, 尝试重连
                try:
                    self._reconnect()
                except Exception as ee:
                    logger.error("%s 重连失败: %s, 等待 30s 后重试", tag, ee)
                    self.stop.wait(30)
                    continue

            # 每 30s 打印一次汇总
            if time.time() - last_stats_log >= 30:
                logger.info("%s ok=%s fail=%s ddl=%s",
                            tag, self.stats['ok'], self.stats['fail'], self.stats['ddl'])
                last_stats_log = time.time()

            # 抖动等待
            self.stop.wait(random.uniform(lo, hi))

        logger.info("%s worker 退出 (ok=%s fail=%s ddl=%s)",
                    tag, self.stats['ok'], self.stats['fail'], self.stats['ddl'])
        self._close()
