# Phase 6C 详细设计 —— 处置层（10 分钟解决）

> 覆盖支柱五（修复闭环：Playbook + 验证回路 + 分级授权）、支柱六（SLA 报表 + 分级触达/值班）。
> 交付目标：8 类承诺问题中，全自动类（锁阻塞）**MTTR ≤ 600s** 且验证回路自动关闭事故；
> 半自动/建议类给出一键执行 + 验证。
> 前置：6A（事故/状态机/verify 消费组骨架）、6B（plans 已引用 Playbook）。工单 6C-01 ~ 6C-11。

---

## 1. 数据模型（契约源）

### 1.1 Playbook（处置剧本）表名 `monitor_playbook`

| 字段 | 类型 | 约束/默认 | 说明 |
|------|------|----------|------|
| id | bigint pk | auto | |
| playbook_id | str(48) | unique, index | `PB-<CATEGORY>-<ACTION>`，如 `PB-LOCK-KILL-BLOCKER` |
| name | str(200) | | |
| category | enum(同 Incident.category) | index | |
| signal | str(40) | index | 主要适用 signal |
| applicable_db_types | json(list) | default=list | ['mysql','tdsql',...] |
| risk_level | enum(low,mid,high) | | 决定授权策略(§3) |
| precheck | json(list) | default=list | 前置检查步骤(见 §2.1) |
| steps | json(list) | default=list | 执行步骤 |
| verify | json(dict) | default=dict | 验证判据(见 §4) |
| rollback | json(list) | default=list | 回滚步骤 |
| params_schema | json(dict) | default=dict | 参数占位定义(如 blocker_id) |
| est_minutes | int | default=5 | 预计耗时 |
| enabled | bool | default=True | |
| auto_execute | bool | default=False | 是否允许自动执行(仅 low 有意义) |
| created_at/updated_at | datetime | auto | |

### 1.2 PlaybookRun（执行实例）表名 `monitor_playbook_run`

| 字段 | 类型 | 约束/默认 | 说明 |
|------|------|----------|------|
| id | bigint pk | auto | |
| run_id | str(48) | unique, index | `PBR-<YYYYMMDDHHMMSS>-<incident序>` |
| playbook | fk→Playbook | on_delete=PROTECT, related_name='runs' | |
| incident | fk→Incident | on_delete=CASCADE, related_name='playbook_runs' | |
| params | json(dict) | default=dict | 实际参数(blocker_id=1976 等) |
| status | enum(pending_approval,prechecking,executing,verifying,succeeded,failed,rolled_back,timeout) | index, default='pending_approval' | 见 §5 |
| trigger_mode | enum(auto,one_click,approved) | | 触发方式 |
| approved_by | str(50) | default='' | |
| step_results | json(list) | default=list | 每步结果(见 §2.2) |
| verify_result | json(dict) | default=dict | 验证结果 |
| error_message | text | default='' | |
| started_at/finished_at | datetime | null=True | |
| created_at | datetime | auto_now_add | |

### 1.3 OnCallSchedule（值班表）表名 `monitor_oncall_schedule`

| 字段 | 类型 | 约束/默认 | 说明 |
|------|------|----------|------|
| id | bigint pk | auto | |
| name | str(100) | | 班次名 |
| user | str(50) | | 值班人(用户名) |
| contact_dingtalk / contact_wecom / contact_phone | str(120) | default='' | 触达方式 |
| weekday_mask | int | default=127 | 位掩码周一~周日(bit0=周一) |
| start_hour / end_hour | int | default 0/24 | 值班时段 |
| escalate_to | str(50) | default='' | 升级联系人(下一级值班) |
| enabled | bool | default=True | |

---

## 2. Playbook 结构（steps/precheck/step_results 契约）

### 2.1 precheck / steps 元素结构
```json
{ "seq":1, "action":"query|execute|wait|http",
  "desc":"确认 blocker 仍存在",
  "sql":"SELECT 1 FROM information_schema.processlist WHERE id={blocker_id}",
  "sql_by_db":{"oracle":"...","pgsql":"..."},   // 可选, 覆盖 sql
  "expect":"rows>=1 | ok | affected>=1",         // 判定式
  "on_fail":"abort|continue|rollback",           // 失败策略
  "timeout_sec":10 }
```
- `{blocker_id}` 等占位由 PlaybookRun.params 渲染（params_schema 声明必填项）。
- `sql` 优先取 `sql_by_db[db_type]`，无则用 `sql`。
- `precheck` 全过才进 `steps`；precheck 不过 → run 直接 failed(code=PRECHECK_FAILED)，
  不执行任何写操作（安全铁律）。

### 2.2 step_results 元素（执行留痕）
```json
{ "seq":1, "action":"execute", "started_at":"...", "finished_at":"...",
  "status":"ok|fail", "rows_affected":1, "output":"...", "error":"" }
```

---

## 3. 分级授权（risk_level → 执行策略）

| risk_level | 触发策略 | 实现 |
|-----------|---------|------|
| low | `PLAYBOOK_AUTO_LOW_RISK=True` 且 playbook.auto_execute=True → 自动执行(trigger=auto)；否则一键 | 收集统计、清 idle 会话 |
| mid | 一键执行 + 单人快速审批；**P1 事故可配"先执行后补审"**(execute_first_then_approve) | kill blocker、扩表空间 |
| high | 完整多级审批(复用 approval_engine 现有多级流) | 重启实例、主从切换、参数回滚 |

- **P1 紧急通道**：Incident.priority=P1 且 playbook.risk_level=mid 时，允许
  trigger_mode=one_click 立即执行并**异步补审批记录**（approval_engine 建"事后审批"工单）。
  由配置 `INCIDENT_P1_EXECUTE_FIRST`(默认 True) 控制。
- **熔断**：某实例 1 小时内 auto/one_click 执行次数 ≥ `PLAYBOOK_AUTO_CIRCUIT_BREAK`(3)，
  自动停用该实例的自动化（转人工），发通知。计数用 Redis `INCR`+`EXPIRE 3600`。

---

## 4. 验证回路（本期灵魂，闭合 10 分钟）

### 4.1 verify 判据结构（Playbook.verify / plans[i].verify）
```json
{ "metric":"blocked_sessions", "recover_expr":"== 0",
  "window_sec":300, "check_interval_sec":15, "min_stable_checks":3,
  "data_source":"ash|golden|collector" }
```
- `recover_expr`：`== 0` / `< N` / `<= N` / `>= N`，对 metric 当前值求值。
- 判定：连续 `min_stable_checks`(3) 次采样满足 recover_expr 即"恢复"；窗口 `window_sec` 内
  未恢复即"超时"。
- metric 取值：`data_source=ash` 时查 session_sample 派生（如 blocked_sessions=当前
  is_blocked 计数）；`golden` 取哨兵黄金量；`collector` 取最近 metric_point。

### 4.2 验证流程（pipeline_worker 的 cg_verify 线程，6A 已建骨架）
```
消费 dbaiops:verify 消息(incident_id, playbook_run_id, verify 判据)
 → 进入监视循环(最长 window_sec):
     每 check_interval_sec 取 metric 当前值，评估 recover_expr
     连续 min_stable_checks 满足 → 恢复
 → 恢复:
     PlaybookRun.status=succeeded, verify_result={recovered:true, at:..., checks:[...]}
     Incident.transition(verifying→resolved), resolved_at=now
     发"已解决"通知(含 MTTR 与处置摘要)
     提升为 Problem 知识(promote_to_case, 复用 auto_fix_loop._promote_to_case 思路)
 → 超时未恢复:
     PlaybookRun.status=timeout
     Incident.transition(verifying→plan_ready), priority 升一级
     发"处置未生效, 建议下一方案"通知(推荐 plans 中更激进档)
```
- 验证的"发 verify 消息"由执行成功后触发（§5 步骤末 `emit_verify`）。
- 6A 已把 cg_verify 线程建好并 XACK，本工单填入上述逻辑。

---

## 5. PlaybookRun 执行引擎（`monitor/playbook_engine.py`）

`execute_run(run_id)` 状态机：
```
pending_approval ─(授权通过/自动)─▶ prechecking
prechecking ─(precheck全过)─▶ executing ; ─(precheck失败)─▶ failed
executing ─(steps全成功)─▶ verifying(emit_verify) ; ─(某步on_fail=abort失败)─▶ failed
executing ─(某步失败 on_fail=rollback)─▶ 执行 rollback ─▶ rolled_back
verifying ─(cg_verify回写)─▶ succeeded | timeout
```
- 每步执行：用 `DbConnector.get_connection(config)`（TDSQL 用 TDSQLChecker 连接），
  渲染 SQL，超时控制，写 step_results。**写操作全程 AuditLog**（复用现有）。
- 执行入口来源：作战室"执行"按钮(one_click) / 审批通过(approved) / 低风险自动(auto)。
- 幂等：同 run 重复 execute 拒绝（status 非 pending/prechecking 返回 CONFLICT）。
- 连接管理：finally `connection.close()`（Django ORM）+ 目标库连接显式关闭（吸取教训）。

---

## 6. 首批 Playbook 内容（8 类，随本期交付，写入 `init_playbooks.py`）

> 每类给出核心 Playbook（保守/标准/激进按需拆多条或用 risk_level 区分）。以下列关键 SQL 骨架。

### 6.1 PB-LOCK-KILL-BLOCKER（锁阻塞，mid，**全自动候选**）
```
precheck: SELECT ... WHERE id={blocker_id} 存在 (mysql) / v$session blocker 存在 (oracle)
steps:    execute KILL {blocker_id}        (mysql/tdsql)
          ALTER SYSTEM KILL SESSION '{sid},{serial}' (oracle)
          SELECT pg_terminate_backend({blocker_id}) (pg)
verify:   metric=blocked_sessions recover_expr="== 0" window=300 source=ash
rollback: [] (kill 不可回滚, 故 precheck 必须严格)
params:   blocker_id (来自 incident.rca_result 证据链/ash blocking_chains)
```

### 6.2 PB-CONN-CLEAN-IDLE（连接耗尽，mid）
```
precheck: 空闲会话数 > 阈值
steps:    批量 kill idle>Ns 的会话 (mysql: processlist command=Sleep time>N;
          pg: pg_terminate_backend WHERE state='idle' AND state_change<now()-Ns;
          oracle: ALTER SYSTEM KILL SESSION for INACTIVE>N)
verify:   metric=conn_usage_pct recover_expr="< 80" window=180
```

### 6.3 PB-SPACE-EXTEND（表空间将满，mid，半自动）
```
precheck: 表空间 used_pct>=90 且 可加文件(有剩余磁盘)
steps:    oracle: ALTER TABLESPACE {ts} ADD DATAFILE ... SIZE {mb}M AUTOEXTEND ON;
          mysql: (innodb 自动扩展, 多为清理; 或加 undo/临时空间)
          pg: 提示扩卷(建议为主)
verify:   metric=tablespace_used_pct recover_expr="< 90" window=120
rollback: 记录(扩容一般不回滚)
```

### 6.4 PB-REPL-RESTART（复制中断，mid，半自动）
```
precheck: slave 状态确为停止且无正在进行的手工维护
steps:    mysql: STOP SLAVE; START SLAVE; (或 skip 一个错误后 start)
          pg: 重启 wal receiver 指引; oracle DG: 重启 apply
verify:   metric=repl_running recover_expr="== 1" window=180
```

### 6.5 PB-CONFIG-ROLLBACK（配置漂移，mid，一键回滚）
```
precheck: 存在参数快照 diff 且有原值
steps:    对每个漂移参数 SET GLOBAL {k}={old_v} (mysql) / ALTER SYSTEM (oracle) 等
verify:   metric=触发指标恢复 window=300
rollback: 再设回当前值
params:   drift_params[] (来自 config_advisor 快照 diff)
```

### 6.6~6.8 慢SQL/死锁/宕机（建议为主，Playbook 以"取证+建议"为主，risk=low/high）
- PB-SLOW-KILL-TOPSQL(mid)：kill 元凶会话 + 附 index_advisor 建议(建议不自动执行 DDL)。
- PB-DEADLOCK-ADVISE(low)：解析死锁链 + 热点对象 + 事务改造建议(纯建议)。
- PB-DOWN-GUIDE(high)：重启/切换指引 + 影响播报(环境相关, 不自动执行, 走完整审批)。

---

## 7. 分级触达与值班（支柱六）

- **触达升级链**：P1 事故 plan_ready 时，按 OnCallSchedule 当前值班人触达（钉钉+企微+
  预留电话 webhook）；`ONCALL_ESCALATE_MIN`(15) 内未 ack → 触达 escalate_to；再未 ack →
  提优先级并全员通知。
- **通知节流**：P2 即时单渠道；P3/P4 每小时汇总（复用 alert_manager 聚合缓冲）。
- 值班判定：`get_current_oncall()` 按 now 的 weekday_mask + 时段匹配 enabled 记录。

---

## 8. SLA 报表（支柱六，`monitor/sla_report.py` + 报表页）

- 统计源：Incident 表的时间戳字段 + 8 类承诺范围过滤。
- 指标：按 category/priority 分组的 MTTD(t_detect_sec)、MTTA(acked_at−detected_at)、
  MTTR(t_resolve_sec) 的均值/P50/P90 + **1-5-10 达标率**(sla_detect_ok/plan_ok/resolve_ok 占比)。
- 输出：`GET /api/v1/sla/report/?from=&to=&category=` →
```json
{ "code":"OK", "range":["...","..."],
  "overall":{"mttd_sec":22,"mtta_sec":40,"mttr_sec":410,
             "detect_ok_rate":0.95,"plan_ok_rate":0.9,"resolve_ok_rate":0.83},
  "by_category":[{"category":"lock","count":12,"mttr_sec":180,"resolve_ok_rate":1.0}...] }
```
- 周报自动生成复用 `report_engine`（加"SLA 章节"）。
- 前端 `SlaReport.jsx`（菜单"SLA 报表"）：三张卡(MTTD/MTTA/MTTR) + 达标率仪表 + 分类表。

---

## 9. API 契约（6C 交付）

| 方法 路径 | 用途 | 关键字段 |
|----------|------|---------|
| `GET /api/v1/playbooks/` | 剧本列表 | playbook_id/name/category/risk_level/auto_execute |
| `POST /api/v1/incidents/<id>/execute/` | 执行某方案 | body `{"scenario":"standard"}` 或 `{"playbook_id":"PB-...","params":{...}}` → 建 PlaybookRun 并 execute_run(异步) |
| `GET /api/v1/playbook-runs/<run_id>/` | 执行详情 | status/step_results/verify_result |
| `POST /api/v1/playbook-runs/<run_id>/approve/` | 审批通过 | 走 approval_engine, 通过后触发执行 |
| `POST /api/v1/playbook-runs/<run_id>/rollback/` | 手动回滚 | 执行 rollback steps |
| `GET /api/v1/sla/report/` | SLA 报表 | 见 §8 |
| `GET/POST /api/v1/oncall/` | 值班表 CRUD | OnCallSchedule 字段 |

`execute` 接口鉴权：需 config 写权限(RBAC) + risk_level 对应审批级；P1 紧急通道按 §3。
非法状态转移一律 409/CONFLICT；precheck 失败 422/PRECHECK_FAILED。

---

## 10. 文件清单（6C）

| 文件 | 操作 | 工单 |
|------|------|------|
| `monitor/models.py` + 迁移 | Playbook/PlaybookRun/OnCallSchedule | 6C-01 |
| `monitor/playbook_engine.py` | 执行引擎(状态机+步骤+回滚) | 6C-02 |
| `monitor/pipeline.py` (cg_verify) | 验证回路填充 | 6C-03 |
| `monitor/management/commands/init_playbooks.py` | 8 类首批 Playbook | 6C-04 |
| `monitor/approval_engine.py` | P1 事后审批/紧急通道 | 6C-05 |
| `monitor/oncall.py` | 值班判定+升级链 | 6C-06 |
| `monitor/notifications.py` | 电话/短信 webhook 预留 + 升级触达 | 6C-06 |
| `monitor/sla_report.py` + `report_engine.py` | SLA 统计+周报章节 | 6C-07 |
| `monitor/api_views_incident.py` + urls | execute/approve/rollback/playbooks/sla/oncall | 6C-08 |
| `frontend/src/pages/IncidentDetail.jsx` | 方案卡执行/审批/回滚接线 + 验证状态 | 6C-08 |
| `frontend/src/pages/SlaReport.jsx`, `OnCallSettings.jsx` | 新建 | 6C-09 |
| `phase6/drills/*` + `verify_phase6c.py` | 演练+验收 | 6C-10 |
| 端到端联调(6A+6B+6C) | 全链路演练 | 6C-11 |

---

## 11. 施工工单（带验收标准）

| 工单 | 标题 | 验收标准 |
|------|------|---------|
| **6C-01** | Playbook/Run/OnCall 模型 | migrate 成功；字段表 vs models 一致 |
| **6C-02** | 执行引擎 | precheck 失败零写操作；步骤留痕；rollback 可触发；连接不泄漏 |
| **6C-03** | 验证回路 | 恢复→事故自动 resolved 记 MTTR；超时→回退 plan_ready 升级 |
| **6C-04** | 首批 Playbook | 8 类可加载；PB-LOCK-KILL-BLOCKER 参数化正确 |
| **6C-05** | 分级授权+P1通道 | low 自动/mid 一键+审批/high 多级；P1 先执行后补审；熔断生效 |
| **6C-06** | 值班+升级触达 | 当前值班人正确；15min 未 ack 升级；渠道预留可配 |
| **6C-07** | SLA 报表 | 报表数据与 Incident 时间戳一致；1-5-10 达标率正确 |
| **6C-08** | 执行 API+作战室接线 | 作战室点"执行"→建 Run→执行→验证状态回显；契约一致 |
| **6C-09** | SLA/值班前端 | 页面渲染真实数据 |
| **6C-10** | 单元+故障演练 | 见 §12 |
| **6C-11** | 全链路端到端 | 见 §12 总演练 |

---

## 12. 6C 验收演练（全链路，1-5-10 终验）

`phase6/drills/e2e_lock.py`（锁阻塞全自动闭环，终极验收）：
1. 对测试实例注入行锁阻塞（另会话抢锁）。
2. 期望自动发生：ASH ≤15s 检测 → Incident(P1,lock) → 诊断 ≤60s 出 kill blocker 方案 →
   （mid+P1 紧急通道）一键/自动执行 KILL → 验证回路监视 blocked_sessions==0 →
   连续 3 次满足 → 事故 resolved。
3. 断言：`t_detect_sec≤60`、`t_plan_sec≤300`、`t_resolve_sec≤600`，PlaybookRun.status=succeeded，
   verify_result.recovered=true，Incident.status=resolved。

其他演练：
- `e2e_space.py`：表空间打满 → PB-SPACE-EXTEND 一键 → 验证 used_pct<90 → resolved。
- `e2e_config.py`：改一个参数制造 drift → config_drift 事故 → PB-CONFIG-ROLLBACK 一键回滚 →
  验证恢复 → resolved。

`verify_phase6c.py`：import 校验 + 三 e2e 演练断言 + SLA 报表数值校验。

**Phase 6 总验收**：8 类问题的注入演练全过；锁阻塞类端到端 MTTR≤600s 且**无人工介入**
（P1 紧急通道自动执行）；SLA 报表 1-5-10 达标率可见；契约四方一致；`verify_phase6{a,b,c}.py`
全绿。达成即 v5.0 目标"1 分钟发现、5 分钟方案、10 分钟解决"闭环成立。
