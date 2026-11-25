有条件通过

# DB-AQ-002 交付报告：MySQL 方言时序敏感测试加固

- 任务单：`docs/pm/orders/DB-AQ-002-AQMan时序敏感测试加固.md`（PM 2026-11-25 派单）
- 执行：AQMan（AWS Quick）。代码由 AQMan 修改；本机验证由编码代理 Kiro 在用户设备 Smudy 上执行，AQMan 核对其原始输出
- 成文：2026-11-25 17:5x CST
- 基线：开工前 17:42:22 自动 `git pull --ff-only`，b98a266 → **0945b07**（拉到本任务单、STATUS v1.61、ROSTER v1.2）
- 口径：只记实测，推断标「推断」。本报告不含任何密码、token、密钥

## 一、结论

**有条件通过。** 改动严格按任务单执行，只改了一个工作量数字，断言未动。本机方言套件整套跑 3 轮全部通过，unit 回归通过，扫描与点查的总耗时比由改动前的 1.25–1.73 倍拉大到 6.57–7.63 倍。

条件一项：推送后 CI 的结果以本报告推送后 AQMan 复核为准（见 §五），报告成文时尚未产生。

另外说明一点：3 轮全部通过、余量扩大约 5 倍，只能说明偶发失败的概率大幅降低，**不能证明「已彻底消除」**。Kiro 在总结里用了「有效消除」，这个说法超出了证据，不予采信。

## 二、改动（diff 摘要）

只改了 `monitor/tests_dialect.py` 一个文件，共 1 行：

```diff
@@ MySQLSlowSqlDialectTests.test_live_fallback_reports_real_seconds_and_sorts_by_total
-        self._run('SELECT COUNT(*) FROM sqlstat_probe WHERE pad LIKE CONCAT(%d, "%%")', 4)
+        self._run('SELECT COUNT(*) FROM sqlstat_probe WHERE pad LIKE CONCAT(%d, "%%")', 20)
         self._run('SELECT id FROM sqlstat_probe WHERE id = %d', 50)
```

对照任务单逐条核对：

| 任务单要求 | 执行情况 |
|---|---|
| 全表扫描 4 → 20 次，点查保持 50 次 | ✅ L294 由 4 改为 20；L295 的 50 未动 |
| 不改两处断言（L305 总耗时比较、L307 按总耗时排序） | ✅ 未动 |
| 不改为比较平均单次耗时 | ✅ 未改 |
| 不在 setUp 里清空 performance_schema | ✅ setUp 未动 |

据实说明：L306 断言的提示文字仍是「全表扫描 4 次的总耗时应大于主键点查 50 次」，与新的执行次数（20 次）对不上。这句文字属于断言语句的一部分，任务单要求不改断言，所以没有改动。是否顺手把文字更正为「20 次」，请 PM 裁决。

验证前 Kiro 复核了 `git diff --stat` 与 `git diff`，确认只有这一处改动。

## 三、本机验证

环境与变量按 AQMAN-ENV 报告 §六（MySQL/PG 方言口径，不设 DJANGO_SETTINGS_MODULE / POSTGRES_DB / DIALECT_ORACLE_DSN）。每轮开始前都 `docker restart aqman-mysql80-test`，并只读确认 performance_schema 里没有任何 sqlstat_probe 摘要。

### 3.1 方言套件整套 3 轮（任务单要求 ≥3 轮）

| 轮次 | 整套结果 | 目标用例 | 扫描总耗时 | 点查总耗时 | 总耗时比 | COUNT_STAR（扫描/点查） |
|---|---|---|---|---|---|---|
| 1 | `Ran 31 tests in 88.897s OK (skipped=11)` | ok | 0.035716s | 0.004679s | 7.63 | 20 / 50 |
| 2 | `Ran 31 tests in 88.817s OK (skipped=11)` | ok | 0.037413s | 0.005020s | 7.45 | 20 / 50 |
| 3 | `Ran 31 tests in 85.926s OK (skipped=11)` | ok | 0.033520s | 0.005104s | 6.57 | 20 / 50 |

- 3 轮里 MySQLSlowSqlDialectTests 的 3 个用例全部 ok。
- COUNT_STAR 每轮都正好是 20 和 50，与代码一致，说明没有混入其他语句。
- 11 个 skip 与改动前相同，都是 DM、GBase、Oracle 的 DSN 未设置，与 CI 的 test-dialect job 一致。
- 日志存在仓库外：`C:\AWSQuick\.aqman\aq002-dialect-1..3.log`。

### 3.2 与改动前对照（数据见 AQMAN-ENV 报告 §五）

| 条件 | 整套轮次 | 总耗时比范围 | 最小比 | 失败 |
|---|---|---|---|---|
| 改动前（扫描 4 次） | 3（另有首跑失败 1 次，比值 0.99） | 1.25–1.73 | 1.25 | 4 次中 1 次 |
| **改动后（扫描 20 次）** | **3** | **6.57–7.63** | **6.57** | **3 次中 0 次** |

点查总耗时基本不变（约 4.7–5.1ms）；扫描总耗时随次数线性增长，约由 7–11ms 升到 34–37ms。改动前那次失败时，点查偏慢到 8.0ms；按改动后的数据，扫描至少要慢 6.5 倍以上才会翻转（推断）。

### 3.3 后端 unit 回归（任务单要求）

`DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_unit` 加 workflow 级 5 个变量：`Ran 467 tests in 54.880s OK`，0 失败、0 错误（与改动前 467 一致）。

### 3.4 静态检查（任务单未要求，提前对齐 CI static job）

| 检查 | 结果 |
|---|---|
| `python -m compileall -q monitor dbmonitor` | exit 0，无输出 |
| `python scripts/lint_redlines.py` | exit 0，「红线约束扫描通过」 |
| `ruff check monitor dbmonitor --select F821,F601,F811` | **未执行**：.venv 和系统 PATH 里都没有 ruff，按约束不安装；以推送后 CI 的 static job 结果为准 |

### 3.5 收尾

3 个容器已 `docker stop`，保留未删。验证结束时 `git status --porcelain` 只有 ` M monitor/tests_dialect.py` 一行。

## 四、提交

本报告与代码改动同批提交，只含 `monitor/tests_dialect.py` 和本报告两个文件，经本机同步程序 `pull --rebase` 后再提交、推送，提交身份为 `AQMan <aqman@local>`。提交号见推送回执。

## 五、推送后 CI

成文时还未产生。推送后由 AQMan 经 GitHub 接口复核 CI（重点看静态检查 job 的 ruff 一项，以及 test-dialect job），结果另行报 PM；如有失败，将据实回填本报告。

## 六、未测 / 未核实

1. ruff 本机未跑，以 CI static job 为准。
2. 改动后本机只跑了 3 轮，「偶发失败已消除」无法由此证明，只能说余量扩大约 5 倍。
3. 只跑了 MySQL/PG 方言套件与 unit；integration、前端、Oracle 方言本次没有重跑。改动只在一条 MySQL 方言用例里，未跑的这些推断不受影响。
4. Linux（CI 环境）下改动后的比值未知，待 CI 结果。

## 七、版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-25 | AQMan | 首版：L294 扫描 4→20；本机方言整套 3 轮通过（比值 6.57–7.63）、unit 467 OK；结论有条件通过，待推送后 CI |
