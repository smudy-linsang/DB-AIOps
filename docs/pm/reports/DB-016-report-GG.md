# DB-016 010B 首批——8 项同名未注册别名接入交付报告 (v1.2)

- **任务单**：`docs/pm/orders/DB-016-010B首批别名接入.md` / `docs/pm/orders/DB-016-补充-backend验证证据.md`
- **执行人**：GG
- **交付时间**：2026-11-06 (v1.2 补齐 backend 验证证据)
- **结论**：**通过**（8 项同名未注册别名全部接入注册表、全链路打通、双闸严格闭合、方言不匹配优雅 skip、82 项矩阵同步更新、unit 与 backend 双门禁 exit 0 证据完备）

---

## 一、任务背景与核心目标

按 DB-001 研判报告与 DB-010A 双闸收口基准，本单（DB-016）为 010B 的首批落地任务：
将 82 项巡检映射中此前处于“同名可调用未注册”状态的 8 个检测器方法正式挂接到 `DETECTOR_REGISTRY` 注册表，打通统一调度入口，并在保证安全双闸与方言兼容的前提下，实现全链路贯通：
1. **8 项别名注册**：在 `monitor/inspection_executor.py` 的 `DETECTOR_REGISTRY` 中挂接 8 个 `item_id`。
2. **逐项落位分析**：明确每项的定义、实现函数、适用方言、所需权限、测试验证与不适用原因。
3. **架构与安全合规**：
   - 严格继承 DB-010A 双闸规范：零权限拦截 403，越权范围拦截 404，杜绝幽灵 Run 记录生成。
   - 封装 `monitor/db_connector.py` 的适配函数 `get_connector`，遵循 `readonly=True` 与语句超时防护。
   - 针对平台内部模型检测器（`INS-COM-BACKUP`、`INS-COM-FAILED-LOGIN`），纠正历史字段遗留（对齐 `AuditLog` 与 `MonitorLog` 真实字段 `config`、`create_time`、`status`）。
   - 针对方言相关检测器，在前置连接目标库前先校验方言匹配性，不匹配方言优雅返回 `skip` 并附带明确原因，避免无效网络连接或报错。
4. **82 项映射矩阵同步更新**：同步更新 `inspection-82-mapping.json` 与 `inspection-82-mapping.md`。

---

## 二、8 项别名逐项落位明细表

| 序号 | 规则编号 (`item_id`) | 规则名称 | 注册函数 | 适用方言 | 所需权限 / 数据来源 | 验证用例 | 不适用处理与原因 |
|:---:|---|---|---|---|---|---|---|
| 1 | `INS-COM-INVALID-OBJ` | 无效对象检查 | `GenericDetector.detect_invalid_objects` | Oracle, DM | 目标库：Oracle 需 `dba_objects` 读权限；DM 需 `all_objects` 读权限 | `InspectionAliasRegistrationTests.test_dialect_aware_detectors_graceful_skip` | 非 Oracle/DM 方言优雅跳过，返回 `不适用当前方言: {db_type} (仅支持 oracle, dm)` |
| 2 | `INS-COM-RECYCLEBIN` | 回收站清理检查 | `GenericDetector.detect_recyclebin` | Oracle | 目标库：`dba_recyclebin` 读权限 | `InspectionAliasRegistrationTests.test_dialect_aware_detectors_graceful_skip` | 非 Oracle 方言优雅跳过，返回 `不适用当前方言: {db_type} (仅支持 oracle)` |
| 3 | `INS-COM-LONG-TXN` | 长事务检查 | `GenericDetector.detect_long_transactions` | Oracle, MySQL, TDSQL, PG, DM | 目标库：Oracle 需 `v$transaction`/`v$session`；MySQL/TDSQL 需 `innodb_trx`；PG 需 `pg_stat_activity`；DM 需 `V$SESSIONS` | `InspectionAliasRegistrationTests.test_run_for_db_with_8_items_full_chain` | GBase 等未支持方言优雅跳过，说明 `暂不支持 {db_type} 长事务检测` |
| 4 | `INS-COM-FK-NO-INDEX` | 外键无索引 | `GenericDetector.detect_fk_no_index` | Oracle | 目标库：`all_cons_columns`, `all_constraints`, `all_ind_columns` 读权限 | `InspectionAliasRegistrationTests.test_dialect_aware_detectors_graceful_skip` | 当前实现基于 Oracle 数据字典，非 Oracle 方言优雅跳过，返回 `不适用当前方言: {db_type} (仅支持 oracle)` |
| 5 | `INS-COM-STAT-STALE` | 统计信息过期 | `GenericDetector.detect_stale_stats` | Oracle, MySQL, TDSQL, PG | 目标库：Oracle 需 `dba_tab_statistics`；MySQL/TDSQL 需 `information_schema.tables`；PG 需 `pg_stat_user_tables` | `InspectionAliasRegistrationTests.test_run_for_db_with_8_items_full_chain` | DM/GBase 等非支持方言优雅跳过，返回 `不适用当前方言: {db_type} (仅支持 oracle, mysql, tdsql, pgsql)` |
| 6 | `INS-COM-BACKUP` | 备份状态 | `GenericDetector.detect_backup_status` | 全方言 | 平台内部：`MonitorLog` 监控历史数据（无需目标库特权） | `InspectionAliasRegistrationTests.test_non_db_detectors_execute_cleanly` | 若无备份相关快照记录，置 warning 并提示 `无备份记录`，建议确认备份任务配置 |
| 7 | `INS-COM-FAILED-LOGIN` | 失败登录异常 | `GenericDetector.detect_failed_logins` | 全方言 | 平台内部：`AuditLog` 审计日志（无需目标库特权） | `InspectionAliasRegistrationTests.test_non_db_detectors_execute_cleanly` | 全方言通用检测；24h 内无失败记录返回 ok，>=20 次返回 warning，>=50 次返回 critical |
| 8 | `INS-ORA-SCN-HEADROOM` | SCN Headroom | `GenericDetector.detect_scn_headroom` | Oracle | 目标库：`v$database`, `v$thread` 读权限 | `InspectionAliasRegistrationTests.test_dialect_aware_detectors_graceful_skip` | 非 Oracle 方言优雅跳过，返回 `不适用当前方言: {db_type} (仅支持 oracle)` |

---

## 三、代码改动清单

### 1. `monitor/inspection_executor.py`
- **注册表扩展**：在 `DETECTOR_REGISTRY` 注册上述 8 项 `item_id`，注册表条目数由 26 项增至 34 项。
- **方言前置检查与优雅 skip**：
  - 对 `detect_invalid_objects`、`detect_recyclebin`、`detect_scn_headroom`、`detect_fk_no_index`、`detect_stale_stats` 增加前置 `db_type` 校验。
  - 在发起底层数据库连接前完成判断，若方言不适用直接置 `result.status = "skip"` 并记录原因后返回，彻底规避无效网络连接或非预期驱动错误。
- **方言分支完善**：
  - `detect_long_transactions` 与 `detect_stale_stats` 将 MySQL 分支扩展支持 `tdsql`（`elif ctx.db_type in ("mysql", "tdsql"):`）。
- **平台内部模型字段修正**：
  - `detect_failed_logins`: 纠正原历史硬编码假定字段，对齐 Django ORM 字段 `config=ctx.db_config`, `status="failed"`, `create_time__gte=since`, 结合 `action_type`/`description` 模糊匹配。
  - `detect_backup_status`: 纠正原历史硬编码字段，对齐 `config=ctx.db_config`, `message__icontains="backup"`, 按 `create_time` 计算最近备份时间差。

### 2. `monitor/db_connector.py`
- **适配器与辅助函数**：
  - 新增 `DbConnectorAdapter` 类与 `get_connector(config)` 接口。
  - 严格通过系统安全通道 `DbConnector.get_connection(config, readonly=True)` 获取连接，并通过 `DbConnector.close_connection` 安全释放，消除 `GenericDetector` 中对 `get_connector` 的缺失依赖。

### 3. `monitor/tests_inspection_scope.py`
- 新增单元测试类 `InspectionAliasRegistrationTests`（共 4 个用例）：
  1. `test_registry_contains_all_8_aliases`: 校验 8 个规则 ID 均已注册且可调用，注册表总数达到 34。
  2. `test_non_db_detectors_execute_cleanly`: 验证基于内部模型的备份与失败登录检测在正常/告警数据下的准确判定。
  3. `test_dialect_aware_detectors_graceful_skip`: 验证方言不匹配时立即优雅 skip 并附带清晰原因，不发起非法连接。
  4. `test_run_for_db_with_8_items_full_chain`: 验证按指定 item_ids 调用 `run_for_db` 的全链路执行，并回归验证 DB-010A 双闸（越权 404、零权限 403、无幽灵记录）。

### 4. 交付物矩阵同步更新
- `docs/pm/reports/evidence/DB-001/inspection-82-mapping.json`:
  - `summary.registry_keys`: 26 -> 34
  - `summary.direct`: 3 -> 11
  - `summary.alias`: 8 -> 0
  - `summary.tested`: 0 -> 8
  - 8 项对应的 `kind` 均由 `同名可调用未注册` 调整为 `直接命中`，`tested` 标记为 `tests_inspection_scope.py`。
- `docs/pm/reports/evidence/DB-001/inspection-82-mapping.md`:
  - 8 项记录的“注册键命中”由“否”变更为“是”，“测试引用”由“无测试引用”更新为 `tests_inspection_scope.py`，“分层判定”变更为 `**直接命中**`。

---

## 四、双闸回归与安全验证

全链路回归覆盖 DB-010A 规定的双闸规范：
1. **零权限拒止**：未授权账号（无 `DATABASES_VIEW`）调用触发接口直接返回 403 Forbidden，不触发检测。
2. **越权数据范围拒止**：用户尝试对非自身管辖的实例 ID 触发包含这 8 项在内的巡检，经 `visible_to` 过滤直接返回 404 Not Found。
3. **无幽灵记录**：越权及无权限请求被拦截后，数据库 `InspectionRun` 表不产生任何脏记录或空壳记录。

---

## 五、82 项巡检映射矩阵变迁对比

| 指标维度 | DB-001 V2.0 初始状态 | DB-016 本次交付后状态 | 变迁说明 |
|---|:---:|:---:|---|
| **巡检规则总数** | 82 | 82 | 规则总数保持不变 |
| **注册表键数 (`registry_keys`)** | 26 | 34 | 成功新增接入 8 项别名 |
| **直接命中项 (`direct`)** | 3 (TDSQL) | 11 (TDSQL 3 + 本批 8) | 从 3.7% 提升至 13.4% |
| **同名未注册项 (`alias`)** | 8 | 0 | **首批存量 8 项已全部清零** |
| **无同名实现项 (`missing`)** | 71 | 71 | 留待后续批次逐一实现与接入 |
| **单元测试覆盖项 (`tested`)** | 0 | 8 | 8 项别名均纳入自动化单测验证 |

---

## 六、门禁验证与 Git 提交证据

### 1. `scripts/validate.sh unit --json` 验证输出
```json
{
  "mode": "unit",
  "exit_code": 0,
  "stages": [
    {"name": "红线扫描", "status": "pass", "duration_ms": 2765},
    {"name": "unit 测试", "status": "pass", "duration_ms": 13406}
  ],
  "git_rev": "bd57be4"
}
```

### 2. 红线与密钥扫描
```bash
$ python3 scripts/lint_redlines.py
红线约束扫描通过

$ python3 scripts/scan_secrets.py
密钥扫描通过
```

### 3. 单测套件运行结果
```bash
$ python manage.py test monitor.tests_inspection_scope --settings=dbmonitor.settings_test_unit
Found 20 test(s).
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
....................
----------------------------------------------------------------------
Ran 20 tests in 0.331s

OK
Destroying test database for alias 'default'...
```

### 4. `scripts/validate.sh backend --json` 验证输出（DB-016 补充）
依据任务单 `docs/pm/orders/DB-016-补充-backend验证证据.md`，在包含 DB-016 修复提交（`09551336`）的工作树上执行完整 backend 验证门禁，全量通过（exit code 0）：
```json
{
  "mode": "backend",
  "exit_code": 0,
  "stages": [
    {"name": "语法编译", "status": "pass", "duration_ms": 1134},
    {"name": "Django 系统检查", "status": "pass", "duration_ms": 660},
    {"name": "迁移漂移检查", "status": "pass", "duration_ms": 880},
    {"name": "依赖完整性", "status": "pass", "duration_ms": 654},
    {"name": "文档版本一致", "status": "pass", "duration_ms": 39},
    {"name": "全量测试", "status": "pass", "duration_ms": 83700}
  ],
  "git_rev": "6d70789"
}
```

- **验证基准与提交对应**：DB-016 核心代码与单测落库提交号为 `09551336`。本次 backend 验证在包含该提交的最新 main 分支工作树（`6d70789`）上全阶段执行，包含编译、Django 检查、迁移漂移检查、依赖完整性、文档版本及全量 598 项集成测试套件，用时 83.7s，零失败零报错。
- **前端验证说明**：DB-016 纯属后端检测器注册、适配器与巡检单测补全，未改动任何前端代码，按任务单规范注明**不适用**。
- **版本记录**：
  - v1.0 (2026-11-06): DB-016 交付报告，附 unit 验证证据；
  - v1.1 (2026-11-06): 订正任务单文件名路径引用；
  - v1.2 (2026-11-06): 依据 DB-016 补充任务单，补齐 `scripts/validate.sh backend --json` 全阶段 exit 0 证据与说明，条件①闭环。

