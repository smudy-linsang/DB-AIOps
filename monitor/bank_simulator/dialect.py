# -*- coding: utf-8 -*-
"""银行业务模拟器 - 多方言 SQL 适配。

支持: mysql / pgsql / oracle (gbase/tdsql 走 mysql 方言)。
DDL 差异集中在本模块; DML 通过参数化 SQL 统一。

约定:
  - 客户等级列统一命名 cust_level (Oracle 中 level 是保留字, 故改名)
  - 所有表名/列名小写, 避免 Oracle 大小写敏感问题
  - 参数占位符: mysql/tdsql/gbase/pgsql 用 %s; oracle 用 :name
"""
import logging

logger = logging.getLogger('bank_simulator')


class Dialect:
    """方言基类。"""
    name = 'base'
    param_style = '%s'
    auto_inc = 'BIGINT AUTO_INCREMENT PRIMARY KEY'
    now_fn = 'NOW()'
    date_fn = 'CURDATE()'
    clob_type = 'TEXT'
    decimal = 'DECIMAL(18,2)'
    # Oracle 用 :name 占位
    use_named_params = False

    def ddl_customer(self):
        return f"""
        CREATE TABLE IF NOT EXISTS bsim_customer (
            customer_id   {self.auto_inc},
            customer_no   VARCHAR(20) NOT NULL,
            name          VARCHAR(64) NOT NULL,
            id_type       TINYINT NOT NULL DEFAULT 1,
            id_no         VARCHAR(32),
            mobile        VARCHAR(20),
            cust_level    VARCHAR(16) NOT NULL DEFAULT 'NORMAL',
            created_at    DATETIME NOT NULL
        )
        """

    def ddl_account(self):
        return f"""
        CREATE TABLE IF NOT EXISTS bsim_account (
            account_id        {self.auto_inc},
            account_no        VARCHAR(24) NOT NULL,
            customer_id       BIGINT NOT NULL,
            account_type      VARCHAR(16) NOT NULL,
            currency          VARCHAR(3) NOT NULL DEFAULT 'CNY',
            balance           {self.decimal} NOT NULL DEFAULT 0,
            available_balance {self.decimal} NOT NULL DEFAULT 0,
            status            TINYINT NOT NULL DEFAULT 1,
            opened_at         DATETIME NOT NULL,
            updated_at        DATETIME NOT NULL
        )
        """

    def ddl_transaction(self):
        return f"""
        CREATE TABLE IF NOT EXISTS bsim_transaction (
            tx_id                {self.auto_inc},
            tx_no                VARCHAR(32) NOT NULL,
            account_id           BIGINT NOT NULL,
            tx_type              VARCHAR(16) NOT NULL,
            amount               {self.decimal} NOT NULL,
            balance_after        {self.decimal} NOT NULL,
            channel              VARCHAR(16) NOT NULL,
            counterparty_account VARCHAR(24),
            remark               VARCHAR(128),
            tx_time              DATETIME NOT NULL
        )
        """

    def ddl_loan(self):
        return f"""
        CREATE TABLE IF NOT EXISTS bsim_loan (
            loan_id           {self.auto_inc},
            loan_no           VARCHAR(32) NOT NULL,
            account_id        BIGINT NOT NULL,
            principal         {self.decimal} NOT NULL,
            rate              DECIMAL(8,4) NOT NULL,
            start_date        DATE NOT NULL,
            term_days         INT NOT NULL,
            accrued_interest  {self.decimal} NOT NULL DEFAULT 0,
            status            VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
        )
        """

    def ddl_daily_summary(self):
        return f"""
        CREATE TABLE IF NOT EXISTS bsim_daily_summary (
            summary_id       {self.auto_inc},
            summary_date     DATE NOT NULL,
            account_id       BIGINT NOT NULL,
            deposit_count    INT NOT NULL DEFAULT 0,
            deposit_amount   {self.decimal} NOT NULL DEFAULT 0,
            withdraw_count   INT NOT NULL DEFAULT 0,
            withdraw_amount  {self.decimal} NOT NULL DEFAULT 0,
            eod_balance      {self.decimal} NOT NULL DEFAULT 0
        )
        """

    def ddl_audit_log(self):
        return f"""
        CREATE TABLE IF NOT EXISTS bsim_audit_log (
            log_id     {self.auto_inc},
            op_type    VARCHAR(32) NOT NULL,
            op_user    VARCHAR(32) NOT NULL,
            op_time    DATETIME NOT NULL,
            detail     {self.clob_type}
        )
        """

    def all_ddl(self):
        return [
            self.ddl_customer(),
            self.ddl_account(),
            self.ddl_transaction(),
            self.ddl_loan(),
            self.ddl_daily_summary(),
            self.ddl_audit_log(),
        ]

    def indexes(self):
        return [
            "CREATE INDEX idx_bsim_acct_cust ON bsim_account(customer_id)",
            "CREATE INDEX idx_bsim_tx_acct   ON bsim_transaction(account_id)",
            "CREATE INDEX idx_bsim_tx_time   ON bsim_transaction(tx_time)",
            "CREATE INDEX idx_bsim_loan_acct ON bsim_loan(account_id)",
        ]

    def drop_indexes(self):
        return [
            "DROP INDEX idx_bsim_tx_time",
        ]


class MySQLDialect(Dialect):
    name = 'mysql'


class PgSQLDialect(Dialect):
    name = 'pgsql'
    auto_inc = 'BIGSERIAL PRIMARY KEY'
    now_fn = 'CURRENT_TIMESTAMP'
    date_fn = 'CURRENT_DATE'
    clob_type = 'TEXT'

    def ddl_customer(self):
        return """
        CREATE TABLE IF NOT EXISTS bsim_customer (
            customer_id   BIGSERIAL PRIMARY KEY,
            customer_no   VARCHAR(20) NOT NULL UNIQUE,
            name          VARCHAR(64) NOT NULL,
            id_type       SMALLINT NOT NULL DEFAULT 1,
            id_no         VARCHAR(32),
            mobile        VARCHAR(20),
            cust_level    VARCHAR(16) NOT NULL DEFAULT 'NORMAL',
            created_at    TIMESTAMP NOT NULL
        )
        """

    def ddl_account(self):
        return """
        CREATE TABLE IF NOT EXISTS bsim_account (
            account_id        BIGSERIAL PRIMARY KEY,
            account_no        VARCHAR(24) NOT NULL UNIQUE,
            customer_id       BIGINT NOT NULL,
            account_type      VARCHAR(16) NOT NULL,
            currency          VARCHAR(3) NOT NULL DEFAULT 'CNY',
            balance           NUMERIC(18,2) NOT NULL DEFAULT 0,
            available_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
            status            SMALLINT NOT NULL DEFAULT 1,
            opened_at         TIMESTAMP NOT NULL,
            updated_at        TIMESTAMP NOT NULL
        )
        """

    def ddl_transaction(self):
        return """
        CREATE TABLE IF NOT EXISTS bsim_transaction (
            tx_id                BIGSERIAL PRIMARY KEY,
            tx_no                VARCHAR(32) NOT NULL,
            account_id           BIGINT NOT NULL,
            tx_type              VARCHAR(16) NOT NULL,
            amount               NUMERIC(18,2) NOT NULL,
            balance_after        NUMERIC(18,2) NOT NULL,
            channel              VARCHAR(16) NOT NULL,
            counterparty_account VARCHAR(24),
            remark               VARCHAR(128),
            tx_time              TIMESTAMP NOT NULL
        )
        """

    def ddl_loan(self):
        return """
        CREATE TABLE IF NOT EXISTS bsim_loan (
            loan_id           BIGSERIAL PRIMARY KEY,
            loan_no           VARCHAR(32) NOT NULL,
            account_id        BIGINT NOT NULL,
            principal         NUMERIC(18,2) NOT NULL,
            rate              NUMERIC(8,4) NOT NULL,
            start_date        DATE NOT NULL,
            term_days         INT NOT NULL,
            accrued_interest  NUMERIC(18,2) NOT NULL DEFAULT 0,
            status            VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
        )
        """

    def ddl_daily_summary(self):
        return """
        CREATE TABLE IF NOT EXISTS bsim_daily_summary (
            summary_id       BIGSERIAL PRIMARY KEY,
            summary_date     DATE NOT NULL,
            account_id       BIGINT NOT NULL,
            deposit_count    INT NOT NULL DEFAULT 0,
            deposit_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,
            withdraw_count   INT NOT NULL DEFAULT 0,
            withdraw_amount  NUMERIC(18,2) NOT NULL DEFAULT 0,
            eod_balance      NUMERIC(18,2) NOT NULL DEFAULT 0
        )
        """

    def ddl_audit_log(self):
        return """
        CREATE TABLE IF NOT EXISTS bsim_audit_log (
            log_id     BIGSERIAL PRIMARY KEY,
            op_type    VARCHAR(32) NOT NULL,
            op_user    VARCHAR(32) NOT NULL,
            op_time    TIMESTAMP NOT NULL,
            detail     TEXT
        )
        """


class OracleDialect(Dialect):
    name = 'oracle'
    auto_inc = 'NUMBER GENERATED BY DEFAULT ON NULL AS IDENTITY PRIMARY KEY'
    now_fn = 'SYSTIMESTAMP'
    date_fn = 'TRUNC(SYSTIMESTAMP)'
    clob_type = 'CLOB'
    use_named_params = True
    param_style = ':1'

    def ddl_customer(self):
        return """
        CREATE TABLE bsim_customer (
            customer_id   NUMBER GENERATED BY DEFAULT ON NULL AS IDENTITY PRIMARY KEY,
            customer_no   VARCHAR2(20) NOT NULL UNIQUE,
            name          VARCHAR2(64) NOT NULL,
            id_type       NUMBER(3) DEFAULT 1 NOT NULL,
            id_no         VARCHAR2(32),
            mobile        VARCHAR2(20),
            cust_level    VARCHAR2(16) DEFAULT 'NORMAL' NOT NULL,
            created_at    TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
        )
        """

    def ddl_account(self):
        return """
        CREATE TABLE bsim_account (
            account_id        NUMBER GENERATED BY DEFAULT ON NULL AS IDENTITY PRIMARY KEY,
            account_no        VARCHAR2(24) NOT NULL UNIQUE,
            customer_id       NUMBER NOT NULL,
            account_type      VARCHAR2(16) NOT NULL,
            currency          VARCHAR2(3) DEFAULT 'CNY' NOT NULL,
            balance           NUMBER(18,2) DEFAULT 0 NOT NULL,
            available_balance NUMBER(18,2) DEFAULT 0 NOT NULL,
            status            NUMBER(3) DEFAULT 1 NOT NULL,
            opened_at         TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            updated_at        TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
        )
        """

    def ddl_transaction(self):
        return """
        CREATE TABLE bsim_transaction (
            tx_id                NUMBER GENERATED BY DEFAULT ON NULL AS IDENTITY PRIMARY KEY,
            tx_no                VARCHAR2(32) NOT NULL,
            account_id           NUMBER NOT NULL,
            tx_type              VARCHAR2(16) NOT NULL,
            amount               NUMBER(18,2) NOT NULL,
            balance_after        NUMBER(18,2) NOT NULL,
            channel              VARCHAR2(16) NOT NULL,
            counterparty_account VARCHAR2(24),
            remark               VARCHAR2(128),
            tx_time              TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
        )
        """

    def ddl_loan(self):
        return """
        CREATE TABLE bsim_loan (
            loan_id           NUMBER GENERATED BY DEFAULT ON NULL AS IDENTITY PRIMARY KEY,
            loan_no           VARCHAR2(32) NOT NULL,
            account_id        NUMBER NOT NULL,
            principal         NUMBER(18,2) NOT NULL,
            rate              NUMBER(8,4) NOT NULL,
            start_date        DATE NOT NULL,
            term_days         NUMBER NOT NULL,
            accrued_interest  NUMBER(18,2) DEFAULT 0 NOT NULL,
            status            VARCHAR2(16) DEFAULT 'ACTIVE' NOT NULL
        )
        """

    def ddl_daily_summary(self):
        return """
        CREATE TABLE bsim_daily_summary (
            summary_id       NUMBER GENERATED BY DEFAULT ON NULL AS IDENTITY PRIMARY KEY,
            summary_date     DATE NOT NULL,
            account_id       NUMBER NOT NULL,
            deposit_count    NUMBER DEFAULT 0 NOT NULL,
            deposit_amount   NUMBER(18,2) DEFAULT 0 NOT NULL,
            withdraw_count   NUMBER DEFAULT 0 NOT NULL,
            withdraw_amount  NUMBER(18,2) DEFAULT 0 NOT NULL,
            eod_balance      NUMBER(18,2) DEFAULT 0 NOT NULL
        )
        """

    def ddl_audit_log(self):
        return """
        CREATE TABLE bsim_audit_log (
            log_id     NUMBER GENERATED BY DEFAULT ON NULL AS IDENTITY PRIMARY KEY,
            op_type    VARCHAR2(32) NOT NULL,
            op_user    VARCHAR2(32) NOT NULL,
            op_time    TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            detail     CLOB
        )
        """

    def indexes(self):
        return [
            "CREATE INDEX idx_bsim_acct_cust ON bsim_account(customer_id)",
            "CREATE INDEX idx_bsim_tx_acct   ON bsim_transaction(account_id)",
            "CREATE INDEX idx_bsim_tx_time   ON bsim_transaction(tx_time)",
            "CREATE INDEX idx_bsim_loan_acct ON bsim_loan(account_id)",
        ]

    def drop_indexes(self):
        return ["DROP INDEX idx_bsim_tx_time"]

    def all_ddl(self):
        # Oracle 不支持 CREATE TABLE IF NOT EXISTS, 去掉该子句,
        # 由 worker 捕获 ORA-00955 实现幂等。
        return [s.replace(' IF NOT EXISTS', '') for s in super().all_ddl()]


def get_dialect(db_type: str) -> Dialect:
    t = (db_type or '').lower()
    if t in ('mysql', 'tdsql', 'gbase'):
        return MySQLDialect()
    if t in ('pgsql', 'postgresql'):
        return PgSQLDialect()
    if t == 'oracle':
        return OracleDialect()
    raise ValueError(f"不支持的数据库类型: {db_type}")
