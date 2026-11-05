# DB-AIOps 建设方向研判报告（设计团队）

- 任务单：`docs/pm/orders/DB-001-设计团队-建设方向研判.md`、`docs/pm/orders/DB-011-设计团队修订DB-001研判.md`
- 执行：DSMan（队长、研判主笔）、GG（架构与实现可行性）
- 版本：V2.0（按 DB-002 评审 R1～R4 返工点全面修订，设计团队合意签署）
- 日期：2026-11-05
- 结论：**通过**（研判完成度 100%，已逐条闭合 O 在 DB-002 指出的四项主要缺陷，待部门经理验收后派 O 独立复审）
- 取证来源与基线：
  - 源码分析基线：Git HEAD `0ccc682` / `edf9942`（静态走查 + 调用点检索 + AST 语法树解析）。
  - 评审反例与实测证据：引自 O 独立评审报告 `docs/pm/reports/DB-002-report-O.md` 及其取证工件 `docs/pm/reports/evidence/DB-002-O/`（含 `probe-results.json`、`reproduce.py`）。
  - 搜索/抓取次数：0 次（纯本地代码、测试用例与设计文档复核）。

---

## 一、v2.6 现状盘点（以代码为准）

**口径**：HEAD 静态走查 + AST 语法树分析 + 调用点验证；“真实现”指代码路径完整且可被调用，不等于环境验证通过（本轮未启动全部真实异构库）。对未测或存在反例的入口，明确标注真实状态，不宣称未测入口全量安全。

### 1.1 规模与骨架

- **版本单一源**：`dbmonitor/version.py` = **2.6.0**；85 个模型 / 35 个 migration；158 条 `api/` 路由 + 148 个视图类；18 个管理命令（3 个常驻守护）；前端 29 个页面（28 个挂路由）。
- **三守护机制**：collector（60s 轮询）、sentinel（8s 探活 / 5s ASH）、pipeline（Redis Stream 三消费组）；均基于 `monitor/process_lease.py` 的租约加 fencing token 实现单活与切换。
- **存储链路**：PG(ORM) + TimescaleDB + Elasticsearch 三层接线（`start_monitor.py:400-472`）；**Redis 是 sentinel/pipeline 的前置依赖**（未配 `REDIS_URL` 时直接 `ValueError`）。
- **测试现状**：23 个 `monitor/tests*.py`（含 24 条方言测试用例 `tests_dialect.py`、以及 A-AUTORUN 测试 `tests_pm_a_inbox.py`）；V3.0 OAM SIT/UAT 51/51（有条件通过，遗留 R-1~R-5）。

### 1.2 能力矩阵（按域）与安全真实状态

| 能力域 | 判定 | 关键证据与真实边界 |
|---|---|---|
| 采集 | 部分实现（达梦条件） | Oracle 141 键、MySQL 274 键、PG 228 键、DM 174 键、GBase 132 键、TDSQL 含 proxy 拓扑；`checkers/__init__.py:46-54` 注册。达梦缺驱动时静默跳过 |
| 告警 | 真实现（文档口径滞后） | 实发 11 种 `alert_type`；`AlertLog.ALERT_TYPE_CHOICES` 只声明 5 种；静默窗/通知规则/模板/批量删除齐全 |
| 三层检测 | 真实现 | `detectors/l1_hard.py`、`l2_baseline.py`、`l3_composite.py`、`config_drift.py`；阈值 80/95%、空间 90/95%、阻塞 10/30s |
| 基线与容量 | 真实现 | `baseline_engine.py` 168 时间槽 + Welford；`capacity_engine.py`；`health_engine.py` |
| RCA | 真实现（编号已换代） | 生产走 `rca_engine_v2.py`：39 条 `RULES_V2` + 1 条复合；v1 仅兼容薄壳（`analyze()` 发 DeprecationWarning 后转发 v2） |
| 巡检 | **已知严重缺陷与越权** | **入口缺陷**：手动巡检接口 POST 400（参数不匹配）；**安全越权**：详情与触发缺少数据范围闸；**规则接线**：82 项规则仅 3 项被调用，79 项静默 skip（详见 1.4） |
| 事故/工单/Playbook/SLA/值班 | 真实现 | `Event/Incident/Problem`、`IncidentWarRoom.jsx`、`PlaybookRun*`、`SlaReport.jsx`、`oncall.py` |
| LLM / Agent / Copilot | 代码完整，默认关闭 | `llm/` 10 文件：路由、诊断、Agent 循环、只读工具、证据包、Schema 校验、SSRF 防护；`LLM_ENABLED/AGENT_ENABLED=False` |
| OAM | 五层齐全，默认关闭 | `monitor/oam/` 34 文件（env/perception/kb/policy/reward/verifier/bench/audit）；`OAM_ENABLED=False`；`kb/rebuild` 恒返回 501 |
| 前端 | 29 页，1 页未接线 | `AlertDetail.jsx` 仅 import、未挂路由；列表页用内嵌弹层，独立详情页不可达 |
| 权限/审计/可观测 | **存在局部越权缺口** | 虽有 `DatabaseConfig.objects.visible_to(user)` 与 RBAC 双闸，但**巡检 API（`api_views_phase5.py`）未接入**，不能宣称全量安全 |

### 1.3 README 宣称核实（真未实现 vs 文档滞后）

| README 宣称 | 判定 | 代码证据与事实核实 |
|---|---|---|
| 6 种库全量采集 | **部分实现** | Oracle/MySQL/PG/GBase/TDSQL 代码已接入；达梦 `dm.py` 强依赖 `dmPython`，`requirements.txt` 装的是 `pyodbc`，缺驱动时静默跳过采集 |
| 6 类告警 | **真实现且超出文档** | 实发 11 种；但 `ALERT_TYPE_CHOICES` 只有 5 种，存在口径漂移 |
| RCA 内置 R001~R006 | **文档滞后，编号漂移** | 仅 R001 对得上；R002→R011+R021、R003→R031、R005→R061/R083、R006→R081；**R004「QPS 突降」在 v2 无独立规则** |
| 基线分析引擎 | 真实现 | `baseline_engine.py` + `alert_engine.py` 三重条件/收敛/升级 |
| 邮件告警 | 真实现（默认静默失败） | `notifications.py:87` 走 Django mail；`ADMIN_EMAILS` 为空时返回 False，不报错 |
| Dashboard/详情页 | 真实现 | `Dashboard.jsx`、`DatabaseDetail.jsx`；原 `monitor/templates/monitor/*.html` 已清理 |
| 基线示例 `calculate_baseline` | **文档滞后（示例不可运行）** | 该方法在 `IntelligentBaselineEngine`；`BaselineEngine` 只有 `calculate_baseline_for_metric`/`calculate_full_baseline`，直接运行抛 `AttributeError` |
| 配置示例 `create(password='明文')` | **文档滞后（安全错误示例）** | `models.py:86,113-121` 需 `set_password()` 才加密；照抄会导致明文落库 |
| 改 `start_monitor.py` 顶部“配置区”调阈值 | **文档滞后** | 阈值定义在 `checkers/base.py:18-20`，`start_monitor.py:47-49` 仅为 import |
| 能力边界“诚实声明”（标注 `【未实现】`） | **真未实现（声明未落地）** | 全仓库仅该声明自身出现 1 次，无具体功能打标 |
| 巡检 82 项规则 | **部分实现（严重缺陷）** | `inspection_registry.py` 定义 82 项；执行器 `inspection_executor.py:1665` 仅 3 个 TDSQL 项直接命中，79 项返回 `status=skip`；手动触发接口返回 400 |
| TEST_REPORT 结论 | **已过期且与 PROJECT_SUMMARY 冲突** | 报告基线 `98c7637` 落后 HEAD 140+ 提交；严重缺陷已修，但文档仍保留“不通过”结论未刷新 |

### 1.4 影响方向判断的代码级风险与真实反例（吸收 O 评审 R1/R2/R3）

1. **巡检详情与触发接口范围越权（反例 R1，高危）**：
   - `monitor/api_views_phase5.py:294-303`（`InspectionRunDetailView`）仅标记 `@require_auth`，直接通过 `InspectionRun.objects.get(run_id=run_id)` 读取，**未套用 `DatabaseConfig.objects.visible_to(request.user)` 过滤，也缺少功能权限闸门**。经 O 隔离验证（`DB-002-O/reproduce.py`），受限用户能跨范围直接 GET 读取其他实例的巡检 findings 与 `raw_data`（HTTP 200）。
   - `monitor/api_views_phase5.py:222-239`（`InspectionRunTriggerView`）同样裸查 `DatabaseConfig.objects.get(id=config_id)`，受限账号甚至零权限账号均可向非授权实例发起巡检请求并写入失败记录。
   - **结论更正**：全系统虽有双闸设计，但 Phase 5 巡检模块属于明显漏网之鱼，**“全平台双闸严格闭合”不成立**。在修补该模块前，严禁宣称未测 API 全量安全。
2. **手动巡检入口本身不可用（反例 R2，高危）**：
   - `monitor/api_views_phase5.py:256-257` 调用 `InspectionExecutor(config, level=level)` 然后调用 `executor.run()`。
   - 但 `monitor/inspection_executor.py:1509-1515` 的实际定义为 `__init__(self, max_workers=5, default_timeout=30)`，业务执行方法是 `run_for_db(db_config, level)`，根本不存在接收 `config, level` 的构造器，也不存在 `run()` 方法。
   - 实测向 `/api/v1/inspection/runs/trigger/` POST 发起巡检，**恒返回 HTTP 400（`InspectionExecutor.__init__() got an unexpected keyword argument 'level'`）** 并遗留 failed 记录。
   - **结论更正**：此前所谓“82 项能跑 3 项”，仅仅是在调度器 `inspection_scheduler.py` 内部探针下的注册覆盖表现；**UI 手动巡检主入口处于完全中断状态**。
3. **巡检 82 项分层映射与真实覆盖缺口（反例 R3，纠正工作量预估）**：
   - 撤回 V1.1 中“工作量仅约半天即可彻底释放 79 项已有巡检能力”的轻率表述。经对 82 项定义的精细 AST 与反射分析，82 项巡检规则并非仅改字典就能点亮：
     - **直接命中 `item_id`**：仅 **3 项**（`INS-TDSQL-SHARD`、`INS-TDSQL-PROXY`、`INS-TDSQL-SHARDKEY`）；
     - **声明的 `detect_method` 在检测器类中存在同名实现**：仅 **8 项**（如 `detect_invalid_objects`、`detect_recyclebin`、`detect_long_transactions`、`detect_fk_no_index`、`detect_stale_stats`、`detect_backup_status`、`detect_failed_logins`、`detect_scn_headroom`）。且即使这 8 项有同名函数，也受限于方言适用性与数据采集依赖；
     - **检测器类中完全缺失实现**：高达 **71 项**（如 `detect_locked_users`、`detect_7d_capacity`、`detect_binlog_dump_delay`、`detect_wal_receiver`、`detect_dsc_nodes` 等）。这些项目并非简单的“接线错误”，而是确实**尚未编写具体的检测逻辑代码**！
   - **健康分评分逻辑缺陷**：`inspection_executor.py:1699-1703` 在计算总分时分母排除了 skip 项。实测当 82 项全 skip 时分数为 0；当 1 个 ok + 79 个 skip 时，分数畸形显示为满分 100 分。该逻辑掩盖了真实检查覆盖率严重不足的现状，前端契约必须明确区分**未评估（未运行）、覆盖不足（跳过过多）、正常**三种状态。
4. **达梦驱动与方言断层**：按标准依赖部署时达梦被静默跳过，六库支持存在“文档宣称支持、默认环境无法运行”的断层。
5. **RCA 告警门槛**：`start_monitor.py:856` 仅对 `severity=='critical'` 发告警，多数 warning/error 规则仅入事件流，对外宣称“39 条规则自动告警”会造成误导。
6. **RedisChecker 是存根桩**：`start_monitor.py:68-75` 恒返回 DOWN，误配 redis 会持续产生误报。
7. **AI / OAM 能力默认全关**：默认部署形态下无任何 AI 介入。
8. **CI 触发分支与默认分支漂移（已由 DEC-006 定调）**：历史状态下 `ci.yml` 仅监听 master，目前按 DEC-006 统一改监听 main，master 冻结。

---

## 二、设计稿时效性评估

**范围**：仓库根目录 22 份设计/方案类 `*.md` + `plans/` 下 3 份，共 25 份（基线 HEAD `0ccc682`）。  
**判定口径**：已落地 / 部分落地 / 已过时 / 仍有效（四档互斥；以当前代码和实测证据为准，不凭文档自述或仅有个别代码存根推定整稿完成）。  
**汇总（经 R4 返工重算）**：**已落地 11 份、部分落地 12 份、已过时 1 份、仍有效 1 份**。

### 2.1 逐项判定表

| # | 文档路径 | 判定 | 修正依据与真实边界（吸收 DB-002 R4 返工点） |
|---|---|---|---|
| 1 | `DB_AIOps_DESIGN.md` | 部分落地 | 告警/审批/自监控/健康分在；但 §13 Kafka/RabbitMQ 事件总线、多租户（`tenancy.py` 已删）、MLOps 未实现 |
| 2 | `DB_AIOps_MASTER_DESIGN.md` | 部分落地 | 三平面架构在；Celery 虽有历史代码但在 `start_monitor.py:62` 默认关闭（`USE_CELERY=False`），现行主干为 sentinel + Redis Stream |
| 3 | `DB_AIOPS_V2.0_DESIGN.md` | 部分落地 | 性能页/ASH/WarRoom 在；旧口径 1-5-15 已被 DEC-005 明确废止并统一为 1-5-10；Multi-Agent 智囊团无独立实现 |
| 4 | `DESIGN.md` | **部分落地**（**更正**） | 原判“已落地”撤回。虽然基础安全（crypto/auth）、通知和时序库存在，但该稿第一期（§12.1）验收标准包含**全行 200 套真实数据库接入、采集成功率≥99%、生产环境基线部署**。代码片段存在不能证明整期与整稿已通过验收 |
| 5 | `V2.5_UPGRADE_DESIGN.md` | 部分落地 | WP0–WP4 核心代码在。**更正说明**：`.github/workflows/ci.yml:343-354` 已经实现了 CycloneDX Python SBOM 生成与上传流水线；但镜像签名、WORM 与行内准入在仓库外仍待核实 |
| 6 | `V2.6_HARDENING_DESIGN.md` | 部分落地 | W1–W5 的测试、规则整合与脚本施工已落地；但该设计宣称的权限范围兜底被本次 R1 越权反例击破，不能判定整稿无缺陷闭合；L1 SSE 全量广播仍是真实缺口 |
| 7 | `V3.0_DBAGENT_REDESIGN.md` | 部分落地 | OAM 五层代码与 verifier/bench 存在，但 policy 仅实现 `prompted.py`；GRPO 强化学习未做，生产级实库表现与基线跑分缺乏验证 |
| 8 | `V3.0_DIALECT_MATRIX.md` | **仍有效**（**更正数据**） | **用例存量更正为 24 条**（`tests_dialect.py` AST 确证为 24 条测试函数，含新增 3 条 MySQL 慢 SQL 方言测试）；**更正 GBase 证据说明**：GBase 8a 在历史测试中已有**真实容器连通与版本探测证据**（`8.6.2.43-R7`），卡点在于仓库缺失自动化用例资产及 `sample_sessions` 兼容性，而非“实库零覆盖” |
| 9 | `PHASE5_DEVELOPMENT_DESIGN.md` | 部分落地 | RCA 2.0 与巡检模型已交付；但巡检存在 R1 越权与 R2 入口 400 故障；`impact_engine.py` 整合了部分影响分析，但缺乏完整的因果图（causal_graph）等价验证 |
| 10 | `PHASE6_DEVELOPMENT_DESIGN.md` | 部分落地 | Event/Incident/Problem、sentinel、pipeline 消费流在；但 1-5-10 属于生产运行期指标，演练脚本尚无全场景达标证据 |
| 11 | `NEXT_PHASE_DEVELOPMENT_DESIGN.md` | **部分落地**（**更正**） | 原判“已落地”撤回。虽然 checkers 拆分与多层写入在，但该稿 **P2-1 明确包含 Redis/MongoDB 监控设计**（`:1550-1561`），而当前 RedisChecker 仅为 DOWN 存根，Mongo 为 None，存在明确未实现章节 |
| 12 | `PROJECT_IMPROVEMENT_DESIGN.md` | 已落地 | `validate.sh`、appconf/self_monitor、依赖与密钥扫描均在运行；附录 B 自带落地实测 |
| 13 | `PROJECT_ANALYSIS_AND_NEXT_PHASE.md` | **已过时** | v2.2 时代体检报告，单体与早期阶段假设已被重构推翻，不可作为现行依据 |
| 14 | `METRICS_DESIGN.md` | 部分落地 | 多数核心指标已在 checkers 中实现，但 §3 当前状态表已脱离代码实际；指标返回值语义与缺值表现仍需方言验证 |
| 15 | `DB_SCHEMA_4NF_DESIGN.md` | 已落地 | 12 组多值字段拆表及 0018/0023 存量回填全部完成且经迁移测试覆盖 |
| 16 | `ORACLE_RAC_ADG_DESIGN.md` | 部分落地 | RAC 节点与 ADG 基础时延在；但 `ic_*`、`gc_*`、归档缺口及 MRP 进程细节仍未全量补齐 |
| 17 | `TDSQL_SLOWSQL_DESIGN.md` | 部分落地 | 按 set 采集、SQL 详情、退化对比已在代码库实现并有单测；§8 内网实测清单 V-1~V-10 仍待执行；明确不做 proxy 层耗时拆解 |
| 18 | `HARNESS_IMPROVEMENT_DESIGN.md` | 已落地 | `AGENTS.md` 编辑-验证纪律、脚本及钩子均已就位 |
| 19 | `BATCH_ALERT_DELETE_DESIGN.md` | 已落地 | 批量删除视图、路由及前端勾选交互完整实现 |
| 20 | `BUGFIX_DESIGN.md` | 已落地 | 35 项缺陷修复与回归用例成体系；文档自留 BUG-132 APIKey 缓存持久化缺口已注明 |
| 21 | `SCOPE_HARDENING_CHANGELOG.md` | 部分落地 | 记录的既有视图整改可指认，但本次 R1 证明 Phase 5 巡检接口遗漏了整改，范围硬化尚未全量闭环 |
| 22 | `V2.5_REVIEW_REMEDIATION_DESIGN.md` | 已落地 | R25-01~R25-12 涉及的既有加固项均可在代码中核对 |
| 23 | `plans/database-list-ux-design.md` | 已落地 | 列表页六大 UX 改进（缓存、并行拉取、健康分、徽章等）在 `DatabaseList.jsx` 落地 |
| 24 | `plans/frontend-ux-optimization-design.md` | 部分落地 | 性能页、EMCC 风格与 SQL 页面已在；但新增模型和部分扩展接口未完全对齐现库 |
| 25 | `plans/bank_simulator/DESIGN.md` | 已落地 | 模拟器、负载注入命令与脚本完整且可运行 |

### 2.2 统一口径澄清

1. **时效目标统一为 1-5-10**：依据 **DEC-005** 裁决，旧口径 1-5-15 正式废止。必须明确区分“业务 SLO 目标”、“检测预算”与“实际运行期达标证据”三层，不假称当前全平台已无条件达标。
2. **编排底座现行为 Redis Stream**：Celery 默认未启用，现行生产依托 sentinel + pipeline 三消费流。
3. **v3.0 区分引用**：区别《NEXT_PHASE》中的数据完整性（已大部分落地）与《V3.0_DBAGENT》中的 OAM 架构（部分落地）。

---

## 三、候选方向（排序与研判）

### 方向 A：生产就绪收口——安全修复、异构实库与规模举证（推荐首攻）

- **目标**：以银行生产准入为标准，先闭合 R1 越权与 R2 接口故障，再收口 TDSQL 慢 SQL 全链路在真实环境（proxy+set）下的表现；补齐达梦驱动与 GBase 方言用例；完成 500 实例容量与主备单活长稳演练，产出五方签字材料。
- **主要工作量**：
  1. 巡检 API 增加 RBAC 权限与 `visible_to` 范围闸门（安全前置）；
  2. 巡检执行器契约修复（解决 400）与 82 项分层梳理；
  3. TDSQL 内网实测（Lingma 通道）与真实缺陷修复；
  4. GBase 8a 方言用例编写与 `sample_sessions` 修复；
  5. 达梦 `dmPython` 动态加载解耦；
  6. 500 实例压测与故障切换演练。
- **风险**：依赖内网物理环境和窗口；Lingma 人工转达耗时波动。
- **对银行生产的意义**：这是系统能否正式上线投产的决定性前提，证据最硬，直接解除上线阻断。

### 方向 B：OAM 经验驱动生产化（差异化，次优先）

- **目标**：推进 V3.0 OAM 从框架演示走向真实辅助：构建真实故障知识库，规范自愈写操作的安全沙箱与人工复核机制。
- **主要工作量**：真实故障样本标注、知识库向量检索质量调优、高危写操作隔离验证。
- **风险**：模型幻觉导致的生产误操作；提示词策略成本可控性。
- **对银行生产的意义**：构筑智能化技术壁垒，但严重依赖方向 A 提供的高质量指标与安全边界。

### 方向 C：采集与数据底座纵深（基础支撑）

- **目标**：补齐 Oracle RAC/ADG 高级指标（`ic_*`/`gc_*`/MRP 进程），针对 500 实例高负载优化采集并发架构。
- **主要工作量**：Oracle 方言补充、单进程线程池向分布式 Worker 平滑迁移设计。
- **风险**：高频连接对被监控生产库造成负荷。
- **对银行生产的意义**：提升监控精细度，属于常规演进。

### 方向 D：平台化与多租户隔离
- **目标**：租户逻辑完全隔离、开放 API 网关。当前银行专网需求紧迫度低，工作量巨大。

### 方向 E：前端 UX 与细节打磨
- **目标**：图表丰富度与复杂交互微调。可穿插在主线中进行。

**排序结论**：**A > B > C > D > E**。

---

## 四、首攻建议与首批任务拆分（严格顺序与前置依赖）

**首攻策略**：以 **方向 A** 为主攻切片，**安全与契约先行，实库验证严格按依赖推进**。

### 4.1 任务执行拓扑与依赖顺序

```text
               ┌────────────────────────┐
               │ DB-010A: 巡检安全与契约 │ (安全前置，本地立即开工)
               │ (修 R1 越权 + 修 R2 400) │
               └───────────┬────────────┘
                           │
                           ▼
               ┌────────────────────────┐
               │ DB-010B: 82项分层映射   │ (输出真实状态与不适用清单)
               │ 与覆盖率真实呈现        │
               └────────────────────────┘

┌──────────────────────┐         ┌──────────────────────┐
│ DB-004: TDSQL 环境准备│ ──────> │ DB-005: 慢SQL内网实测│
│ (只读保护/拓扑/探针)  │         │ (列表/分析/退化/构成) │
└──────────────────────┘         └──────────┬───────────┘
                                            │
                                            ▼
                                 ┌──────────────────────┐
                                 │ DB-006: 按需缺陷修复  │
                                 │ (main提交+复测证据)  │
                                 └──────────┬───────────┘
                                            │
                                            ▼
                                 ┌──────────────────────┐
                                 │ DB-005 复测闭环      │
                                 └──────────────────────┘
```

### 4.2 首批任务清单（建议草案）

| 建议单号 | 任务名称 | 前置依赖 | 建议执行 | 核心验收标准 |
|---|---|---|---|---|
| **DB-010A** | 巡检 API 安全闸与调用契约修复 | 无（立即并行） | GG / C | 1) `api_views_phase5.py` 全面引入 `visible_to` 与权限闸；<br>2) 编写越权回归测试（覆盖零权限与越界实例）；<br>3) 修复 `InspectionExecutor` 构造与调用，手动 POST 触发通过（非 400）。 |
| **DB-010B** | 巡检 82 项规则分层映射与真实覆盖率呈现 | DB-010A | DSMan / GG | 1) 产出 82 项完整映射矩阵（定义→方法→callable同名→方言约束→真缺实现）；<br>2) 修正健康分评分逻辑，区分未评估、覆盖不足与正常。 |
| **DB-004** | TDSQL 内网实测准备与安全探针 | 无（先行铺垫） | Lingma / DSMan | 1) 确认内网环境、只读权限与 proxy+set 拓扑；<br>2) 运行只读探针 V-1~V-9，明确 V-7 失败时严禁执行 EXPLAIN ANALYZE。 |
| **DB-005** | TDSQL 慢 SQL 全链路内网首轮实测 | DB-004 就绪 | Lingma（执行）<br>A（复核） | 1) 覆盖慢 SQL 列表、耗时构成、环比基线、优化建议；<br>2) 输出真实实测报告与可复现缺陷单。 |
| **DB-006** | TDSQL 内网实测缺陷定向修复 | DB-005 缺陷输入 | GG / C | 1) 仅由 DB-005 产生的确切缺陷驱动；<br>2) 输出直推 main 的 commit SHA 及 `validate.sh` 证据；<br>3) 交付 DB-005 进行二次复测验收。 |
| **DB-009** | GBase 8a 与达梦环境资产补齐 | 可独立并行 | C / GG | 1) 基于现有 GBase 容器编写 `GbaseCheckerDialectTests`；<br>2) 修复 `sample_sessions` 在 GBase 上的兼容性；<br>3) 达梦驱动动态加载解耦。 |
| **DB-007** | 500 实例容量与长稳基线设计与压测 | DB-005/010 闭环 | GG + A | 1) 给出工作负载、拓扑与采样延迟（P95）；<br>2) 提供真实资源瓶颈定位报告。 |
| **DB-008** | 主备租约切换与五方签字演练 | DB-007 达标 | GG + A + 用户 | 1) 租约切换无重复执行、无证据丢失；<br>2) 恢复后时序与审计对账一致。 |

---

## 五、约束与缺口

1. **环境约束**：
   - DM、TDSQL 无外部公开实例，高度依赖内网与硬件窗口；
   - Lingma 纯人工转达，交互与调度存在时延；
   - GG/C/D 本机开发环境基础设施（`docker-compose.dev`）待进一步完善。
2. **安全与工程约束**：
   - 必须先彻底消除 Phase 5 巡检模块的代码越权（R1）与入口缺陷（R2），方可扩展外部实测；
   - 巡检 71 项未实现规则需按方言拆分、循序渐进补充，杜绝将空桩伪装为成功。
3. **工作流纪律约束**：
   - 直推 `main`、无 PR 门禁；
   - **依据 DEC-006**，默认分支切换为 `main`，`ci.yml` 监听 `main`，`master` 已完全冻结；
   - 每次代码编辑后必须跑通 `scripts/validate.sh unit --json`，推前跑全量门禁。

---

## 六、架构与实现可行性（GG 负责；按 R1～R4 返工复核确认）

**基线**：HEAD `0ccc682` / `edf9942`。

### 6.1 架构底座与安全边界现状审定
1. **多进程守护与调度底座**：Collector、Sentinel、Pipeline 三常驻守护基于 `ProcessLease` 租约 + fencing token，机制扎实；
2. **多层存储解耦**：PostgreSQL(ORM) + TimescaleDB + Elasticsearch 分工明确，时序库由上下文管理器严格托管；
3. **安全闸门覆盖评估**：核心模块严格遵循 `DatabaseConfig.objects.visible_to(user)` 与 RBAC；但本次评审暴露了 **`monitor/api_views_phase5.py` 的明显遗漏**，证明历史加固存在死角。必须在架构规范中明确：**所有对外暴露的 API 必须同时接入权限闸门与数据范围过滤**，并纳入前置 CI 静态红线拦截。

### 6.2 候选方向实现可行性深度复核（撤回低成本承诺，实事求是）

- **方向 A（生产就绪收口，首选首攻）**：**高度可行，但必须纠正工作量与风险认知**。
  - *安全与入口*：R1 越权与 R2 构造错误集中在 `api_views_phase5.py`，逻辑清晰，可迅速完成修复与单元测试覆盖；
  - *巡检 82 项规则*：**推翻此前“半天字典修复 79 项”的不实判断**。实际只有 8 项有同名方法，71 项完全缺失实现代码。因此巡检工作量必须拆解为：
    - **阶段一（契约与呈现）**：完成安全闸门接入、执行器契约修正、并在报告中诚实展示跳过项与未评估状态（覆盖率真实呈现）；
    - **阶段二（能力补充）**：按通用、Oracle、MySQL 等方言分类，按需补齐高价值检测器实现；
  - *TDSQL 慢 SQL 实测*：代码侧框架已经就绪，依赖内网真机执行与参数微调；
  - *GBase 方言*：已有真库环境连通记录，重点是补齐 `tests_dialect.py` 自动化测试类并适配 `sample_sessions` 的元数据视图差异。
- **方向 B（OAM 经验驱动）**：架构分层优良，但当前缺乏高质量样本，在没有完备的沙箱执行与只读限制前，严禁直接放通生产自愈写权限。
- **方向 C（采集纵深）**：常规指标增补，500 实例采集稳定性需在方向 A 压测结论后定调。

---

## 七、结论与未尽事项

- **结论：通过**（设计团队 DSMan、GG 共同复核签署）——
  - 全面吸纳 O 独立评审（DB-002）的 R1～R4 返工要求；
  - 充分披露了巡检模块的数据范围越权与手动接口 400 故障；
  - 彻底撤回无证据的工期承诺，完成 82 项巡检规则的分层映射清点；
  - 纠正了设计稿判定与证据说明中的过度概括与错误；
  - 重构了首批任务的依赖拓扑，明确了安全前置与内网实测闭环流程。
- **未尽事项**：
  - 实施 DB-010A 修复巡检 API 越权与调用契约；
  - 开展内网 TDSQL 慢 SQL 全链路实测与缺陷闭环；
  - 实施 500 实例容量与长稳切换演练。
- **成本披露**：搜索 0 次、抓取 0 次（纯本地代码走查与文档核验）。

---

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| V0.5 | 2026-11-04 | DSMan | 初稿：骨架事实 + 方向/首攻/约束 |
| V1.0 | 2026-11-04 | DSMan | 回填第 1、2 节代码盘点与 25 份设计稿初判 |
| V1.1 | 2026-11-04 | GG | GG 完成第 6 节可行性初审并签字交付评审 |
| V2.0 | 2026-11-05 | DSMan, GG | **按 DB-002 评审 R1～R4 意见全面返工修订**：<br>1) 披露巡检 API 数据越权（R1）与手动入口 400（R2）；<br>2) 撤回“半天修复 79 项”承诺，完成 82 项分层映射与健康分逻辑分析（R3）；<br>3) 纠正 25 份设计稿判定（NEXT_PHASE/DESIGN/SBOM/GBase/方言 24 条用例）（R4）；<br>4) 统一基线与口径（DEC-005 统一 1-5-10，DEC-006 统一 main 分支）；<br>5) 重构首批任务依赖拓扑（安全前置 + 004→005→006→005 复测）。设计团队合意签署通过 |
