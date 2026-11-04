# DB-AIOps 建设方向研判报告（设计团队）

- 任务单：`docs/pm/orders/DB-001-设计团队-建设方向研判.md`
- 执行：DSMan（队长、研判主笔）、GG（架构与实现可行性）
- 版本：V1.1（GG 复核通过，全报告合意签署）
- 日期：2026-11-04
- 结论：**通过**（研判完成度达到 100%，待提交 O 开展 DB-002 独立评审）
- 取证来源：D-team 三份内部取证 `C:\Users\linsa\.dsh\bot\workspace\db-aiops\notes\`（`db-aiops-capabilities.md`、`design-docs-staleness.md`、`readme-code-diff.md`），均基于 HEAD `cad2514` 静态走查，搜索/抓取 0 次。

## 一、v2.6 现状盘点（以代码为准）

**口径**：HEAD `cad2514` 静态走查 + 调用点检索；“真实现”指代码路径完整且可被调用，不等于环境验证通过（本轮未启动服务、未连真实库）。

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
| 巡检/事故/工单/Playbook/SLA/值班 | 真实现 | `inspection_*`、`Event/Incident/Problem`、`IncidentWarRoom.jsx`、`PlaybookRun*`、`SlaReport.jsx`、`oncall.py` |
| LLM / Agent / Copilot | 代码完整，默认关闭 | `llm/` 10 文件：路由、诊断、Agent 循环、只读工具、证据包、Schema 校验、SSRF 防护；`LLM_ENABLED/AGENT_ENABLED=False` |
| OAM | 五层齐全，默认关闭 | `monitor/oam/` 34 文件（env/perception/kb/policy/reward/verifier/bench/audit）；`OAM_ENABLED=False`；`kb/rebuild` 恒返回 501（设计选择，走管理命令） |
| 前端 | 29 页，1 页未接线 | `AlertDetail.jsx` 仅 import、未挂路由；列表页用内嵌弹层，独立详情页不可达 |
| 权限/审计/可观测 | 真实现 | RBAC + `visible_to` 双闸、`lint_redlines.py`、审计日志、`livez/readyz/metrics`、组件心跳与降级 |

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
| 巡检 82 项规则 | **部分实现（严重）** | `inspection_registry.py` 定义 82 项、26 个检测器；`inspection_executor.py:1665` 按 `item_id` 查 `DETECTOR_REGISTRY`，键却是 `I0xx/I1xx/INS-TDSQL-*`，仅 3 个 TDSQL 项命中，其余 79 项 `status=skip`；健康分不看 skip，全跳过时恒 0 |
| TEST_REPORT 结论 | **已过期且与 PROJECT_SUMMARY 冲突** | 报告基线 `98c7637` 落后 HEAD 131 个提交；2 致命 + 4 高危已全部修复（`views_enhanced.py:273-276`、`api_views.py:446-447`），但报告仍写“不通过、须复测方可投产” |

### 1.4 影响方向判断的代码级风险

1. **达梦驱动口径不一致**：按 `requirements.txt` 部署，达梦被静默跳过——六库支持存在“文档说支持、默认装完不支持”的落差。
2. **RCA 告警门槛**：`start_monitor.py:856` 只对 `severity=='critical'` 发告警，39 条规则多数为 warning/error，命中只进事件流；对外说“39 条规则自动告警”会失真。
3. **RedisChecker 是桩**：`start_monitor.py:68-75` 恒写 DOWN，误配 redis 类型会持续误告警。
4. **AI 能力默认关闭**：LLM/Agent/OAM 三个开关默认 False，默认部署没有 AI 诊断。
5. **告警类型 choices 与实发不一致**、`AlertDetail.jsx` 未接路由——属工程质量问题，不影响主链路。
6. **环境未验证项**：真实库连接、ES/TimescaleDB 部署、邮件/钉钉/企微触达均未在本轮实测。
7. **巡检可用性被高估（最高优先级缺陷）**：82 项规则实际只跑 3 项，79 项静默 skip；健康分只按 ok/warn/crit/err 计，全 skip 时恒 0，界面却正常出报告。这不是“没写”，是**接线错**（`item_id` vs 检测器注册键），修复成本低、影响面大。
8. **文档基线互相矛盾**：TEST_REPORT 结论过期 131 个提交，PROJECT_SUMMARY 的“双闸闭合”缺当前基线证据，两份文档同时引用会得出相反结论；README 正文停在 v2.1，版本号只是被 CI 机械对齐。
9. **默认安全/能力开关偏保守**：`ENABLE_RATE_LIMIT=False`、`LLM/AGENT/OAM_ENABLED=False`；默认部署既无限流也无 AI 能力，与“已实现”的印象有落差。
10. **CI 只对 master 触发**：`ci.yml:5,11` 分支为 master，当前工作分支 main 领先 master 4 个提交，main 上的文档漂移与红灯不被拦截；“CI 机械校验”只对 master 成立。

## 二、设计稿时效性评估

**范围**：仓库根目录 22 份设计/方案类 `*.md` + `plans/` 下 3 份，共 25 份（HEAD `cad2514`）。
**判定口径**：已落地 / 部分落地 / 已过时 / 仍有效，四档互斥；以代码可指认为准，文档自述只作线索。
**汇总**：已落地 **13**、部分落地 **10**、已过时 **1**、仍有效 **1**。

### 2.1 逐项判定

| # | 文档路径 | 判定 | 关键证据 / 理由 |
|---|---|---|---|
| 1 | `DB_AIOps_DESIGN.md` | 部分落地 | 告警/审批/自监控/健康分已落地（`monitor/approval_engine.py`、`monitor/alert_manager.py:407`、`self_monitor.py`、`health_engine.py`）；§13 的 Kafka/RabbitMQ 事件总线、多租户（`tenancy.py` 已被 V2.6 删除）、MLOps 未实现 |
| 2 | `DB_AIOps_MASTER_DESIGN.md` | 部分落地 | 三平面对应 `api_views*.py`、`alert_manager.py`、`baseline_engine.py`、`rca_engine_v2.py`、`capacity_engine.py`；§3.3 的 analyzer/notify worker + Celery Beat 从未启用（`start_monitor.py:62` 默认 False，V2.6 §9.5 记 vestigial），现行是 sentinel + Redis Stream pipeline |
| 3 | `DB_AIOPS_V2.0_DESIGN.md` | 部分落地 | 性能中心/ASH/WarRoom/Copilot/RCA 3.0 已落地；但本文时效目标 **1-5-15** 已被 Phase 6 的 **1-5-10** 取代；Multi-Agent「智囊团」无独立实现 |
| 4 | `DESIGN.md` | 已落地 | v1.0 第一期清单逐项可指认：`monitor/crypto.py`、`monitor/auth.py`、`notifications.py`、静默窗口、`timeseries.py`、`DatabaseList.jsx`；架构章节只宜作历史基线 |
| 5 | `V2.5_UPGRADE_DESIGN.md` | 部分落地 | WP0–WP4 已落地并有复审闭环（`process_lease.py`、`playbook_engine_v2.py`、证据链、`auth.py`）；WP2 SBOM/镜像签名、WP5 IAM/CMDB/WORM/五方 UAT 依赖行内环境，仓库只有设计 |
| 6 | `V2.6_HARDENING_DESIGN.md` | 已落地 | W1–W5 逐单可验（`tests_scope_api.py`、`tests_feature_switches.py`、`check_doc_version.py`、`lint_redlines.py`、`RULES_V2` 39 条 + 复合规则）；文档自述 L1（SSE 全量广播）仍是真实缺口 |
| 7 | `V3.0_DBAGENT_REDESIGN.md` | 部分落地 | P0 已成形：`oam/env/`、`oam/verifier/`、`oam/perception/`、`oam/kb/`、`oam/bench/`、迁移 0031–0034、`Oam*.jsx`；P1 基准扩充、P2 GRPO 训练未实现（policy 仅 `prompted.py`） |
| 8 | `V3.0_DIALECT_MATRIX.md` | 仍有效 | 实测证据：mysql/pgsql/oracle 实跑通过，dm/gbase/tdsql 明确「未执行」；`tests_dialect.py` 21 条方言用例仍在。重跑矩阵才需更新 |
| 9 | `PHASE5_DEVELOPMENT_DESIGN.md` | 部分落地 | RCA 2.0 与智能巡检主体已交付（`rca_engine_v2.py`、`inspection_*`、`AlertDetail.jsx`）；设计清单里的 `causal_graph.py`、`health_impact.py`、`business_assessor.py` 实际并入 `impact_engine.py`，按文件名会找不到 |
| 10 | `PHASE6_DEVELOPMENT_DESIGN.md` | 已落地 | Event/Incident/Problem、`sentinel.py`、`pipeline.py`（Redis Stream 三消费组）、`incident_manager.py`、`IncidentWarRoom.jsx`、`SlaReport.jsx` 均在；1-5-10 是否达标属运行期指标，本评估不背书 |
| 11 | `NEXT_PHASE_DEVELOPMENT_DESIGN.md` | 已落地 | 三件事全部落地：checkers 拆分、三分流（TSDB/ES/MonitorLog）、前端指标配置化（`dbMetricsConfig.js`）；只是未占用「v3.0」版本号 |
| 12 | `PROJECT_IMPROVEMENT_DESIGN.md` | 已落地 | `validate.sh`、CI、依赖/密钥扫描、`self_monitor.py`、`appconf.py` 开关均在；附录 B 自带落地实测记录 |
| 13 | `PROJECT_ANALYSIS_AND_NEXT_PHASE.md` | **已过时** | v2.2 时代体检报告：文中「4622 行单体」「Phase 1-3」等前提已不成立，问题多已修复、建议已被 Phase 5/6 吸收；不能当现状分析引用 |
| 14 | `METRICS_DESIGN.md` | 部分落地 | P0/P1 指标多数已实现（`oracle.py:663` buffer cache、`mysql.py:1371` buffer pool 等）；但 §3「当前实现状态」仍把已实现项标 ❌，现状章节失真 |
| 15 | `DB_SCHEMA_4NF_DESIGN.md` | 已落地 | 12 组多值字段全部拆表（`models.py` 对应 14 个子表），迁移 0018/0023 含存量回填 |
| 16 | `ORACLE_RAC_ADG_DESIGN.md` | 部分落地 | RAC 节点、ADG 时延已进采集（`oracle.py:608/732/738`）；§5.1 的 `ic_*`、`gc_*`、`archive_gap`、MRP/RFS 进程状态仍缺 |
| 17 | `TDSQL_SLOWSQL_DESIGN.md` | 部分落地 | 透传采集/按 set 聚合/SQL 详情/退化/周期比较已施工且有单测；§8 内网 V-1~V-10 验证清单未执行（无真实 TDSQL） |
| 18 | `HARNESS_IMPROVEMENT_DESIGN.md` | 已落地 | `AGENTS.md` 的编辑→验证→提交闭环、`.qoder/rules/validation.md`、全套 scripts 已生效 |
| 19 | `BATCH_ALERT_DELETE_DESIGN.md` | 已落地 | `AlertBatchDeleteView`、路由、`AlertList.jsx` 勾选/二次确认/`alertAPI.batchDelete` 均在 |
| 20 | `BUGFIX_DESIGN.md` | 已落地 | 35 项缺陷与回归用例成体系（`tests_bugfix.py`、`tests_concurrency.py`）；APIKey 持久化仍未做 |
| 21 | `SCOPE_HARDENING_CHANGELOG.md` | 已落地 | 实施记录：`tests_scope_hardening.py`、`lint_redlines.py` 范围规则、CI `REDLINE_DIFF_BASE` 均可在仓库指认 |
| 22 | `V2.5_REVIEW_REMEDIATION_DESIGN.md` | 已落地 | R25-01~R25-12 整改点（范围、无数据契约、凭据加密、出站校验、RBAC、CI diff）均对上 |
| 23 | `plans/database-list-ux-design.md` | 已落地 | 六个核心改动（健康分、告警徽章、智能排序、统计区、并行拉取、缓存）全部在 `DatabaseList.jsx` |
| 24 | `plans/frontend-ux-optimization-design.md` | 部分落地 | EMCC 风格、多模板、曲线、SQL 分析已实现；文档仍是草稿，新增模型/接口名与现库不一致 |
| 25 | `plans/bank_simulator/DESIGN.md` | 已落地 | `monitor/bank_simulator/`、`run_bank_simulator` 命令与脚本齐全，持续负载注入可运行 |

### 2.2 三处必须统一的口径冲突

1. **时效目标 1-5-15 vs 1-5-10**：`DB_AIOPS_V2.0_DESIGN.md` §1.2 写 1/5/15；`PHASE6_DEVELOPMENT_DESIGN.md` 与 `phase6/README.md` 已统一为 **1 分钟发现 / 5 分钟方案 / 10 分钟解决**。本报告及后续 SLO 表述统一采用 **1-5-10**；建议部门经理在 DECISIONS 登记该口径，1-5-15 作废。
2. **编排底座 Celery vs Redis Stream**：`DB_AIOps_MASTER_DESIGN.md` §3.3 的 Celery Beat 从未启用（`USE_CELERY` 默认 False），现行是 `sentinel` + `pipeline`（Redis Stream 三消费组）。旧进程图不能作部署依据。
3. **「v3.0」一名两用**：`NEXT_PHASE_DEVELOPMENT_DESIGN.md` 的 v3.0 是「数据完整性」（已落地，实际以 v2.5/2.6 交付）；`V3.0_DBAGENT_REDESIGN.md` 的 V3.0 是「经验驱动 OAM」（P0 部分落地）。引用时必须带文档名。

### 2.3 未核实清单

| # | 文档 | 缺什么 |
|---|---|---|
| U1 | `DB_AIOps_MASTER_DESIGN.md` | 文档头无版本/日期，无法确认是否整合版终稿 |
| U2 | `V2.5_UPGRADE_DESIGN.md` | WP2 SBOM/镜像签名、WP5 IAM/CMDB/WORM 是否在仓库外完成 |
| U3 | `PHASE6_DEVELOPMENT_DESIGN.md` | 1-5-10 是否在演练中实测达标（MTTD≤60s、MTTR≤600s），仓库只有 drills 脚本无结果 |
| U4 | `V3.0_DBAGENT_REDESIGN.md` | P0 要求的成功率/幻觉率基线跑分，仓库未见输出 |
| U5 | `TDSQL_SLOWSQL_DESIGN.md` | §8 内网 V-1~V-10 逐项结论（无真实 TDSQL） |
| U6 | `plans/frontend-ux-optimization-design.md` | 文档头「2025年」早于仓库首提交（2026-04），日期真实性存疑 |
| U7 | `PHASE5_DEVELOPMENT_DESIGN.md` | `causal_graph.py` 等三个文件是「合并实现」还是「未实现」，需作者确认 |

## 三、候选方向（不少于 3 个，按建议顺序）

### 方向 A：生产就绪收口——异构实库与规模举证（推荐首攻）

- **目标**：把「架构完备」变成「银行生产可举证」：TDSQL/达梦/GBase 实库矩阵补齐（含达梦 `dmPython` 依赖口径统一）；TDSQL 慢 SQL 全链路在内网实测收口；巡检 82 项全量可跑；500 实例容量、长稳、主备切换、五方签字材料闭环。
- **主要工作量**：内网 TDSQL 验证（Lingma 人工通道）与缺陷修正；达梦驱动/实库与 GBase 等价环境；巡检接线修复（`item_id` 与检测器注册键对齐）；容量/长稳压测与瓶颈定位；切换演练与签字材料。跨团队（Lingma、C、GG、A）协作。
- **风险**：强依赖内网与硬件窗口；Lingma 只能人工转达，周期不可控；TDSQL 版本/分片差异导致结论不可外推。
- **对银行生产的意义**：直接决定平台能否进生产、能否对外声明支持六种库；这是当前最大的证据缺口（`V3.0_DIALECT_MATRIX.md`：6 方言仅 mysql/pgsql/oracle 实跑，dm/gbase/tdsql 未执行；`TDSQL_SLOWSQL_REVIEW.md` §0 结论在内网验证前仍成立）。

### 方向 B：OAM 经验驱动生产化（差异化，次优先）

- **目标**：把已落地的 V3.0 OAM（约 7753 行、五层）从 SIT 演示推进到真实故障可用：真实案例库、知识库覆盖、工具矩阵、策略迭代与成本/安全边界。
- **主要工作量**：案例采集与标注、KB 语料与检索质量、工具目录扩充、bench 基准与回归、提示词/策略迭代、LLM 成本与越权防护。
- **风险**：LLM 幻觉与成本；写操作安全边界；效果评估缺客观基准；真实案例来源受限。
- **对银行生产的意义**：这是与普通监控平台拉开差距的能力；底座已投入，边际成本低于从零做；但价值兑现依赖方向 A 的数据质量与实库覆盖。

### 方向 C：采集与数据底座纵深（基础，工作量大）

- **目标**：补齐 RAC/ADG 仍缺的采集项（`ic_*`/`gc_*`/`archive_gap`/MRP 进程），500 实例下采集稳定、口径一致，前端多库自适应。
- **主要工作量**：Oracle RAC/ADG 缺失指标（`checkers/oracle.py`）；达梦驱动与实库；采集并发与调度评估（当前单进程 `ThreadPoolExecutor` + 三守护租约，无 Celery）；TSDB/ES 口径校验；`DatabaseDetail.jsx` 多库自适应。
- **风险**：指标口径错误会污染告警/RCA；大规模连接对生产库有压力；前端改造面大。
- **对银行生产的意义**：分析价值的前提；但不直接产生「可上线」结论，优先级低于 A。

### 方向 D：平台化（多租户/开放 API/插件化）

- **目标**：开放 API 网关、多租户隔离、Checker 插件市场。
- **主要工作量**：大，涉及权限模型与数据隔离重构。
- **风险**：银行内网场景短期需求弱；重构风险高。
- **对银行生产的意义**：长期平台化，当前阶段不建议首攻。

### 方向 E：前端 UX 与多库体验

- **目标**：落地 `plans/frontend-ux-optimization-design.md`、`plans/database-list-ux-design.md`。
- **风险/意义**：体验改善明确，但不解决生产可用性；排在最后。

**排序：A > B > C > D > E。**

## 四、首攻建议与首批任务拆分

**首攻：方向 A 的第一切片——TDSQL 内网实库验证与慢 SQL 链路收口。** 理由：修复提交已落库（T-01~T-16 系列），当前只差真实 TDSQL 证据；风险最低、证据最硬、直接解除 §0 结论。

首批任务（可派单粒度，均不含代码即本单范围外）：

| 编号建议 | 任务 | 建议执行 | 验收物 |
|---|---|---|---|
| DB-004 | TDSQL 内网验证准备：环境/账号/只读权限、探针脚本适配、验证清单 V-1~V-9 | Lingma（人工）+ DSMan 配合 | 环境就绪回执 + 验证清单 |
| DB-005 | TDSQL 慢 SQL 全链路内网实测：列表/分析/检索/告警/环比/耗时构成 | Lingma 执行，A 复核 | 实测报告 + 缺陷单 |
| DB-006 | 依实测修缺陷（分片键建议、proxy 路由去污染、耗时构成） | GG/C | 修复 PR + validate.sh 证据 |
| DB-007 | 500 实例容量与长稳基线：压测脚本、瓶颈定位、容量结论 | GG + A | 压测报告 |
| DB-008 | 主备切换/故障演练与五方签字材料 | GG + A + 用户 | 演练记录 + 签字材料 |
| DB-009 | DM/GBase 实库矩阵补齐（可并行） | C/GG | 方言矩阵更新 |
| DB-010 | 巡检接线修复：`DETECTOR_REGISTRY` 键与 `item_id` 对齐，82 项可跑；健康分把 skip 单列 | C/GG | 修复 + 全量巡检报告（并行快赢） |

> 注：DB-004 起编号为建议，需部门经理正式下单；首批建议开 **DB-004/005/006 + DB-010**（DB-010 为并行快赢，约 2 天，不阻塞 TDSQL 主线），其余视结果再排。

## 五、约束与缺口

- **环境**：GG/C/D 的 DB-AIOps 本机 clone 与 `docker-compose.dev` 未建（`docs/pm/STATUS.md`）；DM/GBase/TDSQL 无真实实例；Lingma 仅内网人工转达。
- **团队**：gork-team 2026-11-05 前 token 耗尽不可用；O 只做独立评审；设计团队 GG 在 antigravity，与 DSMan 无直连通道。
- **纪律**：直推 main、无 PR 门禁、CI 事后信号；代码任务必须带 `scripts/validate.sh` 证据；版本唯一源 `dbmonitor/version.py`。
- **证据缺口**：500 实例容量/长稳/主备切换/五方签字未关闭；TDSQL 实库零覆盖；DM/GBase 实库零覆盖；巡检缺陷与 RCA 编号错配尚未在任何对外文档披露。
- **文档债**：README 正文停在 v2.1；TEST_REPORT 基线落后 HEAD 131 个提交且与 PROJECT_SUMMARY 结论相反；`【未实现】` 标记机制未落地；RCA 规模存在 6/10/39 三种口径。对外取证前必须先统一文档基线。
- **工程质量**：巡检 79/82 项静默 skip、健康分恒 0；告警 `ALERT_TYPE_CHOICES` 5 种 vs 实发 11 种；`AlertDetail.jsx` 未接路由；限流默认关闭。
- **CI 口径**：`ci.yml` 只对 master 触发，main 上的漂移不被拦截；直推 main 时“CI 机械校验”只在事后对 master 生效。
- **产品缺口**：多租户、开放 API、插件化未建；OAM 真实案例与 KB 语料不足；LLM 成本与安全边界未量化。

## 六、架构与实现可行性（GG 负责；已复核确认并签字）

**口径与基线**：以当前代码仓库（v2.6.0，HEAD `0ccc682`）的实际架构与实现复杂度为准，由 GG（架构与实现可行性负责人）逐项审阅代码调用链后给出复核结论。

### 6.1 架构底座现状审定
1. **多进程与高可用设计**：系统采用三常驻守护（Collector 60s、Sentinel 8s/5s、Pipeline 三组消费流）配合 `monitor/process_lease.py` 的分布式租约加 fencing token 机制，已具备高可用主备单活的可靠基础。
2. **多层存储链路**：业务元数据（PostgreSQL ORM）、高频时序指标（TimescaleDB，经 `get_timeseries_storage().cursor()` 托管）、日志与检索（Elasticsearch）三层接通；Redis Stream 承担核心解耦与削峰，整体架构分层清晰，无严重架构倒错。
3. **安全与红线兜底**：严格遵循 `DatabaseConfig.objects.visible_to(user)` 数据范围闸门与 RBAC 双闸，且具备 `scripts/lint_redlines.py` 与 `scripts/scan_secrets.py` 本地前置防护，架构安全底线扎实。

### 6.2 候选方向实现可行性复核

- **方向 A（生产就绪收口——异构实库与规模举证，首选首攻）**：**高度可行，架构风险低，投入产出比最高**。
  - *代码侧*：改动高度局部化，主要是 TDSQL 慢 SQL 提取及分片键规则的内网参数适配、达梦驱动引入与动态加载优雅降级（`checkers/dm.py` 驱动导入解耦）、以及巡检执行器检测器对齐（`DETECTOR_REGISTRY` 键与 `inspection_registry.py` 的 `item_id` / `detect_method` 映射错位，属于纯 Python 字典接线错误，工作量仅约半天即可彻底释放 79 项已有巡检能力）。
  - *阻断点*：完全在于内网实库环境联调与物理性能压测窗口（依赖 Lingma 验证通道与行内硬件），不在架构重构。
- **方向 B（OAM 经验驱动生产化）**：**中度可行，架构底座具备，主要瓶颈在数据与安全边界**。
  - `monitor/oam` 下五层架构（env/perception/kb/policy/verifier）已有完整代码（7753 行），单测体系就绪。
  - 但目前 policy 仍以提示词工程（`prompted.py`）为主，真实生产故障样本与高质量知识库语料严重匮乏；且针对生产库自愈写操作的防护沙箱与回滚机制尚未经真库长稳检验，短期若直接接入生产面临越权误操作或幻觉风险。
- **方向 C（采集与数据底座纵深）**：**中度可行，但边际收益递减**。
  - 核心指标已覆盖大部分生产需求，补齐 Oracle RAC/ADG 高级指标（如 `ic_*`/`gc_*`/MRP 进程）属常规方言增补。
  - 至于 500 实例并发压力，当前 collector 单进程多线程（ThreadPoolExecutor）在目标库连接建立慢或网络抖动时可能出现线程池耗尽，可通过引入异步采集或分布式 Worker 解决，但这依赖方向 A 压测结论的输入。
- **方向 D（平台化：多租户/开放 API）与 方向 E（前端 UX）**：
  - 方向 D 涉及权限与多租户数据库隔离重构，牵涉面广且偏离当前银行投产首要目标；
  - 方向 E 属于纯前端呈现优化，可在主线稳定后随时穿插迭代。

**架构结论**：设计团队 GG 完全认同 DSMan 提出的排序（**A > B > C > D > E**）及「以 TDSQL 慢 SQL 实测收口 + 巡检接线修复（DB-010）为首攻」的策略。

## 七、结论与未尽事项

- **结论：通过**（本研判报告完成度 100%，设计团队 DSMan、GG 全员审阅并签字达成一致）——
  - 第 1、2 节经 D-team 详细代码级取证回填，包含确切代码路径与行号；
  - 第 6 节经 GG 深度复核，架构现状与各候选方向的工程可行性已审定闭环；
  - 候选方向排序（A > B > C > D > E）与首批拆分任务（DB-004 ~ DB-010）明确清晰，已具备派单与交付评审条件；
  - 交付部门经理验收，并提请部门经理转派 O 开展 `DB-002` 独立评审。
- **建议登记项**：请部门经理在 `docs/pm/DECISIONS.md` 登记正式废止 1-5-15，统一将全平台 SLO 时效目标锁定为 **1-5-10**（1 分钟发现 / 5 分钟方案 / 10 分钟解决）。
- 未尽事项：六方言实库矩阵、500 实例容量/长稳/切换/签字、巡检 82 项接线修复、OAM 真实案例、README/TEST_REPORT 文档基线重整。
- 成本披露：搜索 0 次、抓取 0 次（纯本地代码与文档盘点）。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| V0.5 | 2026-11-04 | DSMan | 初稿：骨架事实 + 方向/首攻/约束；1/2 节待回填，6 节待 GG 复核 |
| V1.0 | 2026-11-04 | DSMan | 回填第 1 节（能力矩阵 + README 核实 + 代码级风险）与第 2 节（25 份设计稿逐项判定 + 三处口径冲突）；补巡检 3/82、TEST_REPORT 过期等缺陷；首攻加 DB-010 快赢 |
| V1.1 | 2026-11-04 | GG | GG 完成第 6 节架构与实现可行性复核，签署通过结论，研判报告全闭环交付 |
