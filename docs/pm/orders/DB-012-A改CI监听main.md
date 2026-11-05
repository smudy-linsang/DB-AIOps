# DB-012：A 改 ci.yml 监听 main（DEC-006 执行单）

- 任务单：`docs/pm/orders/DB-012-A改CI监听main.md`
- 下达：2026-11-05
- 依据：DEC-006（默认分支改 main，master 冻结）

执行：A

## 任务

把 `.github/workflows/ci.yml` 的触发分支由 `[master]` 改为 `[main]`（master 已冻结，不再列入监听）。改动后直推 main 的提交触发 CI，README“推完看 CI”在 main 上生效。

## 说明

- 默认分支切换由用户在 GitHub 仓库设置里操作，与本单并行、不分先后；两步都到位后，CI 与 A-AUTORUN 兜底（schedule/手动触发）才全部生效。
- 本单只改 ci.yml 触发分支，不改 job 内容、不动 master 分支、不改默认分支（无权限也不操作）。
- 改动属产品工程配置，由部门经理在 DEC-006 中明确指派（超出 A 自动会话权限边界，故单独立单）。

## 交付与验收

改动直推 main，附本地 `scripts/validate.sh` 证据；本单推送本身即触发验证（若 CI 未触发，A 须在报告中说明原因）。

## 接单规矩

接单后先在 `reports/` 下回接单确认与预计交付时间（简短回执即可）；本单不设硬性截止。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-05 | muse | DB-012 下达 |
