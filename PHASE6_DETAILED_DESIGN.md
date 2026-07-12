# DB-AIOps Phase 6 详细设计说明书（照图施工版）

> **文档性质**: 详细设计说明书（LLD）。概要设计见 `PHASE6_DEVELOPMENT_DESIGN.md`（HLD），
> 本文是其唯一施工依据。**本文与代码冲突时，以本文为准修改代码；本文有误时，先改本文再施工。**
> **设计日期**: 2026-07-12　**目标版本**: v5.0　**状态**: 仅设计，未施工
>
> **施工者注意（三条铁律，源自本项目 Phase 5 的真实教训）**:
> 1. 每个新 API 必须同时完成三处接线：`dbmonitor/urls.py` 注册 + `frontend/src/services/api.js`
>    封装 + 页面调用。缺一处即视为未完成（Phase 5 巡检模块曾因漏注册 URL 而整体不可用）。
> 2. 前后端字段名以本文第 8 章「接口契约」为唯一事实源，禁止任何一侧擅自改名
>    （Phase 5 曾因 warn_count/warning_count 之类错配导致页面全空）。
> 3. 每个工单（第 11 章）完成后必须在本地全栈环境跑通其 DoD 验收步骤，纯代码审查不算完成
>    （Phase 5 verify 脚本只验证了 import 成功，掩盖了全部运行时缺陷）。

---

## 目录

1. 施工总则与代码规范
2. 进程拓扑与数据流
3. 数据模型详细设计（Django Models + 迁移）
4. TimescaleDB 超表 DDL
5. Redis Stream 消息总线设计
6. 模块详细设计（逐文件）
7. 检测规则库详细定义（L1/L2/L3 全量规则表）
8. 接口契约（API 详细设计，唯一事实源）
9. Playbook 详细定义（含全量 SQL）
10. 前端详细设计
11. 工单分解（WBS）与验收标准（DoD）
12. 配置项清单
13. 故障注入演练设计
14. 兼容性、灰度与回滚

---

## 1. 施工总则与代码规范

### 1.1 沿用本仓库既有规范
- 异常处理：禁止裸 `except:`，一律 `except Exception`；单指标/单步骤失败不得中断整体流程。
- 配置：一切可调参数走 `os.environ.get()` + `settings.py`，默认值写入本文第 12 章。
- 数据库访问：Django ORM 为主；TimescaleDB 走 `monitor/timeseries.py` 的单例连接模式；
  目标库连接复用 `monitor/db_connector.py`。
- TDSQL 特殊性（已验证事实）：`/*proxy*/` 网关命令**只认小写**；互联网链路 SYN 偶发丢包，
  所有对 TDSQL 实例的新增连接必须带重试（参照 `checkers/tdsql.py` 的
  `CONNECT_RETRIES=5, CONNECT_TIMEOUT_SEC=15` 模式）。
- 线程内 Django DB 连接：worker 线程结束前必须 `connection.close()`
  （2026-07 已修复过一次连接泄漏，见 start_monitor.py `_run_single_check` 的注释）。

### 1.2 新增目录结构

```
monitor/
├── phase6/                        # 本期全部新增代码收敛于此包，便于灰度与回滚
│   ├── __init__.py
│   ├── constants.py               # 枚举/常量: 类别、优先级、状态机、流名称
│   ├── streams.py                 # Redis Stream 封装(发布/消费/DLQ)
│   ├── sentinel/
│   │   ├── __init__.py
│   │   ├── probes.py              # 各库探针 SQL 与解析
│   │   ├── ash_sampler.py         # ASH-lite 会话采样
│   │   └── daemon.py              # 哨兵主循环(被 management command 调用)
│   ├── detection/
│   │   ├── __init__.py
│   │   ├── rules_l1.py            # L1 硬阈值规则(第 7 章表的代码化)
│   │   ├── rules_l3.py            # L3 复合规则
│   │   ├── static_fallback.py     # L2 基线未成熟时的静态兜底阈值表
│   │   └── evaluator.py           # 规则求值器(输入指标字典→输出 Event 列表)
│   ├── incidents/
│   │   ├── __init__.py
│   │   ├── aggregator.py          # 事件→事故聚合器(消费者)
│   │   ├── priority.py            # 优先级矩阵
│   │   └── lifecycle.py           # 事故状态机(唯一允许改 status 的入口)
│   ├── diagnosis/
│   │   ├── __init__.py
│   │   ├── pipeline.py            # 诊断管道编排(带阶段时间预算)
│   │   ├── ash_context.py         # ASH 时间线切片(供 ContextAggregator 调用)
│   │   └── realtime_diag.py       # Real-Time 诊断(独立短超时连接)
│   ├── playbooks/
│   │   ├── __init__.py
│   │   ├── registry.py            # Playbook 定义注册表(第 9 章的代码化)
│   │   ├── executor.py            # 执行引擎(precheck/step/verify/rollback)
│   │   └── verifier.py            # 验证回路(消费者)
│   ├── notify/
│   │   ├── __init__.py
│   │   └── dispatcher.py          # 按 NotificationPolicy 分级触达 + 升级链
│   └── sla.py                     # 秒表计算与报表聚合
├── management/commands/
│   ├── start_sentinel.py          # 新增: 哨兵+ASH 守护进程
│   ├── run_pipeline.py            # 新增: 管道 worker(聚合/诊断/验证三消费组)
│   └── init_phase6.py             # 新增: 初始化 Playbook/NotificationPolicy/超表
scripts/drill/                     # 故障注入演练(第 13 章)
│   ├── run_drill.py
│   ├── inject_lock_storm.py
│   ├── inject_conn_storm.py
│   ├── inject_disk_fill.sh
│   └── inject_repl_break.py
frontend/src/pages/
│   ├── IncidentList.jsx           # 新增
│   ├── IncidentWarRoom.jsx        # 新增(作战室)
│   ├── SLAReport.jsx              # 新增
│   └── OnCallSettings.jsx         # 新增
frontend/src/components/
│   ├── IncidentStopwatch.jsx      # 1-5-10 三段秒表
│   ├── IncidentTimeline.jsx
│   ├── RcaEvidenceCard.jsx
│   └── PlanExecutePanel.jsx
```

### 1.3 全局完成定义（每个工单叠加各自 DoD）
代码 + 迁移 + URL 三处接线 + 本地全栈端到端演示通过 + 无新增连接泄漏
（跑 10 轮后 `pg_stat_activity` 连接数稳定）+ `PROJECT_MANUAL.md` 对应章节更新。

---

## 2. 进程拓扑与数据流

### 2.1 进程清单（本地开发 = 4 个终端 + Docker）

| # | 进程 | 启动命令 | 职责 |
|---|------|---------|------|
| P1 | Web/API | `python manage.py runserver 0.0.0.0:8000` | 现有 |
| P2 | 全量采集 | `python manage.py start_monitor` | 现有(瘦身改造, 见 6.8) |
| P3 | **哨兵** | `python manage.py start_sentinel` | T0 探活 + T1 ASH 采样 + L1 就地求值 |
| P4 | **管道worker** | `python manage.py run_pipeline` | 聚合/诊断/验证三消费组(单进程多线程) |
| — | 前端 | `cd frontend && npm run dev` | 现有 |

### 2.2 端到端数据流（以锁风暴为例，标注时间预算）

```
t=0s    业务侧发生阻塞
t≤15s   [P3] ASH 采样发现 blocked 链(第1次)
t≤30s   [P3] 第2次采样确认 → 规则 ASH-001 命中 → XADD dbaiops:events
t≤31s   [P4/聚合组] 消费事件 → 无同 dedup_key 开放事故 → 创建 Incident(P1, status=open)
        → 秒表 detected_at 落表 → SSE monitor:incidents 推送
        → [P4/通知] P1 首报: "发现锁风暴, 诊断中"(含作战室链接)
        → XADD dbaiops:diagnosis
t≤90s   [P4/诊断组] 管道完成(预算60s): rca_result/impact/plans 落库
        → status=plan_ready, plan_ready_at 落表
        → P1 二报: 根因+首选方案+一键执行链接
t≤120s  DBA 在作战室点击"执行标准方案"(或低风险自动执行)
        → PlaybookRun 创建 → precheck → 执行 kill blocker → status=verifying
        → XADD dbaiops:verify
t≤400s  [P4/验证组] blocked_pairs==0 持续 180s → PlaybookRun=succeeded
        → Incident status=resolved, resolved_at/mttr_sec 落表 → 三报: 已解决+处置摘要
```

### 2.3 消息流拓扑

```
生产者                          