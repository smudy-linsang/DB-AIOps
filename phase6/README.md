# DB-AIOps Phase 6 详细设计说明书（施工总纲）

> **性质**: 详细设计说明书（Detailed Design Spec），非概要设计。目标是让实现者
> **照图施工**，不依赖二次脑补。
> **概要设计母文档**: `../PHASE6_DEVELOPMENT_DESIGN.md`（六大支柱、1-5-10 目标、EM13c 对标）
> **本目录**: 把母文档拆成可独立施工的三册 + 公共契约册。

---

## 0. 文档地图

| 文档 | 覆盖支柱 | 交付目标 | 施工工单数 |
|------|---------|---------|-----------|
| `00_conventions.md` | 全局 | 命名规范、契约源规则、字段类型约定、错误码、测试基线 | — |
| `10_phase6A_discovery.md` | 支柱一/二/三 | **1 分钟发现**：哨兵 + ASH-lite + 管道解耦 + 事件/事故模型 + L1/L3 检测 | 6A-01 ~ 6A-12 |
| `20_phase6B_diagnosis.md` | 支柱四/六(部分) | **5 分钟方案**：诊断管道自动化 + 通知带方案 + 作战室页 + 证据链 | 6B-01 ~ 6B-10 |
| `30_phase6C_remediation.md` | 支柱五/六 | **10 分钟解决**：Playbook 统一 + 验证回路 + 快速审批 + SLA 报表 | 6C-01 ~ 6C-11 |

**阅读顺序**：先读 `00_conventions.md`（所有册共享的契约与约定），再按 6A→6B→6C 施工。
6A 是地基，未完成前 6B/6C 无法验收。

---

## 1. 契约源唯一性原则（本期铁律，针对 Phase 5 教训）

Phase 5 的全部返工（URL 未注册、字段名 item_code/item_id 错配、config_id/db_config_id
错配、前后端字段名不一致）根因是**没有单一契约源**。本期规定：

1. **数据模型契约源** = 各册的「数据模型」章节的字段表。Django `models.py` 必须与字段表
   逐字段一致（名称、类型、default、null、choices）。
2. **API 契约源** = 各册的「API 契约」章节的请求/响应 JSON 样例。后端序列化输出与前端读取
   **必须使用样例中的字段名**，禁止任何一侧自行改名。
3. **跨进程消息契约源** = `00_conventions.md` 的 Redis Stream 消息 schema。
4. 任何字段名分歧一律以本说明书为准；如需改名，改说明书 → 再改代码，禁止反向。
5. 每个工单验收项含「契约一致性检查」：字段表 vs models.py vs API 样例 vs 前端读取，四方比对。

---

## 2. 全局架构（进程与数据流，施工态）

```
┌────────────────────────────────────────────────────────────────────────┐
│ 进程清单（本期后系统共 5 个常驻进程）                                       │
├────────────────────────────────────────────────────────────────────────┤
│ P1 sentinel_daemon   [新增,6A]  每实例1长连接, 5-10s 探活+黄金量+ASH采样   │
│ P2 collector         [改造,6A]  原 start_monitor 瘦身: 只采集+落库+发事件    │
│ P3 pipeline_worker   [新增,6A]  消费 Redis Stream: 检测→诊断→验证 三消费组    │
│ P4 django (runserver/gunicorn) [复用] REST API + SSE + 前端               │
│ P5 inspection (调度) [复用]     T3 巡检, 日/周/月                          │
└────────────────────────────────────────────────────────────────────────┘

数据流:
  目标库 ──(哨兵5-10s)──▶ P1 ──golden/ash──▶ TimescaleDB(session_sample)
                          │
                          └──event──▶ Redis Stream "events"
  目标库 ──(采集60s)────▶ P2 ──metrics──▶ PG/TSDB/ES
                          └──event──▶ Redis Stream "events"
  Redis "events" ──▶ P3(detect消费组) ──▶ 生成/更新 Incident ──▶ Redis "diagnosis"
  Redis "diagnosis" ─▶ P3(diag消费组) ──▶ RCA+影响+方案 落库 Incident ──▶ 通知
  Redis "verify" ────▶ P3(verify消费组)─▶ 监视修复后指标 ──▶ 关闭/升级 Incident
  Django ◀── 前端轮询/SSE 读 Incident/Event/Timeline
```

---

## 3. 1-5-10 秒表定义（度量口径，全册共用）

三段计时锚点全部落在 `Incident` 表的时间戳字段上（见 6A 数据模型），口径固定：

| 段 | 起点 | 终点 | 达标阈值 | 字段计算 |
|----|------|------|---------|---------|
| **T_detect（发现）** | 问题客观发生时刻（信号源事件的 `occurred_at`） | Incident.detected_at | ≤60s | detected_at − first_event.occurred_at |
| **T_plan（方案）** | Incident.detected_at | Incident.plan_ready_at | ≤300s | plan_ready_at − detected_at |
| **T_resolve（解决）** | Incident.detected_at | Incident.resolved_at | ≤600s | resolved_at − detected_at |

- 对"仅承诺 8 类"的问题（见母文档第四部分）才纳入达标率统计；范围外事故照常计时但不计入 SLA。
- T_detect 的"客观发生时刻"取该事故第一个关联 Event 的 `occurred_at`（哨兵探活失败时刻 /
  采样命中时刻 / 采集时刻），不是入库时刻，避免把系统自身延迟算进"发生"。

---

## 4. 配置项总表（新增，写入 settings.py + .env，本期一次性登记）

| 配置键 | 默认值 | 用途 | 引入册 |
|--------|-------|------|--------|
| `SENTINEL_ENABLED` | True | 哨兵进程总开关 | 6A |
| `SENTINEL_INTERVAL_SEC` | 8 | 哨兵探活间隔 | 6A |
| `SENTINEL_FAIL_THRESHOLD` | 3 | 连续失败几次判 DOWN | 6A |
| `SENTINEL_CONNECT_TIMEOUT_SEC` | 5 | 哨兵连接超时 | 6A |
| `ASH_ENABLED` | True | ASH-lite 采样开关 | 6A |
| `ASH_INTERVAL_SEC` | 15 | 会话采样间隔 | 6A |
| `ASH_RETENTION_DAYS` | 7 | session_sample 保留天数 | 6A |
| `PIPELINE_ENABLED` | True | pipeline_worker 总开关 | 6A |
| `PIPELINE_STREAM_MAXLEN` | 100000 | Redis Stream 上限(近似裁剪) | 6A |
| `INCIDENT_STORM_THRESHOLD` | 10 | 每实例每分钟事件数超此值触发风暴合并 | 6A |
| `INCIDENT_FLAPPING_WINDOW_MIN` | 10 | flapping 检测窗口 | 6A |
| `INCIDENT_FLAPPING_COUNT` | 3 | 窗口内 fire/resolve 次数阈值 | 6A |
| `DIAG_BUDGET_SEC` | 60 | 诊断管道单事故预算(超时告警) | 6B |
| `DIAG_RCA_TOPN` | 3 | RCA 返回候选根因数 | 6B |
| `PLAYBOOK_AUTO_LOW_RISK` | True | 低风险 Playbook 自动执行 | 6C |
| `PLAYBOOK_VERIFY_WINDOW_SEC` | 300 | 验证回路观察窗口 | 6C |
| `PLAYBOOK_AUTO_CIRCUIT_BREAK` | 3 | 1 小时内自动动作达此数则熔断转人工 | 6C |
| `ONCALL_ESCALATE_MIN` | 15 | P1 未确认自动升级分钟数 | 6C |

---

## 5. 验收总纲（每册末尾有本册细化）

| 里程碑 | 演练场景 | 达标线 |
|--------|---------|--------|
| 6A 完成 | 注入：kill 实例进程 / 制造锁阻塞链 / 打满连接 | 三场景 MTTD ≤60s，Incident 正确生成且不风暴刷屏 |
| 6B 完成 | 上述事故 | 事故创建 ≤60s 后，通知/作战室已含根因+2~3套方案+证据链 |
| 6C 完成 | 锁阻塞(全自动)/表空间满(半自动)/配置漂移(一键回滚) | 全自动类 MTTR ≤600s 且验证回路自动关闭事故 |

演练环境：本地 Docker 6 目标库 + 两个真实 TDSQL 实例（集中式 119.45.220.89:15002 /
分布式 :15005）。故障注入脚本纳入 `phase6/drills/` 一并交付。
