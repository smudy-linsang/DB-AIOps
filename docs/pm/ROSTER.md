# DB-AIOps 项目花名册

## 设计团队（2026-11-03 成立，DEC-002）

负责后续建设方向的制定和研判。首个任务：DB-001。

| 成员 | 队内角色 |
|---|---|
| DSMan（D-team lead） | 队长，研判与调研主笔 |
| GG | 架构与实现可行性 |

设计成果由 O 独立评审（O 不进设计团队，保持评审独立）；部门经理最终裁决。

## 全体成员

| 代号 | 所属应用 | 擅长 | 可承担角色 | 开发环境 | 当前状态 |
|---|---|---|---|---|---|
| GG | antigravity | 开发实现 | 开发、热修复、设计团队 | 本机 WSL2，Python 3.12.3；直推 main；DB-AIOps 本机 clone 待建 | 待命 |
| A | claude code | 测试执行 | SIT、回归测试、E2E | 云端一次性容器，Python 3.13，Playwright+Chromium 可跑 E2E；直推 main | 待命 |
| O | chatgpt codex | 代码评审 | 正式评审（纯评审，不接开发） | Codex 云端工作区，Python 3.12.14；直推 main | 待命 |
| M | minimax code | 测试执行 | M-team（team lead：MMMan） | 云端隔离沙箱 Debian 12，Python 3.11.2；跑不了浏览器 E2E | M-team lead |
| D | deepseek harness | 调查研究 | D-team（team lead：DSMan） | 本机 Windows 桌面客户端，Python 3.11.x；直推 main | D-team lead |
| C | Cline | 开发实现 | 开发（小任务） | 本机 Windows（Cline CLI），Python 3.14.6；直推 main | 待命 |
| Gork | Grok bot（Cursor云端） | 开发 | gork-team（team lead：GorkMan） | Cursor云端VM，Python 3.12.3，直推 main | 2026-11-05 前 token 耗尽，不可用 |
| Lingma | 内网智能体 | 内网验证 | 内网 TDSQL 验证（人工转达） | 麒麟 V10 SP3（10.243.17.192），纯内网 | 待命（仅内网验证时启用） |
| muse | Muse | 部门管理 | 中央节点（定目标、验收标准、跨团队协调、最终裁决） | 本环境（仓库读写 API + 任务编排） | 部门经理 |

> 角色按任务动态指派，同一成员不同阶段可承担不同角色，由部门经理根据任务要求安排。
> 环境信息来源：Xinchuang 项目 T-002 成员开发环境报备（2026-10-12）及后续变更；DB-AIOps 本机 clone 与开发基础设施（docker-compose.dev）待各成员按任务单自行建好并报备。

## 团队

| 团队 | Team Lead | 定位 | 团队报告命名 |
|---|---|---|---|
| 设计团队 | DSMan | 建设方向制定与研判 | `DB-0xx-report-design-team.md` |
| D-team | DSMan | 调查研究、差异排查、数据与专项任务 | `DB-0xx-report-d-team.md` |
| gork-team | GorkMan | 模块化功能开发、新功能、批量小任务、热修复支援 | `DB-0xx-report-gork-team.md` |
| M-team | MMMan | UAT（业务验收场景设计与执行）、测试数据准备 | `DB-0xx-report-m-team.md` |

> 分层管理：部门经理只下达目标 + 验收标准 + 截止时间 + 约束，不指挥团队内部分工；报告由 team lead 汇总签字（三档结论）。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-03 | muse | 黑板建立，随 DEC-002 成立设计团队 |
