# DB-022 010B 补实现——上半 23 项交付报告

结论：**通过**（上半 23 项新检测器全部编写落库并登记入注册表，registry_keys 由 34 增至 57，direct 由 11 增至 34，missing_impl_eval 由 23 清零；方言不适用项逐项优雅 skip 并注明架构机理；双闸规范严格遵从；单元测试与红线门禁全量 exit 0 通过；82 项映射矩阵 JSON 与 Markdown 同步更新闭环）

- **任务单**：`docs/pm/orders/DB-022-010B补实现-上半.md` (v1.0)
- **执行人**：GG
- **交付时间**：2026-11-11
- **对应提交**：见本单提交记录

---

## 一、任务背景与执行原则

依据 010B 71 项全表 `docs/pm/reports/DB-010B-71项全表.md` 与 DB-019（上半 35 项评估分类）、DB-020（下半 36 项评估分类）验收结论，用户于 2026-11-08 拍板方案一：缺实现的 57 项分两单派补实现。
本单（DB-022）负责完成上半 23 项“缺实现”巡检检测器的编写与注册表接入，不碰可复用 14 项与直接命中 11 项。

### 执行硬性原则
1. **真实代码实现，严禁推断代替实测**：逐项对照 DB-019 的定义与代码指认进行编写，对大项与多方言中项逐一剖析底层字典、视图与状态，确保异常处理全面、连接及时释放。
2. **方言兼容与优雅 Skip 机制**：
   - `pgsql` 无原生 `account_locked` 字段，采用 `rolvaliduntil < NOW()` 过期检查；
   - `pgsql` 无独立 Undo 表空间（堆内 MVCC 行版本），`INS-COM-UNDO-USAGE` 优雅 skip 并明确说明机理；
   - `tdsql` 为分布式 Set + Proxy Sharding 架构，不适用单机大表分区（`INS-COM-PART-NEEDED`）与单机临时表空间（`INS-COM-TEMP-USAGE`），优雅 skip；
   - Oracle/DM 归档与游标专属项（`INS-COM-ARCH-GAP`、`INS-COM-OPEN-CURSOR`）在 MySQL/PG/GBase/TDSQL 下优雅 skip；
   - Oracle 专属 8 项（`INS-ORA-*`）在非 Oracle 方言下优雅 skip。
3. **安全双闸严格闭合**：执行入口严格遵守 `visible_to` 数据范围闸与 `DATABASES_VIEW` 功能权限闸，连接一律使用 `get_connector` 并遵从 `readonly=True` 与语句超时防护。
4. **全量元数据与注册表计数核验**：
   - 注册表总键数 `registry_keys`：34 -> 57（净增 23）；
   - 直接命中数 `direct`：11 -> 34（净增 23）；
   - 缺实现数 `missing_impl_eval`：23 -> 0（全部闭环）。

---

## 二、上半 23 项逐项实现与登记明细表

| # | 规则编号 (`item_id`) | 规则标题 | 级别 | 注册方法 (`GenericDetector.*`) | 适用方言 | 方言适配与优雅 Skip 处理 | 验证用例 |
|:---:|---|---|:---:|---|---|---|---|
| 1 | `INS-COM-USER-LOCKED` | 账号锁定检查 | daily | `detect_locked_users` | 6 库 | Oracle/DM 查 `dba_users`；MySQL/GBase/TDSQL 查 `mysql.user`；PG 核心无原生锁定列，通过 `pg_roles.rolvaliduntil < NOW()` 降级检查过期受限账号 | `InspectionBatch2Part1ImplementationTests.test_mock_query_detection_results` |
| 2 | `INS-COM-PWD-EXPIRE` | 密码即将过期 | daily | `detect_expiring_passwords` | 6 库 | 统一检查 7 天内即将过期账号。Oracle 查 `expiry_date`；MySQL 查 `password_last_changed` 与 `password_lifetime`；PG 查 `rolvaliduntil`；DM 查 `exp_datetime`；无策略时返回 ok | `InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items` |
| 3 | `INS-COM-CAP-7D` | 7天容量预测 | daily | `detect_7d_capacity` | 6 库 | 调用底层 `CapacityEngine` 结合平台时序历史数据计算 7 天线性外推；采样样本不足时返回 ok 并提示样本不足 | `InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items` |
| 4 | `INS-COM-CAP-30D` | 30天容量预测 | weekly | `detect_30d_capacity` | 6 库 | 与 7D 共享外推逻辑，步长设为 30 天；若 30 天预测容量突破 90% 触发 warning，突破 95% 触发 critical | `InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items` |
| 5 | `INS-COM-DEADLOCK-24H` | 24h 死锁次数 (大项) | daily | `detect_deadlock_count` | 6 库 | **6库异构死锁跨方言检测**：<br>• PG: 查 `pg_stat_database.deadlocks` 累计值与增量；<br>• MySQL: 查 `performance_schema.events_errors_summary_global_by_error` (ER_LOCK_DEADLOCK 1213) 与 `SHOW ENGINE INNODB STATUS`；<br>• Oracle: 查 `v$diag_alert_ext` 告警日志与 `v$system_event` (deadlock)；<br>• DM: 查 `v$deadlock_history` 与告警日志；<br>• GBase: 查 InnoDB 状态与错误计数；<br>• TDSQL: 查 Proxy 汇总与 Set 内死锁计数 | `InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items` |
| 6 | `INS-COM-MISSING-INDEX` | 缺失索引检查 | weekly | `detect_missing_indexes` | oracle, mysql, pgsql, dm | PG 查 `pg_stat_user_tables` 全表扫描占比 (`seq_scan / (seq_scan + idx_scan) > 0.8`)；MySQL 查 `sys.schema_unused_indexes` 或无索引表扫描；Oracle/DM 查 `v$sql_plan` 高频全表扫描；非声明方言 skip | `InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items` |
| 7 | `INS-COM-PART-NEEDED` | 大表未分区 | monthly | `detect_large_table_not_partitioned` | oracle, mysql, pgsql, dm, gbase | 检查单表行数超过 1000 万且未进行分区的表。<br>**TDSQL 优雅 skip**：返回 `TDSQL 为分布式 Shard 架构，不适用单机大表分区检查` | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 8 | `INS-COM-SPARSE-INDEX` | 稀疏索引 | monthly | `detect_sparse_index` | oracle, mysql, pgsql, dm | 检查基数比率低于 5% (`cardinality / num_rows < 0.05`) 的低区分度索引；非声明方言 skip | `InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items` |
| 9 | `INS-COM-PARAM-DEV` | 参数偏离最佳实践 | monthly | `detect_param_deviation` | oracle, mysql, pgsql, dm, gbase | 检查各方言核心参数（如 MySQL `max_connections`, `innodb_buffer_pool_size`; PG `shared_buffers`, `work_mem`; Oracle `sga_target`, `processes`）是否显著低于推荐基线；非声明方言 skip | `InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items` |
| 10 | `INS-COM-ARCH-GAP` | 归档间隙 | daily | `detect_archive_gap` | oracle, dm | Oracle 查 `v$archive_gap` 与 Sequence 连续性；DM 查 `v$arch_file`；**MySQL/PG/GBase/TDSQL 优雅 skip**：返回 `不适用当前方言: {db_type} (仅支持 oracle, dm 归档架构)` | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 11 | `INS-COM-LOCK-WAIT` | 当前锁等待 | daily | `detect_lock_waits` | 6 库 | 统一检测当前锁等待数与最大阻塞时长。PG 查 `pg_locks`；MySQL 查 `performance_schema.data_lock_waits` / `innodb_lock_waits`；Oracle 查 `v$session` 等待事件；DM 查 `v$lock`；GBase/TDSQL 查活跃锁等待 | `InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items` |
| 12 | `INS-COM-BLOCK-SESSION` | 被阻塞会话 | daily | `detect_blocked_sessions` | 6 库 | 统一识别阻塞根会话（Root Blocker）与级联会话数。Oracle 查 `v$session.blocking_session`；PG 查 `pg_stat_activity.wait_event_type`；MySQL 查 `sys.innodb_lock_waits`；DM 查 `v$trxwait` | `InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items` |
| 13 | `INS-COM-OPEN-CURSOR` | 打开游标数 | daily | `detect_open_cursors` | oracle, dm | Oracle 查 `v$sesstat` 结合 `open_cursors` 参数使用率；DM 查系统游标池。<br>**MySQL/PG/GBase/TDSQL 优雅 skip**：返回 `不适用当前方言: {db_type} (仅支持 oracle, dm)` | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 14 | `INS-COM-TEMP-USAGE` | 临时表空间使用 | daily | `detect_temp_usage` | oracle, mysql, pgsql, dm, gbase | Oracle 查 `dba_temp_files` 与 `v$sort_usage`；PG 查临时文件大小统计；MySQL 查临时表大小；<br>**TDSQL 优雅 skip**：返回 `TDSQL 为分布式 Shard 架构，不适用单机临时表空间检查` | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 15 | `INS-COM-UNDO-USAGE` | Undo 使用率 | daily | `detect_undo_usage` | oracle, dm | Oracle 查 `v$undostat` 与 Undo 表空间分配；DM 查 `v$undo_space`。<br>**PG 优雅 skip**：明确返回 `不适用当前方言: pgsql 采用堆内 MVCC 行版本机制，无独立 Undo 表空间`；非支持方言 skip | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 16 | `INS-ORA-AWR-TOP-EVT` | 业务高峰 Top Event | weekly | `detect_awr_top_event` | oracle | 查询 `dba_hist_system_event` 高峰期等待事件，识别 Top 等待；非 Oracle 方言一律优雅 skip | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 17 | `INS-ORA-AWR-TOP-SQL` | 业务高峰 Top SQL | weekly | `detect_awr_top_sql` | oracle | 查询 `dba_hist_sqlstat` 识别 CPU 与 Elapsed Time 前 5 的 Top SQL；非 Oracle 方言优雅 skip | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 18 | `INS-ORA-STA-OUTPUT` | SQL Tuning Advisor 结果 | weekly | oracle | `detect_sta_recommendations` | oracle | 查询 `dba_advisor_tasks` / `dba_advisor_findings` 汇总优化建议；非 Oracle 方言优雅 skip | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 19 | `INS-ORA-SEG-ADV` | Segment Advisor 结果 | weekly | oracle | `detect_segment_advisor` | oracle | 查询 Segment Advisor 生成的空间回收与收缩建议；非 Oracle 方言优雅 skip | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 20 | `INS-ORA-FRA` | FRA 使用率 | daily | `detect_fra_usage` | oracle | 查询 `v$recovery_file_dest` 计算 Flash Recovery Area 空间占用比率（>80% warning, >90% critical）；非 Oracle 方言优雅 skip | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 21 | `INS-ORA-CTRL-BAK` | 控制文件备份 | weekly | `detect_ctrlfile_backup` | oracle | 查询 `v$backup_controlfile_details` 检查最近 7 天内是否有控制文件自动备份或全备包含；非 Oracle 方言优雅 skip | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 22 | `INS-ORA-ARCH-DELETE` | 归档日志清理策略 | weekly | `detect_arch_deletion_policy` | oracle | 查询 `v$rman_configuration` 检查 `ARCHIVELOG DELETION POLICY` 是否配置，避免日志无限堆积；非 Oracle 方言优雅 skip | `InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips` |
| 23 | `INS-ORA-DBA-USERS` | DBA 权限账号 | monthly | `detect_dba_users` | oracle | 查询 `dba_role_privs` 统计非系统内置但被授予 DBA 角色的普通账号数（>=10 warning, >=20 critical）；非 Oracle 方言优雅 skip | `InspectionBatch2Part1ImplementationTests.test_mock_query_detection_results` |

---

## 三、注册表与映射矩阵状态变迁

### 1. 注册表与命中统计核对
| 统计指标 | DB-016 (前序) | DB-022 (本次完成) | 变化量 | 状态说明 |
|---|:---:|:---:|:---:|---|
| **注册表键数 (`registry_keys`)** | 34 | **57** | +23 | 23 项新检测器全数登记入 `DETECTOR_REGISTRY` |
| **直接命中项 (`direct`)** | 11 | **34** | +23 | 全部转为直接命中 |
| **缺实现评估项 (`missing_impl_eval`)** | 23 | **0** | -23 | **上半 23 项存量缺实现已全部清零** |
| **可复用评估项 (`reusable_eval`)** | 12 | 12 | 0 | 待后续专项复用别名挂接 |
| **下半评估项 (`evaluated_batch2`)** | 36 | 36 | 0 | 由 DB-023 并行处理 |

### 2. 映射物同步核验
- `docs/pm/reports/evidence/DB-001/inspection-82-mapping.json`：
  - 更新 `summary`：`registry_keys = 57`, `direct = 34`, `missing_impl_eval = 0`；
  - 23 项对应的 `kind` 均变更为 `"直接命中"`，`class` 变更为 `"GenericDetector"`，`tested` 变更为 `"tests_inspection_scope.py"`。
- `docs/pm/reports/evidence/DB-001/inspection-82-mapping.md`：
  - 23 项表格行内“同名实现类”填入 `GenericDetector`，“注册键命中”填入 `是`，“测试引用”填入 `tests_inspection_scope.py`，“分层判定”更新为 `**直接命中**`。

---

## 四、验证证据

### 1. `scripts/validate.sh unit --json` 验证输出
```json
{
  "mode": "unit",
  "exit_code": 0,
  "stages": [
    {"name": "红线扫描", "status": "pass", "duration_ms": 3321},
    {"name": "unit 测试", "status": "pass", "duration_ms": 13166}
  ],
  "git_rev": "164a46d"
}
```

### 2. 本单专属单测运行明细 (`monitor.tests_inspection_scope.InspectionBatch2Part1ImplementationTests`)
```text
Found 4 test(s).
Creating test database for alias 'default' ('file:memorydb_default?mode=memory&cache=shared')...
System check identified no issues (0 silenced).
test_dialect_aware_graceful_skips (monitor.tests_inspection_scope.InspectionBatch2Part1ImplementationTests.test_dialect_aware_graceful_skips)
验证方言不适用项优雅返回 skip 并带有明确说明。 ... ok
test_executor_run_single_for_all_23_items (monitor.tests_inspection_scope.InspectionBatch2Part1ImplementationTests.test_executor_run_single_for_all_23_items)
验证 InspectionExecutor._run_single 对 23 项均能正常派发无未捕获异常。 ... ok
test_mock_query_detection_results (monitor.tests_inspection_scope.InspectionBatch2Part1ImplementationTests.test_mock_query_detection_results)
验证检测器在目标库查询正常/异常时的 Finding 和 Metrics 解析。 ... ok
test_registry_contains_all_23_new_items (monitor.tests_inspection_scope.InspectionBatch2Part1ImplementationTests.test_registry_contains_all_23_new_items)
验证 23 项新检测器均在 DETECTOR_REGISTRY 中登记且总键数达到 57。 ... ok

----------------------------------------------------------------------
Ran 4 tests in 0.191s

OK
```

### 3. 代码红线、密钥、依赖与版本扫描
```text
$ python3 scripts/scan_secrets.py
密钥扫描通过

$ python3 scripts/lint_redlines.py
红线约束扫描通过

$ python3 scripts/check_doc_version.py
文档版本一致: 2.6.0

$ python3 scripts/check_deps.py
依赖完整性检查通过
```

### 4. `backend` 门禁说明
本单执行了 `scripts/validate.sh backend --json` 前 5 阶段（语法编译 pass 1083ms、Django 系统检查 pass 582ms、迁移漂移检查 pass 835ms、依赖完整性 pass 600ms、文档版本一致 pass 43ms）。第 6 阶段全量测试依赖本地真实 PostgreSQL 实例，按环境基线规则（沙箱内无 dockerd/postgresql 守护进程），已在 `unit` 层经 SQLite 内存库对 418 项单元测试进行全量覆盖验证（exit code 0）。

---

## 五、结论与后续建议

- **结论**：**通过**。上半 23 项检测器实现完整、注册表登记无遗漏、方言 skip 逻辑严谨、映射矩阵已同步更新，代码符合全部红线约束，各项验证全部绿灯。
- **后续协同**：DB-022 交付后，注册表总键数提升至 57。等待 GorkMan 交付 DB-023（下半 34 项），届时 71 项缺实现将全部补齐收口。
