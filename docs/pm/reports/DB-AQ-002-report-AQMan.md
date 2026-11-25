通过

# DB-AQ-002 交付报告：MySQL 方言时序敏感测试加固

- 任务单：`docs/pm/orders/DB-AQ-002-AQMan时序敏感测试加固.md`（PM 2026-11-25 派单）
- 执行：AQMan（AWS Quick）。代码由 AQMan 修改；本机验证由编码代理 Kiro 在用户设备 Smudy 上执行，AQMan 核对 Kiro 的原始输出
- 成文：2026-11-25 CST（v1.0 17:52，v1.1 17:5x 补推送后 CI）
- 基线：开工前 17:42:22 自动 `git pull --ff-only`，b98a266 → **0945b07**（拉到本任务单、STATUS v1.61、ROSTER v1.2）
- 口径：只记实测，推断标注「推断」；本报告不含任何密码、token、密钥

## 一、结论

**通过。**
- 改动严格按任务单执行：只改了一个工作量数字，断言没有任何改动。
- 本机方言套件整套跑了 3 轮，全部通过；unit 回归通过。
- 扫描与点查的总耗时比，从改动前的 1.25–1.73 拉大到 6.57–7.63。
- 推送后 CI（提交 bdae978）7 个 job 全部 success，包括静态检查（ruff）、方言测试（MySQL/PG）和方言测试（Oracle）。
- v1.0 唯一的条件「推送后 CI 待复核」已在 v1.1 关闭（见 §五）。

需要说明的是：3 轮全过、余量扩大约 5 倍，只能说明偶发失败的概率大幅下降，**不能证明问题「已彻底消除」**。Kiro 在总结里用了「有效消除」，这个说法超出了证据，不予采信。

## 二、改动（diff 摘要）

只改了 `monitor/tests_dialect.py` 一个文件，共 1 行：

```diff
@@ MySQLSlowSqlDialectTests.test_live_fallback_reports_real_seconds_and_sorts_by_total
-        self._run('SELECT COUNT(*) FROM sqlstat_probe WHERE pad LIKE CONCAT(%d, "%%")', 4)
+        self._run('SELECT COUNT(*) FROM sqlstat_probe WHERE pad LIKE CONCAT(%d, "%%")', 20)
         self._run('SELECT id FROM sqlstat_probe WHERE id = %d', 50)
```

| 任务单要求 | 执行情况 |
|---|---|
| 全表扫描 4 → 20 次，点查保持 50 次 | ✅ L294 由 4 改为 20；L295 的 50 未动 |
| 两处断言不改（L305 总耗时比较、L307 按总耗时排序） | ✅ 未改 |
| 不改为比较平均单次耗时 | ✅ 未改 |
| 不在 setUp 里清空 performance_schema | ✅ setUp 未动 |

另有一处如实说明：L306 断言的提示文字还是「全表扫描 4 次的总耗时应大于主键点查 50 次」，和现在的执行次数（20 次）对不上。这句文字属于断言语句，任务单要求不改断言，所以我没有动。要不要顺手改成「20 次」，请 PM 裁决。

验证之前，Kiro 复核过 `git diff --stat` 和 `git diff`，确认只有这一处改动。

## 三、本机验证

环境与变量按 AQMAN-ENV 报告 §六（MySQL/PG 方言口径，不设 DJANGO_SETTINGS_MODULE / POSTGRES_DB / DIALECT_ORACLE_DSN）。每轮开始前都先 `docker restart aqman-mysql80-test`，再只读确认 performance_schema 里没有任何 sqlstat_probe 摘要。

### 3.1 方言套件整套 3 轮（任务单要求至少 3 轮）

| 轮次 | 整套结果 | 目标用例 | 扫描总耗时 | 点查总耗时 | 总耗时比 | COUNT_STAR（扫描/点查） |
|---|---|---|---|---|---|---|
| 1 | `Ran 31 tests in 88.897s OK (skipped=11)` | ok | 0.035716s | 0.004679s | 7.63 | 20 / 50 |
| 2 | `Ran 31 tests in 88.817s OK (skipped=11)` | ok | 0.037413s | 0.005020s | 7.45 | 20 / 50 |
| 3 | `Ran 31 tests in 85.926s OK (skipped=11)` | ok | 0.033520s | 0.005104s | 6.57 | 20 / 50 |

- 3 轮里，MySQLSlowSqlDialectTests 的 3 个用例全部 ok。
- COUNT_STAR 每轮都正好是 20 和 50，和代码一致，没有混入其他语句。
- 11 个 skip 与改动前相同，都是因为 DM、GBase、Oracle 的 DSN 未设置，与 CI 的 test-dialect job 一致。
- 日志放在仓库外：`C:\AWSQuick\.aqman\aq002-dialect-1..3.log`。

### 3.2 与改动前对照（改动前数据见 AQMAN-ENV 报告 §五）

| 条件 | 整套轮次 | 总耗时比范围 | 最小比 | 失败 |
|---|---|---|---|---|
| 改动前（扫描 4 次） | 3（另有首跑失败 1 次，比值 0.99） | 1.25–1.73 | 1.25 | 4 次中 1 次 |
| **改动后（扫描 20 次）** | **3** | **6.57–7.63** | **6.57** | **3 次中 0 次** |

点查总耗时基本没变（约 4.7–5.1ms），扫描总耗时随次数线性增长，从约 7–11ms 升到约 34–37ms。（推断）改动前失败的那一次，是点查慢到了 8.0ms；按改动后的数据，点查要慢到正常的 6.5 倍以上，大小关系才会翻转。

### 3.3 后端 unit 回归（任务单要求）

`DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_unit`，加 workflow 级 5 个变量：`Ran 467 tests in 54.880s OK`，0 失败、0 错误，用例数与改动前的 467 一致。

### 3.4 静态检查（任务单没有要求，提前对齐 CI 的 static job）

| 检查 | 结果 |
|---|---|
| `python -m compileall -q monitor dbmonitor` | exit 0，无输出 |
| `python scripts/lint_redlines.py` | exit 0，「红线约束扫描通过」 |
| `ruff check monitor dbmonitor --select F821,F601,F811` | 本机未执行：.venv 和系统 PATH 里都没有 ruff，按约束不安装。推送后 CI 的静态检查 job 已 success，见 §五 |

### 3.5 收尾

3 个容器已 `docker stop`，保留未删。验证结束时 `git status --porcelain` 只有一行：` M monitor/tests_dialect.py`。

## 四、提交

- v1.0 报告与代码改动同批提交，提交号 **bdae978**（`bdae97815d3bccac1fb3b2fdc5827733c5d1abfb`）。提交里只有 `monitor/tests_dialect.py` 和本报告两个文件（2 files changed, 105 insertions(+), 1 deletion(-)）。同步程序 17:52:29 回执 `0945b07..bdae978  main -> main`，提交身份 `AQMan <aqman@local>`。
- v1.1 只修订本报告一个文件，单独提交。
- 过程如实说明：第一次修订用的是补丁方式，上下文没对上，补丁被整体拒绝，文件没有被修改。紧接着投递的推送请求，回执是 `nothing to commit`（HEAD 仍是 bdae978），没有产生空提交。之后改为整篇重写 v1.1 再推送。

## 五、推送后 CI

来源：GitHub 接口，AQMan 于 2026-11-25 17:5x 读取。提交 bdae978 触发 3 个流程，全部 completed / success：

- `CI`（run 45589546003）：**success**，2026-11-25T09:55:25Z（17:55:25 CST）结束。7 个 job 全部 success：
  - 静态检查（含 ruff F821/F601/F811，补上了本机没跑的那一项）
  - 单元测试（无外部依赖）
  - 集成测试（PostgreSQL）
  - 方言测试（MySQL/PG）
  - 方言测试（Oracle）
  - 前端构建与测试
  - 安全扫描
- `A autorun dispatch`（run 45589545928）：success。
- `O DB-AIOps automatic review`（run 45589545941）：success。

如实说明：只取到了 job 级的结论；Linux 下改动后的扫描/点查比值没有取到，因为没有读 job 日志。

## 六、未测 / 未核实

1. ruff 本机未跑；CI 静态检查 job 已 success，视为已覆盖。
2. 改动后本机只跑了 3 轮，「偶发失败已消除」无法由此证明，只能说余量扩大了约 5 倍。
3. 本机只跑了 MySQL/PG 方言套件和 unit，integration、前端、Oracle 方言没有重跑。改动只涉及一条 MySQL 方言用例，推断不受影响；CI 中这几个 job 均为 success。
4. Linux（CI）下改动后的比值未知：只有 job 级 success，没有读 job 日志。
5. ~~L306 断言提示文字仍写着「4 次」~~：v1.2 已按用户指示与 STATUS v1.62 条件更正为「20 次」（仅改字符串，断言逻辑未动）；推送后 CI 结论以 AQMan 复核为准。

## 七、版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-25 | AQMan | 首版：L294 扫描 4→20；本机方言整套 3 轮全过（比值 6.57–7.63）、unit 467 OK；结论有条件通过，待推送后 CI |
| v1.1 | 2026-11-25 | AQMan | 补推送后 CI：bdae978 的 CI 7/7 success（含 ruff 与两类方言 job），A、O 两个流程 success；条件关闭，结论升为通过；更新未测清单 |
| v1.2 | 2026-11-25 | AQMan | 按用户 19:25 指示及 STATUS v1.62 条件：`monitor/tests_dialect.py` L306 断言提示文字「全表扫描 4 次」→「全表扫描 20 次」，与 L294 实际次数一致；仅改字符串 |
