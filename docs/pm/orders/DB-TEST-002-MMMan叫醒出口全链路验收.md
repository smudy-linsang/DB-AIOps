# DB-TEST-002：MMMan 叫醒出口（HTTP Fire）全链路验收

- 任务单：`docs/pm/orders/DB-TEST-002-MMMan叫醒出口全链路验收.md`
- 下达：2026-11-05（America/Los_Angeles）／2026-11-06（Asia/Shanghai）
- 依据：`docs/pm/orders/DB-003-自动链接入.md` 第 5 项（全链路验收）；DB-003 验收记录中「成员须各自具备可验证的叫醒出口」一项，此前 M-team（MMMan）侧缺出口
- 性质：临时测试单，不占用正式 `DB-xxx` 编号；验收结束后归档说明，可保留本文件作证据
- 下达人：MMMan（成员自建验收测试单，用于验证本成员叫醒出口；非 muse 下达，不含产品开发任务）

执行：MMMan

## 背景

DB-003 要把 DB-AIOps 黑板接进自动链。三层机制里，前两层已通：① 发现＝宿主机 `member_poller` 轮询；② 广播＝协调机分发器 + 飞书群。第三层「叫醒」要求每个成员有自己的出口：A 用定时任务 Fire API（`A_AUTORUN_FIRE_URL` / `A_AUTORUN_TOKEN`），O 用 WAKE-O-AUTO，DSMan 用 DSH `/api/bot`。

M-team 侧此前只有「发现」，没有真正能拉起会话的出口。本单验证 MMMan 侧出口：宿主机 `member_poller` 检测到新单后，经 HTTP（Bearer 令牌）调用本机 Fire 服务，由 Fire 服务以 `mcode exec` 拉起一个新会话执行该单。

## 任务（轻量，只验证链路）

本单**不做**产品开发、不改测试断言、不碰 `STATUS.md` / `DECISIONS.md`、不改他人文件、不改产品代码。

1. **必须由自动叫醒拉起，禁止人工代领。**若发现本单是被人工打开会话处理的，如实写明「本轮非自动拉起」，并说明是自动链哪一段失效。
2. 落短报告：`docs/pm/reports/DB-TEST-002-report-m-team.md`，**第一行结论三档之一**（通过 / 有条件通过 / 不通过）。
3. 报告须写明：
   - 本次会话标识（session id）、触发时刻（注明时区）、触发方式（HTTP Fire 出口）；
   - 完整链路各段证据：发现（轮询日志/状态文件）→ 叫醒（HTTP 请求与令牌鉴权结果）→ 执行（本次会话产出）→ 落库（报告 commit SHA）；
   - 结论与缺口：链路哪几段通了、哪几段没通、没通的复通条件。
4. 允许的写入范围仅：`docs/pm/reports/DB-TEST-002-report-m-team.md`（本报告）。如需补证据，证据文件放 `docs/pm/reports/evidence/DB-TEST-002-m-team/`。

## 通过标准

| # | 检查项 | 通过条件 |
|---|---|---|
| 1 | 发现 | `member_poller`（MMMan，DB-AIOps 仓）在本单推入 main 后数轮内检测到 `orders/DB-TEST-002-*.md` |
| 2 | 叫醒 | 经 HTTP + Bearer 令牌调用 Fire 服务成功，被拉起一个新会话（本单即由该会话执行） |
| 3 | 执行 | 该会话读任务单全文并按黑板规范处理 |
| 4 | 落库 | `docs/pm/reports/DB-TEST-002-report-m-team.md` 由该会话写入并推上 main，结论三档齐全 |

任一项失败：对应报告结论不得写「通过」，须记「有条件通过」或「不通过」并列缺口与复通条件。

## 约束

- 令牌/密钥不得写入报告、聊天或仓库。
- 报告与证据一律中文、带版本号。
- 提交纪律：只 `git add -- <我件>` 与 `git commit --only`，不碰他人暂存内容；不改 `STATUS.md` / `DECISIONS.md`。
- 验收结束后本单归档说明由下达方补。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-05 | MMMan | MMMan 叫醒出口（HTTP Fire）全链路验收测试单下达 |
