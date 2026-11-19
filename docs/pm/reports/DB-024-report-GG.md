通过

# DB-024：修复 DB-022 检测器实库缺陷 交付报告

- **任务单**：`docs/pm/orders/DB-024-修复-DB-022-检测器实库缺陷.md` (v1.0)
- **接单回执**：`docs/pm/reports/DB-024-receipt-GG.md`（已直推 `origin/main`，commit `5c13952`）
- **前序依据**：`docs/pm/reports/DB-022-补充-实库补证-report-GG.md`
- **执行人**：GG
- **交付时间**：2026-11-13 12:15 (+08:00)
- **改动文件清单**：
  - `monitor/inspection_executor.py`
  - `monitor/tests_inspection_scope.py`
  - `docs/pm/reports/DB-024-report-GG.md`（本报告）
- **边界遵循声明**：未修改 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`、L81-L1283 既有基线检测方法、`Batch010bDetector`，亦未修改既有单元测试断言。

---

## 一、修复范围与架构设计结论

### 1.1 根因 A（`DictCursor` 兼容）与 `DbConnector` 调用方审计

对 `monitor/` 与 `dbmonitor/` 全量检索 `DbConnector.get_connection` 与 `DictCursor` 后确认：

1. `monitor/db_connector.py`（`L112`, `L143`, `L284`）在 `_connect_mysql` 中显式配置 `cursorclass=pymysql.cursors.DictCursor`，使 MySQL / GBase / TDSQL 游标返回 `dict` 行（而非位置元组）。
2. 下游已有多个核心模块依赖或显式适配 `DictCursor`：
   - `monitor/bank_simulator/worker.py`、`monitor/inspection_detectors_010b.py`、`monitor/slow_query_engine.py`（T-01 已做兼容）、`monitor/sqlstat_collect.py`、`monitor/checkers/{gbase,mysql,tdsql}.py`；
   - 以及通过 `DbConnector.get_connection(...)` 访问目标库的 `monitor/api_views_incident.py`、`monitor/api_views_perf.py`、`monitor/api_views_v2.py`、`monitor/exceptions.py`、`monitor/plan_capture.py`、`monitor/playbook_engine.py`、`monitor/sentinel.py`、`monitor/sql_explain.py`、`monitor/verify_loop.py`、`monitor/management/commands/{start_monitor,rotate_db_passwords}.py`。
3. **结论**：**保持 `DbConnector._connect_mysql` 继续返回 `pymysql.cursors.DictCursor` 不变**（零回归风险，不影响 ASH / SlowSQL / Playbook 等全部现有调用方）；在 `monitor/inspection_executor.py` 的 `GenericDetector` 中新增游标无关适配层（`L1306-L1622`），消除位置下标 `r[0]/r[1]` 与 `zip(cols, dict)` 对列名/键值的错位依赖：
   - `_degrade_note`（`L1306`）：统一按 `monitor.degrade.record_degrade(...)` 留痕；
   - `_row_values`（`L1312`）：同时兼容 `tuple`/`pyodbc.Row` 与 `dict`，按列序返回元组；
   - `_row_col`（`L1321`）：同时支持列序索引与大小写无关列名读取；
   - `_scalar`（`L1339`）：安全读取单行标量结果；
   - `_show_var_value`（`L1348`）与 `_show_vars_map`（`L1363`）：兼容 MySQL / GBase `SHOW VARIABLES` / `SHOW GLOBAL STATUS` 在元组游标与 `DictCursor` 下的双列解析；
   - `_rows_to_dicts`（`L1374`）：统一将结果集转换为小写键 `dict` 列表；
   - `_mysql_major_version`（`L1392`）、`_extract_capacity_point`（`L1403`）、`_evaluate_capacity_prediction`（`L1461`）：版本解析与时序容量点提取/线性斜率外推共享 helper。

### 1.2 根因 B（零静默吞错与去除硬编码假值）

对 DB-022 新增的 15 项 `INS-COM-*` 方法逐一清理内层裸 `try...except` 静默返回 `ok` / `0` / `[]` 的逻辑：
- 非预期的 SQL / 游标 / 结构异常统一通过 `_degrade_note(...)` 留痕后显式上抛，由 `_run_single` 落入 `status='error'`，不再掩盖真实缺陷；
- 对已知版本差异或选装视图的合法降级分支（例如 MySQL 8.0 无 `INNODB_LOCK_WAITS`、fallback 至 `INNODB_METRICS.lock_deadlocks`、DM 未开归档 `ARCH_MODE='N'`），先精确识别边界并写入 `degrade` 痕，仅对明确不适用的架构/能力边界返回 `skip`；
- 清除 `detect_open_cursors` 对 DM 会话上限 `1000` 的硬编码，改为从实例参数动态读取 `MAX_SESSION_STATEMENT`。

---

## 二、15 项 `INS-COM-*` 逐项修复明细

所有修改均位于 `monitor/inspection_executor.py`（`L1625-L2887`）：

| # | 规则编号 | 修复方法与行号 | 本轮修复要点 |
|---:|---|---|---|
| 1 | `INS-COM-USER-LOCKED` | `detect_locked_users` (`L1625`) | 移除静默吞错与 `r[0]/r[1]` 依赖；GBase 分支改为查询 `gbase.user_check` 并兼容 `DictCursor`；MySQL/PG 改用 `_rows_to_dicts` / `_row_values` 正确提取被锁/过期账号 |
| 2 | `INS-COM-PWD-EXPIRE` | `detect_expiring_passwords` (`L1727`) | 移除吞错；DM 修正 `dba_users` 过期日期列（`EXPIRY_DATE`）并安全格式化时间；GBase 改查 `gbase.user_check` 密码有效期；MySQL/PG 兼容 `DictCursor` |
| 3 | `INS-COM-CAP-7D` | `detect_7d_capacity` (`L1827`) | 统一委托 `_evaluate_capacity_prediction`：先判断 `ctx.db_config.pk` 是否存在，未关联已纳管实例（如平台元数据库 `db_monitor`）返回 `skip`；`MonitorLog` 查询强制过滤 `status='UP'` 并统计 `non_up_samples_excluded`；样本不足返回 `skip`，样本充足时按时间跨度执行线性容量外推与 `warning` 阈值判定 |
| 4 | `INS-COM-CAP-30D` | `detect_30d_capacity` (`L1840`) | 同 `CAP-7D` 口径（14 天窗口向后外推 30 天），过滤非 `UP` 历史记录并按实际样本/斜率输出预测增长量与增长率 |
| 5 | `INS-COM-DEADLOCK-24H` | `detect_deadlock_count` (`L1853`) | 移除吞错；GBase 8a 识别 EXPRESS 列存引擎无 InnoDB 死锁统计，按能力边界显式返回 `skip`；MySQL 优先读取 `Innodb_deadlocks` 状态变量，空行时合法 degrade 并 fallback 至 `information_schema.INNODB_METRICS` (`lock_deadlocks`) |
| 6 | `INS-COM-MISSING-INDEX` | `detect_missing_indexes` (`L1923`) | 移除吞错与元组下标/列名错配，改用 `_row_col` / `_rows_to_dicts` 兼容 `DictCursor`；Oracle/DM/MySQL/PG 统一校验真实统计字段，非适用方言清晰 `skip` |
| 7 | `INS-COM-PART-NEEDED` | `detect_large_table_not_partitioned` (`L2009`) | 移除吞错；DM 修正 `dba_segments` 分组与投影列为 `SEGMENT_NAME` 避免无效列名报错；MySQL/PG/GBase 统一安全读取大表行数与容量 |
| 8 | `INS-COM-SPARSE-INDEX` | `detect_sparse_index` (`L2103`) | 移除吞错，改用 `_rows_to_dicts` 解决 `DictCursor` 下 `zip(cols, dict)` 把列名当成真实表名/索引名/基数的严重失真 |
| 9 | `INS-COM-PARAM-DEV` | `detect_param_deviation` (`L2189`) | 移除吞错；DM 改查 `V$PARAMETER` 并修正布尔/数值解析（`BOOL`/`VARCHAR`/数字）；MySQL/GBase 改用 `_show_vars_map` 兼容 `DictCursor` 解析变量 |
| 10 | `INS-COM-ARCH-GAP` | `detect_archive_gap` (`L2264`) | 移除吞错；DM 通过 `V$DATABASE` / `V$DM_ARCH_INI` 检测归档开关，`ARCH_MODE='N'` 时显式返回 `skip`（不再报视图/列不存在错） |
| 11 | `INS-COM-LOCK-WAIT` | `detect_lock_waits` (`L2359`) | 移除吞错；MySQL 8.0+ 按 `_mysql_major_version` 切至 `performance_schema.data_lock_waits`（5.7 保留 `information_schema.INNODB_LOCK_WAITS`）；DM 改为兼容的会话锁等待联查；GBase 改用 `PROCESSLIST` + `TABLE_LOCKS` |
| 12 | `INS-COM-BLOCK-SESSION` | `detect_blocked_sessions` (`L2467`) | 移除吞错；DM 修正长阻塞会话查询条件与字段映射；GBase 改用 `information_schema.PROCESSLIST`（`TIME >= 300` 等活跃/等待长事务识别）；MySQL/PG 兼容 `DictCursor` |
| 13 | `INS-COM-OPEN-CURSOR` | `detect_open_cursors` (`L2554`) | 移除吞错与 `1000` 硬编码假值；DM 从实例系统参数动态读取 `MAX_SESSION_STATEMENT` 与 `V$OPEN_STMT` 会话游标计数计算占用率 |
| 14 | `INS-COM-TEMP-USAGE` | `detect_temp_usage` (`L2629`) | 移除吞错；DM 改为按 `TEMP` 表空间数据文件与使用率查询；GBase 联查 `Meminfo_temp_heap_*` 与 `SESSION_MEMORY_USAGE_INFO.TEMP_SPACE` 计算临时堆使用率；MySQL 结合 `INNODB_SESSION_TEMP_TABLESPACES`、`information_schema.FILES`（区分有界 vs `ibtmp1` 自动扩展）及 `temptable_max_ram` 计算真实临时空间使用率 |
| 15 | `INS-COM-UNDO-USAGE` | `detect_undo_usage` (`L2778`) | 移除吞错；DM 查询 `ROLL` 回滚表空间数据文件总容量/空闲/已用并准确计算 `pct_used`；Oracle 保持 Undo 查询并统一用 `_rows_to_dicts` 安全取值；其余非 Undo 表空间架构按方言 `skip` |

---

## 三、4 个外网真实库全量 23 项复测对照

复测于 2026-11-13 12:07 (+08:00) 通过只读通道在同批 4 个外网真实库 + 平台元数据库对照执行，每库跑满 DB-022 上半全部 **23 项**（15 项 `INS-COM-*` + 8 项 `INS-ORA-*`），合计 **115 次**执行，**`error = 0`**。

> **安全脱敏声明**：所有目标库均以平台加密配置或运行期环境变量解密只读直连，本报告仅保留实例编号（`cid`）、数据库产品版本与巡检业务结果，不记录任何主机地址、端口、用户名或口令。

### 3.1 分支实测结果对比总表

| 分支 | 目标标识与实测版本 | DB-022 补充（修复前） | DB-024 复测结果（23 项全量） | 复测状态 |
|---|---|---|---|---|
| **DM8** | `DatabaseConfig cid=9`（`DM_测试库`）<br>`DM Database Server 64 V8 \| 0x7000d \| 03134284488-20260330-320063-20200` | **不通过**（6 `ok`, **6 `error`**, **3 吞错为 `ok`**, 8 ORA `skip`） | `ok`: **12**, `skip`: **11**（含 3 项规则前置 skip + 8 项 ORA skip）, `error`: **0** | **通过** |
| **GBase 8a** | `DatabaseConfig cid=11`（`GBase8A_测试库`）<br>`8.6.2.43-R7-free.110605` | **不通过**（4 `ok`, **1 `KeyError`**, **5 吞错为 `ok`**, 13 `skip`） | `ok`: **9**, `skip`: **14**（含 `DEADLOCK-24H` EXPRESS skip + 5 非适用 COM + 8 ORA skip）, `error`: **0** | **通过** |
| **MySQL 8.0** | `DatabaseConfig cid=19`（`M-team probe-mysql-8.0`）<br>`8.0.45` | **不通过**（7 `ok`, **1 `KeyError`**, **4 吞错/假阴性/字段错位**, 11 `skip`） | `warning`: **2**（真实命中）, `ok`: **10**, `skip`: **11**（3 COM + 8 ORA skip）, `error`: **0** | **通过** |
| **PostgreSQL** | `DatabaseConfig cid=2`（`PostgreSQL_测试库`）<br>`PostgreSQL 16.13 ...` | 12 `ok`（旧版容量未预测趋势）, 11 `skip` | `ok`: **10**, `warning`: **2**（真实高增长预测）, `skip`: **11**, `error`: **0** | **通过** |
| **PG 元数据库对照** | `pgsql_db_monitor`（未关联 `DatabaseConfig` pk，同版本 PG 16.13） | 12 `ok`（含无历史容量的假平稳 `ok`）, 11 `skip` | `ok`: **10**, `skip`: **13**（`CAP-7D`/`CAP-30D` 正确识别无 `cid` 并 skip）, `error`: **0** | **通过** |
| **Oracle** | 外网无真实商用 Oracle 库 | **未测·延后**（23 项） | **未测·延后**（23 项，留待上线后内网完成实库复测） | **未测·延后** |

### 3.2 DM8（`cid=9`）重点项逐项实测结果

`MonitorLog` 窗口核验：7d `total=1667, up=2`（其余 1665 条非 UP）；14d `total=1667, up=2`。

| 规则编号 | 修复前 | 修复后状态 | 复测输出摘要与关键指标原文 |
|---|---|---|---|
| `INS-COM-PWD-EXPIRE` | `error`（无效列 `exp_datetime`） | `ok` | `即将过期密码账号 0 个`（`expiring_users=[]`） |
| `INS-COM-CAP-7D` | 误纳入非 UP 样本报平稳 `ok` | `skip` | `有效 UP 时序采样仅 2 点（不足 3 点，已过滤非 UP 记录 1665 条），暂无法预测 7 天容量` |
| `INS-COM-CAP-30D` | 误纳入非 UP 样本报平稳 `ok` | `skip` | `有效 UP 时序采样仅 2 点（不足 5 点，已过滤非 UP 记录 1665 条），暂无法预测 30 天容量` |
| `INS-COM-PART-NEEDED` | `error`（无效列 `table_name`） | `ok` | `大表未分区 0 张`（`unpartitioned_large_tables=[]`） |
| `INS-COM-PARAM-DEV` | `error` | `ok` | `偏离参数 0 项`（`deviations=[]`） |
| `INS-COM-ARCH-GAP` | `error` | `skip` | `实例未开启归档模式 (ARCH_MODE=N)，不适用归档间隙检查`（`arch_mode='N'`） |
| `INS-COM-LOCK-WAIT` | SQL 失败被吞为 `ok` | `ok` | `锁等待会话 0 个`（`lock_waits=[]`） |
| `INS-COM-BLOCK-SESSION` | SQL 失败被吞为 `ok` | `ok` | `长阻塞会话 0 个`（`blocked_sessions=[]`） |
| `INS-COM-OPEN-CURSOR` | SQL 失败且硬编码上限 `1000` | `ok` | `打开游标数 2 (上限 20000)`（`max_session_open=1, session_usage_pct=0.0, current_open=2, max_open=20000`） |
| `INS-COM-TEMP-USAGE` | `error` | `ok` | `临时表空间使用率 26.2%`（`temp_files=[{'name': 'TEMP', 'total_mb': 64.0, 'used_mb': 16.75}], pct_used=26.2`） |
| `INS-COM-UNDO-USAGE` | SQL 失败被吞且未算使用率 | `ok` | `Undo 使用率 17.6% 状态正常`（`undo_tablespaces=[{'name': 'ROLL', 'total_mb': 128.0, 'used_mb': 22.5, 'free_mb': 105.5}], pct_used=17.6`） |
| 其余 4 项 `INS-COM-*` | — | `ok` | `USER-LOCKED` / `DEADLOCK-24H` / `MISSING-INDEX` / `SPARSE-INDEX` 均实测 `0` 无异常 |
| 8 项 `INS-ORA-*` | `skip` | `skip` | 全部优雅返回 `不适用当前方言: dm (仅支持 oracle 专属特性)` |

### 3.3 GBase 8a（`cid=11`）重点项逐项实测结果

`MonitorLog` 窗口核验：7d `total=8653, up=8362`（排除 291 条非 UP）；14d `total=9591, up=8362`（排除 1229 条非 UP）。

| 规则编号 | 修复前 | 修复后状态 | 复测输出摘要与关键指标原文 |
|---|---|---|---|
| `INS-COM-USER-LOCKED` | SQL 失败被吞为 `ok` | `ok` | `锁定账号 0 个`（查询 `gbase.user_check` 成功，`locked_users=[]`） |
| `INS-COM-PWD-EXPIRE` | SQL 失败被吞为 `ok` | `ok` | `即将过期密码账号 0 个`（`expiring_users=[]`） |
| `INS-COM-CAP-7D` | 仅统计点数无外推 | `ok` | `7天容量预测(UP采样 8362 点): 当前 4.74 MB，日均 +0.00 MB/天，预计7天后 4.74 MB (+0.0%)` |
| `INS-COM-CAP-30D` | 仅统计点数无外推 | `ok` | `30天容量预测(UP采样 8362 点): 当前 4.74 MB，日均 +0.00 MB/天，预计30天后 4.74 MB (+0.0%)` |
| `INS-COM-DEADLOCK-24H` | 状态变量空行假性 `ok` | `skip` | `不适用当前方言: gbase 使用 EXPRESS 引擎，无 InnoDB 死锁统计 (能力边界 skip)` |
| `INS-COM-PART-NEEDED` | SQL 失败被吞为 `ok` | `ok` | `大表未分区 0 张`（`unpartitioned_large_tables=[]`） |
| `INS-COM-PARAM-DEV` | `error`（`KeyError: 1`） | `ok` | `偏离参数 0 项`（`deviations=[]`） |
| `INS-COM-LOCK-WAIT` | SQL 失败被吞为 `ok` | `ok` | `锁等待会话 0 个`（`lock_waits=[]`） |
| `INS-COM-BLOCK-SESSION` | SQL 失败被吞为 `ok` | `ok` | `长阻塞会话 0 个`（`blocked_sessions=[]`） |
| `INS-COM-TEMP-USAGE` | SQL 失败被吞为 `ok` | `ok` | `临时表空间使用率 0.0%`（`temp_mb=0.0, temp_heap_total_mb=985.0, session_temp_mb=0.0, pct_used=0.0`） |
| 5 项非适用 `COM` + 8 项 `ORA` | `skip` | `skip` | `MISSING-INDEX` / `SPARSE-INDEX` / `ARCH-GAP` / `OPEN-CURSOR` / `UNDO-USAGE` 与 8 项 `INS-ORA-*` 全部 `skip` |

### 3.4 MySQL 8.0.45（`cid=19`）重点项逐项实测结果

`MonitorLog` 窗口核验：7d / 14d 均为 `total=5171, up=5171`（非 UP 0 条）。

| 规则编号 | 修复前 | 修复后状态 | 复测输出摘要与关键指标原文 |
|---|---|---|---|
| `INS-COM-USER-LOCKED` | **漏报为 0**（`DictCursor` + 吞错） | **`warning`** | `锁定账号 3 个`；真实检出 `mysql.infoschema`、`mysql.session`、`mysql.sys`（`status='LOCKED'`，见 `findings[0]`：`发现 3 个被锁定的账号`） |
| `INS-COM-SPARSE-INDEX` | **字段错位成列名** | **`warning`** | `稀疏索引 10 个`；真实返回表名/索引名/基数（如 `tdsql_sqlcheck_test.inspection_results.idx_ir_category`: `cardinality=5, table_rows=2723` 等全部 10 条真实索引信息） |
| `INS-COM-CAP-7D` / `CAP-30D` | 仅统计点数无外推 | `ok` | 7d / 30d 均返回 `UP采样 5171 点: 当前 800.85 MB，日均 +0.00 MB/天，预计... 800.85 MB (+0.0%)` |
| `INS-COM-DEADLOCK-24H` | 状态变量空行吞为 `0` | `ok` | `死锁次数 0 次`（通过 `INNODB_METRICS` `lock_deadlocks` 等路径真实校验，不再盲返 0） |
| `INS-COM-PARAM-DEV` | `error`（`KeyError: 1`） | `ok` | `偏离参数 0 项` |
| `INS-COM-LOCK-WAIT` | 8.0 无旧表被吞 | `ok` | `锁等待会话 0 个`（已切至 `performance_schema.data_lock_waits`） |
| `INS-COM-TEMP-USAGE` | 8.0 缺列报错被吞 | `ok` | `临时表空间使用率 1.0%`（`temp_mb=10.0, session_temp_mb=0.08, ibtmp_used_mb=10.0, ibtmp_alloc_mb=12.0, temptable_max_mb=1024.0, pct_used=1.0`，正确识别自动扩展 `ibtmp1` 而非误报 `83.3%`） |
| 其余 5 项 `COM` + 11 项 `skip` | — | `ok` / `skip` | `PWD-EXPIRE` / `MISSING-INDEX` / `PART-NEEDED` / `BLOCK-SESSION` 均 `ok`；`ARCH-GAP` / `OPEN-CURSOR` / `UNDO-USAGE` 与 8 项 `INS-ORA-*` 全部 `skip` |

### 3.5 PostgreSQL 16.13（`cid=2` 与元数据库 `db_monitor`）重点项实测结果

`cid=2` `MonitorLog` 窗口核验：7d `total=7020, up=7000`（排除 20 条非 UP）；14d `total=7958, up=7938`（排除 20 条非 UP）。

| 目标 | 规则编号 | 修复后状态 | 复测输出摘要与关键指标原文 |
|---|---|---|---|
| `cid=2`（`PostgreSQL_测试库`） | `INS-COM-CAP-7D` | **`warning`** | `7天容量预测(UP采样 7000 点): 当前 1776.04 MB，日均 +153.28 MB/天，预计7天后 2848.99 MB (+60.4%)`（`analyzed_points=25, span_days=7.0, max_tablespace_used_pct=67.31`） |
| `cid=2`（`PostgreSQL_测试库`） | `INS-COM-CAP-30D` | **`warning`** | `30天容量预测(UP采样 7938 点): 当前 1776.04 MB，日均 +152.64 MB/天，预计30天后 6355.3 MB (+257.8%)`（`analyzed_points=25, span_days=7.65`） |
| `cid=2` 其余 10 项 PG 适用项 + 11 项 skip | — | `ok` (10) / `skip` (11) | 全部通过（无 `error`） |
| `pgsql_db_monitor`（未关联 `DatabaseConfig`） | `INS-COM-CAP-7D` / `CAP-30D` | **`skip`** | `未关联已纳管 DatabaseConfig 实例（如平台元数据库），跳过历史容量预测` |
| `pgsql_db_monitor` 其余 10 项 PG 适用项 + 11 项 skip | — | `ok` (10) / `skip` (11) | 与业务 PG 表现一致，共 10 `ok`, 13 `skip`, `error=0` |

### 3.6 Oracle 分支（`未测·延后` 声明）与 8 项 `INS-ORA-*` 登记核验

- **23 项 Oracle 实库运行结果：未测·延后**（外网无真实商用 Oracle 环境；本地容器化测试实例不纳入本次实库通过判定）。
- 在 4 个外网非 Oracle 真实库及 `db_monitor` 上，`inspection_executor.py` 注册表中的全部 8 项 DB-022 Oracle 专属条目（`INS-ORA-AWR-TOP-EVT`、`INS-ORA-AWR-TOP-SQL`、`INS-ORA-STA-OUTPUT`、`INS-ORA-SEG-ADV`、`INS-ORA-FRA`、`INS-ORA-CTRL-BAK`、`INS-ORA-ARCH-DELETE`、`INS-ORA-DBA-USERS`）均已实测验证稳定返回 `skip`（`不适用当前方言: <db_type> (仅支持 oracle 专属特性)`），无误触发与异常。

---

## 四、单元测试补强与验证门禁结果

### 4.1 新增回归单测（`monitor/tests_inspection_scope.py`）

新增 `@tag('unit')` 测试类 `Db024DetectorFixTests`（`L695+`），包含 5 个回归用例，全量使用 mock，不依赖外网实库：
1. `test_root_cause_a_dictcursor_compatibility`：验证 `DictCursor`（dict 行）下 `USER-LOCKED`、`SPARSE-INDEX`、`PARAM-DEV` 等正确取回行值而非报 `KeyError` 或把列名当行值；
2. `test_root_cause_b_no_silent_error_swallowing`：验证模拟 SQL / 游标异常时显式触发 `error` 并留存 `degrade` 痕，不再静默吞为 `ok`；
3. `test_dm8_detector_fixes`：验证 DM8 修正后的 `PWD-EXPIRE`、`PART-NEEDED`、`ARCH-GAP`（`ARCH_MODE='N'` 走 `skip`）、`OPEN-CURSOR`（动态读取 `MAX_SESSION_STATEMENT`）、`TEMP-USAGE`、`UNDO-USAGE`；
4. `test_gbase_and_mysql8_dialect_fixes`：验证 GBase `gbase.user_check`、`DEADLOCK-24H`（EXPRESS 能力边界 `skip`）、`TEMP-USAGE` 与 MySQL 8.0 `data_lock_waits`、`INNODB_METRICS` fallback、自动扩展 `ibtmp1` 计算；
5. `test_capacity_prediction_filters_up_and_computes_trend`：验证 `CAP-7D`/`CAP-30D` 过滤非 `UP` 样本、元数据实例 `skip`、真实上升斜率触发 `warning`。

### 4.2 代码校验门禁实测记录

在完成全部 Python 代码编辑后执行的校验记录（Windows `.venv/Scripts/python.exe` + `WSLENV=DJANGO_SETTINGS_MODULE:PYTHONUTF8`）：

| 校验项 | 执行范围与命令 | 实测结果 |
|---|---|---|
| 红线规范扫描 | `python scripts/lint_redlines.py` | **通过**（`红线约束扫描通过`, exit 0） |
| DB-022/023/024 定向单测 | `Db024DetectorFixTests` + `InspectionBatch2Part1ImplementationTests` + `monitor.tests_inspection_db023` (`--tag unit`) | **通过**（`Ran 15 tests in 0.251s`, `OK`, exit 0） |
| `scripts/validate.sh unit` | 红线扫描 + `unit` 全量单测（SQLite `:memory:`） | 红线扫描 `pass` (`781ms`)；`unit`: `Ran 434 tests in 20.378s`：**430 passed, 4 failed**（4 个失败全部为 `monitor/tests_inspection_scope.py:442-520` 既有的 `DamengDriverVisibilityTests`，详见 §五 说明） |
| `scripts/validate.sh backend` | 编译 + check + migrations check + 依赖 + 文档版本 + PostgreSQL 集成全量测试 | 语法编译 `pass` (`153ms`)、系统检查 `pass` (`586ms`)、迁移漂移 `pass` (`716ms`)、依赖完整性 `pass` (`594ms`)、文档版本 `pass` (`92ms`)；全量 `Ran 619 tests in 93.954s`：**586 passed, 29 skipped, 4 failed**（同上 4 个既有用例） |

---

## 五、范围外观察与遗留说明（不越界改动，供排期参考）

1. **既有 `DamengDriverVisibilityTests` 本地环境敏感性**：本机 Windows `.venv` 为执行 DB-022 实库采集验证预装了 `dmPython` 驱动，导致既有用例 `monitor/tests_inspection_scope.py` 中的 `DamengDriverVisibilityTests`（4 个断言 `is_dm_checker_available() == False` 的测试：`test_checker_unregistered_visible_signals`、`test_start_monitor_dm_produces_visible_signal`、`test_celery_task_dm_produces_visible_signal`、`test_inspection_executor_and_reports_dm_visibility`）在本地 `.venv` 运行时触发失败；在无 `dmPython` 的标准 CI Linux 环境中预期通过，且本单按纪律未修改既有测试代码（推送后将复核 GitHub CI 结果）。
2. **`L81-L1283` 早前基线检测方法**：按 DB-024 任务边界，本单仅修复 DB-022 上半涉及的 15 项 `INS-COM-*` 方法，未触动 `L81-L1283` 既有基线实现与 `Batch010bDetector`；后续若针对历史基线方法开展类似 `DictCursor`/吞错审计，建议另立专项单推进。
3. **Oracle 23 项内网复测**：保持 `未测·延后` 标记，待内网具备真实商用 Oracle 实例后完成最终实库补测并闭环。
