通过

# AVATAR-WATCHER 撤销／留用状态独立只读核验报告（缺陷闭环）

| 项 | 内容 |
|---|---|
| 任务单 | `docs/pm/orders/AVATAR-WATCHER-DISABLE.md`（11-11 20:08 用户拍板停用）与 `docs/pm/orders/AVATAR-WATCHER-KEEP.md`（11-11 20:19 用户改主意、撤销停用、继续留用）；两单执行方均为 MMMan |
| 本次性质 | **独立只读核验**，为 MMMan 结单提供第二事实源；**不做最终裁决** |
| 执行 | 缺陷闭环（M-team，独立核验方） |
| 核验窗口 | 2026-11-12 17:00:28 — 17:13 +0800（Asia/Shanghai） |
| 调查时点 HEAD | `cc960e5`（核验期间工作区干净、0/0）；**提交时 main 已由并行会话前移至 `554c2a3`**（他人他单之 AVATAR-SYNC13 补记），本报告为**新增一件**、未改动任何既有件 |
| 报告版本 | v1.0 |
| 会话标识 | `mvs_63ff5b7f82064641a13f2d31f6bc8926` |

---

## 0. 结论

**通过。** 本次核验的 6 项实测全部取到实测输出（命令与关键行见 §1），KEEP 单 §三 的 3 项核验**逐项成立**，DISABLE 单 §二 的 4 步**均未见被执行**。本结论仅指「本次独立只读核验取证完整、KEEP 三项核验成立、DISABLE 四步未执行」这一事实层判断，**不替代 MMMan 结单裁决，亦不替代 PM／用户对 watcher 存废的产品决策**。

结论所依赖的两项证据边界已在 §3 登记：① 12:08 那次改动**无改前版本可作逐字节 diff**（详见 §1.4 局限说明）；② 头像派单分支在核验窗内**实测触发过一次**（17:01:30），故「最近 30 分钟 tail 全为无待办」这一预期**不成立**，详见 §1.5。

---

## 1. 逐项实测结果

### 1.1 第 1 项·计划任务状态 —— DISABLE 第 1 步（停用任务）未执行

命令：

    schtasks /Query /TN "\MMMan-DBAIOps-AvatarWatcher" /FO LIST /V

关键行（17:12:48 之后复读）：

    TaskName:                             \MMMan-DBAIOps-AvatarWatcher
    Task To Run:                          C:\Python314\pythonw.exe C:\dispatcher\mman_avatar_watchdog.py
    Status:                               Ready
    Scheduled Task State:                 Enabled
    Last Run Time:                        2026/11/12 17:12:47
    Last Result:                          0
    Next Run Time:                        2026/11/12 17:17:46
    Schedule Type:                        One Time Only, Minute
    Repeat: Every:                        0 Hour(s), 5 Minute(s)

核验起始时（17:00 前后）同源读数：Status＝Ready、Scheduled Task State＝Enabled、Last Run Time＝2026/11/12 16:57:47、Last Result＝0、Next Run Time＝2026/11/12 17:02:46。

**判据锚点命中**：DISABLE 第 1 步要求任务状态为 **Disabled**（禁用、保留任务定义）；实测为 **Ready ／ Enabled**，且 Last Result＝0、最近一次运行成功、下一次排程正常推进 ⇒ **该步未执行**。任务定义本身未被删除（`schtasks /Query` 可正常查到），与「禁用而非删除」的约定无冲突。

### 1.2 第 2 项·watcher 进程是否在跑 —— DISABLE 第 2 步（停进程）未执行

命令：

    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'avatar_watch|avatar_watcher|avatar-watch' } |
      Select-Object ProcessId, Name, CreationDate, ExecutablePath, CommandLine

关键输出（17:00 前后）：

    ProcessId : 36812
    Name      : pythonw.exe
    CreationDate : 2026/11/11 9:12:45
    ExecutablePath : C:\Users\linsa\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe
    CommandLine    : "...\pythonw.exe" "C:\DeepSeek Bot\avatar-states\avatar_watcher.py"

    ProcessId : 12728
    Name      : pythonw.exe
    CreationDate : 2026/11/11 9:12:48
    ExecutablePath : C:\Python314\pythonw.exe
    CommandLine    : C:\Python314\pythonw.exe C:\dispatcher\mman_avatar_watcher.py

**路径区分（按任务单要求，不混）**：

| PID | 程序路径 | 归属 | 判定 |
|---|---|---|---|
| 12728 | `C:\dispatcher\mman_avatar_watcher.py` | **M-team 自建装置**（即 DISABLE 第 2 步指名要停的那个进程） | **在跑**，起于 11-11 09:12:48，与任务单所载 PID 一致 |
| 36812 | `C:\DeepSeek Bot\avatar-states\avatar_watcher.py`（dsh runtime 的 pythonw） | 其它程序，**非** M-team 自建装置 | 与本单无关，不得计入 |

watchdog 侧补测（它为瞬时进程，非常驻）：

    # 60 秒逐秒取样（只读）
    for ($n=1; $n -le 60; $n++) { Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'mman_avatar_watchdog' } ... }

    HIT@17:12:48 PID=50748 CREATED=11/12/2026 17:12:47

即 17:12:47 由计划任务拉起 watchdog（PID 50748）并即时退出；任务侧 `Last Run Time＝2026/11/12 17:12:47`、`Last Result＝0` 相互印证。watchdog 自身日志 `C:\dispatcher\mman_avatar_watchdog.log` 全文仅两行：

    2026-11-11 09:07:49 launched watcher pid=20388
    2026-11-11 09:12:48 launched watcher pid=12728

其源码头注释载明设计为「watcher 掉线就拉起、重复实例只保留一个」「退出码：正常（无论是否拉起）」⇒ 11-11 之后未再拉起，是因为 PID 12728 一直存活，**不是**因为 watchdog 停了。

**判据锚点命中**：DISABLE 第 2 步要求「进程表中无 watcher/watchdog 进程」；实测 watcher **在跑**（PID 12728）且 watchdog 每 5 分钟被计划任务拉起并成功退出 ⇒ **该步未执行**。

> 口径提示：watchdog 设计为瞬时执行，**进程表中未见其常驻不能作为「已停用」的判据**；本项判据取「计划任务 Last Run／Last Result」＋「60 秒取样实捕」。

### 1.3 第 3 项·`avatar_watch_state.json` 的 mtime 是否在推进 —— 成立

命令：

    Get-Item C:\dispatcher\avatar_watch_state.json | Select-Object LastWriteTime, Length

两次读数（间隔 ≥ 10 分钟）：

| 读数 | 读出时刻 | LastWriteTime | Length |
|---|---|---|---|
| READ1 | 2026-11-12 17:00:28 | 2026-11-12 17:00:27 | 1,938 B |
| READ2 | 2026-11-12 17:11:12 | 2026-11-12 17:11:11 | 1,918 B |

间隔 10 分 44 秒，**mtime 由 17:00:27 推进至 17:11:11**；文件内 `updatedAt` 字段为 1794474104400 ＝ **2026-11-12 17:01:44**，`source`＝`mman_avatar_watcher` ⇒ **watcher 正常轮询并持续写盘**，推进成立。

### 1.4 第 4 项·扫描器头像派单分支是否仍启用 —— 启用，且本窗内实际触发过一次

文件：`C:\dispatcher\mman_dbaiops_quiet_scan.py`，**mtime 2026-11-12 12:08:18**，18,420 B。

头像相关行号（源码实读，`Select-String -Pattern 'avatar'`）：

    35 : AVATAR_STATE = os.path.join(HERE, "avatar_watch_state.json")
    47 : AVATAR_COOLDOWN_MIN = 10
    149: def avatar_pending():
    150:     st = load_json(AVATAR_STATE, {})
    288:     avatars = avatar_pending()
    298:     pending_avatars = []
    299:     for it in avatars:
    300:         key = "__avatar__:%s:%s" % (it["bot"], it["avatar"])
    301:         if cooled(fired, key, AVATAR_COOLDOWN_MIN, now):
    305:         pending_avatars.append((it, key))
    314:     if not (pending_orders or pending_avatars or pending_daily or pending_rejudge):
    315:         log("quiet: 无待办（orders=%d avatars=%d daily_missing=%s）" ...)
    333:     for it, key in pending_avatars:
    344:         log("fire avatar %s -> %s" % (key, json.dumps(r, ensure_ascii=False)))

要点：

1. **分支完整、无禁用开关**：`avatar_pending()`（149–171）逻辑完整；派单段（333–347）完整；源码中**不存在**任何针对头像的禁用常量、`AVATAR_ENABLED=False` 之类开关或「跳过头像」的分支守卫 ⇒ **当前形态为启用**。
2. **12:08 那次改动落在何处（不凭时间戳断言）**：源码内证为独立分支标记 —— 288 行后为

       # D. 日周期复判回填（11-12 / 11-15）
       rejudge = rejudge_pending(today)
       pending_rejudge = bool(rejudge) and now.hour >= REJUDGE_AFTER_HOUR and not cooled(...)

   而 298–305 行前的注释为 `# B. 头像待办（MMMan 自身只在空闲态触发，避免唤醒循环）`、`# C. 连驱当日记录缺失`、`# A. 新任务单` ⇒ 12:08 改动是**新增 D 段（日周期复判回填）**，与 B 段头像待办**代码路径分离**；且 `pending_avatars` 的构造条件、`AVATAR_COOLDOWN_MIN`、`fire avatar` 日志行**均原样存在**。
3. **功能实证（最强证据）**：核验窗内 17:01:30 日志出现一条 `fire avatar`，即头像派单分支**实际执行并成功派单**（见 §1.5）⇒ 分支非「存在但被关掉」。
4. **★局限（据实声明）**：**无 12:08 改前版本可作逐字节 diff** —— `C:\dispatcher` **不是 git 仓库**（`git rev-parse --is-inside-work-tree` → fatal: not a git repository，且无 `.git`）；全盘递归检索仅得当前这一份 `mman_dbaiops_quiet_scan.py`，**无备份副本**；唯一编译产物 `C:\dispatcher\__pycache__\mman_dbaiops_quiet_scan.cpython-314.pyc` 的 mtime 为 **12:08:19**，系该次改动后**重新编译**所得，**不含改前源码**；DB-AIOps 仓库内亦无该脚本副本（grep 命中项全为报告／任务单正文中的文字提及）。⇒ **「12:08 改动与头像分支无关」这一判断，依据为源码内证（分支标记与代码路径分离）＋功能实证（17:01:30 触发），而非前后版本逐字节 diff**；diff 不可得的原因与影响已登记于 §3.1。

### 1.5 第 5 项·`mman_quiet_scan.log` 的 fire avatar 行与近 30 分钟 tail

文件：`C:\dispatcher\mman_quiet_scan.log`，核验起始 mtime 2026-11-12 16:56:30，79,797 B，共 870 行。

**fire avatar 行**：核验**开始时**计数 **38**，末条为

    [2026-11-11 21:01:30] fire avatar __avatar__:bot_0ba138cd0596:avatar-cbab946b1ea239c9d59d76a1...b4.gif -> {"ok": true, "status": 200, "body": ... "status": "duplicate", "runId": "fire_f9ae602997d1" ...}

核验**窗内新增 1 条**，结束时计数 **39**，末条为

    [2026-11-12 17:01:30] fire avatar __avatar__:bot_0ba138cd0596:avatar-cbab946b1ea239c9d59d76a1...b4.gif -> {"ok": true, "status": 200, "body": ... "status": "launched", "runId": "fire_ffcb9fa3557b", "pid": 54140 ...}

**最近 30 分钟 tail（16:31:30 — 17:06:30）**：以「无待办（orders=0 avatars=0 daily_missing=False）」为主，共 7 行 quiet（16:31:30／16:36:30／16:41:30／16:46:30／16:51:30／16:56:30／17:06:30）；**但并非全部为无待办** —— 其间含 17:01:30 一条 `fire avatar`。全文件中 `avatars=0` 行累计 788 条。

⇒ **任务单「最近 30 分钟 tail 仍为无待办（avatars=0）」这一预期不成立**：该窗内确有 1 次头像派单发生。触发来源可解释且非故障 —— 17:01:30 前后本核验会话（缺陷闭环 bot，`bot_0ba138cd0596`）正处于 streaming 态，watcher 据此写入 todo，扫描器在下一次轮询（17:01:30）即派单，17:06:30 复归 quiet（todo 被消费）。

### 1.6 第 6 项·结论性判断

**DISABLE 单 §二 的 4 步是否曾被执行：4 步均未见执行。**

| DISABLE 步骤 | 要求 | 实测 | 判定 |
|---|---|---|---|
| 1 停用计划任务 | 任务 Disabled | Status＝Ready、State＝Enabled、Last Result＝0、5 分钟排程推进 | **未执行** |
| 2 停掉 watcher/watchdog 进程 | 进程表无 mman_avatar_watcher.py／mman_avatar_watchdog.py | watcher PID 12728 在跑（11-11 09:12:48 起）；watchdog 17:12:47 被任务拉起（PID 50748）并成功退出 | **未执行** |
| 3 停用扫描器头像派单分支 | 仅停用 avatar 分支 | 分支完整存在、无禁用开关，且 17:01:30 实测触发并派单成功 | **未执行** |
| 4 清残留待办 | 仅当第 3 步跳过时需要 | 第 3 步未执行（分支未被停用）⇒ 第 4 步**无需触发**；且该文件由 watcher 持续重写（mtime 推进中），17:00:28 读取时其 `todo` 非空（1 条，内容为缺陷闭环 bot 的 streaming 态条目） | **无需触发** |

旁证：KEEP 单已于仓库落库（`git log --grep=AVATAR` → `271e9ad docs(pm): AVATAR-WATCHER-KEEP 撤销停用 watcher 继续留用（执行：MMMan，用户已改主意）`），且 `3d9f0ed docs(pm): STATUS v1.38 + DEC-008：… watcher 留用…`；两单的交付报告（`AVATAR-WATCHER-DISABLE-report-m-team.md`／`AVATAR-WATCHER-KEEP-report-m-team.md`）在 `docs/pm/reports/` 中**均未落库**，与「交付报告尚未落库」的前提一致。仓库内已有的相关件为 `AVATAR-WATCHER-IDENTIFY-report-m-team.md`（11-11 19:59，结论「有条件通过」）及 AVATAR-SYNC2…SYNC12 系列。

**KEEP 单 §二.3 的 3 项核验是否成立：3 项逐项成立。**

| KEEP 核验项 | 实测 | 判定 |
|---|---|---|
| 计划任务状态为 Ready/Enabled | Ready／Enabled，Last Result＝0，5 分钟排程推进 | **成立** |
| 进程表中有 watcher 进程在跑 | PID 12728，`C:\dispatcher\mman_avatar_watcher.py`，起于 11-11 09:12:48 | **成立** |
| `avatar_watch_state.json` 的 mtime 在推进 | 17:00:27 → 17:11:11（间隔 10 分 44 秒） | **成立** |

---

## 2. 证据汇总

| # | 核验项 | 实测值 | 证据强度 |
|---|---|---|---|
| 1 | 计划任务状态 | Ready／Enabled，Last Run 17:12:47、Result 0、Next 17:17:46 | 直接实测（schtasks 输出） |
| 2 | watcher 进程 | PID 12728 在跑（11-11 09:12:48 起）；watchdog 瞬时进程 PID 50748 于 17:12:48 实捕 | 直接实测（进程表 ＋ 60 秒取样） |
| 3 | 状态文件 mtime | 17:00:27 → 17:11:11（间隔 10 分 44 秒） | 直接实测（两次读数） |
| 4 | 扫描器头像分支 | 完整存在、无禁用开关；12:08 改动落在 `# D. 日周期复判回填` 段；17:01:30 实测触发 | 直接实测（源码行号 ＋ 功能触发）；★前后 diff 不可得（见 §3.1） |
| 5 | 日志 | fire avatar 38→39，末条 17:01:30；近 30 分钟 tail＝7 行 quiet ＋ 1 行 fire avatar | 直接实测（日志计数与尾行） |
| 6 | 结论性判断 | DISABLE 4 步均未执行；KEEP 3 项核验逐项成立 | 由 §1.1–§1.5 直接推得 |

---

## 3. 未尽事项与风险

1. **★12:08 改动无改前版本可作逐字节 diff**（见 §1.4 第 4 点）：`C:\dispatcher` 无版本控制、无备份副本、唯一 .pyc 为改动后重编译。本报告对该改动的判断依据是**源码内证 ＋ 功能实证**，不等于「已证明头像分支一字未改」。若需更强结论，需外部提供改前文件副本。
2. **★头像派单分支实测可触发**：17:01:30 那次派单说明该分支在当前配置下处于工作态。结合 `AVATAR-WATCHER-IDENTIFY-report-m-team.md` 所陈「watcher 当日已重复下发 7 次、只烧回合、无收益」，成本问题在现配置下**仍可能复现** —— 是否继续留用属产品取舍，建议由 PM／用户裁决（本报告不代裁）。
3. **核验者自身即触发源之一**：缺陷闭环 bot 处于 streaming 时 watcher 即写 todo 并触发派单，本窗内那次即由此产生。该行为属机制正常运转，非故障；但意味着「近 30 分钟无 fire avatar」不成立时，须先排除自身会话活动这一成因。
4. **同名近似程序并存**：`C:\DeepSeek Bot\avatar-states\avatar_watcher.py`（PID 36812）与 M-team 自建装置仅一字之差。后续任何核验必须**按可执行路径区分**，否则易把外部程序误算作 M-team 装置（或反之）。
5. **watchdog 为瞬时进程**：其不在进程表常驻属设计使然，判「是否停用」须取计划任务 Last Run／Last Result 与取样实证，不可仅凭进程表缺失。
6. **两单交付报告仍未落库**：DISABLE／KEEP 的 m-team 交付报告在 `docs/pm/reports/` 中均不存在；本报告只提供事实层第二来源，**结单与归档由 MMMan 处置**。
7. **本报告未触及 STATUS.md／DECISIONS.md**：二者仅部门经理可改，本单明确只报事实。

---

## 4. 本次核验的边界（未做之事声明）

全程只读：**未改任何代码、未禁用或启用任何计划任务、未停止或启动任何进程、未修改任何配置、未删除任何文件、未重启宿主桌面端、未裸改 `avatar_watch_state.json`、未写入他人交付物、未改 STATUS.md／DECISIONS.md**。本次仅新增本报告一件（另有一份 UTF-8 提交信息文件写在系统临时目录，不入库）。核验期间未对被观察对象施加任何干预，故上表读数均为自然运行态下的实测值。另：提交时 main 已由并行会话前移至 `554c2a3`（他人他单之 AVATAR-SYNC13 补记），本报告以**新增一件**方式落库，未触碰任何既有件，亦未与其并行改动冲突。

---

## 5. 复现命令清单（只读）

    # 1 计划任务
    schtasks /Query /TN "\MMMan-DBAIOps-AvatarWatcher" /FO LIST /V
    # 2 进程（含路径区分）
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'avatar_watch' } |
      Select-Object ProcessId, Name, CreationDate, ExecutablePath, CommandLine | Format-List
    # 3 状态文件两次读数（间隔 ≥ 10 分钟）
    Get-Item C:\dispatcher\avatar_watch_state.json | Select-Object LastWriteTime, Length
    # 4 扫描器头像分支行号
    Select-String -Path C:\dispatcher\mman_dbaiops_quiet_scan.py -Pattern 'avatar' |
      ForEach-Object { "{0}: {1}" -f $_.LineNumber, $_.Line.Trim() }
    # 5 日志
    Select-String -Path C:\dispatcher\mman_quiet_scan.log -Pattern 'fire avatar' | Measure-Object
    Get-Content C:\dispatcher\mman_quiet_scan.log -Tail 30
    # 6 watchdog 取样
    Get-Content C:\dispatcher\mman_avatar_watchdog.log
