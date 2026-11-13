# DB-025：修复 DB-023 GBase 视图口径

执行：GorkMan

## 背景

- DB-023-补充实库补证报告已验收通过（PM 采信，STATUS v1.45）：DM 4 项实测有效（#65/#66/#68 skip 均以实测状态为准，#67 critical 为测试实例归档未开启的真实发现、非检测器缺陷），DM 条件关闭；GBase 3 项（#69–#71）error——`gcluster_v$node_info` 与 `gcluster_v$table_distribution` 在实库 5 个库均不存在，近似名 `gbase.table_distribution` 是另一张基表（7 列：index_name、dbName、tbName、isReplicate、hash_column、lmt_storage_size、table_storage_size），列不同、不得冒充；GBase 条件不关闭，转入本单修复；Oracle 16 项与 GTID 仍延后。
- 视图缺失明细见补证报告 `docs/pm/reports/DB-023-补充-实库补证-report-gorkman.md`「GBase：1046 与视图诊断」一节（逐库 USE 后 1109/1146；官方通道先报 1046 未选库）。

## 任务

1. **查明 GBase 8a 真实数据来源**：在 GBase 8a（8.6.2.43-R7-free，127.0.0.1:5258）上查明节点负载均衡（#69）、数据分布均衡（#70）、节点宕机（#71）三项检测的真实数据来源（集群视图、系统表或管理接口），修正三项 SQL；经核实确无数据源的，改口径为 `skip` 并附核实依据，不得把空结果或 error 写成均衡/无宕机。
2. **排查同类隐患**：排查 `Batch010bDetector` 是否存在与 DB-022 报告根因 A/B 同类问题（DictCursor/元组取值冲突、裸 `except` 吞错致假绿），有则一并修复并写进报告。
3. **复测**：修复后在同一 GBase 8a 实库（127.0.0.1:5258，cid=11）逐项重跑 #69–#71，逐项留实测输出原文，须得出真实判定（命中/未命中/skip）；确无数据源且已改 `skip` 的，复测须验证 `skip` 判定与依据一致。

## 验收物

- 修复报告 `docs/pm/reports/DB-025-report-gorkman.md`（第一行结论三档之一：通过/有条件通过/不通过），逐项写清修复方法与复测结果，附实库连接信息。
- 补证到后 PM 核对：GBase 条件闭环后 DB-023 外网条件全部关闭（Oracle 与 GTID 仍为剩余条件，延至上线后）。

## 约束

- 不改 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`（仅部门经理维护）。
- 只做本单所列视图口径修复与同类隐患排查；实现有其他问题另立单，不在本单顺手扩围。
- 直推 main；每次代码编辑后跑 `scripts/validate.sh unit`，提交前跑 backend/frontend/all，推后看 CI，红了立刻修或回滚。
- 全部测试在外网；内网不动、凭据不供给；不索取新凭据，走 DB-009 已验证的通道与凭据口径。
- 证据只认目标机实测输出原文；推断、同构、代码审查、预期输出不算实测；空结果不得写成正常。
- 接单后先在黑板上回接单确认与 ETA（reports/ 下简短回执），飞书群只作广播。
- 注：DB-024（GG）若先改了连接器游标，本单复测以最新连接器状态为准；视图修正本身不依赖游标改动，两单可并行。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-13 | muse | DB-025 下达：修复 DB-023 GBase 视图口径，派 GorkMan |
