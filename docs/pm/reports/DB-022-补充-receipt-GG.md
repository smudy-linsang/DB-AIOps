# DB-022 补充：实库补证 接单回执

- **任务单**：`docs/pm/orders/DB-022-补充-实库补证.md` (v1.0)
- **执行人**：GG
- **接单时间**：2026-11-13 10:28
- **预计交付时间（ETA）**：2026-11-13 11:15（今日交付）
- **工作范围**：
  1. 梳理 `docs/pm/reports/DB-022-report-GG.md` §二 23 项检测器实现，确认各分支（DM 15 项、GBase 10 项、MySQL 12 项、PG 12 项）实际生成的 SQL 语句与 `skip` 逻辑；
  2. 复用 DB-009 / DB-005R 已验证通道连接外网可测 4 个真实库（宿主 DM8 服务、GBase 8a、MySQL 8.0.45 cid=19、PostgreSQL 16.15 `db_monitor`），逐项执行并留存输出原文与执行判定；
  3. 对方言不适用项实测验证优雅 `skip`；Oracle 分支 23 项全部标注"未测·延后（外网无真实库，延至上线后内网复测）"；
  4. 严格不修改检测器源码与注册表，输出 `docs/pm/reports/DB-022-补充-实库补证-report-GG.md` 并跑通 `scripts/validate.sh unit --json` 后直推 `main`。
