# M-team 就位声明与环境报备（DB-AIOps）

- 文件性质：**就位声明＋环境报备，非接单回执**。
- 团队：M-team，Team Lead：MMMan（minimax code）。
- 报备时间：2026-11-04。
- 仓库：`C:\DB-AIOps`，分支 `main`，报备时 HEAD `004eb9b`，与 `origin/main` 一致（`rev-list --left-right --count` = 0/0）。
- 报备依据：本文件全部结论均为**本机现场实测**，未采信任何既有叙述；差异项一律给出实测证据与口径建议，不擅自改写 `ROSTER.md`（该文件由 muse 维护）。
- 报备过程说明：本文件起草期间 DSMan 已提交并推送《DB-001 建设方向研判报告》（`9592f02` ＋ 合并 `004eb9b`），仓库 HEAD 相应前进；本文件以**终态 HEAD `004eb9b`** 为准。

## 1. 为何不占 DB-0xx 编号、不写回执

黑板 `docs/pm/orders/` 现存单据为 DB-001（设计团队）、DB-003（自动链接入），**均未派给 M-team**。按「无单不写回执、回执须挂单号」的纪律，本方当前无接单凭证可签，故：

- 不新建 `DB-0xx-receipt-m-team.md`（无单号可挂，避免占用编号、避免制造噪音）；
- 交付报告命名规则仍按 ROSTER 执行：`DB-0xx-report-m-team.md`（派单后适用）；
- 本文件仅作**就位声明**（让部门经理确认 M-team 已具备承接条件）与**环境报备**（让后续派单方知道开工前还差什么）。

## 2. 团队就位状态

| 项 | 状态 |
|---|---|
| 承接范围（ROSTER 定位） | UAT（业务验收场景设计与执行）、测试数据准备 |
| Team Lead | MMMan |
| 队内成员编制 | **待 muse 补记**（ROSTER 团队表与 README L23 均只给 team lead 与团队定位，未列 M-team 队内成员代号） |
| 执行单元（内部） | UAT 场景线、测试数据线、缺陷闭环线（三条线均在 minimax code 桌面端本地运行） |
| 内部纪律 | 禁 `float` 与内置 `hash()`；冻结输入语义；严禁改代码时顺手改测试断言；未实际验证不得写「已销账」；缺陷编号沿用 SIT-Dxx / UAT-Dxx / UAT2-Dxx |
| 提交纪律 | 直推 main 无 PR 门禁；只 `git add -- <我件>` ＋ `git commit --only`；不代改他人文件；`STATUS.md` / `DECISIONS.md` 仅 muse 可改；纯 `docs/pm/reports/` 新增单不跑 `validate.sh`（对标 DSMan DB-001 回执先例） |

> **本次提交的暂存区处置（纪律落地留痕）**：报备期间 DSMan 的 `DB-001-report-design-team.md` 曾一度出现在我方 `git status` 的暂存区（本机并发操作所致），一度存在误带入风险。我方以 `git add -- <我件>` ＋ `git commit --only -F <msg> -- <我件>` 双重限定，最终提交**仅含本文件一份**（`git diff --cached --stat` 实测 `1 file changed, 152 insertions(+)`），未触碰他人文件，亦未清空他人暂存内容。

## 3. 执行环境报备（实测）

### 3.1 主机与工具链

| 项 | 实测值 | 备注 |
|---|---|---|
| 操作系统 | Windows 11 桌面端（PowerShell 5.1 / Git Bash） | 非云端隔离沙箱 |
| 系统 Python | 3.14.6（`C:\Users\linsa\AppData\Local\Python\pythoncore-3.14-64\python.exe`） | `py -0p` 仅登记 3.14 |
| 项目 venv | **3.11.16**（仓库根 `.venv/`，uv 自备 CPython） | 已按 `requirements.lock` 复刻，见 §4 |
| uv | 0.12.10 | 可自备 Python 版本并按 lock 复刻环境 |
| Node / npm | v24.18.0 / 11.16.0 | 供 `validate.sh frontend` 用 |
| Git | 2.56.0.windows.4 | |
| Docker CLI | 29.9.2 | unit 层不需要 Docker |
| Bash | `C:\Program Files\Git\bin\bash.exe` 可用 | **WSL 不可用**（`wsl` 命令报宿主错误），故 `validate.sh` 一律走 Git Bash |
| Playwright | 1.62.0（用户级 site-packages） | 浏览器内核可用，见 §5 |
| 浏览器 | Chrome、Edge 均已安装 | |
| 仓库 clone | `C:\DB-AIOps` **已存在**，main，0/0 与远端一致（终态 HEAD `004eb9b`） | 报备时工作区干净（DSMan 的报告已由其本人提交） |
| 版本源 | `dbmonitor/version.py` = **2.6.0** | 唯一版本来源；本方不手写版本号 |

### 3.2 与 ROSTER 登记的差异（3 处，请裁定）

| # | ROSTER 现状 | 本机实测 | 影响 |
|---|---|---|---|
| 1 | M 行：开发环境「云端隔离沙箱 Debian 12，Python 3.11.2；跑不了浏览器 E2E」 | 本机 Windows 桌面端直接运行；系统 Python 3.14.6；**浏览器 E2E 内核实测可用**（§5） | 环境口径与浏览器能力两项均与登记不符，影响是否需要为本方另配沙箱、也影响「谁跑 E2E」的分工 |
| 2 | 脚注称「DB-AIOps 本机 clone 待各成员按任务单自行建好并报备」；GG 行标注「DB-AIOps 本机 clone 待建」 | M-team 本机 clone **已存在**且与 `origin/main` 同步（终态 HEAD `004eb9b`） | 属已建成事实，补记即可；**STATUS L15「约束与缺口」的待建名单为「GG/C/D 的 DB-AIOps 本机 clone 与 docker-compose.dev 开发基础设施待建」，本就不含 M**，与现场一致；脚注与 GG 行按其他成员各自状态处理，本方不代改 |
| 3 | M 行登记 Python 3.11.2 | 本机仅 3.14.6；`requirements.lock` 由 `uv pip compile --generate-hashes --python-version 3.11` 生成 | 决定本地复刻路径（见 §4.3）：3.11 与 lock 对齐成本最低 |

> 说明：第 1 项「跑不了浏览器 E2E」一条经实测证伪，本方主动更正，不等裁定。

### 3.3 环境自建现状（已完成）

- 初测时仓库根**无** `.venv/`、`venv/`，`scripts/validate.sh` 自动激活失败后回落到系统 `python`，导致 Django 缺失、unit 秒挂（见 §4）。
- 本方已按 `requirements.lock` 在仓库根建 `.venv/`（CPython **3.11.16**，由 uv 自备），并按 lock 装齐全部依赖（`uv pip install -r requirements.lock`，退出码 0）。
- `.gitignore` 已忽略 `/.venv/`、`/venv/`、`__pycache__/` ⇒ venv **不污染工作区、不进版本库**；复刻后实测 `git status` 无 venv 相关条目。

## 4. 验证闸门现状：初测不通过 → 环境复刻后已转绿

### 4.1 实测证据（2026-11-04，报备时刻）

命令：`scripts/validate.sh unit`（经 Git Bash 执行），**退出码 1**。

| 阶段 | 结果 | 耗时 |
|---|---|---|
| 红线扫描 | pass | 941ms |
| unit 测试 | **fail** | 224ms |

失败原因（原文要点）：`manage.py` 导入 `django.core.management` 时 `ModuleNotFoundError: No module named 'django'`，并伴随 `ImportError: Couldn't import Django. Are you sure it's installed and available on your PYTHONPATH environment variable? Did you forget to activate a virtual environment?`；脚本末行输出「===> 验证失败，禁止提交」。

**复刻后复测（同日）**：同一条命令 **退出码 0**，脚本末行输出「===> 验证全部通过」。

| 阶段 | 结果 | 耗时 |
|---|---|---|
| 红线扫描 | pass | 1010ms |
| unit 测试 | pass（**396 tests，OK**） | 33327ms |

说明：unit 走 `dbmonitor.settings_test_unit`（SQLite 内存库），全程不连 PostgreSQL / ES / Redis。日志中出现的 MySQL `127.0.0.1` 连接失败、哨兵 `db unreachable` 堆栈、越权 404/403 等，均为**用例刻意构造的降级与异常路径输出**，终判为 OK。

### 4.2 根因

**不是代码缺陷，是环境未初始化**：unit 阶段使用独立 settings（`dbmonitor.settings_test_unit`，SQLite 内存库，零外部服务依赖），但仍需 Django 及依赖已安装；本方无虚拟环境 ⇒ 脚本回落到系统 Python 3.14.6，而该解释器未装 Django ⇒ 秒挂。

> 「零外部依赖」指不依赖 PostgreSQL / ES / Redis 等外部服务，**不等于不需安装 Python 包**。此点此前易被误解，本次一并澄清。

### 4.3 复刻路径（已执行，退出码全绿）

```
uv venv --python 3.11 .venv                                            # 实得 CPython 3.11.16
uv pip install --python .venv/Scripts/python.exe -r requirements.lock   # 退出码 0
scripts/validate.sh unit                                                # 退出码 0
```

要点：

- 选 3.11 而非 3.14，因 `requirements.lock` 的哈希即按 3.11 解析生成，3.11 复刻为**零重算**路径；用 3.14 需重生成 lock，lock 变更属工程决策，不宜由本方单方发起。
- 依赖清单 `requirements.txt` 全部具备 Windows wheel（`psycopg2-binary` / `pyodbc` / `oracledb` / `numpy` 等），本方已实测无编译级阻塞。
- venv 建在仓库根，被 `.gitignore` 忽略，不污染版本库。

### 4.4 开工前置条件（P0）—— 已达成

**依赖装齐且 `scripts/validate.sh unit` 退出码为 0**，是 M-team 承接任何涉及代码改动的 DB-0xx 单之前的硬前置。

当前状态：**已达成**（2026-11-04 实测退出码 0，396 tests OK）。本方承接代码类 DB-0xx 单时，本地验证闸已可用。

边界：上述结论仅覆盖 `unit` 层（SQLite 内存库）。`backend` 层需 PostgreSQL、`frontend` 层需 `npm ci` ＋ `npm run build`，本方**尚未实测**这两层；接单若涉及，需先单独验证后再承诺。

## 5. 浏览器 E2E 能力实测（修正 ROSTER 登记）

| 项 | 实测结果 |
|---|---|
| Playwright | 1.62.0 可导入（`C:\Users\linsa\AppData\Roaming\Python\Python314\site-packages\playwright`） |
| 浏览器内核缓存 | `%LOCALAPPDATA%\ms-playwright\chromium_headless_shell-1234` 存在 |
| 启动探针 | `p.chromium.launch()` **成功**，返回 `CHROMIUM OK 155.0.8059.79` |
| 旁路浏览器 | Chrome、Edge 均已安装 |

结论：**本方具备浏览器 E2E 执行能力**，ROSTER 中 M 行「跑不了浏览器 E2E」与现场不符，已更正。

边界声明（不夸大）：以上只证明**浏览器内核可启动**，不等于 DB-AIOps 的 E2E 用例已可跑通 —— 用例还依赖 Django 服务起动、测试数据就位、目标页面可达。上述三项须在依赖装齐后于首单实测确认，未确认前不写入任何「E2E 已验证」结论。

## 6. 承接准备与边界

- 承接方向：UAT 业务场景设计与执行、测试数据准备（造数走 Django 幂等脚本，含回滚与分档，验证走 `validate.sh unit` 的 SQLite 内存库路径，避免碰生产数据）。
- 产出物命名：回执 `DB-0xx-receipt-m-team.md`；交付报告 `DB-0xx-report-m-team.md`（派单后适用）。
- 沟通通道：飞书群**只广播、不作回执通道**；回执一律落 `docs/pm/reports/`。
- 派单未下前，本方不自定方向、不擅自开工。

## 7. 待部门经理裁定（3 项）

1. **M 的执行环境口径**：以本机 Windows 桌面端为准（建议更正 ROSTER M 行），还是仍按「云端隔离沙箱 Debian 12」另配环境？
2. **本地 Python 版本口径**：本方已按 `requirements.lock` 目标建 3.11 venv（实得 3.11.16），**`requirements.lock` 未做任何改动、仓库零变更**。仍请裁定：维持 lock 的 3.11 口径（建议），还是登记 3.14.6 并重生成 lock（属工程决策，不宜由本方单方发起）。
3. **M-team 队内成员编制**：README L23 已列各团队定位（M-team：UAT 与数据准备），但未列 M-team 的**队内成员代号**。本方下辖执行单元（UAT 场景线、测试数据线、缺陷闭环线）请补记，以便派单时明确分工与签报对象。

## 8. 轮询链路：已被上游覆盖，本方不再请裁（仅一条事实报备）

**上游现状（实测黑板）**：

- DEC-003（2026-11-03，用户批准）已允许分发器增加 DB-AIOps 黑板轮询（`docs/pm/orders`、`docs/pm/reports`），与 Xinchuang 黑板并行；分发器对仓库只读。
- DB-003「自动链接入」执行方为 **GorkMan、A、O**（STATUS L8）；GorkMan 已交接单 ETA 回执，交付窗口 2026-11-05～11-06。
- STATUS L9 记「分发器双仓轮询：分发器已获准扩展至 DB-AIOps 黑板，配置与重启验证进行中」。

因此本方起草时设想的「等 DB-003 或申请授权建只读实例」这一兜底请裁项，**已被上游决策覆盖，本方撤回，不再占用裁决位**。

**本方仅作一条事实报备**：本机轮询工具支持 `--workdir` 多仓并行、DB-AIOps clone 亦已就绪，但**状态文件按成员名命名，同一成员跨仓并行会互相覆盖**。因此本方**不擅自自建轮询实例**（既会与 DB-003 在建链路重复，又存在状态文件撞车风险），继续以「飞书广播 ＋ 部门经理点名」为接单通道，待 DB-003 完成后按上游口径接入。

## 9. 成本披露

搜索 0 次；抓取 0 次。本轮为本地只读核验 ＋ 2 次 `validate.sh unit` 实测（初测 fail、复刻后 pass）＋ venv 与依赖安装（uv 自备 CPython 3.11.16，按 lock 全量安装退出码 0）；未改任何代码、未改他人文件、未触碰 `STATUS.md` / `DECISIONS.md`，版本库只新增本文件一份。

## 10. 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-04 | MMMan | 就位声明＋环境报备首版；含 unit 实测不绿的完整证据、浏览器 E2E 能力实测更正、3 项待裁定与 1 项报备 |
| v1.1 | 2026-11-04 | MMMan | 追记环境复刻结果：`.venv/`（CPython 3.11.16）按 `requirements.lock` 建齐，`validate.sh unit` 复测 **exit 0**（396 tests OK），§4 闸门现状、§3.3 环境自建、§4.4 前置条件据此更新；v1.0 中「unit 不绿」的记录**保留作留痕，不删改** |
| v1.2 | 2026-11-04 | MMMan | 提交前现场复核后校正：① 仓库终态 HEAD 更新为 `004eb9b`（DSMan 已提交并推送 DB-001 报告）；② §8 原「DB-003 兜底请裁」因 DEC-003 已批准分发器双仓轮询、DB-003 执行方为 GorkMan/A/O 而**撤回该请裁项**，改为事实报备；③ §3.2 第 2 项补 STATUS L15 待建名单不含 M 的佐证；④ §2 增补并发暂存误入风险的处置留痕 |
