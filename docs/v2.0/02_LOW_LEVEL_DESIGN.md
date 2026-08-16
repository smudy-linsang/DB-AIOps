# DB-AIOps v2.0 智能数据库运维平台 - 详细设计说明书 (LLD)

> **文档版本**：v2.0  
> **编制日期**：2026-08-16  
> **实施指导原则**：照图施工、精确到类/方法/数据流/SQL/异常分支

---

## 1. 监控目标全栈采集详细设计

### 1.1 采集引擎插件化架构设计
继承 `BaseDBChecker` 抽象基类，所有数据库类型 Checker 均须实现标准化接口：

```
                    ┌─────────────────────────┐
                    │      BaseDBChecker      │
                    ├─────────────────────────┤
                    │ + check(config): dict   │
                    │ + check_ash(config):list│
                    │ + test_conn(config):bool│
                    └────────────┬────────────┘
         ┌───────────────┬───────┴───────┬───────────────┐
         ▼               ▼               ▼               ▼
┌─────────────────┐ ┌─────────┐ ┌─────────────────┐ ┌─────────┐
│  OracleChecker  │ │MySQLChk │ │  PostgreSQLChk  │ │DamengChk│
└─────────────────┘ └─────────┘ └─────────────────┘ └─────────┘
```

### 1.2 各数据库内核级 SQL 采集清单（照图施工标准）

#### 1.2.1 Oracle / RAC / ADG 采集模块 (`monitor/checkers/oracle.py`)
1. **RAC Cache Fusion 延迟采样**：
   ```sql
   SELECT inst_id, event, total_waits, time_waited_micro / 1000 AS time_waited_ms, average_wait
   FROM gv$system_event
   WHERE event IN ('gc current block receive time', 'gc cr block receive time', 'gc current block 2-way', 'gc cr block 2-way')
   ```
2. **ADG 备库同步延迟与进程状态**：
   ```sql
   SELECT name, value, unit FROM v$dataguard_stats WHERE name IN ('apply lag', 'transport lag');
   SELECT process, status, sequence#, block# FROM v$managed_standby WHERE process LIKE 'MRP%';
   ```
3. **SGA 组件细分与 PGA 命中率**：
   ```sql
   SELECT component, current_size / 1024 / 1024 AS size_mb FROM v$sga_dynamic_components;
   SELECT name, value FROM v$pgastat WHERE name IN ('aggregate PGA target parameter', 'total PGA allocated', 'PGA cache hit percentage');
   ```

#### 1.2.2 MySQL / TDSQL 采集模块 (`monitor/checkers/mysql.py`)
1. **InnoDB 引擎核心状态解析**：
   - 执行 `SHOW GLOBAL STATUS` 捕获：
     - `Innodb_buffer_pool_pages_dirty` / `Innodb_buffer_pool_pages_total` (脏页率)
     - `Innodb_buffer_pool_read_requests` 与 `Innodb_buffer_pool_reads` (命中率)
     - `Innodb_row_lock_current_waits` / `Innodb_row_lock_time_avg` (锁等待)
2. **行锁阻塞链采集 (MySQL 8.0+ / 5.7 兼容)**：
   ```sql
   SELECT 
       r.trx_id AS waiting_trx_id, r.trx_mysql_thread_id AS waiting_thread,
       r.trx_query AS waiting_query, b.trx_id AS blocking_trx_id,
       b.trx_mysql_thread_id AS blocking_thread, b.trx_query AS blocking_query,
       TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_seconds
   FROM performance_schema.data_lock_waits w
   JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_engine_transaction_id
   JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_engine_transaction_id;
   ```
3. **主从 GTID 与复制延迟**：
   - 执行 `SHOW REPLICA STATUS` / `SHOW SLAVE STATUS` 提取 `Seconds_Behind_Master`、`Retrieved_Gtid_Set`、`Executed_Gtid_Set`。

#### 1.2.3 PostgreSQL 采集模块 (`monitor/checkers/pgsql.py`)
1. **MVCC 死亡元组与膨胀度**：
   ```sql
   SELECT schemaname, relname, n_live_tup, n_dead_tup,
          ROUND(n_dead_tup * 100.0 / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_tup_ratio,
          last_vacuum, last_autovacuum
   FROM pg_stat_user_tables
   WHERE n_dead_tup > 1000 ORDER BY dead_tup_ratio DESC LIMIT 10;
   ```
2. **长事务与锁等待依赖树**：
   ```sql
   SELECT 
       blocked_locks.pid AS waiting_pid, blocked_activity.usename AS waiting_user,
       blocked_activity.query AS waiting_query, blocking_locks.pid AS blocking_pid,
       blocking_activity.usename AS blocking_user, blocking_activity.query AS blocking_query,
       EXTRACT(EPOCH FROM (NOW() - blocked_activity.query_start))::INT AS wait_seconds
   FROM pg_catalog.pg_locks blocked_locks
   JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
   JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
       AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
       AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
       AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
       AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
       AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
       AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
       AND blocking_locks.pid != blocked_locks.pid
   JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
   WHERE NOT blocked_locks.granted;
   ```

---

## 2. 1-5-15 智能分析引擎详细设计

### 2.1 1-Min 感知：多变量时变基线与拓扑收敛算法
- **类名**：`monitor/intelligent_baseline_engine.py` -> `MultiVariateBaselineDetector`
- **输入**：当前 1 分钟内的采集快照 $M_t = \{QPS, CPU, ActiveSessions, LockWaits, DiskIO\}$
- **算法逻辑**：
  1. 计算当前时间槽 $s \in [0, 167]$（周一 00:00 至 周日 23:00）；
  2. 获取历史基线特征均值 $\mu_s$ 与标准差 $\sigma_s$；
  3. 计算偏离度 $Z_i = \frac{M_{t,i} - \mu_{s,i}}{\sigma_{s,i}}$；
  4. 当 $\sum w_i \cdot \mathbb{I}(|Z_i| > 3.0) \ge 0.65$ 时判定为多变量联合突增异常；
  5. **拓扑抑制过滤**：通过 `DatabaseTopology.get_downstream_nodes(root_id)` 检索级联节点，在 300 秒内发生的相同告警类次生事件合并到 root 故障中。

### 2.2 5-Min 定位：RCA 3.0 因果图推导与会话溯源
- **类名**：`monitor/rca_engine_v3.py` -> `CausalInferenceEngine`
- **核心数据结构**：
  ```python
  @dataclass
  class CausalNode:
      node_id: str
      node_type: str  # 'CHANGE' | 'SQL' | 'RESOURCE' | 'LOCK' | 'CLUSTER'
      name: str
      evidence_refs: List[str]
      timestamp: datetime
      metrics: Dict[str, Any]

  @dataclass
  class IncidentCauseGraph:
      incident_id: str
      root_node: CausalNode
      propagation_path: List[Tuple[CausalNode, CausalNode, str]] # (Source, Target, Reason)
      confidence: float
  ```
- **推导流程**：
  1. 从 `ChangeEvent` 获取近 2 小时发生的 DDL、配置变更作为先验根源候选；
  2. 从 `BlockingTreeView` 获取持有锁超时的 Top Blocker PID/SID 对应的 SQL 指纹；
  3. 结合等待事件分类权重（如 `enq: TX` -> 事务行锁，`db file sequential read` -> 索引失效全表扫）；
  4. 输出树形因果链至 `monitor_incident_cause_chain` 表。

### 2.3 15-Min 解决：自愈 Playbook 决策与安全沙箱 (Dry-Run)
- **类名**：`monitor/remediation_planner.py` -> `PlaybookExecutor`
- **Dry-Run 预演算法**：
  1. **输入**：`incident_id`, `playbook_code`, `target_config_id`, `params`
  2. **模拟执行**：
     - 若为 `KILL_SESSION`：查询被终止会话当前持有锁资源数、属于哪一客户端 IP/用户，判断是否属于核心中间件/主控服务；若为受保护账号（如 `root`, `rdsadmin`, `SYS`），Dry-Run 判定失败（`REJECT_PROTECTED_USER`）；
     - 若为 `RESIZE_TABLESPACE`：检查底层宿主机物理磁盘剩余容量是否足够扩容，计算扩容后剩余空间利用率；
  3. **输出**：
     ```json
     {
       "dryrun_status": "PASSED",
       "impact_level": "LOW",
       "affected_sessions": 1,
       "released_locks_estimate": 14,
       "rollback_plan_ready": true,
       "estimated_recovery_seconds": 5
     }
     ```
