# DB-023 接单回执

本文件是接单确认与 ETA，不是交付报告，也不是终验结论。34 项检测器尚未实现，不得把本回执当成已完工。

- **任务单**：`docs/pm/orders/DB-023-010B补实现-下半.md`（v1.0）
- **执行人**：GorkMan
- **接单时间**：2026-11-10 23:17（America/Los_Angeles，PST，UTC-8）
- **接单时 main**：`9cf7beaa7712403961d5d522ff253d299aa80248`
- **预计交付时间（ETA）**：2026-11-11 06:00（America/Los_Angeles）
- **命名**：任务单只要求 `reports/` 下简短回执，未规定文件名。本文件沿用 GorkMan 既有回执名 `DB-023-ack-gorkman.md`。

## 开工前核对

1. 已 `git fetch origin main`。接单时本地 `main` 与 `origin/main` 同为 `9cf7beaa7712403961d5d522ff253d299aa80248`。
2. 仓库内无 `DB-023-ack*`、`DB-023-receipt*`、`DB-023-report*`，也无下半 34 项检测器实现。本次不覆盖他人成果。
3. `docs/pm/STATUS.md` v1.33 仍写 DB-022 / DB-023 截至 2026-11-10 16:15 无回执。DB-022（GG，上半 23 项）此刻也尚未落库；实现时只追加本单 34 个注册键，不改已有键，不挂 `INS-ORA-OPT-STATS-GATHER` 与 `INS-MYSQL-BINLOG-DELAY`。

## 工作范围

1. 为任务单列出的 34 项逐项新写检测器，并登记进 `DETECTOR_REGISTRY`。定义与对象以 DB-020 报告的代码指认为准。
2. 方言不适用时在连接目标库之前返回 `skip` 并写明原因。Oracle 非 RAC / 非 Data Guard / 非 Exadata、达梦非 DSC / 非 DataWatch、PostgreSQL 主库无 WAL receiver 等形态差异，单独 skip，不把「查不到」写成故障或健康。
3. 不绕过 010A 双闸，不新开执行入口。不改可复用 14 项与直接命中 11 项的已有实现。
4. 每项用单元测试实测命中、未命中、方言 skip、查询异常。外网能连上的库另作实测；连不上的在交付报告里写明局限。

## 约束复述

- 不改 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`。
- 直推 main，不开 PR。推送前 `pull --rebase`。不 force push，不 `git add .`。
- 代码编辑后跑 `scripts/validate.sh unit`，提交前跑 `scripts/validate.sh all`，推送后看 CI。
- 全部测试在外网。内网不动，不索取凭据，不重启采集器。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-10 | GorkMan | 接单回执。时间为 America/Los_Angeles 23:17，ETA 11-11 06:00。非正式报告 |
