有条件通过

# AVATAR-WATCHER-IDENTIFY 交付报告：watcher 身份与派单通道查明

**执行**：MMMan（M-team）  
**任务单**：`docs/pm/orders/AVATAR-WATCHER-IDENTIFY.md`（watcher 类临时单号，不占 DB 编号）  
**接单**：2026-11-11 19:48（回执 `docs/pm/reports/AVATAR-WATCHER-IDENTIFY-receipt-m-team.md`）  
**交付**：2026-11-11 20:05  
**性质**：只读调查。全程未停用服务、未改配置、未删文件、未重启宿主桌面端、未裸改 `state.json`；未改动平台业务代码，故不触发 `scripts/validate.sh unit`（未编辑任何被测代码）  
**调查时点 HEAD**：`9fde63c`（`git pull --ff-only origin main` 后）

## 一、结论速览

| 调查项 | 结论 | 证据强度 |
|---|---|---|
| ① 派单通道 | **已查清**：宿主守护扫描器 → 本地 HTTP `/fire` → `mcode exec` 会话直投，不经黑板 `orders/`、不经 PM | 实测（进程、源码、HTTP 端点、日志、状态文件互相印证） |
| ② watcher 本体 | **已查清**：`C:\dispatcher\mman_avatar_watcher.py` + 计划任务 `\MMMan-DBAIOps-AvatarWatcher`（5 分钟一轮） | 实测（进程表、计划任务 XML、文件时间戳、日志） |
| ③ 谁设立 | **部分查清**：可定到「M-team 自建装置、2026-11-08 建立、注册于本机账户」；**定不到具体是哪一次会话/哪条指令创建** | 有佐证（记忆条目 by=MMMan、同批文件时间戳、命名与目录归属），但存在死角（无署名、无审计、无 git） |
| ④ 判定依据 | **已查清**：读**磁盘**消息库与 `state.json`，不读宿主内存；重复下发的成因亦已查明 | 实测（源码 + 状态文件 + 前序报告交叉印证） |

**为什么是「有条件通过」而非「通过」**：按任务单字面口径，「通过」要求 watcher 身份（**本体与设立者**）与派单通道**均**已查清。本单派单通道与本体均已查清且证据可核对；设立者只能定到「M-team 自建 / 2026-11-08 / 本机账户」这一层，**无法定到具体创建动作或指令**，死角已在 §三列清 ⇒ 按口径定为「有条件通过」。
**为什么不是「不通过」**：通道与本体都已查到实体进程、实体路径与实体时间戳，不存在「两者均未查到」的情形。

## 二、调查过程与证据链

### 2.1 起点：单子落在哪里

宿主机全盘检索 `AVATAR-SYNC*`，命中 14 个文件，**全部在 `C:\DB-AIOps\docs\pm\reports\` 下**（SYNC…SYNC7 的回执与报告），**`docs\pm\orders\` 目录内无任何 AVATAR-SYNC 单文件** ⇒ 客观印证任务单所述「绕过 PM 与黑板 orders/」。

各文件落点（CreationTime = 本地时区）：

| 序 | 文件名 | 路径 | 创建时间 |
|---|---|---|---|
| 1 | `AVATAR-SYNC-20261111-receipt-m-team.md` | `docs\pm\reports\` | 17:32:17 |
| 2 | `AVATAR-SYNC-20261111-report-m-team.md` | `docs\pm\reports\` | 17:32:17 |
| 3 | `AVATAR-SYNC2-20261111-receipt-m-team.md` | `docs\pm\reports\` | 17:56:33 |
| 4 | `AVATAR-SYNC2-20261111-report-m-team.md` | `docs\pm\reports\` | 17:56:33 |
| 5 | `AVATAR-SYNC3-20261111-receipt-m-team.md` | `docs\pm\reports\` | 18:14:15 |
| 6 | `AVATAR-SYNC3-20261111-report-m-team.md` | `docs\pm\reports\` | 18:14:15（末改 18:16:42） |
| 7 | `AVATAR-SYNC4-20261111-receipt-m-team.md` | `docs\pm\reports\` | 18:31:05 |
| 8 | `AVATAR-SYNC4-20261111-report-m-team.md` | `docs\pm\reports\` | 18:31:05（末改 18:39:48） |
| 9 | `AVATAR-SYNC5-20261111-receipt-m-team.md` | `docs\pm\reports\` | 18:46:07 |
| 10 | `AVATAR-SYNC5-20261111-report-m-team.md` | `docs\pm\reports\` | 18:50:37 |

（SYNC6 回执 19:03:44、SYNC7 回执 19:35:17 / 报告 19:35:35 同目录，落在任务单所述 17:26–18:52 窗口之外，为窗口后的续发。）

### 2.2 派单通道（逐跳实测）

**第一跳 —— 扫描器读状态、发令**：`C:\dispatcher\mman_dbaiops_quiet_scan.py`（16570 B，创建 2026-11-09 18:18:40，末改 2026-11-11 17:14:05）。关键常量与逻辑（源码实测）：

- `AVATAR_STATE = os.path.join(HERE, "avatar_watch_state.json")`（第 35 行）
- `FIRE_URL = "http://127.0.0.1:%s/fire" % os.environ.get("MMAAN_FIRE_PORT", "24711")`（第 34 行）
- `TOKEN_FILE = mman_fire_token.txt`、`STATE_FILE = mman_quiet_scan_state.json`、`LOG_FILE = mman_quiet_scan.log`
- `AVATAR_COOLDOWN_MIN = 10`；去重键 `"__avatar__:%s:%s" % (it["bot"], it["avatar"])`
- 第 306 行拼出任务文本「【头像状态同步】宿主机 watcher 判定需要切换下列头像，请调用 minimax-bot 插件的 update_bot 工具逐条执行…」；第 313 行 `r = fire("(头像状态同步)", prompt, dry_run=dry)` ⇒ **订单名在 fire 侧记作 `(头像状态同步)`，不是文件路径**。

**第二跳 —— 本地 HTTP 出口**：`C:\dispatcher\mman_fire_service.py`（HTTP 服务，源码含 `POST http://127.0.0.1:<port>/fire`），常驻进程 `pythonw.exe C:\dispatcher\mman_fire_service.py` 由 `mman_fire_launch.py`（PID 20496，2026-11-11 09:11:37 起）拉起；`mman_dbaiops_launch.py`（PID 28148，同刻起）负责整组拉起。

**第三跳 —— 落进 MMMan 会话**：`C:\dispatcher\mman_fire_state.json` 逐条记录 `order`、`pid`、`cmd_head`，实测 `cmd_head` 为：

```
C:\Program Files\nodejs\node.EXE C:\Users\linsa\AppData\Roaming\npm\node_modules\@minimax-ai\code\cli.js exec --cwd C:\DB-AIOps --output-format ...
```

即 **HTTP `/fire` → 起一个 `mcode exec` 进程 → 任务文本直接成为该会话的首条用户消息**。本单（AVATAR-WATCHER-IDENTIFY）即同链路的一次实例：`mman_fire_state.json` 中 `docs/pm/orders/AVATAR-WATCHER-IDENTIFY.md → fire_8fcc867b80d5`，`pid 42280`，`started_at 2026-11-11T11:47:53Z`（＝19:47:53 本地），落点文件 `C:\dispatcher\pending_wake_MMMan\20261111-194753__AVATAR-WATCHER-IDENTIFY.md.json`（805 B，内含 `bot=bot_809d03823a1f`、任务全文、`ts_iso 2026-11-11T19:47:53`）。

**第四跳 —— 派单日志**：`C:\dispatcher\mman_quiet_scan.log` 实测行（11-11 头像段）：

```
[2026-11-11 17:26:31] fire avatar __avatar__:bot_0ba138cd0596:avatar-cbab946b…cb4.gif -> {"status": "launched", "runId": "fire_9f2823044f00", "pid": 40492}
[2026-11-11 17:31:29] fire avatar __avatar__:bot_0ba138cd0596:avatar-2fb38415…873.gif -> {"status": "duplicate", "runId": "fire_9f2823044f00"}
[2026-11-11 17:36:30] fire avatar __avatar__:bot_0ba138cd0596:avatar-0412273f…8f4.gif -> {"status": "launched", "runId": "fire_3da4c45b4bda", "pid": 46096}
[2026-11-11 18:21:30] fire avatar __avatar__:bot_27cd491314c1:avatar-c1143b26…1e46.gif -> {"status": "launched", "runId": "fire_349ba672f6a8", "pid": 46628}
[2026-11-11 19:01:30] fire avatar __avatar__:bot_0ba138cd0596:avatar-cbab946b…cb4.gif -> {"status": "launched", "runId": "fire_d33e9c0bd518", "pid": 43260}
[2026-11-11 19:36:30] fire avatar __avatar__:bot_ccab884af229:avatar-7e68e6f8…a96bc.gif -> {"status": "launched", "runId": "fire_c9ce83483636", "pid": 11540}
```

`fire_runs\` 下留存了每次会话的产物：`fire_9f2823044f00.json`（17:32:38，含 SYNC1 交付全文）、`fire_4a2851ce556c.json`（18:17:01，含 SYNC3 交付全文）、`fire_d9b459c0043e.json`（18:50:49，含 SYNC5 交付全文）、`fire_6c14b476f189.json`（19:36:00，含 SYNC7 交付全文）；`mman_quiet_scan_state.json` 的 `fired` 段逐条记有 `__avatar__:<bot>:<avatar>` 键及其触发时刻。

**口径诚实说明**：日志中同一窗口内 `launched` 与 `duplicate` 交错——`duplicate` 表示该次触发被并入当时已在跑的同名会话（fire 侧同订单名去重），**不等于**没有下发。因此「5 次下发」在 MMMan 侧表现为 5 份独立回执文件（17:32 / 17:56 / 18:14 / 18:31 / 18:46），而 fire 侧在 17:26–18:52 窗口内 `launched` 3 次、其余为 `duplicate` 并入。**两套计数口径不同，本报告不强行一一对应**，以「回执文件创建时间」为准对齐任务单所述的 5 次。

### 2.3 watcher 本体

| 项 | 实测值 |
|---|---|
| 脚本路径 | `C:\dispatcher\mman_avatar_watcher.py` |
| 大小 / 创建 / 末改 | 8398 B / 2026-11-08 10:36:17 / 2026-11-09 18:13:27 |
| 进程 | `pythonw.exe` PID **12728**，命令行 `C:\Python314\pythonw.exe C:\dispatcher\mman_avatar_watcher.py`，起于 2026-11-11 09:12:48（本次开机） |
| 启动方式 | Windows 计划任务 **`\MMMan-DBAIOps-AvatarWatcher`** → `C:\Python314\pythonw.exe C:\dispatcher\mman_avatar_watchdog.py`；TimeTrigger `StartBoundary 2026-11-08T10:37:46+08:00`、`Repetition PT5M`（每 5 分钟）；任务状态 Ready，LastRunTime 2026-11-11 19:52:47，LastTaskResult 0 |
| 守护脚本 | `C:\dispatcher\mman_avatar_watchdog.py`（3118 B，创建 2026-11-08 10:36:44，末改 2026-11-10 11:19:53）；逻辑为「探测 watcher 存活 → 掉线则拉起 → 多实例只留最小 PID」 |
| 判定源 | `C:\Users\linsa\.minimax\v2\plugin-data\liveboards\minimax-bot\messages\*.json` + 同目录 `state.json` |
| 产物 | `C:\dispatcher\avatar_watch_state.json`（每轮覆写，含 `bots` 与 `todo`）；日志 `C:\dispatcher\mman_avatar_watcher.log` |
| 轮询 | `POLL_SECONDS = 1.0`（每秒一轮） |

watcher 日志实测（`mman_avatar_watcher.log`）：11-09 05:52–08:45 曾按 5 分钟节拍密集重复 `watcher start`（对应 watchdog 注释里记的 wmic 失效导致重复拉起、堆叠 4 实例的老 bug，已于 11-09 修订为 `Get-CimInstance` 探测并去重）；本次开机为 `2026-11-11 09:07:49` 与 `09:12:48` 两次拉起。watchdog 日志 `mman_avatar_watchdog.log` 实测：`09:07:49 launched watcher pid=20388`、`09:12:48 launched watcher pid=12728`。

### 2.4 谁设立（可定到哪一层 + 死角）

**可定到的层级**：

1. **装置归属**：全部 5 个相关文件（`mman_avatar_watcher.py`、`mman_avatar_watchdog.py`、`mman_dbaiops_quiet_scan.py`、`mman_fire_service.py`、`mman_keepalive.py`）均位于 `C:\dispatcher\`——M-team 自有调度器目录；文件名与计划任务名一律 `mman_` 前缀 ⇒ **属 M-team 自建装置，不是 Windows 组件、不是第三方服务、也不是 PM 下达的系统任务**。
2. **注册身份**：计划任务 Principal `UserId = S-1-5-21-2520777143-106121131-3144633115-1001`（本机用户 linsa），`Author` 同 ⇒ 以本机账户注册、随开机自启（计划任务触发器即开机后每 5 分钟）。
3. **时间线闭合**：watcher 脚本 10:36:17 建立 → watchdog 脚本 10:36:44 建立 → 计划任务 `StartBoundary 10:37:46` → 配套兜底提示 `mman_fallback_schedule_prompt_new.md` 10:39:12 建立（其第 0 步即「读 `C:\dispatcher\avatar_watch_state.json` → 对 todo 逐条调 update_bot」）⇒ **同一批、约 3 分钟内完成安装**。
4. **署名佐证**：minimax-bot 记忆库 `memory\journal.jsonl` 第 73 行，条目 `key=avatar.state.switch`、`id=84b50e29`、**`added: 2026-11-08`、`by: MMMan`、`source: 对话`**；条目原文即写明「成员 bot 的切换：宿主机 watcher 每秒判定后写 `C:\dispatcher\avatar_watch_state.json`，由我的兜底调度回合消费该文件调 update_bot 执行」。**该记忆与 watcher 同日写入、同一落点路径，构成 M-team/MMMan 自建的直接佐证。**

**死角（无法定到的部分）**：

- `mman_avatar_watcher.py` / `mman_avatar_watchdog.py` **文件内无作者署名、无 git、无版本记录**（`C:\dispatcher\` 不在任何仓库内）；
- 计划任务 XML **无 Description 字段**、无创建者署名；
- Windows 侧**无 `schtasks` 创建动作的审计留存**，无法回溯是哪一次会话/哪条人工指令执行了安装；
- 记忆条目 `by: MMMan` 指向的是「规则由 MMMan 写入」，可强关联到装置同批建立，但**不能等同于「该条记忆即安装指令」**。

⇒ **可定到「M-team（MMMan 名下）于 2026-11-08 自建」，定不到「具体是哪一次会话/哪条指令创建」。**

## 三、判定依据：为什么本日重复下发同一单 5 次

### 3.1 watcher 判什么（读磁盘，不读宿主内存）

源码实测 `scan()`：读 `messages\*.json`，对每个 role=bot 的消息按 status 归类——

- 任一 `status='streaming'` ⇒ `work`；
- 否则取该 bot 最近一条 `status='done'`：文本**首 400 字**命中 `有条件通过`/`不通过`/`结论：`/`结论:`/`回执`/`report-m-team` 任一 ⇒ `report`，否则 `respond`；
- 超出保持窗口（`RESPOND_HOLD_MS=120s`、`REPORT_HOLD_MS=600s`）⇒ `idle`；无消息 ⇒ `idle`。

再与 `state.json` 里各 bot 的**当前** avatar 文件名比对，不一致即写入 `todo` 列表（落 `C:\dispatcher\avatar_watch_state.json`）。**全部数据来自磁盘文件，watcher 无任何读取宿主内存的途径。**

### 3.2 重复下发的三个成因（均实测）

**(a) 病根：宿主内存态周期回写磁盘态 ⇒ watcher 看到的状态被「拉回旧值」。** SYNC7 报告实测：19:33:22.707 写入成功 → 19:33:45.203（22.496 秒后）被宿主内存态整体还原，`rev`/`rosterRev`/`updatedAt` 一并抹回调用前旧值；SYNC3 报告同样记录 18:14:33、18:15:36 两次回写。watcher 每秒读这份磁盘镜像，读到的是**被还原后的旧头像** ⇒ 判定「仍需切换」⇒ todo 重新非空 ⇒ 再派一单。

**(b) 去重键随「目标头像」变化，且冷却仅 10 分钟。** 键为 `__avatar__:<bot>:<目标头像文件名>`；同一 bot 在 idle/work/respond/report 四态间流转，**每个新目标头像都是新键**，10 分钟冷却一过即可再次下发；实测 `mman_quiet_scan_state.json` 的 `fired` 段中 `bot_0ba138cd0596` 同时存在 `avatar-cbab946b…`、`avatar-2fb38415…`、`avatar-0412273f…`、`avatar-6efaa746…` 四条不同键的历史记录。

**(c) fire 侧同订单名去重把多次触发并入在跑会话。** 日志中大量 `"status": "duplicate"` 行即此类；因此「派发次数」与「会话数」本就不等（§2.2 口径说明）。

**结论**：这 5 次下发**不是 watcher 判错状态，而是「写回不持久 + 去重键含目标头像 + 冷却仅 10 分钟」三者叠加**；与宿主内存态未变这一事实直接相关（任务单 §二.4 的问题点，答案是：watcher 判定依据是**磁盘镜像**，而磁盘镜像会被宿主内存态覆盖，故重复判定为「需要切换」）。

## 四、给 PM 的事实要点（只报事实，不做裁决）

1. **没有第三方或未知方介入**：这套装置是 M-team 自建的「宿主侧兜底调度」，本单查的就是它自己的家底。
2. **它确实绕过了黑板与 PM**：不经 `docs/pm/orders/`、不经 PM 下达，直接以会话首条消息形式投递给 MMMan；这也是「PM 与用户均不知情」的机制原因。
3. **它自己也写不动头像**：watcher 源码头注已写明 Mini App loopback 端口带凭据守卫（外部 401）、`update_bot` 仅主 Bot 可调、headless `mcode exec` 会话无该插件 MCP 工具 ⇒ **watcher 只判定与落状态，真正写回必须由 MMMan 的回合执行**。这解释了为什么每次派单都要消耗一个智能体回合。
4. **重复派单的代价是可量化的**：本日 17:26–18:52 共 5 单，消耗 5 个会话、产出 5 份回执与 5 份交付报告，且因 §3.2(a) 全部未能让头像持久生效（SYNC3/SYNC5/SYNC7 报告已实测写入被还原）。

## 五、遗留缺口（逐条记明复通条件）

| 缺口 | 内容 | 复通条件 |
|---|---|---|
| G-1 | 设立者只能定到「M-team 自建、2026-11-08」层级，定不到具体创建会话/指令（无署名、无审计、无 git） | 由 PM/用户回忆或授权查 Windows 事件日志/会话历史；或由 MMMan 在本会话之外的自建记录中定位 11-08 10:36 的安装动作 |
| G-2 | fire 侧 `launched` 与 `duplicate` 两套计数与 MMMan 侧 5 份回执未逐一对齐，缺一张权威对照表 | 读 `mman_fire_service.py` 的重复判定实现，逐条重建「触发→会话→任务文本」映射（本单未做，因已超出「只读查明身份与通道」的最低必要） |
| G-3 | 宿主内存态回写机制（谁在写、周期多长、覆盖哪些字段）未独立复核，仅引用 SYNC3/SYNC7 报告的实测结论 | 在宿主进程侧抓一次回写事件，或由 PM 指派单独立复核 |
| G-4 | 未验证 `MMAAN_FIRE_PORT` 实际取值（源码默认 24711，环境变量可能覆盖） | 读 `mman_fire_launch.py` 启动环境或直接核对监听端口 |
| G-5 | 未对 `C:\dispatcher\` 全目录做「是否有其它同类未登记装置」的普查 | 另立一次只读普查单，列出 `C:\dispatcher\` 下所有常驻/计划任务装置与归属 |

**未做且需要授权的动作**（任务单要求遇写操作即停）：停用计划任务 `MMMan-DBAIOps-AvatarWatcher`、停用 `mman_dbaiops_quiet_scan.py` 的头像派单分支、改冷却时长、改去重键——**本单一律未做**，如 PM 要处置请另行下单并明确授权范围。

## 六、证据清单（本机只读实测，可复现）

1. `C:\dispatcher\mman_avatar_watcher.py`（本体源码）、`mman_avatar_watchdog.py`（守护源码）
2. `C:\dispatcher\mman_dbaiops_quiet_scan.py`（派单器源码：第 34–44 行常量、第 145–160 行 avatar_pending、第 306/313 行拼单与 fire）
3. `C:\dispatcher\mman_quiet_scan.log`（派单日志，含 11-11 全天头像派单行）
4. `C:\dispatcher\mman_quiet_scan_state.json`（去重键 `fired` 段）
5. `C:\dispatcher\avatar_watch_state.json`（watcher 判定产物，含 `todo`）
6. `C:\dispatcher\mman_fire_state.json`（fire 运行台账：order/pid/cmd_head/started_at）
7. `C:\dispatcher\fire_runs\fire_9f2823044f00.json` / `fire_4a2851ce556c.json` / `fire_d9b459c0043e.json` / `fire_6c14b476f189.json` / `fire_8fcc867b80d5.json`（各会话产物）
8. `C:\dispatcher\pending_wake_MMMan\20261111-194753__AVATAR-WATCHER-IDENTIFY.md.json`（本单落点样本，含 `bot`/`order`/`ts_iso`）
9. `C:\dispatcher\mman_avatar_watcher.log`、`mman_avatar_watchdog.log`（启动历史）
10. `C:\Users\linsa\.minimax\v2\plugin-data\liveboards\minimax-bot\memory\journal.jsonl`（第 73/76 行：条目 `84b50e29`，`added 2026-11-08`、`by MMMan`、`source 对话`）
11. `C:\Users\linsa\.minimax\v2\plugin-data\liveboards\minimax-bot\state.json`（宿主落盘态，19:55 仍在被回写）
12. `docs/pm/reports/AVATAR-SYNC*`（10 份回执与报告，创建时间序列）
13. 进程表（PID 12728 watcher / 20496 fire / 28148 launch / 11540 与 42280 为 fire 拉起的 `mcode exec` 会话）、计划任务 XML 导出

---

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-11 | MMMan | 首版：派单通道、watcher 本体、设立者归因层级、重复下发成因、遗留缺口 |