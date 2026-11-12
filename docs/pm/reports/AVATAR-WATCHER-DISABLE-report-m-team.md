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
  ★**v1.1 订正（2026-11-12 17:1x）**：本行成立范围仅限**本报告读时点（≤16:56:30）**。其后 **17:01:30 新增 1 条 `fire avatar`**（计数 38→39），触发来源为**核验会话自身**（缺陷闭环 bot `bot_0ba138cd0596` 处于 streaming 态 ⇒ watcher 写 todo ⇒ 扫描器下一轮派单 ⇒ 17:06:30 复归 quiet）。详见 §五 订正一。

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

## 五、独立核验回报与逐条订正（2026-11-12 17:1x；v1.1）

本报告 v1.0 落库后，**独立只读核验**由 M-team 缺陷闭环完成并落库：`docs/pm/reports/AVATAR-WATCHER-核验-report-缺陷闭环.md`（**结论：通过**，提交 `eb075c6`）。该核验在 17:00:28–17:13 窗口独立取到 6 项实测，与本报告的结论**一致**：DISABLE 四步均未执行、KEEP 三项核验逐项成立、任务 Ready/Enabled、watcher PID 12728 自 11-11 09:12:48 在跑、`avatar_watch_state.json` mtime 17:00:27→17:11:11 推进。**本报告结论档位不变。**

### 订正一：「今日无新增 `fire avatar`」的成立范围

- **v1.0 原文**：近 30 分钟 tail 全为「无待办（avatars=0）」⇒ 今日无新增 `fire avatar`。
- **订正**：该结论**仅对本报告读时点（≤16:56:30）成立**。独立核验窗口内 **17:01:30 新增 1 条 `fire avatar`**（日志计数 38→39，末条 `[2026-11-12 17:01:30] fire avatar __avatar__:bot_0ba138cd0596:…`）。
- **成因（已查清，非故障）**：17:01:30 前后**核验会话自身**（缺陷闭环 bot `bot_0ba138cd0596`）处于 streaming 态 ⇒ watcher 据此写入 todo ⇒ 扫描器下一轮轮询即派单 ⇒ 17:06:30 复归 quiet（todo 被消费）。
- **对本报告结论的影响**：**无**。「DISABLE 四步未执行」「KEEP 三项核验成立」两项结论均不依赖「今日无新增 fire avatar」——恰恰相反，新增派单**反证头像派单分支处于工作态**（与核验方结论一致）。
- **★方法论留存**：判读「近 30 分钟无 `fire avatar`」类活口径时，**须先排除自身会话活动这一成因**（bot 处于 streaming ⇒ watcher 写 todo ⇒ 派单），否则会把会话活动误读成业务派单或误判成异常。

### 订正二：12:08 改动的证据强度上限（如实收紧）

- **v1.0 原文**：已排除一处易误判——`quiet_scan.py` 的 12:08 mtime 来自日周期复判回填分支 D，与头像派单分支无交集。
- **订正**：该判断**成立**（源码内证：288 行后新增 `# D. 日周期复判回填` 段，`pending_avatars` 构造条件、`AVATAR_COOLDOWN_MIN`、`fire avatar` 日志行均原样存在；且功能实证 17:01:30 该分支实派单成功）。
- **但证据强度需收紧**：`C:\dispatcher` **不是 git 仓库**、全盘无改前副本、唯一 `.pyc` 为改动后重新编译（mtime 12:08:19）⇒ ★**无改前版本可作逐字节 diff**。故准确表述为「依据**源码内证 ＋ 功能实证**判定该次改动与头像分支无交集」，**不等于「已证明头像分支一字未改」**。若需更强结论，须外部提供改前文件副本。

### 订正三：watchdog 为瞬时进程（判据口径）

watchdog **不常驻**属设计使然（源码头注释载明「watcher 掉线就拉起、重复实例只保留一个」，退出码无论是否拉起均为正常）⇒ ★**「进程表中未见 watchdog 进程」不可作为「已停用」的判据**；判「是否停用」须取计划任务 `Last Run`／`Last Result` ＋ 取样实证（核验方以 60 秒逐秒取样于 17:12:48 实捕 PID 50748，与任务 `Last Run=2026/11/12 17:12:47`、`Last Result=0` 相互印证）。

### 本轮其它事实补充

- **同名近似程序并存**：`C:\DeepSeek Bot\avatar-states\avatar_watcher.py`（PID 36812，dsh runtime 的 pythonw）与 M-team 自建装置 `C:\dispatcher\mman_avatar_watcher.py`（PID 12728）**仅一字之差** ⇒ 后续任何核验必须**按可执行路径区分**，否则易互相误算。
- **两份交付报告落库时点**：独立核验报告 §1.6 旁证栏记「两单的交付报告均未落库」——该读数对应**核验起始时点（≤17:00）**；本方两份报告于 **17:08 落库**（提交 `7563662`）⇒ 两者**不矛盾**，是时点先后，非事实冲突。

## 六、team lead 独立复核签收（2026-11-12 17:23:41–17:26；v1.2）

### 6.0 复核范围与纪律

★**读时点 2026-11-12 17:23:41–17:26（实时取数，非引用核验方读数）**；★**全程只读**：计划任务信息查询、进程枚举、状态文件读取、日志读取、源码定位、git 只读、CI 查询 ⇒ ★**未改代码、未启用或禁用计划任务、未停或启进程、未改配置、未删文件、未重启宿主桌面端、未裸改 `avatar_watch_state.json`、未改 STATUS.md／DECISIONS.md、未触碰他人交付物**；★**本节不新增缺陷单、不改母报告 `DB-005R-report-m-team.md`**（G-11 链路与档位不变）。

### 6.1 对核验方六项实测之逐项复跑（我方 17:24–17:26 独立取数）

| 项 | 核验方所记 | 我方 17:24–17:26 复跑实测 | 判定 |
|---|---|---|---|
| ① 计划任务 | Ready／Enabled、每 5 分钟一轮 | `State=Ready`、`Settings.Enabled=True`、Trigger `2026-11-08T10:37:46+08:00`、`Repetition interval=PT5M`、LastRun `2026/11/12 17:22:47`、LastResult `0`、NextRun `17:27:46` | 成立 |
| ② 进程 | M-team 自建 watcher PID 12728（CreationDate 11-11 09:12:48）；watchdog 瞬时进程 17:12:48 实捕 PID 50748；他方同名程序 PID 36812 | PID 12728 在跑：`C:\Python314\pythonw.exe C:\dispatcher\mman_avatar_watcher.py`、CreationDate `2026/11/11 9:12:48`；PID 36812 为他方装置：`C:\Users\linsa\.dsh\...\pythonw.exe C:\DeepSeek Bot\avatar-states\avatar_watcher.py`、CreationDate `2026/11/11 9:12:45`；★**PID 50748 当前不在进程表（瞬时进程已退出，与设计使然一致，非停用）** | 成立 |
| ③ 状态文件推进 | 17:00:27 → 17:11:11 | mtime `2026-11-12 17:24:32`、1,948 B、`updatedAt=1794475472531`（＝17:24:32，与 mtime 一致）、`source=mman_avatar_watcher` | 成立且持续推进 |
| ④ 扫描器分支 | `avatar_pending`（149–171）、`pending_avatars`（298–305）、`fire avatar`（333–347）完整存在、无禁用开关或跳过头像的守卫、12:08 改动落在独立分支 D | mtime `2026-11-12 12:08:18`、407 行；`def avatar_pending` 在 149 行；`pending_avatars = []` 在 298 行且其构造与 `AVATAR_COOLDOWN_MIN`、SELF_BOT 空闲态守卫原样在 298–305；`fire avatar` 日志字面在 344 行（fire 块 333–347）；日周期复判分支标记在 198／291／292／314／371 行；★**全文检索 disable／DISABLE／skip_avatar／no_avatar／AVATAR_OFF／watcher_off 命中皆 0 ⇒ 无任何停用开关** | 成立 |
| ⑤ 日志 | `fire avatar` 计数 38→39、末条 17:01:30、近 30 分钟 tail 非全为无待办 | 全日志 `fire avatar` 计数 **39**；末条 L871 `[2026-11-12 17:01:30] fire avatar __avatar__:bot_0ba138cd0596:avatar-cbab946b1ea239c9…`（status=launched、runId=fire_ffcb9fa3557b）；★**我方 17:25:01 读时点近 30 分钟（≥16:55）共 6 行＝fire avatar 1 行＋quiet 5 行 ⇒ 「tail 全为无待办」不成立** | 成立 |
| ⑥ CI | run 43189986103 completed·success | CI run 43189986103 ⇒ status=completed、conclusion=success、headSha=`eb075c6c3d5791ddeaeef984860ea2199b54d855`、workflow CI | 成立 |

### 6.2 一处活态更新（核验窗口之后的新事实，据实补记）

★**近 30 分钟 tail 的 `avatars` 值已由 0 变 1**：`16:56:30`／`17:06:30` 两行为 `avatars=0`，而 ★**`17:11:30`／`17:16:30`／`17:21:30` 三行为 `avatars=1`**（读时点 17:25:01）⇒ 待办中存 1 条未消费的头像待办；★**成因与核验方 §3③ 同一机制**（会话处于 streaming 态 ⇒ watcher 写 todo ⇒ 扫描器下一轮派单）——★**核验方 §3③ 所载「核验者自身会话活动即触发源之一」在我方复跑窗口内再次复现**；★**该现象不构成异常、不改判任何结论**；★**同时再次印证：「近 N 分钟 tail 的 `avatars` 值」属活口径、成立范围仅限各自读时点**（本报告 v1.1 订正一已就同类口径立界）。

### 6.3 档位关系之认定（据实并列，不代裁）

★**核验报告首行「通过」＝本次独立只读核验取证完整**；★**本报告首行「有条件通过」＝本方对本单档位的判断**（本方对「授权撤回 vs 执行失败」的档位判断）⇒ ★**二者事实层一致、不构成冲突**（核验方亦已据实并列）⇒ ★**两报告结论档位均不变**；★**watcher 存废与两单最终档位属 PM／用户裁量，本方不代裁**，★**已据实上报 Mr.Linsang**；★**在其裁定前现态维持不变**（任务与 watcher 仍在运行、DISABLE 四步仍未执行）——★**任何执行动作须待明确授权，不得因核验通过而径行执行停用**。

### 6.4 核验方 §3 未尽事项六条之处置（逐条归属）

| 核验方 §3 条 | 处置 | 归属 |
|---|---|---|
| ① 12:08 改动无改前版本可逐字节 diff | ★**不可补测（客观不可得）**：准确表述已由本报告 v1.1 订正二收紧为「源码内证＋功能实证」；★**若需更强结论须外部提供改前文件副本** | 需外部材料，非我方可派 |
| ② 「重复下发 7 次、只烧回合、无收益」成本问题在现配置下仍可能复现 | ★**属产品取舍**（是否保留该分支）⇒ 本方不代裁、不改配置 | PM／用户裁决 |
| ③ 核验者自身会话活动即触发源之一 | ★**机制正常运转**，非故障；★**我方 17:2x 复跑窗口内再次复现**（avatars 由 0 变 1），已作 6.2 活态补记；★**方法论留存**：判读「近 N 分钟无派单」类活口径须先排除自身会话活动 | 已闭合（口径已立） |
| ④ 同名近似程序并存 | ★**口径已立**：后续任何核验按**可执行路径**区分（`C:\dispatcher\mman_avatar_watcher.py` 属 M-team；`C:\DeepSeek Bot\avatar-states\avatar_watcher.py` 属他方） | 已闭合（口径已立） |
| ⑤ watchdog 为瞬时进程 | ★**判据口径已立**：取计划任务 Last Run／Last Result ＋ 取样实证，★**不可仅凭进程表缺失判「已停用」**（已由 v1.1 订正三固化） | 已闭合（口径已立） |
| ⑥ 两单交付报告落库时点先后 | ★**已注明且不构成冲突**（核验起始 ≤17:00 的读数 vs 本方 17:08 落库 `7563662`） | 已闭合 |

### 6.5 是否再派定向实测之裁定

★**本方不再派定向实测**：① 客观不可得（无改前副本）、③ ④ ⑤ ⑥ 已由 v1.1 订正与本节闭合、② 属 PM／用户裁决而不属实测范围 ⇒ ★**除非 PM／用户另有指示或外部提供改前副本，本线不再新增实测**；★**后续仅在 watcher 存废获裁定、且需执行相应步骤时，按新授权另开单**，不并入本线。

### 6.6 本节边界与落库说明

只读复核与签收，★**未产生缺陷单、未改 STATUS.md／DECISIONS.md、未改母报告 `DB-005R-report-m-team.md`**（G-11 链路与档位不变）、**未触碰他人交付物**；★**落库时 main 已由并行会话前移（他人头像同步交付件），本件以新增补记方式落库、落库后 main 继续前移属并发正常**。

---

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-12 | MMMan | 首次入库（撤销单核验与现态证据） |
| v1.1 | 2026-11-12 | MMMan | 补 §五：独立核验（缺陷闭环，eb075c6）回报与逐条订正三处；结论档位不变 |
| v1.2 | 2026-11-12 | MMMan | 补 §六：team lead 独立复核签收（17:23:41–17:26 复跑六项逐项成立 ＋ avatars 活态由 0 变 1 补记 ＋ 档位关系认定 ＋ 未尽事项六条逐条处置 ＋ 不再派定向实测）；结论档位不变 |
