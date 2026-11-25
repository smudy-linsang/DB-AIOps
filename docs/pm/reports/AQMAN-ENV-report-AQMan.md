有条件通过

# AQMAN-ENV 报告 v1.0：AQMan 本机代码/测试能力搭建与实测（Kiro + Smudy）

- 任务单：无（非派单工作）。系用户 2026-11-25 在对话中要求 AQMan 恢复 Kiro、搭本机测试环境并逐项实测，事后写成本报告落板，供 PM 核验与裁决 ROSTER 中「代码任务待 Kiro 恢复」一项。
- 执行：AQMan（AWS Quick）；本机操作由编码代理 Kiro（kiro-cli 3.6.0，经 Quick 调用）在用户设备 Smudy 上执行，AQMan 派单、核对原始输出与仓库原文
- 成文：2026-11-25 17:2x CST
- 基线：本地 main @ **4a576fc**（`4a576fc7796f167a877652295e418b050e1d56cc`，STATUS v1.60），开工前 17:23:05 自动 `git pull --ff-only` 回执 `Already up to date.`
- 口径：只记实测；数字均出自 Kiro 贴回的命令原始输出，或 AQMan 直读的仓库文件；推断标「推断」。本报告不含密码、token、密钥；测试库口令为 ci.yml 公开测试值，下文一律不写出。

## 一、结论

**有条件通过。** 本机已具备「改代码 + 按 CI 口径跑测试」的能力：unit、integration（全量与随机顺序）、前端、Oracle 方言、MySQL/PG 方言五类测试均已在本机按 CI 配置实跑通过。条件两项：

1. **MySQL 方言用例 1 条存在时序敏感**：`MySQLSlowSqlDialectTests.test_live_fallback_reports_real_seconds_and_sorts_by_total` 本机 9 次中失败 1 次（见 §五）；定性为低概率时序抖动、非产品缺陷属**推断**，是否改测试待 PM 裁决。
2. **依赖安装与 CI 有一处偏差**：Windows 专用传递依赖 `colorama` 不在 `requirements.lock`（lock 为 Linux 生成）内，系单独补装，未经 hash 校验（见 §三）。

| 测试类别 | 本机结果 | 与 CI 口径 |
|---|---|---|
| 后端 unit | `Ran 467 tests in 56.286s OK` | 等价 |
| 后端 integration 全量 | `Ran 650 tests in 250.443s OK (skipped=31)` | 等价（仅端口偏差） |
| 后端 integration 随机顺序 | `Ran 650 tests in 256.512s OK (skipped=31)`，seed `4393687640` | 等价（仅端口偏差） |
| 前端 | 12 个测试文件、100 条用例全部通过 | Node 24（CI 为 22） |
| Oracle 方言 | `Ran 4 tests in 12.230s OK`，0 skip；CI「确认用例真的跑了」4 项断言全过 | 等价（仅端口偏差） |
| MySQL/PG 方言 | 首次 `Ran 31 tests FAILED (failures=1, skipped=11)`；其后整套 3 次均 `Ran 31 tests OK (skipped=11)` | 等价（仅端口偏差）；1 条时序敏感见 §五 |

## 二、编码代理 Kiro 恢复

- 现象：首个账号连续 4 次调用均在执行任何命令前被服务端拒绝，报 `User ID is temporarily suspended`（Request ID 依次为 f6fb7c43-…、d0a4b5e1-…、1e1b267b-…、011036be-…）；用户在终端直接调用也同样被拒。
- 处置：用户换新账号重新登录，终端自测「nihao」得到正常回复（有用户截图）；第 5 次由 Quick 调用仍报 suspended（Request ID 0f1c7db8-…），用户完全重启 Quick 后第 6 次调用成功。
  - 推断：Quick 托管的 Kiro 进程在换号前已经启动，仍持有旧账号凭据，重启后才换成新凭据。未核实。
- 第 6 次只读环境检查（10 项）实测：git 2.56.1、gh 2.97.0；`gh auth status` 显示已登录 smudy-linsang，token 范围含 repo、workflow（输出中 token 已打码）；仓库 `## main...origin/main` 无差异，HEAD 4a576fc；`git push --dry-run` 返回 `Everything up-to-date`；Python 3.14.6、Node v24.18.0、npm 11.16.0、Docker 29.9.3；系统 Python 无 Django。
- 据实更正：Kiro 当轮总结写了「GitHub 权限为 ADMIN」，但它贴出的原始输出里没有这一项，不予采信。

## 三、环境搭建（用户批准后执行）

| 项 | 实测 |
|---|---|
| Python 3.11 | 3.11.9，按当前用户安装，路径 `C:\Users\linsa\AppData\Local\Programs\Python\Python311\python.exe`，无 UAC 弹窗；不改系统 PATH，原有 3.14 未动 |
| 虚拟环境 | `C:\AWSQuick\DB-AIOps\.venv`（3.11.9）；`.gitignore` 已有 `/.venv/` 规则 |
| 后端依赖 | `pip install --require-hashes -r requirements.lock` 直接执行失败：`colorama`（Windows 专用、`click` 的传递依赖）不在 lock 中。改为 `--require-hashes --no-deps` 安装 lock 内全部 44 个包（均过 hash 校验），再单独补装 `colorama`（**未经 hash 校验**） |
| 前端依赖 | `frontend\` 下 `npm ci`，安装 311 个包，0 个漏洞；`.gitignore` 已有 `frontend/node_modules/` 规则 |
| 数据库容器 | `aqman-pg16-test`（postgres:16，主库，55432→5432）、`aqman-mysql80-test`（mysql:8.0，53306→3306）、`aqman-targetpg16-test`（postgres:16，targetdb，55433→5432）、`aqman-oracle23-test`（gvenzl/oracle-free:23-slim-faststart，51521→1521）；全部专用，测完已停止并保留，其余容器未动 |
| 仓库状态 | 每轮收尾 `git status --porcelain` 均为空，仓库内已跟踪文件零改动 |

另：上午 AQMan 在 Smudy 读到 Python 3.12.10，与 Kiro 读数不一致。推断该值来自 AWS Quick 自带的 Python 运行时，并非系统安装的 Python。未核实。

## 四、与 CI 配置对齐（含一次自我纠错）

AQMan 直读 `.github/workflows/ci.yml` 与 `manage.py` 原文核对：

- workflow 级 env（ci.yml L18–23）：DJANGO_SECRET_KEY、DB_MONITOR_SECRET_KEY、TIMESCALEDB_ENABLED='False'、ES_ENABLED='False'、PYTHONDONTWRITEBYTECODE='1'，所有 job 继承。
- 全文仅 **L80** 设 `DJANGO_SETTINGS_MODULE: dbmonitor.settings_test_unit`（test-unit job）；test-integration job（L84–117）的步骤只设 POSTGRES_HOST/USER/PASSWORD。
- `manage.py` L9：`os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')`。

**纠错**：首轮本机 integration 用的是 `DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_integration`，与 CI 不等价（当时同样是 650 OK / 31 skip，但 settings 不同）。已按 CI 口径重跑（不设 DJANGO_SETTINGS_MODULE，也不设 POSTGRES_DB）。测试前在同一进程内取证 Django 实际生效的配置：`SETTINGS_MODULE=dbmonitor.settings`、PostgreSQL 127.0.0.1:55432/db_monitor、ES_ENABLED=False、TIMESCALEDB_ENABLED=False、CACHE=locmem、HASHERS=PBKDF2、EMAIL=smtp。两套 settings 的实际差异在密码哈希（MD5 对 PBKDF2）与邮件后端，均不影响结果；按 CI 口径重跑耗时由约 147s 增至约 250s（推断主因为 PBKDF2）。§一表中的 integration 数字即这次 CI 口径重跑的结果。

另一处过程问题：Kiro 每次调用都是独立的 PowerShell 进程，环境变量不会跨调用保留，首次取证因此读到了默认值。此后一律在同一条命令里设变量、打印变量、跑测试。

与 CI 的已知偏差仅以下几项：端口（55432/53306/55433/51521，CI 为默认端口）、Node 24（CI 为 22）、`colorama` 单独补装、宿主 Windows（CI 为 ubuntu-latest）。

## 五、MySQL 方言用例时序敏感（待 PM 裁决）

- 用例：`monitor.tests_dialect.MySQLSlowSqlDialectTests.test_live_fallback_reports_real_seconds_and_sorts_by_total`（`monitor/tests_dialect.py` L291–308）。它先跑 4 次 LIKE 全表扫描（约 2 万行），再跑 50 次主键点查，读 performance_schema 的累计耗时，断言「扫描总耗时 > 点查总耗时」（L305），以及「按总耗时排序扫描在前」（L307）。
- 首次失败（整套跑，MySQL 容器为新建）：`AssertionError: 0.007902 not greater than 0.008003 : 全表扫描 4 次的总耗时应大于主键点查 50 次`
- 复测：单独跑 5 轮，全部 OK；整套跑 3 轮（每轮先重启 MySQL，并确认 performance_schema 中无 sqlstat_probe 摘要），全部 OK。整套 3 轮读数：

| 轮次 | 整套结果 | 扫描 4 次总耗时 | 点查 50 次总耗时 | 总耗时比 |
|---|---|---|---|---|
| 1 | `Ran 31 OK (skipped=11)` | 0.011278s | 0.007884s | 1.43 |
| 2 | `Ran 31 OK (skipped=11)` | 0.009283s | 0.005359s | 1.73 |
| 3 | `Ran 31 OK (skipped=11)` | 0.007169s | 0.005737s | 1.25 |
| 首次失败 | FAILED | 0.007902s | 0.008003s | 0.99 |

- 合计：本机 9 次失败 1 次（整套 4 次中 1 次，单独 5 次中 0 次）。三轮的 COUNT_STAR 恒为 4 和 50，说明统计中**没有混入其他用例的语句**。Kiro「累计污染」的说法被这组读数否定，不予采信。
- 11 个 skip 均为 `DIALECT_DM_DSN / DIALECT_GBASE_DSN / DIALECT_ORACLE_DSN 未设置`（DM 2 个、GBase 5 个、Oracle 4 个），与 CI test-dialect job 不设这三个 DSN 一致。
- 定性（推断）：断言比较的是真实执行耗时，本机通过的几轮里余量只有 1.25–1.73 倍，一次抖动（失败那次点查偏慢）就足以让大小翻转。属测试对执行时间敏感、低概率抖动，不是产品缺陷。另据 AQMan 于 2026-11-25 读取 GitHub 接口，CI 在 Linux 上 96fb948、ab0f910 两次推送均为 success。
- 建议（未改代码，交 PM 决定）：保留「按总耗时」比较与排序断言，只拉大两类工作量的差距（例如扫描 4→20 次，或点查 50→10 次）以扩大余量。**不建议**改为比较平均单次耗时，那样会失去 L307「按总耗时排序」这一被测行为。Kiro 另提的「setUp 里清空 performance_schema」方案，因已证实没有混入其他语句，针对不上这次失败。

## 六、复跑方法（本机，PowerShell）

每类测试都在**同一个命令块**内完成设变量和跑测试；口令取 ci.yml 公开测试值，此处以 `<CI测试口令>` 代替。

```powershell
# 公共：workflow 级变量
cd C:\AWSQuick\DB-AIOps
$env:DJANGO_SECRET_KEY='ci-only-not-a-real-secret'; $env:DB_MONITOR_SECRET_KEY='ci-only-not-a-real-secret'
$env:TIMESCALEDB_ENABLED='False'; $env:ES_ENABLED='False'; $env:PYTHONDONTWRITEBYTECODE='1'

# unit
$env:DJANGO_SETTINGS_MODULE='dbmonitor.settings_test_unit'
.venv\Scripts\python manage.py test monitor --tag unit -v 2

# integration（先 docker start aqman-pg16-test；不设 DJANGO_SETTINGS_MODULE / POSTGRES_DB）
Remove-Item Env:\DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue
$env:POSTGRES_HOST='127.0.0.1'; $env:POSTGRES_PORT='55432'; $env:POSTGRES_USER='postgres'; $env:POSTGRES_PASSWORD='<CI测试口令>'
.venv\Scripts\python manage.py test monitor -v 1
.venv\Scripts\python manage.py test monitor --shuffle -v 1

# MySQL/PG 方言（另起 aqman-mysql80-test、aqman-targetpg16-test）
$env:DIALECT_MYSQL_DSN='mysql://root:<CI测试口令>@127.0.0.1:53306/testdb'
$env:DIALECT_PG_DSN='postgresql://postgres:<CI测试口令>@127.0.0.1:55433/targetdb'
.venv\Scripts\python manage.py test monitor.tests_dialect -v 2

# Oracle 方言（另起 aqman-oracle23-test，healthcheck.sh 返回 0 后）
$env:DIALECT_ORACLE_DSN='oracle://system:<CI测试口令>@127.0.0.1:51521/FREEPDB1'
.venv\Scripts\python manage.py test monitor.tests_dialect.OracleDialectTests -v 2

# 前端
cd frontend; npm run test:run
```

日志留存于仓库外：`C:\AWSQuick\.aqman\dialect.log`、`oracle.log`、`dialect-full-1..3.log`（未入库）。

## 七、未测 / 未核实

1. `colorama` 未经 hash 校验；Windows 下严格 `--require-hashes` 全量安装不可行（lock 文件为 Linux 生成）。
2. 时序敏感用例的定性仅基于 9 次样本，失败原因的机制为推断；新建容器首次运行的影响未单独验证。
3. 前端在 Node 24 下跑（CI 为 22），未在 Node 22 下复测。
4. 前端只跑了测试，未跑 `npm run build`，也未跑 CI 的 `npm audit --audit-level=high`；CI 的 static（ruff、红线、`manage.py check`、迁移漂移等）与 security（pip-audit、bandit、密钥扫描）两个 job 本机未跑。
5. DM、GBase 方言没有 DSN，本机未测（CI 同样不测）。
6. 本报告是文档提交，推送后的 CI 结果在成文时未知。
7. Kiro 换号后 Quick 侧失败、重启 Quick 后恢复，其原因为推断。

## 八、版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-25 | AQMan | 首版：Kiro 恢复、本机测试环境搭建、五类测试按 CI 口径实跑、MySQL 方言时序敏感用例 9 次复测定性；结论有条件通过 |
