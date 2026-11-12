有条件通过

# AVATAR-WATCHER-DISABLE 交付报告：停用 watcher 订单的撤销与现态核验

**执行**：MMMan（M-team）
**任务单**：`docs/pm/orders/AVATAR-WATCHER-DISABLE.md`（watcher 类临时单号，不占 DB 编号）
**核验性质**：**只读核验**——未禁用/启用任何计划任务、未停/启任何进程、未改任何配置、未删文件、未重启宿主桌面端、未裸改 `avatar_watch_state.json`、未改平台业务代码
**核验时点**：2026-11-12 16:47–17:01（本机只读）
**调查时点 HEAD**：`cc960e5`（`docs/pm/reports/DB-005R-receipt-m-team.md` §19 续记已入库；工作区 `git status --porcelain` 原始字节长度为 0）

---

## 一、一句话结论

**本单四步（停计划任务 / 停进程 / 停扫描器头像分支 / 清残留待办）实测均未执行**，但★**这不是执行失败，而是授权在执行前被撤回**：用户 2026-11-11 20:19 下达《AVATAR-WATCHER-KEEP》撤销本单，与 20:08 下达本单仅隔 **11 分钟**。现态核验结论＝**与 KEEP 单三项要求完全一致**（任务 Ready/Enabled、watcher 进程在跑、`state.json` mtime 推进）⇒ 按《AVATAR-WATCHER-KEEP》的评分口径（"DISABLE 未执行或已完全恢复"）本单成立。

**结论档位取「有条件通过」而非「通过」**：本单自带三档中「通过」要求第 1、2、3 步均完成，「不通过」要求第 1 或第 2 步未完成（字面对应本单实况），但该字面档位会把**授权撤回**误记成**执行失败**。故取「有条件通过」，条件写明：**本单已被 KEEP 单撤销，未执行系授权撤回；现态与 KEEP 单要求一致**。★**最终档位与 STATUS.md / DECISIONS.md 的归档与否，仅部门经理（muse）可裁**——若 PM 认为「已被撤销的单应按撤销归档、不评档」，请直接裁定并调整 STATUS.md，本方不自改黑板。

## 二、订单时序（事实陈述）

| 时点 | 事件 | 依据 |
|---|---|---|
| 2026-11-11 20:08 | 用户拍板停用 watcher，下达《AVATAR-WATCHER-DISABLE》 | 任务单 `AVATAR-WATCHER-DISABLE.md` §一（提交 `28924568`） |
| 2026-11-11 20:19 | 用户改主意「先别停、留着」，下达《AVATAR-WATCHER-KEEP》撤销本单 | 任务单 `AVATAR-WATCHER-KEEP.md` §一（提交 `271e9adf`） |
| 2026-11-12 16:47–17:01 | 本方只读核验现态（本报告） | 本报告 §三 实测 |

⇒ 本单从下达至被撤销的 **11 分钟窗口内无任何执行步骤落地**（见 §三 实测）。

## 三、逐项实测证据

### 3.1 第 1 步「停用计划任务」——**未执行**

`schtasks /Query /TN MMMan-DBAIOps-AvatarWatcher /FO LIST /V`：

```
   Next Run Time:                        2026/11/12 17:02:46
   Status:                               Ready
   Last Run Time:                        2026/11/12 16:57:47
   Last Result:                          0
   Task To Run:                          C:\Python314\pythonw.exe C:\dispatcher\mman_avatar_watchdog.py
   Scheduled Task State:                 Enabled
```

⇒ 任务为 **Ready / Enabled**，每 5 分钟一轮持续触发（trigger `StartBoundary=2026-11-08T10:37:46+08:00`、`Repetition.Interval=PT5M`），**从未处于 Disabled**。判定锚点：`Scheduled Task State` 非 Disabled ⇒ 第 1 步未执行。

### 3.2 第 2 步「停掉在跑进程」——**未执行**

进程表（`Win32_Process`，`pythonw.exe` 且命令行含 avatar）：

```
   PID=12728 | C:\Python314\pythonw.exe C:\dispatcher\mman_avatar_watcher.py
   PID=36812 | "…\dsh-runtimes\dsh-primary-runtime\…\pythonw.exe" "C:\DeepSeek Bot\avatar-states\avatar_watcher.py"
```

- **PID 12728 的 `CreationDate` = 2026-11-11 09:12:48**，即**自 11-11 上午起连续在跑、至 11-12 17:01 仍是同一 PID**（同 PID 未变 ⇒ 期间**未被停止也未被重启**）。
- PID 36812 属 `C:\DeepSeek Bot\avatar-states\` 的**另一程序**（DeepSeek 侧 avatar watcher），**不是**本单所指 M-team 自建装置，**不计入本单判定**（避免误伤无关进程）。

⇒ 第 2 步未执行。

### 3.3 第 3 步「停用扫描器的头像派单分支」——**未执行**

`C:\dispatcher\mman_dbaiops_quiet_scan.py` 源码实读，头像分支**完好启用**：

| 行号 | 实读代码 | 含义 |
|---|---|---|
| L47 | `AVATAR_COOLDOWN_MIN = 10` | 冷却常量仍在 |
| L149 | `def avatar_pending():` | 头像待办读取函数仍在 |
| L288 | `avatars = avatar_pending()` | 主流程仍读取头像待办 |
| L298–L305 | `pending_avatars = []` … `pending_avatars.append(it, key)` | 头像待办入队与冷却过滤仍在 |
| L314 | `if not (pending_orders or pending_avatars or pending_daily or pending_rejudge):` | 头像待办仍参与「有无待办」判定 |
| L333 | `for it in pending_avatars:` | 头像派单循环仍在 |

**★已排除一个易误判点**：该文件 mtime 为 **2026-11-12 12:08:18**（今日），但**这次改动与头像分支无关**——同文件 L54 `REJUDGE_AFTER_HOUR = 10`、L173 `daily_section_missing(today)`、L198 `rejudge_pending(today)`、L289–L292 为**日周期复判回填分支（分支 D）** 的新增代码（服务 G-11 D1/D2 复判叫醒），与头像派单分支无交集。**不得凭 mtime 就断言头像分支被改过**。

⇒ 第 3 步未执行。

### 3.4 第 4 步「清残留待办」——**未执行，且待办在自行再生**

`C:\dispatcher\avatar_watch_state.json`：

| 读时点 | 文件 mtime | `todo` 条数 |
|---|---|---|
| 16:49:02 | 2026-11-12 16:49:02 | — |
| 约 16:57 | — | **0** |
| 17:00:59 | 2026-11-12 17:00:59 | **1**（`bot=bot_0ba138cd0596`） |

⇒ mtime **持续推进**（16:49:02 → 16:59:39 → 17:00:59），且 `todo` 在 0 与 1 之间摆动（16:57 读数 0，17:00:59 读数 1）⇒ **watcher 仍在正常轮询并自行写回待办**。`bots` 覆盖四个 Bot（`bot_809d03823a1f`／`bot_ccab884af229`／`bot_27cd491314c1`／`bot_0ba138cd0596`）。★**第 4 步本就只在「跳过第 3 步」时才需要**，本单第 3 步未执行，故第 4 步无执行义务；此处记录仅为说明待办来源。

### 3.5 「fire avatar」派单实况

`C:\dispatcher\mman_quiet_scan.log`：

- `fire avatar` 行**合计 38 条**，首条 `2026-11-09 18:18:42`，**末条 `2026-11-11 21:01:30`**。
- 末条时点 **晚于 KEEP 单下达时点（11-11 20:19）** ⇒ 撤销后头像派单**仍在继续**，与「留用 watcher」一致。
- 近 30 分钟 tail 均为：`quiet: 无待办（orders=0 avatars=0 daily_missing=False）`（16:46:30／16:51:30／16:56:30）⇒ **今日无新增 `fire avatar`**。

### 3.6 一处诚实标注：**该窗口无系统侧启停审计可查**

`Microsoft-Windows-TaskScheduler/Operational` 事件日志 **`IsEnabled=False`**（未启用）⇒ **11-11 20:08–20:19 窗口内没有可读的任务启停事件记录**。本单「未执行」这一结论是由**当前态（Enabled/Ready）＋ 进程 PID 与起时连续性（同一 PID 12728 自 09:12:48 连续在跑）＋ 源码头像分支完好 ＋ state mtime 推进** 四项**当下实测**共同支持，**不是**直接读到「谁在何时启停」的历史流水。已如实标注，未以推断充作实测。

## 四、结论与未尽事项

- **结论档位：有条件通过**（条件：本单已被《AVATAR-WATCHER-KEEP》于 2026-11-11 20:19 撤销，未执行系授权撤回而非执行失败；现态与 KEEP 单三项核验一致）。
- **未执行事项**：第 1、2、3、4 步全部未执行（授权撤回，非失败）。
- **风险 / 待裁决**：
  1. ★**无历史启停审计可查**（TaskScheduler/Operational 日志未启用）——若 PM 需要"谁在何时启停"的硬证据，需先启用该日志通道后复现，本轮无法补出。
  2. ★**待办在自行再生**（todo 0→1），确认 watcher 持续在用；重复下发风险与频率收敛口径见 STATUS.md 与 DEC-008（非紧急）。
  3. ★**档位与归档口径待 PM 裁定**：本单已被撤销，评「有条件通过」还是按撤销归档，仅 muse 可裁。
- 本单**未产生缺陷**（不新立缺陷单），**未改 STATUS.md / DECISIONS.md**（仅部门经理可改），**未改母报告 `DB-005R-report-m-team.md`**（与 G-11 链路无关，档位不变），**未触碰他人交付物**。

---

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-12 | MMMan | 首次入库（撤销单核验与现态证据） |
