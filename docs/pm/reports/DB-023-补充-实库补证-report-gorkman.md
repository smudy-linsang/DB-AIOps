通过

# DB-023 补充：实库补证报告

- **版本**：v1.0
- **日期**：2026-11-12（洛杉矶时间，PST，UTC-8）。宿主机主测时刻 2026-11-13T10:34:45+08:00，即洛杉矶时间 2026-11-12 18:34:45 PST。
- **执行**：GorkMan
- **任务单**：`docs/pm/orders/DB-023-补充-实库补证.md`（v1.0）
- **接单回执**：`docs/pm/reports/DB-023-补充-ack-gorkman.md`（commit `584e9b11820ba1bc57b21ea602f2870950d43827`）
- **原单报告**：`docs/pm/reports/DB-023-report-gorkman.md`（v1.1，结论为有条件通过）

本补充单的范围是：在外网已验证通道上执行 DM 4 项（#65–#68）和 GBase 3 项（#69–#71），按实测原文记录；视图缺失记 error，不得写成健康、均衡或无宕机。七项均已在实库执行并记录，符合任务单，故本补充单结论为通过。

`INS-DM-ARCH` 的 critical（归档未开启，`ARCH_MODE=N`）是这台测试实例的实测发现，不是检测器缺陷，也不是本单不通过的理由。GBase 三项官方检测器报 1046；逐库 `USE` 之后确认目标视图不存在（1146/1109），按任务单记 error。原单仍未闭环、且本单明确延后的 Oracle 16 项与 GTID（#54）单列「未测·延后」，不写成这 7 项的条件。PM 是否据此关闭原单外网 DM/GBase 条件，由 PM 决定。本报告不改 `docs/pm/STATUS.md`。

## 范围与非范围

本单执行并记录：

| # | item_id | 实库 |
|---:|---|---|
| 65 | `INS-DM-DSC` | 达梦 id=9 |
| 66 | `INS-DM-DW-STANDBY` | 达梦 id=9 |
| 67 | `INS-DM-ARCH` | 达梦 id=9 |
| 68 | `INS-DM-RLOG` | 达梦 id=9 |
| 69 | `INS-GBASE-BALANCE` | GBase id=11 |
| 70 | `INS-GBASE-DIST` | GBase id=11 |
| 71 | `INS-GBASE-NODE-DOWN` | GBase id=11 |

不在本单：

- Oracle 16 项（#36–#43、#45–#52）：未测·延后。外网无真实库，延至上线后内网复测。
- GTID（#54 `INS-MYSQL-GTID`）：未测·延后。需官方 MySQL 主从；本单未执行。
- MySQL 5 项与 PostgreSQL 6 项：原单报告 §三已在本机 MariaDB 与 PostgreSQL 上执行过，不在本单范围。本单没有重跑这两段。

## 通道与实例身份

通道为 DB-009 已验证通道：`DatabaseConfig.get_password()` 取口令，经 `monitor.db_connector.get_connector` 连接。未索取新凭据。未重启采集器（旁路连接）。口令未落盘。证据只记录 `password_len` 与 `username_len`。

宿主机检出 HEAD：`1d8ebbf5f7ca4c005bfe962c6a3f4ceeca911c2a`。其父提交为本单回执 `584e9b11820ba1bc57b21ea602f2870950d43827`。证据 README 写明检测器实现来自原单 tip `b3927d48`。官方判定走宿主 `Batch010bDetector`。本单未改检测器代码。证据中的 `host_repo` 为 `C:\DB-AIOps`；脚本路径里的用户目录已掩码为 `<user-home>`，主机名为 `<local-host>`。

主测时刻：`captured_at=2026-11-13T10:34:45+08:00`，`captured_at_utc=2026-11-13T02:34:45+00:00`，`captured_at_pst=2026-11-12T18:34:45-08:00`。七项官方 SQL 的 `started_at` 与整包 `finished_at` 都是 `2026-11-13T10:34:45+08:00`。GBase 视图诊断 `captured_at=2026-11-13T10:37:13+08:00`。补充盘点 `captured_at=2026-11-13T10:38:12+08:00`。

### 达梦

| 项 | 实测 |
|---|---|
| 平台配置 | id=9，名称 `DM_测试库`，`db_type=dm`，`is_active=true`，端口 `<dm-port>` |
| 选用 | id=9，`username_len=6`，`password_len=9` |
| 连接 | `connected=true`，`connect_ms=74`，`err=None` |

身份查询（主测 `2026-11-13T10:34:45+08:00`）：

- `SELECT ID_CODE, NAME, STATUS, MODE$, SVR_VERSION FROM V$INSTANCE` 失败。`ProgrammingError: ('42S22', '[42S22] 第1 行附近出现错误:\n无效的列名[STATUS] (-2111) (SQLExecDirectW)')`，`elapsed_ms=5`。带 `STATUS` 列的语句因 -2111 无效列名失败，属达梦字典差距，与 DB-009 同类。身份以下面无 `STATUS` 的查询为准。
- `SELECT NAME, DSC_NODES, ARCH_MODE, ROLE$ FROM V$DATABASE` 成功，1 行：`NAME=DAMENG`，`DSC_NODES=1`，`ARCH_MODE=N`，`ROLE$=0`。`elapsed_ms=0`。

补充盘点（`2026-11-13T10:38:12+08:00`）：

- `SELECT BANNER FROM V$VERSION` 成功，5 行：`DM Database Server 64 V8`；`DB Version: 0x7000d`；`03134284488-20260330-320063-20200`；`Msg Version: 1`；`Gsu level(5) cnt: 102`。
- `SELECT ID_CODE, NAME, MODE$, SVR_VERSION FROM V$INSTANCE` 成功，1 行：`ID_CODE=--03134284488-20260330-320063-20200 Pack2`，`NAME=DAMENG`，`MODE$=NORMAL`，`SVR_VERSION=DM Database Server x64 V8`。
- `SELECT * FROM V$INSTANCE WHERE 1=0` 成功，0 行。证据记录的 `v_instance_cols` 为：`NAME`、`INSTANCE_NAME`、`INSTANCE_NUMBER`、`HOST_NAME`、`SVR_VERSION`、`DB_VERSION`、`START_TIME`、`STATUS$`、`MODE$`、`OGUID`、`DSC_SEQNO`、`DSC_ROLE`、`BUILD_VERSION`、`BUILD_TIME`。列名是 `STATUS$`，不是 `STATUS`。
- `SELECT PARA_NAME, PARA_VALUE FROM V$PARAMETER WHERE PARA_NAME IN ('MAX_SESSIONS','INSTANCE_NAME') OR PARA_NAME LIKE '%VERSION%'` 失败，同样是 -2111：`无效的列名[PARA_NAME]`。

### GBase

| 项 | 实测 |
|---|---|
| 选用 | id=11，名称 `GBase8A_测试库`，`db_type=gbase`，端口 5258（任务单已写，保留），`username_len=4`，`password_len=4` |
| 同端口候选 | id=15，名称 `probe-gbase`，`db_type=gbase`，`is_active=true`，端口 5258，`preferred=false`。本单未使用 |
| 连接 | `connected=true`，`connect_ms=49`，`err=None` |
| 版本 | `SELECT VERSION() AS ver` 成功，1 行：`8.6.2.43-R7-free.110605`。`elapsed_ms=2` |
| 当前库 | `SELECT DATABASE() AS dbname` 成功，1 行：`dbname=null`。`elapsed_ms=1` |
| 诊断时 | `service_name_empty=true`。`current_db.database=null`，`current_db.version=8.6.2.43-R7-free.110605` |

库列表（诊断 `SHOW` 结果）：`information_schema`、`performance_schema`、`gbase`、`gclusterdb`、`gctmpdb`。

DB-009 已记录 GBase 不支持语句超时变量，错误为 `OperationalError: (1193, "Unknown system variable 'max_statement_time'")`（见 `docs/pm/reports/evidence/DB-009-m-team/db009_gbase_dialect_tests.txt`）。这是 DB-009 已知能力边界，不是本单缺陷。本单证据包里的 GBase 错误码是 1046、1109、1146。

## 总表

| # | item_id | 判定 | 一句话 |
|---:|---|---|---|
| 65 | `INS-DM-DSC` | skip | `DSC_NODES=1`，非多节点 DSC，不适用 |
| 66 | `INS-DM-DW-STANDBY` | skip | 未配置 DataWatch 备库，不适用；空结果未写成健康 |
| 67 | `INS-DM-ARCH` | critical | 归档未开启（`ARCH_MODE=N`）；实例实测，不是检测器缺陷 |
| 68 | `INS-DM-RLOG` | skip | 无实时 RLOG 同步对象；SQL 未使用 `V$RLOG` |
| 69 | `INS-GBASE-BALANCE` | error | 官方通道 1046 `No database selected`；视图在五个库上都不存在 |
| 70 | `INS-GBASE-DIST` | error | 同上 1046；不得写成均衡 |
| 71 | `INS-GBASE-NODE-DOWN` | error | 与 #69 同一 SQL，1046；语句未成功，不得写成无宕机 |

## 逐项

实库名称与版本见上一节。下列 SQL、行数、错误文本与检测器返回均来自 `docs/pm/reports/evidence/DB-023-补充-gorkman/db023_live_results.json`，文本摘要同目录 `db023_live_results.txt`。执行时间除另注外均为 `2026-11-13T10:34:45+08:00`。

### #65 `INS-DM-DSC` 节点状态 — skip

实库：达梦 id=9 `DM_测试库`，`SVR_VERSION=DM Database Server x64 V8`，库名 `DAMENG`。

| SQL | 结果 |
|---|---|
| `SELECT DSC_NODES FROM V$DATABASE` | 成功，1 行，`DSC_NODES=1`。`elapsed_ms=0` |
| `SELECT EP_SEQNO, EP_NAME, EP_STATUS FROM V$DSC_EP_INFO` | 成功，0 行，`rows=[]`。`elapsed_ms=1` |
| `SELECT INST_ID, INST_NAME, STATUS FROM V$DSC_INSTANCES` | 失败。`ProgrammingError: ('42S02', '[42S02] 第1 行附近出现错误:\n无效的表或视图名[V$DSC_INSTANCES] (-2106) (SQLExecDirectW)')`。`elapsed_ms=0` |

官方检测器 `detect_dsc_nodes`（`Batch010bDetector`，`elapsed_ms=8`）：`status=skip`，`severity=info`，`summary=DSC_NODES=1，非多节点 DSC，不适用（不用 V$INSTANCE 行数代替节点数）`，`error=null`，`findings=[]`，`metrics={}`。形态 skip 以实测 `DSC_NODES=1` 为准。

### #66 `INS-DM-DW-STANDBY` 主备同步 — skip

实库：同上，达梦 id=9。

| SQL | 结果 |
|---|---|
| `SELECT ROLE$ FROM V$DATABASE` | 成功，1 行，`ROLE$=0`。`elapsed_ms=0` |
| `SELECT MODE$ FROM V$INSTANCE` | 成功，1 行，`MODE$=NORMAL`。`elapsed_ms=0` |
| `SELECT ARCH_TYPE, ARCH_DEST, ARCH_STATUS, ARCH_SRC FROM V$ARCH_STATUS` | 成功，0 行，`rows=[]`。`elapsed_ms=0` |

官方检测器 `detect_dw_standby`（`elapsed_ms=14`）：`status=skip`，`severity=info`，`summary=未配置 DataWatch 备库，不适用（不把 PRIMARY 标成健康）`，`error=null`，`findings=[]`，`metrics={}`。`V$ARCH_STATUS` 为 0 行，空结果未写成健康。

### #67 `INS-DM-ARCH` 归档状态 — critical

实库：同上，达梦 id=9。这是该测试实例上的真实发现。

| SQL | 结果 |
|---|---|
| `SELECT ARCH_MODE FROM V$DATABASE` | 成功，1 行，`ARCH_MODE=N`。`elapsed_ms=0` |
| `SELECT ARCH_TYPE, ARCH_DEST, ARCH_STATUS, ARCH_SRC FROM V$ARCH_STATUS` | 成功，0 行，`rows=[]`。`elapsed_ms=0` |
| `SELECT COUNT(*) AS cnt FROM V$ARCH_FILE` | 成功，1 行，`cnt=0`。`elapsed_ms=0` |

官方检测器 `detect_dm_arch`（`elapsed_ms=15`）：`status=critical`，`severity=critical`，`summary=归档未开启（ARCH_MODE=N）`，`error=null`。`findings` 一条：`type=dm_arch`，`band=critical`，`message=归档未开启（ARCH_MODE=N）`。`metrics`：`grade=3.0`，`threshold_band=critical`，`arch_mode=N`，`arch_files=0`。完整记录即可。本单不因此改为不通过，也不改检测器。

### #68 `INS-DM-RLOG` 同步状态 — skip

实库：同上，达梦 id=9。证据中该项 `notes` 为「SQL 不含 V$RLOG」。

| SQL | 结果 |
|---|---|
| `SELECT ARCH_TYPE, ARCH_DEST, ARCH_STATUS, ARCH_SRC FROM V$ARCH_STATUS` | 成功，0 行，`rows=[]`。`elapsed_ms=0`。本项 SQL 只有这一条，未使用 `V$RLOG` |

官方检测器 `detect_dm_rlog`（`elapsed_ms=14`）：`status=skip`，`severity=info`，`summary=无实时 RLOG 同步对象，不适用（不使用 V$RLOG 偏移或水位冒充同步进度）`，`error=null`，`findings=[]`，`metrics={}`。`ARCH_TYPE` 仅认 REALTIME/TIMELY/RLOG；本查询 0 行，没有这类对象，记 NotApplicable skip。

### #69 `INS-GBASE-BALANCE` 节点负载均衡 — error

实库：GBase id=11 `GBase8A_测试库`，`VERSION()=8.6.2.43-R7-free.110605`，`DATABASE()=null`。官方通道未 `USE`，与 `_connect_mysql` 允许 `service_name` 留空一致（诊断包 `service_name_empty=true`）。

SQL：`SELECT NODEID, NODENAME, NODETYPE, STATUS, CPU_USED, MEM_USED FROM gcluster_v$node_info`

结果：失败。`OperationalError: (1046, 'No database selected')`。`elapsed_ms=4`。没有返回 CPU_USED / MEM_USED 行。

官方检测器 `detect_gbase_node_balance`（`elapsed_ms=22`）：`status=error`，`severity=info`，`summary=检测失败: (1046, 'No database selected')`，`error=OperationalError: (1046, 'No database selected')`，`findings=[]`，`metrics={}`。

### #70 `INS-GBASE-DIST` 数据分布均衡 — error

实库：同上，GBase id=11。证据中该项 `notes` 为「实库若无此视图或视图名不同，检测结果记 error，不得写成均衡」。

SQL：`SELECT NODEID, SUM(DATA_SIZE) AS bytes FROM gcluster_v$table_distribution GROUP BY NODEID`

结果：失败。`OperationalError: (1046, 'No database selected')`。`elapsed_ms=1`。

官方检测器 `detect_gbase_distribution`（`elapsed_ms=36`）：`status=error`，`severity=info`，`summary=检测失败: (1046, 'No database selected')`，`error=OperationalError: (1046, 'No database selected')`，`findings=[]`，`metrics={}`。不得写成均衡。

### #71 `INS-GBASE-NODE-DOWN` 节点宕机 — error

实库：同上，GBase id=11。证据中该项 `notes` 为「0 行记 error，不得写成无宕机」。本项与 #69 是同一条 SQL。

SQL：`SELECT NODEID, NODENAME, NODETYPE, STATUS, CPU_USED, MEM_USED FROM gcluster_v$node_info`

结果：失败。`OperationalError: (1046, 'No database selected')`。`elapsed_ms=1`。语句未成功，0 行的情况没有出现。

官方检测器 `detect_gbase_node_down`（`elapsed_ms=18`）：`status=error`，`severity=info`，`summary=检测失败: (1046, 'No database selected')`，`error=OperationalError: (1046, 'No database selected')`，`findings=[]`，`metrics={}`。不得写成无宕机。

## GBase：1046 与视图诊断

官方三项目对外报错都是 1046，因为连接后 `DATABASE()` 为 null、未选定默认库。诊断在五个库上逐个 `USE` 之后，目标视图仍然不存在。两条都成立：官方检测器的对外结果是 1046；视图不存在是更深的事实。本单不改实现。

诊断文件：`docs/pm/reports/evidence/DB-023-补充-gorkman/db023_gbase_diag.json`（`2026-11-13T10:37:13+08:00`）。补充盘点：`docs/pm/reports/evidence/DB-023-补充-gorkman/db023_extra_inventory.json`（`2026-11-13T10:38:12+08:00`）。

未选定库时，诊断包重放了两条官方 SQL，结果仍是 `OperationalError: (1046, 'No database selected')`。同一时刻的近似名检索成功，1 行：

```sql
SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.tables WHERE TABLE_NAME LIKE '%node%' OR TABLE_NAME LIKE '%distribut%' OR TABLE_NAME LIKE '%gcluster%'
```

返回 `TABLE_SCHEMA=gbase`，`TABLE_NAME=table_distribution`。`row_count=1`。

逐库 `USE` 成功后，两条官方 SQL 的错误如下。模式限定的 `SELECT * FROM \`库\`.\`视图\` LIMIT 20` 与下表同一错误码。

| 库 | `gcluster_v$node_info` | `gcluster_v$table_distribution` |
|---|---|---|
| `information_schema` | 1109 `Unknown table 'gcluster_v$node_info' in information_schema` | 1109 `Unknown table 'gcluster_v$table_distribution' in information_schema` |
| `performance_schema` | 1109 `Unknown table 'gcluster_v$node_info' in performance_schema` | 1109 `Unknown table 'gcluster_v$table_distribution' in performance_schema` |
| `gbase` | 1146 `Table 'gbase.gcluster_v$node_info' doesn't exist` | 1146 `Table 'gbase.gcluster_v$table_distribution' doesn't exist` |
| `gclusterdb` | 1146 `Table 'gclusterdb.gcluster_v$node_info' doesn't exist` | 1146 `Table 'gclusterdb.gcluster_v$table_distribution' doesn't exist` |
| `gctmpdb` | 1146 `Table 'gctmpdb.gcluster_v$node_info' doesn't exist` | 1146 `Table 'gctmpdb.gcluster_v$table_distribution' doesn't exist` |

`information_schema` / `performance_schema` 的异常类型是 `OperationalError`。`gbase` / `gclusterdb` / `gctmpdb` 的异常类型是 `ProgrammingError`。

补充盘点里更宽的近似名检索也只有 1 个对象，且带类型：

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM information_schema.tables WHERE LOWER(TABLE_NAME) LIKE '%node%' OR LOWER(TABLE_NAME) LIKE '%distribut%' OR LOWER(TABLE_NAME) LIKE '%gcluster%' OR LOWER(TABLE_NAME) LIKE '%balance%' OR LOWER(TABLE_NAME) LIKE '%cluster%' OR LOWER(TABLE_NAME) LIKE '%vc_%'
```

返回 1 行：`TABLE_SCHEMA=gbase`，`TABLE_NAME=table_distribution`，`TABLE_TYPE=BASE TABLE`。

`SHOW TABLES` 行数：`gbase` 40（含 `table_distribution`），`gclusterdb` 0，`gctmpdb` 0，`information_schema` 39，`performance_schema` 7。表名全文在补充盘点 JSON，此处不另抄。

`SHOW COLUMNS FROM gbase.table_distribution` 成功，7 列：`index_name` varchar(128) PRI；`dbName` varchar(64)；`tbName` varchar(64)；`isReplicate` varchar(3) 默认 `YES`；`hash_column` varchar(64)；`lmt_storage_size` bigint(20)；`table_storage_size` bigint(20)。这与检测器所用的 `NODEID` / `DATA_SIZE` 视图不是同一对象。`SELECT * FROM gbase.table_distribution LIMIT 5` 成功，0 行。

结论：`gcluster_v$node_info` 与 `gcluster_v$table_distribution` 在该实例上不存在。名称相近的 `gbase.table_distribution` 是另一张基表，列不同。不得把它当作检测器视图，也不得据此改实现。按任务单，#69–#71 记 error。

## 未测·延后

下列项本单没有执行，没有 SQL 输出。状态一律为未测·延后。它们不是上面 7 项的通过条件。

| # | item_id | 本单 |
|---:|---|---|
| 36 | `INS-ORA-LOB-PCT` | 未测·延后 |
| 37 | `INS-ORA-DBLINK` | 未测·延后 |
| 38 | `INS-ORA-DIR-OBJ` | 未测·延后 |
| 39 | `INS-ORA-RMAN-AGE` | 未测·延后 |
| 40 | `INS-ORA-DG-LAG` | 未测·延后 |
| 41 | `INS-ORA-DG-STATUS` | 未测·延后 |
| 42 | `INS-ORA-RAC-VIP` | 未测·延后 |
| 43 | `INS-ORA-REDOLOG-SIZE` | 未测·延后 |
| 45 | `INS-ORA-OPEN-FILE` | 未测·延后 |
| 46 | `INS-ORA-LOB-CACHE` | 未测·延后 |
| 47 | `INS-ORA-ASH-USAGE` | 未测·延后 |
| 48 | `INS-ORA-RMAN-CONF` | 未测·延后 |
| 49 | `INS-ORA-AUDIT-TRAIL` | 未测·延后 |
| 50 | `INS-ORA-PGA-LEAK` | 未测·延后 |
| 51 | `INS-ORA-IO-STAT` | 未测·延后 |
| 52 | `INS-ORA-EXADATA` | 未测·延后 |
| 54 | `INS-MYSQL-GTID` | 未测·延后 |

Oracle 16 项：外网无真实库，延至上线后内网复测。GTID：需官方 MySQL 主从；MariaDB 无 `gtid_mode` 的说明见原单，本单未再测。

## 原单 §五引用

原单 `docs/pm/reports/DB-023-report-gorkman.md` §五第 3–7 条是方法口径，本单不改方法、不重做判定，只引用原文：

3. 半同步「已配置但未生效」只在脚本游标里打到 critical。MariaDB 上开关打开后状态即为 ON。
4. DBLINK「未使用」只对照库缓存里最多 5000 条含 `@` 的 SQL。
5. 打开文件数在没有 `v$resource_limit.files` 时退回数据文件合计，到不了 10000 的常见 OS 描述符量级。
6. 膨胀率是页数与统计宽度的估算，不是 `pgstattuple`。
7. 阈值 error 档在巡检 Run 的计数里落在 warning。`metrics.threshold_band` 仍区分 warn 与 error。

§五第 1 条里的达梦 4 项与 GBase 3 项由本单补上实测。同条中的 Oracle 16 项，以及第 2 条的 GTID，仍是上一节的未测·延后。

## 证据清单

目录：`docs/pm/reports/evidence/DB-023-补充-gorkman/`。SHA-256 与字节数用同目录 `README-evidence.md` 中的值。`manifest.json` 在 README 中未给 SHA-256。

| 文件 | 说明 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| `db023_live_results.json` | 7 项官方 SQL + `Batch010bDetector` 判定 | 20095 | `f309c482f17395c0e1aed4c650dc0909d6374e4a21d1e526bb86e018fcdba8c5` |
| `db023_live_results.txt` | 同上文本摘要 | 5023 | `73703c45fd215663d103e97f912ab736fd98af032ca5806389d855e03bfc27e1` |
| `db023_gbase_diag.json` | 五个库逐个 USE 后查目标视图；information_schema 近似名检索 | 7941 | `05a9cc37b239e4c014b038a484b4d810cbcf538b29db6fff3184ac7aaba94849` |
| `db023_extra_inventory.json` | 达梦版本；各库 SHOW TABLES；`gbase.table_distribution` 列与样例 | 12250 | `7c7dcb94ba0e7e0c050cc800466c76f9cd0cf000d897d790a9bab23d8ebc2375` |
| `manifest.json` | 机器可读清单 | — | — |
| `README-evidence.md` | 本清单的说明 | — | — |

入库前已对前四个文件逐字节核对，大小与 SHA-256 与 README 一致。证据文件入库后未再改内容。

## 纪律

- 未改检测器实现与注册表，未改业务代码。
- 未改 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`。
- 未覆盖 `docs/pm/reports/DB-023-ack-gorkman.md` 与 `docs/pm/reports/DB-023-report-gorkman.md`。
- 未重启采集器。未索取新凭据。口令未写入本报告或证据。
- 直推 main，不开 PR。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-12 | GorkMan | 实库补证报告。日期为洛杉矶时间；宿主机主测 2026-11-13T10:34:45+08:00。本补充单结论：通过。DM 4 项与 GBase 3 项按实测原文记录。Oracle 16 项与 GTID 未测·延后 |
