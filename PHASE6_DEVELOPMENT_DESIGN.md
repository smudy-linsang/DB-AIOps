# DB-AIOps Phase 6 开发设计方案 —— "1-5-10" 事件驱动智能运维

> **设计日期**: 2026-07-12
> **当前版本**: v4.0 → **目标版本**: v5.0
> **阶段主题**: 从「监控 + 分析」走向「事件驱动的发现-诊断-处置闭环」
> **对标参考**: Oracle Enterprise Manager Cloud Control 13c（事件/事故模型、ADDM、ASH、
> 自适应阈值、Corrective Actions、Incident Manager）
> **核心目标（唯一验收口径）**: **1 分钟发现问题、5 分钟给出解决方案、10 分钟解决问题**
> **状态**: 仅设计，未施工

---

## 第一部分：现状能力审计（基于代码实测，非文档宣称）

### 1.1 现有资产盘点

| 层 | 已有能力 | 代码位置 | 成熟度 |
|----|---------|---------|--------|
| 采集 | 6 库 Checker、60s 轮询、20 线程并发、TDSQL 真实环境已验证 | checkers/*, start_monitor.py | ★★★★ |
| 存储 | PG + TimescaleDB(指标/快照) + ES(检索) + Redis(SSE) | timeseries.py, elasticsearch_engine.py | ★★★★ |
| 基线 | 168 时间槽 + Welford 在线更新 + 三重判定(量级/方向/持续) | baseline_engine.py, alert_engine.py | ★★★ |
| 告警 | AlertLog 去重/静默窗口/聚合缓冲/超时升级/邮件+钉钉+企微 | alert_manager.py, notifications.py | ★★★ |
| RCA | v2 规则库 35 条(R001-R080, 9 域)、置信度、因果链 | rca_engine_v2.py | ★★★ |
| 上下文 | 30min 指标/相关告警/集群/业务/近期变更聚合 | context_aggregator.py | ★★★ |
| 影响评估 | 健康度衰减 + 业务系统影响 | impact_engine.py | ★★ |
| 方案生成 | 场景模板(保守/标准/激进) + 分步 SQL | remediation_planner.py | ★★ |
| 自动修复 | 5 个白名单动作 + 风险分级 + 审批流 | auto_fix_loop.py, approval_engine.py | ★★ |
| 巡检 | 82 项注册表 + 执行器 + 3 个 TDSQL 真实检测器（2026-07 已修通） | inspection_* | ★★★ |
| 推送 | SSE(alerts/metrics 双通道) | sse_views.py | ★★★ |

### 1.2 对照 1-5-10 的真实差距（关键结论）

**差距一：发现慢 —— 当前 P1 级问题实际 MTTD ≈ 2~5 分钟**

- 一切靠 60s 轮询：worst case = 60s 间隔 + 采集耗时（TDSQL 广域网实测 30~100s）+ 串行分析。
- 采集环里塞了重活：实测单轮中 D5 慢查询分析 10s、D6 配置检查 20s（各自还另开 WAN 连接），
  拖慢整个发现节奏。
- 基线告警要求「连续 N 轮异常」的持续性条件 → 渐变型问题 3 分钟起步。
- 宕机发现依赖采集连接失败（含 5 次重试 ≈ 75s）→ DOWN 的 MTTD 可达 2~3 分钟。
- **没有秒级探活；没有会话级采样（ASH 类能力空白）；没有事件驱动，全是批处理节奏。**

**差距二：方案被动 —— RCA/方案链路存在但「人不点就不算」**

- `AlertRCADetailView` 的调用链（上下文聚合 → RCA v2 → 影响评估 → 方案生成）
  **只在用户打开告警详情页时按需计算**，不预计算、不落库、不进通知。
- 告警通知只有标题和描述，DBA 收到后仍要登录平台、找告警、点详情、等计算 —— 5 分钟窗口
  被人的反应时间吃掉。
- ml_anomaly_detection / intelligent_baseline_engine 是孤岛模块，未接入主采集环。

**差距三：解决无闭环 —— 修复动作与告警不联动，修完没人确认**

- FixRules 的 5 个白名单动作只绑定巡检 finding（`execute_finding`），告警/事故触发不了。
- **没有验证回路**：执行修复后无自动复查指标恢复的机制，事故不会自动关闭。
- 没有回滚编排；审批流按风险分级但无 P1 紧急通道（事后审批）。
- PLAN_TEMPLATES（planner）/ FIX_RULES（auto_fix）/ auto_remediation_engine 三套半成品
  各自为政，动作定义重复且互不引用。

**差距四：告警模型平面化 —— 缺 EM13c 的 Event→Incident→Problem 分层**

- AlertLog 是单层平面记录，风暴时（本地部署实测一轮就产生 27 条告警）人无法聚焦。
- 无优先级评分（P1-P4）、无事故状态机、无 MTTD/MTTR 计时，1-5-10 无法度量也就无法管理。

---

## 第二部分：总体设计 —— 六大支柱

```
            ┌─────────────────────────────────────────────────────────┐
            │  支柱六: 作战室与 SLA 度量 (Incident Manager UI + 1-5-10 秒表) │
            └───────────────────────────▲─────────────────────────────┘
                                        │
┌──────────────┐   ┌──────────────┐   ┌┴─────────────┐   ┌──────────────┐
│ 支柱一        │   │ 支柱二        │   │ 支柱四        │   │ 支柱五        │
│ 分层采集      │──▶│ 事件-事故-    │──▶│ 诊断即服务    │──▶│ 修复闭环      │
│ T0哨兵/T1 ASH│   │ 问题模型      │   │ (自动RCA管道) │   │ (Playbook+   │
│ /T2全量/T3巡检│   │ (聚合/降噪/   │   │ 创建即诊断    │   │  验证回路)    │
│              │   │  优先级/SLA)  │   │              │   │              │
└──────┬───────┘   └──────▲───────┘   └──────────────┘   └──────────────┘
       │                  │
       └──────────────────┘
         支柱三: 三层检测 (硬阈值 L1 / 自适应基线 L2 / 复合规则+ML L3)
         —— 采集处内联流式评估, 不等下一轮
```

**设计哲学（借 EM13c 三个核心思想）**：
1. **ASH 思想**：诊断的原料是"时间线上的活动会话与等待"，不是孤立快照。没有会话采样历史，
   任何 RCA 都是猜。
2. **Incident 思想**：告警是机器视角（Event），运维处理的对象是业务视角（Incident）。
   两层分离才能既不漏报又不淹死人。
3. **Corrective Action 思想**：检测规则应当直接绑定纠正动作，"发现即带方案、确认即可执行、
   执行即有验证"。

---

## 第三部分：支柱详细设计

### 支柱一：分层采集体系（对标 EM13c 分频采集 + ASH）

| 层 | 频率 | 内容 | 载体 | 新增/复用 |
|----|------|------|------|----------|
| **T0 哨兵** | 5~10s | TCP 连通 + `SELECT 1` + 3~5 个黄金状态量（threads_running、blocked、active sessions） | 独立轻量进程 `sentinel_daemon`，每实例一个长连接，断线即报 | **新增** |
| **T1 ASH-lite** | 15s | processlist / innodb_trx / lock_waits / 等待状态采样 → TimescaleDB `session_sample` 超表 | 哨兵进程顺带采，或独立采样线程 | **新增** |
| **T2 全量指标** | 60s | 现有 6 库 Checker 全量指标 | start_monitor（瘦身：只采集+落库+发事件） | 复用改造 |
| **T3 深度巡检** | 日/周/月 | 82 项巡检 | inspection_scheduler | 复用 |

关键改造点：
- **采集环瘦身**：慢查询分析（D5）、配置检查（D6）从每轮采集中移出，改为独立低频任务
  （15min/1h），采集轮回归"60 秒内必然完成"。
- **管道解耦**：采集线程只负责「采集 → 落库 → 向 Redis Stream 发 Event」；检测与诊断由
  独立 worker 消费流，互不阻塞。当前"采集+基线+RCA+健康+容量全部串行在一轮里"的模式废除。
- **哨兵防误报**：广域网链路（TDSQL 实例实测 SYN 丢包）要求连续 3 次失败 + 备用路径
  （复用主采集连接状态）双确认才判 DOWN，避免链路抖动误报 P1。
- **ASH-lite 一石三鸟**：(a) 锁风暴/长事务的秒级检测数据源；(b) RCA 时间线回放的原料；
  (c) TDSQL 集中式实例 performance_schema digest 为空时的 Top SQL 降级来源
  （按采样频次统计 —— 顺带解决遗留问题）。

### 支柱二：事件-事故-问题模型（对标 EM13c Event→Incident→Problem）

**新增数据模型**：

```
Event(事件)                     ← 机器视角，高频，允许风暴
  event_id / config / source(sentinel|stream|baseline|ml|inspection)
  metric_key / value / threshold / severity / dedup_key / created_at

Incident(事故)                  ← 运维视角，聚合后的处理单元，一等公民
  incident_id / config / category(8类, 见第四部分)
  priority(P1~P4)              ← 影响面 × 紧急度矩阵自动评定
  status: open → diagnosing → plan_ready → executing → verifying → resolved → closed
  detected_at / plan_ready_at / resolved_at   ← 1-5-10 三段秒表的原始数据
  rca_result(JSON) / impact(JSON) / plans(JSON)   ← 诊断管道产物落库
  events: 1:N Event / playbook_runs: 1:N PlaybookRun

Problem(问题)                   ← 跨事故共性根因，沉淀知识
  problem_id / signature / incident_count / kb_ref(关联 AlertCase/知识库)
```

**聚合与降噪规则**：
- 聚合键：(实例, 类别, 5min 滑动窗)；因果折叠：R 规则命中的因果链上游事件吸收下游事件
  （如"锁风暴"事故吸收同窗口的"threads_running 基线偏离"事件）。
- 风暴熔断：同实例每分钟 > N 个事件自动合并为一个事故并标记"事件风暴"。
- Flapping 抑制：同 dedup_key 在 10min 内反复 fire/resolve ≥3 次 → 合并为单事故并提高优先级。
- 维护窗口：现有 AlertSilenceWindow 升级为 Blackout（对象级 + 计划时段 + 需审批 + 到期自动恢复）。
- 兼容性：AlertLog 保留（通知历史/旧页面兼容），Incident 成为新的处理入口。

### 支柱三：三层检测（对标 EM13c 静态阈值 + 自适应阈值）

| 层 | 判定方式 | 数据源 | 延迟预算 | 产出 |
|----|---------|--------|---------|------|
| **L1 硬阈值** | 无须基线的确定性规则：DOWN、blocked_sessions>0 持续 30s、conn≥95%、space≥95%、复制中断 | T0/T1 | **≤15~30s** | P1/P2 Event |
| **L2 自适应基线** | 现有 168 槽三重判定 + **成熟度门槛**（槽样本<14 天回退保守静态阈值，杜绝冷启动漏报） | T2 | ≤90s | P2/P3 Event |
| **L3 复合规则 + ML** | 多指标联合判定（如 threads_running↑ + lock_waits>0 + qps↓ ⇒ 锁风暴，直接高置信）; ml_anomaly_detection 接入为检测器 | T1+T2 | ≤60s | 高置信 Event，可直升 Incident |

关键原则：**检测内联化**——T0/T1 数据到达即评估（流式），T2 在采集落库时同步评估，
彻底废除"等下一轮分析批处理"的节奏。

### 支柱四：诊断即服务（对标 ADDM / Real-Time ADDM）

**事故创建即自动触发诊断管道**（异步 worker，端到端预算 ≤60s）：

```
Incident 创建
  → ContextAggregator 增强版
      （新增: ASH-lite 时间线切片、事故前后 30min 会话/等待画像、变更关联）
  → RCA v2 规则评估 + case_rag 相似案例检索（并行）
  → 影响量化（对标 ADDM impact%）:
      受影响会话数(ASH 采样计数) × 健康分衰减 × 关联业务系统等级
  → RemediationPlanner 生成 2~3 套方案（保守/标准/激进, 含分步 SQL 与风险级）
  → 全部落库 Incident.rca_result / impact / plans
  → 通知升级: 推送内容 = 发生了什么 + 为什么(置信度) + 影响谁 + 怎么修 + 作战室直达链接
```

- **"5 分钟给方案"由此变为"事故创建后 ≤60s 方案已就位并随通知送达"**，5 分钟预算
  留给人工确认与复杂场景的深度诊断。
- **Real-Time 诊断模式**（对标 EM13c Emergency Monitoring）：实例 hang / 主采集失败时，
  用独立诊断连接（超短超时、绕过连接池）抓 processlist + 锁链，保证"库快死时反而看得最清"。
- **证据链**：每条 RCA 结论挂证据（指标曲线切片、会话清单、锁等待链、SQL 文本），
  作战室可回放，杜绝"黑盒结论"。
- 诊断结果缓存与复用：同事故复看不重算；相似事故引用历史诊断加速。

### 支柱五：修复闭环（对标 EM13c Corrective Actions）

**统一 Playbook 体系**（收编三套半成品：PLAN_TEMPLATES / FIX_RULES / auto_remediation_engine）：

```
Playbook
  playbook_id / category / risk_level / applicable_db_types
  precheck[]   前置检查（不满足即拒绝执行, 如: 确认 blocker 会话仍存在）
  steps[]      执行步骤（每步: SQL/动作 + 超时 + 失败策略）
  verify       验证判据（指标 + 恢复阈值 + 观察窗口, 如: blocked_sessions=0 持续 3min）
  rollback[]   回滚步骤
PlaybookRun    执行实例（关联 Incident, 每步执行记录 + 验证结果 + 操作者/审批者）
```

**执行策略（分级授权）**：

| 风险级 | 策略 | 示例 |
|-------|------|------|
| 低 | 自动执行（可按实例开关） | 收集统计信息、清理 idle 会话 |
| 中 | 一键执行 + 单人快速审批（P1 事故可配置"先执行后补审"） | kill blocker、扩表空间 |
| 高 | 完整多级审批（现有 approval_engine） | 重启实例、主从切换、参数回滚 |

**验证回路（本支柱的灵魂，当前完全缺失）**：
- PlaybookRun 执行完毕 → 进入 `verifying` 状态 → 后台按 verify 判据监视触发指标
  N 分钟（默认 5min）。
- 恢复 ⇒ Incident 自动 resolved，记录 MTTR，通知"已解决 + 处置摘要"。
- 未恢复 ⇒ 自动升级优先级 + 通知升档 + 推荐下一套方案（标准→激进）。
- 全程写 AuditLog；回滚一键触发且同样走验证。

### 支柱六：作战室与 SLA 度量（对标 Incident Manager）

- **事故作战室页**（升级现有 AlertDetail）：
  时间线（事件流+变更+操作动作合并展示）｜根因卡（结论+置信度+证据链）｜影响卡｜
  方案卡（2~3 套，一键执行/审批入口）｜验证状态｜**1-5-10 三段秒表**（实时显示
  检测耗时/方案耗时/解决耗时与达标状态）。
- **SLA 报表**：按事故类别统计 MTTD / MTTA / MTTR 与 1-5-10 达标率，周报自动生成
  （复用 report_engine）。**度量不了就管理不了——秒表数据是本期一切优化的反馈回路。**
- **通知分级触达**：P1 连环通知（钉钉@所有人 + 企微 + 预留电话/短信 webhook 接口 +
  15min 未确认自动升级到下一级联系人）；P2 即时单渠道；P3/P4 每小时汇总。
  新增简版值班表（OnCallSchedule）。

---

## 第四部分：1-5-10 承诺范围 —— 第一批 8 类问题矩阵

> 诚实原则：不是所有数据库问题都能 10 分钟解决。本期对以下 8 类做出 1-5-10 承诺并用
> 故障注入演练验收；范围外问题仍走通用流程（尽力而为）。

| # | 类别 | 检测信号(层) | 检测预算 | Playbook | 自动化等级 |
|---|------|-------------|---------|----------|-----------|
| 1 | 实例宕机/不可连 | 哨兵 3 连失败+双路确认 (L1) | ≤30s | 重启/切换指引 + 影响播报 | 指引(环境相关) |
| 2 | 锁等待风暴/长事务阻塞 | ASH-lite blocked 链 (L1/L3) | ≤30s | 定位 blocker → kill → 验证 | **全自动可达** |
| 3 | 连接数耗尽/连接风暴 | conn≥95% 或 5min 斜率异常 (L1/L3) | ≤30s | 清 idle 会话 + 来源 IP 分析 + 限流建议 | 半自动 |
| 4 | 表空间/磁盘将满 | space≥90/95% + 增速预测 (L1+容量引擎) | ≤90s | 扩容(自动加文件) / 清理指引 | 半自动 |
| 5 | 复制中断/严重延迟 | slave 状态 + lag 阈值 (L1) | ≤60s | 重启复制线程 / 跳过错误指引 / 切换建议 | 半自动 |
| 6 | 慢 SQL 突增/计划劣化 | digest 或 ASH 采样统计 (L3) | ≤3min | kill 元凶 + 索引建议(index_advisor) + SQL 改写建议 | 建议为主 |
| 7 | 配置漂移引发异常 | 参数快照 diff + 变更时间关联 (L2) | ≤90s | 参数回滚方案(带原值) | 一键回滚 |
| 8 | 死锁频发 | deadlock 计数增速 (L2/L3) | ≤90s | 死锁链解析 + 热点对象识别 + 事务改造建议 | 建议为主 |

### TDSQL 专项补强（并入本期）
- groupshard 跨 set 指标聚合：按 set 主节点直连分别采集后平台侧聚合
  （`show routes` 已提供各 set 主节点地址），解决 SHOW GLOBAL STATUS 单 set 路由问题。
- 集中式实例 Top SQL：由 ASH-lite 采样统计兜底（digest 为空的遗留问题）。
- 通用巡检项（I001-I012）将 tdsql 纳入 mysql 协议家族分支处理。

---

## 第五部分：数据模型与接口变更清单（设计态）

**新增模型**：`Event`、`Incident`、`Problem`、`Playbook`、`PlaybookRun`、`OnCallSchedule`
**TimescaleDB 新超表**：`session_sample`（ASH-lite，保留 7 天自动降采样）、`event_stream`
**新增进程**：`sentinel_daemon`（哨兵+ASH 采样）、`pipeline_worker`（检测/诊断/验证消费者）
**消息通道**：Redis Stream（`events` / `diagnosis` / `verify` 三个 stream，消费组隔离）
**新增 API（示意）**：
`/api/v1/incidents/`（列表/详情/确认/关闭）、`/api/v1/incidents/{id}/timeline/`、
`/api/v1/incidents/{id}/execute/{plan_id}/`、`/api/v1/playbooks/`、`/api/v1/sla/report/`
**前端新增/改造**：作战室页（改造 AlertDetail）、事故列表（Incident 视角）、SLA 报表页、
值班表设置页

---

## 第六部分：实施路线图（3 个子阶段，总计 9~12 周）

| 子阶段 | 主题 | 内容 | 工期 | 验收 |
|-------|------|------|------|------|
| **6A** | 1 分钟发现 | 哨兵层 + ASH-lite + Redis Stream 管道解耦 + L1/L3 流式检测 + Event/Incident 模型 + 风暴抑制 | 3~4 周 | 注入宕机/锁风暴/连接风暴，MTTD ≤60s |
| **6B** | 5 分钟方案 | 诊断管道自动化（创建即诊断 ≤60s）+ 通知带根因方案 + 作战室页 + 证据链 | 3~4 周 | 事故通知内含可执行方案；作战室秒表可见 |
| **6C** | 10 分钟解决 | Playbook 统一 + 纠正动作绑定 + 验证回路 + 快速审批通道 + SLA 报表 | 3~4 周 | 8 类故障注入演练，全自动类 MTTR ≤10min |

**依赖顺序**：6A 是地基（没有秒级数据与事故模型，后两段无从谈起）→ 6B → 6C。
每子阶段结束做一次故障注入演练回归（锁风暴 / 杀连接进程 / 写满磁盘 / 断复制，
在本地 Docker 目标库 + TDSQL 真实实例上执行）。

---

## 第七部分：风险与边界

| 风险 | 缓解 |
|------|------|
| 哨兵/ASH 采样对目标库的额外负载 | 只读轻查询；频率可按实例配置；一键降级开关 |
| 互联网链路抖动导致秒级探活误报（TDSQL 实测 SYN 丢包） | 连续 3 次失败 + 主采集连接状态双路确认；链路质量本身作为一个指标呈现 |
| 自动修复的安全边界 | 白名单动作 + precheck 强制 + 全程审计 + 回滚 + 单实例熔断（1 小时内自动动作 ≥3 次即停用自动化转人工） |
| 事件风暴打爆管道 | Redis Stream 削峰 + 消费组水平扩展 + 熔断合并 |
| 基线冷启动误报 | 成熟度门槛：不足 14 天样本回退保守静态阈值 |
| Phase 5 式"布线遗漏"重演 | 每个子阶段验收含端到端演练（本地部署时已发现纯代码验证不可信的教训） |

---

## 附：与 EM13c 概念映射速查

| EM13c 概念 | 本设计对应 |
|-----------|-----------|
| Metric Collection（多频率采集） | 支柱一 T0~T3 分层采集 |
| ASH / ASH Analytics | ASH-lite（session_sample 超表） |
| Event → Incident → Problem | 支柱二 同名三层模型 |
| Adaptive Thresholds | L2 自适应基线 + 成熟度门槛 |
| ADDM（含 impact 量化） | 支柱四 诊断管道 + 影响量化 |
| Real-Time ADDM / Emergency Monitoring | Real-Time 诊断模式（独立诊断连接） |
| Corrective Actions | 支柱五 Playbook 绑定 + 分级授权 |
| Incident Manager | 支柱六 作战室 + SLA 秒表 |
| Blackout | 维护窗口（Blackout 升级版） |
| Notification / Escalation Chains | 分级触达 + 值班表 + 升级链 |
