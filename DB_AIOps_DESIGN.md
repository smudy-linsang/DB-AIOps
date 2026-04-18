# 数据库自动化智能运维平台 (DB-AIOps) 设计文档

## 版本信息
| 项目 | 内容 |
|------|------|
| **项目名称** | 数据库自动化智能运维平台 (DB-AIOps) |
| **文档版本** | v2.0 |
| **编制日期** | 2026-04-16 |
| **文档状态** | 正式版 |
| **适用范围** | 银行近200套异构数据库（Oracle/MySQL/PostgreSQL/DM8/Gbase8a/TDSQL） |

---

## 目录

- [1. 项目概述](#1-项目概述)
  - [1.1 背景与目标](#11-背景与目标)
  - [1.2 系统定位](#12-系统定位)
  - [1.3 设计原则](#13-设计原则)
- [2. 系统架构](#2-系统架构)
  - [2.1 总体架构](#21-总体架构)
  - [2.2 组件说明](#22-组件说明)
  - [2.3 数据流设计](#23-数据流设计)
- [3. 模块详细设计](#3-模块详细设计)
  - [3.1 数据采集模块](#31-数据采集模块)
  - [3.2 时序存储模块](#32-时序存储模块)
  - [3.3 智能基线模块](#33-智能基线模块)
  - [3.4 容量预测模块](#34-容量预测模块)
  - [3.5 告警管理模块](#35-告警管理模块)
  - [3.6 根因分析模块](#36-根因分析模块)
  - [3.7 健康评分模块](#37-健康评分模块)
  - [3.8 运维工单模块](#38-运维工单模块)
  - [3.9 报告生成模块](#39-报告生成模块)
- [4. 数据模型设计](#4-数据模型设计)
  - [4.1 配置元数据模型](#41-配置元数据模型)
  - [4.2 时序指标模型](#42-时序指标模型)
  - [4.3 分析结果模型](#43-分析结果模型)
  - [4.4 运维操作模型](#44-运维操作模型)
- [5. 接口设计](#5-接口设计)
  - [5.1 内部接口](#51-内部接口)
  - [5.2 外部API](#52-外部api)
- [6. 技术选型](#6-技术选型)
- [7. 部署架构](#7-部署架构)
- [8. 安全设计](#8-安全设计)
- [9. 实施路线图](#9-实施路线图)
  - [9.1 Phase 0: 紧急修复](#91-phase-0-紧急修复)
  - [9.2 Phase 1: 智能基线](#92-phase-1-智能基线)
  - [9.3 Phase 2: 预测与评分](#93-phase-2-预测与评分)
  - [9.4 Phase 3: 决策辅助](#94-phase-3-决策辅助)
- [10. 风险评估与应对](#10-风险评估与应对)
- [附录](#附录)

---

## 1. 项目概述

### 1.1 背景与目标

**业务背景**：随着银行业务发展，数据库规模已接近200套，涵盖Oracle、MySQL、PostgreSQL、达梦(DM8)、Gbase8a、TDSQL六种异构类型。当前运维依赖人工巡检，存在以下痛点：
1. 监控覆盖率不足（人工巡检有效覆盖率<30%）
2. 固定阈值告警误报率高，形成"告警疲劳"
3. 故障发现滞后（平均延迟>30分钟）
4. 容量管理粗放（阈值触发，事后干预）
5. 运维经验依赖个人，难以传承

**建设目标**：
1. **智能监控**：7×24全自动采集，100%覆盖
2. **预测预警**：基于动态基线，提前30天预警容量问题
3. **决策辅助**：将DBA专家经验知识化，提供优化建议
4. **闭环管理**：监控→分析→建议→审批→执行全流程

### 1.2 系统定位

本平台定位为**面向金融行业异构数据库环境的自动化智能运维管理平台**，区别于传统工具：

| 对比维度 | 传统通用监控(Zabbix) | 数据库厂商工具(OEM) | 本平台(DB-AIOps) |
|----------|---------------------|-------------------|------------------|
| 告警方式 | 固定阈值 | 固定阈值 | **动态时间感知基线** |
| 预测能力 | 无 | 部分厂商有 | **多模型容量预测** |
| 分析深度 | 指标展示 | SQL/会话级分析(单库) | **基线偏移+业务影响+根因(多库)** |
| 异构支持 | 通用但浅层 | 单一厂商深度 | **主流国产+国际数据库统一视图** |
| 运维闭环 | 仅监控 | 仅分析 | **监控→分析→建议→审批→执行全闭环** |

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| **分层解耦** | 采集、存储、分析、展示各层职责单一、接口清晰，可独立扩展和替换 |
| **插件化采集** | 数据库类型通过Checker插件扩展，新增数据库类型不改动核心调度代码 |
| **时序优先** | 监控数据本质是时间序列，存储和查询设计以时序为核心，不使用关系型数据库存储原始指标 |
| **渐进式智能** | 从简单统计（均值/方差）到时间感知基线再到预测模型，按数据积累程度分阶段开放高级功能 |
| **安全合规** | 密码加密、操作审计、全链路安全，贯穿所有模块设计 |
| **故障隔离** | 采集任务异常不影响Web服务；单库采集失败不传播到其他库 |

---

## 2. 系统架构

### 2.1 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    L5 展示与交互层                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ 总览大屏  │  │ 单库详情  │  │ 运维工单  │  │ 告警中心/报告 │  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                    L4 智能决策层                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ 容量预测  │  │ 业务影响  │  │ 根因分析  │  │ 优化建议      │  │
│  │ 引擎     │  │ 评估     │  │ 引擎(RCA) │  │ 引擎         │  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                    L3 基线分析层                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ 动态基线  │  │ 三重条件  │  │ 健康评分  │  │ 聚合计算      │  │
│  │ 建模引擎  │  │ 异常检测  │  │ 引擎     │  │ 调度器        │  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                    L2 数据存储层                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ 时序指标库 │  │ 关系元数据 │  │ 告警/审计 │  │ 分析结果      │  │
│  │ TimescaleDB│  │ 库(PgSQL) │  │ 库(PgSQL) │  │ 缓存(Redis)  │  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                    L1 数据采集层                                 │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐      │
│  │Oracle│ │MySQL │ │ PG   │ │ DM8  │ │Gbase │ │TDSQL │      │
│  │Checker│ │Checker│ │Checker│ │Checker│ │Checker│ │Checker│      │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘      │
├─────────────────────────────────────────────────────────────────┤
│                    L0 被监控数据库（生产环境）                    │
│  Oracle  MySQL  PostgreSQL  DM8  Gbase  TDSQL                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 组件说明

| 组件 | 职责 | 技术实现 |
|------|------|----------|
| **采集调度器** | 管理200+数据库的并发采集，自适应频率调整 | Python APScheduler/Celery |
| **Checker插件池** | 各数据库类型的专用采集器 | 继承BaseDBChecker的插件类 |
| **时序存储** | 原始指标数据的高性能写入和查询 | TimescaleDB (PostgreSQL扩展) |
| **元数据存储** | 配置、告警、工单等关系数据 | PostgreSQL 15 |
| **缓存服务** | 基线模型热缓存，加速实时检测 | Redis 7.0 |
| **分析引擎** | 基线建模、容量预测、健康评分等 | Python + NumPy/SciPy/statsmodels |
| **Web服务** | 前端界面和REST API | Django + Gunicorn + Nginx |
| **前端展示** | 可视化界面和交互 | Vue 3 + ECharts 5 |

### 2.3 数据流设计

```
[被监控数据库]
      │
      │ SQL采集（只读）
      ↓
[采集调度器] ←── 自适应频率控制（正常5分钟/异常1分钟/DOWN30秒）
      │
      │ 结构化指标 (JSON)
      ↓
[时序指标库] ──→ [实时异常检测] ──→ [告警推送]
      │            ↑                    (邮件/钉钉)
      │         基线对比
      ↓            │
[批量聚合（每小时/每天）]
      │            │
      ↓            │
[聚合数据] ──→ [容量预测引擎] ──→ [扩复工单]
      │            │
      │            ↓
      ├──→ [健康评分引擎] ──→ [日报推送]
      │            │
      │            ↓
      └──→ [优化建议引擎] ──→ [建议报告]
                                      │
[DBA / 管理员] ←── [Web界面] ←───────┘
      │
      │ 审批操作
      ↓
[审批工单引擎] ──→ [执行引擎] ──→ [审计日志]
```

---

## 3. 模块详细设计

### 3.1 数据采集模块

#### 3.1.1 插件化采集框架

```python
class BaseDBChecker:
    def get_connection(self, config: DatabaseConfig) -> Connection
    def collect_metrics(self, config: DatabaseConfig, conn: Connection) -> dict
    def check(self, config: DatabaseConfig) -> CollectionResult
    def get_min_permissions(self) -> list[str]  # 返回最小权限清单

CHECKER_MAP = {
    'oracle': OracleChecker,    # oracledb thin 模式，无需 Instant Client
    'mysql':  MySQLChecker,     # pymysql，兼容 MySQL 5.7/8.0+
    'pgsql':  PostgreSQLChecker,# psycopg2-binary
    'dm':     DamengChecker,    # pyodbc + DM8 ODBC Driver
    'gbase':  GbaseChecker,     # pymysql（MySQL 协议兼容）
    'tdsql':  TDSQLChecker,     # pymysql
}
```

#### 3.1.2 采集结果标准 Schema

```json
{
  "version":            "string（数据库版本，截取前50字符）",
  "active_connections": "integer（当前活跃连接数）",
  "max_connections":    "integer（最大连接数配置）",
  "conn_usage_pct":     "float（连接使用率 %）",
  "uptime_seconds":     "integer（实例运行秒数）",
  "qps":                "float（每秒查询数，不支持时为 null）",
  "locks": [
    {"blocker_id": "string", "blocker_user": "string",
     "waiter_id": "string",  "waiter_user":  "string", "seconds": "integer"}
  ],
  "tablespaces": [
    {"name": "string", "total_mb": "float", "used_mb": "float", "used_pct": "float"}
  ],
  "_extended": {}
}
```

> **Bug 记录**：PostgreSQL Checker 中 `used_pct` 使用了相对最大值的自比较算法，导致误报。第一期 P0 修复项，正确实现须基于物理磁盘实际可用空间计算。

#### 3.1.3 自适应采集调度

| 状态 | 采集间隔 | 触发/退出条件 |
|------|----------|---------------|
| NORMAL | 5 分钟 | 默认状态 |
| ELEVATED | 1 分钟 | 任一指标偏离基线 2σ；连续10次正常后回退 NORMAL |
| RECOVERY | 30 秒 | 数据库由 UP 变 DOWN；检测到恢复后发通知并回退 NORMAL |
| SCHEDULED_HIGH | 1 分钟 | DBA 手动设置的业务高峰监控时段 |

---

### 3.2 时序存储模块

采用 **TimescaleDB**（PostgreSQL 扩展）作为时序指标存储：

| 数据类型 | 存储方式 | 分区策略 | 保留周期 |
|----------|----------|----------|----------|
| 原始采集指标 | 超表 metric_point | 7 天/chunk，7天后自动压缩 | 12 个月 |
| 1 小时聚合 | 连续聚合超表 | 自动更新 | 36 个月 |
| 日级聚合 | 连续聚合超表 | 自动更新 | 永久 |
| 采集快照（JSON） | 超表 collection_snapshot | 1 天/chunk | 12 个月 |

---

### 3.3 智能基线模块

#### 3.3.1 基线建模流程（每日凌晨 2:00 执行）

```
FOR EACH 活跃数据库 × 可建模指标:
  1. 读取近 28 天（可配置）历史数据，按时间槽分组（7×24=168 槽）
  2. 剔除被标记为异常的数据点（防止历史异常污染基线）
  3. 统计每槽: count / mean / std / p90 / p95 / p99
  4. 数据量 < 7 个点 → 标记"数据不足"，用全时段统计降级替代
  5. 计算 normal_min = mean - k×std, normal_max = mean + k×std（k 按指标配置，默认2.0）
  6. 写入 BaselineModel 表（upsert）
  7. 刷新 Redis 基线缓存（TTL 24 小时）
```

#### 3.3.2 实时三重条件异常检测（每次采集后执行）

| 条件 | 判定规则 | 目的 |
|------|----------|------|
| **量级** | 超出 `[μ-2σ, μ+2σ]` → 警告；超出 `[μ-3σ, μ+3σ]` → 严重 | 偏离程度判定 |
| **方向** | 上升敏感指标（连接数/慢查询/锁等待）只在异常升高时告警；下降敏感（缓存命中率/TPS）只在异常下降时告警 | 避免反向误报 |
| **持续性** | 连续 **3 次**采集均满足量级+方向条件（约 15 分钟） | 过滤瞬时抖动 |

三重条件全满足方触发告警；活跃告警期间同实例同类型不重复推送；指标连续 2 次恢复后自动触发恢复通知。

---

### 3.4 容量预测模块

#### 3.4.1 模型选择逻辑

```
Input: 某表空间/数据库近 N 天的每日末 used_pct 序列

Step 1: 数据充分性检查
  < 14 天 → 降级为简单线性外推
  14-30 天 → 线性回归
  > 30 天 → 进入 Step 2

Step 2: 周期性检测（FFT / 自相关）
  存在显著 7/30 天周期 → 候选加入 Holt-Winters

Step 3: 用近 7 天数据做交叉验证，选 MAPE 最小的模型

Step 4: 预测未来 90 天的 used_pct 序列

Step 5: 提取
  - 预计超过告警线（默认 85%）的日期
  - 预计超过危险线（默认 95%）的日期
  - 月均增长率、95% 置信区间

Step 6: 结果写入 PredictionResult，若 warn_date 距今 < 30 天且无有效工单 → 自动创建扩容建议工单
```

#### 3.4.2 扩容量计算

```
推荐扩容量 = 预计未来 90 天增量 × 安全系数（默认 1.5）
扩容目标 = (current_used + 推荐扩容量) / 0.70   ← 扩容后使用率建议 ≤ 70%
```

---

### 3.5 告警管理模块

#### 3.5.1 告警级别

| 级别 | 触发条件 | 推送渠道 | 响应时限 |
|------|----------|----------|----------|
| **P1 严重** | 实例 DOWN / 连接使用率 > 95% / 容量触达危险线 | 短信 + 钉钉/企微 + 邮件 | 15 分钟 |
| **P2 重要** | 指标偏离基线 3σ / 容量触达告警线 / 锁等待 > 5 分钟 | 钉钉/企微 + 邮件 | 1 小时 |
| **P3 警告** | 指标偏离基线 2σ / 容量使用率 > 80% / 预测 30 天内触达告警线 | 邮件 | 当日 |
| **P4 提示** | 优化建议 / 长期预测性提醒 / 维护建议 | 日报汇总 | 下个维护窗口 |

#### 3.5.2 告警风暴防护

- **同实例同类合并**：活跃期间不重复推送，仅更新状态
- **批量聚合**：5 分钟内 3 个以上数据库触发同类告警 → 合并为一条推送
- **静默窗口**：DBA 可为指定数据库设置维护期静默（仅记录不推送）
- **告警确认**：DBA 标记"已知晓"后，N 小时内不再提示（不代表关闭）

---

### 3.6 根因分析模块

规则驱动的 **事件-原因-建议三元组知识库**，支持规则热加载：

| 规则 | 触发现象 | 推断原因 | 处置建议 |
|------|----------|----------|----------|
| R001 | 连接数持续增长，超基线 3σ | 连接泄漏（应用未正确释放连接） | 检查连接池配置；定位未关闭连接的来源 IP |
| R002 | 慢查询突增 + QPS 同步下降 | 执行计划突变（统计信息过期） | 立即收集统计信息；检查近期大批量数据变更 |
| R003 | 锁等待上升，涉及特定表 DML | 热点表行锁竞争（大事务未提交） | 定位阻塞源 SQL；检查大事务；拆分批量操作 |
| R004 | 表空间 < 1 小时内使用率急增 ≥ 5% | 数据膨胀（误操作/业务 Bug/临时表爆增） | 查大对象增长；检查近 1 小时大 DML 操作 |
| R005 | QPS/TPS 骤降，无对应锁等待 | I/O 子系统性能瓶颈 | 检查存储层 I/O 延迟；排查全表扫描大查询 |
| R006 | Buffer Pool 命中率持续 < 90% | 内存不足或大表全扫描污染 | 检查全表扫描 SQL；评估增大缓冲池参数 |
| R007 | Redo/Binlog 生成速率突增 > 基线 5 倍 | 计划外大批量 DML | 确认是否有计划内批量作业；定位来源会话 |
| R008 | 数据库由 UP 变 DOWN | 宕机/重启/OOM Kill | 检查 OS 日志和数据库 Alert Log |
| R009 | 连接率 > 80%，来源集中于少数 IP | 特定应用连接池配置过大或泄漏 | 按来源 IP 分析连接分布；限制问题 IP 最大连接数 |
| R010 | 等待事件 Top 1 为 "log file sync"（Oracle） | Redo Log 写入瓶颈 | 检查 Redo Log 所在磁盘 I/O；考虑迁移到更快存储 |

**复合因果链**：多条规则同时触发时系统尝试推断关联关系（如 R001 + R003 → "连接堆积引发锁竞争"复合故障，优先级提升为 P1）。

---

### 3.7 健康评分模块

每日对每个数据库生成综合健康评分（满分 100 分）：

| 维度 | 权重 | 评分依据 |
|------|------|----------|
| 可用性（Availability） | 25% | 近 30 天停机次数、累计停机时长、最近一次恢复时长 |
| 容量（Capacity） | 25% | 最紧张资源的剩余量及预测到期时间 |
| 性能（Performance） | 25% | 近 7 天性能指标相对基线的偏离程度和频次 |
| 配置（Configuration） | 15% | 关键参数合理性检查得分 |
| 运维规范（Operations） | 10% | 未处理告警数、待审批工单数、过期统计信息数 |

评分变化趋势（日环比、周环比）同步展示，支持识别评分下降趋势。

---

### 3.8 运维工单模块

#### 3.8.1 风险分级与审批要求

| 风险等级 | 操作类型示例 | 审批要求 | 执行方式 |
|----------|-------------|----------|----------|
| 低风险 | 统计信息更新、会话查询、告警确认 | 无需审批 | DBA 直接执行或平台自动执行 |
| 中风险 | Kill Session、临时参数修改、表空间扩容 | DBA 主管审批（1 人） | 审批通过后平台自动执行 |
| 高风险 | 数据文件操作、索引 DROP、关键参数修改 | DBA 主管 + 业务负责人（2 人） | 审批后 DBA 手动执行，结果回填 |
| 极高风险 | 生产数据 DELETE/TRUNCATE/DROP TABLE | 双 DBA + 部门负责人（3 人）+ 备份截图 | 平台仅生成 SQL 草稿，须双人旁站手动操作 |

#### 3.8.2 工单必要字段

每条工单须包含：触发来源、操作描述（业务语言）、完整执行 SQL（允许审批人修改）、风险说明（自动生成）、回滚方案、执行前检查项清单、预计影响范围和影响时长。

---

### 3.9 报告生成模块

| 报告类型 | 生成频率 | 主要受众 | 核心内容 |
|----------|----------|----------|----------|
| 日报 | 每日 8:00 | DBA 团队 | 前日告警汇总、今日待处理事项、需关注指标 |
| 周报 | 每周一 9:00 | DBA 主管 | 告警统计、健康评分变化、新增优化建议、待处理工单 |
| 月报 | 每月 1 日 | DBA 主管 + 部门负责人 | 月度故障统计、容量规划进度、优化建议采纳情况 |
| 年度规划报告 | 每年 11 月 | IT 管理层 | 年度资源消耗分析、来年存储/内存需求预测、规划建议 |

---

## 4. 数据模型设计

### 4.1 配置元数据模型

```sql
-- 数据库配置
CREATE TABLE database_config (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,        -- 连接别名（如"核心交易库_主节点"）
    db_type         VARCHAR(20)  NOT NULL,        -- oracle/mysql/pgsql/dm/gbase/tdsql
    host            VARCHAR(100) NOT NULL,
    port            INTEGER      NOT NULL,
    username        VARCHAR(100) NOT NULL,
    password_enc    TEXT         NOT NULL,        -- AES-256-GCM 加密存储
    service_name    VARCHAR(100),                 -- Oracle SID/服务名；PG 数据库名
    environment     VARCHAR(20)  DEFAULT 'prod',  -- prod/uat/dev/dr
    is_active       BOOLEAN      DEFAULT TRUE,
    collect_interval_sec INTEGER DEFAULT 300,     -- 自适应采集间隔（自动更新）
    created_at      TIMESTAMPTZ  DEFAULT now(),
    updated_at      TIMESTAMPTZ  DEFAULT now()
);

-- 业务系统
CREATE TABLE business_system (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    importance  VARCHAR(20)  NOT NULL,  -- critical/important/normal
    owner       VARCHAR(100),
    contact     VARCHAR(200)
);

-- 数据库 ↔ 业务系统（多对多）
CREATE TABLE db_business_mapping (
    db_config_id  INTEGER REFERENCES database_config(id),
    biz_system_id INTEGER REFERENCES business_system(id),
    PRIMARY KEY (db_config_id, biz_system_id)
);

-- 指标元数据
CREATE TABLE metric_definition (
    metric_key      VARCHAR(100) PRIMARY KEY,
    display_name    VARCHAR(100) NOT NULL,
    unit            VARCHAR(20),                  -- count/pct/mb/qps/sec
    db_types        TEXT[],
    alert_direction VARCHAR(10)  DEFAULT 'up',    -- up/down/both
    sigma_k         FLOAT        DEFAULT 2.0,
    fixed_warn_val  FLOAT,                        -- 基线未就绪时的固定阈值兜底
    is_capacity     BOOLEAN      DEFAULT FALSE    -- 是否参与容量预测
);
```

---

### 4.2 时序指标模型

```sql
-- 原始指标点（TimescaleDB 超表）
CREATE TABLE metric_point (
    time         TIMESTAMPTZ      NOT NULL,
    db_config_id INTEGER          NOT NULL,
    metric_key   VARCHAR(100)     NOT NULL,
    value        DOUBLE PRECISION,
    status       VARCHAR(20)      DEFAULT 'normal'  -- normal/anomaly/unavailable
);
SELECT create_hypertable('metric_point', 'time', chunk_time_interval => INTERVAL '7 days');
CREATE INDEX ON metric_point (db_config_id, metric_key, time DESC);

-- 完整采集快照（原始 JSON，用于回溯）
CREATE TABLE collection_snapshot (
    time          TIMESTAMPTZ NOT NULL,
    db_config_id  INTEGER     NOT NULL,
    status        VARCHAR(10) NOT NULL,  -- UP/DOWN
    raw_data      JSONB,
    collection_ms INTEGER
);
SELECT create_hypertable('collection_snapshot', 'time', chunk_time_interval => INTERVAL '1 day');
```

---

### 4.3 分析结果模型

```sql
-- 动态基线模型
CREATE TABLE baseline_model (
    id              SERIAL PRIMARY KEY,
    db_config_id    INTEGER      NOT NULL,
    metric_key      VARCHAR(100) NOT NULL,
    time_slot       SMALLINT     NOT NULL,         -- 0-167（周几×24 + 小时）
    sample_count    INTEGER      NOT NULL,
    mean            FLOAT        NOT NULL,
    std             FLOAT        NOT NULL,
    p90             FLOAT,  p95 FLOAT,  p99 FLOAT,
    normal_min      FLOAT,
    normal_max      FLOAT,
    data_sufficient BOOLEAN      DEFAULT TRUE,     -- FALSE 表示数据不足，降级处理
    updated_at      TIMESTAMPTZ  DEFAULT now(),
    UNIQUE (db_config_id, metric_key, time_slot)
);

-- 容量预测结果
CREATE TABLE prediction_result (
    id                  SERIAL PRIMARY KEY,
    db_config_id        INTEGER      NOT NULL,
    metric_key          VARCHAR(100) NOT NULL,
    resource_name       VARCHAR(100),
    current_value       FLOAT,
    monthly_growth_rate FLOAT,
    predicted_warn_date DATE,                      -- 预计触达告警线日期
    predicted_crit_date DATE,                      -- 预计触达危险线日期
    model_used          VARCHAR(50),
    confidence          FLOAT,
    recommendation      TEXT,
    generated_at        TIMESTAMPTZ  DEFAULT now(),
    UNIQUE (db_config_id, metric_key, generated_at::DATE)
);

-- 综合健康评分
CREATE TABLE health_score (
    id                 SERIAL PRIMARY KEY,
    db_config_id       INTEGER NOT NULL,
    score_date         DATE    NOT NULL,
    total_score        FLOAT   NOT NULL,           -- 0-100
    availability_score FLOAT,
    capacity_score     FLOAT,
    performance_score  FLOAT,
    config_score       FLOAT,
    ops_score          FLOAT,
    score_detail       JSONB,                      -- 各项扣分原因
    UNIQUE (db_config_id, score_date)
);
```

---

### 4.4 运维操作模型

```sql
-- 告警记录（不可删除）
CREATE TABLE alert (
    id              SERIAL PRIMARY KEY,
    db_config_id    INTEGER      NOT NULL,
    alert_type      VARCHAR(50)  NOT NULL,         -- baseline/capacity/down/lock/connection
    severity        VARCHAR(10)  NOT NULL,         -- P1/P2/P3/P4
    metric_key      VARCHAR(100),
    current_value   FLOAT,
    baseline_value  FLOAT,
    title           VARCHAR(200) NOT NULL,
    description     TEXT,
    rca_rules       JSONB,                         -- 匹配的 RCA 规则列表
    biz_impact      JSONB,                         -- 业务影响评估
    status          VARCHAR(20)  DEFAULT 'active', -- active/acknowledged/resolved
    acknowledged_by VARCHAR(100),
    acknowledged_at TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  DEFAULT now()
);

-- 运维工单（审批流，不可删除）
CREATE TABLE audit_log (
    id             SERIAL PRIMARY KEY,
    db_config_id   INTEGER      NOT NULL,
    source         VARCHAR(50),                    -- manual/ai_suggestion/alert
    action_type    VARCHAR(50)  NOT NULL,
    risk_level     VARCHAR(20)  NOT NULL,          -- low/medium/high/critical
    description    TEXT         NOT NULL,
    sql_command    TEXT         NOT NULL,
    rollback_cmd   TEXT,
    pre_check_list JSONB,
    status         VARCHAR(20)  DEFAULT 'pending', -- pending/approved/rejected/executing/success/failed
    created_by     VARCHAR(100),
    approver_1     VARCHAR(100),  approve_1_at TIMESTAMPTZ,
    approver_2     VARCHAR(100),  approve_2_at TIMESTAMPTZ,
    executor       VARCHAR(100),
    execute_at     TIMESTAMPTZ,
    result         TEXT,
    created_at     TIMESTAMPTZ  DEFAULT now()
);
```

---

## 5. 接口设计

### 5.1 内部接口

各引擎模块通过 Django ORM 和 Redis 客户端直接交互，不封装 HTTP 接口。关键内部接口约定：

| 接口 | 调用方 | 被调用方 | 说明 |
|------|--------|---------|------|
| `BaseDBChecker.check()` | 采集调度器 | Checker 插件 | 返回 CollectionResult 标准结构 |
| `BaselineEngine.get_baseline(db_id, metric, slot)` | 异常检测 | Redis 缓存 / DB | 优先从 Redis 读，缺失时回源 DB |
| `PredictionEngine.run(db_id, metric_key)` | 每日批量调度 | statsmodels 分析 | 写入 prediction_result 表 |
| `AlertManager.fire(alert_data)` | 异常检测 | 告警推送服务 | 检查去重后路由到各推送渠道 |

### 5.2 外部 API

所有 API 须携带认证 Token，支持基于角色的数据范围过滤：

| 接口路径 | 方法 | 说明 |
|----------|------|------|
| `/api/v1/databases/` | GET | 获取所有数据库配置列表（不含密码） |
| `/api/v1/databases/{id}/status/` | GET | 获取指定数据库当前状态（最新采集结果） |
| `/api/v1/databases/{id}/metrics/` | GET | 查询历史指标，支持时间范围和指标名过滤 |
| `/api/v1/databases/{id}/baseline/` | GET | 获取基线模型数据 |
| `/api/v1/databases/{id}/prediction/` | GET | 获取最新容量预测结果 |
| `/api/v1/databases/{id}/health/` | GET | 获取健康评分历史 |
| `/api/v1/alerts/` | GET | 查询告警列表，支持状态/级别/时间过滤 |
| `/api/v1/alerts/{id}/acknowledge/` | POST | 确认告警 |
| `/api/v1/auditlogs/` | GET | 查询运维工单列表 |
| `/api/v1/auditlogs/{id}/approve/` | POST | 审批通过 |
| `/api/v1/auditlogs/{id}/reject/` | POST | 拒绝 |
| `/api/v1/health/` | GET | 平台自身健康检查（供外部监控探活） |

---

## 6. 技术选型

| 类别 | 组件 | 版本要求 | 选型理由 |
|------|------|----------|----------|
| Web 框架 | Django | ≥ 5.0 | 现有技术基础；ORM 成熟；Admin 可快速搭建配置管理 |
| 任务调度（单机） | APScheduler | ≥ 3.10 | 当前已使用；适合 200 套以内规模 |
| 任务调度（集群） | Celery + Redis | ≥ 5.3 | 规模超过单机时的水平扩展方案（预留） |
| 时序数据库 | TimescaleDB | ≥ 2.x | 复用 PostgreSQL 生态；时序压缩能力强 |
| 关系数据库 | PostgreSQL | ≥ 15 | 系统库（元数据/告警/工单）；与 TimescaleDB 同实例 |
| 缓存 | Redis | ≥ 7.0 | 基线模型热缓存；Celery 任务队列 |
| 前端图表 | ECharts | ≥ 5.4 | 现有技术基础；时序折线图能力强 |
| 前端框架 | Vue 3（Composition API）| ≥ 3.3 | 渐进式引入；改善复杂交互页面 |
| 统计/预测 | numpy + scipy + statsmodels | 最新稳定版 | 满足线性回归/ARIMA/Holt-Winters |
| 密码加密 | cryptography | ≥ 42.0 | AES-256-GCM 支持；标准实现 |
| 报表导出 | openpyxl + ReportLab | 最新稳定版 | Excel + PDF 报表 |
| 生产部署 | Gunicorn + Nginx | Gunicorn≥21 / Nginx≥1.24 | 标准 Python 生产部署方案 |
| 容器化（可选） | Docker + Compose | Docker≥25 | 降低部署复杂度；已有 docker-compose.yml 基础 |

**数据库驱动**：

| 数据库 | 驱动 | 说明 |
|--------|------|------|
| Oracle | oracledb（thin 模式） | 无需 Oracle Instant Client |
| MySQL / TDSQL / Gbase | pymysql | 兼容 MySQL 5.7/8.0+，纯 Python |
| PostgreSQL | psycopg2-binary | 成熟稳定，binary 包免编译 |
| 达梦 DM8 | pyodbc + DM8 ODBC Driver | DM8 官方推荐连接方式 |

---

## 7. 部署架构

### 7.1 当前（开发）部署

```
[单台 Windows/Linux 服务器]
  ├── python manage.py runserver      ← Web 服务（开发用）
  ├── python manage.py start_monitor  ← 采集进程
  └── SQLite                          ← 系统库（仅开发）
```

### 7.2 目标（生产）部署

```
                        Nginx (443/HTTPS)
                             │
               ┌─────────────┴─────────────┐
               │                           │
      ┌─────────▼──────────┐   ┌───────────▼────────────┐
      │    Web 服务器       │   │      采集服务器          │
      │  Django + Gunicorn  │   │  APScheduler / Celery  │
      │  （无状态，可多实例）│   │  Checker 插件池（200套）│
      └─────────┬──────────┘   └───────────┬────────────┘
               └─────────────┬─────────────┘
                             │
               ┌─────────────▼──────────────────┐
               │           数据层                │
               │  PostgreSQL 15 + TimescaleDB    │
               │  （主库 + 流复制从库）           │
               │  Redis 7.0（缓存 + 任务队列）   │
               └────────────────────────────────┘

网络要求：
  采集服务器 → 各生产数据库：1521/3306/5432/5236 等端口（只读账号）
  Web/采集服务器 → 数据层：内网直连
  外部用户 → Nginx：443（HTTPS）
```

### 7.3 环境规划

| 环境 | 用途 | 规格参考 |
|------|------|----------|
| 开发环境 | 功能开发与单元测试 | 开发者本机，SQLite |
| 测试环境 | 集成测试、功能验收 | 4C/8G，PostgreSQL，接入测试数据库 |
| 生产环境 | 监控全量 200 套数据库 | Web: 8C/16G×2；采集: 8C/32G×1；DB: 16C/32G 主从 |

---

## 8. 安全设计

### 8.1 认证与授权（RBAC）

| 角色 | 权限范围 | 典型用户 |
|------|----------|----------|
| 只读观察员 | 查看监控数据、告警、报告；不可执行操作 | 业务负责人、审计人员 |
| DBA 操作员 | 只读 + 创建工单 + 执行低风险操作 + 确认告警 | 普通 DBA |
| DBA 主管 | DBA 操作员 + 审批中/高风险工单 + 管理数据库配置 | DBA 组长 |
| 系统管理员 | 全部权限 + 用户管理 + 系统配置 | 平台管理员 |

支持按数据库分组的数据范围隔离（如按业务域或按环境），DBA 只能看到被授权的数据库组。

### 8.2 密码安全

- 所有数据库连接密码采用 **AES-256-GCM** 加密存储，密钥通过环境变量或密钥管理服务注入，不存储在代码库中
- 禁止在日志、API 响应、页面中输出任何明文密码
- 支持密码定期轮换，更新后平台自动使用新密码重建采集连接

### 8.3 审计要求

- 所有对数据库配置的修改（增删改密码）记录审计日志
- 所有运维工单（含拒绝、修改、执行）完整记录操作人、时间、内容、结果
- 告警的确认、静默操作记录操作人和原因
- 审计日志**不可删除、不可修改**，只允许追加写入；敏感信息自动脱敏

---

## 9. 实施路线图

### 9.1 Phase 0：紧急修复（即时，1-2 周）

| 优先级 | 问题 | 说明 |
|--------|------|------|
| **P0** | PostgreSQL Checker 的 `used_pct` 计算 Bug | 当前实现导致误报，必须修复 |
| **P0** | 采集任务超时隔离（单库超时不影响其他库） | 关键可靠性保障 |
| **P0** | SQLite → PostgreSQL 迁移（生产前提） | 生产部署先决条件 |
| **P0** | 密码 AES-256 加密存储 | 合规硬性要求 |
| **P0** | 生产安全配置（ALLOWED_HOSTS/CSRF/HTTPS） | 上线前必须 |
| **P1** | 审批执行逻辑统一到 AuditLog 引擎 | 消除 auto_remediation_engine.py 与 views_enhanced.py 的职责重叠 |

### 9.2 Phase 1：智能基线（2026 Q2，3-4 个月）

目标：完成全行 200 套数据库接入，7×24 稳定采集，告警推送通路打通，生产部署完成。

**核心功能**：
- 完善 Gbase/TDSQL Checker（补齐全量指标）
- 集成 TimescaleDB，原始指标写入时序超表
- RBAC 基础角色权限（只读/DBA/主管/管理员）
- 钉钉/企微 Webhook 告警推送
- 告警去重与静默窗口
- 数据库配置 CRUD 管理页面（支持 200 套接入）

**验收标准**：
- 200 套数据库完成接入，采集成功率 ≥ 99%
- DB DOWN → DBA 收到告警 ≤ 5 分钟
- 所有密码密文存储，通过安全基线检查

### 9.3 Phase 2：预测与评分（2026 Q3-Q4，3-4 个月）

目标：动态时间感知基线取代固定阈值，容量问题提前 30 天预知，主动生成处置工单。

**核心功能**：
- 时间感知基线建模（7×24=168 时间槽，滑动窗口 28 天）
- 三重条件告警（量级+方向+持续性）
- 趋势图叠加基线正常范围带（阴影区域可视化）
- 多模型容量预测（线性回归/Holt-Winters/ARIMA 自动选择）
- 自动生成预警工单（提前 30 天，进入审批流）
- 容量规划视图（按紧急程度排序的全库到期时间总览）
- 多维度综合健康评分（5 个维度，每日更新）
- 周报自动生成（Excel 格式）

**验收标准**：
- 告警误报率 < 15%（对比 Phase 1 基准）
- 容量预警提前量 ≥ 30 天
- 健康评分覆盖全部 200 套数据库，每日更新

### 9.4 Phase 3：决策辅助（2027 Q1-Q2，4-6 个月）

目标：将 DBA 专家经验知识化，主动发现优化机会，支撑管理层 IT 资源规划决策。

**核心功能**：
- 慢查询日志采集（MySQL slow log / PG pg_stat_statements / Oracle AWR）
- 索引建议（基于慢查询频次和执行计划）
- 关键参数合理性自动检查（20+ 条规则）
- 各库资源使用特征画像（负载类型识别、高峰时段识别）
- 年度存储/连接资源需求预测汇总报告
- 月报/年报 PDF 自动生成（含管理摘要）
- 完善 RBAC（按数据库组分配数据权限）
- 开放 REST API，支持与行内 ITSM/CMDB 集成

**验收标准**：
- 每周有效优化建议 ≥ 5 条（DBA 评审确认）
- DBA 主动发现问题比例 > 60%（而非业务反馈后被动发现）
- 管理层月报自动化率 100%

---

## 10. 风险评估与应对

| 风险 | 可能性 | 影响 | 应对措施 |
|------|--------|------|----------|
| 基线冷启动期误报率高（数据积累不足）| 高（Phase 1 初期必然）| 中 | 积累 ≥ 4 周后再开放基线告警；冷启动期使用固定阈值兜底，明确标注"基线建立中" |
| 部分数据库系统视图权限无法获取 | 中 | 中 | 提前完成最小权限清单梳理，与业务方协商授权；无法授权的降级处理，不影响连通性监控 |
| 采集 SQL 对生产数据库产生性能影响 | 中 | 高 | 采集 SQL 在测试环境做性能评估（执行计划 + 耗时测试）；采集时间对齐，避免同一秒内并发大量查询 |
| 告警推送渠道不稳定导致漏报 | 低 | 高 | 至少配置两路独立渠道；推送失败有重试机制；P1 告警在告警中心界面始终显示 |
| 200 套并发采集导致平台性能问题 | 中 | 中 | 线程池并发（默认 20 线程）；单轮超时告警不阻塞下一轮；性能不足时水平扩展采集服务器 |
| 数据库密码等敏感信息泄露 | 低 | 极高 | AES-256 加密；密钥通过环境变量注入；服务器严格访问控制；定期安全审计 |
| 预测模型准确性不足，扩容建议时机偏差 | 中 | 低 | 预测结果附带置信度说明；DBA 审批时可调整建议量；持续跟踪预测偏差，定期优化模型 |
| 多数据库版本差异导致采集 SQL 兼容性问题 | 高 | 低 | 各 Checker 按主要版本区间做兼容适配；采集异常时捕获后记录错误并标记 DOWN，不向上传播 |
| 网络防火墙限制采集服务器连接数据库 | 中 | 高 | 平台建设立项时同步提交网络策略申请；提前与网络团队确认端口连通性 |
| 平台数据库（PG/TimescaleDB）自身故障 | 低 | 高 | 配置 PostgreSQL 主从流复制；关键配置数据定期备份 |

---

## 附录

### 附录 A：各数据库监控账号最小权限清单

**Oracle**（账号 `db_monitor`）：
```sql
GRANT CREATE SESSION TO db_monitor;
GRANT SELECT ON V_$VERSION      TO db_monitor;
GRANT SELECT ON GV_$SESSION     TO db_monitor;
GRANT SELECT ON V_$INSTANCE     TO db_monitor;
GRANT SELECT ON GV_$INSTANCE    TO db_monitor;
GRANT SELECT ON DBA_DATA_FILES  TO db_monitor;
GRANT SELECT ON DBA_FREE_SPACE  TO db_monitor;
GRANT SELECT ON GV_$LOCK        TO db_monitor;
GRANT SELECT ON V_$PARAMETER    TO db_monitor;
GRANT SELECT ON V_$SYSSTAT      TO db_monitor;
-- Phase 3 补充：
GRANT SELECT ON GV_$SQL              TO db_monitor;  -- 慢查询分析
GRANT SELECT ON DBA_TAB_STATISTICS   TO db_monitor;  -- 统计信息检查
```

**MySQL**（账号 `db_monitor@'采集服务器IP'`）：
```sql
GRANT PROCESS ON *.*                    TO 'db_monitor'@'采集服务器IP';
GRANT REPLICATION CLIENT ON *.*         TO 'db_monitor'@'采集服务器IP';
GRANT SELECT ON performance_schema.*   TO 'db_monitor'@'采集服务器IP';
GRANT SELECT ON information_schema.*   TO 'db_monitor'@'采集服务器IP';
-- Phase 3 补充：
GRANT SELECT ON mysql.slow_log          TO 'db_monitor'@'采集服务器IP';
```

**PostgreSQL**（账号 `db_monitor`）：
```sql
CREATE USER db_monitor WITH PASSWORD 'encrypted_password';
GRANT pg_monitor TO db_monitor;   -- PG 10+ 内置监控角色，覆盖 pg_stat_*、pg_locks 等
GRANT CONNECT ON DATABASE postgres TO db_monitor;
```

**达梦 DM8**（账号 `db_monitor`）：
```sql
CREATE USER db_monitor IDENTIFIED BY 'encrypted_password';
GRANT SELECT ON V$VERSION    TO db_monitor;
GRANT SELECT ON V$SESSIONS   TO db_monitor;
GRANT SELECT ON V$INSTANCE   TO db_monitor;
GRANT SELECT ON V$TABLESPACE TO db_monitor;
GRANT SELECT ON V$LOCK       TO db_monitor;
GRANT SELECT ON V$PARAMETER  TO db_monitor;
```

---

### 附录 B：容量类指标列表（参与容量预测）

| 指标键 | 含义 | 数据库类型 |
|--------|------|------------|
| `tablespace.{name}.used_pct` | 表空间使用率（%） | Oracle, DM8 |
| `tablespace.{name}.used_mb` | 表空间已用量（MB） | Oracle, DM8 |
| `database.{name}.size_mb` | 数据库大小（MB） | MySQL, PG, Gbase |
| `conn_usage_pct` | 连接数使用率（%） | 全部 |
| `archive_log_rate_mb_per_hour` | 归档日志生成速率 | Oracle, PG |
| `binlog_rate_mb_per_hour` | Binlog 生成速率 | MySQL |

---

### 附录 C：名词说明

| 名词 | 解释 |
|------|------|
| 时间槽（Time Slot） | 将一周 7×24 小时划分为 168 个独立时间窗口单元，每个单元建立独立基线模型 |
| 动态基线 | 基于历史同一时间槽数据计算的统计正常范围，随时间滚动更新，区别于人工设定的固定阈值 |
| 三重条件 | 量级（偏离程度）+ 方向（上升/下降）+ 持续性（连续 N 次），三个条件同时满足才触发告警 |
| 告警疲劳 | 告警频繁误报导致运维人员逐渐对告警失去关注，真实问题被忽视的现象 |
| RCA | Root Cause Analysis，根因分析，基于规则库推断告警现象最可能的根本原因 |
| Checker（采集插件） | 针对特定数据库类型的采集模块，继承 BaseDBChecker，只需实现连接和指标采集逻辑 |
| TimescaleDB | 基于 PostgreSQL 的时序数据库扩展，提供超表自动分区和时序数据压缩能力 |
| AIOps | Artificial Intelligence for IT Operations，将 AI/ML 技术应用于 IT 运维场景 |

---

*本文档为 DB_Monitor v0.0.1 的完整设计文档，随开发进展持续迭代更新。*

---

## 11. 增强设计（落地版）

本章节在原设计基础上，结合当前项目代码现状（Django 单体 + `start_monitor` 采集命令 + SQLite/可切 PostgreSQL）给出可执行增强方案。目标是：在不引入过度复杂度的前提下，把系统推到当前团队可达的最佳表现。

### 11.1 最佳表现目标（12 个月目标）

#### 11.1.1 规模与性能目标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 接入规模 | 300 套数据库 | 保守高于当前 200 套目标，留出扩容冗余 |
| 全量采集时长（P95） | ≤ 180 秒 | 5 分钟周期内完成全量采集，预留分析与入库时间 |
| 单实例采集超时 | 10-15 秒（可配置） | 超时即失败隔离，不拖慢全局 |
| 采集成功率 | ≥ 99.5%（剔除目标库不可用） | 以日为统计窗口 |
| 告警送达时延（P1） | ≤ 60 秒（P95） | 渠道包含钉钉/邮件，至少双通道 |
| 告警送达时延（P2/P3） | ≤ 180 秒（P95） | 非致命告警可适当容忍 |
| Dashboard 查询延迟（P95） | ≤ 2 秒 | 面向近 24 小时数据 |
| API 可用性 | ≥ 99.9% | 月级 SLA |

#### 11.1.2 智能能力目标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 基线告警误报率 | ≤ 12% | 比现有规划再压一档 |
| 基线告警漏报率 | ≤ 8% | 以人工复盘事件为基准 |
| 容量预测 30 天 MAPE | ≤ 15% | 容量类核心指标 |
| 高优先级告警自动 RCA 覆盖率 | ≥ 80% | 能产出候选根因 + 建议动作 |
| 工单闭环率（P1/P2） | ≥ 95% | 告警触发后 24h 内进入处理流程 |

### 11.2 架构收敛原则（避免过度设计）

为确保落地，本系统采用“三层收敛”：

1. **单体优先**：保持 Django 单体，拆分为 `web` 与 `collector` 两类进程，不立即拆微服务。
2. **时序与元数据分治**：元数据、告警、审批放 PostgreSQL；高频指标进入 TimescaleDB（可与 PostgreSQL 同实例同集群）。
3. **异步化边界明确**：采集、告警推送、基线重算、预测任务进入统一任务队列（Celery），Web 只负责展示与控制。

### 11.3 目标技术架构（增强版）

#### 11.3.1 进程拓扑

- `web-api`：Django + Gunicorn，负责 UI、API、配置管理、审批流。
- `collector-worker`：执行数据库采集任务（按数据库分片并发）。
- `analyzer-worker`：执行基线重算、预测、健康评分、RCA 批处理。
- `scheduler`：统一调度（Celery Beat），替代长期 BlockingScheduler 驻留。
- `notify-worker`：告警路由、去重、重试、降级。

#### 11.3.2 并发与分片策略

- 数据库实例按 `db_type + hash(config_id)` 分片，支持水平扩展多个 `collector-worker`。
- 单 worker 并发建议 20-40（按 CPU/网络/目标库承载压测调优）。
- 每轮采集设“全局最晚完成时间”，超时任务进入失败队列并补偿，不阻塞下一轮。

### 11.4 核心链路设计（强一致视角）

#### 11.4.1 采集到告警主链路

1. 读取活跃配置快照（读已提交，避免运行中配置漂移）。
2. 并发执行 checker，输出标准化指标对象。
3. 原始采集结果落盘（`collection_snapshot`）。
4. 指标扁平化写入 `metric_point`。
5. 实时规则 + 基线判定，生成 `alert` 事件。
6. 告警去重与状态机处理（active/ack/resolved）。
7. 推送任务异步投递，失败重试与多渠道兜底。

#### 11.4.2 关键一致性要求

- 同一 `(db_id, alert_type, metric_key)` 同时只允许一个 active 告警。
- 采集失败也必须写入失败快照，确保可审计和可回放。
- 告警推送与告警状态更新使用“先持久化再投递”模式，避免漏告。

### 11.5 数据模型增强（在现有基础上补齐）

建议新增/增强以下字段（逻辑层兼容现有 `MonitorLog`、`AlertLog`、`AuditLog`）：

| 表 | 字段 | 用途 |
|----|------|------|
| `database_config` | `owner_team`, `biz_domain`, `importance` | 业务影响评估与告警路由 |
| `metric_point` | `collect_task_id`, `source_status` | 任务级追踪与故障定位 |
| `collection_snapshot` | `error_code`, `error_class`, `retry_count` | 失败分析与重试策略 |
| `alert` | `fingerprint`, `first_seen_at`, `last_seen_at`, `notify_fail_count` | 去重、抑制、可靠推送 |
| `audit_log` | `ticket_no`, `change_window`, `approval_path` | 与变更流程系统对接 |

### 11.6 告警治理策略（可执行）

#### 11.6.1 告警优先级矩阵

- **P1**：实例 DOWN、连接耗尽、关键库锁等待持续超阈值。
- **P2**：性能基线严重偏离、容量紧急、持续慢查询异常。
- **P3**：轻度偏离、趋势风险。
- **P4**：建议与提醒。

#### 11.6.2 降噪机制

- 同类告警冷却时间（默认 15 分钟）。
- 维护窗口静默（可按实例、按告警类型配置）。
- 抑制规则：若上游故障已确认（例如网络抖动），下游衍生告警自动降级。

### 11.7 安全与合规增强项

- 默认禁用 `DEBUG`，引入环境分层配置（dev/test/prod）。
- 邮件、Webhook、密钥统一走环境变量与密钥管理，不落仓库。
- 审批与执行 API 全量留痕，包含请求人、审批人、执行证据和回滚证据。
- 对外 API 引入 Token + 角色 + 数据域三层鉴权。

### 11.8 可观测性与运维设计（平台自监控）

平台必须监控自己：

- 任务队列积压（长度、等待时长）。
- 采集成功率、超时率、重试率。
- 告警发送成功率、重试次数、通道可用性。
- 基线任务耗时、预测任务耗时。
- Web API P95/P99 延迟与错误率。

建议统一暴露 `/metrics`（Prometheus 格式）并提供平台健康大盘。

### 11.9 三阶段实施（增强版）

#### 阶段 A（0-4 周）：稳态化

- 修复 `start_monitor` 当前可读性/健壮性问题，提炼 checker 抽象与错误码。
- 补齐 `.gitignore`，清理运行产物入库问题（`__pycache__`、`staticfiles`、`db.sqlite3`）。
- 完成配置安全整改（密钥、邮件凭据、DEBUG）。
- 建立最小自动化测试集（采集流程、告警去重、密码加解密）。

**验收**：连续 7 天采集稳定，P1 告警可达，核心链路可回放。

#### 阶段 B（5-10 周）：架构升级

- 引入 Celery + Redis，调度与执行解耦。
- 迁移系统库到 PostgreSQL，接入 TimescaleDB 承载时序数据。
- 打通告警多通道路由与失败重试。
- 完成基线任务离线重算与在线判定拆分。

**验收**：200 套库 5 分钟全量采集达标，告警延迟与误报率达到阶段目标。

#### 阶段 C（11-16 周）：智能增强

- 上线容量预测、健康评分、RCA 规则引擎增强版。
- 上线审批流闭环和变更系统对接。
- 上线周报/月报自动生成和管理视图。

**验收**：预测准确度与闭环率达到目标，平台形成可持续运营机制。

### 11.10 最终验收标准（上线门槛）

满足以下全部条件才可定义为“达到最佳表现版本”：

1. 采集规模 ≥ 300 套，日采集成功率 ≥ 99.5%。
2. P1 告警送达 P95 ≤ 60 秒，且双通道可用。
3. 动态基线误报率 ≤ 12%，漏报率 ≤ 8%。
4. 容量预测 30 天 MAPE ≤ 15%。
5. 审批执行链路全量审计可追溯，不可篡改。
6. 平台自身可用性 ≥ 99.9%，关键指标可观测。

---

## 12. 当前版本与目标版本差距（Gap Checklist）

用于每周例会追踪的最小清单：

- [ ] 采集调度从单进程 APScheduler 升级为队列化调度。
- [ ] 元数据与时序数据存储分治完成。
- [ ] 告警分级、抑制、静默、重试策略上线。
- [ ] 审批接口从占位实现升级为真实流程引擎。
- [ ] 安全配置完成环境变量化与密钥托管。
- [ ] 自动化测试覆盖核心链路（采集/告警/审批/加密）。
- [ ] 平台可观测性面板上线并纳入日常巡检。

---

*本增强章节作为落地执行基线，优先级高于“愿景型描述”；若发生冲突，以可验证、可交付、可运维为第一原则。*

---

## 13. 终极设计（平台化版本）

本章节给出“终极能力形态”：面向 3-5 年演进，支撑多团队协作、跨环境治理、审计合规与持续优化。定位为企业级数据库运维平台，而非单一监控系统。

### 13.1 北极星指标与 SLO 体系

#### 13.1.1 北极星指标（North Star）

- **MTTD（平均发现时长）**：P1 事件 <= 1 分钟。
- **MTTR（平均恢复时长）**：P1 事件 <= 30 分钟。
- **主动发现率**：>= 75%（非业务方先报障）。
- **自动化闭环率**：>= 60%（告警 -> 建议 -> 工单 -> 执行 -> 验证）。
- **误报率**：<= 8%，**漏报率** <= 5%。

#### 13.1.2 SLO + 错误预算

| 服务域 | SLO | 错误预算（月） | 触发动作 |
|--------|-----|----------------|----------|
| 采集服务 | 可用性 99.95% | 21.6 分钟 | 冻结新功能，优先稳定性修复 |
| 告警服务 | P1 送达成功率 99.99% | 0.01% 丢失 | 启动通道降级与补偿重放 |
| API 服务 | 可用性 99.9%，P95 < 2s | 43.2 分钟 | 限流、缓存、读写拆分 |
| 预测服务 | 日任务成功率 99.5% | 0.5% 失败 | 自动回退上一个稳定模型 |

### 13.2 平台化目标架构（事件驱动）

#### 13.2.1 架构核心

- **控制平面（Control Plane）**：配置、权限、策略、审批、治理规则。
- **数据平面（Data Plane）**：采集、流处理、异常检测、推送、执行。
- **智能平面（Intelligence Plane）**：基线、预测、RCA、策略推荐、反馈学习。

#### 13.2.2 事件总线与解耦

关键事件统一进入总线（Kafka/RabbitMQ 均可）：

- `CollectStarted`
- `CollectSucceeded`
- `CollectFailed`
- `AnomalyDetected`
- `AlertRaised`
- `AlertAcknowledged`
- `RemediationApproved`
- `RemediationExecuted`
- `PostCheckCompleted`

所有消费者幂等处理，使用 `event_id + aggregate_id + version` 去重，保证“至少一次投递”下的最终一致性。

### 13.3 多租户与组织治理模型

#### 13.3.1 组织分层

- **Tenant**（租户/机构）
- **Domain**（业务域）
- **Environment**（prod/uat/dev/dr）
- **Database Group**（数据库分组）
- **Asset**（实例级对象）

#### 13.3.2 权限模型

- RBAC + ABAC 混合：角色权限 + 属性策略（环境、业务域、风险级别、时段）。
- 高风险操作采用 `MFA + 双人复核 + 时间窗口签名`。
- 所有审批动作附带 `policy_snapshot`，保证“事后可复盘当时规则”。

### 13.4 数据治理与生命周期

#### 13.4.1 数据分级

| 级别 | 例子 | 存储要求 | 访问要求 |
|------|------|----------|----------|
| L1 公开 | 健康评分汇总 | 标准存储 | 普通只读 |
| L2 内部 | 指标时序 | 加密存储 | 按域授权 |
| L3 敏感 | SQL 摘要、会话信息 | 字段级脱敏 + 审计 | 审批访问 |
| L4 高敏 | 凭据、密钥引用 | 不落库明文 | 最小权限 + 托管 |

#### 13.4.2 生命周期策略

- 原始高频数据：12-18 个月。
- 分钟级聚合：36 个月。
- 小时/天级聚合：长期保留。
- 审计数据：按监管要求至少 5 年，建议 7 年。
- 归档采用“热温冷”分层（在线查询 -> 近线对象存储 -> 冷归档）。

### 13.5 智能引擎终极方案（MLOps + RuleOps）

#### 13.5.1 双引擎策略

- **RuleOps**：规则引擎负责可解释、可审计的基线告警和处置建议。
- **MLOps**：模型引擎负责预测、异常评分、根因排序。
- 冲突处理：规则优先级高于模型；模型只可“建议”，不可直接越权执行。

#### 13.5.2 模型治理闭环

1. 训练数据快照版本化（dataset_id）。
2. 模型版本化（model_id, feature_schema, metrics）。
3. 灰度发布（10% -> 50% -> 100%）。
4. 在线漂移监控（PSI/KS/误差漂移）。
5. 自动回滚到上一个稳定版本。

### 13.6 告警与处置的“L4 自动驾驶”分级

| 自动化等级 | 含义 | 动作 |
|------------|------|------|
| L1 观测 | 只观测不行动 | 记录与告警 |
| L2 建议 | 给出建议需人工确认 | 生成工单草案 |
| L3 半自动 | 低风险动作自动执行 | 执行后强制验证与回滚预案 |
| L4 闭环 | 策略授权范围内自动闭环 | 自动执行 + 验证 + 审计 + 通知 |

默认从 L1 起步，按数据库分组逐级升档，必须满足连续稳定窗口（例如 30 天）才可升档。

### 13.7 韧性工程与灾备设计

#### 13.7.1 异常场景演练（GameDay）

每月演练至少一次：

- 告警通道故障（钉钉/邮件单通道失效）
- 时序库写入抖动
- 队列堆积
- 核心 worker 宕机
- 区域网络抖动

#### 13.7.2 恢复目标

- **RTO** <= 15 分钟（控制平面）
- **RPO** <= 5 分钟（核心事件与告警状态）
- 跨可用区部署：Web、队列、数据库最少双活或主备自动切换。

### 13.8 发布工程与变更安全

- 所有策略、规则、模型、SQL 模板均走 GitOps。
- 引入“策略即代码”（Policy as Code），审批规则可审计可回滚。
- 发布采用蓝绿/金丝雀，关键链路强制合成流量回归。
- 任何自动执行动作必须具备：
  - 前置检查
  - 幂等执行
  - 后置验证
  - 一键回滚

### 13.9 FinOps 与容量成本优化

新增成本治理维度：

- 单实例监控成本（采集、存储、计算、推送）可观测。
- 按业务域出具“成本-风险”矩阵。
- 对长期低利用实例给出整合建议。
- 对高增长实例提供“扩容 vs 架构优化”双路径建议。

### 13.10 对外生态集成蓝图

- CMDB：资产主数据、归属关系同步。
- ITSM：自动创建与更新工单。
- IAM：统一认证与权限。
- SIEM：安全事件上报。
- APM/日志平台：应用与数据库事件关联分析。

通过统一 `OpenAPI + Webhook + Event Stream` 三种接口形态输出能力，满足不同系统接入模式。

### 13.11 终极路线图（18 个月）

#### 阶段 I（0-3 个月）：稳定化与安全化

- 完成基础稳态、配置安全、关键链路测试覆盖。
- 建立 SLO 仪表盘与错误预算机制。

#### 阶段 II（4-9 个月）：平台化与解耦

- 事件总线接入，控制/数据/智能平面解耦。
- 上线多租户权限模型与策略中心。
- 时序与元数据分层、归档策略落地。

#### 阶段 III（10-18 个月）：智能闭环与自治

- 模型治理平台化，灰度与漂移监控上线。
- L3/L4 自动化能力在低风险域规模化运行。
- 与 CMDB/ITSM/IAM/SIEM 全面对接，形成企业级运维中台能力。

### 13.12 终极验收门槛（Definition of Excellence）

1. 覆盖 >= 500 套数据库实例，跨环境稳定运行。
2. P1 告警端到端（发现->通知）P95 <= 45 秒。
3. 主动发现率 >= 75%，自动化闭环率 >= 60%。
4. 误报率 <= 8%，漏报率 <= 5%。
5. 关键操作 100% 可审计、可回放、可追责。
6. 平台通过季度灾备演练与安全审计。
7. 发布后 30 天内无 P0 级平台故障。

---

*终极设计用于定义“上限”，执行时采用“分层兑现”策略：先兑现稳定性与安全，再兑现智能与自动化，始终以可解释、可审计、可回滚为底线。*