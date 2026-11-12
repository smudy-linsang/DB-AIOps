通过

# AVATAR-WATCHER-KEEP 交付报告：撤销停用、留用 watcher 的恢复核验

**执行**：MMMan（M-team）
**任务单**：`docs/pm/orders/AVATAR-WATCHER-KEEP.md`（watcher 类临时单号，不占 DB 编号）
**核验性质**：**只读核验**——未禁用/启用任何计划任务、未停/启任何进程、未改任何配置、未删文件、未重启宿主桌面端、未裸改 `avatar_watch_state.json`、未改平台业务代码
**核验时点**：2026-11-12 16:47–17:01（本机只读）
**调查时点 HEAD**：`cc960e5`（`docs/pm/reports/DB-005R-receipt-m-team.md` §19 续记已入库；工作区 `git status --porcelain` 原始字节长度为 0）

---

## 一、一句话结论

《AVATAR-WATCHER-DISABLE》（用户 11-11 20:08 拍板停用）已被《AVATAR-WATCHER-KEEP》（用户 11-11 20:19 改主意留用）**撤销**，本单三项核验**全部成立**：① 计划任务 **Ready/Enabled** 且每 5 分钟持续触发；② watcher 进程 **在跑**（PID 12728，自 11-11 09:12:48 连续在跑未重启）；③ `avatar_watch_state.json` **mtime 持续推进**。**DISABLE 四步实测均未执行**，故符合本单「通过」口径（"DISABLE 未执行或已完全恢复，核验通过"）⇒ **通过**。

## 二、订单时序（事实陈述）

| 时点 | 事件 | 依据 |
|---|---|---|
| 2026-11-11 20:08 | 用户拍板停用 watcher，下达《AVATAR-WATCHER-DISABLE》 | 任务单 `AVATAR-WATCHER-DISABLE.md` §一（提交 `28924568`） |
| 2026-11-11 20:19 | 用户改主意「先别停、留着」，下达本单撤销 DISABLE、授权继续留用 watcher | 任务单 `AVATAR-WATCHER-KEEP.md` §一（提交 `271e9adf`） |

⇒ 本单 §二 第 1 项「立即停止执行 DISABLE」**无需恢复动作**（DISABLE 从未落地，见 §三）；第 2 项「如已部分执行则恢复」**不适用**；第 3 项核验逐项成立。

## 三、三项核验逐项实测

### 3.1 核验项①：计划任务为 Ready/Enabled

`schtasks /Query /TN MMMan-DBAIOps-AvatarWatcher /FO LIST /V`：

```
   Next Run Time:                        2026/11/12 17:02:46
   Status:                               Ready
   Last Run Time:                        2026/11/12 16:57:47
   Last Result:                          0
   Task To Run:                          C:\Python314\pythonw.exe C:\dispatcher\mman_avatar_watchdog.py
   Scheduled Task State:                 Enabled
```

- `Scheduled Task State = Enabled`、`Status = Ready`、每轮 `Last Result = 0`；
- 触发器：`StartBoundary = 2026-11-08T10:37:46+08:00`、`Repetition.Interval = PT5M` ⇒ **5 分钟一轮持续在跑**；
- `Task To Run` 为 watchdog 脚本 `C:\dispatcher\mman_avatar_watchdog.py` ⇒ 与 IDENTIFY 报告查明的通道一致（watchdog 负责拉起 watcher 本体）。

⇒ **核验项①成立**。

### 3.2 核验项②：watcher 进程在跑

```
   PID=12728 | C:\Python314\pythonw.exe C:\dispatcher\mman_avatar_watcher.py
   PID=36812 | "…\dsh-runtimes\dsh-primary-runtime\…\pythonw.exe" "C:\DeepSeek Bot\avatar-states\avatar_watcher.py"
```

- **PID 12728** = 本单所指 M-team 自建装置，`CreationDate = 2026-11-11 09:12:48`，至核验时点（11-12 17:01）**仍是同一 PID** ⇒ 在跑且**期间未被停止、未被重启**；
- **PID 36812** 属 `C:\DeepSeek Bot\avatar-states\` 的另一程序（DeepSeek 侧），**与本单无关**，仅列出以免误判，未对其做任何动作。

⇒ **核验项②成立**。

### 3.3 核验项③：`avatar_watch_state.json` mtime 在推进

| 读时点 | 文件 mtime | `todo` 条数 |
|---|---|---|
| 16:49 | 2026-11-12 16:49:02 | — |
| 约 16:57 | — | 0 |
| 16:59 | 2026-11-12 16:59:39 | — |
| 17:00 | 2026-11-12 17:00:59 | 1（`bot=bot_0ba138cd0596`） |

⇒ mtime **连续推进**（16:49:02 → 16:59:39 → 17:00:59），且 `todo` 在 0 与 1 之间摆动（16:57 读 0、17:00:59 读 1）⇒ **watcher 正常轮询并自行写回待办**；`bots` 覆盖四个 Bot（`bot_809d03823a1f`／`bot_ccab884af229`／`bot_27cd491314c1`／`bot_0ba138cd0596`）。

⇒ **核验项③成立**。

### 3.4 补充：DISABLE 四步「未执行」的正向证据

| DISABLE 步骤 | 实测 | 判定 |
|---|---|---|
| 1 停用计划任务 | 任务 Ready/Enabled，从未 Disabled | 未执行 |
| 2 停掉进程 | PID 12728 自 11-11 09:12:48 连续在跑、同一 PID 未变 | 未执行 |
| 3 停扫描器头像分支 | `mman_dbaiops_quiet_scan.py` L47/L149/L288/L298–L305/L314/L333 头像分支完好启用 | 未执行 |
| 4 清残留待办 | todo 由 watcher 自行写回（0→1），第 3 步未执行故本步无执行义务 | 不适用 |

★**已排除一处易误判**：该扫描器文件 mtime 为 2026-11-12 12:08:18（今日），但改动内容是 **日周期复判回填分支 D**（L54 `REJUDGE_AFTER_HOUR`、L173 `daily_section_missing`、L198 `rejudge_pending`、L289–L292，服务 G-11 D1/D2 复判叫醒），**与头像分支无交集**——不得凭 mtime 断言头像分支被改过。

### 3.5 补充：撤销后派单仍在继续（与「留用」一致）

`C:\dispatcher\mman_quiet_scan.log`：`fire avatar` 共 **38 条**，首条 `2026-11-09 18:18:42`，**末条 `2026-11-11 21:01:30`**——末条**晚于 KEEP 单下达时点 20:19** ⇒ 撤销后头像派单仍在进行；近 30 分钟 tail（16:46:30／16:51:30／16:56:30）均为 `quiet: 无待办（orders=0 avatars=0 daily_missing=False）`，**今日无新增 `fire avatar`**。

## 四、结论与未尽事项

- **结论档位：通过**（三项核验全部成立，且 DISABLE 未执行，符合本单「通过」口径）。
- **未执行事项**：本单 §二 第 2 项「恢复已部分执行的 DISABLE」**不适用**（DISABLE 从未落地，无需恢复）；第 1 项「停止执行 DISABLE」**已生效**（未执行任何 DISABLE 步骤）。
- **风险 / 待办（不影响本单结论）**：
  1. ★**重复下发风险仍在**：watcher 保留即意味着宿主内存态回写时仍可能重复判定「需要切换」（11-11 当日 `fire avatar` 38 条即为此现象）；派单频率收敛与状态漂移期逐次执行口径见 STATUS.md 与 DEC-008（**非紧急**）。**这是用户在知悉 SYNC8/SYNC9 已证明进程内通道可持久切换的前提下主动选择留用的结果**，本单只报事实、不代 PM 定口径。
  2. ★**无历史启停审计可查**：`Microsoft-Windows-TaskScheduler/Operational` 事件日志 `IsEnabled=False` ⇒ 无「谁在何时启停」的历史流水；本报告结论由当前态＋进程连续性＋源码＋state mtime 四项当下实测支持，已如实标注，未以推断充作实测。
  3. ★**归属未解**：watcher 由谁在何次会话建立仍只能定到「M-team 自建装置 / 2026-11-08 / 本机账户」这一层（见《AVATAR-WATCHER-IDENTIFY》报告 §一③，本单未新查、沿用该结论，不重复认定）。
- 本单**未产生缺陷**（不新立缺陷单），**未改 STATUS.md / DECISIONS.md**（仅部门经理可改），**未改母报告 `DB-005R-report-m-team.md`**（与 G-11 链路无关，档位不变），**未触碰他人交付物**。

---

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-12 | MMMan | 首次入库（撤销停用后留用状态的恢复核验） |
