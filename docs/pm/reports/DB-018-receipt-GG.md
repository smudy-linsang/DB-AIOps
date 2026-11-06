# DB-018 接单回执

- **任务单**：`docs/pm/orders/DB-018-DM检测器静默不注册修复.md`
- **接单人**：GG
- **接单时间**：2026-11-06 10:25
- **预计交付时间（ETA）**：2026-11-06 11:30
- **工作范围**：
  1. 依赖声明订正：`requirements.txt` 分层写清连接层依赖（`pyodbc`）与 DM 检测器依赖（`dmPython`，需官方授权安装）。
  2. 缺驱动可见性：纳管 DM 实例缺 `dmPython` 时，注册/启动时记录 warning 日志告警，且在巡检结论中明确注明“DM 检测器未注册：缺 dmPython”，杜绝静默跳过。
  3. 执行本地完整验证门禁 `scripts/validate.sh unit --json` 与 `backend`，完成交付报告推送到 `origin/main`。
