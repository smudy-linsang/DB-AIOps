# DB-TEST-001：自动链全链路验收

- 任务单：`docs/pm/orders/DB-TEST-001-自动链全链路验收.md`
- 下达：2026-11-05（America/Los_Angeles）
- 依据：`docs/pm/orders/DB-003-自动链接入.md` 第 5 项；A/O 分项有条件通过后，用户已配齐 `A_AUTORUN_FIRE_URL` / `A_AUTORUN_TOKEN`
- 性质：临时测试单，不占用正式 `DB-xxx` 编号；验收结束后由 GorkMan 归档说明，可保留本文件作证据

执行：A、O

## 背景

DB-003 要把 DB-AIOps 黑板接进自动链。GorkMan 侧 `member_poller` 双仓并行已存活；A 的 A-AUTORUN、O 的 WAKE-O-AUTO / o-autorun 分项均为有条件通过，条件含「全链路待测试单」。本单即该测试单：验证从落库 → 轮询检测到 → 分发器广播 → A/O 自动叫醒并落报告 → GorkMan 终验的闭环。

## 任务（轻量，只验证链路）

本单**不做**产品开发、不改测试断言、不碰 STATUS/DECISIONS、不碰 DB-001/DB-002、不改 Xinchuang。

1. **A（须由 A-AUTORUN 自动拉起，禁止人工代领）**
   - 接单后先落短回执：`docs/pm/reports/DB-TEST-001-ack-A.md`（或团队惯用「回执」命名），写清：自动会话、接单时间（America/Los_Angeles）、基线 SHA。
   - 再落短报告：`docs/pm/reports/DB-TEST-001-report-A.md`，第一行结论三档之一。
   - 报告须写明：`claims/DB-TEST-001-A.json` 曾出现 dispatched→running（或等价占用态）、本次由 Fire API / a-autorun 拉起、交单后占用已清理。
   - 除 reports/claims 与 A 权限边界内新增测试文件外，禁止改其它路径。

2. **O（须由 o-autorun / WAKE-O-AUTO 自动拉起，禁止人工代领）**
   - 接单后先落 ETA 回执：`docs/pm/reports/DB-TEST-001-ack-O.md`。
   - 再落短报告：`docs/pm/reports/DB-TEST-001-report-O.md`，第一行结论三档之一。
   - 报告须写明：Actions run ID（或等价触发证据）、自动拉起路径、本单为 DB-TEST 临时编号已被正确收取。
   - 不评审 DB-001；不改产品代码。

3. **GorkMan（终验，本单不写入执行行以免误路由）**
   - 核对：宿主机 `member-poller-gg-db` 日志/state 是否检测到本单；分发器/飞书广播是否可见（出口未恢复则如实记缺口，不静默）。
   - 核对 A/O 回执与报告均由自动链落地。
   - 汇总写入 `docs/pm/reports/DB-003-report-自动链接入.md`（DB-003 正式终验），并可选附 `docs/pm/reports/DB-TEST-001-report-gorkman.md`。

## 通过标准（全链路）

| # | 检查项 | 通过条件 |
|---|---|---|
| 1 | 轮询检测 | `member-poller-gg-db`（或等价 DB-AIOps poller）在本单推入 main 后的数轮内检测到 `orders/DB-TEST-001-*.md` |
| 2 | 分发器广播 | 飞书群出现本单广播；若出口未通，GorkMan 在终验中写明可见性核对结果，不得静默 |
| 3 | A 自动叫醒 | a-autorun 触发；出现 A 的 ack/report；claims 占用态流转正常后清理 |
| 4 | O 自动叫醒 | o-autorun 触发；出现 O 的 ack/report |
| 5 | GorkMan 终验 | 写出 DB-003 正式汇总报告，三档结论；本测试单归档说明 |

任一项失败：对应成员报告结论不得写「通过」；GorkMan 终验记「有条件通过」或「不通过」并列缺口。

## 接单规矩

接单确认与 ETA 一律回黑板 `reports/`；飞书群只作广播。本单目标：下达后数小时内 A/O 自动落报告，GorkMan 在证据齐后当日或次日终验。

## 约束

- 不改 Xinchuang 现有链路行为。
- 不碰 DB-001、不占用 DB-002。
- 不改 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`（仅部门经理维护）。
- DB-AIOps 直推 main；若有代码改动须遵守 `scripts/validate.sh`；本单对 A/O 预期仅为 docs/pm/reports（及 A 的 claims）写入。
- 密钥/令牌不得写入报告或聊天。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-05 | GorkMan | DB-003 全链路验收测试单下达 |
