# DB-010A 巡检安全/契约/覆盖呈现收口交付报告

- **任务单**：`docs/pm/orders/DB-010A-巡检安全契约覆盖呈现收口.md`
- **执行人**：GG
- **交付时间**：2026-11-05
- **结论**：**通过**（R1 权限与数据范围双闸闭合、R2 执行契约与全链路验证通过、覆盖呈现三档区分落地）
- **阻断关系解除说明**：满足 DB-013 条件②（先于任何执行入口放通闭合）。

---

## 一、任务背景与收口目标

按 DB-001 V2.2 基线与 DB-013 条件②要求，在放通任何巡检或执行入口前，必须先收口安全、契约与覆盖率呈现缺陷：
1. **R1**：巡检详情与触发接口补齐功能权限闸（`require_permission`）与实例/Run 范围闸（`visible_to`）；覆盖“零权限”与“有权限但范围外”回归；排查 Phase 5 其余入口。
2. **R2**：`InspectionExecutor` 构造与 `run_for_db`/`run` 契约统一，修复 `POST /api/v1/inspection/runs/trigger/` 的 400 崩溃，打通 API→执行器→持久化→详情展示全链路。
3. **覆盖率呈现**：健康分与覆盖状态解耦，区分**未评估（unassessed）/ 覆盖不足（insufficient_coverage）/ 正常（normal）**，杜绝 1 ok + 79 skip 伪装 100/100 绿灯。

---

## 二、代码改动清单

### 1. `monitor/api_views_phase5.py`
- **双闸闭合**：
  - `AlertRCADetailView`: 挂 `@require_permission(Perm.ALERTS_VIEW)`，实例过滤使用 `DatabaseConfig.objects.visible_to(request.user)`。
  - `AlertRCAQuickView`: 挂 `@require_permission(Perm.ALERTS_VIEW)`。
  - `InspectionRunListView`: 挂 `@require_permission(Perm.DATABASES_VIEW)`，列表查询过滤 `db_config__in=visible_dbs`。
  - `InspectionRunTriggerView`: 挂 `@require_permission(Perm.DATABASES_VIEW)`，触发前先校验 `DatabaseConfig.objects.visible_to(request.user).filter(id=config_id).first()`，未找到或范围外直接返回 404，不生成任何幽灵记录。
  - `InspectionRunDetailView`: 挂 `@require_permission(Perm.DATABASES_VIEW)`，查询校验 `db_config__in=visible_dbs`，范围外返回 404，不泄露实例名与 Findings。
  - `InspectionItemListView`: 挂 `@require_permission(Perm.DATABASES_VIEW)`。
  - `InspectionIssuePatternListView`: 挂 `@require_permission(Perm.DATABASES_VIEW)`。
  - `AlertCaseListView` / `AlertCaseSearchView`: 挂 `@require_permission(Perm.ALERTS_VIEW)`。
  - `Phase5StatsView`: 挂 `@require_permission(Perm.DATABASES_VIEW)`。
- **契约调用**：
  - `InspectionRunTriggerView` 调用统一执行契约 `InspectionExecutor(config, level=level, triggered_by='api').run()`，同步返回 `run_id`、`status`、`total_items`、`executed_items`、`passed_items`、`failed_items`、`health_score`、`coverage_status`、`coverage_rate`。

### 2. `monitor/inspection_executor.py`
- **契约统一**：
  - `__init__` 同时兼容旧参数 `InspectionExecutor(max_workers=4)` 与面向对象调用 `InspectionExecutor(db_config, level="daily")`。
  - 增加 `run() -> Dict[str, Any]` 业务方法，统一调用 `run_for_db` 并返回完整汇总。
  - `run_id` 增加 UUID 随机段 `f"INSP-{timestamp}-{uuid[:6]}-{db_id}"`，解决同秒并发与测试运行中的主键唯一约束冲突。
- **4NF 子表持久化陷阱修复**：
  - `_sync_items_to_db` 中，在 `ItemModel.objects.update_or_create` 完成主模型持久化之后再给 `applicable_db_types` 赋值，避免 setter 在 `__init__` 提前触发 `parent.save()` 导致后续 `save(force_insert=True)` 报主键冲突。
- **覆盖状态与健康分判定算法**：
  - `_calc_coverage_status(ok, warn, crit, err, skip) -> Tuple[str, float]`:
    - `executed == 0`: 返回 `("unassessed", 0.0)`（未评估）。
    - `executed > 0 and skip / (executed + skip) > 0.5`: 返回 `("insufficient_coverage", rate)`（覆盖不足，跳过项超过 50%）。
    - 其余: 返回 `("normal", rate)`（正常）。
  - `_calc_health_score(ok, warn, crit, err, skip=0)`: 当 `executed == 0` 时固定返回 0.0。

### 3. 前端展示收口
- `frontend/src/pages/InspectionCenter.jsx`:
  - 表格健康度列根据 `coverage_status` 分支渲染：
    - `unassessed`: 灰色 Tag 显示“未评估”。
    - `insufficient_coverage`: 橙色 Tag 显示“覆盖不足 (X分)”，Tooltip 提示实际覆盖率。
    - `normal`: 正常百分比进度条。
- `frontend/src/pages/InspectionDetail.jsx`:
  - 巡检报告卡片根据 `coverage_status` 显式呈现“未评估”与“覆盖不足 (覆盖率 X%)”，避免误导运维人员。

### 4. 独立测试套件 `monitor/tests_inspection_scope.py`
- 16 项全量单测，包含四个测试用例类：
  1. `InspectionPermissionGateTests` (7 项): 验证零权限账号下列表、触发、详情、项定义、模式、RCA 均返回 403，触发接口不产生任何记录。
  2. `InspectionScopeGateTests` (4 项): 验证有功能权限但数据范围受限时，越权触发返回 404（不产生记录）、越权读详情返回 404（无数据泄露）、列表严格过滤范围外实例、RCA 越权返回 404。
  3. `InspectionExecutionContractTests` (2 项): 验证手动触发 API 200 返回、全链路持久化并可查详情；验证 `InspectionExecutor` 两种调用契约均正确运行。
  4. `InspectionCoveragePresentationTests` (3 项): 验证未评估、覆盖不足（1 ok + 79 skip）、正常三档覆盖率及健康分算法。

---

## 三、验证证据

### 1. 单元测试结果
```bash
$ python manage.py test monitor.tests_inspection_scope --settings=dbmonitor.settings_test_unit
Found 16 test(s).
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
................
----------------------------------------------------------------------
Ran 16 tests in 0.230s

OK
Destroying test database for alias 'default'...
```

### 2. 红线与密钥静态扫描
```bash
$ python3 scripts/lint_redlines.py
红线约束扫描通过

$ python3 scripts/scan_secrets.py
密钥扫描通过
```

### 3. `scripts/validate.sh unit --json` 证据
```json
{
  "mode": "unit",
  "exit_code": 0,
  "stages": [
    {"name": "红线扫描", "status": "pass", "duration_ms": 2401},
    {"name": "unit 测试", "status": "pass", "duration_ms": 11977}
  ]
}
```

---

## 四、结论与交接

1. **R1 / R2 / 覆盖呈现缺陷全部闭合**：Phase 5 巡检与 RCA 接口安全双闸闭合，手动触发契约 400 缺陷修复，覆盖呈现不再掩盖错误 skip。
2. **解除前置依赖**：满足 DB-013 判定条件②，相关入口已具备安全放通前提。
