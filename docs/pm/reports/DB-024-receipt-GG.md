# DB-024：修复 DB-022 检测器实库缺陷 接单回执

- **任务单**：`docs/pm/orders/DB-024-修复-DB-022-检测器实库缺陷.md` (v1.0)
- **执行人**：GG
- **接单时间**：2026-11-13 11:44
- **预计交付时间（ETA）**：2026-11-13 13:30（今日交付）
- **工作范围**：
  1. 修复根因 A：`DbConnector` 的 `DictCursor` 与 `GenericDetector` 元组下标 / `zip` 不兼容问题，并核查全部受影响调用方，将改动面写入报告；
  2. 修复根因 B：清理 `GenericDetector` 内层裸 `except` 静默吞错（含 `detect_open_cursors` 硬编码假值），使真实 SQL/游标异常显式落入 `error` 或 `degrade`；
  3. 逐项修复 DM8（9 项）、GBase 8a（6 项 + 1 变量空行）、MySQL 8.0（2 项 + 1 状态变量空行）方言 SQL 失配及 `CAP-7D`/`CAP-30D` `status='UP'` 过滤与容量外推/区分口径；
  4. 补充对应单测并在同一外网 4 个真实库（宿主 DM8 cid=9、GBase 8a 127.0.0.1:5258、MySQL 8.0.45 13306 cid=19、PostgreSQL 16.13 db_monitor+cid=2）逐项复测留存原文（Oracle 仍延后）；
  5. 跑通 `scripts/validate.sh` 全量校验，输出 `docs/pm/reports/DB-024-report-GG.md` 并直推 `main`。
