# 头像状态同步 — 接单回执（AVATAR-SYNC6-20261111）

| 项 | 内容 |
|---|---|
| 代号 | AVATAR-SYNC6-20261111（宿主机 watcher 直接下发，未落 `docs/pm/orders/`，正式单号**待确认**，经 MMMan 转 muse 裁决；与同日 AVATAR-SYNC / SYNC2 / SYNC3 / SYNC4 / SYNC5 为第六次独立下发，且**首次指向不同 Bot**） |
| 执行 | MMMan（bot_809d03823a1f，M-team team lead，`isMain=true` 主 Bot） |
| 接单时间 | 2026-11-11 19:02（GMT+0800） |
| 预计交付 | 2026-11-11 19:30（GMT+0800） |
| 任务内容 | 调用 minimax-bot 插件 `update_bot` 工具，把 `bot=bot_0ba138cd0596`（缺陷闭环）的头像切换为 `avatar-cbab946b1ea239c9d59d76a56d1a0c8d17cb026b196934f5383b399e111c2bb4.gif`（watcher 判定当前状态 work） |

## 接单时已核实的事实（均为实测，不含推断）

1. **本单是真实切换，不是状态重申**：19:03 读 `state.json` 实测 `bot_0ba138cd0596`（缺陷闭环）当前头像为
   `avatar-0412273f0da3ce5471a6c1ed3dbc3afa3ec405b3d02939021baaee566e5038f4.gif`、`rev=19`、`updatedAt=1794036659995`，
   与目标值 `avatar-cbab946b1ea239c9d59d76a56d1a0c8d17cb026b196934f5383b399e111c2bb4.gif` **不同**。
   目标头像文件在 `avatars/` 目录中**实际存在**（38 个头像文件含该文件），图库校验不会拦。
2. **本单目标 Bot 与前五单不同**：SYNC1–SYNC5 全部指向 `bot_27cd491314c1`（测试数据）；本单指向 `bot_0ba138cd0596`（缺陷闭环），
   即前五单的「回写抹平」结论**不能直接套用于本单**，需本单独立实测判定。
3. **本会话未挂载 minimax-bot 的 MCP 工具**：`tool_search` 以 `update_bot avatar bot` / `minimax-bot update_bot` /
   `bot avatar update status` / `update_bot` 四种口径检索均 0 命中（total=0）；`mcode-tools connector tools --keyword bot`
   返回的目录中亦无 `update_bot`（Connector 目录与 Plugin MCP 目录是两套）。
   插件侧注册完整：`~/.minimax/mcp-runtime-names.json` 中 `minimax-bot` 段含 `update_bot` 等 21 个工具。
4. **上一单（SYNC5）已实证**：SYNC4 的成功写入在约 6 分 50 秒后被宿主内存态回写还原（`rev`/`rosterRev`/`updatedAt` 一并被抹平），
   故本单必须**实测写入后持续采样**，验证持久性，不以单次落盘值当成功。

## 接单时已知风险（先报备，不自行改口径）

1. **通道可得性**：任务单指定的「调用 update_bot 工具」在宿主进程内 MCP 通道上，该通道需内存态随机票据
   （`x-mavis-miniapp-token`，`randomBytes(32)` 签发、仅存宿主内存）。本会话为 headless `mcode exec` 会话，工具未挂载。
2. **持久性**：进程外写入可能被宿主内存态周期回写抹平（SYNC5 实证）。本单需实测区分「写入成功」与「持久生效」。
3. **处置口径**：不伪造工具调用、不伪造回执、不裸改 `state.json`、不重启宿主桌面端（未获授权，会连带中断本会话与在跑任务）。
   优先走宿主进程内 MCP 通道；若不可得，沿用 SYNC4 已实证路径（加载插件 `miniapp/node/mcp.mjs` 的 `createMcpHandler`，
   按 MCP 协议真实 `initialize → tools/list → tools/call(update_bot)`），并在本报告中如实标注通道差异与回写风险。

## 交付物

- 回执：本文件 `docs/pm/reports/AVATAR-SYNC6-20261111-receipt-m-team.md`
- 交付报告：`docs/pm/reports/AVATAR-SYNC6-20261111-report-m-team.md`

---

版本：v1.0 ｜ 2026-11-11 ｜ MMMan
