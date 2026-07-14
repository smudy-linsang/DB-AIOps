# Phase 7 第一册：ASH 引擎升级（数据与采样层）

> 工单 7A-01 ~ 7A-08。本册是地基：session_sample v2、统一等待类、逐库采样 SQL v2、
> SQL 指纹、sql_stat 快照、sql_plan 计划库、连续聚合与保留。
> 环境实测基线（2026-07-14）: MySQL 8.0.46 performance_schema=ON 且 digest 表有数据；
> PG 16.14 有 wait_event_type/pg_stat_progress_*，pg_stat_statements 可装（7D-04）；
> Oracle XE 21c v$session 含 sql_id/wait_class/program/module，v$sqlstats/v$sql_plan 可读。

---

## 1. session_sample v2 数据模型（7A-01）

在既有超表上 **ALTER TABLE 增列**（不迁移旧数据，旧行新列为 NULL，查询侧 COALESCE）。
执行位置: `monitor/timeseries.py::init_hypertables()` 幂等追加（模式与既有建表一致）。

新增列字段表（契约源）:

| 列名 | 类型 | 说明 | 来源 |
|---|---|---|---|
| `wait_class` | VARCHAR(20) | 统一等待类（§3 九类枚举, 小写） | 采样时映射 |
| `sql_id` | VARCHAR(32) | 库原生 SQL 标识（Oracle sql_id / MySQL digest 前 32 / PG query_id 十进制串）; 无则 NULL | 采样 |
| `program` | VARCHAR(120) | 客户端程序名 | 采样 |
| `module` | VARCHAR(120) | 模块（Oracle module / PG application_name / MySQL 空） | 采样 |
| `lock_type` | VARCHAR(40) | 被阻塞时的锁类型（TABLE/RECORD/relation/tuple/TX/TM…） | 锁明细查询 |
| `lock_mode` | VARCHAR(40) | 锁模式（X,S,IX / RowExclusiveLock / mode 6…） | 锁明细查询 |
| `lock_object` | VARCHAR(200) | 争用对象 `schema.table`；取不到则 NULL | 锁明细查询 |
| `sample_gap_sec` | SMALLINT | 本样本代表的秒数=写入时的采样间隔 | 哨兵 |

DDL（幂等）:
```sql
ALTER TABLE session_sample
  ADD COLUMN IF NOT EXISTS wait_class     VARCHAR(20),
  ADD COLUMN IF NOT EXISTS sql_id         VARCHAR(32),
  ADD COLUMN IF NOT EXISTS program        VARCHAR(120),
  ADD COLUMN IF NOT EXISTS module         VARCHAR(120),
  ADD COLUMN IF NOT EXISTS lock_type      VARCHAR(40),
  ADD COLUMN IF NOT EXISTS lock_mode      VARCHAR(40),
  ADD COLUMN IF NOT EXISTS lock_object    VARCHAR(200),
  ADD COLUMN IF NOT EXISTS sample_gap_sec SMALLINT;
CREATE INDEX IF NOT EXISTS idx_ss_config_class_time
  ON session_sample (db_config_id, wait_class, time DESC);
CREATE INDEX IF NOT EXISTS idx_ss_config_digest_time
  ON session_sample (db_config_id, sql_digest, time DESC);
```

同时 `timeseries.write_session_samples()` 的 INSERT 列清单同步扩展; 行 dict 缺键写 NULL。

**验收 7A-01**: DDL 幂等重跑无错; 新老行混存查询正常; write_session_samples 写入含 8 新列。

---

## 2. 采样条件变化（重要口径）

- MySQL 家族: 保持 `command NOT IN ('Sleep','Daemon')`；**另加**处于显式事务但空闲的
  会话（长事务证据, 见 §4.2 trx 联查）→ 归 `application` 类, state 记 `idle_in_trx`。
- PG: `state <> 'idle'` **且** `backend_type = 'client backend'`（排除后台进程）;
  `idle in transaction` 保留 → `application`。
- Oracle: `type='USER' AND status='ACTIVE'` **或** `blocking_session IS NOT NULL`;
  `wait_class='Idle'` 且非阻塞相关的不采（与 EMCC 同口径）。

---

## 3. 统一等待类映射（7A-02, 代码级契约）

新文件 `monitor/detectors/wait_class.py`, 唯一导出 `classify(db_type, row) -> str`。
row 为采样行 dict（含 command/state/wait_event/wait_event_type 等原始值）。

### 3.1 Oracle / DM
```python
_ORACLE_MAP = {
    'User I/O': 'user_io', 'System I/O': 'system_io', 'Concurrency': 'concurrency',
    'Application': 'application', 'Commit': 'commit', 'Network': 'network',
    'Configuration': 'configuration', 'Scheduler': 'configuration',
    'Queueing': 'other', 'Other': 'other', 'Administrative': 'other',
}
# 规则: v$session.state != 'WAITING' → on_cpu；否则查表, Idle 行在采样 SQL 已过滤
```

### 3.2 PostgreSQL（wait_event_type 列）
```python
_PG_MAP = {
    'Lock': 'concurrency', 'LWLock': 'concurrency', 'BufferPin': 'concurrency',
    'IO': 'user_io', 'Client': 'network', 'IPC': 'other', 'Extension': 'other',
    'Timeout': 'other', 'Activity': 'other',
}
# 规则(优先级):
# 1) state == 'idle in transaction' → application
# 2) wait_event_type IS NULL and state=='active' → on_cpu
# 3) 查 _PG_MAP, 缺省 other
```

### 3.3 MySQL / TDSQL / GBase（state 关键词优先级规则, 从上到下首个命中生效）
```python
_MYSQL_RULES = [  # (判定, wait_class)
    (lambda s: 'lock' in s, 'concurrency'),                       # 各种 metadata/row/table lock
    (lambda s: 'commit' in s or 'binlog' in s or 'flush' in s, 'commit'),
    (lambda s: 'net' in s or 'sending to client' in s, 'network'),
    (lambda s: 'tmp table' in s or 'file' in s or 'disk' in s
               or s == 'sending data', 'user_io'),
    (lambda s: 'repl' in s or 'relay' in s or s == 'idle_in_trx', 'application'),
    (lambda s: s in ('executing', 'statistics', 'preparing', 'optimizing',
                     'sorting result', 'creating sort index', 'updating',
                     'update', 'init', 'checking permissions'), 'on_cpu'),
]
# 输入 s = (state or '').lower(); 无 state 且 command=='Query' → on_cpu; 兜底 other
# is_blocked=True 的行强制覆写为 concurrency（阻塞语义优先）
```

**验收 7A-02**: 单元样例 30 条（每库 10 条典型 state/wait_event → 期望类）全对;
枚举外值不可能出现（classify 只返回九类之一）。

---

## 4. 逐库 ASH 采样 SQL v2（7A-03, `monitor/sentinel.py` 重写三个 `_ash_*`）

### 4.1 Oracle（单查询, 含锁对象）
```sql
SELECT s.sid || ',' || s.serial# AS session_id, s.sql_id, s.username AS user_name,
       s.machine AS client_host, s.program, s.module, s.status AS command,
       s.state, s.event AS wait_event, s.wait_class AS raw_wait_class,
       s.last_call_et AS active_secs, s.blocking_session AS blocker_id,
       CASE WHEN s.blocking_session IS NOT NULL THEN 1 ELSE 0 END AS is_blocked,
       s.row_wait_obj# AS wait_objno
FROM v$session s
WHERE s.type = 'USER'
  AND (s.status = 'ACTIVE' OR s.blocking_session IS NOT NULL)
  AND NOT (s.wait_class = 'Idle' AND s.blocking_session IS NULL)
```
- `lock_object`: 对 `wait_objno > 0` 的行, 批量一次
  `SELECT object_id, owner||'.'||object_name FROM dba_objects WHERE object_id IN (...)`
  （每次采样最多 1 次补查; 结果进程内 LRU 缓存 1h, 容量 512）。
- `lock_type/lock_mode`: 被阻塞行补查
  `SELECT sid, type, lmode, request FROM v$lock WHERE sid IN (...) AND request > 0`
  → lock_type=type(TX/TM), lock_mode='req:'||request。
- `sql_digest` = sql_id（Oracle 直接用 sql_id 作为统一指纹）; `db_name` = service_name。

### 4.2 MySQL 家族（processlist + digest + 锁明细 + 长事务, 4 段查询一次往返各一）
```sql
-- ① 会话主体（含空闲中事务）
SELECT p.id AS session_id, p.user AS user_name, p.host AS client_host,
       p.db AS db_name, p.command, p.state, p.time AS active_secs,
       LEFT(COALESCE(p.info,''),200) AS sql_text
FROM information_schema.processlist p
WHERE p.command NOT IN ('Sleep','Daemon')
UNION ALL
SELECT p.id, p.user, p.host, p.db, p.command, 'idle_in_trx',
       TIMESTAMPDIFF(SECOND, t.trx_started, NOW()),
       LEFT(COALESCE(t.trx_query,''),200)
FROM information_schema.innodb_trx t
JOIN information_schema.processlist p ON p.id = t.trx_mysql_thread_id
WHERE p.command = 'Sleep';
-- ② 原生 digest（有则覆盖指纹）
SELECT t.PROCESSLIST_ID AS session_id, sc.DIGEST AS digest
FROM performance_schema.events_statements_current sc
JOIN performance_schema.threads t ON t.THREAD_ID = sc.THREAD_ID
WHERE t.PROCESSLIST_ID IS NOT NULL AND sc.DIGEST IS NOT NULL;
-- ③ 阻塞边（沿用现查询）
-- ④ 锁明细（被阻塞行）
SELECT w.REQUESTING_ENGINE_TRANSACTION_ID, l.LOCK_TYPE, l.LOCK_MODE,
       CONCAT(l.OBJECT_SCHEMA,'.',l.OBJECT_NAME) AS lock_object
FROM performance_schema.data_lock_waits w
JOIN performance_schema.data_locks l
  ON l.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID;
```
- `program/module` 置 NULL（MySQL 无来源）; `sql_id` = digest 前 32。

### 4.3 PostgreSQL（单查询 + 锁明细）
```sql
SELECT a.pid AS session_id, a.usename AS user_name, a.client_addr AS client_host,
       a.datname AS db_name, a.state AS command, a.state,
       a.wait_event_type, a.wait_event, a.application_name AS module,
       a.backend_type AS program, a.query_id,
       EXTRACT(EPOCH FROM (now()-a.query_start))::int AS active_secs,
       LEFT(a.query,200) AS sql_text,
       (pg_blocking_pids(a.pid))[1] AS blocker_id,
       cardinality(pg_blocking_pids(a.pid)) > 0 AS is_blocked
FROM pg_stat_activity a
WHERE a.state IS NOT NULL AND a.state <> 'idle'
  AND a.backend_type = 'client backend';
-- 锁明细（被阻塞行）
SELECT l.pid, l.locktype AS lock_type, l.mode AS lock_mode,
       COALESCE(n.nspname||'.'||c.relname, l.locktype) AS lock_object
FROM pg_locks l
LEFT JOIN pg_class c ON c.oid = l.relation
LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE NOT l.granted;
```
- `sql_id` = query_id（非 0 时, 十进制字符串）。

**验收 7A-03**: 三库分别注入锁阻塞（复用 drills 手法）后单次采样,
`wait_class='concurrency' AND lock_object IS NOT NULL` 的行存在;
采样单库耗时 <80ms（本地容器实测打印）。

---

## 5. SQL 归一化与统一指纹（7A-04, 新文件 `monitor/sqlfingerprint.py`）

```
digest 优先级: ① Oracle sql_id ② MySQL performance_schema DIGEST(截 32)
              ③ PG query_id(非0) ④ md5(normalize(sql_text))[:32] ⑤ NULL(无文本)
normalize(text):
  1. 去注释 /*..*/ 与 --行注释   2. 小写   3. 连续空白→单空格
  4. 数字字面量→'?'  正则 \b\d+(\.\d+)?\b
  5. 引号串→'?'      正则 '([^']|'')*' 与 "([^"]|"")*"
  6. IN (?,?,?)→in(?)  正则 in\s*\(\s*\?(\s*,\s*\?)*\s*\)
```
导出 `unified_digest(db_type, native_digest, sql_text) -> str|None` 与
`normalize(sql_text) -> str`。哨兵与 sql_stat 采集共用, **保证同一 SQL 两处指纹一致**。

**验收 7A-04**: 单测 12 例（同 SQL 不同字面量→同指纹; 不同 SQL→不同指纹; 三库原生
优先级正确）。

---

## 6. 采样频率与背压（7A-05）

- `ASH_INTERVAL_SEC` 默认 15 → **5**（settings + .env 注释）; 哨兵探活间隔不变。
- 写入行 `sample_gap_sec = 当前生效间隔`。
- 背压: `InstanceSentinel` 记录最近 2 次 `ash_sample()` 耗时, 均 >800ms →
  生效间隔×2（上限 30s, 记 WARN 日志与 metric `ash_backoff`）;
  之后每 10 次采样尝试减半恢复, 直到回到配置值。
- TDSQL 广域网实例: `.env` 可按需 `ASH_INTERVAL_SEC=15` 全局放宽（本期不做每实例粒度）。

**验收 7A-05**: 本地实例以 5s 稳定采样 10min 无背压; 人工在采样 SQL 加 `SLEEP(1)`
模拟慢采样 → 观察间隔升到 10s→20s 并在移除后恢复。

---

## 7. 连续聚合与保留（7A-06）

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS session_ash_1m
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', time) AS bucket, db_config_id,
       COALESCE(wait_class,'other') AS wait_class,
       COALESCE(sql_digest,'')      AS sql_digest,
       COALESCE(user_name,'')       AS user_name,
       COALESCE(db_name,'')         AS db_name,
       SUM(COALESCE(sample_gap_sec,15))::int AS active_sec,
       COUNT(*) AS samples,
       SUM(CASE WHEN is_blocked THEN COALESCE(sample_gap_sec,15) ELSE 0 END)::int AS blocked_sec
FROM session_sample GROUP BY 1,2,3,4,5,6;
SELECT add_continuous_aggregate_policy('session_ash_1m',
  start_offset=>INTERVAL '2 hours', end_offset=>INTERVAL '1 minute',
  schedule_interval=>INTERVAL '1 minute', if_not_exists=>TRUE);
SELECT add_retention_policy('session_ash_1m', INTERVAL '90 days', if_not_exists=>TRUE);
```
- AAS(bucket, class) = `SUM(active_sec)/60.0`。近 2h 内查询自动读实时部分
  （TimescaleDB real-time aggregate 默认行为）。
- raw 表保留 7d 不变（ASH 明细钻取窗口=7 天, 之后只剩 1m 聚合）。

**验收 7A-06**: 聚合视图数据与 raw 手工聚合一致（抽 3 个 bucket 比对）; 90d 策略注册成功。

---

## 8. sql_stat 快照增量（7A-07, collector T2 60s）

超表 `sql_stat`（新建, chunk 1d, 保留 90d）:

| 列 | 类型 | 说明 |
|---|---|---|
| time | TIMESTAMPTZ | 采集时刻 |
| db_config_id | INT | 实例 |
| sql_digest | VARCHAR(32) | 统一指纹（§5） |
| db_name | VARCHAR(100) | 库/schema |
| exec_delta | BIGINT | 周期内执行次数增量 |
| elapsed_ms_delta | BIGINT | 周期内总耗时增量(ms) |
| rows_delta | BIGINT | 周期内行数增量 |
| reads_delta | BIGINT | 逻辑读/共享块访问增量 |
| sql_text_sample | TEXT | 归一化文本样例(截 500) |

逐库来源（TopN=100 按总耗时, 快照全量拉→进程内 prev 差分→只写 delta>0 行）:
- MySQL: `performance_schema.events_statements_summary_by_digest`
  (COUNT_STAR, SUM_TIMER_WAIT/1e9→ms, SUM_ROWS_SENT, SUM_ROWS_EXAMINED→reads)
- PG: `pg_stat_statements` (calls, total_exec_time, rows, shared_blks_hit+read)
  —— 依赖 7D-04 环境准备; 不可用时跳过并在采集日志提示一次。
- Oracle: `v$sqlstats` (executions, elapsed_time/1000, rows_processed, buffer_gets),
  digest=sql_id。
- 差分基线缓存 `self._sqlstat_prev[config_id]`; 计数回绕(实例重启)→丢弃该轮。

**验收 7A-07**: 三库各注入 20 次同一 SQL → sql_stat 出现该 digest 且
exec_delta≈20; 重启目标库不产生负值行。

---

## 9. sql_plan 计划库 + cpu_cores（7A-08）

普通表 `sql_plan`（Django model `SqlPlan`, monitor/models.py）:

| 字段 | 类型 | 说明 |
|---|---|---|
| id | AutoField | — |
| config | FK DatabaseConfig | 实例 |
| sql_digest | CharField(32), index | 指纹 |
| plan_hash | CharField(32) | 计划指纹: Oracle plan_hash_value; MySQL/PG=md5(计划树结构化后) |
| plan_json | JSONField | 原始计划(JSON) |
| plan_text | TextField | 渲染文本(缩进树) |
| cost_total | FloatField null | 总代价 |
| source | CharField(12) | auto/manual/incident |
| captured_at | DateTimeField | — |
| is_current | BooleanField | 该 digest 最新计划标记（同 digest 唯一 true） |

采集器 `monitor/plan_capture.py::capture(config, sql_digest, sql_text=None)`:
- Oracle: `SELECT * FROM v$sql_plan WHERE sql_id=:1 AND child_number=0`（列: id,
  parent_id, operation, options, object_name, cost, cardinality）→ 树化 JSON;
  plan_hash 取 `v$sqlstats.plan_hash_value`。
- MySQL: 需要样例原文（session_sample.sql_text 或 sql_text_sample）;
  `EXPLAIN FORMAT=JSON <text>`; plan_hash=md5(剔除 cost/rows 字段后的结构 JSON)。
  仅接受单条 SELECT/UPDATE/DELETE/INSERT…SELECT; 其余拒绝（安全铁律）。
- PG: `EXPLAIN (FORMAT JSON) <text>`（**禁 ANALYZE**）; plan_hash 同上结构 md5。
- 自动采集时机: ① collector 每小时对该实例 sql_stat 按 elapsed_ms_delta Top10 采集;
  ② 事故诊断管道对 top_sql_by_samples 前 3 采集(source=incident); ③ API 手动。
- 新计划 hash ≠ 该 digest 现 is_current 的 hash → 翻转 is_current 并**发
  `plan_change` 事件**（详见 40 册 7D-01）。

cpu_cores 黄金量（并入 `sentinel.golden_metrics`）:
- MySQL: 无 SQL 来源 → 跳过（NULL, 前端不画线）; Oracle: `v$parameter cpu_count`;
- PG: `SHOW max_parallel_workers`? 不准 → `SELECT setting FROM pg_settings WHERE
  name='max_worker_processes'` 也不是核数 → PG 16 无核数视图, 跳过;
- 实测可得的只有 Oracle; 其余库允许在 DatabaseConfig 新增可选字段
  `cpu_cores`(IntegerField null, 手工维护), 采集值优先、手工值兜底。

**验收 7A-08**: 三库对一条真实 SQL capture 成功, plan_text 可读, plan_hash 稳定
（同计划重采不变）; Oracle cpu_cores 采到; 改 MySQL 配置 cpu_cores=4 后 API 可见。
