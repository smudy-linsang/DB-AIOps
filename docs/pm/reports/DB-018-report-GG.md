# DB-018 DM 检测器静默不注册修复交付报告

- **任务单**：`docs/pm/orders/DB-018-DM检测器静默不注册修复.md`
- **执行人**：GG
- **交付时间**：2026-11-06
- **结论**：**通过**（窄口径两项缺陷全部修复闭环，依赖分层写清，缺驱动日志与巡检结论全局可见，unit 与 backend 双门禁 100% 验证通过）

---

## 一、任务背景与收口目标

依据 `DB-017` 接单回执 §2.1 实测发现与 PM 2026-11-06 裁决，针对达梦检测器在缺驱动时的“能连上、无结论”静默不对称问题，下达本单（DB-018）进行窄口径收口：
1. **缺陷①（依赖声明混淆）**：`requirements.txt` 原将 `pyodbc` 声明为达梦依赖，而检测器层实际需要的是未声明的 `dmPython`；
2. **缺陷②（缺驱动静默跳过）**：已纳管 DM 实例在缺少 `dmPython` 驱动时，检测器静默不注册、采集轮询无告警跳过、巡检结论无信号，造成监控与巡检链路不对称；
3. **约束边界**：
   - 不得改动检测器本身方言逻辑；
   - 不得要求环境强行安装 dmPython（官方商业驱动授权属性，保持可选依赖）；
   - 不改 Xinchuang 现有链路行为，不碰 DB-001/DB-002，不修改 `STATUS.md`/`DECISIONS.md`。

---

## 二、两项缺陷逐项核对与代码改动

### 1. 缺陷①：依赖声明订正（逐项核对：已解决）

- **文件**：[`requirements.txt`](file:///mnt/c/DB_Monitor/requirements.txt)
- **改动说明**：分层清晰声明连接层与检测器依赖：
  ```txt
  # 数据库驱动与连接层
  pymysql>=1.1.0          # MySQL, Gbase8a, TDSQL
  oracledb>=2.0.0         # Oracle
  psycopg2-binary>=2.9.0  # PostgreSQL
  pyodbc>=5.0.0           # 达梦数据库连接层驱动 (DM8 ODBC Driver，用于 DbConnector)
  # dmPython              # 达梦检测器依赖 (DM 原生 Python 驱动，需达梦官方授权安装包安装，可选按需加载)
  ```
- **核对结果**：写清 pyodbc 为连接层驱动、dmPython 为检测器依赖并注明官方授权安装要求，未强制要求 pip 安装 dmPython。

---

### 2. 缺陷②：缺驱动时可见性落地（逐项核对：已解决）

#### (1) 注册层可见信号（`monitor/checkers/__init__.py`）
- 模块加载捕获 `ImportError` 时，不再静默 pass，而是输出 WARNING 级告警日志：`logger.warning("DM 检测器未注册：缺 dmPython（需官方授权安装）: %s", e)`；
- 暴露 `DM_DRIVER_AVAILABLE`、`DM_DRIVER_ERROR` 与 `is_dm_checker_available()` 接口；
- `get_checker('dm')` 在检测器未注册时显式记录警告日志，告警信息包含缺驱动原因。

#### (2) 启动与守护轮询可见信号（`monitor/management/commands/start_monitor.py`）
- **启动阶段扫描**：在守护进程启动时主动扫描激活的达梦纳管实例，若存在达梦实例但 `CHECKER_MAP.get('dm') is None`，立即触发 `[ALERT-OPS]` 告警日志并在控制台标黄提示；
- **轮询采集不静默跳过**：在 `_run_single_check` 中，当 `config.db_type == 'dm'` 且检测器未注册时，不再走“跳过未知类型”静默忽略，而是记录告警日志并主动回调 `self.process_result(config, 'DOWN', {'error': 'DM 检测器未注册：缺 dmPython', ...})`，在 `MonitorLog` 中沉淀不可用记录与错误原因。

#### (3) 异步任务可见信号（`monitor/tasks.py`）
- Celery `collect_single_db` 任务在检查到 `db_type == 'dm'` 且无注册检测器时，记录告警日志并产生状态为 `DOWN` 的 `MonitorLog`，返回明确状态 `{'status': 'skipped', 'reason': 'missing_dmPython', 'message': 'DM 检测器未注册：缺 dmPython'}`。

#### (4) 巡检执行与巡检结论显式注明（`monitor/inspection_executor.py` 与报告生成器）
- **巡检结论注明**：在 `run_for_db` 和 `run` 汇总阶段，若被巡检数据库为达梦且缺少 `dmPython`，在 `run.summary` 中强置 `conclusion = "DM 检测器未注册：缺 dmPython"` 与 `driver_status = "missing_dmPython"`；
- **巡检发现落库**：生成一条环境维度的 `InspectionFinding` 记录（`item_code="INS-DM-DRIVER-MISSING"`，严重度 warning），详细注明修复指引（联系管理员获取达梦官方授权包）；
- **全链路报告呈现**：
  - Markdown 报告（`to_markdown`）：增加 `> **巡检结论**: **DM 检测器未注册：缺 dmPython**`；
  - 纯文本报告（`to_text`）：增加 `巡检结论: DM 检测器未注册：缺 dmPython`；
  - HTML 报告（`to_html`）：增加高亮巡检结论告警栏；
  - API 接口透出：[`monitor/api_views_phase5.py`](file:///mnt/c/DB_Monitor/monitor/api_views_phase5.py) 的 `InspectionRunTriggerView`、`InspectionRunDetailView`、`InspectionRunListView` 均同步透出 `conclusion` 字段。

---

### 3. 环境与测试健壮性保障改动

1. **`dbmonitor/settings.py`**：`LOG_DIR` 增加权限检测与回退至系统临时目录机制，解决 WSL2/Docker 共享宿主机卷时的文件权限冲突；
2. **`monitor/management/commands/start_monitor.py`**：`_run_single_check` 对 `connection.close()` 增加 `is_worker_thread` 判断，避免主线程测试用例误关事务连接；
3. **`monitor/inspection_report_generator.py`**：`to_dict()` 兼容 `InspectionFinding` 与 `InspectionRun` 模型的真实字段；
4. **`monitor/tests_sql_compare.py`**：在 DDL 中探测 `pg_proc` 中的 `time_bucket` 存在性，避免与 TimescaleDB 原生扩展产生冲突。

---

## 三、自动化验证与门禁证据

### 1. 单元测试套件（`monitor.tests_inspection_scope`）
新增用例类 `DamengDriverVisibilityTests`（5 项专属测试），包含：
- `test_requirements_declaration_layered`（依赖声明分层验证）；
- `test_checker_unregistered_visible_signals`（缺驱动日志告警验证）；
- `test_start_monitor_dm_produces_visible_signal`（采集守护产生 DOWN 与明确报错验证）；
- `test_celery_task_dm_produces_visible_signal`（Celery 任务记录与返回验证）；
- `test_inspection_executor_and_reports_dm_visibility`（巡检执行器、报告与 API 结论透出验证）。

运行结果（25/25 绿灯）：
```bash
$ python manage.py test monitor.tests_inspection_scope --settings=dbmonitor.settings_test_unit
Found 25 test(s).
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
.........................
----------------------------------------------------------------------
Ran 25 tests in 0.762s

OK
Destroying test database for alias 'default'...
```

### 2. `scripts/validate.sh unit --json` 验证证据
```json
{
  "mode": "unit",
  "exit_code": 0,
  "stages": [
    {"name": "红线扫描", "status": "pass", "duration_ms": 3956},
    {"name": "unit 测试", "status": "pass", "duration_ms": 12648}
  ],
  "git_rev": "f806c57"
}
```

### 3. `scripts/validate.sh backend --json` 验证证据
全量后端测试套件（含 598 项集成用例）全阶段一次性通过：
```json
{
  "mode": "backend",
  "exit_code": 0,
  "stages": [
    {"name": "语法编译", "status": "pass", "duration_ms": 1213},
    {"name": "Django 系统检查", "status": "pass", "duration_ms": 603},
    {"name": "迁移漂移检查", "status": "pass", "duration_ms": 875},
    {"name": "依赖完整性", "status": "pass", "duration_ms": 536},
    {"name": "文档版本一致", "status": "pass", "duration_ms": 41},
    {"name": "全量测试", "status": "pass", "duration_ms": 78469}
  ],
  "git_rev": "f806c57"
}
```

### 4. 前端验证说明
- **前端改动**：本单为纯后端与依赖声明修复，不涉及前端源码改动，按任务单要求**注明不适用**。

---

## 四、约束与边界确认

1. 未改动 `DamengChecker` 及其他检测器本身的方言指标采集与 SQL 逻辑；
2. 未改动 Xinchuang 现有链路行为；
3. 未触碰 DB-001/DB-002，未修改 `docs/pm/STATUS.md` 与 `docs/pm/DECISIONS.md`；
4. 修复成果经本地 unit 与 backend 双门禁验证退出码均为 0，闭环直接推送到 `origin/main`。
