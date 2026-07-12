# Phase 6A 详细设计 —— 发现层（1 分钟发现）

> 覆盖支柱一（分层采集）、支柱二（事件-事故-问题模型）、支柱三（三层检测）。
> 交付目标：注入宕机/锁风暴/连接风暴，**MTTD ≤ 60s**，且事故不刷屏。
> 前置阅读：`00_conventions.md`。工单：6A-01 ~ 6A-12。

---

## 1. 数据模型（契约源，逐字段）

> 落成 `monitor/models.py`，迁移文件 `monitor/migrations/00XX_phase6a.py`。字段名/类型
> **必须**与下表逐字一致。类型记法见 `00_conventions.md#2`。

### 1.1 Event（事件）表名 `monitor_event`

| 字段 | 类型 | 约束/默认 | 说明 |
|------|------|----------|------|
| id | bigint pk | auto | |
| event_uid | str(32) | unique, index | `EVT-<uuid4hex12>` |
| config | fk→DatabaseConfig | on_delete=CASCADE, related_name='events' | 列 config_id |
| db_type | str(20) | | 冗余存, 免 join |
| source | enum(sentinel,collector,baseline,ml,inspection) | | 见 conventions#3.1 |
| signal | str(40) | index | 见 conventions#4 |
| metric_key | str(100) | default='' | |
| value | float | default=0.0 | |
| threshold | float | default=0.0 | |
| severity | enum(critical,error,warning,info) | index | |
| dedup_key | str(120) | index | `<config_id>:<signal>[:<sub>]` |
| occurred_at | datetime | index | 客观发生时刻(信号源给) |
| created_at | datetime | auto_now_add | 入库时刻 |
| incident | fk→Incident | null=True, on_delete=SET_NULL, related_name='events' | 归属事故 |
| detail | json | default=dict | 结构化证据 |

索引：`(config_id, signal, occurred_at)`、`(incident_id)`、`(dedup_key, occurred_at)`。
保留策略：Event 表按月分区非必须（PG 单表可撑），但需定时任务清理 90 天前记录（工单 6A-12）。

### 1.2 Incident（事故）表名 `monitor_incident`

| 字段 | 类型 | 约束/默认 | 说明 |
|------|------|----------|------|
| id | bigint pk | auto | |
| incident_id | str(48) | unique, index | `INC-<YYYYMMDDHHMMSS>-<config_id>` |
| config | fk→DatabaseConfig | on_delete=CASCADE, related_name='incidents' | |
| db_type | str(20) | | 冗余 |
| category | enum(availability,lock,connection,capacity,replication,performance,config,other) | index | |
| title | str(200) | | 人读标题 |
| priority | enum(P1,P2,P3,P4) | index | 见 §4.3 定级矩阵 |
| status | enum(open,diagnosing,plan_ready,executing,verifying,resolved,closed) | index, default='open' | 见 §4.2 状态机 |
| dedup_key | str(120) | index | 聚合键, 见 §4.1 |
| event_count | int | default=1 | 聚合事件数 |
| is_storm | bool | default=False | 事件风暴标记 |
| is_flapping | bool | default=False | 抖动标记 |
| **occurred_at** | datetime | index | 首个关联 event 的 occurred_at (T_detect 起点) |
| **detected_at** | datetime | null=True | 事故生成时刻 (发现秒表终点) |
| **plan_ready_at** | datetime | null=True | 方案就位时刻 (6B 写) |
| **acked_at** | datetime | null=True | 人工确认时刻 |
| acked_by | str(50) | default='' | |
| **executing_at** | datetime | null=True | 首次执行修复时刻 (6C 写) |
| **resolved_at** | datetime | null=True | 解决时刻 (6C 验证回路写) |
| closed_at | datetime | null=True | 关闭时刻 |
| rca_result | json | default=dict | 6B 诊断产物 |
| impact | json | default=dict | 6B 影响产物 |
| plans | json | default=list | 6B 方案产物 |
| health_snapshot | float | default=0.0 | 生成时健康分 |
| problem | fk→Problem | null=True, on_delete=SET_NULL, related_name='incidents' | |
| created_at | datetime | auto_now_add | |
| updated_at | datetime | auto_now | |

**SLA 计算字段（非存储，模型 @property，供 API 输出）**：
- `t_detect_sec` = (detected_at − occurred_at).total_seconds()（occurred/detected 均非空时）
- `t_plan_sec` = (plan_ready_at − detected_at)…
- `t_resolve_sec` = (resolved_at − detected_at)…
- `sla_detect_ok` = t_detect_sec ≤ 60；`sla_plan_ok` ≤ 300；`sla_resolve_ok` ≤ 600。

### 1.3 Problem（问题）表名 `monitor_problem`

| 字段 | 类型 | 约束/默认 | 说明 |
|------|------|----------|------|
| id | bigint pk | auto | |
| problem_id | str(48) | unique, index | `PRB-<uuid4hex12>` |
| signature | str(200) | index | `<db_type>:<category>:<root_signal>` 归一签名 |
| title | str(200) | | |
| incident_count | int | default=0 | 累计事故数 |
| first_seen_at | datetime | null=True | |
| last_seen_at | datetime | null=True | |
| kb_ref | str(64) | default='' | 关联 AlertCase / 知识库 id |
| status | enum(active,mitigated,archived) | default='active' | |
| created_at | datetime | auto_now_add | |

> Problem 在 6A 仅建表 + 由聚合逻辑写签名与计数；深度知识沉淀在 6B/6C 接入 case_rag。

### 1.4 TimescaleDB 超表：session_sample（ASH-lite）

> DDL 落 `monitor/timeseries.py` 的 `init_hypertables()`，参照现有 `metric_point` 模式。

```sql
CREATE TABLE IF NOT EXISTS session_sample (
    time          TIMESTAMPTZ    NOT NULL,
    db_config_id  INTEGER        NOT NULL,
    db_type       VARCHAR(20)    NOT NULL,
    session_id    VARCHAR(64),          -- 会话/线程 id
    user_name     VARCHAR(100),
    client_host   VARCHAR(120),
    db_name       VARCHAR(100),
    command       VARCHAR(40),          -- Query/Sleep/... (mysql) 或 status(pg)
    state         VARCHAR(120),         -- 等待状态/wait_event
    wait_event    VARCHAR(120),         -- pg: wait_event; mysql: state 归一
    active_secs   INTEGER,              -- 该会话已持续秒数
    is_blocked    BOOLEAN DEFAULT FALSE,
    blocker_id    VARCHAR(64),          -- 阻塞者会话 id
    sql_digest    VARCHAR(64),          -- 语句摘要 hash (有则填)
    sql_text      TEXT                  -- 截断 200 字
);
SELECT create_hypertable('session_sample','time',
        chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
-- 保留策略 (ASH_RETENTION_DAYS=7):
SELECT add_retention_policy('session_sample', INTERVAL '7 days', if_not_exists => TRUE);
```

索引建议：`CREATE INDEX ON session_sample (db_config_id, time DESC);`
`CREATE INDEX ON session_sample (db_config_id, is_blocked, time DESC);`

---

## 2. 支柱一：分层采集 —— 施工细节

### 2.1 sentinel_daemon（新增进程，文件 `monitor/sentinel.py` + 命令 `monitor/management/commands/start_sentinel.py`）

**职责**：对每个 `is_active=True` 的实例，每 `SENTINEL_INTERVAL_SEC`(8s) 做一次探活 +
黄金状态量采集；每 `ASH_INTERVAL_SEC`(15s) 做一次会话采样。**每实例一个常驻长连接**，
连接由本进程独占（不与 collector 共享）。

**运行模型**：主线程加载实例列表（每 60s 刷新一次以感知新增/停用），为每个实例起一个
采样线程（`threading.Thread`），线程内 `while` 循环按各自节奏采样。线程数 = 实例数
（当前 6，规模可控；>50 实例时改线程池，非本期范围）。

**探活算法（判 DOWN，防广域网误报）**：
```
每 SENTINEL_INTERVAL_SEC:
  t0 = now()
  try:
     conn.ping() 或 执行 SELECT 1 (超时 SENTINEL_CONNECT_TIMEOUT_SEC)
     latency_ms = (now()-t0)*1000
     consecutive_fail = 0
     采集黄金状态量 → 发 golden metrics event(仅异常时) + 写 latency 指标
  except:
     consecutive_fail += 1
     重建连接(下一轮生效)
     if consecutive_fail == SENTINEL_FAIL_THRESHOLD(3):
        # 双路确认: 查该实例最近一次 collector 采集是否也失败
        if 最近90s内无成功 MonitorLog(status=UP):
            发 event(signal=instance_down, severity=critical, occurred_at=首次失败时刻)
        # 已发过则不重复(dedup_key=<cid>:instance_down 去重)
  恢复时(fail 从>0归0且曾报过down): 发 event(signal=instance_down, severity=info, detail.recovered=true)
```
- `occurred_at` 取**首次失败**时刻（t0 of 第一次 fail），使 MTTD 反映真实发生时间。
- 探活最坏发现延迟 = 3 × 8s = 24s < 60s。达标。

**黄金状态量（每 db_type 的确定性只读查询，仅取秒级可得的关键量）**：

| db_type | 黄金量 | SQL |
|---------|--------|-----|
| mysql / tdsql / gbase | threads_running, threads_connected, blocked(见ASH), qps增量 | `SHOW GLOBAL STATUS LIKE 'Threads_running'`; `... 'Threads_connected'`; `SHOW VARIABLES LIKE 'max_connections'` |
| pgsql | active_sessions, blocked_sessions, conn_used | `SELECT count(*) FILTER (WHERE state='active'), count(*) FILTER (WHERE wait_event_type='Lock') FROM pg_stat_activity` |
| oracle | active_sessions, blocked, conn | `SELECT status,count(*) FROM v$session GROUP BY status`; `SELECT count(*) FROM v$session WHERE blocking_session IS NOT NULL` |
| dm | 同 oracle 风格 v$session | 见 dm.py 现有查询 |

黄金量只在**触发 L1 硬阈值**时发 event（见 §5），平时只写 latency 到 metric_point，不发事件。

### 2.2 ASH-lite 采样器（在 sentinel.py 内，每实例线程按 ASH_INTERVAL_SEC 节奏）

**每 db_type 的采样 SQL（取当前活动会话 + 阻塞关系）**：

**MySQL / TDSQL / GBase**（经 proxy 对 TDSQL 也可用）：
```sql
-- 活动会话
SELECT id AS session_id, user AS user_name, host AS client_host, db AS db_name,
       command, state, time AS active_secs,
       LEFT(COALESCE(info,''),200) AS sql_text
FROM information_schema.processlist
WHERE command NOT IN ('Sleep','Daemon');
-- 阻塞关系 (MySQL8/TXSQL)
SELECT r.trx_mysql_thread_id AS waiter, b.trx_mysql_thread_id AS blocker,
       TIMESTAMPDIFF(SECOND,r.trx_started,NOW()) AS wait_secs
FROM performance_schema.data_lock_waits w
JOIN information_schema.innodb_trx b ON w.blocking_engine_transaction_id=b.trx_id
JOIN information_schema.innodb_trx r ON w.requesting_engine_transaction_id=r.trx_id;
```
将阻塞关系合并进会话样本：waiter 会话 `is_blocked=true, blocker_id=blocker`。

**PostgreSQL**：
```sql
SELECT pid AS session_id, usename AS user_name, client_addr AS client_host,
       datname AS db_name, state AS command, wait_event AS wait_event,
       EXTRACT(EPOCH FROM (now()-query_start))::int AS active_secs,
       LEFT(query,200) AS sql_text,
       (pg_blocking_pids(pid))[1] AS blocker_id,
       cardinality(pg_blocking_pids(pid))>0 AS is_blocked
FROM pg_stat_activity WHERE state IS NOT NULL AND state<>'idle';
```

**Oracle / DM**：
```sql
SELECT s.sid||','||s.serial# AS session_id, s.username AS user_name,
       s.machine AS client_host, s.status AS command, s.event AS wait_event,
       s.last_call_et AS active_secs, s.blocking_session AS blocker_id,
       CASE WHEN s.blocking_session IS NOT NULL THEN 1 ELSE 0 END AS is_blocked
FROM v$session s WHERE s.type='USER' AND s.status='ACTIVE';
```

**写入**：批量 `INSERT INTO session_sample`（复用 timeseries 连接模式，autocommit）。
每次采样一批行，`time=now()`。**采样即检测**：本批若出现 `is_blocked=true` 的会话，
立即触发 L1 blocked_session 检测（见 §5.2），不等下一轮。

**TDSQL 特例**：
- 集中式(noshard)：直连 proxy 采 processlist 即可。
- 分布式(groupshard)：proxy 的 processlist 是网关会话；如需后端 set 明细，用
  `show routes` 得到各 set 主节点地址后可选直连（本期 ASH 先只采 proxy 层，够检测阻塞；
  跨 set 深采列 6B 增强）。
- 集中式 digest 为空的 Top SQL：由 session_sample 按 `sql_digest`/`sql_text` 分组计数兜底
  （6B 诊断消费）。

### 2.3 collector 瘦身（改造 `start_monitor.py`）

**移出采集环的重活**（改为独立低频，见工单 6A-10）：
- D5 慢查询分析（`slow_query_engine`）→ 独立任务 15min 一次。
- D6 配置检查（`config_advisor`）→ 独立任务 1h 一次。

**采集环保留**：连接→collect_metrics→落库(PG/TSDB/ES)→**发 event 到 Stream**。
Phase 2 引擎（基线/健康/容量/RCA v1）从"每轮内联"改为：基线更新保留（轻量 Welford），
其余移到 pipeline_worker 或低频任务。采集环单轮预算回到"60s 内必然完成"。

**collector 发事件**：采集落库后，对本轮指标跑 L1/L2 检测（§5），命中则
`XADD dbaiops:events`。发事件用新工具 `monitor/redis_bus.py::emit_event(dict)`。

---

## 3. redis_bus.py（新增，Stream 读写唯一入口）

```python
# monitor/redis_bus.py  —— 契约见 00_conventions.md#3
STREAM_EVENTS = "dbaiops:events"
STREAM_DIAG   = "dbaiops:diagnosis"
STREAM_VERIFY = "dbaiops:verify"

def get_bus():  # 复用 settings.REDIS_URL
    import redis
    from django.conf import settings
    return redis.from_url(settings.REDIS_URL)

def emit_event(payload: dict) -> str:
    """payload 必须符合 conventions#3.1；自动补 schema/event_uid/created。返回 stream id"""
    ...  # XADD dbaiops:events MAXLEN ~ PIPELINE_STREAM_MAXLEN * {k:json.dumps(v)}

def emit_diagnosis(incident_id, config_id, trigger): ...
def emit_verify(payload): ...

def ensure_groups():
    """幂等创建 cg_detect/cg_diag/cg_verify (XGROUP CREATE ... MKSTREAM)"""
```

序列化：Stream 字段值统一 `json.dumps`，消费端 `json.loads`；顶层扁平以便 `XADD` field=value。
实际实现可整体塞一个 `data` field（`XADD ... data <json>`），简化编解码——**本期采用单 data 字段方式**，
消费端 `json.loads(fields[b'data'])`。

---

## 4. 支柱二：事件-事故-问题 —— 施工细节

### 4.1 聚合键与去重

- **Event.dedup_key** = `f"{config_id}:{signal}"`（部分信号带子键，如 space_high 带表空间名：
  `f"{config_id}:space_high:{tablespace}"`）。
- **Incident.dedup_key** 同 Event.dedup_key 的"实例:类别"归并：`f"{config_id}:{category}"`
  + 5min 滑动窗。即**同实例同类别 5 分钟内只有一个 open 事故**，新事件并入该事故并
  `event_count += 1`、更新 `updated_at`。
- **因果折叠**：若新事件 signal 属于某 open 事故根因规则的因果链下游（因果映射表见 6B），
  并入该事故而非新建。6A 先实现"同 category 归并"，因果折叠留 6B 增强（需 RCA 规则）。

### 4.2 Incident 状态机（合法转移，非法转移返回 CONFLICT）

```
open ──(诊断入队)──▶ diagnosing ──(方案就位)──▶ plan_ready
plan_ready ──(开始执行)──▶ executing ──(执行完成)──▶ verifying
verifying ──(指标恢复)──▶ resolved ──(人工/自动归档)──▶ closed
verifying ──(未恢复,超窗)──▶ plan_ready   # 回退再规划(升级)
任意非closed ──(人工强制解决)──▶ resolved
任意非closed ──(误报/维护)──▶ closed(带 reason)
resolved ──(复发)──▶ open   # 由新事件触发, 记 flapping
```
- 转移入口：模型方法 `Incident.transition(to_status, actor, reason='')`，内部校验
  `ALLOWED_TRANSITIONS` 字典，非法转移 `raise IncidentStateError`（API 层转 409/CONFLICT）。
- 每次转移写时间戳字段（detected_at/plan_ready_at/executing_at/resolved_at/closed_at）
  与一条 `AuditLog`（复用现有审计模型）。
- 6A 只需实现 open→diagnosing 的自动触发（发 diagnosis 事件）与 open/closed（误报关闭）；
  其余状态由 6B/6C 驱动，但状态机与字段**本期一次性建全**。

### 4.3 优先级定级矩阵（生成事故时计算 priority）

`priority = f(影响面, 紧急度)`：

**影响面（impact_scope）** 由关联业务系统重要度决定（BusinessSystem.importance，经
config→business_systems 反查取最高级）：

| 业务重要度 | 影响面等级 |
|-----------|-----------|
| critical/核心 | 高 |
| important | 中 |
| normal / 无关联 | 低 |

**紧急度（urgency）** 由 signal 决定：

| 紧急度 | signals |
|--------|---------|
| 高 | instance_down, blocked_session, conn_storm, repl_broken |
| 中 | conn_high, space_high, deadlock_surge, repl_lag, slow_surge |
| 低 | config_drift, baseline_deviation |

**矩阵**：

| 影响面\紧急度 | 高 | 中 | 低 |
|--------------|----|----|----|
| 高 | P1 | P1 | P2 |
| 中 | P1 | P2 | P3 |
| 低 | P2 | P3 | P4 |

实现：`monitor/incident_manager.py::compute_priority(config, signal) -> 'P1'..'P4'`。

### 4.4 风暴与抖动抑制

- **风暴**：窗口内（1min）某 config 产生 event 数 > `INCIDENT_STORM_THRESHOLD`(10)，
  则本 config 后续事件全部并入一个标 `is_storm=True` 的事故（title 前缀"【事件风暴】"），
  且该事故只发一次通知。计数用 Redis `INCR`+`EXPIRE 60`。
- **抖动**：同 dedup_key 在 `INCIDENT_FLAPPING_WINDOW_MIN`(10min) 内 fire/resolve
  ≥ `INCIDENT_FLAPPING_COUNT`(3) 次，事故标 `is_flapping=True`，priority 上调一级，
  抑制重复通知（改为窗口末汇总）。

### 4.5 incident_manager.py（新增，事故生成/聚合核心）

```python
# monitor/incident_manager.py
def ingest_event(evt: dict) -> Incident:
    """检测消费组调用: 落 Event → 聚合/新建 Incident → 触发诊断入队。
       返回归属 Incident。全流程幂等(event_uid 去重)。"""
    # 1. 幂等: event_uid 已存在则直接返回其 incident
    # 2. 落 Event 行
    # 3. 风暴计数(Redis INCR)；超阈走风暴合并
    # 4. 找 5min 窗内 open 的同 dedup_key(实例:category) 事故:
    #      有 → 并入(event_count++, updated_at, event.incident=inc)
    #      无 → 新建 Incident(status=open, occurred_at=evt.occurred_at,
    #             detected_at=now, priority=compute_priority(...),
    #             health_snapshot=最近HealthScore)
    # 5. 抖动检测 → 标记
    # 6. 新建的 open 事故: transition(open→diagnosing) 且 emit_diagnosis(...)
    # 7. 更新 Problem 签名计数
    # 8. 返回 incident
```

---

## 5. 支柱三：三层检测 —— 规则与判定式

> 检测器统一签名：`detect(config, sample_or_metrics) -> list[event_dict]`。
> 放 `monitor/detectors/`（新目录）：`l1_hard.py` / `l2_baseline.py` / `l3_composite.py`。

### 5.1 L1 硬阈值（确定性，秒级，数据源=哨兵/ASH/采集）

| 规则 | 判定式 | signal | severity | 数据源 |
|------|--------|--------|----------|--------|
| 实例宕机 | 哨兵连续失败≥3 且 90s 无成功采集 | instance_down | critical | sentinel |
| 阻塞会话 | ASH 样本中 `is_blocked=true` 会话存在且最长 wait_secs≥30 | blocked_session | critical | ash |
| 连接高水位 | threads_connected/max_connections ≥ 0.95 (pg/oracle 同理) | conn_high | critical | sentinel/collector |
| 空间高水位 | 任一表空间/库 used_pct ≥ 95 (warning:≥90) | space_high | critical/warning | collector |
| 复制中断 | mysql: Slave_IO/SQL_Running≠Yes; pg: 流复制断; oracle: DG apply 停 | repl_broken | critical | collector |

判定式里的常数集中在 `monitor/detectors/thresholds.py`（可被 AlertThresholdTemplate 覆盖）：
```python
CONN_CRIT_PCT=95; CONN_WARN_PCT=80
SPACE_CRIT_PCT=95; SPACE_WARN_PCT=90
BLOCK_WAIT_CRIT_SEC=30; BLOCK_WAIT_WARN_SEC=10
```

### 5.2 阻塞检测细化（锁风暴，1-5-10 承诺重点，全自动路径起点）

ASH 采样每 15s 命中 `is_blocked` 会话即评估：
```
blocked = [s for s in sample if s.is_blocked]
if blocked:
   max_wait = max(s.active_secs for s in blocked)
   waiters  = len(blocked)
   blockers = distinct(s.blocker_id)
   sev = critical if max_wait>=30 else (warning if max_wait>=10 else info)
   if sev in (critical,warning):
      emit_event(signal=blocked_session, severity=sev, value=waiters,
        occurred_at=样本time, dedup_key=f"{cid}:blocked_session",
        detail={ "waiters":waiters, "blockers":list(blockers), "max_wait_sec":max_wait,
                 "chains":[{"blocker":b,"waiter":w,"wait_sec":t}...] })
```
`detail.chains` 是 6C Playbook「kill blocker」的直接输入。检测延迟 ≤ 15s。

### 5.3 L2 自适应基线（复用现有 baseline_engine，补成熟度门槛）

- 复用 `BaselineEngine.check_current_against_baseline(data)` 输出的异常列表。
- **成熟度门槛**（新增）：某 metric 的 168 槽中当前槽样本数 `< 14`（少于两周）时，
  该槽不用基线判定，回退到静态保守阈值（`thresholds.py` 中的 warn/crit）。避免冷启动漏报/误报。
- 输出转 event：signal=baseline_deviation，metric_key=指标名，severity 按现有三重判定，
  category 由 metric→category 映射表（`detectors/metric_category.py`）决定。

### 5.4 L3 复合规则（多指标联合，高置信直升事故）

| 复合规则 | 判定式（同一实例同一时刻） | 产出 signal | severity |
|---------|--------------------------|------------|----------|
| 锁风暴 | blocked_session 命中 且 threads_running 较基线↑≥50% 且 qps↓ | blocked_session | critical (置信+) |
| 连接风暴 | conn 5min 斜率>正常且 threads_connected 突增≥2×基线 | conn_storm | critical |
| 慢查询突增 | slow_queries 增速≥基线3× 或 ASH 中长活动会话数突增 | slow_surge | warning/critical |
| 死锁频发 | innodb_deadlocks 增量 5min≥3 (pg deadlocks 同理) | deadlock_surge | warning |

复合规则命中的事件带 `detail.composite=true` 与命中的各子条件，供 6B RCA 提高置信度。
L3 用 collector 落库后的指标 + 最近 ASH 样本联合判定，运行在 pipeline_worker（检测消费组）
或 collector 尾部（本期放 collector 尾部，减少一次流转延迟）。

---

## 6. pipeline_worker（新增进程，文件 `monitor/pipeline.py` + 命令 `start_pipeline.py`）

**三消费组单进程多线程**（本期规模足够；未来可拆多进程）：
```
线程A cg_detect  : XREADGROUP dbaiops:events → incident_manager.ingest_event → XACK
线程B cg_diag    : XREADGROUP dbaiops:diagnosis → (6B 诊断管道占位, 6A 先落 stub) → XACK
线程C cg_verify  : XREADGROUP dbaiops:verify → (6C 验证回路占位) → XACK
```
- 6A 交付线程A完整（发现闭环），线程B/C 建骨架并 XACK（避免堆积），逻辑在 6B/6C 填。
- 幂等：ingest_event 用 event_uid 去重；消费失败不 XACK → 下次重投（至多 N 次后进死信，记日志）。
- 优雅退出：SIGTERM 时停止 XREADGROUP、处理完在途消息、关闭连接。
- Django ORM 用法：每次处理前 `close_old_connections()`；线程结束/长空闲 `connection.close()`
  （吸取 6C 连接泄漏教训）。

---

## 7. API 契约（6A 交付，前后端唯一契约源）

> 挂 `dbmonitor/urls.py`，视图放 `monitor/api_views_incident.py`。响应含 `code` 包络。

### 7.1 `GET /api/v1/incidents/`  事故列表
Query: `status`, `priority`, `category`, `config_id`, `limit`(默认50)
Resp:
```json
{ "code":"OK", "total": 2, "incidents": [
  { "incident_id":"INC-20260712100000-7", "config_id":7, "db_name":"TDSQL分布式测试库",
    "db_type":"tdsql", "category":"lock", "title":"锁等待阻塞: 3 个会话被阻塞",
    "priority":"P1", "status":"diagnosing", "event_count":5, "is_storm":false,
    "occurred_at":"2026-07-12T10:00:00+08:00", "detected_at":"2026-07-12T10:00:12+08:00",
    "t_detect_sec":12, "sla_detect_ok":true,
    "acked_by":"", "created_at":"..." } ] }
```

### 7.2 `GET /api/v1/incidents/<incident_id>/`  事故详情
Resp 增加 `rca_result` `impact` `plans`（6A 阶段为空对象/数组）、`t_plan_sec` `t_resolve_sec`、
`health_snapshot`。

### 7.3 `GET /api/v1/incidents/<incident_id>/timeline/`  时间线
Resp:
```json
{ "code":"OK", "items": [
  {"kind":"event","at":"...","signal":"blocked_session","severity":"critical","detail":{...}},
  {"kind":"status","at":"...","from":"open","to":"diagnosing","actor":"system"},
  {"kind":"action","at":"...","text":"..."} ] }
```
`items` 按 `at` 升序，合并 Event + 状态转移(AuditLog) + 动作。

### 7.4 `POST /api/v1/incidents/<incident_id>/ack/`  确认
Body `{}`（actor 取自 auth）。Resp `{"code":"OK","status":"...","acked_at":"...","acked_by":"..."}`。
状态非法（已 closed）返回 `{"code":"CONFLICT",...}` 409。

### 7.5 `POST /api/v1/incidents/<incident_id>/close/`  关闭（误报/维护）
Body `{"reason":"误报"}`。写 closed_at + AuditLog。

### 7.6 `GET /api/v1/events/`  事件流（调试/审计）
Query: `config_id`, `signal`, `incident_id`, `limit`. Resp `{"code":"OK","total":N,"events":[...]}`。

**RBAC**：所有接口用 `get_user_database_ids(user)` 过滤 config_id（None=全部）。

---

## 8. 前端（6A 交付最小可视，作战室完整版在 6B）

新增页 `frontend/src/pages/IncidentList.jsx`（路由 `/incidents`，菜单"事故中心"）：
- 表格列（dataIndex 严格用 §7.1 字段名）：incident_id | db_name | category | priority(Tag色:
  P1红/P2橙/P3蓝/P4灰) | status | event_count | **t_detect_sec(带 sla_detect_ok 达标绿/超标红)** |
  occurred_at | 操作(详情/确认)。
- 顶部筛选：status/priority/category/config。
- 数据源 `incidentAPI.list()`（`services/api.js` 新增，端点见 §7）。
- 兜底：`setIncidents(Array.isArray(d)?d:(d.incidents||[]))`（吸取 Phase 5 教训）。

事故详情页 `IncidentDetail.jsx` 6A 先展示概要 + 时间线（rca/impact/plans 区块占位"诊断中"），
6B 补全作战室。

---

## 9. 文件清单（6A 新增/改造）

| 文件 | 操作 | 工单 |
|------|------|------|
| `monitor/models.py` | 加 Event/Incident/Problem | 6A-01 |
| `monitor/migrations/00XX_phase6a.py` | 迁移 | 6A-01 |
| `monitor/timeseries.py` | 加 session_sample 超表 | 6A-03 |
| `monitor/redis_bus.py` | 新建 | 6A-04 |
| `monitor/db_connector.py` | 加 gbase/tdsql 分支 | 6A-02 |
| `monitor/sentinel.py` | 新建 | 6A-05 |
| `monitor/management/commands/start_sentinel.py` | 新建 | 6A-05 |
| `monitor/detectors/{__init__,thresholds,metric_category,l1_hard,l2_baseline,l3_composite}.py` | 新建 | 6A-06 |
| `monitor/incident_manager.py` | 新建 | 6A-07 |
| `monitor/pipeline.py` + `commands/start_pipeline.py` | 新建 | 6A-08 |
| `monitor/management/commands/start_monitor.py` | 瘦身+发事件 | 6A-09,6A-10 |
| `monitor/api_views_incident.py` | 新建 | 6A-11 |
| `dbmonitor/urls.py` | 挂 incident/event 路由 | 6A-11 |
| `frontend/src/pages/IncidentList.jsx`, `IncidentDetail.jsx` | 新建 | 6A-11 |
| `frontend/src/services/api.js` | 加 incidentAPI | 6A-11 |
| `frontend/src/App.jsx`, `components/EMLayout.jsx` | 加路由+菜单 | 6A-11 |
| `phase6/drills/inject_*.py` | 故障注入脚本 | 6A-12 |
| `verify_phase6a.py` | 验收脚本 | 6A-12 |

---

## 10. 施工工单（带验收标准）

> 依赖顺序：6A-01→02→03→04 为地基；05/06/07 可并行；08 依赖 04/06/07；
> 09/10 依赖 04/06；11 依赖 01/07；12 最后。

| 工单 | 标题 | 关键交付 | 验收标准（可执行） |
|------|------|---------|------------------|
| **6A-01** | 事件/事故/问题模型 | 3 表 + 迁移 + 状态机方法 + property | `migrate` 成功；`Incident.transition` 非法转移抛错单测过；字段表 vs models.py 逐字段一致 |
| **6A-02** | DbConnector 扩 gbase/tdsql | 两分支(pymysql) | 对 6 类实例 `get_connection` 均成功 `SELECT 1` |
| **6A-03** | session_sample 超表 | DDL + 保留策略 | `\d session_sample` 存在；insert 一行可查；7 天保留策略生效 |
| **6A-04** | redis_bus + Stream | emit_*/ensure_groups | `XINFO GROUPS` 见 3 消费组；emit 后 `XLEN` 增 |
| **6A-05** | sentinel_daemon | 探活+黄金量+ASH 采样 | 启动后 session_sample 每15s 有新行；kill 目标库进程后 ≤24s 产 instance_down event |
| **6A-06** | 三层检测器 | l1/l2/l3 + thresholds | 单测：构造样本→期望 event；阻塞样本必出 blocked_session |
| **6A-07** | incident_manager | ingest_event 聚合/定级/风暴 | 单测：同 category 5min 内并单事故；11 个事件/分触发 is_storm；P1 矩阵正确 |
| **6A-08** | pipeline_worker | 三消费组，A 完整 | 向 events 投 1 事件→≤2s 生成 Incident；diag/verify 线程 XACK 不堆积 |
| **6A-09** | collector 发事件 | 采集尾部 L1/L2/L3 + emit | 制造 conn_high→采集轮后出 event；采集单轮 ≤60s |
| **6A-10** | collector 瘦身 | D5/D6 移出为低频任务 | 采集环不再含慢查询/配置检查；独立任务按 15min/1h 跑 |
| **6A-11** | Incident API + 前端 | 6 接口 + 两页 + 菜单 | 契约测试 curl 断言字段名；前端事故列表显示真实事故与 t_detect 达标色 |
| **6A-12** | 演练与验收脚本 | inject_* + verify_phase6a | 见 §11 三场景 MTTD ≤60s |

---

## 11. 6A 验收演练（必须在真实/容器库上跑）

`phase6/drills/`：

| 脚本 | 注入动作 | 期望 | 判定 |
|------|---------|------|------|
| `inject_down.py <cid>` | 停目标库容器/kill 会话使连接失败 | ≤24s 出 instance_down 事故 P1/P2 | 查 Incident.occurred→detected ≤60s |
| `inject_lock.py <cid>` | 开事务锁行不提交 + 另会话抢锁 | ≤15s 出 blocked_session 事故，detail.chains 有阻塞对 | t_detect_sec≤60 且 chains 非空 |
| `inject_conn.py <cid>` | 并发开满连接至≥95% | ≤1 采集轮出 conn_high/conn_storm 事故 | t_detect_sec≤60 |

`verify_phase6a.py`（仿 verify_phase5.py 风格）：逐项 import 校验 + 造事件走通 ingest→Incident
+ 查三场景事故存在且 SLA 达标。输出 `通过 N/N`。

**验收总判定**：三场景 MTTD ≤60s，事故不刷屏（风暴合并生效），契约四方一致（字段表/models/
API/前端）。达成即 6A 完成，进入 6B。
