**结论：不通过**（DB-022 补充实库补证任务已按单在外网 4 个真实库逐项执行完成并留存原文；但按实库运行质量判定为**不通过**——外网 4 库仅 PostgreSQL 16.13 全部 12/12 正常通过；DM8 15 项中有 **9 项** SQL 在 DM8 失效 [6 项直接 `error`、3 项报错被内部吞成 `ok`]，GBase 8a 10 项中有 **6 项**异常 + 1 项变量空行 [1 项 `DictCursor` `KeyError`、5 项报错被内部吞成 `ok`]，MySQL 8.0.45 12 项中有 **5 项**异常 + 1 项变量空行 [1 项 `KeyError`、1 项真实命中 3 行因 `DictCursor` 吞错漏报为 0、1 项命中 10 行但字段值错位成列名、2 项 8.0 兼容报错被内部吞成 `ok`]；Oracle 分支 23 项全部**未测·延后**。须针对实库缺陷另立修复单闭环，并在上线后内网完成 Oracle 复测。）

# DB-022 补充：实库补证交付报告（上半 23 项）

- **任务单**：`docs/pm/orders/DB-022-补充-实库补证.md` (v1.0)
- **前序报告**：`docs/pm/reports/DB-022-report-GG.md`
- **执行人**：GG
- **接单回执**：`docs/pm/reports/DB-022-补充-receipt-GG.md`
- **交付时间**：2026-11-13 10:35 (+08:00)
- **代码纪律声明**：严格遵守任务单约束，**未修改**任何检测器源码（`monitor/inspection_executor.py`）、注册表（`DETECTOR_REGISTRY`）、目标库数据、`docs/pm/STATUS.md` 与 `docs/pm/DECISIONS.md`；所暴露的问题统一记入 §七，建议另立单修复。

---

## 一、实库连接信息与总览统计

### 1.1 实库连接与通道明细（凭据不回显）

所有目标库均以 `readonly=True` 只读会话直连验证（口令由 `DatabaseConfig` 加密存储或本地 `.env` 运行期解密注入内存，报告与脚本均不记录明文用户名/口令）。

| 分支 | 通道与目标库标识 | 连接驱动与参数 | 实测库名 | 实测数据库版本原文 | 实测数据库时间 |
|---|---|---|---|---|---|
| **DM** | `DatabaseConfig cid=9`（`DM_测试库`，`db_type=dm`，宿主 DM8 服务 `5236`） | `DbConnector._connect_dm`（`pyodbc` + `{DM8 ODBC DRIVER}`，只读连接） | `DAMENG` | `DM Database Server 64 V8 \| DB Version: 0x7000d \| 03134284488-20260330-320063-20200 \| Msg Version: 1 \| Gsu level(5) cnt: 102` | `2026-11-13 10:30:48` |
| **GBase** | `DatabaseConfig cid=11`（`GBase8A_测试库`，`db_type=gbase`，`127.0.0.1:5258`；同实例另有 `cid=15`） | `DbConnector._connect_mysql`（`pymysql` + **`DictCursor`**）；原生 SQL 对照用 `pymysql.cursors.Cursor` | `None`（未指定默认库，系统库含 `information_schema` / `performance_schema` / `gbase` / `gclusterdb` / `gctmpdb`） | `8.6.2.43-R7-free.110605` | `2026-11-13 10:30:50` |
| **MySQL** | `DatabaseConfig cid=19`（`M-team probe-mysql-8.0`，`db_type=mysql`，`13306`，DB-005R 同款） | `DbConnector._connect_mysql`（`pymysql` + **`DictCursor`**）；原生 SQL 对照用 `pymysql.cursors.Cursor` | `aiops_probe_shop` | `8.0.45` | `2026-11-13 10:30:51` |
| **PG** | 主测库：`db_monitor`（宿主机 PG 容器 `5432`，DB-023 同款）；容量采样及对照库：`DatabaseConfig cid=2`（`timeseriesdb`，同实例 `5432`） | `DbConnector._connect_postgresql` / `psycopg2`（`readonly=True, autocommit=True`） | 主测：`db_monitor`<br>对照/时序：`timeseriesdb` | `PostgreSQL 16.13 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit`（注：任务单描述为 16.15，宿主实测返回为 16.13） | `db_monitor`: `2026-11-13 02:30:51 UTC`<br>`timeseriesdb`: `2026-11-13 02:30:52 UTC` |
| **Oracle** | 外网无真实 Oracle 库（本地 `cid=3` 为容器 XE，不在本次真实库补证范围内） | — | — | — | — |

> **关键方法说明（为什么双路对照执行）**：代码走查与实测均发现，部分检测器内层写了 `try: cur.execute(...) except Exception: rows = [] / 0`（吞掉了目标库真实 SQL 异常），且 `DbConnector._connect_mysql` 默认开启 **`DictCursor`** 而检测器按元组下标 `r[0]/r[1]` 或 `dict(zip(cols, r))` 取值。因此本次对每一项均**同步记录两部分实测结果**：
> 1. **原生 SQL 直连实测**：用各自引擎标准游标直接执行检测器源码中的 SQL 原文（不吞错），记录目标库真实返回行或原始报错；
> 2. **检测器实库调用**：经 `InspectionExecutor._run_single` 调用对应 `GenericDetector.detect_*` 连接同一实库，记录最终返回状态与 `summary`/`metrics`/`error`。

### 1.2 分支实测结果汇总表

| 分支 | 要求测项数 | 正常通过（未命中） | 有效命中 | `error`（直接报错） | `error`（SQL报错吞错 / 游标缺陷假阴性 / 字段错位） | 实测 `skip`（非适用项） | 未测·延后 | 分支实测结论 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **DM8** (`cid=9`) | 15 项（+8 项 ORA skip） | **6** | 0 | **6** | **3**（报错被吞为 `ok`） | 8（`INS-ORA-*`） | 0 | **不通过**（9/15 SQL 在 DM8 失效） |
| **GBase 8a** (`cid=11`) | 10 项（+13 项非适用 skip） | **4**（含 1 项变量空行 `0`） | 0 | **1**（`KeyError: 1`） | **5**（SQL 报错被吞为 `ok`） | 13（5 通用 + 8 ORA） | 0 | **不通过**（6/10 失败，1 项无 InnoDB 变量） |
| **MySQL 8.0.45** (`cid=19`) | 12 项（+11 项非适用 skip） | **7**（含 1 项状态变量空行 `0`） | 0（注：以下含 2 项原生 SQL 真实命中但检测器异常） | **1**（`KeyError: 1`） | **4**（2 项报错被吞 + 1 项假阴性漏报 + 1 项字段值错位） | 11（3 通用 + 8 ORA） | 0 | **不通过**（5/12 存在严重缺陷） |
| **PostgreSQL 16.13** (`db_monitor` + `cid=2`) | 12 项（+11 项非适用 skip） | **12** | 0 | 0 | 0 | 11（3 通用 + 8 ORA） | 0 | **通过**（12/12 正常可执行 + 11/11 优雅 skip） |
| **Oracle** | 23 项 | — | — | — | — | — | **23** | **未测·延后**（延至上线后内网复测） |

---

## 二、DM 分支实测明细（宿主 DM8 服务 `cid=9`）

- **实库信息**：库名 `DAMENG`，版本 `DM Database Server 64 V8 | DB Version: 0x7000d | 03134284488-20260330-320063-20200 | Msg Version: 1 | Gsu level(5) cnt: 102`，执行时间 `2026-11-13 10:30:48`（+08:00）。
- **辅助环境观察**：`cid=9` 的 `MonitorLog` 近 7d/14d 共有 `1588` 条记录；其中部分定时采集记录因后台 Python 3.11 `.venv` 缺少 `dmPython` 写入了 `DOWN {"error": "DM 检测器未注册：缺 dmPython"}`，而巡检检测器走 `pyodbc {DM8 ODBC DRIVER}` 均可直连 DM8。

### 2.1 DM8 目标 15 项逐项执行原文

| # | 规则编号 | SQL 原文 / 数据源（摘自 `inspection_executor.py`） | 原生 SQL 实测输出原文 | 检测器 `GenericDetector` 实库调用输出原文 | 真实判定 |
|---:|---|---|---|---|---|
| 1 | `INS-COM-USER-LOCKED` | `SELECT username, account_status FROM dba_users WHERE account_status LIKE '%LOCKED%'` | `ok`，`columns=["username","account_status"]`，`row_count=0`，`rows=[]` | `status="ok"`, `summary="锁定账号 0 个"`, `metrics={"locked_users": []}` | **未命中** |
| 2 | `INS-COM-PWD-EXPIRE` | `SELECT username, exp_datetime FROM dba_users WHERE exp_datetime IS NOT NULL AND exp_datetime BETWEEN SYSDATE AND SYSDATE + 7` | **`ERR`**: `ProgrammingError: ('42S22', '[42S22] 第1 行附近出现错误:\n无效的列名[exp_datetime] (-2111) (SQLExecDirectW)')` | **`status="error"`**, `error="('42S22', '[42S22] 第1 行附近出现错误:\n无效的列名[exp_datetime] (-2111) (SQLExecDirectW)')"` | **`error`**（DM8 `dba_users` 实为 `EXPIRY_DATE`） |
| 3 | `INS-COM-CAP-7D` | 平台 `MonitorLog` 7 日时序查询（无目标库 SQL）：`MonitorLog.objects.filter(config=ctx.db_config, create_time__gte=since)` | ORM 实测：`cid=9` 近 7d 样本 `count_7d=1588`（最新记录 `2026-11-13 02:30:12+00:00`） | `status="ok"`, `summary="7天容量采样 1588 点，容量增长趋势平稳"`, `metrics={"samples_count": 1588}` | **未命中**（样本点含缺驱动 DOWN 记录，另记 §七） |
| 4 | `INS-COM-CAP-30D` | 平台 `MonitorLog` 14 日时序查询（无目标库 SQL）：`MonitorLog.objects.filter(config=ctx.db_config, create_time__gte=since)` | ORM 实测：`cid=9` 近 14d 样本 `count_14d=1588` | `status="ok"`, `summary="30天容量采样 1588 点，长期容量水位健康"`, `metrics={"samples_count": 1588}` | **未命中** |
| 5 | `INS-COM-DEADLOCK-24H` | `SELECT COUNT(*) FROM V$DEADLOCK_HISTORY` | `ok`，`columns=["COUNT(*)"]`，`row_count=1`，`rows=[(0,)]` | `status="ok"`, `summary="死锁次数 0 次"`, `metrics={"deadlock_count": 0}` | **未命中** |
| 6 | `INS-COM-MISSING-INDEX` | `SELECT owner, table_name, num_rows FROM all_tables WHERE owner NOT IN ('SYS','SYSTEM','SYSSSO') AND num_rows > 10000 AND table_name NOT IN (SELECT DISTINCT table_name FROM all_indexes) LIMIT 10` | `ok`，`columns=["owner","table_name","num_rows"]`，`row_count=0`，`rows=[]` | `status="ok"`, `summary="建议补索引表 0 张"`, `metrics={"missing_index_candidates": []}` | **未命中** |
| 7 | `INS-COM-PART-NEEDED` | `SELECT table_name, ROUND(SUM(bytes)/1024/1024/1024, 2) AS size_gb FROM dba_segments WHERE segment_type = 'TABLE' GROUP BY table_name HAVING SUM(bytes) > 10 * 1024 * 1024 * 1024 FETCH FIRST 10 ROWS ONLY` | **`ERR`**: `ProgrammingError: ('42S22', '[42S22] 第6 行附近出现错误:\n无效的列名[table_name] (-2111) (SQLExecDirectW)')` | **`status="error"`**, `error="('42S22', '[42S22] 第8 行附近出现错误:\n无效的列名[table_name] (-2111) (SQLExecDirectW)')"` | **`error`**（DM8 `dba_segments` 列名为 `SEGMENT_NAME`） |
| 8 | `INS-COM-SPARSE-INDEX` | `SELECT owner, index_name, table_name, distinct_keys, num_rows, ROUND(distinct_keys/NULLIF(num_rows,0)*100, 2) AS sel_pct FROM all_indexes WHERE owner NOT IN ('SYS','SYSTEM') AND num_rows > 1000 AND distinct_keys > 0 AND distinct_keys/NULLIF(num_rows,0) < 0.05 LIMIT 10` | `ok`，`columns=["owner","index_name","table_name","distinct_keys","num_rows","sel_pct"]`，`row_count=0`，`rows=[]` | `status="ok"`, `summary="稀疏索引 0 个"`, `metrics={"sparse_indexes": []}` | **未命中** |
| 9 | `INS-COM-PARAM-DEV` | `SELECT PARA_NAME, PARA_VALUE FROM V$PARAMETER WHERE PARA_NAME IN ('MAX_SESSIONS', 'BUFFER')` | **`ERR`**: `ProgrammingError: ('42S22', '[42S22] 第1 行附近出现错误:\n无效的列名[PARA_NAME] (-2111) (SQLExecDirectW)')` | **`status="error"`**, `error="('42S22', '[42S22] 第1 行附近出现错误:\n无效的列名[PARA_NAME] (-2111) (SQLExecDirectW)')"` | **`error`**（DM8 `V$PARAMETER` 列名为 `NAME, VALUE`） |
| 10 | `INS-COM-ARCH-GAP` | `SELECT THREAD_ID, LOW_SEQ, HIGH_SEQ FROM V$ARCHIVE_GAP` | **`ERR`**: `ProgrammingError: ('42S02', '[42S02] 第1 行附近出现错误:\n无效的表或视图名[V$ARCHIVE_GAP] (-2106) (SQLExecDirectW)')` | ⚠️ **内层 `except` 吞错返回 `status="ok"`**, `summary="归档间隙 0 处"`, `metrics={"archive_gaps": []}` | **`error`（SQL 报错被吞，检测器假绿 `ok`）** |
| 11 | `INS-COM-LOCK-WAIT` | `SELECT SESS_ID, SQL_TEXT, BLOCKED FROM V$SESSIONS WHERE BLOCKED = 1` | **`ERR`**: `ProgrammingError: ('42S22', '[42S22] 第1 行附近出现错误:\n无效的列名[BLOCKED] (-2111) (SQLExecDirectW)')` | **`status="error"`**, `error="('42S22', '[42S22] 第1 行附近出现错误:\n无效的列名[BLOCKED] (-2111) (SQLExecDirectW)')"` | **`error`**（DM8 `V$SESSIONS` 无 `BLOCKED` 列） |
| 12 | `INS-COM-BLOCK-SESSION` | `SELECT SESS_ID, BLOCKER_SESS_ID, WAIT_TIME FROM V$SESSIONS WHERE BLOCKER_SESS_ID IS NOT NULL AND WAIT_TIME >= 300` | **`ERR`**: `ProgrammingError: ('42S22', '[42S22] 第3 行附近出现错误:\n无效的列名[BLOCKER_SESS_ID] (-2111) (SQLExecDirectW)')` | **`status="error"`**, `error="('42S22', '[42S22] 第5 行附近出现错误:\n无效的列名[BLOCKER_SESS_ID] (-2111) (SQLExecDirectW)')"` | **`error`**（DM8 `V$SESSIONS` 无 `BLOCKER_SESS_ID` / `WAIT_TIME`） |
| 13 | `INS-COM-OPEN-CURSOR` | SQL 1: `SELECT COUNT(*) FROM V$OPEN_TABLES`<br>SQL 2: `SELECT PARA_VALUE FROM V$PARAMETER WHERE PARA_NAME = 'MAX_OPEN_SESSIONS'` | SQL 1 **`ERR`**: `ProgrammingError: ('42S02', '[42S02] 第1 行附近出现错误:\n无效的表或视图名[V$OPEN_TABLES] (-2106) (SQLExecDirectW)')`<br>SQL 2 **`ERR`**: `ProgrammingError: ('42S22', '[42S22] 第1 行附近出现错误:\n无效的列名[PARA_NAME] (-2111) (SQLExecDirectW)')` | ⚠️ **两段 `except` 均吞错返回 `status="ok"`**, `summary="打开游标数 0 (上限 1000)"`, `metrics={"current_open": 0, "max_open": 1000}` | **`error`（双 SQL 报错被吞，返回硬编码假值）** |
| 14 | `INS-COM-TEMP-USAGE` | `SELECT tablespace_name, ROUND(SUM(bytes)/1024/1024, 2) total_mb, ROUND(SUM(user_bytes)/1024/1024, 2) used_mb FROM dba_temp_files GROUP BY tablespace_name` | **`ERR`**: `ProgrammingError: ('42S02', '[42S02] 第4 行附近出现错误:\n无效的表或视图名[dba_temp_files] (-2106) (SQLExecDirectW)')` | **`status="error"`**, `error="('42S02', '[42S02] 第6 行附近出现错误:\n无效的表或视图名[dba_temp_files] (-2106) (SQLExecDirectW)')"` | **`error`**（DM8 临时表空间见 `V$TABLESPACE WHERE TYPE$=2`） |
| 15 | `INS-COM-UNDO-USAGE` | `SELECT TABLESPACE_NAME, TOTAL_SIZE, FREE_SIZE FROM V$UNDO_SPACE` | **`ERR`**: `ProgrammingError: ('42S02', '[42S02] 第1 行附近出现错误:\n无效的表或视图名[V$UNDO_SPACE] (-2106) (SQLExecDirectW)')` | ⚠️ **内层 `except` 吞错返回 `status="ok"`**, `summary="Undo 使用率 0.0% 状态正常"` | **`error`（SQL 报错被吞，检测器假绿 `ok`）** |

### 2.2 DM8 非适用项实测验证（8 项 `INS-ORA-*`）

全部 8 项 Oracle 专属检测器（`INS-ORA-AWR-TOP-EVT`、`INS-ORA-AWR-TOP-SQL`、`INS-ORA-STA-OUTPUT`、`INS-ORA-SEG-ADV`、`INS-ORA-FRA`、`INS-ORA-CTRL-BAK`、`INS-ORA-ARCH-DELETE`、`INS-ORA-DBA-USERS`）在 DM8 实库调用均返回：`status="skip"`, `summary="不适用当前方言: dm (仅支持 oracle 专属特性)"`，耗时 `0ms`，判定均为 **`skip`**。

---

## 三、GBase 分支实测明细（GBase 8a `cid=11`, `127.0.0.1:5258`）

- **实库信息**：默认库 `None`（实例含系统库 `information_schema` / `performance_schema` / `gbase` / `gclusterdb` / `gctmpdb`），版本 `8.6.2.43-R7-free.110605`，执行时间 `2026-11-13 10:30:50`（+08:00），`@@sql_mode="PIPES_AS_CONCAT,ANSI_QUOTES,IGNORE_SPACE,ONLY_FULL_GROUP_BY,NO_AUTO_VALUE_ON_ZERO,STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION,PAD_CHAR_TO_FULL_LENGTH"`。
- **架构事实核验**：实库中**不存在 `mysql` 库**，用户表位于 `gbase.user`（且无 `account_locked`、`password_last_changed`、`password_lifetime` 列）；`information_schema` 下**无 InnoDB 系列表**（如 `innodb_lock_waits` / `innodb_trx` / `innodb_temp_table_info`）；同时平台 `DbConnector._connect_mysql` 给 `gbase` 固定开启 **`pymysql.cursors.DictCursor`**。

### 3.1 GBase 8a 目标 10 项逐项执行原文

| # | 规则编号 | SQL 原文 / 数据源（摘自 `inspection_executor.py`） | 原生 SQL 实测输出原文（tuple 游标） | 检测器 `GenericDetector` 实库调用输出原文（`DictCursor`） | 真实判定 |
|---:|---|---|---|---|---|
| 1 | `INS-COM-USER-LOCKED` | `SELECT user, host, account_locked FROM mysql.user WHERE account_locked = 'Y'` | **`ERR`**: `ProgrammingError: (1146, "Table 'mysql.user' doesn't exist")` | ⚠️ **内层 `except` 吞错返回 `status="ok"`**, `summary="锁定账号 0 个"`, `metrics={"locked_users": []}` | **`error`（表 `mysql.user` 不存在，报错被吞）** |
| 2 | `INS-COM-PWD-EXPIRE` | `SELECT user, host, password_last_changed, password_lifetime FROM mysql.user WHERE password_lifetime IS NOT NULL AND password_lifetime > 0` | **`ERR`**: `ProgrammingError: (1146, "Table 'mysql.user' doesn't exist")` | ⚠️ **内层 `except` 吞错返回 `status="ok"`**, `summary="即将过期密码账号 0 个"`, `metrics={"expiring_users": []}` | **`error`（表 `mysql.user` 不存在，报错被吞）** |
| 3 | `INS-COM-CAP-7D` | 平台 `MonitorLog` 7 日时序查询（无目标库 SQL） | ORM 实测：`cid=11` 近 7d 样本 `count_7d=8673`（最新 `2026-11-13 02:30:15+00:00`, `UP`） | `status="ok"`, `summary="7天容量采样 8673 点，容量增长趋势平稳"`, `metrics={"samples_count": 8673}` | **未命中** |
| 4 | `INS-COM-CAP-30D` | 平台 `MonitorLog` 14 日时序查询（无目标库 SQL） | ORM 实测：`cid=11` 近 14d 样本 `count_14d=9514` | `status="ok"`, `summary="30天容量采样 9514 点，长期容量水位健康"`, `metrics={"samples_count": 9514}` | **未命中** |
| 5 | `INS-COM-DEADLOCK-24H` | `SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks'` | `ok`，`columns=["Variable_name","Value"]`，`row_count=0`，`rows=[]`（GBase 8a 无 InnoDB 引擎，变量不存在故返回空集） | `status="ok"`, `summary="死锁次数 0 次"`, `metrics={"deadlock_count": 0}` | **未命中**（注：GBase 无此变量，结果恒为 0） |
| 6 | `INS-COM-PART-NEEDED` | `SELECT table_schema, table_name, ROUND((data_length + index_length)/1024/1024/1024, 2) AS size_gb FROM information_schema.tables WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') AND (data_length + index_length) > 10 * 1024 * 1024 * 1024 AND table_name NOT IN (SELECT DISTINCT table_name FROM information_schema.partitions WHERE partition_name IS NOT NULL) LIMIT 10` | `ok`，`columns=["table_schema","table_name","size_gb"]`，`row_count=0`，`rows=[]` | `status="ok"`, `summary="大表未分区 0 张"`, `metrics={"unpartitioned_large_tables": []}` | **未命中**（注：0 行掩盖了 `dict(zip(cols, r))` 在 `DictCursor` 下的隐患） |
| 7 | `INS-COM-PARAM-DEV` | `SHOW VARIABLES LIKE 'max_connections'` | `ok`，`columns=["Variable_name","Value"]`，`row_count=1`，`rows=[["max_connections", "100"]]` | **`status="error"`**, `error="1"`（即 `DictCursor` 下 `int(row[1])` 抛出 **`KeyError: 1`**） | **`error`**（SQL 正常，检测器游标取值 Bug） |
| 8 | `INS-COM-LOCK-WAIT` | `SELECT r.trx_id waiting_trx_id, r.trx_mysql_thread_id waiting_thread, b.trx_id blocking_trx_id FROM information_schema.innodb_lock_waits w JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id` | **`ERR`**: `OperationalError: (1109, "Unknown table 'innodb_lock_waits' in information_schema")` | ⚠️ **内层 `except` 吞错返回 `status="ok"`**, `summary="锁等待会话 0 个"`, `metrics={"lock_waits": []}` | **`error`（表不存在，报错被吞）** |
| 9 | `INS-COM-BLOCK-SESSION` | `SELECT r.trx_mysql_thread_id, TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_sec FROM information_schema.innodb_trx r WHERE r.trx_wait_started IS NOT NULL AND TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) >= 300` | **`ERR`**: `OperationalError: (1109, "Unknown table 'innodb_trx' in information_schema")` | ⚠️ **内层 `except` 吞错返回 `status="ok"`**, `summary="长阻塞会话 0 个"`, `metrics={"blocked_sessions": []}` | **`error`（表不存在，报错被吞）** |
| 10 | `INS-COM-TEMP-USAGE` | `SELECT ROUND(SUM(allocated)/1024/1024, 2) AS temp_mb FROM information_schema.innodb_temp_table_info` | **`ERR`**: `OperationalError: (1109, "Unknown table 'innodb_temp_table_info' in information_schema")` | ⚠️ **内层 `except` 吞错返回 `status="ok"`**, `summary="临时表空间使用率 0.0%"`, `metrics={"temp_mb": 0, "pct_used": 0.0}` | **`error`（表不存在，报错被吞）** |

### 3.2 GBase 8a 非适用项实测验证（13 项）

- 通用 5 项在 GBase 实库调用均返回 `status="skip"`：
  - `INS-COM-MISSING-INDEX`：`不适用当前方言: gbase (仅支持 oracle, mysql, pgsql, dm)`
  - `INS-COM-SPARSE-INDEX`：`不适用当前方言: gbase (仅支持 oracle, mysql, pgsql, dm)`
  - `INS-COM-ARCH-GAP`：`不适用当前方言: gbase (仅支持 oracle, dm 归档架构)`
  - `INS-COM-OPEN-CURSOR`：`不适用当前方言: gbase (仅支持 oracle, dm)`
  - `INS-COM-UNDO-USAGE`：`不适用当前方言: gbase (仅支持 oracle, dm)`
- Oracle 专属 8 项（`INS-ORA-*`）：均返回 `status="skip"`, `summary="不适用当前方言: gbase (仅支持 oracle 专属特性)"`。

---

## 四、MySQL 分支实测明细（真实 MySQL 8.0.45，`cid=19`，`13306`）

- **实库信息**：库名 `aiops_probe_shop`，版本 `8.0.45`，执行时间 `2026-11-13 10:30:51`（+08:00），`@@sql_mode="ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"`。
- **重要实测发现**：MySQL 8.0.45 实库下，`DbConnector._connect_mysql` 返回的 **`DictCursor`** 与 `GenericDetector` 代码的 tuple 下标 / `zip` 假定严重冲突，直接酿成了 **1 例假阴性漏报（真实锁定账号被漏报为 0）**、**1 例结构体字段名错位** 和 **1 例直接 `KeyError` 崩溃**；同时 MySQL 8.0 已移除 `information_schema.innodb_lock_waits`，且 `innodb_temp_table_info` 无 `allocated` 列。

### 4.1 MySQL 8.0.45 目标 12 项逐项执行原文

| # | 规则编号 | SQL 原文 / 数据源（摘自 `inspection_executor.py`） | 原生 SQL 实测输出原文（tuple 游标） | 检测器 `GenericDetector` 实库调用输出原文（`DictCursor`） | 真实判定 |
|---:|---|---|---|---|---|
| 1 | `INS-COM-USER-LOCKED` | `SELECT user, host, account_locked FROM mysql.user WHERE account_locked = 'Y'` | **`ok`，真实命中 `row_count=3`**:<br>`[["mysql.infoschema","localhost","Y"], ["mysql.session","localhost","Y"], ["mysql.sys","localhost","Y"]]` | ⚠️ **触发假阴性漏报**：`status="ok"`, `summary="锁定账号 0 个"`, `metrics={"locked_users": []}`（原因：`DictCursor` 游标下 `r[0]` 抛出 `KeyError: 0`，被内层 `except Exception: rows = []` 吞掉） | **`error`（实库真实命中 3 个锁定账号，检测器吞错漏报成 `ok`）** |
| 2 | `INS-COM-PWD-EXPIRE` | `SELECT user, host, password_last_changed, password_lifetime FROM mysql.user WHERE password_lifetime IS NOT NULL AND password_lifetime > 0` | `ok`，`columns=["user","host","password_last_changed","password_lifetime"]`，`row_count=0`，`rows=[]` | `status="ok"`, `summary="即将过期密码账号 0 个"`, `metrics={"expiring_users": []}`（注：若后续真有行也会因 `r[0]` 触发 `KeyError` 被吞） | **未命中**（当前 0 行；存在代码隐患见 §七） |
| 3 | `INS-COM-CAP-7D` | 平台 `MonitorLog` 7 日时序查询（无目标库 SQL） | ORM 实测：`cid=19` 近 7d 样本 `count_7d=5130`（最新 `2026-11-13 02:29:18+00:00`, `UP`） | `status="ok"`, `summary="7天容量采样 5130 点，容量增长趋势平稳"`, `metrics={"samples_count": 5130}` | **未命中** |
| 4 | `INS-COM-CAP-30D` | 平台 `MonitorLog` 14 日时序查询（无目标库 SQL） | ORM 实测：`cid=19` 近 14d 样本 `count_14d=5130` | `status="ok"`, `summary="30天容量采样 5130 点，长期容量水位健康"`, `metrics={"samples_count": 5130}` | **未命中** |
| 5 | `INS-COM-DEADLOCK-24H` | `SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks'` | `ok`，`columns=["Variable_name","Value"]`，`row_count=0`，`rows=[]`（MySQL 8.0 无 `Innodb_deadlocks` 状态变量，对照查 `information_schema.INNODB_METRICS` 的 `lock_deadlocks` 存在且当前 `COUNT=0`） | `status="ok"`, `summary="死锁次数 0 次"`, `metrics={"deadlock_count": 0}` | **未命中**（注：状态变量在 8.0 不存在导致恒返回 0） |
| 6 | `INS-COM-MISSING-INDEX` | `SELECT object_schema, object_name, count_read, count_write FROM performance_schema.table_io_waits_summary_by_table WHERE count_star > 1000 AND count_read > count_write * 10 AND object_schema NOT IN ('mysql', 'sys', 'performance_schema', 'information_schema') LIMIT 10` | `ok`，`columns=["object_schema","object_name","count_read","count_write"]`，`row_count=0`，`rows=[]` | `status="ok"`, `summary="建议补索引表 0 张"`, `metrics={"missing_index_candidates": []}`（注：若返回非空行同样存在 `dict(zip(cols, r))` 值错位隐患） | **未命中**（当前 0 行） |
| 7 | `INS-COM-PART-NEEDED` | `SELECT table_schema, table_name, ROUND((data_length + index_length)/1024/1024/1024, 2) AS size_gb FROM information_schema.tables WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') AND (data_length + index_length) > 10 * 1024 * 1024 * 1024 AND table_name NOT IN (SELECT DISTINCT table_name FROM information_schema.partitions WHERE partition_name IS NOT NULL) LIMIT 10` | `ok`，`columns=["TABLE_SCHEMA","TABLE_NAME","size_gb"]`，`row_count=0`，`rows=[]` | `status="ok"`, `summary="大表未分区 0 张"`, `metrics={"unpartitioned_large_tables": []}` | **未命中**（当前 0 行） |
| 8 | `INS-COM-SPARSE-INDEX` | `SELECT s.table_schema, s.table_name, s.index_name, MAX(s.cardinality) AS cardinality, t.table_rows FROM information_schema.statistics s JOIN information_schema.tables t ON s.table_schema=t.table_schema AND s.table_name=t.table_name WHERE s.table_schema NOT IN ('mysql','information_schema','performance_schema','sys') AND t.table_rows > 1000 GROUP BY s.table_schema, s.table_name, s.index_name, t.table_rows HAVING cardinality > 0 AND cardinality/NULLIF(table_rows,0) < 0.05 LIMIT 10` | **`ok`，真实命中 `row_count=10`**，前 3 行原文：<br>`["tdsql_sqlcheck_test","inspection_results","idx_ir_category",5,2723]`<br>`["tdsql_sqlcheck_test","inspection_results","idx_ir_severity",3,2723]`<br>`["tdsql_sqlcheck_test","role_permissions","idx_rp_role",158,5441]`<br>（含 `operation_logs`、`scan_snapshots`、`inspection_tasks` 共 10 行） | ⚠️ **触发告警但数据结构全错位**：`status="warning"`, `summary="稀疏索引 10 个"`, `findings[0]["details"][0]` 实得：`{"table_schema": "TABLE_SCHEMA", "table_name": "TABLE_NAME", "index_name": "INDEX_NAME", "cardinality": "cardinality", "table_rows": "TABLE_ROWS"}`（真实库名/表名/基数全丢失） | **`error`（实库命中 10 行但 `DictCursor` + `zip` 导致值错位成列名字符串）** |
| 9 | `INS-COM-PARAM-DEV` | `SHOW VARIABLES LIKE 'max_connections'` | `ok`，`columns=["Variable_name","Value"]`，`row_count=1`，`rows=[["max_connections","151"]]` | **`status="error"`**, `error="1"`（即 `DictCursor` 下 `int(row[1])` 抛出 **`KeyError: 1`**） | **`error`**（SQL 正常，检测器游标取值 Bug） |
| 10 | `INS-COM-LOCK-WAIT` | `SELECT r.trx_id waiting_trx_id, r.trx_mysql_thread_id waiting_thread, b.trx_id blocking_trx_id FROM information_schema.innodb_lock_waits w JOIN information_schema.innodb_trx b ON b.trx_id = w.blocking_trx_id JOIN information_schema.innodb_trx r ON r.trx_id = w.requesting_trx_id` | **`ERR`**: `OperationalError: (1109, "Unknown table 'INNODB_LOCK_WAITS' in information_schema")`（对照：MySQL 8.0 `performance_schema.data_lock_waits` 存在，当前 `cnt=0`） | ⚠️ **内层 `except` 吞错返回 `status="ok"`**, `summary="锁等待会话 0 个"`, `metrics={"lock_waits": []}` | **`error`（MySQL 8.0 已移除 `INNODB_LOCK_WAITS`，报错被吞）** |
| 11 | `INS-COM-BLOCK-SESSION` | `SELECT r.trx_mysql_thread_id, TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_sec FROM information_schema.innodb_trx r WHERE r.trx_wait_started IS NOT NULL AND TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) >= 300` | `ok`，`columns=["trx_mysql_thread_id","wait_sec"]`，`row_count=0`，`rows=[]` | `status="ok"`, `summary="长阻塞会话 0 个"`, `metrics={"blocked_sessions": []}` | **未命中** |
| 12 | `INS-COM-TEMP-USAGE` | `SELECT ROUND(SUM(allocated)/1024/1024, 2) AS temp_mb FROM information_schema.innodb_temp_table_info` | **`ERR`**: `OperationalError: (1054, "Unknown column 'allocated' in 'field list'")`（实查该表仅含 `['TABLE_ID','NAME','N_COLS','SPACE']` 4 列） | ⚠️ **内层 `except` 吞错返回 `status="ok"`**, `summary="临时表空间使用率 0.0%"`, `metrics={"temp_mb": 0, "pct_used": 0.0}` | **`error`（列 `allocated` 不存在，报错被吞）** |

### 4.2 MySQL 8.0.45 非适用项实测验证（11 项）

- 通用 3 项在 MySQL 8.0.45 实库调用均返回 `status="skip"`：
  - `INS-COM-ARCH-GAP`：`不适用当前方言: mysql (仅支持 oracle, dm 归档架构)`
  - `INS-COM-OPEN-CURSOR`：`不适用当前方言: mysql (仅支持 oracle, dm)`
  - `INS-COM-UNDO-USAGE`：`不适用当前方言: mysql (仅支持 oracle, dm)`
- Oracle 专属 8 项（`INS-ORA-*`）：均返回 `status="skip"`, `summary="不适用当前方言: mysql (仅支持 oracle 专属特性)"`。

---

## 五、PostgreSQL 分支实测明细（本机 PG 16.13，`db_monitor` + `cid=2 timeseriesdb`）

- **实库信息**：
  - **主对象 `db_monitor`**：版本 `PostgreSQL 16.13 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit`，执行时间 `2026-11-13 02:30:51 UTC`（库内含 137 张真实业务/平台用户表，见 `pg_stat_user_tables`）；
  - **时序对照对象 `timeseriesdb`（`DatabaseConfig cid=2`）**：同宿主机 PG 16.13 实例，执行时间 `2026-11-13 02:30:52 UTC`，`MonitorLog` 近 7d/14d 样本分别有 `7058` / `7899` 条（最新 `2026-11-13 02:28:30+00:00`, `UP`）。

### 5.1 PostgreSQL 16.13 目标 12 项逐项执行原文

| # | 规则编号 | SQL 原文 / 数据源（摘自 `inspection_executor.py`） | 原生 SQL 实测输出原文（`db_monitor` / `cid=2` 对照一致） | 检测器 `GenericDetector` 实库调用输出原文 | 真实判定 |
|---:|---|---|---|---|---|
| 1 | `INS-COM-USER-LOCKED` | `SELECT rolname FROM pg_roles WHERE rolvaliduntil IS NOT NULL AND rolvaliduntil < NOW()` | `db_monitor`: `ok`, `columns=["rolname"]`, `row_count=0`, `rows=[]` | `status="ok"`, `summary="锁定/过期账号 0 个 (PG 采用有效期检查)"`, `metrics={"expired_or_locked_users": []}` | **未命中** |
| 2 | `INS-COM-PWD-EXPIRE` | `SELECT rolname, rolvaliduntil FROM pg_roles WHERE rolvaliduntil IS NOT NULL AND rolvaliduntil BETWEEN NOW() AND NOW() + INTERVAL '7 days'` | `db_monitor`: `ok`, `columns=["rolname","rolvaliduntil"]`, `row_count=0`, `rows=[]` | `status="ok"`, `summary="即将过期密码账号 0 个"`, `metrics={"expiring_users": []}` | **未命中** |
| 3 | `INS-COM-CAP-7D` | 平台 `MonitorLog` 7 日时序查询（无目标库 SQL；`db_monitor` 未独立纳管无 `cid`，由同实例 `cid=2 timeseriesdb` 实测） | ORM 实测：`cid=2` 近 7d 样本 `count_7d=7058` | `cid=2`: `status="ok"`, `summary="7天容量采样 7058 点，容量增长趋势平稳"`, `metrics={"samples_count": 7058}` | **未命中** |
| 4 | `INS-COM-CAP-30D` | 平台 `MonitorLog` 14 日时序查询（同实例 `cid=2 timeseriesdb` 实测） | ORM 实测：`cid=2` 近 14d 样本 `count_14d=7899` | `cid=2`: `status="ok"`, `summary="30天容量采样 7899 点，长期容量水位健康"`, `metrics={"samples_count": 7899}` | **未命中** |
| 5 | `INS-COM-DEADLOCK-24H` | `SELECT COALESCE(SUM(deadlocks), 0) FROM pg_stat_database` | `db_monitor`: `ok`, `columns=["coalesce"]`, `row_count=1`, `rows=[["0"]]` | `status="ok"`, `summary="死锁次数 0 次"`, `metrics={"deadlock_count": 0}` | **未命中** |
| 6 | `INS-COM-MISSING-INDEX` | `SELECT schemaname\|\|'.'\|\|relname AS tblname, seq_scan, COALESCE(idx_scan, 0) AS idx_scan FROM pg_stat_user_tables WHERE seq_scan > 1000 AND (idx_scan IS NULL OR idx_scan = 0 OR seq_scan > idx_scan * 5) ORDER BY seq_scan DESC LIMIT 10` | `db_monitor`（137 张用户表）: `ok`, `columns=["tblname","seq_scan","idx_scan"]`, `row_count=0`, `rows=[]` | `status="ok"`, `summary="建议补索引表 0 张"`, `metrics={"missing_index_candidates": []}` | **未命中** |
| 7 | `INS-COM-PART-NEEDED` | `SELECT schemaname\|\|'.'\|\|relname AS tblname, ROUND(pg_total_relation_size(relid)/1024/1024/1024, 2) AS size_gb FROM pg_catalog.pg_statio_user_tables WHERE pg_total_relation_size(relid) > 10::bigint * 1024 * 1024 * 1024 AND relid NOT IN (SELECT inhrelid FROM pg_inherits UNION SELECT partrelid FROM pg_partitioned_table) LIMIT 10` | `db_monitor`: `ok`, `columns=["tblname","size_gb"]`, `row_count=0`, `rows=[]` | `status="ok"`, `summary="大表未分区 0 张"`, `metrics={"unpartitioned_large_tables": []}` | **未命中** |
| 8 | `INS-COM-SPARSE-INDEX` | `SELECT schemaname\|\|'.'\|\|tablename AS tbl, attname, n_distinct FROM pg_stats WHERE n_distinct > 0 AND n_distinct < 0.05 AND schemaname NOT IN ('pg_catalog', 'information_schema') LIMIT 10` | `db_monitor`: `ok`, `columns=["tbl","attname","n_distinct"]`, `row_count=0`, `rows=[]` | `status="ok"`, `summary="稀疏索引 0 个"`, `metrics={"sparse_indexes": []}` | **未命中** |
| 9 | `INS-COM-PARAM-DEV` | `SHOW max_connections` | `db_monitor`: `ok`, `columns=["max_connections"]`, `row_count=1`, `rows=[["100"]]` | `status="ok"`, `summary="偏离参数 0 项"`, `metrics={"deviations": []}` | **未命中** |
| 10 | `INS-COM-LOCK-WAIT` | `SELECT pid, wait_event_type, wait_event, state FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND state = 'active'` | `db_monitor`: `ok`, `columns=["pid","wait_event_type","wait_event","state"]`, `row_count=0`, `rows=[]` | `status="ok"`, `summary="锁等待会话 0 个"`, `metrics={"lock_waits": []}` | **未命中** |
| 11 | `INS-COM-BLOCK-SESSION` | `SELECT pid, usename, EXTRACT(EPOCH FROM (now() - query_start)) AS duration_sec FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND (now() - query_start) >= INTERVAL '5 minutes'` | `db_monitor`: `ok`, `columns=["pid","usename","duration_sec"]`, `row_count=0`, `rows=[]` | `status="ok"`, `summary="长阻塞会话 0 个"`, `metrics={"blocked_sessions": []}` | **未命中** |
| 12 | `INS-COM-TEMP-USAGE` | `SELECT ROUND(SUM(temp_bytes)/1024/1024, 2) AS temp_mb FROM pg_stat_database` | `db_monitor`: `ok`, `columns=["temp_mb"]`, `row_count=1`, `rows=[["0.00"]]` | `status="ok"`, `summary="临时表空间使用率 0.0%"`, `metrics={"temp_mb": 0, "pct_used": 0.0}` | **未命中** |

> 补充对照：`timeseriesdb`（`cid=2`）上上述 10 条 SQL 执行结果（`columns`、`row_count`、`rows`）与 `db_monitor` 完全一致（均为 `ok` 无异常）。

### 5.2 PostgreSQL 16.13 非适用项实测验证（11 项，含注册表含 `pgsql` 的 `INS-COM-UNDO-USAGE`）

| 规则编号 | 实测 `GenericDetector` 返回原文 | 真实判定 |
|---|---|---|
| `INS-COM-ARCH-GAP` | `status="skip"`, `summary="不适用当前方言: pgsql (仅支持 oracle, dm 归档架构)"` | **`skip`** |
| `INS-COM-OPEN-CURSOR` | `status="skip"`, `summary="不适用当前方言: pgsql (仅支持 oracle, dm)"` | **`skip`** |
| `INS-COM-UNDO-USAGE` | `status="skip"`, `summary="不适用当前方言: pgsql 采用堆内 MVCC 行版本机制，无独立 Undo 表空间"`（注册表将其含于 `applicable_db_types`，运行时按报告 §一原则优雅跳过） | **`skip`** |
| `INS-ORA-*` 其余 8 项 | 均返回 `status="skip"`, `summary="不适用当前方言: pgsql (仅支持 oracle 专属特性)"` | **`skip`** |

---

## 六、Oracle 分支（23 项全部：未测·延后）

外网无真实 Oracle 实例，按任务单要求不做执行、不以本地 XE 或单测 `skip` 充数，23 项逐项均标注为 **未测·延后**：

| # | 规则编号 | 规则标题 | 注册方法 (`GenericDetector.*`) | Oracle 分支当前状态 | 计划执行条件 |
|---:|---|---|---|---|---|
| 1 | `INS-COM-USER-LOCKED` | 账号锁定检查 | `detect_locked_users` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 2 | `INS-COM-PWD-EXPIRE` | 密码即将过期 | `detect_expiring_passwords` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 3 | `INS-COM-CAP-7D` | 7天容量预测 | `detect_7d_capacity` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 4 | `INS-COM-CAP-30D` | 30天容量预测 | `detect_30d_capacity` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 5 | `INS-COM-DEADLOCK-24H` | 24h 死锁次数 | `detect_deadlock_count` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 6 | `INS-COM-MISSING-INDEX` | 缺失索引检查 | `detect_missing_indexes` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 7 | `INS-COM-PART-NEEDED` | 大表未分区 | `detect_large_table_not_partitioned` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 8 | `INS-COM-SPARSE-INDEX` | 稀疏索引 | `detect_sparse_index` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 9 | `INS-COM-PARAM-DEV` | 参数偏离最佳实践 | `detect_param_deviation` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 10 | `INS-COM-ARCH-GAP` | 归档间隙 | `detect_archive_gap` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 11 | `INS-COM-LOCK-WAIT` | 当前锁等待 | `detect_lock_waits` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 12 | `INS-COM-BLOCK-SESSION` | 被阻塞会话 | `detect_blocked_sessions` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 13 | `INS-COM-OPEN-CURSOR` | 打开游标数 | `detect_open_cursors` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 14 | `INS-COM-TEMP-USAGE` | 临时表空间使用 | `detect_temp_usage` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 15 | `INS-COM-UNDO-USAGE` | Undo 使用率 | `detect_undo_usage` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 16 | `INS-ORA-AWR-TOP-EVT` | 业务高峰 Top Event | `detect_awr_top_event` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 17 | `INS-ORA-AWR-TOP-SQL` | 业务高峰 Top SQL | `detect_awr_top_sql` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 18 | `INS-ORA-STA-OUTPUT` | SQL Tuning Advisor 结果 | `detect_sta_recommendations` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 19 | `INS-ORA-SEG-ADV` | Segment Advisor 结果 | `detect_segment_advisor` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 20 | `INS-ORA-FRA` | FRA 使用率 | `detect_fra_usage` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 21 | `INS-ORA-CTRL-BAK` | 控制文件备份 | `detect_ctrlfile_backup` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 22 | `INS-ORA-ARCH-DELETE` | 归档日志清理策略 | `detect_arch_deletion_policy` | **未测·延后** | 外网无真实库，延至上线后内网复测 |
| 23 | `INS-ORA-DBA-USERS` | DBA 权限账号 | `detect_dba_users` | **未测·延后** | 外网无真实库，延至上线后内网复测 |

---

## 七、实库暴露缺陷汇总（建议另立修复单，本单按约束未修改代码）

1. **系统性根因 A：`DbConnector._connect_mysql` 默认 `DictCursor` 与 `GenericDetector` 元组下标/zip 不兼容（影响 MySQL / GBase / TDSQL）**
   - `monitor/db_connector.py` 第 143 行对 `mysql`/`gbase`/`tdsql` 统一设置 `cursorclass=pymysql.cursors.DictCursor`。
   - `GenericDetector` 却全部按 tuple 方式取数：
     - `r[0]` / `r[1]` / `row[1]`（`detect_locked_users`、`detect_expiring_passwords`、`detect_deadlock_count`、`detect_param_deviation`、`detect_temp_usage`）在 `DictCursor` 下直接抛出 `KeyError: 0/1`；
     - `dict(zip(cols, r))`（`detect_missing_indexes`、`detect_large_table_not_partitioned`、`detect_sparse_index`、`detect_lock_waits`、`detect_blocked_sessions`）当 `r` 为 `dict` 时把**字典 key** 而非 value 压缩进结果，已在 MySQL `INS-COM-SPARSE-INDEX` 实测出所有返回值错位成 `"TABLE_SCHEMA"` 等列名字符串。
   - 同时直接导致 `INS-COM-USER-LOCKED` 在 MySQL 8.0.45 上产生**真实假阴性漏报**（实库有 3 个锁定账号，被吞成 0 个）。

2. **系统性根因 B：内层 `except Exception:` 静默吞错导致“假绿 `ok`”**
   - `detect_locked_users`、`detect_expiring_passwords`、`detect_deadlock_count`、`detect_missing_indexes`、`detect_archive_gap`、`detect_lock_waits`、`detect_blocked_sessions`、`detect_open_cursors`、`detect_temp_usage`、`detect_undo_usage` 均存在裸 `except Exception` 直接置 `rows=[]` 或 `0` 而不记录 `error` / `degrade.note`，掩盖了 DM8 3 项、GBase 5 项、MySQL 3 项真实异常（共 11 处假绿/假阴性）。

3. **DM8 方言 SQL 失配 9 项（均已在宿主 DM8 实测暴露）**
   - `INS-COM-PWD-EXPIRE`：DM8 `dba_users` 列名为 `EXPIRY_DATE`，非 `exp_datetime`（`-2111`）。
   - `INS-COM-PART-NEEDED`：DM8 `dba_segments` 对象列名为 `SEGMENT_NAME`，非 `table_name`（`-2111`）。
   - `INS-COM-PARAM-DEV`：DM8 `V$PARAMETER` 列名为 `NAME`、`VALUE`，非 `PARA_NAME`、`PARA_VALUE`（`-2111`）。
   - `INS-COM-ARCH-GAP`：DM8 无 `V$ARCHIVE_GAP` 视图（`-2106`；可参考 DB-009 的 `V$ARCH_STATUS`/`V$ARCH_FILE` 口径）。
   - `INS-COM-LOCK-WAIT`、`INS-COM-BLOCK-SESSION`：DM8 `V$SESSIONS` 57 列中无 `BLOCKED`、`BLOCKER_SESS_ID`、`WAIT_TIME`（`-2111`；参见 DB-009 已验证的 `V$LOCK` / `V$TRX` 关联口径）。
   - `INS-COM-OPEN-CURSOR`：DM8 无 `V$OPEN_TABLES`（`-2106`），且 `V$PARAMETER` 无 `PARA_NAME`（`-2111`）。
   - `INS-COM-TEMP-USAGE`：DM8 无 `dba_temp_files`（`-2106`；实测 `SELECT NAME, TYPE$, TOTAL_SIZE, USED_SIZE FROM V$TABLESPACE WHERE TYPE$=2` 可正常返回 `[('TEMP', 2, 4096, 1072)]`）。
   - `INS-COM-UNDO-USAGE`：DM8 无 `V$UNDO_SPACE`（`-2106`）；且函数内部 `try` 块即便拿到行也未更新 `pct_used`。

4. **GBase 8a 方言 SQL 失配 6 项 + 1 项变量空行**
   - GBase 8a 实库**无 `mysql` 库**，用户表为 `gbase.user` 且无 `account_locked`/`password_lifetime` 列，`INS-COM-USER-LOCKED`、`INS-COM-PWD-EXPIRE` 均报 `(1146, "Table 'mysql.user' doesn't exist")`。
   - GBase 8a **无 InnoDB 引擎**：`INS-COM-LOCK-WAIT`、`INS-COM-BLOCK-SESSION`、`INS-COM-TEMP-USAGE` 分别因缺 `innodb_lock_waits` / `innodb_trx` / `innodb_temp_table_info` 报 `1109`；`INS-COM-DEADLOCK-24H` 查 `Innodb_deadlocks` 返回 0 行（应考虑与能力边界对齐或明确标记 `skip`）。

5. **MySQL 8.0.45 方言 SQL 失配 2 项 + 1 项状态变量空行**
   - `INS-COM-LOCK-WAIT`：MySQL 8.0 已移除 `information_schema.innodb_lock_waits`（`1109`），应改查 `performance_schema.data_lock_waits`（实测 `cnt=0` 可用）。
   - `INS-COM-TEMP-USAGE`：MySQL 8.0 `information_schema.innodb_temp_table_info` 仅含 `TABLE_ID, NAME, N_COLS, SPACE` 4 列，无 `allocated` 列（`1054`）。
   - `INS-COM-DEADLOCK-24H`：`SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks'` 在 MySQL 8.0.45 返回 0 行（实测 `information_schema.INNODB_METRICS WHERE NAME='lock_deadlocks'` 有计数）。

6. **容量预测 (`INS-COM-CAP-7D` / `INS-COM-CAP-30D`) 观察**
   - 仅统计 `MonitorLog` 总行数（未过滤 `status='UP'`，如 DM `cid=9` 的 1588 条中含缺驱动 `DOWN` 记录），也不读取历史指标字段做实际容量斜率外推；另需注意被纳管库与元数据库区分（`db_monitor` 本身不在 `DatabaseConfig` 中）。

---

## 八、验证与约束自检

- 未改动任何业务代码、检测器逻辑与注册表；未改动 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`。
- 本机无新增仓库临时脚本（只读探测脚本存放于会话 scratch 目录并保留 JSON 原文对照）。
- 全套密钥扫描、红线扫描与单元测试验证结果如下（提交前实跑）：
  - `python3 scripts/scan_secrets.py` → `密钥扫描通过`
  - `python3 scripts/lint_redlines.py` → `红线约束扫描通过`
  - `bash scripts/validate.sh unit --json` → `{"mode":"unit","exit_code":0,"stages":[{"name":"红线扫描","status":"pass","duration_ms":5060},{"name":"unit 测试","status":"pass","duration_ms":14745}],"git_rev":"3be36ba"}`
