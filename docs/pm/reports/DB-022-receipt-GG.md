# DB-022 接单回执

- **任务单**：`docs/pm/orders/DB-022-010B补实现-上半.md`
- **执行人**：GG
- **接单时间**：2026-11-11 15:15
- **预计交付时间（ETA）**：2026-11-11 18:30（今日交付）
- **工作范围**：
  1. 依据 `docs/pm/reports/DB-010B-71项全表.md` 与 `DB-019` 评估报告结论，为上半 23 项缺实现巡检项逐项新写检测器（含 1 项大项 `INS-COM-DEADLOCK-24H`、11 项中项、11 项小项）；
  2. 新检测器逐项登记入 `DETECTOR_REGISTRY` 注册表，同步更新映射矩阵（`inspection-82-mapping.json` 与 `.md`）；
  3. 严格遵循 DB-010A 双闸（权限校验 + visible_to 范围过滤），方言不适用时优雅 skip 并附带明确原因；
  4. 编写全量覆盖的单元测试，执行 `scripts/validate.sh unit --json` 与 `backend` 门禁验证，输出标准交付报告并直推 `main`。
