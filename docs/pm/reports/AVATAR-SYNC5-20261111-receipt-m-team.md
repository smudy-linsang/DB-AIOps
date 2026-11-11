# 头像状态同步 — 接单回执（AVATAR-SYNC5-20261111）

| 项 | 内容 |
|---|---|
| 代号 | AVATAR-SYNC5-20261111（宿主机 watcher 直接下发，未落 `docs/pm/orders/`，正式单号**待确认**，经 MMMan 转 muse 裁决；与同日 AVATAR-SYNC / SYNC2 / SYNC3 / SYNC4 为第五次独立下发） |
| 执行 | MMMan（bot_809d03823a1f，M-team team lead，isMain=true 主 Bot） |
| 接单时间 | 2026-11-11 18:44（GMT+0800） |
| 预计交付 | 2026-11-11 19:12（GMT+0800） |
| 任务内容 | 调用 minimax-bot 插件 `update_bot` 工具，把 `bot=bot_27cd491314c1`（测试数据）的头像切换为 `avatar-c1143b26cd078cc8564ab1ab50e910f13d01370a78ecb9373901a331fdf9aa46.gif`（watcher 判定当前状态 work） |

## 接单时已知风险（先报备，不自行改口径）

1. **本会话未挂载 minimax-bot 的 MCP 工具**：`tool_search` 以 `update_bot avatar bot` / `minimax-bot avatar update` / `minimax-bot update_bot 团队 机器人 更新` / `minimax bot avatar` 四种口径检索均 0 命中（total=0）。本会话为 `mcode exec` 无头会话（node PID 48852），插件 MCP 工具不在其内。
2. **上一单（SYNC4）的切换已被宿主回滚，本单属真实切换**：18:43 复核 `state.json` 实测 `bots[bot_27cd491314c1].avatar` 已回到 `avatar-ee923cb4…71e9.gif`、`rev` 回到 22、`rosterRev` 回到 133，`state.json` mtime 为 2026-11-11 18:42:17 —— SYNC4 于 18:35:27 写入的目标值 `avatar-c1143b26…` **已被宿主内存态覆盖还原**。SYNC4 报告 G-3 预判的风险**已成真**，且证明本单仍是「真实切换」而非状态重申。
3. **宿主权威态只在进程内存**（已核实）：`~/.minimax/v2/sqlite/runtime-state.sqlite` 的 `local_runtime_liveboard_state` 仅存 `plugin_id/accepted_source_digest/client_digest/node_digest/preferred_port(58925)/last_error_json`，**不含 roster**；roster 权威副本在宿主进程内存，磁盘 `state.json` 是被周期性整体回写的镜像。
4. **处置口径**：不伪造工具调用、不裸改 `state.json`、不重启宿主桌面端（会连带中断本会话与其它在跑任务，未获授权）。优先走宿主进程内 MCP 通道（PID 42936 = `host-runner.js` 插件运行时，监听 58925）；若确实不可得，沿用同日 SYNC3/SYNC4 已实证路径（加载插件 `miniapp/node/mcp.mjs` 的 `createMcpHandler`，按 MCP 协议真实 `initialize → tools/list → tools/call(update_bot)`），并在报告中如实标注通道差异与回写风险。

## 交付物

- 回执：本文件 `docs/pm/reports/AVATAR-SYNC5-20261111-receipt-m-team.md`
- 交付报告：`docs/pm/reports/AVATAR-SYNC5-20261111-report-m-team.md`

---

版本：v1.0 ｜ 2026-11-11 ｜ MMMan
