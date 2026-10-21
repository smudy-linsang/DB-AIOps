# TDSQL 慢 SQL 能力 —— 内网测试指南

> 对应：`TDSQL_SLOWSQL_REVIEW.md`（问题 T-01 ~ T-16）、`TDSQL_SLOWSQL_DESIGN.md`（实现设计，§8 验证清单）。
> 本轮实现完全依据 TDSQL MySQL 版官方文档开发，开发环境只有原生 MySQL 8.0 与 PostgreSQL 16。
> **凡是与 proxy 行为相关的假设，都只能在内网真实 TDSQL 上确认**，本指南就是为这一步准备的。

整体分三步，约 2 小时（不含周期 SQL 的多日观察）：

| 步骤 | 内容 | 耗时 | 产出 |
|---|---|---|---|
| 1 | 跑只读探针 `scripts/tdsql_probe.py` | 1 分钟 | 结论表 / JSON —— **最重要，请务必发回** |
| 2 | 建测试库、灌数（`scripts/tdsql_workload.py`） | 10 ~ 20 分钟 | 测试表 `aiops_probe_shop.orders` |
| 3 | 按 §4 的场景跑负载，对照 §5 检查平台 | 约 90 分钟 | 截图 / 接口 JSON |

---

## 1. 准备

### 1.1 环境

- **请使用测试实例，不要用生产实例。** 集中式（noshard）与分布式（groupshard）各一套最好；只有一套也可以，先测分布式（风险点更多）。
- 平台部署本分支代码后执行 `python manage.py migrate`。时序库 `sql_stat` 表新增的 12 列在进程启动时会以 `ADD COLUMN IF NOT EXISTS` 自动补齐，无需手工 DDL。

### 1.2 两个账号（按凭据通道约定，只写进 `.env` 或纳管界面，不要贴到任何对话 / 工单里）

| 账号 | 用途 | 权限 |
|---|---|---|
| 监控账号 | 平台纳管、探针 | `SELECT`、`PROCESS`；`performance_schema` 的 `SELECT` |
| 负载账号 | 只用于测试库 `aiops_probe_shop` | 该库上的 `ALL`（建表、灌数、加删索引） |

监控账号**不应**有业务库的写权限。探针 V-7 在没有写权限时会记为"无法判定"，属于正常结果。

---

## 2. 平台配置

### 2.1 `.env` 新增项（均已登记到 `monitor/appconf.py`，不配则用默认值）

```bash
# EXPLAIN ANALYZE 会在目标库真实执行查询（只读事务 + 超时保护），默认关闭；测试时打开
PERF_EXPLAIN_ANALYZE_ENABLED=true
PERF_EXPLAIN_ANALYZE_TIMEOUT_MS=5000

# 慢 SQL 阈值（平均耗时，ms）。测试负载的慢查询约几十到上百 ms，测试期间调低，否则看不到"慢"标记与告警
SLOW_SQL_AVG_MS=50

# 以下一般保持默认
# SQLSTAT_TOPN=200               每个 set 每轮写入的 digest 上限
# SQLSTAT_FETCH_LIMIT=500        每个 set 每轮拉取的活跃 digest 上限
# SQLSTAT_ACTIVE_WINDOW_SEC=900  应 >= 2 倍采集间隔
# SQL_REGRESSION_AVG_RATIO=1.5
# SQL_REGRESSION_MIN_DELTA_MS=5
```

改完重启 `runserver` 与 `start_monitor`。

### 2.2 开启 CPU 时间采集（可选，但强烈建议）

「耗时构成」里的 CPU 拆分依赖 `performance_schema` 的 `events_statements_cpu` consumer，MySQL 8 默认关闭。
不开也能用，只是 CPU 会显示"未采集"，而不会被误算成 100% CPU（这是 T-07 的修复点）。

请 DBA 在**每个 set** 上开启（运行期生效，重启失效）：

```sql
/*sets:allsets*/UPDATE performance_schema.setup_consumers SET ENABLED = 'YES' WHERE NAME = 'events_statements_cpu';
```

持久化需要实例参数 `performance-schema-consumer-events-statements-cpu=ON`（是否能在 TDSQL 管控台修改，请 DBA 确认）。
开销：官方说明为语句级 CPU 计时，开销很小；测试结束可改回 `'NO'`。

---

## 3. 第一步：只读探针（V-1 ~ V-9）

```bash
export TDSQL_PROBE_HOST=...  TDSQL_PROBE_PORT=...  TDSQL_PROBE_USER=...  TDSQL_PROBE_PASSWORD=...
.venv/bin/python scripts/tdsql_probe.py --json tdsql_probe_result.json
```

- 全程只读；V-7 用"只读事务 + `WHERE 1=0` 的 UPDATE + ROLLBACK"，即使只读不生效也不会改动任何行；V-8 只执行 `SELECT SLEEP(3)`。
- 输出不打印主机、端口、账号、密码，节点地址默认打码；`tdsql_probe_result.json` 可以直接发回。
- 退出码：有 FAIL 为 1，否则为 0。

### 3.1 每一项不通过意味着什么

| 项 | 验证 | 不通过时平台的行为 | 需要我们做什么 |
|---|---|---|---|
| V-1 | `/*sets:allsets*/` 透传且带 `info` 列 | 改为逐 set 透传；再不行降级为"未按 set 采集"，页面出现红色"数据不可信"提示 | 发回结论表，可能要调整透传语法 |
| V-2 | 透传查 digest 表 | 同上 | 同上 |
| V-3 | `SUM_CPU_TIME` / `QUERY_SAMPLE_TEXT` 等扩展列，CPU consumer | 自动只采基础列；CPU 显示"未采集"；EXPLAIN ANALYZE 缺少字面量样例时要求手工粘贴 SQL | 一般无需改代码 |
| V-4 | 透传 `SHOW GLOBAL STATUS` | 分布式实例的 QPS / TPS 不再按 set 求和，并标注口径 | 发回结论表 |
| V-5 | 指定单个 set 透传 | EXPLAIN ANALYZE 无法指定 set | 发回结论表 |
| V-6 | 单 set 透传 EXPLAIN ANALYZE | 「执行 EXPLAIN ANALYZE」按钮报错 | 发回报错原文 |
| V-7 | 只读事务经 proxy 生效 | —— | **FAIL 时请不要开启 `PERF_EXPLAIN_ANALYZE_ENABLED`**，并发回 |
| V-8 | `MAX_EXECUTION_TIME` 经 proxy 生效 | EXPLAIN ANALYZE 只能靠连接级超时兜底 | 发回耗时 |
| V-9 | 读写分离时透传落主 | 若落备机，统计只代表备机 | 发回结论表 |

---

## 4. 第二、三步：端到端负载（V-10）

先在平台纳管该实例（监控账号），确认采集正常（实例概览有数据）。然后：

```bash
export TDSQL_WL_HOST=...  TDSQL_WL_PORT=...  TDSQL_WL_USER=...  TDSQL_WL_PASSWORD=...
P=.venv/bin/python

# 建库灌数（40 万行；分布式实例加 --groupshard，按官方语法建 shardkey=user_id 的分片表）
$P scripts/tdsql_workload.py load --groupshard

# 场景 A（30 分钟）：有索引的"健康期"，作为环比基线
$P scripts/tdsql_workload.py index add
$P scripts/tdsql_workload.py run --minutes 30 --qps 5

# 场景 B（30 分钟）：删掉索引，人为制造一次退化
$P scripts/tdsql_workload.py index drop
$P scripts/tdsql_workload.py run --minutes 30 --qps 5      # 期间另开终端执行下一行
$P scripts/tdsql_workload.py lockwait --seconds 20        # 制造一次行锁等待

# 测试结束
$P scripts/tdsql_workload.py drop
```

负载里的四类查询（`run` 结束时会打印客户端侧的次数与平均耗时，用于和平台对账）：

| 名称 | SQL 形态 | 预期 |
|---|---|---|
| `slow_user_sort` | `WHERE user_id = ? ORDER BY created_at DESC LIMIT 10` | 场景 B 中全表扫描 + filesort，是最慢的 SQL；带分片键，只落一个 set |
| `agg_no_shardkey` | `GROUP BY status` 全表聚合 | 无分片键 → 广播到全部 set |
| `range_no_key` | `created_at >= NOW() - INTERVAL ? HOUR` | 无分片键、无索引 |
| `pk_lookup` | `WHERE id = ? AND user_id = ?` | 执行次数最多、最快；**不应排在耗时第一**（这是 T-02 的修复点） |

---

## 5. 检查清单（对应最初的四个问题）

接口均需登录态：`POST /api/v1/auth/login/` 取 token 后加 `Authorization: Bearer <token>`。
下文 `{id}` 为实例 ID，`{digest}` 从列表接口取。

### 5.1 能不能准确抓出慢 SQL

**页面**：SQL 监控 → 选实例，或性能中心 → SQL 页签 →「SQL 洞察」卡片 →「耗时排行」。
**接口**：`GET /api/v1/databases/{id}/slow-queries/?time=30m&limit=50`

| # | 检查 | 期望 |
|---|---|---|
| C-1 | 返回非空，`basis` | `basis = "window"`（来自时间窗增量）；没有红色"数据不可信"提示 |
| C-2 | 排序 | 场景 B 期间 `slow_user_sort` 排第一，`pk_lookup` 不在前列 |
| C-3 | 单位 | `avg_ms` 与 `run` 打印的客户端平均耗时**同一数量级**（服务端略小，差的是网络与 proxy 开销） |
| C-4 | 跨 set 对账（分布式） | `exec_count` ≈ 下面手工查询两次之差在各 set 上的**合计**（不是某一个 set 的值） |
| C-5 | 慢 SQL 告警 | 开启 `SLOW_SQL_AVG_MS=50` 后，场景 B 期间出现慢 SQL 告警 |

C-4 手工对账（用监控账号；间隔 10 分钟执行两次，逐 set 相减后求和，与平台同一时间窗的数字对比）：

```sql
/*sets:allsets*/SELECT DIGEST, COUNT_STAR, ROUND(SUM_TIMER_WAIT/1e9, 1) AS total_ms, LEFT(DIGEST_TEXT, 80) AS q
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = 'aiops_probe_shop' ORDER BY SUM_TIMER_WAIT DESC LIMIT 10;
```

### 5.2 能不能给出正确的优化建议

**页面**：点开 `slow_user_sort` 的 SQL 详情 →「优化建议」。
**接口**：`GET /api/v1/databases/{id}/perf/sql/{digest}/`，看 `advice` 数组。

| # | SQL | 期望 |
|---|---|---|
| A-1 | `slow_user_sort`（场景 B） | `I-01`：给出 `ALTER TABLE \`orders\` ADD INDEX ... (\`user_id\`, \`created_at\`)`，DDL 可直接执行；**不出现** D-01（它带分片键） |
| A-2 | `slow_user_sort`（场景 A，索引存在） | 不再建议建同一个索引（`I-03`/`I-04`） |
| A-3 | `agg_no_shardkey`（分布式） | `D-01` 广播到全部 N 个 set（N 与实例 set 数一致）、`D-05` proxy 二次聚合 |
| A-4 | `range_no_key`（分布式） | `D-01`；索引建议含 `created_at` |
| A-5 | 任意 SQL | 建议里的表名 / 列名都真实存在；没有对 `information_schema` 等系统表的建议 |

建议把 A-1 的 DDL 实际执行一次（即 `index add`），确认语法在 TDSQL 上可执行、执行后 SQL 变快。

### 5.3 能不能看清一条 SQL 的时间花在哪

**页面**：SQL 详情 →「耗时构成」页签。

| # | 检查 | 期望 |
|---|---|---|
| B-1 | 语句层构成 | 锁等待 / CPU / 其他三段；未开 CPU consumer 时显示"CPU 未采集"与开启方法，**不显示 100% CPU** |
| B-2 | 每次执行明细 | `slow_user_sort` 每次扫描约 40 万行、返回 10 行，结论含 `SCAN_INEFFICIENT` / `FULL_SCAN` |
| B-3 | 锁等待 | 跑过 `lockwait` 后，UPDATE 语句的锁等待占比明显，结论含 `LOCK_WAIT` |
| B-4 | 按 set 分布（分布式） | `slow_user_sort` 分布在多个 set；全部 set 的执行次数合计 = C-4 对账结果 |
| B-5 | EXPLAIN ANALYZE | 点「执行（在 set_x 上）」：返回算子树，热点为 `Table scan on orders`，耗时与「平均耗时」同一量级；审计日志里有一条 `EXECUTE_SQL` 记录 |
| B-6 | EXPLAIN ANALYZE 超时 | 把 `PERF_EXPLAIN_ANALYZE_TIMEOUT_MS` 调到 `100` 重试：在约 0.1s 内返回，页面提示"计划被超时截断，耗时为下限" |
| B-7 | EXPLAIN ANALYZE 守卫 | 手工粘贴 `UPDATE ...` / `SELECT ... FOR UPDATE`：被拒绝，不会发到数据库 |

### 5.4 能不能对周期性慢 SQL 和问题做环比

**页面**：「SQL 洞察」→「环比退化」，基线选「对比上一窗口」，时间窗选 30 分钟。
**接口**：`GET /api/v1/databases/{id}/perf/sql-regression/?window=30m&baseline=prev`

| # | 检查 | 期望 |
|---|---|---|
| R-1 | 场景 B 结束时查询 | `slow_user_sort` 状态为「退化」，平均耗时倍数明显大于 1.5，`plan_changed_at` 有值（采集到了计划变化时） |
| R-2 | `pk_lookup` | 「稳定」 |
| R-3 | 「新增」的语义 | 只有基线窗口里完全没出现过的 SQL 才是「新增」（比如 `lockwait` 的 UPDATE） |
| R-4 | 昨日 / 上周同时段 | 运行满一天后，基线切到「对比昨日同时段」能出结果；数据不足时有提示，而不是全部显示「新增」 |

**周期 SQL**（需要多日数据）：用 cron 每天固定时段跑 `batch`，**至少连续 3 天**：

```cron
# 每天 02:10 跑 10 分钟"夜间报表"查询（SQL 形态与 run 不同，便于单独识别）
10 2 * * *  cd /path/to/db-aiops && TDSQL_WL_HOST=... .venv/bin/python scripts/tdsql_workload.py batch --minutes 10
```

第 4 天起：「周期 SQL」页签（或 `GET /api/v1/databases/{id}/perf/sql-periodic/?days=3`）应识别出该查询，
运行时段为 `2点`，出现天数 `3/3`，并给出"最近一次 vs 历次中位数"。某天在 02:10 前先执行 `index drop`、跑完再 `index add`，当天的对比倍数应显著变大。

---

## 6. 请发回的内容

1. `tdsql_probe_result.json`（或探针终端输出）—— **最重要**
2. §5 每项的结论（通过 / 不通过 + 现象），不通过的附截图或接口 JSON
3. 平台日志里的降级留痕：`grep '\[degrade\]' <start_monitor 日志> | sort | uniq -c | sort -rn | head -50`
   —— 每条透传失败、列缺失、降级都会在这里留痕，比截图更能定位问题
4. 实例形态：集中式 / 分布式、set 数、TDSQL 版本（`/*proxy*/show config` 里的 `version`）

**发回前请检查**：不包含主机、端口、账号、密码（探针输出与降级日志均不含密码，但日志可能含主机名，请按需打码）。

---

## 7. 已知限制（本轮不做，见设计文档 §11）

- 不拆分 proxy 层耗时（端到端 vs 后端）：需要 proxy 日志或管控面 API，SQL 接口拿不到。
- 不读 set 上的慢日志文件：需要主机访问。若内网实例 `log_output` 包含 `TABLE`，后续可经透传读 `mysql.slow_log`。
- 读写分离下若透传落到备机（V-9 WARN），统计只代表该备机；需要根据结论再调整路由提示。
