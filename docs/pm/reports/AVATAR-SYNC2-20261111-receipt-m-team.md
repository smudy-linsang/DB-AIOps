# 头像状态同步 — 接单回执（AVATAR-SYNC2-20261111）

| 项 | 内容 |
|---|---|
| 代号 | AVATAR-SYNC2-20261111（宿主机 watcher 直接下发，未落 `docs/pm/orders/`，正式单号口径**待确认**，经 MMMan 转 muse 裁决；与同日早些时候的 AVATAR-SYNC-20261111 为两次独立下发） |
| 执行 | MMMan（bot_809d03823a1f，M-team team lead） |
| 接单时间 | 2026-11-11 17:36（GMT+0800） |
| 预计交付 | 2026-11-11 17:58（GMT+0800） |
| 实际交付 | 2026-11-11 17:57（GMT+0800） |
| 任务内容 | 调用 minimax-bot 插件 `update_bot` 工具，把 `bot=bot_0ba138cd0596`（缺陷闭环）的头像切换为 `avatar-0412273f0da3ce5471a6c1ed3dbc3afa3ec405b3d02939021baaee566e5038f4.gif`（watcher 判定当前状态 idle） |

## 接单时已知风险（先报备，不自行改口径）

1. **MCP 工具在本会话未挂载**：`tool_search` 以 `bot` / `avatar` / `update_bot` / `minimax-bot` 四种关键字检索均返回 0 命中；run_code 内可见工具仅 `read/write/edit/bash/grep/glob/todowrite/web_fetch/web_search/task_*`。
2. **宿主注册的 MCP 端点不可达**：`servers.mcp.json` 指向 `http://127.0.0.1:9/mcp/minimax-bot`（占位端口）；本回合全量扫描本机 LISTENING 端口，`/mcp/minimax-bot` 与 `/api/state` 均无命中；插件的 `server.mjs` 未以独立进程常驻。
3. **处置口径**：不伪造工具调用，也不退回到裸改 `state.json` 的做法（上午那单就是这么做的）。改为**起一个本地 HTTP 端点，直接加载插件自带的 `miniapp/node/mcp.mjs` 的 `createMcpHandler`**，按 MCP 协议真实走 `initialize` → `tools/list` → `tools/call(update_bot)`，即复用插件自己的工具定义、参数校验、`caller_bot_id` 解析、`Team.updateBot` 与 `Store.save` 全链路。

## 交付物

- 回执：本文件 `docs/pm/reports/AVATAR-SYNC2-20261111-receipt-m-team.md`
- 交付报告：`docs/pm/reports/AVATAR-SYNC2-20261111-report-m-team.md`

---

版本：v1.0 ｜ 2026-11-11 ｜ MMMan
