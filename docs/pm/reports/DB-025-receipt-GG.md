# DB-025：修复 DB-023 GBase 视图口径（改派 GG）接单回执

- **任务单**：`docs/pm/orders/DB-025-改派-GG.md` (v1.0)
- **执行人**：GG
- **接单时间**：2026-11-19 10:28 (+08:00)
- **预计交付时间（ETA）**：2026-11-19 11:30 (+08:00)
- **工作计划**：
  1. 在外网实库 GBase 8a（`DatabaseConfig cid=11`）全面盘点各系统库视图/基表与 `SHOW` 状态变量，查明 `#69`（`INS-GBASE-BALANCE`）、`#70`（`INS-GBASE-DIST`）、`#71`（`INS-GBASE-NODE-DOWN`）真实数据源；若确无集群节点/分布数据源，按单将三项调整为基于实测依据的 `skip` 口径，杜绝把空结果或 error 写成正常；
  2. 全量排查 `Batch010bDetector`（`monitor/inspection_detectors_010b.py`）是否存在 `DictCursor`/元组取值冲突或裸 `except` 吞错致假绿隐患，有则一并修复并写入报告；
  3. 修复后在同一 GBase 8a 实库（`cid=11`）逐项复测 `#69–#71` 留存原文，补齐对应单测，跑通校验门禁后提交 `docs/pm/reports/DB-025-report-GG.md` 并直推 `main`。
