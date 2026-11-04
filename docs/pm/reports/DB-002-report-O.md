不通过

# DB-002：O 独立评审 DB-001 V1.1

- 评审人：O（不属于设计团队）。日期：2026-11-04（Asia/Shanghai）。
- 对象：`DB-001-report-design-team.md` V1.1，提交 `d86958b29cb6d1ede73d63c804199e425317050b`；接单检出 `56885c1184a916a17b179df433fc077113da716c`。设计团队原文未改。
- **不通过的是 V1.1 作为“现状已核清、低风险可直接派单”的评审基线，不是否定优先收口生产就绪证据的方向。** TDSQL 实库验证优先有依据，但报告漏掉实际可复现的巡检入口故障和数据范围越权，并把巡检修复范围显著说小，影响 DB-010 的成本与验收设计；需设计团队修订报告后复审。团队自评“通过”不是 PM 验收，O 本结论也不代替 PM/用户裁决。

## 一、主要发现与返工要求

### R1〔高〕巡检详情存在范围越权，不能笼统认定“双闸严格闭合”

报告 §1.2 权限域、§6.1 第 3 点将 RBAC + visible_to 描述为扎实闭合的底线，漏掉巡检 API。

- `monitor/api_views_phase5.py:294-303` 的详情视图仅 `require_auth`，直接 `InspectionRun.objects.get(run_id=...)`；没有相应权限闸，也未用 `db_config` 可见范围限定。
- 本次使用隔离 SQLite、合成用户/数据库/发现项，先确认控制条件：该用户有功能权限，但 `DatabaseConfig.objects.visible_to(user)` **查不到另一实例**。随后 GET 该实例的巡检详情，**HTTP 200**，返回另一实例名称与 finding 的合成 `raw_data` 标记。
- 同文件 `:222-239` 的触发接口也只鉴权并裸查实例；有权限但范围受限的用户可为范围外实例创建失败的 InspectionRun。零功能权限用户也能触及该写路径。这里实测到的是越界读取和元数据写入，**没有证明、也没有声称已在目标库执行 SQL**。

**返工/派单条件**：在报告的安全现状和首批缺陷清单中披露；巡检修复须先补权限与实例/Run 范围闸，再放通执行入口，分别覆盖“零权限”和“有权限但范围外”的回归。不要只修执行接线后扩大既有未授权执行面。其余 Phase 5 相关入口需同类排查，未测入口不宣称全量安全。

### R2〔高〕手动巡检入口本身不可用，“巡检路径完整”不成立

- `monitor/api_views_phase5.py:256-257` 调用 `InspectionExecutor(config, level=level)`，再调用 `executor.run()`。
- 实际定义 `monitor/inspection_executor.py:1509-1515` 的构造参数为 `max_workers/default_timeout`，业务接口是 `run_for_db(db_config, level)`，没有该 `run()` 契约。
- 实测对自有实例 POST `/api/v1/inspection/runs/trigger/`，**HTTP 400**，错误为 `InspectionExecutor.__init__() got an unexpected keyword argument 'level'`，并遗留一条 `status=failed` 运行记录。没有到达检测器选择，更谈不上该入口成功跑 3 项。
- 调度入口 `inspection_scheduler.py:49-52` 使用 `InspectionExecutor().run_for_all_dbs(...)`，确实会进入执行器。因此“82 项仅 3 项命中”应明确是**规则注册/调度层的结构覆盖**，不能当作 UI 手动巡检已成功运行的证明。

**返工/派单条件**：能力矩阵改为部分实现/已知入口缺陷；DB-010 需包含 API→执行器→持久化→详情展示及调度入口的一致契约验证，不只是改一个字典。

### R3〔高〕“半天字典修复释放 79 项已有能力”的估算无依据

对未改的 registry/executor 做计数和调度探针：

| 项目 | 独立结果 | 解释 |
|---|---:|---|
| ALL_ITEMS 定义 | 82 | 六库定义总和，不是某个实例/某个 daily 轮次的分母 |
| DETECTOR_REGISTRY | 26 | 23 个 Ixxx 旧键 + 3 个 TDSQL 项 |
| item_id 直接命中 | 3 | INS-TDSQL-SHARD / PROXY / SHARDKEY |
| 调度探针 | 79 skip、3 次调用 | 3 个检测器函数体替换为 spy；只验证调度，不验证目标 SQL |
| 声明 detect_method 有同名 callable | 11 | 比直接注册多，但仍远少于 82 |
| 声明方法无同名 callable | 71 | **不等于 71 项全部须从零开发**；可能存在语义相近的旧检测器，但报告未给逐项映射和参数/方言等价证明 |

`_run_single()` 在 `:1664-1674` 直接用 item_id 查表，接线缺陷成立；但仅切换到 detect_method 查找不能使全部定义有实现。例：`detect_locked_users`、`detect_7d_capacity`、`detect_wal_receiver` 不在这两个 Detector 类中。已有 26 个检测器内部也有方言不适用/数据不足返回 skip 的分支，不能把“可调用”当作“六库均验证通过”。

健康分的判断需进一步细化：`:1636-1641` **已经保存 info_count 与 summary.skipped**，不是后端完全没记录跳过；`:1699-1703` 的健康分分母排除了 skip，实测全跳过=0，1 个 ok + 79 个 skip=100。后者不代表覆盖充分；前端/契约应区分未评估、覆盖不足、正常，而非仅改一个健康分数。

**返工/派单条件**：撤回 §6.2“工作量仅约半天即可彻底释放 79 项”、§4 的无证据固定工期；按 82 项列出定义→函数→方言→权限→验证→不适用原因，区分接线、别名映射、缺实现、方言限制。DB-010 可先收口安全/契约/真实覆盖提示，再按映射清单拆分补实现，不能用“82 项全部可跑”掩盖错误 skip 或空桩。

### R4〔中〕25 份文档判定与实库证据有可核实的错分/过度概括

- `NEXT_PHASE_DEVELOPMENT_DESIGN.md` 被判整份“已落地、三件事全部落地”，但该稿还有 **P2-1 Redis/MongoDB 监控**（`:1550-1561`）。实际 `start_monitor.py:68-75` 的 RedisChecker 为 DOWN 存根，`:92` 的 mongo 为 None。不能用三个 P0 主干覆盖整份文档，宜改“部分落地”。
- `V2.5_UPGRADE_DESIGN.md` 中“SBOM 仓库只有设计”不成立：`.github/workflows/ci.yml:343,348-354` 已有 CycloneDX 生成及上传；应拆成“SBOM 流水线已实现、当前产物未核验、镜像签名/行内准入待证”，不能合并为未实现。
- `V3.0_DIALECT_MATRIX.md` 表中 GBase **已有真实 GBase 8a 容器连通及手工探测证据**，缺的是自动方言用例执行；报告 §5“DM/GBase/TDSQL 实库零覆盖”抹去了 GBase 的这层历史证据。O 本轮没有重跑 GBase，不把旧记录升级为当前验收。
- 报告称当前 `tests_dialect.py` 21 条，独立 AST 计数在 **cad2514 和 0ccc682 均为 24 条**，新增的 MySQLSlowSqlDialectTests 有 3 条。矩阵的历史 19 条通过可以保留，但当前用例存量要更新，不能说“重跑才需更新”。
- `DESIGN.md` 的第一期验收含 200 实例接入、≥99% 成功率、生产部署等（`:1018-1049`）。静态文件指认只能支持第一期部分代码落地，不能把整份文档/该期验收判为完成。

**返工条件**：逐条更正范围和依据，重算“13 已落地/10 部分落地”汇总；保留历史实测时间、版本和本轮未重跑边界。不要只改汇总数字而不改逐项证据。

## 二、两套取证基线是否影响结论

- cad2514 = `cad2514ab0a51209c1ea58c74e312129a537217b`。
- 0ccc682 = `0ccc682c2bbc5cc554d42aedb4d83b2d2cc5ca7a`。
- 两者间 12 个提交、27 条路径变化：黑板/回执、自动链及 `monitor/tests_pm_a_inbox.py` 新测试；**产品 monitor 的差异仅该新增测试文件**。dbmonitor、frontend/src、依赖锁、原 ci.yml 和本次关键产品文件未变。
- 已逐对象比较 `inspection_executor.py`、`inspection_registry.py`、`api_views_phase5.py`、dm.py、start_monitor.py、原 ci.yml、README：两基线及接单检出对象一致，详见 [baseline-comparison.json](evidence/DB-002-O/baseline-comparison.json)。

**判断：基线不统一是可修正的取证说明问题，不是以上缺陷的成因，也不能用来解释两节结论偏差。** 统一列出源码提交、报告版本/提交、静态盘点与运行证据的各自基线即可；不得将新增自动链误算成产品能力进展。

## 三、25 份设计稿逐项复核

本表核对每份稿的范围、报告判定与代码落点；“认可”只表示对应静态判断有依据，不代表本轮完成实库或生产验收。保留原报告顺序，避免以少数文件抽样代替逐项覆盖。

| # | 文档 | O 判断与独立依据 |
|---|---|---|
| 1 | DB_AIOps_DESIGN.md | 部分落地可保留：approval_engine/health_engine 等在；大范围事件总线、多租户和 MLOps 不能由这些文件推定完成。 |
| 2 | DB_AIOps_MASTER_DESIGN.md | 部分落地可保留：三平面模块在；实际进程为 collector/sentinel/pipeline。Celery 分发代码仍在 start_monitor:188-207，但采集开关读 MONITOR_USE_CELERY，不能把默认未启用写成历史上“从未启用”的实测结论。 |
| 3 | DB_AIOPS_V2.0_DESIGN.md | 部分落地可保留：性能页、ASH、WarRoom 等有落点；旧时效口径已由 DEC-005 统一废止，保留文档历史属性。 |
| 4 | DESIGN.md | **需缩窄/改部分落地**：crypto/auth/notifications/timeseries 等主干在，但第一期含真实 200 实例与生产验收，代码存在不足以认定整稿已落地。 |
| 5 | V2.5_UPGRADE_DESIGN.md | 部分落地可保留，**更正 SBOM**：ci.yml 已生成上传；附录 B 本来也将其列已落地。签名、WORM、行内联调另列待证。 |
| 6 | V2.6_HARDENING_DESIGN.md | W1–W5 核心修改、测试、规则整合可指认；“已落地”须限定这些施工项，**不能外推全部 API 双闸闭合**，本轮 R1 为反例。 |
| 7 | V3.0_DBAGENT_REDESIGN.md | 部分落地可保留：oam 五层、verifier/bench 在，policy/backends 仅 prompted；没有据此证明真实生产效果或 GRPO 已交付。 |
| 8 | V3.0_DIALECT_MATRIX.md | 历史实测仍有价值；**当前用例存量需更正为 24**，GBase 需区分真实连通和自动方言用例未执行，不能只等重跑才更新清点。 |
| 9 | PHASE5_DEVELOPMENT_DESIGN.md | 部分落地成立，但巡检是功能/安全缺陷而非仅文档旧名；impact_engine 可指认 HealthImpactCalculator/BusinessImpactAssessor，不能笼统说 causal_graph 也已并入该文件，需补实际因果链映射。 |
| 10 | PHASE6_DEVELOPMENT_DESIGN.md | 六支柱主要代码在，限于实现层可认可；run-time 1-5-10 无本轮证据。八类问题矩阵还列 ≤90s/≤3min 检测预算，须在统一目标下明确差距与测量，不能当作已全场景达标。 |
| 11 | NEXT_PHASE_DEVELOPMENT_DESIGN.md | **改部分落地**：checkers 拆分、多层写入、指标配置化有落点；P2 Redis/Mongo 存根是整稿“已落地”的直接反例。 |
| 12 | PROJECT_IMPROVEMENT_DESIGN.md | validate.sh、appconf/self_monitor、依赖/密钥脚本和原 CI 施工可指认；main/master 触发差异须作为持续有效性限制，本轮未核实远端 CI 绿灯。 |
| 13 | PROJECT_ANALYSIS_AND_NEXT_PHASE.md | 现状分析已过时可保留：其单体/旧阶段假设与已拆分 checkers、Phase 5/6 不符，不能作当前缺陷清单。 |
| 14 | METRICS_DESIGN.md | 部分落地可保留：checkers 的缓冲池/集群等实现超出旧“当前状态”表；完整指标清单不能靠返回键数验收，语义、单位、缺值仍需方言证明。 |
| 15 | DB_SCHEMA_4NF_DESIGN.md | 限定其 12 组列表字段改造可认可：models 子表、0018 迁移回填及后续 0023 在；不外推 Timescale/ES 或全部生产数据已验收。 |
| 16 | ORACLE_RAC_ADG_DESIGN.md | 部分落地可保留：oracle.py 有 RAC/ADG 核心字段，未找到该稿全部 gc_/ic_/归档缺口及 MRP/RFS 组的等价完整实现；仍需真库逐项验证。 |
| 17 | TDSQL_SLOWSQL_DESIGN.md | 部分落地可保留：sqlstat_collect/sql_profile/sql_explain/sql_advisor 等主干与测试在；V-1..10 及 A/B 清单需内网证据。§11 明确不做 proxy 层耗时分解，验收不得越界承诺。 |
| 18 | HARNESS_IMPROVEMENT_DESIGN.md | AGENTS/validate/hooks 施工可指认；“存在规则”与“远端 main 有门禁”需分开，AGENTS 的 master 与黑板 main 还存在文字冲突。 |
| 19 | BATCH_ALERT_DELETE_DESIGN.md | 实现判定可认可：AlertBatchDeleteView、路由、AlertList 勾选与 batchDelete 客户端对应；本轮未专门做浏览器交互验收。 |
| 20 | BUGFIX_DESIGN.md | 已列修复主干、tests_bugfix/tests_concurrency 可指认；文档自留 BUG-132 APIKey 缓存持久化缺口，不能把“已落地”理解为所有风险关闭。 |
| 21 | SCOPE_HARDENING_CHANGELOG.md | 记载的既有视图修复、lint/CI diff 施工可指认；仍被 R1 证明未覆盖全部 Phase 5 API，须补测试与范围清单。 |
| 22 | V2.5_REVIEW_REMEDIATION_DESIGN.md | LLM 凭据迁移、出站策略、旧视图范围及测试可指认；仅认可其列明整改，不作全平台越权消除背书。 |
| 23 | plans/database-list-ux-design.md | 六项主干静态可认可：DatabaseList 缓存 TTL、Promise.all、health/alert、排序与统计存在；不据此认定虚拟滚动等整稿可选项或真实大规模交互均验收。 |
| 24 | plans/frontend-ux-optimization-design.md | 部分落地可保留：多模板/性能页/曲线和 SQL 页面存在；草稿的字段、接口、日期不宜直接作当前施工规范。 |
| 25 | plans/bank_simulator/DESIGN.md | simulator/worker/schema、命令与脚本可指认，限实现层认可；本轮不向真实库加载数据，不能把脚本存在当作跨六库运行已通过。 |

25 份文件均已读取范围/章节并核对上述落点。至少 #4、#11 的范围必须重判；#5、#8 的证据说明须更正。其余行中“限定”不能被删掉后再汇总成无条件整稿完成。

## 四、方向排序、首攻与任务拆分

### 方向与首攻

- **A 优先、第一切片先做 TDSQL 内网验证：认可。** `TDSQL_SLOWSQL_REVIEW.md` 开头修复状态明确：通用 MySQL/PG/模拟 proxy 证据不能替代真实 proxy+set。V-1..9 探针、V-10 端到端及 A-1..5/B 类检查已有材料，先验证再修实际缺陷比另起大重构合理。
- **A > B > C > D > E 可作为产品投资方向的建议，不是技术执行的严格全序。** C 中影响数据正确性/覆盖的部分应成为 A 的前置或并行工作；真实数据与安全问题未过关时，不能因 B 排第二就优先上生产自治。D/E 的先后缺用户价值/成本定量比较，不应阻碍安全提示等小范围 UX 修复。
- 方向 A 不能统称“阻断完全在环境”：R1–R3 都是仓库内即可复现、必须先闭合的代码/契约缺口。安全基线和 main 验证闭环应列生产就绪前置。
- **遵守 DEC-005 的统一 1-5-10 目标**，不恢复旧目标；同时明确目标、各类场景预算和实测结果三层，当前并无“全平台已达标”证据。

### DB-004~DB-010（仅评审建议，不自行派单或占号）

| 建议单 | 判断与需补齐的验收边界 |
|---|---|
| DB-004 | 可先开。环境/版本/proxy+set 拓扑、权限、隔离测试库、只读保护与超时、凭据安全通道齐全。V-7 FAIL 禁开 EXPLAIN ANALYZE；WARN 不算只读保护 PASS。 |
| DB-005 | **依赖 004 就绪后执行**。保留 V-10、A-1..5、B 类语义，不把清单缩成 V-1..9。记录每 set 的真值、单位/窗口/退化标记、审计；负载/索引操作只进独立测试库。 |
| DB-006 | 可预留修复容量，不能无缺陷输入即承诺修哪些问题；由 005 的可复现缺陷驱动，修复后 A 复核并回到 005 复跑。交付改“main 修复提交 SHA + validate + 复测证据”，**不是 PR**。 |
| DB-007 | 可先设计压测，但容量结论依赖有效采集与数据正确性。补工作负载/拓扑/采样周期、平台与目标库资源/连接压力、P95 延迟/丢失率、长稳时长及失败判据；500 不得只按配置行数算。 |
| DB-008 | 位于 007 结果及备份/恢复准备之后，区分 collector 租约切换、PG/TSDB/Redis 故障与目标库 HA；要求无重复执行、无丢证据、恢复后对账；五方签字不替代技术验证。 |
| DB-009 | 环境/适配可并行。DM 是授权驱动/环境/方言验证；GBase 应从已有真实连通记录出发补当前版本用例和 ASH 回归，不一概按“无实库”重新开荒。 |
| DB-010 | **应优先并行启动，但须重估并拆小**：先安全闸与入口契约/覆盖呈现，再完成 82 项映射清单，后续实现按能力/方言拆分。保留 skip 的真实语义，缺实现和不适用不得伪装成功；按适用项、已执行项和失败项分别验收。 |

“先开 004/005/006+010”可解释为先建工作包，但**不宜理解为四张单无依赖同时执行**。建议就绪顺序为 004→005→按需006→005复测，010 安全/契约与该主线并行；007/008 放在可用性证据之后。建议单号最终由 PM 决定。

## 五、方法、证据与复审门槛

### 已完成的独立检查

- 对两基线和接单提交做 Git 对象/路径比较；逐项复核 25 稿；检查 README、方言矩阵、TDSQL 设计与验证指南。
- [reproduce.py](evidence/DB-002-O/reproduce.py)：原始执行器调度 + 明确 spy 检测器；原始 API/middleware/auth/ORM + 隔离 SQLite 合成数据。**未改断言、未改产品；未触及真实目标库、未执行生产 SQL。** API 首轮独立 harness 曾因 testserver Host 校验失败，改用允许的 localhost 后重跑，最终结果见 [probe-results.json](evidence/DB-002-O/probe-results.json)。
- 现有 feature_switches / tdsql_sqlstat / sql_advice_explain / v3_sit / v3_uat 合计 **114 项通过、无跳过**，仅支持所测分支；并不覆盖本轮新发现的巡检缺陷。仓库 `scripts/validate.sh unit --json` 通过。
- 未重跑六方言真库、浏览器、500 实例、HA/长稳、通知或生产恢复，不背书这些能力。D-team 三份本机 notes 不在本仓，本次以可复现的仓库事实独立复核，不假称审阅了原始本机笔记。

复现命令（仓库根目录）：

```bash
.venv/bin/python docs/pm/reports/evidence/DB-002-O/reproduce.py --output /tmp/db002-probe.json
DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_unit .venv/bin/python manage.py test \
  monitor.tests_feature_switches monitor.tests_tdsql_sqlstat monitor.tests_sql_advice_explain \
  monitor.tests_v3_sit monitor.tests_v3_uat --noinput -v 1
bash scripts/validate.sh unit --json
```

证据另见 [选测摘要](evidence/DB-002-O/selected-tests-summary.txt) 与 [基线对照](evidence/DB-002-O/baseline-comparison.json)。脚本断言用于锁定反例，不是通过标准；产品后续修复后这些反例应不再复现，需由新回归证明正确拒绝与成功路径。

### 返工后复审所需

1. DSMan/GG 修订研判：披露 R1/R2，撤回 R3 的低成本/79 项已有能力承诺，补 82 项映射/缺口分层，修正 R4 及 25 稿汇总；统一源码与报告取证基线。
2. 首批任务草案补安全前置、004→005→006→005 复验依赖、010 分片验收与合理工期；代码修复可由正式后续单执行，**不要求设计团队在 DB-001 研判单里直接改产品代码**。
3. 将 1-5-10 的目标与实际预算/未测证据区分，保留真库矩阵的分层历史证据；方向建议再由 PM/用户裁定。

以上是明确返工点，不以“后续注意”作为无条件放行。**当前结论：不通过。**
