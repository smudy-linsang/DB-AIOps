# DB-AIOps 自有数据库 4NF 规范化改造设计

| 项目 | 内容 |
|------|------|
| 目标 | 使项目自身使用的关系型 schema（PostgreSQL / Django ORM）满足第四范式（4NF） |
| 基线 | commit `648dd88` |
| 范围 | `monitor/models.py` 全部 Django 模型；TimescaleDB 超表与 ES 索引做范式评估并给出结论 |
| 方法 | 识别多值依赖（MVD）→ 拆分子表 → 数据迁移 → 兼容属性 → 全量回归 |

---

## 一、范式判定准则

4NF 要求：关系 R 中每个非平凡多值依赖 X↠Y，X 必须是超键。落到工程上：

1. **1NF 前提**：列值必须原子。`JSONField` 存**列表/数组**（一行内嵌多个值）违反 1NF，必然无法达到 4NF，必须拆出子表。
2. **4NF 违例**：同一行内并存**多个相互独立的多值集合**（如一条规则同时有 channels[]、alert_types[]、severities[]），产生独立 MVD，必须各自拆成独立关系。
3. **可保留**：`JSONField` 存**单个对象（dict）**且与主键 1:1 函数依赖（如 schedule、threshold、params_schema、verify、plan_json、rca_result、impact、score_detail、detail、raw_data）。它是单一复合属性，不引入 MVD，满足 4NF，**不拆**。

据此，改造对象 = 所有**列表型 JSONField**（多值），保留对象 = 所有**对象型 JSONField**（1:1 依赖）。

## 二、现状评估

### 2.1 TimescaleDB 超表（已符合，不改造）
`metric_point / collection_snapshot / session_sample / sql_stat / session_ash_1m` 均为事实表：每行 = 一条原子观测（time × config × key × value），无多值列、无独立 MVD，**已满足 4NF**。保留。

### 2.2 Elasticsearch 索引（不适用，不改造）
ES 为文档/倒排索引存储，4NF 属关系模型理论，不适用于检索引擎的反范式文档设计；其告警/指标文档为查询性能刻意反范式化，属正确工程选择。**不在 4NF 改造范围**，保留。

### 2.3 PostgreSQL（Django）— 需改造的列表型多值字段

| # | 模型 | 字段（列表型，违例） | 拆分出的子表 |
|---|------|---------------------|--------------|
| 1 | NotificationRule | alert_types, severities, channels | NotificationRuleAlertType / Severity / Channel |
| 2 | UserProfile | allowed_databases | UserProfileDatabase |
| 3 | MetricDefinition | db_types | MetricDefinitionDbType |
| 4 | ReportRecord | recipients | ReportRecordRecipient |
| 5 | AlertCase | commands_used, tags, references | AlertCaseCommand / Tag / Reference |
| 6 | BusinessImpactAssessment | health_affected_dimensions, affected_systems | BiaDimension / BiaSystem |
| 7 | InspectionItem | applicable_db_types, references | InspectionItemDbType / Reference |
| 8 | Incident | plans | IncidentPlan |
| 9 | Playbook | applicable_db_types, precheck, steps, rollback | PlaybookDbType / PlaybookStep(phase) |
| 10 | RemediationPlan | steps | RemediationPlanStep |
| 11 | PlaybookRun | step_results | PlaybookRunStepResult |
| 12 | AgentTrace | steps | AgentTraceStep |

保留（对象型 1:1，符合 4NF）：`schedule, execution_context, execution_evidence, score_detail, labels, symptom_signature, business_impact_summary, threshold, detail, raw_data, threshold_violated, rca_result, impact, verify, params_schema, params, verify_result, plan_json`。

## 三、改造方案

### 3.1 子表设计原则
- 标量多值（channels/tags/recipients/db_types/...）：子表 = (父FK, value)，联合唯一，value 建索引。
- 有序/复合多值（plans/steps/precheck/rollback/step_results）：子表 = (父FK, seq, phase?, payload JSON)。payload 为该元素的 1:1 复合值（满足 4NF），seq 保序。
- 所有子表 `on_delete=CASCADE`，随父删除。

### 3.2 兼容策略（控制代码改动面）
- 父模型**删除**列表 JSONField 列，**新增同名 Python property**：
  - getter：从子表按序读出，返回列表（标量值或 payload dict）。
  - setter：整表替换子行（先删后批量插），支持 `obj.plans = [...]` 旧写法。
- 对**原地 mutate 后 save**（如 `run.step_results.append(x); run.save()`）与 **save(update_fields=[...含被删字段...])** 的调用点，逐一改为 setter 赋值或移除 update_fields 项。
- API/前端契约不变：序列化器继续读 property 返回的列表，前端零改动。

### 3.3 数据迁移
- Django migration：先建子表 → `RunPython` 将存量 JSON 列表写入子表 → 删除父列。
- 回滚：`RunPython` 反向把子表聚合回 JSON 列（保证可逆）。

## 四、实施与验证
1. 修改 `models.py`（删列、加子表、加 property）。
2. `makemigrations` + 手写数据迁移。
3. 修正调用点（update_fields / 原地 mutate）。
4. `manage.py test`（50 用例）+ `check` + 启动服务 + 关键接口冒烟（通知规则 CRUD、事故详情 plans、Agent 轨迹 steps）。
5. 提交 GitHub。

## 五、风险与回退
- 风险：运营类字段（plans/steps/step_results）调用面广。缓解：property 兼容 + 全量测试 + 冒烟。
- 回退：迁移可逆；如需紧急回退可反向迁移恢复 JSON 列。
