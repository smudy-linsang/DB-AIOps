# DB-017 交付报告：009 环境与适配先行（DM / GBase 实库矩阵前置）

**结论：有条件通过** —— 本单要求的「环境就绪回执 + 方言矩阵更新计划（可跑/缺口分列）」已交付且全部有本机实测支撑；条件是 DB-009 的 DM/GBase 实库矩阵仍需先闭合三项前置（DM 授权驱动 `dmPython`、GBase 目标库启动、29 条 DM 方言不匹配修复），三项各自的复通条件见 §11。

- 任务单：`docs/pm/orders/DB-017-009环境与适配先行.md`（2026-11-06 muse 下达，执行：MMMan）
- 接单回执：`docs/pm/reports/DB-017-receipt-m-team.md`（2026-11-06 10:00 接单，ETA 今日 20:00）
- 执行：MMMan ｜ 交付时间：2026-11-06 15:35 ｜ 测试时点 HEAD：`7707b4d`（STATUS v1.20）
- 证据目录：`docs/pm/reports/evidence/DB-017-m-team/`（6 份 JSON 实测输出 + 6 份可复现脚本）
- 口径：只读核查，未改产品代码、未改他人文件、未触碰 `STATUS.md` / `DECISIONS.md`；凭据不落盘、不进版本库；正式机 17.192 未动；上线前不安排任何内网相关工作（用户 2026-11-06 决定）

---

## 1. 结论摘要

1. ★**订正我方 10:00 回执的一处初判**：回执写「本机**无** DM 实例 ⇒ DM 实库连通与方言行为不可测」。实测结论相反 —— **本机 DM8 实例在运行且可用**（Windows 服务 `DmServiceDAMENG` Running、进程 `dmsvc`、监听 `127.0.0.1:5236`，`DM Database Server 64 V8`，build `03134284488-20260330`）。DM 的环境**不再是阻塞项**，阻塞项是**驱动与方言**。详见 §4。
2. **DM 连接层是通的**：按平台自身连接器口径（`monitor/db_connector.py:237-267`，pyodbc + `DM8 ODBC DRIVER`）实连成功，**230.7 ms** 建连，`V$VERSION` / `V$SESSIONS` / `V$DATABASE` / `DBA_TABLES` 均可读。
3. **DM 方言大面积不匹配**：把平台声称的 DM 采集 SQL（`monitor/checkers/dm.py` 共 **52** 条）逐条在这台 DM8 上实跑 → **23 条可跑 / 29 条不可跑**（15 条视图不存在、12 条列名不存在、1 条语法不兼容、1 条其它）。另 `start_monitor` 的 DM `sql_stat` 分支用的 `v$sqlstats` 在实库**不存在**，sentinel 的 DM ASH 分支用的 `v$session`（实库是 `V$SESSIONS` 复数）**不存在** ⇒ DM 的「慢 SQL 面」「ASH 面」不可跑。
4. **GBase**：驱动侧无问题（pymysql）；但两个纳管实例（id=11 / id=15，指向 `127.0.0.1:5258`）目标库**未启动**，实测 TCP 拒绝（10061），采集日志累计降级 **2200** 次（2026-11-05 20:29 → 2026-11-06 14:50）；历史真实连通证据在（`shihd/gbase8a:1.0`，`8.6.2.43-R7-free.110605`），但**当前版本仓库里没有任何 GBase 方言用例**。
5. **平台侧 DM/GBase 采集产出实测为零**：时序库 `metric_point` / `sql_stat` 中 `db_config_id=9`（DM）、`11`/`15`（GBase）**无任何行**；对照 instance 18（TDSQL proxy）`metric_point` 67130 行、`sql_stat` 4723 行 ⇒「**连得上 ≠ 平台采得到**」被实测坐实（DM 检测器未注册 ⇒ 采集循环跳过该实例）。
6. **可复现的方言缺口已量化**（§5、§6），可直接作为 DB-009 的排期输入与 PM 的裁决依据；我方**不自判缺陷单、不代改**。

---

## 2. 驱动口径（实测，2026-11-06 15:25~15:32）

| 层 | 驱动 | 本机实测 | 后果 |
|---|---|---|---|
| 数据面连接器 `monitor/db_connector.py:237-267` | `import pyodbc` | **已装**；ODBC 驱动列表实测：`['SQL Server', 'DM8 ODBC DRIVER', 'Microsoft Access Driver (*.mdb, *.accdb)', 'Microsoft Excel Driver (*.xls, *.xlsx, *.xlsm, *.xlsb)', 'Microsoft Access Text Driver (*.txt, *.csv)', 'Microsoft Access dBASE Driver (*.dbf, *.ndx, *.mdx)']` | 连接层**通路是通的**（目标库可达时） |
| 巡检检测器层 `monitor/checkers/dm.py:11` | `import dmPython` | **缺失**：`find_spec=False`、`ModuleNotFoundError: No module named 'dmPython'`、`pip show dmPython` 无输出（该 venv 无 pip 模块） | `DamengChecker` **不注册** ⇒ 该实例在采集/巡检链路上被跳过 |
| 依赖声明 `requirements.txt` | 三行 | `pymysql>=1.1.0  # MySQL, Gbase8a, TDSQL`；`pyodbc>=5.0.0  # 达梦数据库连接层驱动 (DM8 ODBC Driver，用于 DbConnector)`；`# dmPython  # 达梦检测器依赖 (DM 原生 Python 驱动，需达梦官方授权安装包安装，可选按需加载)` | **订正 10:00 回执**：回执写「未声明 dmPython」，该表述已被 **DB-018 修复（`81dab6a`）后的现状取代** —— 现已作为**可选依赖**显式声明（注释行），故「依赖声明不一致」这一项已闭合，剩余的是**驱动未安装** |
| 注册条件 `monitor/checkers/__init__.py:37-47,56-64` | 「达梦 DM8 驱动为可选依赖（需官方授权安装 dmPython）」 | 实测 `CHECKER_MAP` 键 = `['gbase','mysql','oracle','pgsql','tdsql']`（**无 dm**）；`DM_DRIVER_AVAILABLE=False`；`DM_DRIVER_ERROR='No module named 'dmPython''`；`is_dm_checker_available()=False`；`get_checker('dm')=None`；`get_checker('gbase')=<class GbaseChecker>` | 缺依赖时**不报错、不告警**，只是不注册（DB-018 已把该路径改为显式信号，见 §7） |

**GBase 驱动口径**：`requirements.txt` 的 `pymysql` 注释已覆盖 `MySQL, Gbase8a, TDSQL` ⇒ GBase8a **无独立驱动问题**（与 DM 不同），其不可测原因纯粹是**实例未启动**。

---

## 3. DM 环境可得性（★与我方 10:00 回执初判相反）

### 3.1 服务侧事实

- Windows 服务 `DmServiceDAMENG`：**Running**；进程 `dmsvc` × 2（PID 8348 / 8408）。
- 监听 `127.0.0.1:5236`（TCP 可建连）。
- 实例标识（实连后由库自身返回）：
  - `V$VERSION` → `DM Database Server 64 V8` / `DB Version: 0x7000d` / `03134284488-20260330-320063-20200` / `Msg Version: 1` / `Gsu level(5) cnt: 102`
  - `V$DATABASE.NAME` → `DAMENG`；`USER` → `SYSDBA`
  - `DBA_TABLES` → 87 张（均为 `SYS` 库对象）；`DBA_VIEWS(SYSDBA)` → 0；`DBA_INDEXES(SYSDBA)` → 1
  - `V$SESSIONS` → 可读（含 `SESSID`/`USER_NAME`/`CLNT_IP`/`APPNAME`/`TYPE` 等列），实测本机 ODBC 会话 `CLNT_IP=127.0.0.1`、`APPNAME=python.exe`、`TYPE=ODBC`

### 3.2 连接层实测（平台自身口径）

- 连接串：`DRIVER={DM8 ODBC DRIVER};SERVER=127.0.0.1:5236;UID=SYSDBA;PWD=<hidden>`（与 `monitor/db_connector.py:249-256` 一致）
- **建连成功，耗时 230.7 ms**；口令取自平台本地开发库中该实例已存密（`enc:` 前缀 AES-256-GCM），**内存解密直用**，证据中只保留长度，不落盘、不打印明文、不入库
- 基础只读方言实测全部成功：`SELECT 1 FROM DUAL`、`V$VERSION`、`V$DATABASE`、`DBA_TABLES`、`V$SESSIONS`、`V$PARAMETER`

### 3.3 订正说明（诚实标注）

我方 10:00 接单回执 §2.2 写「本机**无** DM 实例，DM 实库连通与方言行为**不可测** ⇒ 记缺口」。该判断**错误**，本轮实测推翻：环境**有**且**可用**。错误原因：当时只核了「平台侧是否采得到」，未核「目标库进程是否在跑」。
**订正后的口径**：DM 的**环境**已就绪；不可测的是**平台侧的 DM 采集链**（§5、§6、§7），不是 DM 实例本身。

---

## 4. DM 方言实跑矩阵（平台声称口径 vs 本机 DM8 实际）

### 4.1 巡检采集 SQL（`monitor/checkers/dm.py`，52 条逐条实跑）

**汇总：23 条可跑 / 29 条不可跑**（不可跑归类：视图不存在 15、列名不存在 12、语法不兼容 1、其它 1）。

**可跑域（基础面）** —— 版本与实例（`V$VERSION`、`V$INSTANCE` 的 INSTANCE_NAME/HOST_NAME/START_TIME、uptime）、会话计数与状态（`V$SESSIONS` 计数、`STATE='ACTIVE'`、`STATE='INACTIVE' AND TRX_ID != 0`、按 STATE 分组、失败登录分组）、参数（`V$PARAMETER` 的 `MAX_SESSIONS` / `BUFFER` / `RLOG_BUF_SIZE` / `RLOG_POOL_SIZE`）、表空间（`V$TABLESPACE` 总量与使用率）、事务计数（`V$TRX`）、归档文件计数（`V$ARCH_FILE`）、Redo 计数（`V$RLOG` 计数）、死锁历史计数（`V$DEADLOCK_HISTORY`）、对象枚举（`DBA_TABLES` / `DBA_INDEXES` / `DBA_VIEWS` / `DBA_PROCEDURES` / `DBA_TRIGGERS` 的非系统 owner 过滤）。

**不可跑域（高级面）** —— 典型错误原文：

| 平台声称 | 实库报错 | 归类 |
|---|---|---|
| `SELECT ... FROM V$SQLSTATS` / `V$SQL_HISTORY` | 无效的表或视图名[V$SQLSTATS] (-2106) / 无效的列名[SQL_TEXT] (-2111) | 视图/列不存在 |
| `V$ARCH_DEST`、`V$DEST_PENDING` | 无效的表或视图名 (-2106) | 视图不存在 |
| `V$CLUSTER`、`V$DSC_INSTANCES` | 无效的表或视图名 (-2106) | 视图不存在（DW/DSC 集群面） |
| `V$GLOBAL_LATCH` | 无效的表或视图名 (-2106) | 视图不存在 |
| `V$LOGIN` | 无效的表或视图名 (-2106) | 视图不存在 |
| `V$SESSIONS` 明细列（`SESS_ID`/`PROGRAM`/`HOST`/`SQL_TEXT`/`TRX_ID`…） | 无效的列名[PROGRAM] / [SQL_TEXT] (-2111) | 列名不存在 |
| `V$TRX` 明细列（`TRX_START_TIME`/`SQL_TEXT`） | 无效的列名 (-2111) | 列名不存在 |
| `V$RLOG` 的 `STATUS` 列、`V$INSTANCE` 的 `LOG_SIZE` 列 | 无效的列名 (-2111) | 列名不存在 |
| `V$DATABASE` 的 `ARCHIVE_MODE` / `MODE$` | 无效的列名 (-2111) | 列名不存在 |
| `V$TABLESPACE` + `DBA_TABLES` 组合（NUM_ROWS 子查询） | 语法分析出错 (-2007) | 语法不兼容 |

### 4.2 采集链其余分支（`start_monitor` / `sentinel`）

| 分支 | SQL | 实测 | 后果 |
|---|---|---|---|
| `start_monitor.py:648-655`（DM sql_stat） | `SELECT sql_id, executions, elapsed_time, rows_processed, buffer_gets, sql_text FROM v$sqlstats … FETCH FIRST 100 ROWS ONLY` | **无效的表或视图名[v$sqlstats] (-2106)** | **DM 的 sql_stat 采集不可用** —— 即便装上 dmPython 也采不到（方言缺口，非驱动缺口） |
| `start_monitor.py:683-687`（DM cpu_cores） | `SELECT value FROM v$parameter WHERE name='cpu_count'` | 可执行（返回空集） | 可跑 |
| `sentinel.py:485`（DM ping） | `SELECT 1 FROM DUAL` | 可执行 | 可跑 |
| `sentinel.py:402-406`（DM golden_metrics） | `SELECT count(*) FROM v$session WHERE type='USER'` | **无效的表或视图名[v$session] (-2106)** | DM 的**连接数派生指标**取不到 ⇒ `conn_usage_pct` 一族失效 |
| `sentinel.py:375-376`（DM ASH，走 Oracle 家族分支 `_ash_oracle`） | `v$session`（不存在）+ `V$SESSIONS.USER_ID`（列不存在） | **两条都不可跑** | **DM 的 ASH 采样链在实库上不可用** ⇒ DM 无性能中心/ASH 数据 |

**口径说明**：dmPython 与 DM8 ODBC DRIVER 都走 DM 协议，SQL 方言是**服务端**行为，因此用 ODBC 实跑可判定「这条 SQL 在 DM8 上能否执行」；本节结论对「装上 dmPython 后能否跑通」同样成立。

---

## 5. GBase 环境可得性

| 项 | 实测 |
|---|---|
| 驱动 | `pymysql`（`requirements.txt` 注释覆盖 Gbase8a）⇒ **无独立驱动问题** |
| 纳管实例 | id=11 `GBase8A_测试库`、id=15 `probe-gbase`，均 `db_type=gbase`、`127.0.0.1:5258`、`user=root`、`is_active=True` |
| 实测连通 | **TCP 拒绝**（`WinError 10061` 目标计算机积极拒绝）；pymysql 连接失败：`OperationalError: (2003, "Can't connect to MySQL server on '127.0.0.1'")` |
| 采集日志 | `monitor.degrade` 的 `collect.Gbase` 降级告警**连续 2200 次**，2026-11-05 20:29 起、至 2026-11-06 14:50 仍在增长（`[degrade] collect.Gbase (第 N 次) 原因=GBase8A_测试库 / probe-gbase 异常=OperationalError (2003, ...)`） |
| 平台侧产出 | 时序库 `metric_point` / `sql_stat` 中 **id=11 / id=15 无任何行** |
| 历史真实连通证据 | `V3.0_DIALECT_MATRIX.md` §二 第 5 行：容器 `shihd/gbase8a:1.0`，**真实 GBase 8a `8.6.2.43-R7-free.110605`**，容器 Up 但 healthcheck 假阳性，手工探测发现 1 个真实缺陷；DB-001 §8 明确「GBase 有真实容器连通与手工探测证据，但**无自动化方言用例执行**」 |
| 当前版本用例缺口 | `monitor/tests_dialect.py` 现有 `MySQLDialectTests`(5) / `MySQLSlowSqlDialectTests`(3) / `PostgresDialectTests`(3) / `MySQLCheckerDialectTests`(4) / `PostgresCheckerDialectTests`(3) / `OracleDialectTests`(4) / `DamengDialectTests`(2) ⇒ **无 GBase 用例类**；`DamengDialectTests` 因 `DIALECT_DM_DSN` 未设置而 **skip（从未实跑）** |
| ASH 回归口径 | `sentinel.py:371` 把 `gbase` 归入 **MySQL 家族分支**（`performance_schema` 采样）⇒ GBase8A 的 ASH 回归前提是 `performance_schema` 可用性，属实例到位后才能测；DB-001 §8 的「ASH 回归」口径即指此 |

---

## 6. 平台侧采集实况（实测）

时序库按实例产出（2026-11-06 15:28 快照，TimescaleDB `timeseriesdb`）：

| 表 | 有产出的实例 | 无产出的实例 |
|---|---|---|
| `metric_point` | id=18（TDSQL proxy）67130 行；id=2（pgsql）58089 行；id=19（本轮 S-1 新建 mysql 实例）331 行 | **id=9（DM）、id=11 / id=15（GBase）无任何行** |
| `sql_stat` | id=18 4723 行；id=19 45 行 | id=9 / 11 / 15 无 |
| `session_sample` | 无（本次快照为空） | — |

采集循环对 DM 的处理路径（`monitor/management/commands/start_monitor.py:188-198`）：`CHECKER_MAP.get('dm')` → `None` ⇒ 进入 `elif config.db_type == 'dm'` 分支 ⇒ 显式跳过并记 `status='missing_driver'`、实例状态置 `DOWN`（**DB-018 修复后的可见信号**）。

**为什么日志里没看到这条新告警**：当前常驻采集进程是 2026-11-05 20:29 启动的**旧代码**（DB-018 于 11-06 落库），故未加载新分支；且 `DeltaTracker` 是**进程内状态**（重启后第一轮不产出数据），重启采集器会打断 G-11 所依赖的 **24 小时采集连续性**（DB-005R 计划 11-07 12:00 回填）⇒ 我方**主动不重启**，改以「代码路径 + 实测取证」证明（`get_checker('dm')=None`、`CHECKER_MAP` 无 `dm`、时序库零产出）。重启采集器的动作留待 PM 决定。

---

## 7. 可跑 / 缺口分列（总表，DB-009 直接可用）

### 7.1 可跑（外网/本机，不需内网）

| # | 项 | 实测依据 |
|---|---|---|
| R-1 | DM **连接层**建连（pyodbc + `DM8 ODBC DRIVER`） | 230.7 ms 建连成功 |
| R-2 | DM **基础监控面**：版本/实例/会话计数与状态/参数/表空间/事务计数/redo 计数/归档文件计数/死锁历史计数/DBA 对象枚举 | 52 条巡检 SQL 中 **23 条实跑通过** |
| R-3 | DM **cpu_cores**（`v$parameter`） | 可执行 |
| R-4 | DM **ping**（`SELECT 1 FROM DUAL`） | 可执行 |
| R-5 | GBase **驱动口径**（pymysql 无独立驱动问题） | `requirements.txt` 注释实测 |
| R-6 | GBase **历史真实连通记录梳理**（容器/版本/已知缺陷） | `V3.0_DIALECT_MATRIX.md` §二、DB-001 §8 |
| R-7 | GBase **当前版本自动化用例缺口清单**（无 gbase 类；dm 类从未实跑） | `monitor/tests_dialect.py` 逐类清点 |
| R-8 | GBase **ASH 回归口径判定**（走 MySQL 家族分支，前提=performance_schema） | `monitor/sentinel.py:371-376` |

### 7.2 缺口（逐项记，不硬编）

| # | 缺口 | 原因 | 复通条件 |
|---|---|---|---|
| G-D1 | DM **巡检检测器层**整体不可用 | `dmPython` 未安装（需达梦官方授权安装包）；`DamengChecker` 不注册 ⇒ 采集循环跳过 DM 实例 | 拿到授权安装包并装入 venv ⇒ `CHECKER_MAP` 出现 `dm` 且 `get_checker('dm')` 非 None |
| G-D2 | DM **慢 SQL 面**（sql_stat）不可用 | 平台用 `v$sqlstats`，实库不存在该视图（-2106） | 改用 DM8 实际可用的 SQL 统计视图并补方言用例（另立单，见 §9 B-2） |
| G-D3 | DM **ASH 面**（性能中心/哨兵采样）不可用 | sentinel 把 `dm` 归入 Oracle 家族分支，用 `v$session` / `V$SESSIONS.USER_ID`，实库两者均不可用 | 同上，另立单修 sentinel 的 DM 分支 |
| G-D4 | DM **连接数派生指标**不可用 | `golden_metrics` 用 `v$session`（实库 `V$SESSIONS`） | 同 G-D3 |
| G-D5 | DM **29 条巡检 SQL** 视图/列名/语法不匹配 | 平台声称口径基于另一版 DM 视图体系 | 按 §4.1 不可跑清单逐条对齐 DM8 实际视图列（另立单） |
| G-G1 | GBase8A **实库连通 / 方言 / ASH 回归**不可测 | id=11 / id=15 目标库未启动（10061）；mock 靶场无 GBase；内网按用户决定不安排 | 启动 GBase8a 目标库（或提供可达实例）⇒ 同一套探针重跑 |
| G-G2 | GBase **无自动化方言用例** | `monitor/tests_dialect.py` 无 gbase 类 | 补 `GbaseDialectTests`（可参照 MySQL 类改造），随 G-G1 一并落地 |
| G-G3 | GBase **历史连通记录的容器已不可复现** | `shihd/gbase8a:1.0` 容器在当前环境不可用（历史证据来自 2026-10-15 矩阵） | 拉起同版本容器 `8.6.2.43-R7-free.110605` 或提供等价实例 |
| G-N1 | 正式机 17.192 相关全部项 | 用户 2026-11-06 决定不动 | 上线后由 PM 另派 |
| G-N2 | 内网凭据相关全部项 | 凭据不供给、不落盘、不进版本库 | 同上 |

---

## 8. 方言矩阵更新计划（`V3.0_DIALECT_MATRIX.md` 交 DB-009 执行）

矩阵现状（2026-10-15 版）：6 方言中 **3 个实跑通过**（mysql / pgsql / oracle，共 19 条用例全过、0 skip）、**3 个未执行**（dm / gbase / tdsql）；GBase 有真实容器连通与手工探测证据但**无自动化用例**。

本单建议的更新分四批（**只列计划，本单不改矩阵文件**）：

| 批 | 内容 | 前提 | 产出 |
|---|---|---|---|
| **A**（可立即做，不需实库） | 把本轮 **DM 方言实测矩阵**（52 条 → 23 可跑 / 29 不可跑 + ASH/golden/sql_stat 三分支）写入矩阵的 DM 行，逐条标注「实跑通过 / 视图不存在 / 列名不存在 / 语法不兼容」 | 无 | DM 行从「未执行」升级为「**部分执行（23/52 基础面通过）**」+ 不可跑清单 |
| **B**（需授权驱动） | DM 方言用例实跑：装 `dmPython` 后，用 `DIALECT_DM_DSN` 打开已存在的 `DamengDialectTests`（2 条：ASH 采样 / 工单预执行 EXPLAIN PLAN FOR） | G-D1 闭合 | DM 行升级为「已执行」；并暴露 G-D2/G-D3/G-D5 的真实失败形态 |
| **C**（需实例） | 补 `GbaseDialectTests`（参照 MySQL 类：连接超时/只读会话/ASH 形状/采集契约/降级不崩）并实跑 | G-G1 + G-G2 | GBase 行从「未执行（无用例）」升级为「已执行」 |
| **D**（对齐修复后） | 把 B/C 的失败项按 G-D2/G-D3/G-D5 分派修复单，修复后重跑矩阵并刷新结论 | B/C 完成 | 6 方言矩阵首次具备「4 方言有实跑证据 + 逐项失败归因」 |

矩阵口径纪律（沿用 DB-001 §1.3 结论）：**未实跑的一律写「未执行」并给原因，不允许推断代替代码指认**；本单已按此把 DM 从「无容器」订正为「环境可用、方言部分可跑」。

---

## 9. 遗留缺口与复通条件（对 PM 的三个待裁决点）

1. **是否就 G-D2/G-D3/G-D5 立缺陷单**（平台侧 DM 方言与实库不匹配，外网本机即可稳定复现）：我方**不自判、不代改**，按 DB-005/DB-017 既有口径交 PM 裁决；建议按「DM 方言对齐」单独立批（§8 B/D 批）。
2. **是否重启常驻采集器**以加载 DB-018 后的 `missing_driver` 可见信号：建议 **PM 决定时点**（当前重启会打断 G-11 的 24h 采集连续性，11-07 12:00 回填后再重启最稳）。
3. **DM 授权驱动 `dmPython` 的获取路径**：需达梦官方授权安装包，本单无法自行解决；一旦到位，§8 B 批可立即执行。

---

## 10. 环境副作用披露（如实记录）

- 本地开发库新增 1 个纳管实例：**id=19 `M-team probe-mysql-8.0`**（`db_type=mysql`、`127.0.0.1:13306`、`service_name=aiops_probe_shop`）—— 属 DB-005R 的 S-1 真实 MySQL 8.0 链路回归用，与本单同批登记（见 DB-005R 报告）。
- 复位了本地开发账号 `mteam` 的口令（仅本机开发库，供 API 验收脚本登录；口令只经环境变量传递，不落盘、不进版本库）。
- DM 实例口令取自本地开发库存密，**内存解密直用**；证据 JSON 中该字段只保留长度（`len=N,head=***`）。
- 探针只执行 `SELECT`；未对 DM/GBase 做任何写入；未重启任何常驻进程；未改任何产品代码与配置。
- 采集日志为**只读追加**，未截断、未清理。

## 11. 约束确认

未改产品代码（若后续需改 DM 方言/哨兵分支，另立单并遵守直推 main 与 `scripts/validate.sh` 纪律）；未改他人文件；未触碰 `docs/pm/STATUS.md` 与 `docs/pm/DECISIONS.md`；不碰 DB-001 设计工作、不占用 DB-002；正式机 17.192 未动；无内网相关操作。

## 12. 证据清单

| 文件 | 内容 |
|---|---|
| `evidence/DB-017-m-team/db017_env.json` | ODBC 驱动列表、dmPython 可用性、检测器注册表、DM/GBase 实例清单与 TCP/pymysql 实测、采集日志摘录 |
| `evidence/DB-017-m-team/db017_dm_probe.json` | DM 建连耗时与基础只读方言结果、检测器层对照（口令打码） |
| `evidence/DB-017-m-team/db017_dm_dialect.json` | DM 方言取数清单（`V$SESSIONS` / `V$VERSION` / `V$DATABASE` / `DBA_*` 等） |
| `evidence/DB-017-m-team/db017_dm_dialect_matrix.json` | **平台声称的 52 条 DM 采集 SQL 逐条实跑矩阵**（可跑/不可跑 + 错误原文） |
| `evidence/DB-017-m-team/db017_dm_ash_probe.json` | sentinel 的 DM ping / golden_metrics / ASH 分支与 checker 关键 SQL 实跑 |
| `evidence/DB-017-m-team/db017_platform_state.json` | 时序库按实例产出统计、纳管实例状态、DM 日志信号 |
| `evidence/DB-017-m-team/db017_*.py`（6 个） | 上述全部结论的可复现脚本（只读；凭据一律从平台自身密文内存解密） |

（本件为交付报告，飞书群只作广播。）
