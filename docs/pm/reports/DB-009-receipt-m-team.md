# DB-009 接单回执：DM/GBase 实库矩阵（方言对齐 + GBase 矩阵）

- 任务单：`docs/pm/orders/DB-009-DM-GBase实库矩阵.md`（2026-11-06 muse 下达，执行：MMMan）
- 依据报告：`docs/pm/reports/DB-017-report-m-team.md`（有条件通过，2026-11-06）
- 执行：MMMan（M-team team lead）
- 接单时间：2026-11-06 17:20
- 接手方式：**本次由兜底定时任务接手**（宿主机 HTTP 出口未生效或未覆盖本单；黑板轮询发现 origin 新提交 `2323926`）
- 结论：**接单确认（不是交付结论）** —— 本件只报可跑范围与 ETA；正式交付报告另出 `DB-009-report-m-team.md`，结论三档之一。

## 1. 口径确认

- 本单三项任务：①DM 方言对齐（29 条不可跑 SQL ＋ `start_monitor` 的 `sql_stat` 分支 ＋ sentinel 的 DM ASH 分支）；②GBase 矩阵（先启动 id=11/15 目标库，启动不了书面说明原因，并补当前版本用例与 ASH 回归的可跑范围）；③更新 `V3.0_DIALECT_MATRIX.md`。
- PM 裁决口径（2026-11-06）：29 条方言不匹配记为缺陷「DM 方言对齐」，**修法走本单**，不另立修复单；DB-018 的依赖声明问题已闭合，剩余是驱动未安装与方言本身。
- 本单**修的是平台侧方言**，不代他人改产品代码以外的范围；不改 Xinchuang 现有链路行为；不碰 DB-001/DB-002；`STATUS.md` / `DECISIONS.md` 不动（仅 muse 可改）。

## 2. 可跑范围判定（按 DB-017 §4/§5/§6 的 52 条矩阵逐条对齐）

| 段 | 内容 | 可跑性 | 依据 |
|---|---|---|---|
| **B-1 DM 巡检 SQL 对齐（29 条）** | 逐条把视图不存在 / 列名不存在 / 语法不兼容改成 DM8 实库真实存在的视图与列 | **可跑（直连 SQL 逐条实测）** | 本机 DM8 实例在跑（Windows 服务 `DmServiceDAMENG` Running，`127.0.0.1:5236`，`DM Database Server 64 V8`，build `03134284488-20260330`）；**候选替代 SQL 已全部在实库逐条试跑通过**（见交付报告证据） |
| **B-2 DM 慢 SQL 面（`start_monitor` sql_stat）** | 平台用 `v$sqlstats`（实库 -2106 不存在） | **可跑**：改用 DM8 实有的 `V$SQL_HISTORY` 聚合口径 | 已实测 `V$SQL_HISTORY` 按 `SQL_ID` 聚合可取到 executions/elapsed/rows/text |
| **B-3 DM ASH 面（sentinel DM 分支）** | sentinel 把 `dm` 归入 Oracle 家族，用 `v$session`（实库为 `V$SESSIONS`，且列名体系不同） | **可跑（SQL 层）**：为 DM 单独出分支，不动 Oracle 分支 | 已实测 `V$SESSIONS` 的真实列（SESS_ID / USER_NAME / STATE / SQL_ID / TRX_ID / CLNT_HOST / CLNT_IP / APPNAME / MODULE / CREATE_TIME） |
| **B-4 DM 检测器级验证**（`DamengChecker` 跑 `collect_metrics`） | 用 `dmPython` 真正执行整段 checker | **不可跑，记缺口** | `dmPython` 需达梦官方授权安装包，未到位；`checkers/__init__.py` 条件注册 ⇒ `DamengChecker` 不注册。**不硬编** |
| **C-1 GBase 目标库启动** | id=11 / id=15（`127.0.0.1:5258`） | **待试**：先查本机是否还有可启的 GBase 实例/容器，启不了就书面说明原因 | 上轮实测 10061 连接被拒；历史连通证据为容器 `shihd/gbase8a:1.0`（`8.6.2.43-R7-free.110605`） |
| **C-2 GBase 自动化用例** | 补 `GbaseDialectTests`（参照 MySQL 类） | **可写可跑**（不依赖 GBase 实例的部分） | 现有 `monitor/tests_dialect.py` 无 gbase 类；用例本身可在 SQLite 口径下实跑 |
| **C-3 GBase ASH 回归** | `sentinel.py` 把 gbase 归入 MySQL 家族（`performance_schema`） | **待 C-1**：实例到位才能测 | 同 DB-017 R-8 口径 |
| **D 方言矩阵更新** | `V3.0_DIALECT_MATRIX.md` 的 DM / GBase 行 | **可做** | 交付报告产出后回填，矩阵只写实测结论 |

## 3. 计划与 ETA

| 段 | 内容 | ETA |
|---|---|---|
| 1 | DM 方言代码修复（29 条 ＋ `start_monitor` ＋ sentinel DM 分支），**逐条在 DM8 实库直连验证通过** | 2026-11-06 20:00（代码与验证证据先落库一次提交） |
| 2 | GBase 目标库启动尝试 ＋ 书面说明；补 `GbaseDialectTests` 与 ASH 可跑范围 | 2026-11-06 21:30 |
| 3 | `V3.0_DIALECT_MATRIX.md` 更新 ＋ 交付报告 ＋ 证据 ＋ `scripts/validate.sh unit`/`backend` | 2026-11-06 23:00 |

## 4. 约束确认

未改他人文件；未触碰 `docs/pm/STATUS.md` / `docs/pm/DECISIONS.md`；凭据不落盘、不进版本库（DM 口令只从平台库存密内存解密，证据中只留 `len=N,head=***`）；正式机 17.192 不动；内网不安排；不改 Xinchuang 现有链路行为。

（本件为接单回执，飞书群只作广播；最终交付以 `docs/pm/reports/DB-009-report-m-team.md` 为准。）
