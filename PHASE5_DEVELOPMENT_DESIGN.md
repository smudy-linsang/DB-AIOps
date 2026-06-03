# DB-AIOps Phase 5 开发设计方案 v1.0

> **设计日期**: 2026-05-21
> **当前版本**: v3.0 → **目标版本**: v4.0
> **阶段主题**: 从「监控告警」走向「AIOps 智能诊断与巡检」
> **预计工期**: P0(3-4周) + P1(4-6周) + P2(2-3周) = 总计 9-13 周

---

## 第一部分：总览与架构

### 1.1 两大主题

| 主题 | 目标 | 核心价值 |
|------|------|---------|
| **主题一：告警 RCA 2.0** | 告警→根因→健康度影响→业务影响→方案 | 让 DBA 10 秒内拿到"发生了什么/为什么/影响谁/怎么修" |
| **主题二：智能巡检** | 周期性体检，主动发现潜在问题 | 变"被动救火"为"主动预防" |

### 1.2 整体架构

```
                       ┌─────────────────────────────────────────────┐
                       │          业务系统 / 业务连续性图谱           │
                       │  (BusinessSystem / ApplicationRegistry)     │
                       └──────────────┬──────────────────────────────┘
                                      │
                                      │  关联映射
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          监控数据层 (已具备)                              │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐         │
│  │ 6 Checkers │  │ TimescaleDB│  │Elasticsearch│ │  PostgreSQL │        │
│  │ 指标采集   │  │  时序指标  │  │   搜索聚合  │  │  业务主库   │        │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └──────┬─────┘        │
└────────┼───────────────┼───────────────┼────────────────┼──────────────┘
         │               │               │                │
         └───────────────┴───────────────┴────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                      【主题一】告警 RCA 2.0 闭环                          │
│                                                                          │
│  告警触发 ──┬─→ 上下文聚合器 (Context Aggregator)                         │
│            │      • 拉取告警前后 5/30/60 分钟指标                          │
│            │      • 拉取相关数据库/集群/主机的指标                          │
│            │      • 拉取相关告警（同一对象/同一窗口）                        │
│            │                                                              │
│            ├─→ 根因分析引擎 (RCA 2.0 Engine)                              │
│            │      • 规则引擎 (扩展现有 R001~R010 至 R001~R050)             │
│            │      • 因果图谱 (Causal Graph) - 复合故障链                  │
│            │      • 案例库匹配 (RAG over ES)                              │
│            │                                                              │
│            ├─→ 影响评估引擎 (Impact Assessment Engine) 【新】             │
│            │      • 健康度影响 → 调用 health_engine 计算衰减              │
│            │      • 业务连续性影响 → 查询业务图谱                          │
│            │          (受影响业务/交易笔数/影响时长/影响等级)               │
│            │                                                              │
│            ├─→ 解决方案生成器 (Remediation Planner)                       │
│            │      • 多方案生成 (保守/标准/激进)                            │
│            │      • 风险评级 + 实施步骤 + 回滚方案                        │
│            │      • 审批工作流 (调用 approval_engine)                    │
│            │                                                              │
│            └─→ 输出 → 告警详情页 / 智能工单 / 处置报告                     │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                      【主题二】智能巡检引擎                                │
│                                                                          │
│  巡检计划 ──→ 任务调度器 (Inspection Scheduler)                           │
│                  • 日检 (24 项快速项)  每日 02:00                         │
│                  • 周检 (40 项深度项)  每周日 03:00                        │
│                  • 月检 (60 项全量项)  每月1日 04:00                       │
│                                                                          │
│  巡检项执行器 (Inspection Executor)                                       │
│      • 30+ 通用巡检项 (六大数据库通用)                                    │
│      • 30+ Oracle 专项巡检项 (AWR/对象/任务等)                            │
│      • 20+ MySQL/PG/DM 专项巡检项                                        │
│      • 每项可插拔:  检测SQL + 风险评估 + 修复建议 + 自动修复hook           │
│                                                                          │
│  结果分析层                                                              │
│      • 风险评分 (Risk Score) = 严重度 × 概率 × 业务影响                    │
│      • 问题聚类 (Problem Clustering)                                      │
│      • 趋势对比 (与上次/同比)                                            │
│      • 解决方案生成 (复用 Remediation Planner)                           │
│                                                                          │
│  输出                                                                    │
│      • 巡检报告 (Markdown/HTML/PDF)                                      │
│      • 巡检工单 (自动创建低风险/中等风险待办)                              │
│      • 巡检知识库 (积累历史问题)                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.3 与现有模块的关系

| 现有模块 | 复用方式 | 增强内容 |
|---------|---------|---------|
| `rca_engine.py` | 规则引擎基础 | 扩展到 50+ 规则，新增因果图谱 |
| `auto_remediation_engine.py` | SQL 安全白名单/审计 | 新增多方案生成、风险评估 |
| `health_engine.py` | 5 维度评分 | 新增"告警导致健康度衰减"计算 |
| `report_engine.py` | PDF/Excel 生成 | 扩展支持巡检报告格式 |
| `alert_engine.py` | 告警触发 | 升级告警 payload 携带业务上下文 |
| `approval_engine.py` | 审批流 | 扩展为方案执行审批 |
| `BusinessSystem` 模型 | 业务图谱基础 | 扩展应用-数据库-业务关系 |
| `start_monitor.py` 调度 | 扩展为巡检任务调度 | - |
| TimescaleDB / ES | 案例库存储 | 新增 vector search 能力 |

---

## 第二部分：主题一 — 告警 RCA 2.0 详细设计

### P0-1: 上下文聚合器 (Context Aggregator) 【新】

**功能**：告警触发时，自动拉取相关上下文，喂给 RCA 引擎

**核心方法**：
```python
class ContextAggregator:
    def aggregate(self, alert: AlertLog) -> dict:
        """
        返回:
        {
            'alert': 告警详情,
            'related_metrics': {  # 告警前后 30min 指标
                'conn_usage_pct': [(ts, value), ...],
                ...
            },
            'related_alerts': [    # 同对象/同窗口 同期 告警
                {'rule_id': 'R001', 'time': '...', 'value': ...},
            ],
            'cluster_context': {...},  # 集群/主从/兄弟节点状态
            'business_context': {...},  # 业务系统上下文
            'recent_changes': [...],    # 最近的 schema/参数变更
        }
        """
```

**数据源**：
- 指标：TimescaleDB（按 `config_id + metric_name + 时间范围` 查）
- 历史告警：AlertLog 表（同对象 30min 窗口）
- 业务上下文：BusinessSystem 表（反查）
- 变更：AuditLog 表（72 小时内的 DDL/参数变更）

**输出到 RCA 引擎的增强数据**：
- 不仅有"当前快照"，还有"30min 趋势"
- 不仅有"本节点"，还有"集群兄弟节点"
- 不仅有"指标"，还有"近期变更"

### P0-2: RCA 2.0 引擎 【升级 rca_engine.py】

**A. 规则库扩展：从 10+ 扩展到 50+**

按"对象域"组织规则：
- **连接域** (R001-R010)：连接泄漏、连接风暴、连接池耗尽、idle in transaction
- **SQL域** (R011-R020)：慢 SQL、绑定窥探、解析过载、Library Cache失效
- **锁域** (R021-R030)：行锁等待、表锁、索引分裂、Deadlock、Lock Escalation
- **IO域** (R031-R040)：磁盘写满、日志切换频繁、Checkpoint 慢、Temp 撑爆
- **内存域** (R041-R050)：Buffer Hit 突降、PGA 溢出、Cache Chain 失效
- **复制域** (R051-R060)：主从延迟、GTID gap、binlog 损坏
- **集群域** (R061-R070)：脑裂、节点驱逐、VIP 漂移、连接池耗尽

**B. 因果图谱 (Causal Graph) 【新】**

```python
# 知识表示
CAUSAL_RULES = [
    {
        'cause': 'conn_usage_pct > 95',
        'effect': ['slow_queries_active +', 'lock_waits +', 'cpu_usage +'],
        'typical_chain': '应用连接池配置不当 → 连接数持续上升 → 资源争抢 → 性能下降',
    },
    {
        'cause': 'log_switches_per_hour > 20',
        'effect': ['wait_event "log file sync" +', 'tps -', 'redo_lag +'],
        'typical_chain': '大事务/批量写入 → redo 切换频繁 → log file sync 等待 → TPS 下降',
    },
    # ... 50+ 条
]
```

**C. 复合故障推导**

不是匹配单条规则，而是：
1. 先识别"主征兆"（最严重/最异常的指标）
2. 沿因果图向上溯源"根因"（cause）
3. 沿因果图向下推导"次生影响"（effect chain）
4. 输出树形结构：

```
[根因] 应用连接池配置 max=200 但 DB max=100
  ├── [主征兆] conn_usage_pct=98% (3分钟持续)
  ├── [次生] slow_queries_active=15 (3分钟增长)
  ├── [次生] lock_waits=8 (新建)
  └── [业务影响] 订单库 5 个服务受影响
       ├── 服务A (订单服务) - 高
       ├── 服务B (支付服务) - 中
       └── 服务C (库存服务) - 中
```

**D. 案例库匹配 (RAG over Elasticsearch)**

```python
# 流程
1. 告警的"特征向量" = 当前指标快照 + RCA 标签 + 业务标签
2. 在 ES 中检索相似历史案例 (top 5)
3. 用 LLM/Ranker 排序，给出"最可能根因"和"成功处置方案"
```

**案例库结构** (新模型)：
```python
class AlertCase(models.Model):
    """历史告警案例库"""
    case_id = models.CharField(unique=True)
    title = models.CharField()
    symptom_signature = JSONField()  # 指标特征
    root_cause = models.TextField()
    resolution = models.TextField()
    sql_used = models.TextField(blank=True)
    tags = JSONField()  # ['oracle', 'tablespace', 'oltp']
    success_count = models.IntegerField(default=0)
    fail_count = models.IntegerField(default=0)
    embedding = models.BinaryField(null=True)  # 向量（可选 v2）
    created_at = models.DateTimeField()
```

### P0-3: 影响评估引擎 (Impact Assessment Engine) 【新】

**A. 健康度影响计算**

```python
class HealthImpactCalculator:
    """告警如何影响健康分"""
    IMPACT_MAP = {
        # 告警类型 → (受影响维度, 衰减系数, 时长)
        'tablespace_full':      ('capacity', 0.30, 'until_resolved'),
        'connection_exhausted': ('availability', 0.40, '1h'),
        'tablespace_warning':   ('capacity', 0.05, '24h'),
        'long_transaction':     ('performance', 0.10, '4h'),
        'replication_lag':      ('availability', 0.20, 'until_resolved'),
    }
    
    def calculate(self, alert, current_health_score) -> dict:
        return {
            'original_score': current_health_score,
            'impacted_score': current_health_score * (1 - 衰减系数),
            'affected_dimensions': ['capacity', 'performance'],
            'expected_recovery_hours': 4.5,
        }
```

**B. 业务连续性影响评估 【新】**

```python
class BusinessImpactAssessor:
    """评估对业务的影响"""
    
    def assess(self, db: DatabaseConfig, alert: AlertLog) -> dict:
        # 1. 查询该 DB 关联的所有业务系统
        systems = db.business_systems.all()  # 已有 BusinessSystem 模型
        
        # 2. 对每个系统评估影响
        impacts = []
        for sys in systems:
            impact_level = self._compute_impact_level(sys, alert)
            impacts.append({
                'system_name': sys.name,
                'criticality': sys.criticality,  # 已有字段
                'impact_level': impact_level,    # 致命/严重/中等/轻微
                'affected_transactions': self._estimate_tps_loss(sys, alert),
                'estimated_users_affected': sys.user_count,
                'sla_breach': self._check_sla_breach(sys, alert),
            })
        
        return {
            'total_systems_affected': len(impacts),
            'critical_systems_affected': sum(1 for i in impacts if i['impact_level'] == 'fatal'),
            'estimated_business_loss_per_hour': ...,
            'sla_breach_risk': 'high',
            'system_impacts': impacts,
        }
```

**评估矩阵**：

| 告警类型 | 业务影响（无业务上下文时） | 业务影响（核心交易库） |
|---------|----------------------|---------------------|
| 实例不可用 | 致命（100%中断） | 致命（核心业务全停） |
| 连接耗尽 | 严重（部分请求失败） | 致命（订单/支付不可用） |
| 表空间 100% | 严重（写入失败） | 严重（核心业务写入失败） |
| 主从延迟 > 5min | 中等（读不一致） | 严重（报表/查询业务） |
| 慢查询 100+ | 中等（部分慢） | 严重（用户体验差） |
| 索引缺失 | 轻微（性能差） | 中等（订单查询慢） |

### P0-4: 解决方案生成器 (Remediation Planner) 【升级 auto_remediation】

**A. 多方案生成**

不再只有"一个 SQL"，而是"3 套方案"：
- **保守方案** (low risk)：监控为主，最小操作（如收集信息、扩容、调整参数）
- **标准方案** (medium risk)：优化结构（如建索引、清理回收站）
- **激进方案** (high risk)：直接 kill session、truncate 临时表（需审批）

**B. 方案结构**

```python
{
    'plan_id': 'P-20260521-001',
    'title': '解决订单库连接池耗尽',
    'scenarios': [
        {
            'name': '保守方案',
            'risk_level': 'low',
            'estimated_time': '5min',
            'auto_executable': True,
            'steps': [
                {
                    'order': 1,
                    'action': 'check_pool',
                    'description': '查看应用连接池配置',
                    'command': 'show application config via http://...',
                    'expected_outcome': '获取当前 max_pool_size',
                },
                {
                    'order': 2,
                    'action': 'kill_idle',
                    'description': '清理空闲超过 30min 的会话',
                    'sql': "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle' AND state_change < now() - interval '30 min'",
                    'risk': 'low',
                    'rollback': None,
                },
            ],
        },
        {
            'name': '激进方案',
            'risk_level': 'high',
            'auto_executable': False,  # 需审批
            'requires_approval': True,
            'steps': [
                {
                    'order': 1,
                    'action': 'restart_app_pool',
                    'description': '重启应用连接池',
                    'command': 'curl -X POST http://app/api/pool/restart',
                    'risk': 'medium',
                    'rollback': 'rollback: 不支持 (连接会重建)',
                },
            ],
        },
    ],
    'recommended': 'conservative',  # 默认推荐
    'business_impact': {
        'conservative': '低 - 5 分钟内恢复',
        'aggressive': '中 - 应用瞬断 10 秒',
    },
}
```

**C. 审批集成**

```python
# 与 approval_engine.py 集成
if plan.scenarios[0].risk_level in ('high', 'critical'):
    approval = ApprovalWorkflow.create(
        plan=plan,
        approvers=['DBA_lead', 'DBA_manager'],
        timeout_min=15,
    )
    # 等待审批 OR 自动定时执行
```

### P0-5: 告警详情页升级 (前端)

**A. 告警详情页新增 4 大区域**：

```
┌─────────────────────────────────────────────────┐
│ [告警标题] 核心订单库表空间使用率达 95%           │
│ [严重度] ERROR [开始时间] 10:23:45               │
├─────────────────────────────────────────────────┤
│ 📊 1. 根因诊断 (RCA 2.0)                         │
│   ✓ 主征兆: 表空间 ORDER_DATA 已用 95%          │
│   ✓ 根因:   订单表日增长 20GB/天，月初归档失败    │
│   ✓ 因果链: 归档失败 → 表空间不释放 → 撑满       │
│   ✓ 置信度: 87% (匹配 3 个历史案例)              │
├─────────────────────────────────────────────────┤
│ 🏥 2. 健康度影响                                 │
│   当前: 78 分 (C级) → 预计: 60 分 (D级)         │
│   影响维度: 容量 (-30%)                         │
├─────────────────────────────────────────────────┤
│ 💼 3. 业务影响                                   │
│   ⚠️  核心订单库 - 致命 (写入即将失败)          │
│   • 订单服务: 影响等级 高                        │
│   • 支付服务: 影响等级 高                        │
│   • 库存服务: 影响等级 中                        │
│   • 预计每分钟损失: 80 单 (¥40,000/min)         │
├─────────────────────────────────────────────────┤
│ 🛠️  4. 解决方案                                  │
│   [保守] 立即扩容数据文件 - 风险低  [一键执行]    │
│   [标准] 归档旧订单数据          - 风险中  [审批]  │
│   [激进] kill 阻塞归档的会话     - 风险高  [审批]  │
│                                                  │
│   📜 历史相似案例: 5 例 (3 例已解决)              │
│   📖 相关知识库: 3 篇文档                        │
└─────────────────────────────────────────────────┘
```

**B. 智能工单**

告警触发后，自动创建工单，包含 RCA + 业务影响 + 方案。值班 DBA 收到后可直接在工单中执行。

---

## 第三部分：主题二 — 智能巡检引擎详细设计

### P1-1: 巡检规则库 (Inspection Rule Registry) 【新】

**统一规则结构**：

```python
@dataclass
class InspectionItem:
    item_id: str                # 'INS-DB-LOG-SWITCH'
    category: str               # 日志/表/索引/对象/任务
    applicable_db_types: list   # ['oracle', 'mysql', ...]
    severity: str               # info/warn/error/critical
    title: str                  # '日志切换频率过高'
    description: str            # 检测逻辑说明
    detect_sql: str             # 检测 SQL/方法
    threshold: dict             # {'warn': 10, 'error': 20, 'critical': 30}
    recommendation: str         # 解决方案
    auto_fixable: bool          # 是否可自动修复
    auto_fix_sql: str           # 自动修复 SQL（可选）
    references: list            # ['MOS 123.1', 'Best Practice: ...']
    est_inspect_time_sec: int   # 预计耗时
```

### P1-2: 30+ 通用巡检项（6 库通用）

| # | item_id | 标题 | 适用 | 严重度 |
|---|---------|------|------|-------|
| 1 | INS-OBJ-INVALID | 无效对象检查 | 所有 | warn |
| 2 | INS-RECYCLEBIN | 回收站清理 | 所有 | warn |
| 3 | INS-USER-LOCKED | 账号锁定 | 所有 | warn |
| 4 | INS-USER-PWD-EXPIRE | 密码即将过期 | 所有 | warn |
| 5 | INS-CAPACITY-7D | 7天容量预测 | 所有 | info |
| 6 | INS-CAPACITY-30D | 30天容量预测 | 所有 | info |
| 7 | INS-CONN-PEAK | 连接峰值 | 所有 | warn |
| 8 | INS-LONG-TXN | 长事务 | 所有 | warn |
| 9 | INS-DEADLOCK-24H | 24h 死锁 | 所有 | warn |
| 10 | INS-SLOW-LOG | 慢查询日志 | 所有 | info |
| 11 | INS-INDEX-USAGE | 索引使用率 | 所有 | warn |
| 12 | INS-UNUSED-INDEX | 30天未使用索引 | 所有 | warn |
| 13 | INS-MISSING-INDEX | 缺失索引（高 cost 扫描） | 所有 | warn |
| 14 | INS-FK-NO-INDEX | 外键无索引 | 所有 | warn |
| 15 | INS-PARTITION-NEEDED | 大表未分区 | 所有 | warn |
| 16 | INS-STAT-STALE | 统计信息过期 | 所有 | warn |
| 17 | INS-SPARSE-INDEX | 稀疏索引（distinct 比例 < 5%） | 所有 | info |
| 18 | INS-BLEVEL-HIGH | BLEVEL > 4 索引 | 所有 | warn |
| 19 | INS-SEQ-NEAR-MAX | 序列接近 MAX | 所有 | warn |
| 20 | INS-SEQ-NO-CACHE | 序列无 CACHE | 所有 | info |
| 21 | INS-PARAM-DEVIATION | 参数偏离最佳实践 | 所有 | warn |
| 22 | INS-BACKUP-STATUS | 备份状态 | 所有 | error |
| 23 | INS-ARCH-GAP | 归档间隙 | Oracle/DM | error |
| 24 | INS-LOG-SWITCH | 日志切换频率 | Oracle/DM/PG | warn |
| 25 | INS-REDO-LAG | Redo Lag | Oracle/DM | warn |
| 26 | INS-LOCK-WAIT | 当前锁等待 | 所有 | warn |
| 27 | INS-BLOCKED-SESSION | 被阻塞会话 | 所有 | warn |
| 28 | INS-OPEN-CURSOR | 打开游标数 | Oracle | info |
| 29 | INS-TEMP-USAGE | 临时段使用率 | 所有 | warn |
| 30 | INS-UNDO-USAGE | Undo 段使用率 | Oracle/DM/PG | warn |

### P1-3: 30+ Oracle 专项巡检项

| # | item_id | 标题 | 严重度 |
|---|---------|------|-------|
| 31 | INS-AWR-SNAPSHOT-INTERVAL | AWR 快照间隔 | warn |
| 32 | INS-AWR-RETENTION | AWR 保留周期 | warn |
| 33 | INS-AWR-TOP-EVENT-PEAK | 业务高峰 Top Event | info |
| 34 | INS-AWR-TOP-SQL-PEAK | 业务高峰 Top SQL | info |
| 35 | INS-AWR-LOAD-PROFILE | 负载画像（DB Time/Executions） | info |
| 36 | INS-AWR-IO-PROFILE | IO 画像（read/write Mb/s） | info |
| 37 | INS-SCN-HEADROOM | SCN Headroom（兼容性风险） | critical |
| 38 | INS-SQL-TUNING-ADVISOR | SQL Tuning Advisor 结果 | warn |
| 39 | INS-SEGMENT-ADVISOR | Segment Advisor 结果 | warn |
| 40 | INS-AUTO-MAINTENANCE-WINDOW | 自动维护窗口合理性 | info |
| 41 | INS-AUTO-SPACE-ADVISOR | Auto Space Advisor 状态 | warn |
| 42 | INS-AUTO-SQL-TUNING | Auto SQL Tuning 状态 | warn |
| 43 | INS-OPTIMIZER-STATS-GATHER | 自动统计信息收集 | warn |
| 44 | INS-DATAPUMP-COUNT | 残留 DataPump 作业 | warn |
| 45 | INS-EXPDP-IMPDP-AGING | 长期运行的导出导入 | warn |
| 46 | INS-FRA-USAGE | 快速恢复区使用率 | warn |
| 47 | INS-CONTROLFILE-BACKUP | 控制文件备份 | warn |
| 48 | INS-ARCHIVELOG-DELETION | 归档日志清理策略 | warn |
| 49 | INS-PASSWORD-LIFE | 密码生命周期 | warn |
| 50 | INS-FAILED-LOGIN | 失败登录异常 | warn |
| 51 | INS-DBA-USERS | DBA 权限账号过多 | warn |
| 52 | INS-TABLESPACE-AUTOEXTEND | 表空间自动扩展 | info |
| 53 | INS-LOB-PCTVERSION | LOB PCTVERSION 异常 | warn |
| 54 | INS-DBLINK | 残留 DBLINK | info |
| 55 | INS-DIRECTORY-OBJ | 异常 Directory 对象 | warn |
| 56 | INS-RMAN-BACKUP-AGE | RMAN 备份距今时长 | error |
| 57 | INS-DATAGUARD-LAG | DataGuard Lag | error |
| 58 | INS-DATAGUARD-STATUS | DataGuard 状态 | error |
| 59 | INS-RAC-VIP | RAC VIP 状态 | warn |
| 60 | INS-RAC-BLOCK-SERV | RAC Block Serve 性能 | info |

### P1-4: 20+ MySQL/PG/DM/GBase/TDSQL 专项

**MySQL**:
- INS-MYSQL-BINLOG-DELAY: binlog dump 延迟
- INS-MYSQL-GTID-CONSISTENCY: GTID 一致性
- INS-MYSQL-SEMI-SYNC: 半同步复制状态
- INS-MYSQL-EVENT-STATUS: Event 调度器状态
- INS-MYSQL-MVIEW-NEVER-USED: 从未使用的物化视图
- INS-MYSQL-FK-CASCADE: 危险外键级联

**PostgreSQL**:
- INS-PG-WAL-RECEIVER: WAL Receiver 状态
- INS-PG-REPL-SLOT-INACTIVE: 失效复制槽
- INS-PG-VACUUM-FREEZE-AGE: 表 freeze age
- INS-PG-BLOAT-ESTIMATE: 表/索引膨胀估算
- INS-PG-EXTENSION-OUTDATED: 扩展版本过旧
- INS-PG-STATEMENT-TIMEOUT: 应用是否设置超时

**达梦**:
- INS-DM-DSC-NODES: DSC 节点状态
- INS-DM-DW-STANDBY: 主备同步状态
- INS-DM-ARCH-STATUS: 归档状态
- INS-DM-RLOG-SYNC: RLOG 同步状态

**GBase**:
- INS-GBASE-NODE-BALANCE: 节点负载均衡度
- INS-GBASE-DISTRIBUTION: 数据分布均衡度

**TDSQL**:
- INS-TDSQL-SHARD-STATUS: 分片状态
- INS-TDSQL-PROXY-STATUS: Proxy 状态
- INS-TDSQL-ZK-STATUS: ZK 状态

### P1-5: 巡检调度器 (Inspection Scheduler) 【新】

```python
# Celery Beat 调度
CELERY_BEAT_SCHEDULE = {
    'daily-inspection': {
        'task': 'monitor.tasks.run_inspection',
        'schedule': crontab(hour=2, minute=0),  # 每天 02:00
        'kwargs': {'level': 'daily'},
    },
    'weekly-inspection': {
        'task': 'monitor.tasks.run_inspection',
        'schedule': crontob(day_of_week=0, hour=3, minute=0),  # 周日 03:00
        'kwargs': {'level': 'weekly'},
    },
    'monthly-inspection': {
        'task': 'monitor.tasks.run_inspection',
        'schedule': crontob(day_of_month=1, hour=4, minute=0),  # 每月1日 04:00
        'kwargs': {'level': 'monthly'},
    },
}
```

**巡检级别**:
- **日检 (Level 1)**：30 项快速项（耗时 < 2min/库）
- **周检 (Level 2)**：日检 + 30 项深度项（耗时 < 10min/库）
- **月检 (Level 3)**：周检 + 30 项全量项（耗时 < 30min/库）

### P1-6: 巡检执行器 (Inspection Executor) 【新】

```python
class InspectionExecutor:
    def __init__(self, db_config: DatabaseConfig):
        self.db_config = db_config
        self.db_type = db_config.db_type
        self.connector = DbConnector.get_connection(db_config)
    
    def run_item(self, item: InspectionItem) -> InspectionResult:
        """执行单个巡检项"""
        start = time.time()
        try:
            # 1. 执行检测
            raw_data = self._execute_detection(item)
            
            # 2. 评估风险
            risk = self._assess_risk(item, raw_data)
            
            # 3. 生成修复建议
            solution = self._generate_solution(item, risk)
            
            return InspectionResult(
                item_id=item.item_id,
                status='NORMAL' if risk.severity == 'info' else 'FOUND',
                severity=risk.severity,
                raw_data=raw_data,
                risk_score=risk.score,
                solution=solution,
                duration_sec=time.time() - start,
            )
        except Exception as e:
            return InspectionResult(
                item_id=item.item_id,
                status='ERROR',
                error=str(e),
                duration_sec=time.time() - start,
            )
    
    def run_all(self, level: str) -> List[InspectionResult]:
        """执行一组巡检项"""
        items = self._get_items_for_level(level)
        results = []
        for item in items:
            if not item.applicable_to(self.db_type):
                continue
            result = self.run_item(item)
            results.append(result)
        return results
```

### P1-7: AWR 分析模块 (AWR Analyzer) 【新 - Oracle 专项】

**A. AWR 快照采集**：
```sql
-- 业务高峰时段（默认 09:00-11:00, 14:00-17:00）最近 7 天的快照
SELECT snap_id, begin_interval_time, end_interval_time
FROM dba_hist_snapshot
WHERE begin_interval_time BETWEEN ... AND ...
ORDER BY begin_interval_time;
```

**B. Top Event 分析**：
```sql
SELECT event, total_waits_fg, time_waited_micro_fg
FROM dba_hist_system_event
WHERE snap_id IN (...)
ORDER BY time_waited_micro_fg DESC
FETCH FIRST 20 ROWS ONLY;
```

**C. Top SQL 分析**：
```sql
SELECT sql_id, executions_total, elapsed_time_total, sql_text
FROM dba_hist_sqlstat
WHERE snap_id IN (...)
ORDER BY elapsed_time_total DESC
FETCH FIRST 20 ROWS ONLY;
```

**D. 业务高峰识别**：
- 自动从 AWR 找出 DB Time 最高的几个时段，标记为"业务高峰"
- 提取该时段的 Top Event / Top SQL 作为巡检项

### P1-8: 巡检报告生成器 (Inspection Report Generator) 【扩展 report_engine】

```python
class InspectionReport:
    def generate(self, run: InspectionRun) -> dict:
        return {
            'meta': {
                'run_id': run.id,
                'level': run.level,  # daily/weekly/monthly
                'started_at': run.started_at,
                'duration_sec': run.duration_sec,
                'total_items': run.total_items,
                'passed_items': run.passed_items,
                'failed_items': run.failed_items,
            },
            'risk_summary': {
                'critical_count': ...,
                'error_count': ...,
                'warn_count': ...,
                'info_count': ...,
                'total_risk_score': ...,
            },
            'findings_by_category': {
                'tablespace': [Finding, ...],
                'index': [Finding, ...],
                'object': [Finding, ...],
                'task': [Finding, ...],
            },
            'top_findings': [Finding],  # 风险评分 Top 10
            'solutions': [...],          # 解决方案
            'trend_comparison': {
                'vs_last_run': {...},     # 与上次巡检对比
                'vs_last_month': {...},   # 与上月同期对比
            },
            'auto_tickets_created': [...],  # 自动创建的工单
        }
```

**报告输出**：
- 邮件摘要 (HTML)
- 完整报告 (PDF)
- 飞书/钉钉卡片
- Web 端详情页

### P1-9: 巡检知识库 (Inspection Knowledge Base) 【新】

把每次巡检的结果、问题、解决方案累积起来：

```python
class InspectionHistory(models.Model):
    """巡检历史记录"""
    db_config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE)
    run_id = models.CharField()
    level = models.CharField()  # daily/weekly/monthly
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    total_items = models.IntegerField()
    failed_items = models.IntegerField()
    risk_score = models.FloatField()
    findings = JSONField()  # [{item_id, severity, raw_data, solution}, ...]
    report_url = models.CharField()

class InspectionIssuePattern(models.Model):
    """问题模式 - 用于预测"""
    pattern_signature = models.CharField(unique=True)
    description = models.CharField()
    occurrence_count = models.IntegerField()
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    recommended_action = models.TextField()
    auto_resolve = models.BooleanField(default=False)
```

---

## 第四部分：实施计划

### 4.1 分阶段路线图

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Phase 5 总工期: 9-13 周                                                   │
│                                                                          │
│ ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐          │
│ │ P0 (3-4周)      │  │ P1 (4-6周)       │  │ P2 (2-3周)     │          │
│ │ 告警RCA 2.0     │  │ 智能巡检引擎     │  │ 集成/优化      │          │
│ │ 主体框架        │  │ 巡检规则库      │  │ 案例库RAG     │          │
│ │                 │  │ + 调度+执行     │  │ 自动修复闭环  │          │
│ │ • 上下文聚合器  │  │ + 报告          │  │ 知识沉淀     │          │
│ │ • RCA 2.0 引擎  │  │                 │  │              │          │
│ │ • 影响评估      │  │ • 60+ 巡检项    │  │              │          │
│ │ • 方案生成器    │  │ • 调度器        │  │              │          │
│ │ • 告警详情页    │  │ • 执行器        │  │              │          │
│ └─────────────────┘  │ • AWR 分析      │  └────────────────┘          │
│                      │ • 报告生成      │                              │
│                      └──────────────────┘                              │
└──────────────────────────────────────────────────────────────────────────┘
```

### 4.2 P0 详细计划（3-4周）

| 周次 | 任务 | 交付物 |
|------|------|-------|
| **W1** | 数据模型设计 + 上下文聚合器 | `ContextAggregator` 类 + `AlertCase` 模型 |
| **W2** | RCA 2.0 规则扩展 (10→30) + 因果图谱 | `rca_engine_v2.py` + `causal_graph.yaml` |
| **W3** | 影响评估引擎 (健康度+业务) | `impact_engine.py` + `business_assessor.py` |
| **W4** | 方案生成器 + 审批集成 + 前端告警详情页升级 | `remediation_planner.py` + 4 大新区域 |

### 4.3 P1 详细计划（4-6周）

| 周次 | 任务 | 交付物 |
|------|------|-------|
| **W5** | 巡检规则库（通用 30 项） | `inspection_registry.py` + `InspectionItem` 模型 |
| **W6** | 巡检执行器 + 调度器 | `inspection_executor.py` + `inspection_scheduler.py` |
| **W7** | Oracle 专项 30 项 + AWR 分析 | `awr_analyzer.py` + Oracle 30 项规则 |
| **W8** | MySQL/PG/DM/GBase/TDSQL 专项 20 项 | 5 库专项规则 |
| **W9** | 巡检报告生成器 + 趋势对比 | `inspection_report.py` + PDF/HTML 输出 |
| **W10** | 巡检知识库 + 模式识别 | `InspectionHistory` + `InspectionIssuePattern` |

### 4.4 P2 详细计划（2-3周）

| 周次 | 任务 | 交付物 |
|------|------|-------|
| **W11** | 案例库 RAG 集成 | 案例向量化 + 相似度检索 |
| **W12** | 自动修复闭环（巡检发现→自动修复） | `auto_remediation_for_inspection.py` |
| **W13** | 知识沉淀 + 优化 + 文档 | 文档/培训材料/部署指南 |

### 4.5 里程碑

| 里程碑 | 时间 | 验收标准 |
|--------|------|---------|
| **M1: RCA 2.0 主体** | W4 结束 | 告警详情页能看到根因/健康度影响/业务影响/方案 |
| **M2: 巡检基础** | W6 结束 | 日检能跑通，30 项通用项可执行 |
| **M3: 全量巡检** | W10 结束 | 6 库 60+ 项全量巡检，月检报告完整 |
| **M4: 智能化闭环** | W13 结束 | 案例库 RAG 工作，自动修复闭环 |

---

## 第五部分：关键设计决策

### 5.1 数据存储

| 数据 | 存储 | 理由 |
|------|------|------|
| 案例库 | Elasticsearch (with vector field) | 全文搜索+向量检索 |
| 巡检历史 | PostgreSQL | 结构化，关联DB |
| 巡检报告 | 文件系统 (PDF) + 索引在 PG | 报告体量大，PG存元数据 |
| AWR 快照摘要 | TimescaleDB | 时序数据，保留 90 天 |

### 5.2 关键技术选型

| 用途 | 选型 | 理由 |
|------|------|------|
| 案例相似度 | sentence-transformers (离线) / 调用 LLM API | 离线优先 |
| 因果图谱 | NetworkX (Python 图计算库) | 轻量、够用 |
| 巡检调度 | Celery Beat | 已有基建 |
| 巡检并发 | ThreadPoolExecutor (per-DB) | 库内串行，库间并行 |
| 风险评分 | 加权模型 (严重度×概率×业务影响) | 简单可解释 |

### 5.3 与现有模块的边界

| 边界 | 原则 |
|------|------|
| RCA 2.0 不修改 alert_engine 触发逻辑 | 只增强告警 payload 携带上下文 |
| 智能巡检不重复采集已有指标 | 复用 6 Checker 的输出，巡检只跑专项 SQL |
| 方案生成不绕过 approval_engine | 所有 high/critical 风险方案走审批 |
| 知识库不破坏现有表 | 新增独立模型，原表零改动 |

### 5.4 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 巡检 SQL 影响生产性能 | 中 | 高 | 慢查询风险项加 LIMIT，默认低峰执行 |
| AWR 视图查询慢 | 中 | 中 | 快照范围限定为 7 天，TOP N 限制 20 |
| 案例库数据稀疏初期不准确 | 高 | 中 | 启动时无 RAG，纯规则；积累 6 月后再启用 RAG |
| 业务图谱数据缺失 | 中 | 中 | 业务图谱可降级为"未配置业务上下文" |
| 方案误执行 | 中 | 高 | 严格 SQL 白名单 + 审批 + 二次确认 |

---

## 第六部分：验收标准

### 主题一 验收

- [ ] **RCA 2.0**：告警详情页展示根因/健康度影响/业务影响/解决方案四大区域
- [ ] **因果图谱**：复合故障能输出树形因果链
- [ ] **影响评估**：健康度衰减计算 + 业务影响清单（按系统枚举）
- [ ] **方案生成**：每个告警能生成 2-3 套方案（保守/标准/激进）
- [ ] **审批集成**：高风险方案走审批工作流
- [ ] **案例库**：相似案例检索 top-5 准确率 ≥ 70%

### 主题二 验收

- [ ] **60+ 巡检项**：通用 30 + Oracle 30 + 其他 20+ 全部可执行
- [ ] **三档调度**：日检/周检/月检 准时执行
- [ ] **6 库覆盖**：Oracle/MySQL/PG/DM/GBase/TDSQL 均支持
- [ ] **报告输出**：PDF/HTML/邮件三种格式
- [ ] **趋势对比**：与上次/同比对比
- [ ] **自动工单**：低风险项自动创建待办
- [ ] **巡检知识库**：3 个月后模式识别可用

---

## 第七部分：核心文件变更清单

### 主题一 (P0)

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 1 | `monitor/models.py` | 修改 | 新增 AlertCase / InspectionRun 等模型 |
| 2 | `monitor/context_aggregator.py` | **新建** | 上下文聚合器 |
| 3 | `monitor/rca_engine_v2.py` | **新建** | RCA 2.0 (保留 v1 兼容) |
| 4 | `monitor/causal_graph.py` | **新建** | 因果图谱 |
| 5 | `monitor/impact_engine.py` | **新建** | 影响评估 |
| 6 | `monitor/health_impact.py` | **新建** | 健康度衰减 |
| 7 | `monitor/business_assessor.py` | **新建** | 业务影响评估 |
| 8 | `monitor/remediation_planner.py` | **新建** | 多方案生成器 |
| 9 | `monitor/alert_engine.py` | 修改 | 告警 payload 增强 |
| 10 | `frontend/src/pages/AlertDetail.jsx` | **新建/重构** | 告警详情页 |
| 11 | `frontend/src/components/RcaPanel.jsx` | **新建** | RCA 展示 |
| 12 | `frontend/src/components/ImpactPanel.jsx` | **新建** | 影响展示 |
| 13 | `frontend/src/components/RemediationPanel.jsx` | **新建** | 方案展示 |

### 主题二 (P1)

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 14 | `monitor/inspection_registry.py` | **新建** | 巡检规则库 |
| 15 | `monitor/inspection_executor.py` | **新建** | 巡检执行器 |
| 16 | `monitor/inspection_scheduler.py` | **新建** | 巡检调度 |
| 17 | `monitor/awr_analyzer.py` | **新建** | AWR 分析 |
| 18 | `monitor/inspection_report.py` | **新建** | 巡检报告 |
| 19 | `monitor/inspection_knowledge.py` | **新建** | 巡检知识库 |
| 20 | `monitor/management/commands/init_inspection_rules.py` | **新建** | 初始化巡检项 |
| 21 | `monitor/checkers/oracle.py` | 修改 | 增加 30+ 巡检 SQL |
| 22 | `monitor/checkers/mysql.py` | 修改 | 增加专项巡检 |
| 23 | `monitor/checkers/pgsql.py` | 修改 | 增加专项巡检 |
| 24 | `frontend/src/pages/InspectionList.jsx` | **新建** | 巡检列表 |
| 25 | `frontend/src/pages/InspectionDetail.jsx` | **新建** | 巡检详情 |
| 26 | `frontend/src/pages/InspectionReport.jsx` | **新建** | 巡检报告 |

---

## 第八部分：业务价值总结

| 能力 | 现状 | Phase 5 完成后 | 业务价值 |
|------|------|---------------|---------|
| 告警处理 | 仅推送，需 DBA 手动查 | RCA + 方案一键执行 | MTTR ↓ 50% |
| 故障定位 | 人工经验 | 30 条规则 + 因果图谱 + 案例库 | 定位时间 ↓ 70% |
| 业务影响 | 无 | 业务图谱 + 损失评估 | 优先保障核心业务 |
| 巡检 | 手工 SQL | 自动化定期巡检 + 报告 | DBA 效率 ↑ 80% |
| 潜在问题 | 事后发现 | 巡检预防 + 模式识别 | 故障 ↓ 60% |
| 知识沉淀 | 散落在人 | 结构化案例库 + 知识图谱 | 团队能力 ↑ 100% |

---

> **下一步建议**：
> 1. 先 Review 本设计文档
> 2. 确认 P0 (RCA 2.0) 的优先级和范围
> 3. 启动 P0-W1: 数据模型 + 上下文聚合器
