# DB-AIOps v2.0 智能数据库运维平台 - 数据库设计说明书 (DDL)

> **文档版本**：v2.0  
> **编制日期**：2026-08-16  
> **设计标准**：严格符合 **4NF（第四范式）** 规范，杜绝非平凡多值依赖，时序数据采用 TimescaleDB Hypertable 优化。

---

## 1. 概念模型与 E-R 关系图

```
┌────────────────────────┐         ┌───────────────────────────────┐
│     DatabaseConfig     │1       1│  DatabaseMetricProfile (4NF)  │
│  (数据库实例基础配置)  │─────────│   (168h 多维基线与资源画像)   │
└──────────┬─────────────┘         └───────────────────────────────┘
           │1
           │
           │*
┌──────────▼─────────────┐1       *┌───────────────────────────────┐
│        Incident        │─────────│   IncidentCauseChain (4NF)    │
│  (故障事故聚合总单)    │         │    (因果图谱推导节点证据链)   │
└──────────┬─────────────┘         └───────────────────────────────┘
           │1
           │*
┌──────────▼─────────────┐*       1┌───────────────────────────────┐
│      PlaybookRun       │─────────│       PlaybookTemplate        │
│   (自愈工单执行记录)   │         │       (标准化应急预案)        │
└────────────────────────┘         └───────────────────────────────┘
```

---

## 2. 关系型数据库表结构设计 (PostgreSQL 4NF)

### 2.1 实例多维画像基线表 (`monitor_database_metric_profile`)
- **说明**：存储纳管实例的硬件配置、业务负载分类（OLTP/OLAP）及高峰时间段。

```sql
CREATE TABLE monitor_database_metric_profile (
    id SERIAL PRIMARY KEY,
    config_id INT NOT NULL REFERENCES monitor_databaseconfig(id) ON DELETE CASCADE,
    profile_type VARCHAR(32) NOT NULL DEFAULT 'mixed', -- 'oltp', 'olap', 'mixed', 'batch'
    cpu_cores INT,
    memory_gb NUMERIC(8,2),
    data_disk_gb NUMERIC(10,2),
    max_qps INT DEFAULT 0,
    peak_hours_json JSONB DEFAULT '[]'::jsonb, -- 典型高峰时段数组 [9, 10, 11, 14, 15, 16]
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_db_profile_config UNIQUE (config_id)
);

CREATE INDEX idx_db_profile_type ON monitor_database_metric_profile(profile_type);
```

### 2.2 事故因果链明细表 (`monitor_incident_cause_chain`)
- **说明**：将一次故障中推理出的多步级联因果链分步存储，满足 4NF 规范，杜绝在 `Incident` 单表中堆砌 JSON 字符串。

```sql
CREATE TABLE monitor_incident_cause_chain (
    id SERIAL PRIMARY KEY,
    incident_id VARCHAR(64) NOT NULL REFERENCES monitor_incident(incident_id) ON DELETE CASCADE,
    step_seq INT NOT NULL, -- 步骤顺序 1, 2, 3...
    node_type VARCHAR(32) NOT NULL, -- 'CHANGE', 'SQL', 'RESOURCE', 'LOCK', 'CLUSTER'
    node_name VARCHAR(128) NOT NULL, -- 节点名称，如 '行锁超时 (SID 1845)'
    description TEXT NOT NULL,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb, -- 关联证据编码，如 ['E1', 'E3']
    confidence NUMERIC(4,3) NOT NULL DEFAULT 1.000, -- 该节点推理置信度 0.000 - 1.000
    metric_snapshot JSONB DEFAULT '{}'::jsonb, -- 触发时的指标快照
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_inc_step UNIQUE (incident_id, step_seq)
);

CREATE INDEX idx_cause_chain_node_type ON monitor_incident_cause_chain(node_type);
```

### 2.3 自愈剧本模板表 (`monitor_playbook_template`)
- **说明**：存储标准化自愈应急预案定义、执行契约与回滚方案。

```sql
CREATE TABLE monitor_playbook_template (
    id SERIAL PRIMARY KEY,
    code VARCHAR(64) UNIQUE NOT NULL, -- 唯一标识码，如 'KILL_ROOT_BLOCKER'
    name VARCHAR(128) NOT NULL,
    db_types JSONB NOT NULL DEFAULT '[]'::jsonb, -- 适用的数据库类型 ['oracle', 'mysql', 'pgsql']
    risk_level VARCHAR(16) NOT NULL DEFAULT 'medium', -- 'low', 'medium', 'high', 'critical'
    min_autonomy_level INT NOT NULL DEFAULT 1, -- 自动执行所需最低自治等级 (0-3)
    steps_payload JSONB NOT NULL DEFAULT '[]'::jsonb, -- 执行步骤序列
    rollback_payload JSONB NOT NULL DEFAULT '[]'::jsonb, -- 逆向回滚步骤序列
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_playbook_code ON monitor_playbook_template(code);
```

### 2.4 自愈预演与执行记录表 (`monitor_playbook_run`)
```sql
CREATE TABLE monitor_playbook_run (
    run_id VARCHAR(64) PRIMARY KEY, -- 'RUN-20260816-XXXX'
    incident_id VARCHAR(64) REFERENCES monitor_incident(incident_id) ON DELETE SET NULL,
    template_code VARCHAR(64) NOT NULL,
    config_id INT NOT NULL REFERENCES monitor_databaseconfig(id) ON DELETE CASCADE,
    operator VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending', -- 'dryrun_passed', 'approved', 'running', 'success', 'failed', 'rolled_back'
    dryrun_result JSONB DEFAULT '{}'::jsonb,
    execution_result JSONB DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_playbook_run_status ON monitor_playbook_run(status);
```

---

## 3. 时序数据库设计 (TimescaleDB Hypertable)

### 3.1 活跃会话历史表 (`active_session_history`)
- **高频采样**：每 10 秒采样一次活跃会话，通过 TimescaleDB Hypertable 支撑海量数据。

```sql
CREATE TABLE active_session_history (
    sample_time TIMESTAMPTZ NOT NULL,
    config_id INT NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    serial_num VARCHAR(64),
    username VARCHAR(64),
    client_ip VARCHAR(64),
    program VARCHAR(128),
    sql_id VARCHAR(64),
    sql_text_digest VARCHAR(64),
    event_name VARCHAR(128),
    wait_class VARCHAR(64), -- 'CPU', 'User I/O', 'Lock', 'Cluster'
    wait_time_ms INT DEFAULT 0,
    blocking_session_id VARCHAR(64) -- 阻塞源会话 ID
);

-- 转换为 TimescaleDB Hypertable（按 1 天分区）
SELECT create_hypertable('active_session_history', 'sample_time', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);

-- 建立复合索引
CREATE INDEX idx_ash_search ON active_session_history(config_id, sample_time DESC);
CREATE INDEX idx_ash_sql ON active_session_history(sql_id, sample_time DESC);

-- 开启 7 天后自动压缩
ALTER TABLE active_session_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'config_id, wait_class'
);
SELECT add_compression_policy('active_session_history', INTERVAL '7 days');
```
