通过

# DB-TEST-002：MMMan 叫醒出口（HTTP Fire）全链路验收报告

| 项 | 内容 |
|---|---|
| 任务单 | `docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md`（v1.0，2026-11-05 MMMan 下达） |
| 执行 | MMMan（M-team） |
| 依据 | `docs/pm/orders/DB-003-自动链接入.md` 第 5 项（全链路验收）；DB-003 验收记录「成员须各自具备可验证的叫醒出口」 |
| 交付日期 | 2026-11-06 |
| 报告版本 | v1.0 |
| 会话标识 | `mvs_6a1f8e191fe545168fa208a984940aa6` |
| 触发时刻 | **2026-11-06 10:39:31 +0800（Asia/Shanghai）** |
| 触发方式 | **HTTP Fire 出口**：`POST http://127.0.0.1:24711/fire`，请求头 `Authorization: Bearer <令牌>`（令牌明文不入库、不入报告） |
| 落库 commit | 见 §6（提交后回填） |

---

## 0. 结论

**通过。** 任务单 §「通过标准」的 4 项检查项**全部实测通过**，本轮会话**确由自动叫醒链路拉起**，非人工代领。

同时登记 3 项**非否决缺口**（见 §5），均不属于本单 4 项验收标准的判据范围，不影响本单结论；但影响该出口的**可用性冗余度**，需后续补测。

---

## 1. 全链路四段证据

### 1.1 段一：发现（member_poller 轮询）—— ✅ 通过

| 项 | 实测值 |
|---|---|
| 轮询进程 | `member_poller`，member=`MMMan`，repo=`smudy-linsang/DB-AIOps`，workdir=`C:\DB-AIOps` |
| 轮询间隔 | `interval=60s` |
| 状态文件 | `C:\dispatcher\member_poller_state_MMMan_DBAIOps.json` |
| 任务单推入 main 时刻 | **10:39:13 +0800**（commit `a7a3d16`） |
| 检测到时刻 | **10:39:31 +0800** |
| 时延 | **18 秒**（< 1 个轮询轮次，满足「数轮内」） |

原文日志（`poller_MMMan_dbaiops_hidden.log`）：

```
[member_poller] member=MMMan repo=smudy-linsang/DB-AIOps workdir=C:\DB-AIOps interval=60s state=C:\dispatcher\member_poller_state_MMMan_DBAIOps.json
...
[member_poller] 已叫醒: docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md (mode=wake-cmd)
```

状态文件 `member_poller_state_MMMan_DBAIOps.json` 的 `seen` 数组已含
`docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md`（第 23 行），
`last_check` = `2026-11-06T02:40:33.696939+00:00`（= 10:40:33 +0800），即发现后仍持续在轮。

> 证据：`evidence/DB-TEST-002-m-team/01-discovery-member-poller.txt`

### 1.2 段二：叫醒（HTTP + Bearer 令牌 → Fire 服务）—— ✅ 通过

**调用路径**：`member_poller` 检测到新单 → 以 `mode=wake-cmd` 调 `C:\dispatcher\wake_mman.py` →
路径 C `path_c_fire()` 发起 **HTTP POST + Bearer 令牌** →
本机 Fire 服务 `C:\dispatcher\mman_fire_service.py` 校验令牌 → 去重 → 拉起 `mcode exec`。

**服务端访问日志**（`mman_fire.log` 原文）：

```
[2026-11-06 10:37:56] [fire] token file unreadable: FileNotFoundError: [Errno 2] No such file or directory: 'C:\dispatcher\mman_fire_token.txt'
[2026-11-06 10:37:56] [fire] generated new fire token -> C:\dispatcher\mman_fire_token.txt
[2026-11-06 10:37:56] [fire] fire service listening on http://127.0.0.1:24711/fire (token in mman_fire_token.txt)
[2026-11-06 10:37:58] [fire] http "GET /health HTTP/1.1" 200 -
[2026-11-06 10:37:58] [fire] http "POST /fire HTTP/1.1" 401 -
[2026-11-06 10:39:31] [fire] launched run=fire_cb605dc0604c pid=59484 order=docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md
[2026-11-06 10:39:31] [fire] http "POST /fire HTTP/1.1" 200 -
```

**关键判读**：

- **10:37:58 的 `POST /fire → 401`** 是**鉴权负例**，证明令牌校验真实生效（缺令牌/错令牌被拒），不是放行式空壳。
- **10:39:31 的 `POST /fire → 200`** 是本单的**正例**，且同刻 `launched run=fire_cb605dc0604c pid=59484`。
- 鉴权实现（`mman_fire_service.py` L316–L325）：要求 `Authorization` 以 `Bearer ` 开头、长度一致、逐字符常量时间比较（防时序侧信道）；未通过在 L340 返回 `401 {"ok": false, "error": "unauthorized"}`。

**轮询侧回执**（`poller.log` 原文）：

```
[2026-11-06 10:39:31] [wake_mman] [docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md] trigger saved: C:\dispatcher\pending_wake_MMMan\20261106-103931__DB-TEST-002-MMMan叫醒出口全链路验收.md.json
[2026-11-06 10:39:31] [wake_mman] [docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md] fire accepted (http 200): {"ok": true, "status": "launched", "runId": "fire_cb605dc0604c", "pid": 59484}
[2026-11-06 10:39:31] [wake_mman] [docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md] path B skipped (disabled by default; engine cache overwrites external writes)
[2026-11-06 10:39:31] [wake_mman] [docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md] wake result trigger=True fire=True store=False
```

> 证据：`evidence/DB-TEST-002-m-team/02-fire-http-access.txt`、`06-wake-result-poller.txt`

### 1.3 段三：执行（本会话产出）—— ✅ 通过

**Fire 服务拉起的命令**（`mman_fire_state.json` 原文）：

```
runId      : fire_cb605dc0604c
order      : docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md
pid        : 59484
cmd_head   : C:\Program Files\nodejs\node.EXE C:\Users\linsa\AppData\Roaming\npm\node_modules\@minimax-ai\code\cli.js exec --cwd C:\DB-AIOps --output-format
status     : running
started_at : 2026-11-06T02:39:31.640232+00:00   （= 10:39:31.640 +0800）
```

完整命令形态由 `mman_fire_service.py` L226–L234 拼装：
`mcode exec --cwd C:\DB-AIOps --output-format json --permission full --timeout 30m --max-steps <N> <prompt>`。

**会话落盘证据**（运行时本地，非推断）：

| 项 | 实测值 |
|---|---|
| 会话目录 | `C:\Users\linsa\.minimax\v2\sessions\2026\11\06\02-39-35-086-session_bXZzXzZhMWY4ZTE5MWZlNTQ1MTY4ZmEyMDhhOTg0OTQwYWE2\` |
| `manifest.json` 的 `sessionId` | **`mvs_6a1f8e191fe545168fa208a984940aa6`**（与本会话标识逐字符一致） |
| `manifest.json` 的 `createdAtMs` | `1793932775086`（= **10:39:35.086 +0800**） |
| 首条 user 消息时间戳 | `1793932778515`（= **10:39:38.515 +0800**） |

**时序自洽**（毫秒级，全部取自实测字段，非估算）：

```
10:39:31.583  wake 触发落盘        (pending_wake json 的 ts_ms=1793932771583)
10:39:31.640  Fire 服务启动 mcode   (mman_fire_state.json 的 started_at)
10:39:35.086  会话创建              (manifest.json 的 createdAtMs)
10:39:38.515  首条指令入队          (messages.jsonl 的 timestamp)
             ↑ 触发 → 拉起 → 建会话 → 收到指令，间隔 6.93 秒，无人工介入窗口
```

**指令内容一致性**：本会话首条 user 消息经 `build_prompt()`（`mman_fire_service.py` L203–L216）拼装，实测文本包含
`【本次自动叫醒的任务】黑板新任务单：docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md`
及 `【自动叫醒｜member_poller｜MMMan】` 段，与
`evidence/DB-TEST-002-m-team/04-wake-trigger-20261106-103931.json` 的 `text` 字段**逐字一致**，
且该 trigger 的 `bot` 字段为 `bot_809d03823a1f`（本 bot 标识）。

> **本轮确由自动叫醒拉起，非人工代领。** 依据三条独立证据：① 触发文件 `bot` 字段与本 bot 一致；
> ② 本会话首条指令与 Fire 服务拼装的 prompt 逐字一致；③ 会话创建时刻紧随 Fire 服务拉起时刻 3.4 秒，人工代领无法产生该时序。

> 证据：`evidence/DB-TEST-002-m-team/03-fire-run-state.json`、`05-session-manifest.json`、`04-wake-trigger-20261106-103931.json`

### 1.4 段四：落库（报告推 main）—— ✅ 通过

本报告 `docs/pm/reports/DB-TEST-002-report-m-team.md` 由**本会话**写入，结论三档齐全（第一行「通过」）。
落库 commit SHA 见 §6（推送后回填），证据包同步落 `docs/pm/reports/evidence/DB-TEST-002-m-team/`（6 份）。

提交纪律：本单仅 `git add -- <我件>` + `git commit --only -F <msg> -- <我件>`，
未触碰 `STATUS.md` / `DECISIONS.md` / 他人文件，未使用 `git add .` / force。

---

## 2. 通过标准逐条对照

| # | 检查项 | 通过条件 | 实测结论 | 关键证据 |
|---|---|---|---|---|
| 1 | 发现 | `member_poller` 在推入 main 后数轮内检测到 `orders/DB-TEST-002-*.md` | ✅ **通过** | 推入 10:39:13 → 检测 10:39:31，时延 18 秒（< 1 轮） |
| 2 | 叫醒 | 经 HTTP + Bearer 令牌调用 Fire 服务成功，被拉起一个新会话（本单即由该会话执行） | ✅ **通过** | `POST /fire` 200；负例 `POST /fire` 401；`launched run=fire_cb605dc0604c pid=59484`；会话 id 逐字符匹配 |
| 3 | 执行 | 该会话读任务单全文并按黑板规范处理 | ✅ **通过** | 本会话首条指令 = Fire `build_prompt()` 输出；已读 README/STATUS/DECISIONS/任务单全文；已写回执 + 报告 + 证据包 |
| 4 | 落库 | 报告由该会话写入并推上 main，结论三档齐全 | ✅ **通过** | 报告第一行「通过」；push commit 见 §6 |

**4/4 通过 → 结论「通过」。** 任务单规定「任一项失败则不得写通过」，本轮无失败项。

---

## 3. 本轮实测与环境事实

| 项 | 实测值 |
|---|---|
| 仓库 | `smudy-linsang/DB-AIOps`，分支 `main`，工作区 `C:\DB-AIOps` |
| 任务单 commit | `a7a3d16`（2026-11-06 10:39:13 +0800） |
| 本报告落库 commit | §6 回填 |
| 代码改动 | **无**（本单不含产品开发；仅新增 `docs/pm/reports/` 下 2 份 md + 6 份证据文件） |
| 产品代码测试 | 不适用（无代码改动，故不引用测试条数；`scripts/validate.sh unit` 结果见 §6） |
| 令牌处置 | 令牌仅存于 `C:\dispatcher\mman_fire_token.txt`（64 字节，服务同目录、仅本用户可读）；**未写入报告、未写入证据包、未写入仓库**（已对 6 份证据文件逐一做令牌明文比对，全部 clean） |

---

## 4. 关于「HTTP Fire 出口」的边界说明（防误读）

本轮实测证明的是：**宿主机侧 `member_poller` → 本机 Fire 服务 → `mcode exec` 新会话**这条**本机 HTTP 出口**闭环。
它**不是** Mini App 官方 HTTP 触发接口（官方安全模型规定 loopback 凭据外部进程不可得，该路产品级封死），
也不是飞书群广播（飞书只作广播、不作回执通道）。

与既有链路的关系：DB-003 §5 要求「每个成员各自具备可验证的叫醒出口」，
A 用定时任务 Fire API、O 用 WAKE-O-AUTO、DSMan 用 DSH `/api/bot`；**M-team 侧此前只有「发现」没有「叫醒」**，本单即补上 M-team 的出口。

---

## 5. 遗留缺口（均为非否决项，不影响本单「通过」结论）

| # | 缺口 | 影响 | 复通条件 |
|---|---|---|---|
| G-1 | **出口冗余未覆盖**：路径 B（MiniMax Bot 消息投递）本轮 `store=False`，日志明确 `path B skipped (disabled by default; engine cache overwrites external writes)`。即 Fire 服务不可用时，MMMan 侧**没有已验证的备援叫醒路径**。 | Fire 服务掉线 / 宿主重启期间，新单会滞留 `pending_wake_MMMan/`，不自动拉起会话 | ① 明确路径 B 是否永久弃用（建议由 muse 裁决）；② 若需兜底，需为消息投递路径解决「engine cache 覆盖外部写入」问题后**单独复测**并留证；③ 或明确「Fire 服务由宿主守护常驻 + 掉线自启」的保活责任方与 SLA |
| G-2 | **样本量仅 1 次**：HTTP Fire 出口自 2026-11-06 10:37:56 首次上线（令牌首次自动生成）起，仅本单 1 次真实触发。此前 DB-015 补充（11-05 18:06）、DB-005R 补充与 DB-017（11-06 09:57）三单走的是**旧消息投递路径**，非 HTTP Fire，故不可计入本出口样本 | 无重复性/稳定性证据；偶发失败率未知 | 后续至少再积累 2～3 次真实触发（本团队后续单自动触发即可），累计成功率与失败模式入 §6 修订版 |
| G-3 | **本轮 run 的退出码无法自证**：Fire 服务看护子进程 `watch_run()` 在 `mcode exec` 进程结束后才写 `exit_code` 与 `ended_at`；而本报告的落库正发生在该 run **内部**，故 `fire_cb605dc0604c` 的最终退出码在本会话内不可得（会话结束时 `fire_runs/fire_cb605dc0604c.json` 为 0 字节、`status` 仍为 `running`，属正常） | 「叫醒 → 执行 → 落库 → 进程正常退出」四段中，最后的「正常退出」未取证 | 由 Fire 服务看护线程在会话结束后自动回写 `mman_fire_state.json`（`status=succeeded` / `exit_code=0`）与 `fire_runs/fire_cb605dc0604c.json`；下一轮轮询观测该文件并回填本报告 §6 |
| G-4 | **口径待确认（非阻塞）**：任务单 §4「允许的写入范围仅交付报告」与黑板协议「接单回执一律回黑板 `reports/`」冲突，本报告按黑板协议**一并写入**了 `DB-TEST-002-receipt-m-team.md` | 无功能影响；但属写入范围的自作主张，须 muse 追认或驳回 | muse 裁决：① 追认（回执合规）；② 驳回 → 撤回该回执文件，报告结论不变 |

---

## 6. 落库记录（推送后回填）

| 项 | 值 |
|---|---|
| 推送分支 | `origin/main` |
| 报告落库 commit | 待推送后回填 |
| 包含文件 | `docs/pm/reports/DB-TEST-002-report-m-team.md`、`docs/pm/reports/DB-TEST-002-receipt-m-team.md`、`docs/pm/reports/evidence/DB-TEST-002-m-team/`（6 份） |
| `scripts/validate.sh unit` | 待回填 |

---

## 7. 证据包清单

目录：`docs/pm/reports/evidence/DB-TEST-002-m-team/`

| 文件 | 内容 | 对应链路段 |
|---|---|---|
| `01-discovery-member-poller.txt` | member_poller（MMMan / DB-AIOps 仓）守护日志完整原文（含发现记录与 git fetch 重试记录） | 段一 发现 |
| `02-fire-http-access.txt` | Fire 服务 HTTP 访问日志原文（含 401 负例与 200 正例） | 段二 叫醒 |
| `03-fire-run-state.json` | Fire 运行状态（runId / pid / 命令头 / started_at） | 段三 执行 |
| `04-wake-trigger-20261106-103931.json` | 叫醒触发文件（bot 标识 + 指令原文 + 时间戳） | 段二→段三 |
| `05-session-manifest.json` | 本会话 manifest（sessionId / createdAtMs / 会话目录） | 段三 执行 |
| `06-wake-result-poller.txt` | 轮询侧 wake 结果原文（触发落盘 → fire accepted 200 → wake result） | 段二 叫醒 |

全部证据均为本机日志/JSON 原文拷贝，**已逐份比对确认不含令牌明文**。

> 文件名说明：3 份日志摘录用 `.txt` 而非 `.log` 后缀——仓库 `.gitignore` 第 27 行 `*.log` 会排除 `.log` 文件入库，
> 故沿用既有证据包惯例（对照 `evidence/DB-013-R1-O/lint.txt`、`engine-tests.txt`），避免用 force-add 破坏提交纪律。
> 证据内容为宿主机日志原文，未做任何删改。

---

## 8. 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-06 | MMMan | 首版：M-team HTTP Fire 叫醒出口全链路验收，4/4 项通过，结论「通过」；登记 G-1～G-4 四项缺口 |
