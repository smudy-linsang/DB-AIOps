通过

# DB-025：修复 DB-023 GBase 视图口径（改派 GG）交付报告

- **任务单**：`docs/pm/orders/DB-025-改派-GG.md` (v1.0)
- **接单回执**：`docs/pm/reports/DB-025-receipt-GG.md`（commit `308ec7a`）
- **前序依据**：`docs/pm/reports/DB-023-补充-实库补证-report-gorkman.md`
- **执行人**：GG
- **交付时间**：2026-11-19 10:35 (+08:00)
- **改动文件清单**：
  - `monitor/inspection_detectors_010b.py`
  - `monitor/tests_inspection_db023.py`
  - `docs/pm/reports/DB-025-report-GG.md`（本报告）
- **边界遵循声明**：未修改 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`、既有 `Db023DetectorTests` 断言，亦未顺手扩围修改无关检测器。

---

## 一、实库连接信息与 GBase 8a 数据源盘点原文（任务 1）

### 1.1 实库连接信息（凭据不回显）

- **目标标识**：`DatabaseConfig cid=11`（`GBase8A_测试库`，`db_type=gbase`）
- **连接通道**：`DbConnector.get_connection(cfg, statement_timeout_ms=8000, readonly=True)`（`pymysql` + `DictCursor`；口令由系统加密配置运行期解密，本报告不记录任何主机地址、端口、用户名或口令）
- **实测时间**：2026-11-19 10:28 (+08:00)

### 1.2 全库视图 / 参数 / `SHOW` 指令实测盘点原文

在 `cid=11` 只读会话上逐项核查全部数据库对象、系统变量与集群 `SHOW` 指令，实测原文如下：

| 核查项 | 实测指令 / SQL | 目标库实测输出原文 |
|---|---|---|
| 产品版本 | `SELECT VERSION() AS ver` | `8.6.2.43-R7-free.110605`（`version_comment = GBase Enterprise Server - Advanced Edition (Commercial)`） |
| 集群节点标志 | `SHOW GLOBAL VARIABLES`（cluster/node/gcluster 相关） | `gbase_gcluster_node_flag = 0`；`gcluster_dml_ddl_proxy_switch = 0`；`gcluster_lock_level = 0`；`server_id = 0` |
| 集群状态变量 | `SHOW GLOBAL STATUS WHERE Variable_name REGEXP 'cluster\|node\|gcluster\|gbase\|dist\|vc\|coord'` | `0` 行（空集） |
| 存储引擎 | `SHOW ENGINES` | 默认引擎 `EXPRESS`，另有 `GsSYS`、`MRG_GSSYS`、`CSV`、`MEMORY` |
| 数据库清单与对象数 | `SHOW DATABASES` + `information_schema.TABLES` | 共 5 个库：`information_schema`（39 张 `SYSTEM VIEW`）、`performance_schema`（7 张 `SYSTEM VIEW`：`CACHE_CELL_STATUS_INFO`/`CACHE_USAGE_INFO`/`DISK_USAGE_INFO`/`HEAP_USAGE_INFO`/`MEMORY_USAGE_INFO`/`SESSION_MEMORY_USAGE_INFO`/`TABLES`）、`gbase`（40 张 `BASE TABLE`）、`gclusterdb`（**0** 张表）、`gctmpdb`（**0** 张表） |
| 集群 `SHOW` 指令 | `SHOW NODES` | `ERR: NotSupportedError: (1235, "This version of GBase doesn't yet support 'NODES'")` |
| 其余集群 `SHOW` | `SHOW CLUSTER` / `SHOW GCLUSTER` / `SHOW VIRTUAL CLUSTERS` / `SHOW VCS` / `SHOW DATANODES` / `SHOW COORDINATORS` | 全部报错 `ProgrammingError: (1064, ...)` |
| 候选集群表/视图 | `gcluster_v$node_info`、`gcluster_v$table_distribution`、`gbase.nodedatamap`、`gbase.vc`、`information_schema.CLUSTER_TABLES`、`information_schema.CLUSTER_TABLE_DISTRIBUTION`、`information_schema.NODE_INFO`、`performance_schema.MEMORY_INFO` | 在 `information_schema.TABLES` 中全库匹配 **0** 行；直接查询分别报 `1146 Table ... doesn't exist` 或 `1109 Unknown table` |
| 近似名基表确认 | `SELECT * FROM gbase.table_distribution LIMIT 5` | 返回 `[]`（0 行；与 DB-023 补充报告一致，该表为 7 列建表分布定义基表，无 `NODEID`/`DATA_SIZE` 节点容量列，不得冒充） |

### 1.3 查明结论与修复方案（`monitor/inspection_detectors_010b.py`）

1. **数据源结论**：外网实库 `cid=11` 为 **GBase 8a 单机免费版（`8.6.2.43-R7-free.110605`，`gbase_gcluster_node_flag=0`）**，未部署 MPP 集群协调层（`gcluster`），全库不存在 `gcluster_v$node_info`、`gcluster_v$table_distribution` 或任何替代集群节点/数据分布数据源，且服务端明确拒绝 `SHOW NODES`（`1235`）。
2. **修复实现**（`monitor/inspection_detectors_010b.py` `L1280-L1448`）：
   - 新增 `_is_gbase_missing_cluster_view(exc)`：仅精确匹配未选默认库或集群视图不存在错误（`1046 No database selected`、`1109 Unknown table`、`1146 Table ... doesn't exist` 及 `_missing_view`）；**不拦截**权限不足（如 `1142`）、连接超时或通用 `RuntimeError`，确保真实故障仍落 `status='error'`。
   - 新增 `_probe_gbase_cluster_view(cur, view_name)`：当主查询触发上述 1046/1109/1146 时，先全库查询 `information_schema.TABLES WHERE LOWER(TABLE_NAME) = '<view_name>'`：
     - **真实 MPP 集群但未配默认库（1046 场景）**：若查到视图所属 schema（如 `gclusterdb`），记录 `degrade_note` 后以 `{schema}.{view_name}` 重查，产出真实均衡/宕机判定；
     - **单机版/无集群视图场景**：若全库 0 匹配，则进一步实采 `SELECT VERSION()` 与 `SHOW GLOBAL VARIABLES LIKE 'gbase_gcluster_node_flag'`，记录 `degrade_note` 并将实测依据写入 `result.metrics['gbase_cluster_evidence']`，随后抛出 `NotApplicable` 返回 **`status='skip'`**；
   - **严守不假绿底线**：当集群视图存在但查询返回空结果（`[]`）时，仍保持 `raise RuntimeError('未取得 ... 不能把空结果当成均衡或无宕机')` -> `status='error'`，绝不把空结果或异常写成正常。

---

## 二、`Batch010bDetector` 根因 A / B 全量排查结论（任务 2）

对 `monitor/inspection_detectors_010b.py` 全文（1523 行，全部 34 项检测器）进行了逐行排查：

1. **根因 A（`DictCursor` / 元组取值冲突）排查结果：无缺陷**
   - 全文件 grep 检查 `.fetchone(`、`.fetchall(`、`row[0]`/`row[1]`：34 项检测器**零处**直接按元组列下标取值；
   - 所有查询统一经 `_query(cur, sql)` -> `_fetchall(cur)`（`L207-L220`），已显式分支处理 `isinstance(rows[0], dict)`（`DictCursor`）与元组游标，统一输出小写键 `dict` 列表；
   - `SHOW` 语句统一经 `_show_map(rows)`（`L364-L375`）解析，同时兼容 `DictCursor`（`variable_name`/`value`）与无列名元组游标（`c0`/`c1`）。
2. **根因 B（裸 `except` 吞错致假绿）排查结果：无静默吞错**
   - 逐一审计全文件全部 10 处 `except`（`L190`、`_close_quiet` `L301/L306`、`_cursor` `L323`、`_run` `L349/L354/L356`、`_exadata` `L837`、`_gtid_consistency` `L872`、`_statement_timeout` `L1110/L1118`、`_dsc_nodes` `L1170/L1177`）：
     - 顶层 `_run()` 将 `NotApplicable` 映射为 `skip`，其余所有异常均调用 `degrade_note` 并置 `status='error'`；
     - 4 处内层 `try/except` 均为明确的视图探测或版本兼容回退（`v$cell` 缺失判非 Exadata、`SHOW SLAVE STATUS` 回退 `SHOW REPLICA STATUS`、`pg_file_settings`+`pg_db_role_setting` 双回退、`V$DSC_EP_INFO` 回退 `V$DSC_INSTANCES`），均记录 `degrade_note` 且回退失败时显式抛出 `RuntimeError`（落 `error`），无一处把报错吞成 `ok`。

---

## 三、GBase 8a 实库（`cid=11`）#69–#71 复测原文（任务 3）

2026-11-19 10:30 (+08:00) 在同一外网实库 `DatabaseConfig cid=11` 上通过 `InspectionExecutor._run_single` 逐项重跑 `#69–#71`，3 项均为 **`skip`**（零 `error`、零假绿），实测日志与返回原文如下：

### 3.1 `monitor.degrade` 运行留痕原文

```text
[2026-11-19 10:30:28,884] WARNING monitor.degrade rid=-: [degrade] inspection.detect_gbase_node_balance.no_cluster_view (第 1 次) 原因=实例无集群视图 gcluster_v$node_info (version=8.6.2.43-R7-free.110605, flag=0) 异常=OperationalError: (1046, 'No database selected')
[2026-11-19 10:30:28,891] WARNING monitor.degrade rid=-: [degrade] inspection.detect_gbase_distribution.no_cluster_view (第 1 次) 原因=实例无集群视图 gcluster_v$table_distribution (version=8.6.2.43-R7-free.110605, flag=0) 异常=OperationalError: (1046, 'No database selected')
[2026-11-19 10:30:28,899] WARNING monitor.degrade rid=-: [degrade] inspection.detect_gbase_node_down.no_cluster_view (第 1 次) 原因=实例无集群视图 gcluster_v$node_info (version=8.6.2.43-R7-free.110605, flag=0) 异常=OperationalError: (1046, 'No database selected')
```

### 3.2 三项逐项返回 JSON 原文与判定对照

| # | 规则编号 | 修复前 | 修复后实测状态 | 实测 `summary` 与 `metrics.gbase_cluster_evidence` 原文 |
|---:|---|---|---|---|
| 69 | `INS-GBASE-BALANCE`（节点负载均衡） | `error`（1046） | **`skip`**（`severity=info`, `findings=[]`, `error=None`） | `当前 GBase 8a 实例（8.6.2.43-R7-free.110605，gbase_gcluster_node_flag=0）全库无集群视图 gcluster_v$node_info（information_schema.TABLES 0 匹配），属非 MPP 集群/单机版，不适用节点负载均衡检查`<br>`{"view": "gcluster_v$node_info", "schema_matches": 0, "version": "8.6.2.43-R7-free.110605", "gbase_gcluster_node_flag": "0"}` |
| 70 | `INS-GBASE-DIST`（数据分布均衡） | `error`（1046） | **`skip`**（`severity=info`, `findings=[]`, `error=None`） | `当前 GBase 8a 实例（8.6.2.43-R7-free.110605，gbase_gcluster_node_flag=0）全库无集群视图 gcluster_v$table_distribution（information_schema.TABLES 0 匹配；gbase.table_distribution 为非同口径基表，不冒充），属非 MPP 集群/单机版，不适用数据分布均衡检查`<br>`{"view": "gcluster_v$table_distribution", "schema_matches": 0, "version": "8.6.2.43-R7-free.110605", "gbase_gcluster_node_flag": "0"}` |
| 71 | `INS-GBASE-NODE-DOWN`（节点宕机） | `error`（1046） | **`skip`**（`severity=info`, `findings=[]`, `error=None`） | `当前 GBase 8a 实例（8.6.2.43-R7-free.110605，gbase_gcluster_node_flag=0）全库无集群视图 gcluster_v$node_info（information_schema.TABLES 0 匹配），属非 MPP 集群/单机版，不适用节点宕机检查`<br>`{"view": "gcluster_v$node_info", "schema_matches": 0, "version": "8.6.2.43-R7-free.110605", "gbase_gcluster_node_flag": "0"}` |

---

## 四、新增单元测试与校验门禁记录

### 4.1 新增回归单测（`monitor/tests_inspection_db023.py`）

在不修改既有 `Db023DetectorTests` 断言的前提下，新增 `@tag('unit')` 测试类 `Db025GbaseViewResolutionTests`（3 个用例）：
1. `test_gbase_standalone_missing_cluster_views_returns_skip_with_evidence`：模拟单机版 `1046` + `information_schema.TABLES` 返回 `[]` + `VERSION()` + `gbase_gcluster_node_flag=0`，验证三项均返回 `skip` 并带完整 `gbase_cluster_evidence`；
2. `test_gbase_mpp_no_default_db_resolves_qualified_schema_and_detects`：模拟 MPP 集群未选默认库 `1046` + `information_schema.TABLES` 命中 `gclusterdb`，验证自动补全 schema 重查并准确输出 `critical` / `ok` / `warning`；
3. `test_gbase_permission_error_still_returns_error_not_skip`：模拟 `1142 SELECT command denied`，验证权限异常不被误转为 `skip`，仍落 `status='error'`。

### 4.2 校验门禁实测结果

| 校验项 | 执行结果 |
|---|---|
| `python3 scripts/scan_secrets.py` | **通过**（`密钥扫描通过`, exit 0） |
| `python3 scripts/lint_redlines.py` | **通过**（`红线约束扫描通过`, exit 0） |
| `manage.py check` & `makemigrations --check --dry-run` | **通过**（`System check identified no issues (0 silenced).` / `No changes detected`） |
| 定向单测（`monitor.tests_inspection_db023` + `Db024DetectorFixTests`, `--tag unit`） | **通过**（`Ran 14 tests in 0.080s`, `OK`, exit 0） |
| 全量 `unit`（`scripts/validate.sh unit --json` + `manage.py test --tag unit`） | 红线扫描 `pass` (`1515ms`)；全量 `Ran 467 tests in 20.888s`：**463 passed, 4 failed**（4 项失败全部为既有的 `DamengDriverVisibilityTests`，因本机 Windows `.venv` 为实库验证安装了 `dmPython` 导致 `is_dm_checker_available()` 为 `True`；本单严守红线未改既有测试，Linux CI 无 `dmPython` 预期全绿） |

---

## 五、DB-023 外网条件闭环说明

- **GBase 条件（#69–#71）**：已完成实库数据源核查、单机版 `skip` 口径与 MPP 未选库自动补全 schema 修复、实库复测与单测补强，**GBase 外网条件可关闭**。
- **剩余延后项**：Oracle 16 项与 MySQL GTID 仍按原口径延至上线后内网环境复测。
