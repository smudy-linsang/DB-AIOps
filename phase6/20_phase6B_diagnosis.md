# Phase 6B 详细设计 —— 诊断层（5 分钟方案）

> 覆盖支柱四（诊断即服务）、支柱六（作战室页 + 证据链）。
> 交付目标：事故创建 ≤`DIAG_BUDGET_SEC`(60s) 后，通知与作战室已含**根因(带置信度)+2~3套
> 可执行方案+影响评估+证据链**。5 分钟预算留给人工确认与深度场景。
> 前置：6A 完成（Incident 已生成、诊断消费组 cg_diag 骨架已在）。工单 6B-01 ~ 6B-10。

---

## 1. 诊断管道总览

`pipeline_worker` 的 `cg_diag` 消费组消费 `dbaiops:diagnosis` 消息（6A 已投递），
对每个 incident 执行五阶段管道，全部落库 `Incident.rca_result/impact/plans` 并置
`plan_ready_at=now`、状态 `diagnosing→plan_ready`，随后触发通知（§6）。

```
diagnosis 消息(incident_id)
 → 阶段1 上下文聚合(ContextAggregator 增强, 含 ASH 时间线)      预算≤15s
 → 阶段2 RCA 评估(rca_engine_v2) ∥ 案例检索(case_rag)          预算≤15s (并行)
 → 阶段3 影响量化(impact_engine 增强, ADDM 风格)               预算≤10s
 → 阶段4 方案生成(remediation → Playbook 引用)                 预算≤15s
 → 阶段5 落库 + 状态转移 + 触发通知                             预算≤5s
 总预算 ≤60s；超 DIAG_BUDGET_SEC 记 warning 指标但仍落已完成部分
```

管道入口：`monitor/diagnosis_pipeline.py::run_diagnosis(incident_id) -> dict`。
幂等：同 incident 重复诊断（trigger=replan）覆盖旧结果并记版本号 `rca_result.version`。

---

## 2. 阶段1：上下文聚合增强（改造 `context_aggregator.py`）

现有 `ContextAggregator.aggregate()` 已产出：alert_summary / related_metrics(30min) /
related_alerts / cluster_context / business_context / recent_changes / topology /
baseline_comparison。**改造为吃 Incident 而非 AlertLog**，并新增两块：

### 2.1 ASH 时间线切片（新增 `_get_ash_timeline()`）
查 `session_sample` 事故前后窗口（默认 [-30min, now]）：
```sql
SELECT time, session_id, user_name, state, wait_event, active_secs,
       is_blocked, blocker_id, sql_digest, LEFT(sql_text,120) AS sql_text
FROM session_sample
WHERE db_config_id = %s AND time BETWEEN %s AND %s
ORDER BY time;
```
产出结构（写入 context['ash_timeline']）：
```json
{ "window":["...","..."],
  "active_session_trend":[{"t":"...","active":N,"blocked":M}...],   // 按采样点聚合
  "top_wait_events":[{"wait_event":"...","samples":K,"pct":P}...],  // 等待画像
  "blocking_chains":[{"t":"...","blocker":"...","waiters":[...],"max_wait_sec":S}...],
  "top_sql_by_samples":[{"sql_digest":"...","sql_text":"...","samples":K}...] // digest 空时的Top SQL兜底
}
```
`top_sql_by_samples` 解决集中式 TDSQL digest 为空的 Top SQL 遗留问题。

### 2.2 变更关联增强（`_get_recent_changes()`）
- 已有：AuditLog 中 72h 内 DDL/参数变更。
- 新增：`config_drift` 类事件与本事故时间窗的关联（参数快照 diff，见 §4 config Playbook 依赖），
  产出"疑似诱因变更"列表，供 RCA 提高 config 类根因置信度。

### 2.3 契约（context dict 顶层键，固定）
`alert_summary`(改名 `incident_summary`) / `related_metrics` / `related_alerts` /
`cluster_context` / `business_context` / `recent_changes` / `topology` /
`baseline_comparison` / **`ash_timeline`**。

---

## 3. 阶段2：RCA 评估 + 案例检索

### 3.1 RCA v2 接入（复用 `rca_engine_v2.RCAEngineV2.diagnose`）
- 输入：`db_type` + 合并特征字典（最新采集指标 + context.ash_timeline 派生特征 +
  event.detail）。派生特征映射见 `diagnosis_pipeline._build_features(incident, context)`。
- **置信度增强**：现有 `_compute_confidence`；本期叠加：
  - 若事件带 `detail.composite=true`（L3 复合命中），命中同域规则置信 ×1.2（上限1.0）。
  - 若 context.recent_changes 有时间吻合的变更且规则 domain=config，置信 +0.15。
  - 若 case_rag 命中相似历史案例且结论一致，置信 +0.1。
- 输出 TopN(`DIAG_RCA_TOPN`=3) 候选根因，每条含：rule_id / name / domain / confidence /
  causal_chain / evidence(见 §3.3)。

### 3.2 案例检索（复用 `case_rag.search_cases`，与 RCA 并行线程）
- 输入：症状签名 `SymptomSignature`（db_type + category + 关键指标离散化）。
- 输出 top-5 相似历史 AlertCase：case_id / 相似度 / 历史根因 / 历史处置 / 是否成功。
- 写入 `rca_result.similar_cases`。相似度≥0.7 的成功案例的处置动作会喂给阶段4 作为方案候选。

### 3.3 证据链（每条 RCA 结论必挂，杜绝黑盒）
`rca_result.root_causes[i].evidence` = 结构化证据数组，每项：
```json
{ "type":"metric|session|lock|sql|change|baseline",
  "label":"threads_running 突增至 120（基线 30）",
  "data":{ "series":[...] | "sessions":[...] | "chains":[...] | "sql":"..." } }
```
证据 `data` 直接取自 context（指标切片/ASH 会话/阻塞链/SQL），前端作战室可回放。

### 3.4 rca_result 契约（落库 Incident.rca_result）
```json
{ "version":1, "engine":"rca_v2",
  "root_causes":[
    {"rule_id":"R021","name":"锁等待激增","domain":"lock","confidence":0.92,
     "causal_chain":[{"rule_id":"R021","factor":"..."}],
     "summary":"会话 1976 持有行锁未提交，阻塞 3 个会话",
     "evidence":[ {...}, {...} ]}
  ],
  "similar_cases":[{"case_id":"...","similarity":0.81,"root_cause":"...","fix":"...","success":true}],
  "diagnosed_at":"...", "budget_ms":48210 }
```

---

## 4. 阶段3：影响量化（改造 `impact_engine.py`，ADDM 风格）

现有 `BusinessImpactAssessor.assess()` 输出健康度衰减 + 业务影响。增强为量化三维：

| 维度 | 计算 | 数据源 |
|------|------|--------|
| 受影响会话数 | ASH 窗口内 blocked/异常会话峰值计数 | context.ash_timeline.blocking_chains |
| 健康度衰减 | 事故前 health_snapshot − 当前 health_engine 重算 | health_engine |
| 业务影响 | config→business_systems 反查，列每个系统 name/importance/owner/contact | BusinessSystem |

`impact` 契约（落库 Incident.impact）：
```json
{ "affected_sessions": 3, "affected_sessions_peak": 5,
  "health_before": 86.7, "health_now": 61.0, "health_drop": 25.7,
  "business_systems":[{"name":"核心交易","importance":"critical","owner":"张三","contact":"..."}],
  "impact_level":"high",   // high/medium/low, 由三维加权(见下)
  "summary":"影响核心交易系统，3 个会话被阻塞，健康度下降 25.7" }
```
`impact_level` = 加权：business 最高重要度(高=3/中=2/低=1) × 0.5 + 归一化会话数 × 0.3 +
归一化健康衰减 × 0.2，分档 high(≥2.3)/medium(≥1.5)/low。此值可反过来校正 Incident.priority
（若 impact_level=high 而 priority≥P3，升级为 P2 并记原因）。

---

## 5. 阶段4：方案生成（改造 `remediation_planner.py`，产出引用 Playbook）

现有 `RemediationPlanner.generate()` 产 PlanScenario(保守/标准/激进)。本期改造：

- **方案锚定 Playbook**：每套方案的 steps 不再是自由文本 SQL，而是**引用 6C 的 Playbook**
  （`playbook_id`）+ 参数绑定。这样"生成方案"与"执行方案"共用同一动作定义，杜绝 Phase 5 式
  三套动作各写一份的问题。6B 阶段 Playbook 表可能未全建，方案里先带 `playbook_ref`（可空）
  + 明文 steps 兜底；6C 建全 Playbook 后回填 ref。
- **方案来源三路合并**：RCA 规则自带的 suggested_actions ∪ case_rag 成功案例处置 ∪
  PLAN_TEMPLATES 模板，去重后按风险分档为保守/标准/激进。
- 每套方案含：`name` / `risk_level`(low/mid/high) / `steps`[{action,sql_or_ref,expected}] /
  `rollback` / `est_minutes` / `requires_approval`(bool) / `verify`(指标+恢复判据，供 6C 验证回路)。

`plans` 契约（落库 Incident.plans，数组）：
```json
[
 {"scenario":"standard","name":"kill 阻塞源会话","risk_level":"mid",
  "playbook_ref":"PB-LOCK-KILL-BLOCKER",
  "steps":[
    {"seq":1,"action":"precheck","desc":"确认 blocker 会话仍存在","sql":"...","expected":"1 row"},
    {"seq":2,"action":"execute","desc":"kill blocker","sql":"KILL <blocker_id>","expected":"OK"}],
  "rollback":[], "verify":{"metric":"blocked_sessions","recover_expr":"== 0","window_sec":300},
  "est_minutes":2, "requires_approval":true},
 {"scenario":"conservative", ...}, {"scenario":"aggressive", ...}
]
```
kill 目标 `<blocker_id>` 由 context.ash_timeline.blocking_chains 参数化注入。

---

## 6. 阶段5：落库 + 通知升级

- 落库：`incident.rca_result/impact/plans` 三字段 + `plan_ready_at=now` +
  `transition(diagnosing→plan_ready)`。
- **通知升级（关键——把方案送到人手上）**：改造 `alert_manager` 通知内容，从"标题+描述"升级为
  结构化卡片，含五要素 + 作战室直达链接：
```
【P1｜锁等待阻塞】TDSQL分布式测试库
发生了什么: 3 个会话被阻塞，最长等待 45s
为什么(置信92%): 会话1976 持行锁未提交 [R021 锁等待激增]
影响谁: 核心交易系统(张三) | 健康度 86.7→61.0
怎么修: [标准]kill 阻塞源(2min,需确认) / [保守]告警观察 / [激进]...
处理: <作战室链接 /incidents/INC-.../>
```
- 通道复用现有 notifications.py（邮件/钉钉/企微）；P1 触发连环通知与升级（6C 值班表接入前，
  6B 先支持"钉钉@所有人 + 15min 未 ack 复发提醒"）。
- 通知发送时机 = plan_ready（不是 open），确保 DBA 第一眼就看到方案。若诊断超预算未完成，
  先发"已发现+诊断中"占位通知，诊断完成再发方案通知。

---

## 7. 支柱六：作战室页（完整版，改造 `IncidentDetail.jsx`）

单页五区（数据全部来自 §8 的 detail 接口，字段名严格一致）：

| 区 | 内容 | 数据源字段 |
|----|------|-----------|
| 头部 | 标题/优先级/状态/**1-5-10 三段秒表**(达标绿超标红)/确认按钮 | incident + t_*_sec + sla_*_ok |
| 时间线 | Event+状态转移+动作合并，可展开证据 | timeline 接口 |
| 根因卡 | TopN 根因 + 置信度进度条 + 因果链 + **证据回放**(指标小图/会话表/锁链) | rca_result.root_causes |
| 影响卡 | 受影响会话数/健康衰减环形/业务系统清单 | impact |
| 方案卡 | 2~3 套 Tab(保守/标准/激进)，每套 steps + 风险标 + **执行/审批按钮**(6C 接) | plans |

- 秒表组件：3 个 Statistic，值=t_detect_sec/t_plan_sec/t_resolve_sec，颜色由 sla_*_ok。
- 证据回放：指标用 MetricsChart(现有组件)喂 evidence.data.series；会话/锁链用 Table。
- 方案执行按钮 6B 先渲染（disabled+"待 6C"），6C 接 `/execute` 接口。

---

## 8. API 契约（6B 交付）

### 8.1 `GET /api/v1/incidents/<incident_id>/`（6A 已有，6B 填满 rca/impact/plans）
Resp 在 6A 基础上，`rca_result`(§3.4) / `impact`(§4) / `plans`(§5) 变为实际内容；
新增 `plan_ready_at` / `t_plan_sec` / `sla_plan_ok`。

### 8.2 `POST /api/v1/incidents/<incident_id>/rediagnose/`  手动重诊断
Body `{}`。触发 `emit_diagnosis(incident_id, trigger='replan')`。Resp `{"code":"OK"}`。

### 8.3 诊断超时/失败可观测
诊断管道写指标 `diag_budget_ms`、`diag_status`(ok/timeout/error) 到 metric_point，
供 SLA 报表(6C)统计。

---

## 9. 文件清单（6B）

| 文件 | 操作 | 工单 |
|------|------|------|
| `monitor/diagnosis_pipeline.py` | 新建(五阶段编排) | 6B-01 |
| `monitor/context_aggregator.py` | 增强(吃 Incident + ash_timeline) | 6B-02 |
| `monitor/rca_engine_v2.py` | 置信度增强 + 证据链输出 | 6B-03 |
| `monitor/case_rag.py` | 接入管道(并行检索) | 6B-04 |
| `monitor/impact_engine.py` | ADDM 风格量化 | 6B-05 |
| `monitor/remediation_planner.py` | 产出引用 Playbook 的 plans | 6B-06 |
| `monitor/pipeline.py` | cg_diag 线程接 run_diagnosis | 6B-07 |
| `monitor/alert_manager.py` / `notifications.py` | 结构化五要素通知 | 6B-08 |
| `monitor/api_views_incident.py` + urls | 填 rca/impact/plans + rediagnose | 6B-09 |
| `frontend/src/pages/IncidentDetail.jsx` + 组件 | 作战室五区 | 6B-09 |
| `phase6/drills/*`, `verify_phase6b.py` | 演练+验收 | 6B-10 |

---

## 10. 施工工单（带验收标准）

| 工单 | 标题 | 验收标准 |
|------|------|---------|
| **6B-01** | 诊断管道编排 | 给定 incident_id，run_diagnosis 产出 rca/impact/plans 三字段且落库；超预算记指标不崩 |
| **6B-02** | 上下文+ASH时间线 | context.ash_timeline 含 blocking_chains 与 top_sql_by_samples（对集中式TDSQL非空）|
| **6B-03** | RCA 置信度+证据链 | 锁事故 root_causes[0].rule_id=R021 且 evidence 含 lock 链；composite 命中置信×1.2 |
| **6B-04** | 案例检索并行 | similar_cases 返回 top-5；与 RCA 并行不串行(耗时≈max非sum) |
| **6B-05** | 影响量化 | impact 三维齐全；impact_level=high 时 priority 自动校正并记原因 |
| **6B-06** | 方案生成 | plans 含 2~3 套，每套 steps/rollback/verify 齐；kill 目标参数化正确 |
| **6B-07** | pipeline cg_diag | diagnosis 消息 →≤60s 事故进 plan_ready；XACK 无堆积 |
| **6B-08** | 结构化通知 | 通知含五要素+作战室链接；plan_ready 时发送；P1 未ack 15min 复提醒 |
| **6B-09** | 作战室 API+前端 | 契约测试字段名一致；页面五区渲染真实诊断；秒表达标色正确 |
| **6B-10** | 演练+验收 | 见 §11 |

---

## 11. 6B 验收演练

复用 6A 的 `inject_lock.py`：注入锁阻塞 → 事故生成后计时，断言：
- ≤60s(DIAG_BUDGET_SEC) 内事故进入 `plan_ready`，`t_plan_sec` 有值。
- rca_result.root_causes[0] 命中锁类规则且带证据链（lock 链非空）。
- plans 至少 2 套，标准方案 = kill blocker 且目标 id 与注入的 blocker 一致。
- impact.business_systems 正确列出（需先给测试实例挂一个 BusinessSystem）。
- 通知内容含五要素（检查发送 payload）。
- 作战室页秒表显示 T_detect/T_plan 且达标标色。

`verify_phase6b.py`：import 校验 + 造锁事故走完管道 + 断言上述。输出 `通过 N/N`。
**总判定**：事故创建到方案就位 ≤60s，作战室五区数据真实，契约四方一致。达成即进 6C。
