# 银行业务模拟器 · 详细设计方案

## 1. 背景与目标

当前 DB-AIOps 项目纳管了 6 个测试数据库，但库中数据长期静态，开发与测试缺乏"活的业务压力"作为诊断依据。本方案以**银行业务**为蓝本，对每个纳管库持续注入"查询 + DML + 偶发 DDL"的混合负载，让库在开发/测试期间始终有 SQL 在跑，从而：

- 监控模块（Phase 1-7）能采集到真实的 QPS、活跃会话、锁等待、慢查询
- 智能引擎（Phase 8）能基于持续变化的指标做基线学习、异常检测、根因分析
- 测试团队能验证告警规则、自治策略在"有压力"的场景下是否生效

## 2. 纳管库盘点

| ID | 名称 | 类型 | 地址 | 可用性 | 备注 |
|----|------|------|------|--------|------|
| 1 | MySQL测试库 | mysql | 127.0.0.1:3306 | ✅ 本地 | 主模拟目标 |
| 2 | PostgreSQL测试库 | pgsql | 127.0.0.1:5433 | ✅ 本地 | 主模拟目标 |
| 3 | Oracle XE测试库 | oracle | 127.0.0.1:1521 (XEPDB1) | ✅ 本地 | 主模拟目标 |
| 4 | GBase 8A 测试库 | gbase | 127.0.0.1:5258 (gbase) | ✅ 本地 | 主模拟目标 |
| 6 | TDSQL集中式测试库 | tdsql | 119.45.220.89:15002 | ⚠️ 远程 | 网络可达时纳入 |
| 7 | TDSQL分布式测试库 | tdsql | 119.45.220.89:15005 | ⚠️ 远程 | 网络可达时纳入 |

**策略**：启动时对每个库先做 `test_connection`，不可达的库打印 WARN 并跳过，不阻塞其它库的模拟。

## 3. 整体架构

```
┌──────────────────────────────────────────────────────┐
│  manage.py run_bank_simulator                        │
│  ├─ 主线程: 调度/信号/日志                            │
│  └─ 每库一个 worker 线程                              │
│       ├─ Phase A: DDL 建表 (幂等)                    │
│       ├─ Phase B: 种子数据 (幂等)                    │
│       └─ Phase C: 调度循环                            │
│            ├─ 柜面交易 (INSERT/UPDATE) 3~8s/笔        │
│            ├─ 内部转账 (事务) 5~15s/笔                │
│            ├─ 报表查询 (SELECT) 10~30s/次             │
│            ├─ 日终批处理 (利息/手续费) 60s/次          │
│            └─ 偶发 DDL (加索引/加字段/删索引) 300s/次  │
└──────────────────────────────────────────────────────┘
           │
           ▼
     logs/bank_simulator.log (每库独立 logger)
```

- **单进程多线程**，每库独立连接、独立调度、独立异常恢复
- 所有 SQL 通过 `monitor.db_connector.DbConnector` 复用现有连接逻辑（含密码解密、TDSQL 重试）
- 每个 worker 独立维护自己的连接，异常时重连
- 支持 `Ctrl+C` 优雅退出（`threading.Event` 作为 stop 信号）

## 4. 银行业务 Schema（每库统一）

> 表名前缀 `bsim_`（bank simulator）避免与业务表冲突；所有表按"先 DROP 再 CREATE"或"CREATE IF NOT EXISTS"保证幂等。

### 4.1 客户表 `bsim_customer`

| 列 | 类型 | 说明 |
|----|------|------|
| customer_id | BIGINT PK AUTO | 客户 ID |
| customer_no | VARCHAR(20) UNIQUE | 客户号（如 C2026080001） |
| name | VARCHAR(64) | 姓名 |
| id_type | TINYINT | 证件类型 1-身份证 2-护照 |
| id_no | VARCHAR(32) | 证件号 |
| mobile | VARCHAR(20) | 手机号 |
| level | VARCHAR(16) | 客户等级 NORMAL/VIP/PRIVATE |
| created_at | DATETIME / TIMESTAMP | 开户时间 |

### 4.2 账户表 `bsim_account`

| 列 | 类型 | 说明 |
|----|------|------|
| account_id | BIGINT PK AUTO | 账户 ID |
| account_no | VARCHAR(24) UNIQUE | 账号 |
| customer_id | BIGINT FK | 所属客户 |
| account_type | VARCHAR(16) | SAVINGS / CHECKING / CREDIT |
| currency | VARCHAR(3) | CNY |
| balance | DECIMAL(18,2) | 余额 |
| available_balance | DECIMAL(18,2) | 可用余额 |
| status | TINYINT | 1-正常 2-冻结 3-销户 |
| opened_at | DATETIME | 开户时间 |
| updated_at | DATETIME | 最近变动 |

### 4.3 交易流水 `bsim_transaction`

| 列 | 类型 | 说明 |
|----|------|------|
| tx_id | BIGINT PK AUTO | 流水 ID |
| tx_no | VARCHAR(32) UNIQUE | 交易流水号 |
| account_id | BIGINT | 账户 |
| tx_type | VARCHAR(16) | DEPOSIT / WITHDRAW / TRANSFER_OUT / TRANSFER_IN / FEE / INTEREST |
| amount | DECIMAL(18,2) | 金额 |
| balance_after | DECIMAL(18,2) | 交易后余额 |
| channel | VARCHAR(16) | COUNTER / ATM / MOBILE / WEB / BATCH |
| counterparty_account | VARCHAR(24) NULL | 对手账号 |
| remark | VARCHAR(128) | 摘要 |
| tx_time | DATETIME | 交易时间 |

### 4.4 贷款表 `bsim_loan`

| 列 | 类型 | 说明 |
|----|------|------|
| loan_id | BIGINT PK AUTO | 贷款 ID |
| loan_no | VARCHAR(32) UNIQUE | 借据号 |
| account_id | BIGINT | 放款账户 |
| principal | DECIMAL(18,2) | 本金 |
| rate | DECIMAL(8,4) | 年利率 |
| start_date | DATE | 起息日 |
| term_days | INT | 期限（天） |
| accrued_interest | DECIMAL(18,2) | 已计提利息 |
| status | VARCHAR(16) | ACTIVE / SETTLED / OVERDUE |

### 4.5 日终汇总 `bsim_daily_summary`

| 列 | 类型 | 说明 |
|----|------|------|
| summary_id | BIGINT PK AUTO | 汇总 ID |
| summary_date | DATE | 业务日期 |
| account_id | BIGINT | 账户 |
| deposit_count | INT | 存入笔数 |
| deposit_amount | DECIMAL(18,2) | 存入金额 |
| withdraw_count | INT | 支取笔数 |
| withdraw_amount | DECIMAL(18,2) | 支取金额 |
| eod_balance | DECIMAL(18,2) | 日终余额 |

### 4.6 审计日志 `bsim_audit_log`

| 列 | 类型 | 说明 |
|----|------|------|
| log_id | BIGINT PK AUTO | 日志 ID |
| op_type | VARCHAR(32) | 操作类型 |
| op_user | VARCHAR(32) | 操作人 |
| op_time | DATETIME | 操作时间 |
| detail | VARCHAR(512) | 详情 |

## 5. 交易模板（每类数据库一套方言）

### 5.1 柜面交易（高频 DML）

- **存款**：`UPDATE bsim_account SET balance=balance+?, available_balance=available_balance+?, updated_at=NOW() WHERE account_id=? AND status=1`
  随后 `INSERT INTO bsim_transaction ...`
- **取款**：先 `SELECT balance, available_balance FROM bsim_account WHERE account_id=? FOR UPDATE`，余额足则 UPDATE + INSERT
- **手续费扣收**：UPDATE 账户余额 + INSERT 交易流水（tx_type='FEE'）

### 5.2 内部转账（事务）

```sql
BEGIN;
  UPDATE bsim_account SET balance=balance-?, updated_at=NOW() WHERE account_id=? AND status=1;
  UPDATE bsim_account SET balance=balance+?, updated_at=NOW() WHERE account_id=? AND status=1;
  INSERT INTO bsim_transaction (tx_type='TRANSFER_OUT', ...);
  INSERT INTO bsim_transaction (tx_type='TRANSFER_IN', ...);
COMMIT;
```

### 5.3 报表查询（只读）

- 客户维度：`SELECT level, COUNT(*), SUM(balance) FROM bsim_account a JOIN bsim_customer c ... GROUP BY level`
- 交易趋势：`SELECT DATE(tx_time), COUNT(*), SUM(amount) FROM bsim_transaction WHERE tx_time > ? GROUP BY 1`
- 大额交易：`SELECT * FROM bsim_transaction WHERE amount > ? ORDER BY tx_time DESC LIMIT 50`

### 5.4 日终批处理

- 贷款利息计提：`SELECT loan_id, principal, rate FROM bsim_loan WHERE status='ACTIVE'`，按日计息 `principal*rate/360`，回写 accrued_interest
- 日终汇总：按账户聚合当日交易写入 `bsim_daily_summary`

### 5.5 偶发 DDL（低频，制造 schema 变更事件供 Phase 8D 捕获）

- `CREATE INDEX idx_bsim_tx_time ON bsim_transaction(tx_time)`
- `ALTER TABLE bsim_audit_log ADD COLUMN batch_id VARCHAR(32)`（仅首次，捕获已存在异常）
- `DROP INDEX idx_bsim_tx_time`（与 CREATE 配对，周期性执行）

> Oracle 用 `CREATE SEQUENCE ...` + `:new.col` 触发器模拟自增；PostgreSQL 用 `SERIAL`；MySQL/GBase/TDSQL 用 `AUTO_INCREMENT`。方言差异集中在 DDL，DML 通过参数化 SQL 统一。

## 6. 调度策略

每个 worker 内部使用 `random.uniform` 抖动间隔，避免节奏整齐：

| 任务 | 间隔 | 权重 | 说明 |
|------|------|------|------|
| 柜面交易 | 3~8s | 60% | 随机挑一类（存/取/费） |
| 内部转账 | 5~15s | 20% | 事务 |
| 报表查询 | 10~30s | 15% | 只读 |
| 日终批处理 | 60s | 4% | 利息计提 + 汇总 |
| 偶发 DDL | 300s | 1% | 建/删索引、加字段 |

**节流**：每库 QPS 上限 ~5，避免把测试库打挂；单条 SQL 超时 10s 后 rollback。

## 7. 管理命令 `run_bank_simulator`

位置：`monitor/management/commands/run_bank_simulator.py`

```
用法:
  ./manage.py run_bank_simulator                    # 跑全部活跃库
  ./manage.py run_bank_simulator --db-ids 1,2,3     # 指定库
  ./manage.py run_bank_simulator --dry-run          # 仅建表+种子, 不启动调度
  ./manage.py run_bank_simulator --duration 3600    # 运行 N 秒后退出 (默认一直跑)
```

实现要点：
- 读取 `DatabaseConfig.objects.filter(is_active=True)`
- 每库启动一个 `BankWorker` 线程，线程内维护自己的连接
- 主线程监听 `SIGINT/SIGTERM`，设置 `stop_event`，worker 检测后优雅退出
- 日志：`logs/bank_simulator.log`（统一），同时每库一个 `logs/bank_simulator_<db_type>.log`

## 8. 可观测性

- 每执行 100 笔交易打印一次汇总：`[MySQL测试库] tx=100 ok=99 fail=1 last=32ms`
- 异常 SQL 打印完整语句 + 错误码（脱敏密码）
- 启动/退出各打印一次 banner
- 日志接入 `logging.getLogger('bank_simulator')`，复用项目 `LOGGING` 配置

## 9. 不可用库处理

- 启动时 `test_connection`，失败的库记录 WARN 并跳过
- 运行中连接断开，worker 自动重连（最多 3 次，间隔指数退避）
- 所有库都不可达时，命令以非 0 退出码退出

## 10. 验收标准

- [ ] 4 个本地库（MySQL/PG/Oracle/GBase）启动后 30s 内开始持续产出 SQL
- [ ] 每库至少完成 50 笔柜面交易 + 10 笔转账 + 5 次报表查询 + 1 次批处理
- [ ] 日志中能看到 DDL 变更事件
- [ ] Ctrl+C 后所有 worker 在 5s 内退出，无残留连接
- [ ] 远程 TDSQL 库：网络可达时自动纳入；不可达时打印 WARN 不影响其它库
- [ ] 监控页面能看到对应库的 QPS/活跃会话上升
