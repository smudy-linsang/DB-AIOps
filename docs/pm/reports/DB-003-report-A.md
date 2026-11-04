有条件通过

# DB-003 A 部分报告：A-AUTORUN 接入 DB-AIOps 黑板

- 成员：A（Claude Code）｜日期：2026-11-04｜人工会话（非自动会话）
- 范围：任务单第 3 项中 A 的部分。member_poller、分发器、汇总验收归 GorkMan；WAKE-O-AUTO 归 O。本文件只是 A 的分项报告，不替代 `DB-003-report-自动链接入.md`。
- 基线：main `79fc9a3`（立单）→ 本次提交 `799aa94`、`1732fbb`。

## 结论：有条件通过

DB-AIOps 侧的 A 自动领单链已建好并推上 main，Xinchuang 侧零改动。另有两件事待办：真实拉起依赖用户一次性配置两个 Secrets；全链路验收依赖 GorkMan 发测试单。

## 改动位置

| 位置 | 内容 |
|---|---|
| `tools/a_inbox.py` | 收件脚本，移植自 Xinchuang 同名脚本，判定、占用、领单竞争、推送自检逻辑不变。DB-AIOps 侧有以下改动：<br>① 单号改为 `DB-xxx`，返工为 `-Rn`；测试单 `DB-TEST-xxx` 不受起始单号限制。<br>② 起始单号为 DB-004，DB-001～DB-003 不会被自动叫醒。<br>③ 回执不算交付报告：文件名含「回执」或独立成段的 `ack`/`receipt` 即视为回执。否则 A 先回 ETA 回执后，单就被判为已完成。<br>④ 别名表内置，不依赖 member_poller（那部分归 GorkMan）。<br>⑤ 推送自检的放行范围改为 DB-AIOps 目录：`docs/pm/reports|claims` 可改；`monitor/tests_*`、`frontend/src/test/`、`frontend/e2e/` 只许新增。 |
| `.github/workflows/a-autorun.yml` | main 上 `docs/pm/orders/**`、`docs/pm/reports/**` 有推送即跑 `--dispatch`，不调用模型。有就绪单时先推 `claims/<单号>-A.json`（dispatched 状态），再调用定时任务的 Fire API。并发组、机器人名、兜底分钟都与 Xinchuang 错开。 |
| 定时任务「A DB-AIOps 黑板自动领单」（`trig_01XxNfTyVsbjHhUk9zQ8zxo1`） | 新建，只能通过 API 触发，每次拉起新开会话。Xinchuang 的「A 黑板自动领单」未改。 |
| `docs/pm/A-AUTORUN-PROMPT.md` | 上面定时任务所存提示词的副本，两处须一致。提示词加入了 AGENTS.md 的凭据红线和 validate.sh 纪律。 |
| `monitor/tests_pm_a_inbox.py` | 把脚本自测接入 unit 层，跟着 `validate.sh unit` 与 CI 一起跑。 |

## 实测

| # | 检查 | 结果 |
|---|---|---|
| 1 | `python3 tools/a_inbox.py --self-test` | 43/43 通过，覆盖：<br>- 路由、就绪规则、DB 单号与测试单、回执不算报告；<br>- 占用超时、拉起次数上限、推送自检；<br>- 用临时裸仓库模拟两个会话抢同一单：先到者领到，后到者看到占用后放弃。 |
| 2 | 真实黑板 `--list` / `--check` | 无输出，退出码 1，即"无待办"，符合预期：DB-001 未路由到 A，DB-003 低于起始单号。<br>把起始单号临时设为 1 再判：DB-003 被判为 ready，`DB-003-回执-A.md` 未被误当成报告。 |
| 3 | `scripts/validate.sh unit --json` | exit_code 0 |
| 4 | `scripts/validate.sh backend --json`（本地 PostgreSQL 16） | exit_code 0：编译、系统检查、迁移漂移、依赖、文档版本、全量测试均通过 |
| 5 | `scripts/scan_secrets.py` | 通过 |
| 6 | 人工拉起一次新定时任务（`fire_trigger`） | 会话 `cse_01GsGootU4N5HdpqfJaWnGqk` 已启动并在约 33 秒后结束；黑板上没有新的 claims 推送，与"无待办"相符。<br>**本会话看不到那次会话的输出内容**，所以只能证明定时任务能被拉起并自行结束，不能证明它跑到了 `--next`。这一项要等测试单才能确证。 |
| 7 | Actions 工作流 | 本报告推上 main 时会首次触发，运行结果见下方「Actions 首次运行」补记。 |

## 未尽事项（条件）

1. **用户一次性人工步骤（必须）**：
   - 在 claude.ai 的定时任务页打开「A DB-AIOps 黑板自动领单」，开启 API 触发并生成令牌；
   - 在 DB-AIOps 仓库 Settings → Secrets and variables → Actions 新建 `A_AUTORUN_FIRE_URL`（Fire URL）和 `A_AUTORUN_TOKEN`（令牌）。
   - 令牌不要贴到任何会话或黑板。未配置时，工作流遇到就绪单会报"缺少 Secrets"并失败，不会静默。
2. **全链路验收**：请 GorkMan 发测试单时在 `执行：` 行写上 A，单号用 `DB-TEST-001` 形式。A 这一环的通过标准是：
   - `claims/DB-TEST-001-A.json` 依次出现 dispatched、running；
   - 出现 `reports/DB-TEST-001-report-A.md`；
   - 占用文件被删除。
3. **兜底轮询不生效**：GitHub 的 `schedule` 只在默认分支上运行，而本仓库默认分支是 `master`，黑板在 `main`。所以"有推送即触发"有效，"每 2 小时兜底"无效，漏触发时只能等下一次黑板推送，或手动 `workflow_dispatch`（同样只能在默认分支上手动触发）。处理办法二选一，由部门经理或用户定：
   - 把默认分支改为 `main`；
   - 把本工作流同步进 `master`。

## 风险与需部门经理知悉

- **直推 main 的代码目前不跑 CI**：`.github/workflows/ci.yml` 只监听 `master`。本次改动已在本地跑过 unit 与 backend 两道闸，但之后每个直推 main 的成员都只有本地验证这一道闸。
- **给 O 的线索（未核实可用性）**：Xinchuang 仓库里有 `docs/pm/O-AUTORUN.md`、`tools/o_autorun.py`、`tools/wake_o.py`，可能就是 O 回执里找不到的 WAKE-O-AUTO 定义所在，供 O 参考。

## Actions 首次运行

（推送后补记）
