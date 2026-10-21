# TDSQL 慢 SQL 能力改造设计

**依据**：`TDSQL_SLOWSQL_REVIEW.md` 的 T-01 ~ T-16
**日期**：2026-10-21
**约束**：本环境无真实 TDSQL。凡 TDSQL 专有行为一律以腾讯云官方文档为准开发，
标注「需内网验证」，并在 §8 给出内网环境的逐项验证清单。

---

## 0. 官方文档依据

| 行为 | 文档结论 | 用在哪 |
|---|---|---|
| 透传 SQL | `/*sets:allsets*/<SQL>` 发往全部 set；`/*sets:set_a,set_b*/<SQL>` 发往指定 set；`/*shardkey:v*/`、`/*shardkey_hash:h*/` 按分片键发送。透传时 proxy **不解析** SQL | 按 set 采集、按 set 执行 EXPLAIN ANALYZE |
| allsets 结果 | 结果集**多一列 `info`，值为 set 名**（官方示例 `repair table` 的输出） | 一次往返拿到全部 set 的 digest，并知道每行来自哪个 set |
| 客户端 `-c` | mysql 命令行客户端须带 `-c` 才保留注释；pymysql 原样发送注释，无此问题 | — |
| 分表键 | 分表按 shardkey 哈希分布到各 set；**不带 shardkey 的 SQL 路由到所有节点，资源消耗大**；主键/唯一索引必须包含 shardkey；shardkey 不可 UPDATE | 分布式规则 D-01、D-06 |
| 表类型 | 分表 / 广播表（`shardkey=noshardkey_allset`，每个 set 全量）/ 单表（默认在第一个 set） | 分布式规则 D-02、D-03 |
| JOIN | 推荐多个分表之间用分表键相等做 JOIN，避免跨节点 | 分布式规则 D-02 |

来源：腾讯云 TDSQL MySQL 版文档《透传 SQL》（product/1042/38195）、《InnoDB 引擎相关》FAQ（product/557/46238）、
《分表键》（product/1042/38506）、《表类型》（product/1042/38142）、《JOIN 与子查询》（product/1042/33364）。

---

## 1. 总体数据流

```
            ┌──────────── 采集 (start_monitor, 每轮) ────────────┐
TDSQL proxy │ /*sets:allsets*/ SELECT ... FROM perf_schema digest │──► 按 (实例, set, digest) 差分
            │ /*sets:allsets*/ SHOW GLOBAL STATUS WHERE ...       │──► 各 set 求和后差分
            └─────────────────────────────────────────────────────┘
                                   │
                                   ▼
                sql_stat（时序库，扩列：set_name + 13 个增量字段）
                                   │
     ┌───────────────┬─────────────┼──────────────┬─────────────────┐
     ▼               ▼             ▼              ▼                 ▼
 慢 SQL 接口     SQL 详情        环比 / 退化     周期 SQL         AI 诊断工具
 (窗口聚合)   (耗时构成+建议)   (自动基线)    (逐次运行比较)   (同一份数据)
                     │
                     ▼ 按需、默认关闭
          EXPLAIN ANALYZE（只读事务 + 超时 + 透传到热点 set）
```

**原则**：凡累计计数器，**先按 set 差分，再跨 set 求和**。永远不对"不同 set 的累计值"做减法（T-03 的根因）。

---

## 2. 采集（`monitor/sqlstat_collect.py`，新增）

### 2.1 取数

MySQL 系（mysql / tdsql / gbase）统一一条 digest 查询，字段从 6 个扩到 19 个：

```
DIGEST, SCHEMA_NAME, DIGEST_TEXT, QUERY_SAMPLE_TEXT, FIRST_SEEN,
COUNT_STAR, SUM_TIMER_WAIT, SUM_LOCK_TIME, SUM_CPU_TIME,
SUM_ROWS_SENT, SUM_ROWS_EXAMINED, SUM_CREATED_TMP_TABLES, SUM_CREATED_TMP_DISK_TABLES,
SUM_SORT_MERGE_PASSES, SUM_SORT_ROWS, SUM_SELECT_SCAN, SUM_SELECT_FULL_JOIN,
SUM_NO_INDEX_USED, SUM_ERRORS
WHERE DIGEST IS NOT NULL AND LAST_SEEN >= NOW() - INTERVAL <活跃窗口> SECOND
ORDER BY SUM_TIMER_WAIT DESC LIMIT <SQLSTAT_FETCH_LIMIT>
```

- **按活跃窗口取，而不是按累计 Top N 取**（修 T-12）：只要本轮内执行过就能进来，不再被历史累计值挡在门外
- `SUM_CPU_TIME`（8.0.28+）、`QUERY_SAMPLE_TEXT`（8.0.3+）在 MariaDB 系与旧版本上不存在 →
  先试完整列，报未知列则退回基础列，**按实例缓存能力探测结果**并 `degrade.note`
- `SUM_CPU_TIME` 需要 `events_statements_cpu` consumer，默认关闭；关闭时全为 0 →
  界面标注"CPU 时间未采集"并给出开启方法，**不得把 0 当成"没用 CPU"**

### 2.2 TDSQL 按 set 取

1. **首选**：`/*sets:allsets*/` + 上述查询，从 `info` 列拿 set 名
2. **回退一**：结果没有 `info` 列 → 用 `/*proxy*/show routes` 拿 set 列表，逐个 `/*sets:<set>*/` 发送
3. **回退二**：透传语法被拒（如 694）→ 不带透传直接查，`set_name=''`，`degrade.note('sqlstat.tdsql_passthrough')`。
   **groupshard 下这条回退的数据不可信**，接口返回 `basis_warning`，界面显式告警
4. noshard 实例同样走透传（只有一个 set），避免读写分离把查询路由到备机

### 2.3 差分

- 键 = `(config_id, set_name, digest)`；本轮值 − 上轮值
- 计数器回退（重启 / `TRUNCATE`）→ 跳过一轮，不写负值
- 上轮缓存里没有的 digest：若 `FIRST_SEEN` 晚于上一轮采集时刻 → 本轮新出现，**增量 = 全部当前值**；
  否则（缓存刚建立或被淘汰）→ 只记录、不写
- 差分后按本轮耗时增量取每个 set 的 Top `SQLSTAT_TOPN`
- 时间单位：皮秒 → 毫秒用四舍五入（原 `//10**9` 截断会把高频亚毫秒 SQL 抹成 0）

### 2.4 实例级计数器（修 T-03 后半、T-15）

groupshard 下 `SHOW GLOBAL STATUS` 同样只落在一个 set。采集器改为
`/*sets:allsets*/ SHOW GLOBAL STATUS WHERE Variable_name IN (...)`，**各 set 求和**后输出
`Slow_queries / Questions / Com_commit / Com_rollback / Innodb_rows_*` 等计数器，
`start_monitor` 据此算 `slow_queries_delta` 与新增的 `qps_current / tps_current`（按真实间隔秒数）。
透传不可用时保持原行为并留痕。

---

## 3. 存储（时序库 `sql_stat` 扩列，无 Django 模型改动）

```sql
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS set_name          VARCHAR(64) DEFAULT '';
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS lock_ms_delta     BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS cpu_ms_delta      BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS tmp_tables_delta  BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS tmp_disk_delta    BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS sort_merge_delta  BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS sort_rows_delta   BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS scan_delta        BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS full_join_delta   BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS no_index_delta    BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS errors_delta      BIGINT DEFAULT 0;
ALTER TABLE sql_stat ADD COLUMN IF NOT EXISTS sql_example       TEXT;
CREATE INDEX IF NOT EXISTS idx_sqlstat_config_time ON sql_stat (db_config_id, time DESC);
```

- 全部 `ADD COLUMN IF NOT EXISTS` + 默认值：**存量行无需清洗**，旧行新列为 0 / 空串，读取侧把"全 0"解释为"未采集"而非"真实为 0"
- 字段语义沿用：`rows_delta` = 返回行，`reads_delta` = 扫描行（MySQL 系）
- `sql_example` 存 `QUERY_SAMPLE_TEXT`（带字面量），供 EXPLAIN 与"前导通配 LIKE"这类需要看字面量的规则使用
- 不改 Django 模型，故无 migration；时序库 DDL 在 `init_hypertables()` 中幂等执行

---

## 4. 慢 SQL 接口（修 T-01 / T-02 / T-09 / T-16）

`SlowQueryEngine.collect_slow_queries_from_db(time_range, limit)`：

1. **主路径**：从 `sql_stat` 按窗口聚合（跨 set 求和），返回字段沿用前端契约
   （`*_sec` 字段**真的是秒**），新增 `digest / avg_ms / lock_time_sec / tmp_disk_tables /
   sort_merge_passes / set_count / hottest_set / is_slow / basis='window'`
2. **排序**：按窗口内总耗时；`is_slow = 平均耗时 >= SLOW_SQL_AVG_MS`
3. **回退**：时序库不可用 → 直连取 digest 累计值（TDSQL 走 §2.2），`basis='cumulative'`，
   界面注明"自计数器清零以来的累计值，非所选时间窗"
4. 取连接一律 `DbConnector.get_connection(cfg, statement_timeout_ms=..., readonly=True)`
5. 慢 SQL 告警（D5）只对 `is_slow` 且近 10 分钟有执行的 SQL 告警，文案给真实毫秒数
6. AI 诊断工具 `slow_queries` 改为调用同一接口（原实现读不存在的 `SlowQueryLog`）
7. 慢日志解析器修正为同时支持 MySQL 5.6 与 8.0 格式（T-11），保留为工具函数

---

## 5. 耗时构成（修 T-07，`monitor/sql_profile.py` + `monitor/sql_explain.py`，新增）

### 5.1 语句层：每次执行平均花在哪

由窗口聚合直接算出，**不需要额外采集**：

| 项 | 计算 | 含义 |
|---|---|---|
| 平均耗时 | elapsed / exec | — |
| 锁等待 | lock / exec | 行锁、表锁、元数据锁 |
| CPU | cpu / exec | 仅在 consumer 开启时可用 |
| 其他（IO / 等待 / 网络） | 平均 − 锁 − CPU | CPU 可用时才拆，否则不拆 |
| 扫描效率 | 扫描行 / 返回行 | > 100 说明大量无效扫描 |
| 全表扫描占比 | select_scan / exec | — |
| 未用索引占比 | no_index / exec | — |
| 落盘临时表占比 | tmp_disk / tmp_tables | GROUP BY / ORDER BY / UNION 落盘 |
| 归并排序轮次 | sort_merge / exec | sort_buffer 不够 |

输出结构化 `findings`（code / severity / 文本 / 证据数值），供界面与建议引擎共用。

### 5.2 分片层（TDSQL）：时间花在哪个 set

按 set 拆分同一 digest：执行次数、总耗时、平均耗时、份额；
**倾斜度** = 最热 set 份额 ÷ (1 / set 数)。倾斜度 ≥ 2 判为热点 set。

### 5.3 算子层：EXPLAIN ANALYZE（默认关闭）

硬约束（任何一条不满足都拒绝执行）：

1. 全局开关 `PERF_EXPLAIN_ANALYZE_ENABLED`（默认 False）
2. 权限 `tickets.execute`（会在目标库真实执行查询，比"查看"高一级）
3. 语句守卫：去注释后必须是**单条 SELECT / WITH...SELECT**；拒绝 `FOR UPDATE`、`LOCK IN SHARE MODE`、
   `INTO OUTFILE/DUMPFILE/@变量`、多语句
4. 会话：`SET SESSION TRANSACTION READ ONLY` + `START TRANSACTION READ ONLY`，结束 `ROLLBACK`
   （本地实测：只读事务下 `EXPLAIN ANALYZE UPDATE` 报 1792，写操作确实被挡住）
5. 超时：会话 `max_execution_time` + 语句内 `/*+ MAX_EXECUTION_TIME(n) */` 双保险，
   上限 `PERF_EXPLAIN_ANALYZE_TIMEOUT_MS`
6. TDSQL：`/*sets:<热点 set>*/EXPLAIN ANALYZE ...`，只在一个 set 上执行（需内网验证）
7. 写审计日志（`AuditLog`，`EXECUTE_SQL`）

**超时截断检测**（本地实测发现）：`max_execution_time` 触发时 EXPLAIN ANALYZE **不报错**，
而是返回一棵被截断的计划（实测 `rows=0`、`actual time=497ms` 恰好卡在 500ms 超时上）。
因此根节点实际耗时 ≥ 超时的 95% 时标记 `truncated=true`，界面显示"已在超时处截断，数值为下限"。

解析：把 TREE 格式解析成算子树，计算每个算子的**自身耗时**
（`实际耗时 × loops − 子算子之和`，近似值），给出按自身耗时排序的热点算子。

---

## 6. 优化建议（修 T-05 / T-06，`monitor/sql_advisor.py`，新增）

### 6.1 解析

轻量分词（处理反引号、引号、`?` 占位、括号深度），只分析最外层语句：
FROM / JOIN（含别名与 ON 条件）、WHERE 顶层 AND 谓词、ORDER BY、GROUP BY、LIMIT、SELECT 列；
UPDATE / DELETE 的目标表与 WHERE。
**列归属**：`别名.列` 按别名映射；单表时无前缀列归该表；多表且无前缀 → **不归属、不建议**（修 T-05 的"把 users.city 建到 orders 上"）。

### 6.2 通用规则

| 编号 | 规则 | 说明 |
|---|---|---|
| I-01 | 复合索引：等值列 → 排序 / 分组列 → 第一个范围列 | 经典 ERS 顺序；排序方向不一致时不并入 |
| I-02 | 覆盖索引变体 | SELECT 列全部在该表且总列数 ≤ 5 时给出 |
| I-03 | 已有索引覆盖时不建议 | 能取到 `information_schema.statistics` 时核对最左前缀 |
| I-04 | 效率已好时不建议 | 有画像且扫描效率 ≤ 10、未用索引占比 0 → 不出索引建议 |
| R-01 | 函数包列 → 改写 | 如 `DATE(col) = ?` → `col >= ? AND col < ?` |
| R-02 | 前导通配 `LIKE '%x'` → B-tree 无效 | 只在有带字面量样例时判定；`LIKE ?` 时提示"取决于模式" |
| R-03 | 深分页 `LIMIT 大偏移` → 键集分页 | 需样例字面量 |
| R-04 | 跨列 OR → `UNION ALL` 或分别建索引 | — |
| R-05 | `NOT IN (子查询)` → `NOT EXISTS` | — |
| R-06 | `SELECT *` | 宽表回表、网络放大 |
| R-07 | `ORDER BY RAND()` | — |
| E-01 ~ E-04 | 证据规则：落盘临时表、归并排序、锁等待占比高、扫描效率差 | 来自 §5.1 画像 |

### 6.3 TDSQL 分布式规则（只在 groupshard 且有 shardkey 元数据时启用）

元数据来自采集器已有的 `tdsql_shard_tables`（`show shardkey`）：分表（带 shardkey）/ 广播表 / 单表。

| 编号 | 规则 | 严重度 |
|---|---|---|
| D-01 | 分表的 WHERE 无 shardkey 等值 / IN 条件 → **广播到全部 N 个 set** | 高 |
| D-02 | 两张分表 JOIN，ON 条件不是双方 shardkey 相等 → **跨 set 关联** | 高 |
| D-03 | 分表与单表 JOIN → 单表只在第一个 set，建议改为广播表 | 中 |
| D-04 | 广播查询 + `ORDER BY ... LIMIT` → 每个 set 返回 offset+n 行再由 proxy 归并 | 中 |
| D-05 | 广播查询 + 聚合 / GROUP BY 不含 shardkey → proxy 二次聚合 | 低 |
| D-06 | UPDATE / DELETE 无 shardkey → 在全部 set 执行（分布式事务） | 高 |
| D-07 | shardkey 被函数包裹或只有范围条件 → 无法路由，等同广播 | 高 |
| D-08 | 按 set 统计的倾斜度 ≥ 2 → 热点 set，检查 shardkey 取值分布 | 中 |

每条建议附：证据（触发它的谓词 / 数值）、DDL 或改写示例（如有）、预期效果。

---

## 7. 环比与周期 SQL（修 T-08，`monitor/sql_compare.py`，新增）

### 7.1 退化检测接口 `GET /perf/sql-regression/`

- 默认对比基线：**上一相邻窗口 / 昨日同时段 / 上周同时段**（`baseline=prev|day|week`），也可显式传两个窗口
- 数据源：`sql_stat` 窗口聚合（执行次数、总耗时、平均耗时、每次扫描行、每次锁时间）
- 对**全量** digest 分类（不再只看两侧 Top 10）：

| 分类 | 判据（阈值可配） |
|---|---|
| 新增 | 基线窗口完全没有执行 |
| 消失 | 本窗口完全没有执行 |
| 退化 | 平均耗时 ≥ 基线 × 1.5 且差值 ≥ 5ms，或总耗时 ≥ 基线 × 2 且差值 ≥ 1s |
| 改善 | 平均耗时 ≤ 基线 × 0.67 且差值 ≥ 5ms |
| 稳定 | 其余 |

- 按"本窗口总耗时 − 基线总耗时"排序，退化项关联同期计划变更（`plan_hash` 变化）

### 7.2 周期 SQL 接口 `GET /perf/sql-periodic/`

- 取近 N 天（默认 7）按小时聚合的执行分布，对总耗时 Top 200 的 digest 判定**日周期**：
  执行集中在固定小时（这些小时占总执行 ≥ 80%），且在 ≥ 70% 的天里都出现
- 对周期 SQL 按"每次运行"（当天的活跃小时）逐日计算总耗时、平均耗时，
  比较**最近一次运行 vs 此前各次的中位数**，给出倍数
- 典型输出："每日 02 点的对账 SQL，今天平均耗时是过去 6 次运行中位数的 3.2 倍"

### 7.3 原期间对比（`CompareView`）

保留 ASH 视角，修正"新增"误判：`new` 改为"基线窗口完全没有出现"，而不是"不在基线 Top 10"。

---

## 8. 内网 TDSQL 验证清单

开发时无法验证，以下每项都需在内网真实 TDSQL 上确认。**任一项不通过，对应功能会自动降级并留痕，
不会报错崩溃**，但数据可信度标识会变红。

| # | 验证项 | 方法 | 期望 |
|---|---|---|---|
| V-1 | 透传 allsets 可用且带 `info` 列 | `/*sets:allsets*/ SELECT 1 AS x` | 每个 set 一行，`info` = set 名 |
| V-2 | 透传查询 performance_schema | `/*sets:allsets*/ SELECT COUNT(*) FROM performance_schema.events_statements_summary_by_digest` | 每个 set 一行 |
| V-3 | digest 扩展列存在 | 同上查 `SUM_CPU_TIME, QUERY_SAMPLE_TEXT` | 不报未知列（TXSQL 8.0.33 应有） |
| V-4 | 透传 SHOW GLOBAL STATUS | `/*sets:allsets*/ SHOW GLOBAL STATUS WHERE Variable_name='Questions'` | 每个 set 一行 |
| V-5 | 指定单 set 透传 | `/*sets:<某set>*/ SELECT @@hostname` | 返回该 set 主节点 |
| V-6 | 透传 EXPLAIN ANALYZE | `/*sets:<某set>*/ EXPLAIN ANALYZE SELECT ...` | 返回 TREE 计划 |
| V-7 | 只读事务经 proxy 生效 | 透传 `START TRANSACTION READ ONLY` 后执行写 | 报 1792 |
| V-8 | `max_execution_time` 经 proxy 生效 | 设 500ms 跑长查询 | 约 500ms 返回 |
| V-9 | 集中式实例开读写分离时，透传是否落主 | 对比 `@@hostname` 与 `show routes` 的 master | 落主 |
| V-10 | 端到端 | 跑 `docs/review/tdsql/workload.sql` 的负载，观察平台慢 SQL 列表、SQL 详情、退化检测 | 数值与 `/*sets:allsets*/` 手工查询一致 |

对应的自动化探测脚本：`scripts/tdsql_probe.py`（只读，凭据走环境变量），一次跑完 V-1 ~ V-9 并输出结论表。
V-10 的负载与检查步骤见 `TDSQL_INTRANET_TEST_GUIDE.md`（负载脚本 `scripts/tdsql_workload.py`，只在独立测试库内读写）。

---

## 9. 配置项（登记到 `monitor/appconf.py` SPECS）

| 名称 | 默认 | 说明 |
|---|---|---|
| `SQLSTAT_TOPN` | 200 | 每个 set 每轮写入的 digest 上限（按本轮耗时增量） |
| `SQLSTAT_FETCH_LIMIT` | 500 | 每个 set 每轮拉取的活跃 digest 上限 |
| `SQLSTAT_ACTIVE_WINDOW_SEC` | 900 | 活跃窗口；应 ≥ 2 倍采集间隔 |
| `SLOW_SQL_AVG_MS` | 1000 | 平均耗时达到即判为慢 SQL |
| `PERF_EXPLAIN_ANALYZE_ENABLED` | False | EXPLAIN ANALYZE 总开关 |
| `PERF_EXPLAIN_ANALYZE_TIMEOUT_MS` | 5000 | EXPLAIN ANALYZE 超时上限 |
| `SQL_REGRESSION_AVG_RATIO` | 1.5 | 退化判定：平均耗时倍数 |
| `SQL_REGRESSION_MIN_DELTA_MS` | 5 | 退化判定：平均耗时最小差值 |

---

## 10. 测试策略

| 层 | 内容 |
|---|---|
| unit | 用**模拟 TDSQL proxy 的假游标**（识别 `/*sets:allsets*/` 前缀、返回 `info` 列、可配置为拒绝透传）覆盖 §2 全部分支；T-03 场景必须得到每轮 400；画像、解析、建议、环比、周期判定全部纯函数测试 |
| unit | 视图：权限闸与数据范围闸**分开测**（用"有权限但范围受限"的账号测范围） |
| dialect（真实 MySQL 8） | 慢 SQL 接口返回真实秒数与正确排序；EXPLAIN ANALYZE 守卫、只读、超时截断检测 |
| 复现脚本 | `docs/review/tdsql/repro_slowsql.py` 对应条目反转为"已修复" |
| 内网 | §8 清单 |

---

## 11. 不做的事

- 不做 proxy 层耗时（端到端 vs 后端）分解：需要 proxy 日志或管控面 API，SQL 接口拿不到；
  预留 `basis` 字段，内网确认数据来源后再接
- 不读取 set 上的慢日志文件：需要主机访问；如内网实例 `log_output` 含 `TABLE`，可后续经透传读 `mysql.slow_log`
- 不改 Oracle / PostgreSQL / 达梦的采集逻辑（仅读取侧统一走 `sql_stat`）
