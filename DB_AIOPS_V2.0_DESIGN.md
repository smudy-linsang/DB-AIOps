# DB-AIOps v2.0 智能数据库运维平台升级改造设计方案

> **版本号**：v2.0  
> **设计日期**：2026-08-16  
> **设计定位**：打造真正面向 DBA 的全栈智能智囊团与核心生产力中枢  
> **核心战役目标**：**1 分钟发现问题（1-Min Detect）、5 分钟定位根因（5-Min Root Cause）、15 分钟闭环解决（15-Min Remediation）**

---

## 目录
1. [项目背景与核心升级愿景](#1-项目背景与核心升级愿景)
2. [1-5-15 目标技术分解与系统总体架构](#2-1-5-15-目标技术分解与系统总体架构)
3. [监控目标采集丰富度升级（概要设计与指标体系）](#3-监控目标采集丰富度升级概要设计与指标体系)
4. [智能化分析与智囊团体系（详细设计）](#4-智能化分析与智囊团体系详细设计)
5. [数据库模型与存储架构设计（4NF + 时序优化）](#5-数据库模型与存储架构设计4nf--时序优化)
6. [前端用户体验与美工交互设计（UI/UX 规范）](#6-前端用户体验与美工交互设计uiux-规范)
7. [核心 REST API 接口设计与契约规范](#7-核心-rest-api-接口设计与契约规范)
8. [实施计划与安全保障机制](#8-实施计划与安全保障机制)

---

## 1. 项目背景与核心升级愿景

### 1.1 现状痛点诊断
目前 DB-AIOps 平台在基础架构、鉴权与部分监控上有了一定基础，但在实际 DBA 深度日常运维中存在以下瓶颈：
1. **指标采集广度与深度不足**：缺乏内核级深度视图（如 Oracle Cache Fusion、MySQL InnoDB Buffer Pool 脏页/自适应哈希、PostgreSQL WAL 与长事务 Vacuum 积压、达梦锁链表等），导致许多隐蔽性故障成为盲区。
2. **分析智能化停留在单点推断**：缺乏时序多变量联合检测、动态拓扑级因果推导与长周期自适应演进，报警误报/漏报频发。
3. **操作割裂、用户体验欠缺**：DBA 在发现告警后，需在列表、详情、性能中心、日志页面来回跳转查找 SQL 或会话，缺乏沉浸式“一站式排障控制台”与自动化安全执行闭环。

### 1.2 v2.0 升级目标：1-5-15 运维时效矩阵
- **1 分钟发现问题（1-Min Detect）**：
  - 秒级内核探针 + Redis 事件流总线 + 动态多维基线（结合 168h 周期与突增检测），异常发生 60 秒内聚合生成统一故障单（Incident），并完成告警拓扑收敛（降噪 85%+）。
- **5 分钟定位根因（5-Min Locate）**：
  - 自动触发 **RCA 3.0 因果图谱推导** + **ASH/AAS 会话级等待链下钻** + **LLM DBA Agent 主动探查**，5 分钟内自动输出带证据链（E1-En）、代码指纹与置信度的《根因排查简报》。
- **15 分钟闭环解决（15-Min Remediate）**：
  - 针对定位出的根因，系统自动关联预置的 **参数调优/索引建议/会话截断/表空间扩容 Playbook**，结合 Dry-Run 预演沙箱与双人审批流，15 分钟内完成安全处置与回滚验证。

---

## 2. 1-5-15 目标技术分解与系统总体架构

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              DB-AIOps v2.0 系统总体架构                                 │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 【体验交互层 (UI/UX)】                                                                 │
│   • DBA 沉浸式作战指挥室 (WarRoom)   • 1-Click 应急快切与会话/锁熔断     • 交互式时光机 (Replay)  │
│   • 动态因果关系拓扑下钻 (Graph UI)   • 全局 Copilot 智能辅助中枢 (Drawer) • 移动端/企微快速审批   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 【智能中枢层 (Brain & Engines)】                                                       │
│   ┌────────────────────────┬────────────────────────┬────────────────────────────────┐ │
│   │  1-Min 智能异常感知    │  5-Min 根因推理图谱    │  15-Min 故障自愈沙箱           │ │
│   │  • 多变量时序基线检测  │  • RCA 3.0 因果图推导  │  • 智能 Playbook 决策引擎      │ │
│   │  • 拓扑告警关联与降噪  │  • ASH 会话阻塞溯源    │  • Dry-Run 预演与影响评估      │ │
│   │  • 变更事件关联 (DDL)  │  • LLM Multi-Agent     │  • L0-L3 安全分级与回滚机制    │ │
│   └────────────────────────┴────────────────────────┴────────────────────────────────┘ │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 【内核采集与分析层 (Checkers & Engines)】                                             │
│   • 异构 DB 深度采集器 (Oracle RAC/ADG, MySQL InnoDB, PG, DM8, TDSQL, GBase 8a)        │
│   • ASH/AAS 实时会话流采样器 (10s 粒度) • 慢查询与 SQL 执行计划捕获器 (SQL Plan/Explain)│
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 【高性能存储底座 (Storage Fabric)】                                                    │
│   • PostgreSQL (Django ORM 4NF 元数据) • TimescaleDB (高吞吐时序指标 Hypertable)       │
│   • Elasticsearch 8.x (案例向量检索/日志聚合) • Redis 7.x (事件总线/分布式锁/会话状态)   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 监控目标采集丰富度升级（概要设计与指标体系）

v2.0 将各类主流数据库的监控指标从当前的 ~40 项大幅扩充至 **120+ 核心指标项**，覆盖底层存储、内核内存、会话等待、集群复制与安全配置。

### 3.1 Oracle / RAC / ADG 全栈指标
- **RAC & Cache Fusion**：`gc current block receive/serve time`、`gc cr block receive/serve time`、`interconnect traffic`、DRM (Dynamic Resource Mastering) 状态。
- **ADG (Active Data Guard)**：`apply lag seconds`、`transport lag seconds`、`mrp process status`、`standby redo log gaps`、`fast sync status`。
- **内存与存储**：SGA 组件（Buffer Cache, Shared Pool, Large Pool）、PGA Hit Ratio、Undo Retention 饱和度、Temp 表空间会话占用 Top 10。
- **等待事件**：Top 10 等待事件（按 `wait_class` 分类：Concurrency, Cluster, User I/O, System I/O, Commit）。

### 3.2 MySQL / TDSQL 高并发与高可用指标
- **InnoDB 内核**：`innodb_buffer_pool_dirty_pct`、`innodb_buffer_pool_hit_rate`、`innodb_rows_read/inserted/updated/deleted`、`innodb_log_waits`、自适应哈希索引 (AHI) 效率。
- **锁与事务**：`innodb_row_lock_waits`、`innodb_row_lock_time_avg`、长事务列表（>30s）、锁等待关系图谱。
- **复制与集群**：`Seconds_Behind_Master`、`GTID Executed/Purged gap`、半同步复制延迟与退化状态、TDSQL Set 级分片负载与 Group Replication 状态。

### 3.3 PostgreSQL 关键特性指标
- **MVCC & Vacuum**：`n_dead_tup`、`vacuum_count`、`autovacuum_freeze_max_age` 耗尽风险、TxID Wraparound 剩余事务数。
- **锁与长事务**：`pg_locks` 依赖关系、`idle in transaction` 持续时间 Top 10、复制槽延迟（`pg_replication_slots.wal_status`）。
- **缓存与写入**：`shared_buffers hit ratio`、`checkpoint write/sync time`、WAL 产生速率 (MB/s)。

### 3.4 达梦 (DM8) / GBase 8a 专有指标
- **DM8**：MAL 通信延迟、DSC 集群缓存交换状态、DW 守护进程状态、Redo 刷盘耗时。
- **GBase 8a**：集群 Coordinator/Data 节点通信状态、GCware 选举状态、Hash 分布倾斜度。

---

## 4. 智能化分析与智囊团体系（详细设计）

### 4.1 1-Min 智能告警感知与拓扑收敛
1. **多变量时序异常检测**：
   - 摒弃单一静态绝对阈值，采用 `动态时变基线 (168h 时间槽)` + `IQR 稳健突增检测`。
   - 当 CPU、QPS、活跃会话同时出现协方差偏离时，触发高优先级聚合告警。
2. **拓扑级告警降噪**：
   - 基于数据库依赖图（主从、RAC 节点、业务系统），当主库发生长事务或夯死时，自动将从库延迟、应用连接池爆满等次生告警归纳至同一个 **`Incident` 根因事件**，推送唯一定位工单。

### 4.2 5-Min 深度根因分析 (RCA 3.0 + Multi-Agent)
1. **RCA 3.0 因果图推导**：
   - 预设因果传递图（如：`慢 SQL -> CPU/IO 耗尽 -> 锁等待堆积 -> 连接池打满 -> 服务拒绝`）。
   - 实时采集快照与因果图做图匹配计算，输出置信度排序的根因链。
2. **LLM Multi-Agent 协同会诊**：
   - **Triage Agent**（分流过滤）：分析指标模式与告警分类；
   - **DBA Expert Agent**（专家深度分析）：结合特定引擎（如 Oracle RAC Cache Fusion / PG Vacuum）调用只读工具收集日志与会话数据；
   - **Verification Agent**（交叉验证）：验证推导假设与历史案例库（RAG Vector Search）相似度，生成带证据的《5分钟定位报告》。

### 4.3 15-Min 故障自愈与 Playbook 执行闭环
1. **风险等级与分级放权（L0-L3）**：
   - **L0 (观察)**：仅出建议与排查报告；
   - **L1 (半自动)**：生成处置方案，需 DBA 一键审批；
   - **L2 (低危自愈)**：安全操作（如 Kill 超过 30 分钟的 Idle in Transaction、归档日志转储）自动执行；
   - **L3 (全自动)**：主从倒换、动态升配等高危操作受策略约束自动触发。
2. **Dry-Run 预演与回滚保障**：
   - 执行前评估影响会话数与事务状态；
   - 每个 Playbook 均必须具备对应逆向回滚步骤，超时或异常自动 Rollback。

---

## 5. 数据库模型与存储架构设计（4NF + 时序优化）

### 5.1 核心数据表结构扩展 (PostgreSQL 4NF)

#### 表 1：`monitor_database_metric_profile` (实例多维特征与配置基线表)
```sql
CREATE TABLE monitor_database_metric_profile (
    id SERIAL PRIMARY KEY,
    config_id INT NOT NULL REFERENCES monitor_databaseconfig(id) ON DELETE CASCADE,
    profile_type VARCHAR(32) NOT NULL, -- 'oltp', 'olap', 'mixed', 'batch'
    cpu_cores INT,
    memory_gb NUMERIC(8,2),
    data_disk_gb NUMERIC(10,2),
    max_qps INT DEFAULT 0,
    peak_hours_json JSONB DEFAULT '[]'::jsonb, -- 高峰时间槽
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_db_profile UNIQUE (config_id)
);
```

#### 表 2：`monitor_incident_cause_chain` (事故因果推理链明细表)
```sql
CREATE TABLE monitor_incident_cause_chain (
    id SERIAL PRIMARY KEY,
    incident_id VARCHAR(64) NOT NULL REFERENCES monitor_incident(incident_id) ON DELETE CASCADE,
    step_seq INT NOT NULL,
    node_type VARCHAR(32) NOT NULL, -- 'metric', 'event', 'change', 'sql', 'lock'
    node_name VARCHAR(128) NOT NULL,
    description TEXT,
    evidence_refs JSONB DEFAULT '[]'::jsonb, -- ['E1', 'E2']
    confidence NUMERIC(4,3) DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_cause_chain_inc ON monitor_incident_cause_chain(incident_id, step_seq);
```

#### 表 3：`monitor_playbook_template` (故障自愈剧本模板表)
```sql
CREATE TABLE monitor_playbook_template (
    id SERIAL PRIMARY KEY,
    code VARCHAR(64) UNIQUE NOT NULL, -- 'KILL_LONG_TRANSACTION', 'EXTEND_TABLESPACE', 'FLUSH_BUFFER'
    name VARCHAR(128) NOT NULL,
    db_types JSONB NOT NULL, -- ['oracle', 'mysql', 'pgsql']
    risk_level VARCHAR(16) NOT NULL, -- 'low', 'medium', 'high', 'critical'
    min_autonomy_level INT DEFAULT 1,
    steps_payload JSONB NOT NULL, -- 步骤定义、参数、执行命令、回滚命令
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

### 5.2 时序存储优化 (TimescaleDB Hypertable)
针对 `active_session_history` 与 `metric_timeseries` 建立 7 天 Chunk 自动滚动分区与数据压缩策略（Compression Policy），支撑 200+ 数据库实例每 10 秒一次高频采样。

---

## 6. 前端用户体验与美工交互设计（UI/UX 规范）

### 6.1 视觉风格与调色板规范 (Design Tokens)
- **主色系**：极客沉浸深蓝与专业科技蓝 (`#0F172A` 底色, `#1E293B` 卡片背景, `#3B82F6` 主动作蓝)。
- **状态高光色**：
  - 正常 (Healthy): `#10B981` (Emerald)
  - 警告 (Warning): `#F59E0B` (Amber)
  - 严重 (Critical): `#EF4444` (Rose)
  - 智能化/AI (AI Accent): `#8B5CF6` 至 `#6366F1` 渐变霓虹紫。
- **排版字体**：`Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`，等宽数字字体展示 QPS / 延迟数据。

### 6.2 核心工作台布局设计

#### 1. 【一站式排障作战室 (Incident WarRoom)】
- **顶部故障全景条**：包含故障严重度、持续时间秒表倒计时（距 1-5-15 目标差距）、受影响业务拓扑。
- **左侧时光机与多维指标关联轴**：可前后拖动时间轴（±30分钟），同步联动 QPS、CPU、等待事件柱状堆叠图。
- **中间因果图谱与根因树**：可视化的 Causal Flow 节点关系，点击节点展示支撑证据与相关 SQL。
- **右侧处置推荐与一键闭环面板**：推荐处置 Playbook，支持参数快速微调、Dry-Run 预演结果对比与一键审批执行。

#### 2. 【数据库 360° 性能中枢 (Performance Hub 2.0)】
- **ASH / AAS 实时热力图**：按等待类别（CPU, User I/O, Lock 等）堆叠展示，支持框选放大。
- **实时阻塞树拓扑 (Blocking Tree)**：红色标注根源阻塞源（Root Blocker），展示持锁 SQL、等待时长、客户端 IP，支持右键一键安全 Kill。
- **Top SQL 智能体检卡片**：自动展示慢 SQL 指纹、执行计划变异（Plan Regression）、索引建议与改写提示。

---

## 7. 核心 REST API 接口设计与契约规范

### 7.1 接口列表汇总

| 模块 | 方法 | 端点路径 | 说明 | 权限 |
| :--- | :--- | :--- | :--- | :--- |
| **感知** | GET | `/api/v2/incidents/realtime-active/` | 获取 1 分钟内发生的实时聚合故障 | `alerts.view` |
| **分析** | GET | `/api/v2/incidents/{id}/warroom-context/` | 获取 WarRoom 全景（指标+因果链+证据） | `alerts.view` |
| **分析** | POST | `/api/v2/databases/{id}/rca-drilldown/` | 针对特定时间段触发多维 RCA 深度钻取 | `metrics.view` |
| **自愈** | GET | `/api/v2/playbooks/matched/{incident_id}/` | 匹配当前故障的推荐 Playbook 方案 | `tickets.view` |
| **自愈** | POST | `/api/v2/playbooks/execute-dryrun/` | 预演 Playbook 并输出安全评估报告 | `tickets.execute` |
| **自愈** | POST | `/api/v2/playbooks/execute-safely/` | 正式执行自愈工单（支持会话阻断/回滚） | `tickets.execute` |
| **性能** | GET | `/api/v2/databases/{id}/blocking-graph/` | 获取实时交互式死锁与阻塞依赖图谱 | `metrics.view` |
| **智能** | POST | `/api/v2/copilot/dba-expert-chat/` | Copilot DBA 专家深度诊断会话 | `metrics.view` |

### 7.2 核心接口请求与响应示例

#### 接口：`GET /api/v2/incidents/{incident_id}/warroom-context/`
**响应示例**：
```json
{
  "code": "OK",
  "data": {
    "incident_id": "INC-20260816-001",
    "title": "Oracle 核心交易库行锁等待堆积与连接耗尽",
    "severity": "critical",
    "duration_seconds": 185,
    "sla_stage": "locating", // detecting / locating / remediating / resolved
    "metrics_snapshot": {
      "active_sessions": 142,
      "cpu_usage_pct": 89.5,
      "top_wait_event": "enq: TX - row lock contention"
    },
    "causal_chain": [
      {
        "step": 1,
        "type": "change",
        "name": "应用发布更新 (OrderService)",
        "desc": "11:20 产生批量更新 SQL",
        "evidence": "E1"
      },
      {
        "step": 2,
        "type": "sql",
        "name": "SQL_ID: 8a7fbc6d 未走主键更新",
        "desc": "持有 order_item 行级排他锁超过 120 秒",
        "evidence": "E2"
      },
      {
        "step": 3,
        "type": "lock",
        "name": "阻塞 38 个后续交易事务",
        "desc": "会话 1845 阻塞了树状下游 38 个会话",
        "evidence": "E3"
      }
    ],
    "recommended_actions": [
      {
        "playbook_code": "KILL_ROOT_BLOCKER",
        "title": "安全终止根源阻塞会话 (SID: 1845)",
        "risk_level": "medium",
        "dryrun_status": "passed",
        "impact_summary": "将释放 38 个被阻塞事务，受影响应用连接将自动重连"
      }
    ]
  }
}
```

---

## 8. 实施计划与安全保障机制

### 8.1 实施演进三部曲
1. **第一阶段（底层采集与指标扩充）**：
   - 升级 6 类数据库 Checkers，扩充 120+ 核心指标；
   - 优化 TimescaleDB 高频采样分流与 ES 向量库构建。
2. **第二阶段（RCA 3.0 与自愈 Playbook 引擎）**：
   - 研发因果拓扑推导与 1-5-15 闭环处置管道；
   - 构建安全 Dry-Run 预演沙箱与回滚链路。
3. **第三阶段（前端 UI/UX 美工重塑与 WarRoom 交付）**：
   - 交付沉浸式排障作战室、实时阻塞图谱与全局 Copilot 深度联动。

### 8.2 生产安全红线保障
1. **只读保护原则**：所有日常采集和推理阶段一律使用最小权限只读账号，杜绝因监控自身造成生产锁表或资源耗尽。
2. **双人复核与自愈权限锁**：任何破坏性自愈操作（Kill Session、参数变更）根据自治等级严格实行权限鉴定与审计追溯。
3. **熔断与降级**：采集模块对目标库执行超时强制截断（默认 5s），防止监控探针夯死影响业务库。

---

*（文档编制完毕，请审阅设计规范与技术架构方案。待您确认后即可开启下一步的代码实施与分模块落地！）*
