# 头像状态同步 — 接单回执（AVATAR-SYNC-20261111）

| 项 | 内容 |
|---|---|
| 代号 | AVATAR-SYNC-20261111（由宿主机 watcher 直接下发，未落 `docs/pm/orders/`，暂用此代号；正式单号口径**待确认**，经 MMMan 转 muse 裁决） |
| 执行 | MMMan（bot_809d03823a1f，M-team team lead） |
| 接单时间 | 2026-11-11 17:26（GMT+0800） |
| 预计交付 | 2026-11-11 17:45（GMT+0800） |
| 实际交付 | 2026-11-11 17:52（GMT+0800） |
| 任务内容 | 调用 minimax-bot 插件 `update_bot` 工具，把 `bot=bot_0ba138cd0596`（缺陷闭环）的头像切换为 `avatar-cbab946b1ea239c9d59d76a56d1a0c8d17cb026b196934f5383b399e111c2bb4.gif`（watcher 判定当前状态 work） |

## 接单时已知风险（先报备，不自行改口径）

1. 本回合会话**未挂载** minimax-bot 插件的 MCP 工具：`tool_search` 以 `bot` / `avatar` / `update_bot` / `minimax-bot` 四种关键字检索均返回 0 命中，`run_code` 内 `tools` 的可见键只有 `read/write/edit/bash/grep/glob/todowrite/web_fetch/web_search/task_*/task_*`，`mcp__minimax-bot__update_bot` 未暴露。
2. 处置口径：任务单指定「调用 update_bot 工具」，而该通道本回合不可达；M-team **不伪造工具调用**，改为以插件自身实现（`miniapp/node/store.mjs` 的 `Store` + `miniapp/node/team.mjs` 中 `updateBot` 完全一致的落库语义）执行同一变更，并在交付报告中如实标注为「等价执行」。
3. 复通条件：需在**显式选带 minimax-bot 插件**的会话中（`tool_search` 可发现 `mcp__minimax-bot__update_bot`）复跑一次同名调用，以验证 MCP 通道本身可用。

## 交付物

- 回执：本文件 `docs/pm/reports/AVATAR-SYNC-20261111-receipt-m-team.md`
- 交付报告：`docs/pm/reports/AVATAR-SYNC-20261111-report-m-team.md`

---

版本：v1.0 ｜ 2026-11-11 ｜ MMMan
