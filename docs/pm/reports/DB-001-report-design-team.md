# DB-AIOps 建设方向研判报告（设计团队）

- 任务单：`docs/pm/orders/DB-001-设计团队-建设方向研判.md`；修订单：`docs/pm/orders/DB-011-设计团队修订DB-001研判.md`
- 执行：DSMan（队长、研判主笔）、GG（架构与实现可行性）
- 版本：V2.1（合并 GG 与 DSMan 两份并行 V2.0 修订，统一为单一版本；按 DB-002 R1–R4）
- 日期：2026-11-05
- 结论：**有条件通过**（设计团队自评；条件见第七节，须 O 复审）
- 合并说明：V2.0 曾出现两份并行修订（GG 08:38 提交与 DSMan 同刻修订），本 V2.1 以 DSMan 版为底合并 GG 版细节；两版对 R1–R4 的结论一致，GG 版“79 项确实未编写”的过度表述与 11/12 汇总口径错误已按 O 的返工要求更正。
- 修订依据：`docs/pm/reports/DB-002-report-O.md`（结论：不通过）及 O 的复现证据 `docs/pm/reports/evidence/DB-002-O/`
- 成本披露：搜索 0 次、抓取 0 次（纯本地代码与文档盘点）

## 〇、取证基线与边界（R4 要求）

| 项 | 基线 | 说明 |
|---|---|---|
| 静态盘点源码 | `cad2514ab0a51209c1ea58c74e312129a537217b` | D-team 三份 notes 的取证提交 |
| O 独立复核源码 | `0ccc682c2bbc5cc554d42aedb4d83b2d2cc5ca7a` | DB-002 评审基线；与 `cad2514` 的产品文件一致（差异仅黑板/自动链与 `monitor/tests_pm_a_inbox.py`） |
| 本次修订源码 | 当前 HEAD（含 DB-011 时点） | `git diff --stat 0ccc682..HEAD -- monitor dbmonitor frontend scripts .github requirements.txt requirements.lock` 为空；产品代码未变 |
| 报告版本 | V1.1 `d86958b`（O 评审对象）→ 本 V2.0 | 版本递增见文末版本记录 |
| 运行证据 | O 的隔离 SQLite 探针（未连真实库） | 证据：`evidence/DB-002-O/probe-results.json`、`reproduce.py`、`baseline-comparison.json` |
| 静态 vs 运行 | 本报告第 1、2 节为静态盘点；第 1.5 节的越权/400 为 O 的运行实测 | 静态“真实现”不等于环境验证通过；未测入口不宣称安全 |

- 时效口径：按 **DEC-005 统一为 1-5-10**（1 分钟发现 / 5 分钟方案 / 10 分钟解决），1-5-15 仅作历史文档属性；目标、场景预算、实测结果三层分开，当前无“全平台已达标”证据。

## 一、v2.6 现状盘点（以代码为准）

**口径**：`cad2514` 静态走查 + 调用点检索；“真实现”指代码路径完整且可被调用，不等于环境验证通过。

### 1.1 规模与骨架

- 版本单一源 `dbmonitor/version.py` = **2.6.0**；85 个模型 / 35 个 migration；158 条 `api/` 路由 + 148 个视图类；18 个管理命令（3 个常驻）；前端 29 个页面（28 个挂路由）。
- 三守护：collector（60s 轮询）、sentinel（8s 探活 / 5s ASH）、pipeline（Redis Stream 三消费组）；均用 `ProcessLease` 租约 + fencing，具备单活与切换基础。
- 存储：PG(ORM) + TimescaleDB + ES 三层已接线（`start_monitor.py:400-472`）；**Redis 是 sentinel/pipeline 的前置**（未配 `REDIS_URL` 时直接 `ValueError`）。
- 测试：22 个 `monitor/tests*.py`；V3.0 OAM SIT/UAT 51/51（有条件通过，遗留 R-1~R-5）。

### 1.2 能力矩阵（按域）

| 能力域 | 判定 | 关键证据 |
|---|---|---|
| 采集 | 真实现（达梦条件） | Oracle 141 键、MySQL 274 键、PG 228 键、DM 174 键、GBase 132 键、TDSQL 含 proxy 拓扑；`checkers/__init__.py:46-54` 注册 |
| 告警 | 真实现（文档口径滞后） | 实发 11 种 `alert_type`；`AlertLog.ALERT_TYPE_CHOICES` 只声明 5 种；静默窗/通知规则/模板/批量删除齐全 |
| 三层检测 | 真实现 | `detectors/l1_hard.py`、`l2_baseline.py`、`l3_composite.py`、`config_drift.py`；阈值 80/95%、空间 90/95%、阻塞 10/30s |
| 基线与容量 | 真实现 | `baseline_engine.py` 168 时间槽 + Welford；`capacity_engine.py`；`health_engine.py` |
| RCA | 真实现（编号已换代） | 生产走 `rca_engine_v2.py`：39 条 `RULES_V2` + 1 条复合；v1 仅兼容薄壳（`analyze()` 发 DeprecationWarning 后转发 v2） |
| 巡检 | **部分实现（已知入口缺陷）** | 规则定义 82 项、26 个检测器，但按 `item_id` 查注册表仅 3 项命中；手动触发入口 400（见 1.5 R2）；调度层结构覆盖 3 项 |
| 事故/工单/Playbook/SLA/值班 | 真实现 | `Event/Incident/Problem`、`IncidentWarRoom.jsx`、`PlaybookRun*`、`SlaReport.jsx`、`oncall.py` |
| LLM / Agent / Copilot | 代码完整，默认关闭 | `llm/` 10 文件：路由、诊断、Agent 循环、只读工具、证据包、Schema 校验、SSRF 防护；`LLM_ENABLED/AGENT_ENABLED=False` |
| OAM | 五层齐全，默认关闭 | `monitor/oam/` 34 文件（env/perception/kb/policy/reward/verifier/bench/audit）；`OAM_ENABLED=False`；`kb/rebuild` 恒返回 501（设计选择，走管理命令） |
| 前端 | 29 页，1 页未接线 | `AlertDetail.jsx` 仅 import、未挂路由；列表页用内嵌弹层，独立详情页不可达 |
| 权限/审计/可观测 | **部分实现（不得外推全平台闭合）** | RBAC + `visible_to` 是主链路底线，`lint_redlines.py`/审计/`livez`/`readyz`/`metrics` 在；但 Phase 5 巡检入口存在 R1 越权反例（1.5） |

### 1.3 README 宣称核实（真未实现 vs 文档滞后）

| README 宣称 | 判定 | 代码证据 |
|---|---|---|
| 6 种库全量采集 | **部分实现** | Oracle/MySQL/PG/GBase/TDSQL 真实现；达梦 `dm.py` 用 `import dmPython`，`requirements.txt` 装的是 `pyodbc`，缺驱动时 `dm` 不注册、采集按“跳过未知类型”静默跳过 |
| 6 类告警 | **真实现且超出文档** | 实发 11 种；但 `ALERT_TYPE_CHOICES` 只有 5 种，二次开发会被误导 |
| RCA 内置 R001~R006 | **文档滞后，编号漂移** | 仅 R001 对得上；R002→R011+R021、R003→R031、R005→R061/R083、R006→R081；**R004「QPS 突降」在 v2 无独立规则** |
| 基线分析引擎 | 真实现 | `baseline_engine.py` + `alert_engine.py` 三重条件/收敛/升级 |
| 邮件告警 | 真实现（默认静默失败） | `notifications.py:87` 走 Django mail；`ADMIN_EMAILS` 为空时返回 False，不报错 |
| Dashboard/详情页 | 真实现 | `Dashboard.jsx`、`DatabaseDetail.jsx` 等；README 指的 `monitor/templates/monitor/*.html` 已删，`monitor/views.py` 是空壳 |
| 基线示例 `calculate_baseline` | **文档滞后（示例不可运行）** | 该方法在 `IntelligentBaselineEngine`；`BaselineEngine` 只有 `calculate_baseline_for_metric`/`calculate_full_baseline`，照抄必 `AttributeError` |
| 配置示例 `create(password='明文')` | **文档滞后（安全错误示例）** | `models.py:86,113-121` 需 `set_password()` 才加密；照抄会明文入库 |
| 改 `start_monitor.py` 顶部“配置区”调阈值 | **文档滞后** | 阈值在 `checkers/base.py:18-20`，`start_monitor.py:47-49` 只是 import |
| 能力边界“诚实声明”（标注 `【未实现】`） | **真未实现（声明未落地）** | 全仓库 `【未实现】` 仅出现 1 次，就是声明本身；没有任何能力被打标 |
| 巡检 82 项规则 | **部分实现（严重）** | 定义 82 / 注册键 26 / 按 item_id 命中 3；其余 79 项 skip；入口另有 R2 的 400 |
| TEST_REPORT 结论 | **已过期且与 PROJECT_SUMMARY 冲突** | 基线 `98c7637` 落后 HEAD 131 个提交；2 致命 + 4 高危已修复，但报告仍写“不通过、须复测方可投产” |

### 1.4 影响方向判断的代码级风险

1. **达梦驱动口径不一致**：按 `requirements.txt` 部署，达梦被静默跳过——六库支持存在“文档说支持、默认装完不支持”的落差。
2. **RCA 告警门槛**：`start_monitor.py:856` 只对 `severity=='critical'` 发告警，39 条规则多数为 warning/error，命中只进事件流；对外说“39 条规则自动告警”会失真。
3. **RedisChecker 是桩**：`start_monitor.py:68-75` 恒写 DOWN，误配 redis 类型会持续误告警。
4. **AI 能力默认关闭**：LLM/Agent/OAM 三个开关默认 False，默认部署没有 AI 诊断。
5. **告警类型 choices 与实发不一致**、`AlertDetail.jsx` 未接路由——属工程质量问题，不影响主链路。
6. **环境未验证项**：真实库连接、ES/TimescaleDB 部署、邮件/钉钉/企微触达均未在本轮实测。
7. **巡检覆盖与健康分语义**：82 项定义中，按 `item_id` 直接命中注册表 3 项；另有 8 项 `detect_method` 有同名 callable 但未注册（连同 3 个直接命中，共 11 项有同名实现）；其余 71 项无同名 callable。**“79 项只是接线错”不成立**：接线错只解释 3 个直接命中之外的调度覆盖，不能证明 71 项都有等价实现，也不能推导固定工期。
8. **文档基线互相矛盾**：TEST_REPORT 结论过期 131 个提交，PROJECT_SUMMARY 的“双闸闭合”缺当前基线证据；README 正文停在 v2.1。对外取证前必须先统一文档基线。
9. **默认安全/能力开关偏保守**：`ENABLE_RATE_LIMIT=False`、`LLM/AGENT/OAM_ENABLED=False`；默认部署既无限流也无 AI 能力。
10. **CI 触发分支（DEC-006 已定调）**：DEC-006 决定默认分支改 main、master 冻结，`ci.yml` 由 A 在 DB-012 改为监听 main；当前 `ci.yml` 仍为 master，DB-012 与仓库默认分支切换完成前，main 上的漂移不被拦截。不能把“已决定”当作“已生效”。
11. **巡检安全缺陷（R1/R2，见 1.5）**：详情越权读取与触发入口 400 是 O 实测的仓库内可复现缺陷，不能归因于环境。

### 1.5 安全现状与已知入口缺陷（R1、R2）

**R1〔高〕巡检详情存在范围越权，不能宣称“双闸严格闭合”。**

- `monitor/api_views_phase5.py:294-303` 的详情视图仅 `require_auth`，直接 `InspectionRun.objects.get(run_id=...)`，未做功能权限闸，也未用 `db_config` 可见范围限定。
- O 用隔离 SQLite + 合成数据实测：有功能权限但 `visible_to(user)` 查不到另一实例的账号，GET 该实例巡检详情返回 **HTTP 200**，含另一实例名称与 finding 的合成 `raw_data`。
- 同文件 `:222-239` 的触发接口同样只鉴权并裸查实例；有权限但范围受限的用户可为范围外实例创建失败的 `InspectionRun`，零功能权限用户也能触及该写路径。**实测到的是越界读取与元数据写入，未证明也未声称已在目标库执行 SQL。**
- 结论：RBAC + `visible_to` 在主链路成立，但 **Phase 5 巡检 API 是反例**；“全平台双闸闭合”的表述撤回。未测入口不宣称全量安全。

**R2〔高〕手动巡检入口本身不可用，“巡检路径完整”不成立。**

- `monitor/api_views_phase5.py:256-257` 调 `InspectionExecutor(config, level=level)` 再 `executor.run()`；实际构造签名为 `__init__(max_workers=5, default_timeout=30)`，业务入口是 `run_for_db(db_config, level)`，没有 `run()`。
- O 实测 POST `/api/v1/inspection/runs/trigger/` 返回 **HTTP 400**（`InspectionExecutor.__init__() got an unexpected keyword argument 'level'`），并遗留 `status=failed` 运行记录；未到达检测器选择。
- 调度入口 `inspection_scheduler.py:49-52` 用 `InspectionExecutor().run_for_all_dbs(...)`，会进入执行器。因此“82 项仅 3 项命中”是**规则注册/调度层的结构覆盖**，不能当作 UI 手动巡检已成功运行的证明。
- 结论：巡检能力判定为**部分实现**；修复必须先补权限/范围闸与入口契约，再放通执行，不能只改字典后扩大既有未授权执行面。

### 1.6 82 项巡检分层映射（R3 要求）

完整 82 行见 **附录 A** 与 `docs/pm/reports/evidence/DB-001/inspection-82-mapping.md`（`inspection-82-mapping.json` 为同源机器可读数据）。统计：

| 分层 | 数量 | 含义 |
|---|---|---|
| 定义总数 | 82 | `inspection_registry.py` 六库定义总和，不是单实例/单轮次分母 |
| 注册表键 | 26 | 23 个 `Ixxx` 旧键 + 3 个 TDSQL 项 |
| 按 item_id 直接命中 | 3 | 仅 `INS-TDSQL-SHARD/PROXY/SHARDKEY` |
| 同名 callable 但未注册 | 8 | `detect_method` 在检测器类中有同名方法，但 `DETECTOR_REGISTRY` 未登记 |
| 无同名 callable | 71 | 不等于 71 项都要从零开发；可能有语义相近的旧检测器，但缺逐项映射与参数/方言等价证明 |
| 测试按方法名引用 | 0 | 现有测试未按 `detect_method` 名引用，覆盖率无法从静态文本证明 |

**判定规则**：直接命中＝接线修好即可调度；同名未注册＝别名映射可接入；无同名＝需逐项评估“缺实现 / 语义相近可复用 / 方言不适用”。**不得把“可调用”当作“六库验证通过”，也不得用“82 项全部可跑”掩盖错误 skip 或空桩。**

**健康分语义**：后端 `:1636-1641` 已保存 `info_count` 与 `summary.skipped`，`:1699-1703` 的分母排除 skip——全跳过得 0，1 个 ok + 79 个 skip 得 100。后者不代表覆盖充分；前端/契约应区分**未评估 / 覆盖不足 / 正常**，而不是只修一个分数。

## 二、设计稿时效性评估

**范围**：仓库根目录 22 份设计/方案类 `*.md` + `plans/` 下 3 份，共 25 份（静态基线 `cad2514`）。
**判定口径**：已落地 / 部分落地 / 已过时 / 仍有效，四档互斥；以代码可指认为准，文档自述只作线索。
**汇总（按 R4 重算）**：已落地 **11**、部分落地 **12**、已过时 **1**、仍有效 **1**。

### 2.1 逐项判定

| # | 文档路径 | 判定 | 关键证据 / 理由（R4 更正处已标注） |
|---|---|---|---|
| 1 | `DB_AIOps_DESIGN.md` | 部分落地 | 告警/审批/自监控/健康分已落地；§13 的 Kafka/RabbitMQ 事件总线、多租户、MLOps 未实现 |
| 2 | `DB_AIOps_MASTER_DESIGN.md` | 部分落地 | 三平面模块在；Celery 分发代码仍在 `start_monitor:188-207`，但采集开关 `MONITOR_USE_CELERY` 默认未启用（**更正**：不写成“从未启用”），现行是 sentinel + Redis Stream pipeline |
| 3 | `DB_AIOPS_V2.0_DESIGN.md` | 部分落地 | 性能中心/ASH/WarRoom/Copilot/RCA 3.0 有落点；旧时效口径已由 DEC-005 废止，保留历史属性 |
| 4 | `DESIGN.md` | **部分落地（R4 更正，原判已落地）** | crypto/auth/notifications/timeseries 等主干在；但第一期含真实 200 实例接入、≥99% 成功率、生产部署验收（`:1018-1049`），代码存在不足以认定整稿/该期完成 |
| 5 | `V2.5_UPGRADE_DESIGN.md` | 部分落地（**SBOM 更正**） | WP0–WP4 已落地；`ci.yml` security job 已用 `pip-audit --format cyclonedx-json` 生成并上传 `sbom-python.json`（**SBOM 流水线已实现，产物未核验**）；镜像签名、WORM、行内准入、WP5 IAM/CMDB 仍待证 |
| 6 | `V2.6_HARDENING_DESIGN.md` | 已落地（限定 W1–W5 施工项） | W1–W5 逐单可验；**不得外推全部 API 双闸闭合**，1.5 R1 即反例；L1（SSE 全量广播）仍是自述缺口 |
| 7 | `V3.0_DBAGENT_REDESIGN.md` | 部分落地 | P0 已成形（oam/env、verifier、perception、kb、bench、迁移 0031–0034、Oam 页面）；P1 基准扩充、P2 GRPO 未实现（policy 仅 `prompted.py`） |
| 8 | `V3.0_DIALECT_MATRIX.md` | 仍有效（**证据更正**） | 历史实测：mysql/pgsql/oracle 实跑；GBase 有真实容器 `8.6.2.43-R7-free.110605` 连通与手工探测证据，但**无自动化方言用例执行**；dm/tdsql 未执行；当前 `tests_dialect.py` 为 **24 条**（原 21 条已过时），重跑需更新清点 |
| 9 | `PHASE5_DEVELOPMENT_DESIGN.md` | 部分落地（**巡检性质更正**） | RCA 2.0 与巡检主体代码在；巡检是功能/安全缺陷（1.5 R1/R2），不只是文档旧名；`impact_engine` 可指认 HealthImpact/BusinessImpact 计算，但 `causal_graph.py` 等与因果链的映射需补证，不能笼统说已并入 |
| 10 | `PHASE6_DEVELOPMENT_DESIGN.md` | 已落地（限实现层） | 六支柱主要代码在；1-5-10 无本轮运行证据；八类问题矩阵的 ≤90s/≤3min 检测预算与统一目标的关系需单列测量，不能当作全场景达标 |
| 11 | `NEXT_PHASE_DEVELOPMENT_DESIGN.md` | **部分落地（R4 更正，原判已落地）** | checkers 拆分、多层写入、指标配置化有落点；P2-1 的 Redis/Mongo 仍是存根（`start_monitor.py:68-75`、`:92`），是整稿“已落地”的直接反例 |
| 12 | `PROJECT_IMPROVEMENT_DESIGN.md` | 已落地（限定施工项） | validate.sh、CI、依赖/密钥扫描、appconf/self_monitor 可指认；按 DEC-006：默认分支改 main、master 冻结，`ci.yml` 由 A 在 DB-012 改为监听 main；当前仍为 master，完成前 main 漂移不被拦截；本轮未核实远端 CI 绿灯 |
| 13 | `PROJECT_ANALYSIS_AND_NEXT_PHASE.md` | **已过时** | v2.2 时代体检报告，前提与建议已被 Phase 5/6 吸收，不能当现状分析引用 |
| 14 | `METRICS_DESIGN.md` | 部分落地 | 指标实现超出其“当前状态”表；完整清单不能靠返回键数验收，语义、单位、缺值仍需方言证明 |
| 15 | `DB_SCHEMA_4NF_DESIGN.md` | 已落地（限定 12 组拆表） | models 子表与 0018/0023 回填在；不外推 Timescale/ES 或全部生产数据已验收 |
| 16 | `ORACLE_RAC_ADG_DESIGN.md` | 部分落地 | RAC/ADG 核心字段在；`ic_*`/`gc_*`/`archive_gap`/MRP-RFS 组缺，需真库逐项验证 |
| 17 | `TDSQL_SLOWSQL_DESIGN.md` | 部分落地 | 主干与测试在；V-1..10 及 A/B 清单需内网证据；§11 明确不做 proxy 层耗时分解，验收不得越界承诺 |
| 18 | `HARNESS_IMPROVEMENT_DESIGN.md` | 已落地（限仓库规则） | AGENTS/validate/hooks 施工可指认；“存在规则”与“远端 main 有门禁”需分开，AGENTS 的 master 与黑板 main 存在文字冲突 |
| 19 | `BATCH_ALERT_DELETE_DESIGN.md` | 已落地 | 后端接口、路由、前端勾选/二次确认/批删客户端对应；本轮未做浏览器交互验收 |
| 20 | `BUGFIX_DESIGN.md` | 已落地（限已列修复） | 35 项修复与回归用例在；文档自留 BUG-132 APIKey 缓存持久化缺口，不能理解为所有风险关闭 |
| 21 | `SCOPE_HARDENING_CHANGELOG.md` | 已落地（限已列视图） | 既有视图修复、lint/CI diff 可指认；**R1 证明未覆盖全部 Phase 5 API**，须补测试与范围清单 |
| 22 | `V2.5_REVIEW_REMEDIATION_DESIGN.md` | 已落地（限整改点） | LLM 凭据迁移、出站策略、旧视图范围及测试可指认；不作全平台越权消除背书 |
| 23 | `plans/database-list-ux-design.md` | 已落地（限六项主干） | 缓存 TTL、Promise.all、health/alert、排序与统计存在；不据此认定虚拟滚动等可选项或大规模交互验收 |
| 24 | `plans/frontend-ux-optimization-design.md` | 部分落地 | 多模板/性能页/曲线/SQL 页面在；草稿字段、接口、日期不宜直接作施工规范 |
| 25 | `plans/bank_simulator/DESIGN.md` | 已落地（限实现层） | simulator/worker/schema、命令与脚本可指认；本轮不向真实库加载数据，脚本存在不等于跨六库运行已通过 |

### 2.2 三处必须统一的口径冲突

1. **时效目标 1-5-15 vs 1-5-10**：`DB_AIOPS_V2.0_DESIGN.md` §1.2 写 1/5/15；`PHASE6_DEVELOPMENT_DESIGN.md` 与 `phase6/README.md` 已统一为 1/5/10。按 **DEC-005** 统一采用 **1-5-10**；1-5-15 仅作历史文档属性。
2. **编排底座 Celery vs Redis Stream**：`DB_AIOps_MASTER_DESIGN.md` §3.3 的 Celery 分发代码仍在，但 `MONITOR_USE_CELERY` **默认未启用**（不是“从未存在”）；现行是 `sentinel` + `pipeline`（Redis Stream 三消费组）。旧进程图不能作部署依据。
3. **「v3.0」一名两用**：`NEXT_PHASE_DEVELOPMENT_DESIGN.md` 的 v3.0 是“数据完整性”（已落地部分，实际以 v2.5/2.6 交付）；`V3.0_DBAGENT_REDESIGN.md` 的 V3.0 是“经验驱动 OAM”（P0 部分落地）。引用必须带文档名。

### 2.3 未核实清单

| # | 文档 | 缺什么 |
|---|---|---|
| U1 | `DB_AIOps_MASTER_DESIGN.md` | 文档头无版本/日期，无法确认是否整合版终稿 |
| U2 | `V2.5_UPGRADE_DESIGN.md` | SBOM 产物未核验；镜像签名、WORM、行内准入、WP5 IAM/CMDB 待证 |
| U3 | `PHASE6_DEVELOPMENT_DESIGN.md` | 1-5-10 与 ≤90s/≤3min 预算的实测计时，仓库只有 drills 脚本无结果 |
| U4 | `V3.0_DBAGENT_REDESIGN.md` | P0 要求的成功率/幻觉率基线跑分，仓库未见输出 |
| U5 | `TDSQL_SLOWSQL_DESIGN.md` | §8 内网 V-1~V-10 逐项结论（无真实 TDSQL） |
| U6 | `plans/frontend-ux-optimization-design.md` | 文档头“2025年”早于仓库首提交（2026-04），日期真实性存疑 |
| U7 | `PHASE5_DEVELOPMENT_DESIGN.md` | `causal_graph.py` 等与 `impact_engine.py` 的因果链映射需补证 |

## 三、候选方向（不少于 3 个，按建议顺序）

> 说明：A > B > C > D > E 是**投资方向建议**，不是技术执行的严格全序。C 中影响数据正确性/覆盖的部分应作为 A 的前置或并行；真实数据与安全问题未过关前，不因 B 排第二就优先上生产自治。

### 方向 A：生产就绪收口——异构实库、安全基线与规模举证（推荐首攻）

- **目标**：把“架构完备”变成“银行生产可举证”：先闭合仓库内可复现的安全/契约缺陷（R1/R2）；再做 TDSQL/达梦/GBase 实库矩阵与 TDSQL 慢 SQL 全链路内网验证；最后做 500 实例容量、长稳、主备切换与五方签字材料。
- **主要工作量**：巡检权限/范围闸与入口契约修复；TDSQL 内网验证（Lingma 人工通道）与按缺陷修复；达梦 `dmPython` 依赖/实库；GBase 从已有真实连通记录出发补当前版本用例；容量/长稳压测；切换演练与签字材料。
- **风险**：内网与硬件窗口不可控；Lingma 人工转达；TDSQL 版本/分片差异；R1/R2 修复不当会扩大未授权执行面。
- **对银行生产的意义**：直接决定能否进生产、能否对外声明六种库支持；当前最大证据缺口（六方言仅 3 实跑；TDSQL 慢 SQL 结论需内网验证解除）。

### 方向 B：OAM 经验驱动生产化（差异化，次优先）

- **目标**：把已落地的 V3.0 OAM（约 7753 行、五层）从 SIT 演示推进到真实故障可用：真实案例库、知识库覆盖、工具矩阵、策略迭代与成本/安全边界。
- **主要工作量**：案例采集与标注、KB 语料与检索质量、工具目录扩充、bench 基准与回归、提示词/策略迭代、LLM 成本与越权防护。
- **风险**：LLM 幻觉与成本；写操作安全边界；效果评估缺客观基准；真实案例来源受限。数据与安全未过关前不接生产自治。
- **对银行生产的意义**：与普通监控平台拉开差距的能力；底座已投入，边际成本较低；价值兑现依赖方向 A 的数据质量与实库覆盖。

### 方向 C：采集与数据底座纵深（基础，工作量大）

- **目标**：补齐 RAC/ADG 仍缺的采集项，500 实例下采集稳定、口径一致，前端多库自适应。
- **主要工作量**：Oracle RAC/ADG 缺失指标；达梦驱动与实库；采集并发与调度评估（当前单进程 `ThreadPoolExecutor` + 三守护租约，无 Celery）；TSDB/ES 口径校验；`DatabaseDetail.jsx` 多库自适应。
- **风险**：指标口径错误污染告警/RCA；大规模连接对生产库有压力；前端改造面大。
- **对银行生产的意义**：分析价值的前提；影响数据正确性的部分应前置到 A，不单独延后。

### 方向 D：平台化（多租户/开放 API/插件化）

- 目标：开放 API 网关、多租户隔离、Checker 插件市场；工作量大，涉及权限模型与数据隔离重构；银行内网短期需求弱，当前不建议首攻。

### 方向 E：前端 UX 与多库体验

- 目标：落地两份 UX 方案；体验改善明确，但不解决生产可用性；安全提示等小范围 UX 修复可随时穿插，不受排序阻断。

**排序：A > B > C > D > E（投资建议，非技术执行全序）。**

## 四、首攻建议与首批任务拆分（含安全前置与依赖顺序）

**首攻：方向 A 的第一切片——先安全/契约收口，再 TDSQL 内网实库验证。** 理由：R1/R2 是仓库内可复现的高危缺陷，修复前不应放通巡检执行入口；TDSQL 修复提交已落库，当前只差真实证据。

### 4.1 安全前置（任何执行入口放通之前）

1. **R1**：巡检详情与触发补权限闸 + 实例/Run 的 `visible_to` 范围闸；分别覆盖“零权限”和“有权限但范围外”的回归；同文件其余 Phase 5 入口同类排查，未测入口不宣称安全。
2. **R2**：`InspectionExecutor` 构造与 `run_for_db/run_for_all_dbs` 契约统一；API→执行器→持久化→详情展示全链路验证，不能只改字典。
3. **巡检覆盖呈现**：健康分区分未评估/覆盖不足/正常；错误 skip、空桩、方言不适用不得伪装成功。
4. **凭据与只读**：数据库凭据只走 `.env` 或纳管界面；目标库连接只读 + 超时；V-7 未通过禁止开 EXPLAIN ANALYZE。

### 4.2 首批任务草案（仅建议，不自行派单或占号）

| 编号建议 | 任务 | 依赖/顺序 | 验收物 |
|---|---|---|---|
| DB-004 | TDSQL 内网验证准备：环境/版本/proxy+set 拓扑、权限、隔离测试库、只读与超时、凭据通道、V-1~V-9 探针 | 可先开；安全前置 1–4 就绪 | 环境就绪回执 + 验证清单 |
| DB-005 | TDSQL 慢 SQL 全链路内网实测：V-10、A-1..5、B 类语义，保留每 set 真值/单位/窗口/退化标记 | **依赖 004 就绪** | 实测报告 + 可复现缺陷单 |
| DB-006 | 按 005 的缺陷修复（分片键建议、proxy 去污染、耗时构成等） | 005 之后；按需，不预承诺修哪些 | main 修复提交 SHA + validate.sh + 复测证据（非 PR） |
| DB-005R | 005 复测（回归确认缺陷关闭） | 006 后 | 复测报告 |
| DB-007 | 500 实例容量与长稳基线：负载/拓扑/采样周期、平台与目标库压力、P95 延迟/丢失率、长稳时长与失败判据 | 依赖有效采集与数据正确性 | 压测报告（500 不按配置行数算） |
| DB-008 | 主备切换/故障演练与五方签字材料：区分 collector 租约、PG/TSDB/Redis 故障、目标库 HA；无重复执行、无丢证据、恢复对账 | 007 结果与备份恢复准备之后 | 演练记录 + 签字材料（签字不替代技术验证） |
| DB-009 | DM/GBase 实库矩阵：DM 授权驱动/环境/方言；GBase 从真实连通记录出发补当前版本用例与 ASH 回归 | 环境/适配可并行 | 方言矩阵更新 |
| DB-010A | 巡检安全/契约/覆盖呈现收口：权限闸 + 实例/Run 范围闸、执行器契约、健康分区分未评估/覆盖不足/正常 | **与 A 主线并行；先于放通入口** | 零权限与范围外回归 + API→执行器→持久化→详情全链路 + 覆盖呈现 |
| DB-010B | 按 82 项映射拆分补实现（定义→函数→方言→权限→验证→不适用） | 010A 之后，按适用/已执行/失败项分批 | 82 项映射矩阵 + 分批实现验收 |

> 依赖顺序：**004 → 005 →（按需）006 → 005 复测**；DB-010A（安全/契约/覆盖呈现）与主线并行并先于执行入口放通，DB-010B 在其后按映射分批；007/008 放在可用性证据之后。**不承诺固定工期**；工作量由 82 项映射与实库验证结果驱动。编号最终由部门经理决定。

## 五、约束与缺口

- **安全基线**：R1 越权（详情/触发）与 R2 入口 400 是仓库内可复现缺陷，必须先闭合；修复前不宣称巡检路径可用或全平台双闸闭合。
- **环境**：GG/C/D 的 DB-AIOps 本机 clone 与 `docker-compose.dev` 未建；DM/TDSQL 无真实实例（GBase 有历史真实连通证据）；Lingma 仅内网人工转达。
- **团队**：gork-team 2026-11-05 前 token 耗尽不可用；O 只做独立评审；GG 与 DSMan 无直连通道（本节由 GG 原复核内容保留 + DSMan 按 R3 更正）。
- **纪律**：直推 main、无 PR 门禁、CI 事后信号（DB-012 处理 main 监听）；代码任务必须带 `scripts/validate.sh` 证据；版本唯一源 `dbmonitor/version.py`。
- **证据缺口**：500 实例容量/长稳/主备切换/五方签字；TDSQL 实库；DM/GBase 当前版本方言用例；巡检 71 项无同名实现的语义评估。
- **文档债**：README 停在 v2.1；TEST_REPORT 落后 131 个提交且与 PROJECT_SUMMARY 冲突；`【未实现】` 标记未落地；RCA 规模 6/10/39 三种口径。
- **工程质量**：告警 `ALERT_TYPE_CHOICES` 5 vs 实发 11；`AlertDetail.jsx` 未接路由；限流默认关闭。
- **产品缺口**：多租户、开放 API、插件化未建；OAM 真实案例与 KB 语料不足；LLM 成本与安全边界未量化。

## 六、架构与实现可行性（GG 复核，DSMan 按 R3 更正）

**口径与基线**：以产品基线 `0ccc682`（与 `cad2514` 产品文件一致）为准；GG 原复核结论保留，R3 相关“半天释放 79 项”的表述撤回并由 DSMan 更正。

### 6.1 架构底座现状

1. **多进程与高可用**：三常驻守护（Collector 60s、Sentinel 8s/5s、Pipeline 三消费组）+ `process_lease.py` 租约/fencing，具备主备单活基础。
2. **多层存储**：PG(ORM)、TimescaleDB（`get_timeseries_storage().cursor()` 托管）、ES 三层接通；Redis Stream 承担解耦与削峰。
3. **安全底座**：`visible_to` 与 RBAC 是主链路底线，`lint_redlines.py`/`scan_secrets.py` 提供前置防护；但 **Phase 5 巡检接口未覆盖（R1）**，因此“双闸严格闭合”不成立，安全基线须列为生产就绪前置。

### 6.2 候选方向可行性复核（更正版）

- **方向 A**：**高度可行，但工作量的不确定性在仓库内缺陷与实库验证，不只在内网环境。**
  - 代码侧：TDSQL 慢 SQL/分片键规则、达梦驱动解耦、巡检权限/契约修复均局部化；但巡检 82 项中仅 3 项直接命中、8 项同名未注册、71 项无同名 callable，**不能用“半天字典修复”估算**；补实现工作量需按附录 A 逐项评估，方言不适用与缺实现必须分开。
  - 前置：R1/R2 安全/契约修复先于执行入口放通；TDSQL 实库验证依赖 Lingma 与硬件窗口。
- **方向 B**：中度可行；OAM 五层与单测就绪，瓶颈在真实案例、KB 语料、策略效果与写操作安全边界；须在数据与安全过关后接生产自治。
- **方向 C**：中度可行；指标覆盖已超旧文档，剩余为 RAC/ADG 细节与 500 实例并发；单进程 ThreadPool 在连接慢/网络抖动时可能耗尽，需压测后决定异步/分布式 Worker。
- **方向 D**：权限与多租户重构，偏离当前投产目标，不建议短期。
- **方向 E**：纯前端，可穿插；安全提示类小修不受排序阻断。

**架构结论**：认同 A > B > C > D > E 的投资排序；但“A 的风险完全在环境”不成立——R1/R2/R3 是仓库内可复现的代码/契约缺口，必须先闭合。

## 七、结论与未尽事项

- **结论：有条件通过**（设计团队自评；O 复审与 PM 验收前不视为通过）——
  - 条件 1（已满足）：R1/R2 已披露并纳入安全前置与首批缺陷清单（1.5、4.1）；
  - 条件 2（已满足）：R3 的“半天/79 项已有能力”与无证据固定工期已撤回；82 项分层映射见附录 A/evidence；
  - 条件 3（已满足）：R4 逐条更正（#4、#11 改部分落地；SBOM 拆分；GBase 证据；方言 24 条），汇总重算为 11/12/1/1；
  - 条件 4（已满足）：取证基线统一（第〇节）；
  - 条件 5（已满足）：首批任务补安全前置与 004→005→006→005 复测依赖，DB-010 拆分为 010A（安全/契约/呈现）与 010B（映射补实现），不承诺固定工期；
  - 条件 6（待满足）：O 复审通过 + 部门经理验收；1-5-10 已在 DEC-005 统一，无需重复登记。
- 未尽事项：六方言实库矩阵、500 实例容量/长稳/切换/签字、巡检 71 项语义评估与补实现、OAM 真实案例、README/TEST_REPORT 文档基线重整。
- 成本披露：搜索 0 次、抓取 0 次。

## 附录 A：82 项巡检分层映射

> 生成方式：`inspection_registry.py` 静态导入 + `inspection_executor.py` AST 解析（未运行 Django/未连库）；机器可读版 `docs/pm/reports/evidence/DB-001/inspection-82-mapping.json`。
> 列含义：注册键命中＝`item_id` 在 `DETECTOR_REGISTRY`；同名实现类＝`detect_method` 在检测器类中的同名方法；测试引用＝现有测试按方法名引用。

| # | 定义 item_id | 标题 | 级别 | 适用方言 | 声明函数 | 同名实现类 | 注册键命中 | 测试引用 | 分层判定 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `INS-COM-INVALID-OBJ` | 无效对象检查 | daily | oracle,dm,gbase | `detect_invalid_objects` | GenericDetector | 否 | 无测试引用 | **同名可调用未注册** |
| 2 | `INS-COM-RECYCLEBIN` | 回收站清理检查 | weekly | oracle,dm | `detect_recyclebin` | GenericDetector | 否 | 无测试引用 | **同名可调用未注册** |
| 3 | `INS-COM-USER-LOCKED` | 账号锁定检查 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_locked_users` | - | 否 | 无测试引用 | **无同名实现** |
| 4 | `INS-COM-PWD-EXPIRE` | 密码即将过期 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_expiring_passwords` | - | 否 | 无测试引用 | **无同名实现** |
| 5 | `INS-COM-CAP-7D` | 7天容量预测 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_7d_capacity` | - | 否 | 无测试引用 | **无同名实现** |
| 6 | `INS-COM-CAP-30D` | 30天容量预测 | weekly | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_30d_capacity` | - | 否 | 无测试引用 | **无同名实现** |
| 7 | `INS-COM-CONN-PEAK` | 连接峰值检查 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_conn_peak` | - | 否 | 无测试引用 | **无同名实现** |
| 8 | `INS-COM-LONG-TXN` | 长事务检查 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_long_transactions` | GenericDetector | 否 | 无测试引用 | **同名可调用未注册** |
| 9 | `INS-COM-DEADLOCK-24H` | 24h 死锁次数 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_deadlock_count` | - | 否 | 无测试引用 | **无同名实现** |
| 10 | `INS-COM-SLOW-LOG` | 慢查询日志检查 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_slow_query_log` | - | 否 | 无测试引用 | **无同名实现** |
| 11 | `INS-COM-INDEX-USAGE` | 索引使用率检查 | weekly | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_unused_indexes` | - | 否 | 无测试引用 | **无同名实现** |
| 12 | `INS-COM-MISSING-INDEX` | 缺失索引检查 | weekly | oracle,mysql,pgsql,dm | `detect_missing_indexes` | - | 否 | 无测试引用 | **无同名实现** |
| 13 | `INS-COM-FK-NO-INDEX` | 外键无索引 | weekly | oracle,mysql,pgsql,dm | `detect_fk_no_index` | GenericDetector | 否 | 无测试引用 | **同名可调用未注册** |
| 14 | `INS-COM-PART-NEEDED` | 大表未分区 | monthly | oracle,mysql,pgsql,dm,gbase | `detect_large_table_not_partitioned` | - | 否 | 无测试引用 | **无同名实现** |
| 15 | `INS-COM-STAT-STALE` | 统计信息过期 | weekly | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_stale_stats` | GenericDetector | 否 | 无测试引用 | **同名可调用未注册** |
| 16 | `INS-COM-SPARSE-INDEX` | 稀疏索引 | monthly | oracle,mysql,pgsql,dm | `detect_sparse_index` | - | 否 | 无测试引用 | **无同名实现** |
| 17 | `INS-COM-BLEVEL-HIGH` | 高 BLEVEL 索引 | monthly | oracle,mysql,pgsql,dm | `detect_high_blevel_index` | - | 否 | 无测试引用 | **无同名实现** |
| 18 | `INS-COM-SEQ-MAX` | 序列接近 MAX | daily | oracle,dm,pgsql | `detect_seq_near_max` | - | 否 | 无测试引用 | **无同名实现** |
| 19 | `INS-COM-SEQ-NO-CACHE` | 序列无 CACHE | monthly | oracle,dm | `detect_seq_no_cache` | - | 否 | 无测试引用 | **无同名实现** |
| 20 | `INS-COM-PARAM-DEV` | 参数偏离最佳实践 | monthly | oracle,mysql,pgsql,dm,gbase | `detect_param_deviation` | - | 否 | 无测试引用 | **无同名实现** |
| 21 | `INS-COM-BACKUP` | 备份状态 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_backup_status` | GenericDetector | 否 | 无测试引用 | **同名可调用未注册** |
| 22 | `INS-COM-ARCH-GAP` | 归档间隙 | daily | oracle,dm | `detect_archive_gap` | - | 否 | 无测试引用 | **无同名实现** |
| 23 | `INS-COM-LOG-SWITCH` | 日志切换频率 | daily | oracle,dm | `detect_log_switches` | - | 否 | 无测试引用 | **无同名实现** |
| 24 | `INS-COM-LOCK-WAIT` | 当前锁等待 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_lock_waits` | - | 否 | 无测试引用 | **无同名实现** |
| 25 | `INS-COM-BLOCK-SESSION` | 被阻塞会话 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_blocked_sessions` | - | 否 | 无测试引用 | **无同名实现** |
| 26 | `INS-COM-OPEN-CURSOR` | 打开游标数 | daily | oracle,dm | `detect_open_cursors` | - | 否 | 无测试引用 | **无同名实现** |
| 27 | `INS-COM-TEMP-USAGE` | 临时表空间使用 | daily | oracle,mysql,pgsql,dm,gbase | `detect_temp_usage` | - | 否 | 无测试引用 | **无同名实现** |
| 28 | `INS-COM-UNDO-USAGE` | Undo 使用率 | daily | oracle,dm,pgsql | `detect_undo_usage` | - | 否 | 无测试引用 | **无同名实现** |
| 29 | `INS-COM-FRAG-TABLE` | 高并行度表 | monthly | oracle,dm,gbase | `detect_high_parallel` | - | 否 | 无测试引用 | **无同名实现** |
| 30 | `INS-COM-FAILED-LOGIN` | 失败登录异常 | daily | oracle,mysql,pgsql,dm | `detect_failed_logins` | GenericDetector | 否 | 无测试引用 | **同名可调用未注册** |
| 31 | `INS-ORA-AWR-SNAP` | AWR 快照间隔 | weekly | oracle | `detect_awr_interval` | - | 否 | 无测试引用 | **无同名实现** |
| 32 | `INS-ORA-AWR-RET` | AWR 保留周期 | monthly | oracle | `detect_awr_retention` | - | 否 | 无测试引用 | **无同名实现** |
| 33 | `INS-ORA-AWR-TOP-EVT` | 业务高峰 Top Event | weekly | oracle | `detect_awr_top_event` | - | 否 | 无测试引用 | **无同名实现** |
| 34 | `INS-ORA-AWR-TOP-SQL` | 业务高峰 Top SQL | weekly | oracle | `detect_awr_top_sql` | - | 否 | 无测试引用 | **无同名实现** |
| 35 | `INS-ORA-SCN-HEADROOM` | SCN Headroom | daily | oracle | `detect_scn_headroom` | GenericDetector | 否 | 无测试引用 | **同名可调用未注册** |
| 36 | `INS-ORA-STA-OUTPUT` | SQL Tuning Advisor 结果 | weekly | oracle | `detect_sta_recommendations` | - | 否 | 无测试引用 | **无同名实现** |
| 37 | `INS-ORA-SEG-ADV` | Segment Advisor 结果 | weekly | oracle | `detect_segment_advisor` | - | 否 | 无测试引用 | **无同名实现** |
| 38 | `INS-ORA-AUTO-MW` | 自动维护窗口 | monthly | oracle | `detect_maintenance_window` | - | 否 | 无测试引用 | **无同名实现** |
| 39 | `INS-ORA-FRA` | FRA 使用率 | daily | oracle | `detect_fra_usage` | - | 否 | 无测试引用 | **无同名实现** |
| 40 | `INS-ORA-CTRL-BAK` | 控制文件备份 | weekly | oracle | `detect_ctrlfile_backup` | - | 否 | 无测试引用 | **无同名实现** |
| 41 | `INS-ORA-ARCH-DELETE` | 归档日志清理策略 | weekly | oracle | `detect_arch_deletion_policy` | - | 否 | 无测试引用 | **无同名实现** |
| 42 | `INS-ORA-DBA-USERS` | DBA 权限账号 | monthly | oracle | `detect_dba_users` | - | 否 | 无测试引用 | **无同名实现** |
| 43 | `INS-ORA-TS-AUTOEXT` | 表空间自动扩展 | monthly | oracle,dm | `detect_ts_autoextend` | - | 否 | 无测试引用 | **无同名实现** |
| 44 | `INS-ORA-LOB-PCT` | LOB PCTVERSION 异常 | monthly | oracle | `detect_lob_pctversion` | - | 否 | 无测试引用 | **无同名实现** |
| 45 | `INS-ORA-DBLINK` | 残留 DBLINK | monthly | oracle | `detect_dblinks` | - | 否 | 无测试引用 | **无同名实现** |
| 46 | `INS-ORA-DIR-OBJ` | Directory 对象 | monthly | oracle | `detect_directory_objects` | - | 否 | 无测试引用 | **无同名实现** |
| 47 | `INS-ORA-RMAN-AGE` | RMAN 备份距今 | daily | oracle | `detect_rman_backup_age` | - | 否 | 无测试引用 | **无同名实现** |
| 48 | `INS-ORA-DG-LAG` | DataGuard Lag | daily | oracle | `detect_dg_lag` | - | 否 | 无测试引用 | **无同名实现** |
| 49 | `INS-ORA-DG-STATUS` | DataGuard 状态 | daily | oracle | `detect_dg_status` | - | 否 | 无测试引用 | **无同名实现** |
| 50 | `INS-ORA-RAC-VIP` | RAC VIP 状态 | daily | oracle | `detect_rac_vip` | - | 否 | 无测试引用 | **无同名实现** |
| 51 | `INS-ORA-REDOLOG-SIZE` | Redo Log 大小 | monthly | oracle | `detect_redolog_size` | - | 否 | 无测试引用 | **无同名实现** |
| 52 | `INS-ORA-OPT-STATS-GATHER` | 自动统计信息收集 | weekly | oracle | `detect_auto_stats_gather` | - | 否 | 无测试引用 | **无同名实现** |
| 53 | `INS-ORA-OPEN-FILE` | 打开文件数 | daily | oracle | `detect_open_files` | - | 否 | 无测试引用 | **无同名实现** |
| 54 | `INS-ORA-LOB-CACHE` | LOB CACHE 读取 | monthly | oracle | `detect_lob_cache_hit` | - | 否 | 无测试引用 | **无同名实现** |
| 55 | `INS-ORA-ASH-USAGE` | ASH 内存使用 | monthly | oracle | `detect_ash_memory` | - | 否 | 无测试引用 | **无同名实现** |
| 56 | `INS-ORA-RMAN-CONF` | RMAN 配置 | monthly | oracle | `detect_rman_config` | - | 否 | 无测试引用 | **无同名实现** |
| 57 | `INS-ORA-AUDIT-TRAIL` | 审计日志 | weekly | oracle | `detect_audit_trail` | - | 否 | 无测试引用 | **无同名实现** |
| 58 | `INS-ORA-PGA-LEAK` | PGA 泄漏 | monthly | oracle | `detect_pga_leak` | - | 否 | 无测试引用 | **无同名实现** |
| 59 | `INS-ORA-IO-STAT` | IO 分布 | monthly | oracle | `detect_io_distribution` | - | 否 | 无测试引用 | **无同名实现** |
| 60 | `INS-ORA-EXADATA` | Exadata 特性 | monthly | oracle | `detect_exadata_features` | - | 否 | 无测试引用 | **无同名实现** |
| 61 | `INS-MYSQL-BINLOG-DELAY` | Binlog Dump 延迟 | daily | mysql,tdsql | `detect_binlog_dump_delay` | - | 否 | 无测试引用 | **无同名实现** |
| 62 | `INS-MYSQL-GTID` | GTID 一致性 | weekly | mysql,tdsql | `detect_gtid_consistency` | - | 否 | 无测试引用 | **无同名实现** |
| 63 | `INS-MYSQL-SEMI-SYNC` | 半同步复制状态 | daily | mysql,tdsql | `detect_semi_sync` | - | 否 | 无测试引用 | **无同名实现** |
| 64 | `INS-MYSQL-EVENT` | Event 调度器 | weekly | mysql,tdsql | `detect_event_scheduler` | - | 否 | 无测试引用 | **无同名实现** |
| 65 | `INS-MYSQL-FK-CASCADE` | 危险外键级联 | monthly | mysql,tdsql | `detect_dangerous_fk` | - | 否 | 无测试引用 | **无同名实现** |
| 66 | `INS-MYSQL-TMP-DISK` | 磁盘临时表比例 | daily | mysql,tdsql | `detect_tmp_disk_ratio` | - | 否 | 无测试引用 | **无同名实现** |
| 67 | `INS-PG-WAL-RECV` | WAL Receiver 状态 | daily | pgsql | `detect_wal_receiver` | - | 否 | 无测试引用 | **无同名实现** |
| 68 | `INS-PG-INACTIVE-SLOT` | 失效复制槽 | daily | pgsql | `detect_inactive_repl_slot` | - | 否 | 无测试引用 | **无同名实现** |
| 69 | `INS-PG-VACUUM-FREEZE` | Freeze Age | daily | pgsql | `detect_freeze_age` | - | 否 | 无测试引用 | **无同名实现** |
| 70 | `INS-PG-BLOAT` | 表/索引膨胀 | weekly | pgsql | `detect_bloat` | - | 否 | 无测试引用 | **无同名实现** |
| 71 | `INS-PG-EXT-OUTDATED` | 扩展版本 | monthly | pgsql | `detect_extension_versions` | - | 否 | 无测试引用 | **无同名实现** |
| 72 | `INS-PG-STMT-TIMEOUT` | Statement Timeout | monthly | pgsql | `detect_statement_timeout` | - | 否 | 无测试引用 | **无同名实现** |
| 73 | `INS-DM-DSC` | DSC 节点状态 | daily | dm | `detect_dsc_nodes` | - | 否 | 无测试引用 | **无同名实现** |
| 74 | `INS-DM-DW-STANDBY` | 主备同步状态 | daily | dm | `detect_dw_standby` | - | 否 | 无测试引用 | **无同名实现** |
| 75 | `INS-DM-ARCH` | 归档状态 | daily | dm | `detect_dm_arch` | - | 否 | 无测试引用 | **无同名实现** |
| 76 | `INS-DM-RLOG` | RLOG 同步状态 | daily | dm | `detect_dm_rlog` | - | 否 | 无测试引用 | **无同名实现** |
| 77 | `INS-GBASE-BALANCE` | 节点负载均衡 | daily | gbase | `detect_gbase_node_balance` | - | 否 | 无测试引用 | **无同名实现** |
| 78 | `INS-GBASE-DIST` | 数据分布均衡 | weekly | gbase | `detect_gbase_distribution` | - | 否 | 无测试引用 | **无同名实现** |
| 79 | `INS-GBASE-NODE-DOWN` | 节点宕机 | daily | gbase | `detect_gbase_node_down` | - | 否 | 无测试引用 | **无同名实现** |
| 80 | `INS-TDSQL-SHARD` | Set 主备健康 | daily | tdsql | `detect_tdsql_set_health` | TDSQLDetector | 是 | 无测试引用 | **直接命中** |
| 81 | `INS-TDSQL-PROXY` | Proxy 网关状态 | daily | tdsql | `detect_tdsql_proxy` | TDSQLDetector | 是 | 无测试引用 | **直接命中** |
| 82 | `INS-TDSQL-SHARDKEY` | 分片表配置 | daily | tdsql | `detect_tdsql_shardkey` | TDSQLDetector | 是 | 无测试引用 | **直接命中** |

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| V0.5 | 2026-11-04 | DSMan | 初稿：骨架事实 + 方向/首攻/约束 |
| V1.0 | 2026-11-04 | DSMan | 回填第 1、2 节；补巡检 3/82、TEST_REPORT 过期等 |
| V1.1 | 2026-11-04 | GG | GG 完成第 6 节架构复核并签署 |
| V2.0 | 2026-11-05 | DSMan（GG 原复核保留） | 按 DB-002 R1–R4 修订：披露 R1/R2；撤回 R3 低成本/固定工期并补 82 项映射；更正 R4 五项与汇总（11/12/1/1）；统一基线；首批任务补安全前置与依赖顺序；结论改“有条件通过” |
| V2.1 | 2026-11-05 | DSMan（合并 GG 并行 V2.0） | 合并两份并行 V2.0，统一单一版本；更正 GG 版“79 项确实未编写”的过度表述与 11/12 汇总错误；CI 口径按 DEC-006 更新；DB-010 拆为 010A/010B |
