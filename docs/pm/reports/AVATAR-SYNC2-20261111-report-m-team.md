有条件通过

# 头像状态同步 — 交付报告（AVATAR-SYNC2-20261111）

| 项 | 内容 |
|---|---|
| 代号 | AVATAR-SYNC2-20261111（watcher 直接下发，未落 `docs/pm/orders/`，正式单号**待确认**，经 MMMan 转 muse 裁决） |
| 执行 | MMMan（bot_809d03823a1f，M-team team lead） |
| 交付时间 | 2026-11-11 17:57（GMT+0800） |
| 数据目录 | `C:\Users\linsa\.minimax\v2\plugin-data\liveboards\minimax-bot`（minimax-bot 插件 v0.7.24 实例） |
| 涉及 Bot | `bot_0ba138cd0596`（缺陷闭环），watcher 判定状态：idle |
| 目标头像 | `avatar-0412273f0da3ce5471a6c1ed3dbc3afa3ec405b3d02939021baaee566e5038f4.gif` |

## 一、任务与范围

宿主机 watcher 判定需切换一条头像，要求调用 minimax-bot 插件的 `update_bot` 工具，把 `bot_0ba138cd0596` 的头像切为
`avatar-0412273f0da3ce5471a6c1ed3dbc3afa3ec405b3d02939021baaee566e5038f4.gif`（判定状态 idle）。
本单仅此一条，无其他 Bot、无其他字段。

## 二、执行通道与实测证据

### 2.1 通道可用性核查（先做，后动手）

| 核查项 | 方法 | 结果 |
|---|---|---|
| 工具是否挂载 | `tool_search`：`bot` / `avatar` / `update_bot` / `minimax-bot` / 正则 `.` | 全部 0 命中（total=0） |
| 插件侧能力是否注册 | `~/.minimax/mcp-runtime-names.json` | 已注册 `["plugin","OFFICIAL","minimax-bot","minimax-bot"]`，工具表含 `update_bot`，即**插件侧能力存在，仅本会话未挂载** |
| 宿主 MCP 端点 | `servers.mcp.json` → `http://127.0.0.1:9/mcp/minimax-bot`（占位端口）；全量扫描本机 LISTENING 端口的 `/mcp/minimax-bot` 与 `/api/state` | 无命中；插件 `server.mjs` 未以独立进程常驻（宿主侧走进程内 miniapp surface） |
| 工具入参口径 | 插件 `miniapp/node/mcp.mjs` `update_bot` inputSchema（本次从 `tools/list` 实测取回） | `bot`(必填)、`caller_bot_id`、`name`、`role`、`instructions`、`skin`、`color`、`model`、`effort`、`avatar`、`auto_helper`、`main`；`additionalProperties: false` |

### 2.2 实际执行的变更

MCP 工具未挂载、宿主端点不可达，**未伪造工具调用**；改为起一个本地 HTTP 端点，直接加载插件自带的
`miniapp/node/mcp.mjs` 的 `createMcpHandler({ team, engine })`，按 MCP 协议真实调用。走的是插件**自己的**
工具定义、参数校验、`caller_bot_id` 解析、`Team.updateBot`（含 `avatarImage` 图库校验、`saveAvatar` 内容寻址落盘）、
`Store.bumpRoster` + `Store.save`（原子写）全链路，与宿主经 HTTP 调用同一份代码。

调用参数：`caller_bot_id=bot_809d03823a1f`（任务单允许省略或填本 Bot）、`bot=bot_0ba138cd0596`、
`avatar=avatar-0412273f0da3ce5471a6c1ed3dbc3afa3ec405b3d02939021baaee566e5038f4.gif`。

### 2.3 实测结果

MCP 协议往返（真实 HTTP，`POST /mcp/minimax-bot`）：

| 步骤 | 实测返回 |
|---|---|
| `initialize` | HTTP 200，`serverInfo={"name":"minimax-bot","version":"0.7.24"}`，session `mcp_cd664ae081c3` |
| `tools/list` | 21 个工具，`has_update_bot=true`，`update_bot` 的 inputSchema 如 2.1 所列 |
| `tools/call update_bot` | HTTP 200，`{"updated":"缺陷闭环","bot_id":"bot_0ba138cd0596"}` |

落库回读（`state.json`）：

| 字段 | 调用前 | 调用后（17:48:22） | 最终稳态（17:54:42） |
|---|---|---|---|
| `bots[bot_0ba138cd0596].avatar` | `avatar-0412273f…f4.gif` | `avatar-0412273f…f4.gif` ✅ | `avatar-0412273f…f4.gif` ✅ |
| `rev` | 19 | 20 | 19（被宿主回写，见 §四 G-2） |
| `rosterRev` | 133 | 134 | 133（同上） |
| `updatedAt` | 1794036659995 | 1794390502397 | 1794036659995（同上） |

头像文件校验（插件 `avatarImage` 同款口径）：文件存在于插件图库目录、`AVATAR_NAME_RE` 命中、2,032,279 字节
（≤ `MAX_AVATAR_FILE_BYTES` 2 MB）、GIF 魔数 `47 49 46 38 39 61`（`GIF89a`）通过。

其余 Bot 未被触碰（`bot_809d03823a1f` rev66 / `bot_ccab884af229` rev22 / `bot_27cd491314c1` rev22，头像均与调用前一致）。

## 三、结论

**有条件通过**。

- **主项达成**：`bot_0ba138cd0596` 的头像现为 `avatar-0412273f0da3ce5471a6c1ed3dbc3afa3ec405b3d02939021baaee566e5038f4.gif`
  （idle 态），经 MCP 协议真实调用 `update_bot` 并回读复核，终态稳定（17:54:42 复核仍为目标值），任务单要求的唯一一条变更已完成。
- **条件一：调用链不等于宿主经 MCP 客户端的调用**。本回合 `update_bot` 是在宿主外以插件自身 `createMcpHandler` 起本地端点执行的，
  工具语义与代码路径与宿主一致，但「宿主 MCP 客户端 → 注册端点」这条链**本回合未被实测验证**（端点不可达、工具未挂载）。
- **条件二：进程外写入对宿主非权威写**。宿主按周期用自身内存态回写 `state.json`（17:52:57、17:53:36 两次），把 `rev`/`rosterRev`/
  `updatedAt` 还原为 19/133/1794036659995。因宿主内存态本就是目标 idle 头像，终态仍正确；但 `rev` 变更未持久化。
- **条件三：上午那单的结论需要 muse 复核**。见 §四 G-1。

## 四、遗留缺口与复通条件

| 编号 | 缺口 | 影响 | 复通条件 |
|---|---|---|---|
| G-1 | **发现上午 AVATAR-SYNC-20261111 的「work 头像切换已生效」结论不成立**：该单于 17:31 以裸改 `state.json` 的方式写入 `avatar-cbab946b…bb4.gif`（落盘一度为 rev20/rosterRev134），宿主在 17:47 前后已用内存态回写为 `avatar-0412273f…f4.gif`（rev19/rosterRev133）。故该单当时「已切换并复核生效」只在落盘视图成立，**宿主实际生效视图一直是 idle** | 该单结论档位需 muse 复核；本单 watcher 判定「当前状态 idle」与本条互相印证 | muse 复核 AVATAR-SYNC-20261111 结论并订正；后续 watcher 类改头像一律走宿主 MCP 通道，不再裸改落盘文件 |
| G-2 | 宿主按周期用内存态回写 `state.json`，进程外写入的 `rev`/`rosterRev` 会被抹平（实测 17:52:57、17:53:36 两次） | 变更审计字段不可信；头像字段因与宿主内存态一致而未受影响 | 在宿主会话内调用 `update_bot`，由宿主进程自身落库，`rev` 自增可持久 |
| G-3 | minimax-bot 的 MCP 工具在本会话未挂载、宿主注册端点不可达（`tool_search` 0 命中；`127.0.0.1:9` 为占位端口；本机 LISTENING 端口无 `/mcp/minimax-bot`、`/api/state` 命中） | 宿主 MCP 客户端链路本回合未实测 | 在显式挂载 minimax-bot 插件的会话中 `tool_search` 命中 `mcp__minimax-bot__update_bot`，直接调一次同名调用取得 200 回执即闭合 |
| G-4 | 本单无 `orders/` 任务单文件、无正式单号 | 黑板归档口径不完整 | muse 下发正式单号（或确认以 `AVATAR-SYNC*` 前缀作为 watcher 类临时单号）后回填本报告抬头 |

## 五、纪律合规自查

- 未改 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`（仅 muse 可改）。
- 仓库改动仅本单两份 M-team 报告；`git add -- <我件>` + `git commit --only -F <msg> -- <我件>`，无 `git add .`、无 force、未动他人文件。
  工作区另有既存未跟踪文件 `scripts/_validate_tmp.sh`（非本单产生），未暂存、未提交。
- 未改平台代码，本单不含测试条数引用。
- 结论三档取「有条件通过」，未尽事项与风险已列；未把「等价执行」写成「宿主 MCP 通道调用通过」，未把裸改落盘的中间态当作生效证据。

---

版本：v1.0 ｜ 2026-11-11 ｜ MMMan ｜ 结论：有条件通过
