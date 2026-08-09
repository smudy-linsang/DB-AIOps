# DB-AIOps 项目优化提升设计方案

| 项目 | 内容 |
|------|------|
| 文档版本 | v1.0（2026-08-09） |
| 定位 | 项目级质量与可靠性提升方案。覆盖 CI/测试基础设施/自监控/配置治理/失败姿态整改/协作harness |
| 前置输入 | ① `HARNESS_IMPROVEMENT_DESIGN.md`（Agent H，2026-08-08）② `BUGFIX_DESIGN.md`（本轮 38 项缺陷全量审计，已合并 PR #2） |
| 证据基础 | 逐行审计 `monitor/`(47K 行 Python) + `frontend/src/`(16K 行) 的实测结论，非推测 |
| 范围 | 工程基础设施 + 系统自监控能力（新增少量业务表与 API）；**不改动既有业务逻辑语义** |

---

## 序、与 Agent H 方案的关系

Agent H 的方案我**整体采纳**，它对问题的定位是准确的：`93 次编辑、0 次验证`
反映的确实是"缺少机械化反馈闭环"。AGENTS.md、validate.sh、pre-commit 三件套
是正确的起手式，本方案 W7 直接继承。

但在实施本轮 38 项缺陷修复的过程中，我实测到几个**会让该方案在关键场景失效**的点，
必须先说清楚，否则照做会产生"已经有验证闭环了"的错觉：

### S.1 三处需要修正

| # | H 方案的设定 | 实测问题 | 本方案的处理 |
|---|-------------|---------|-------------|
| C1 | 验证靠**本地** pre-commit 钩子 | 钩子是 `.git/hooks/` 下的本地文件，**不随仓库分发**、`--no-verify` 可跳过、且**对云端容器里工作的 Agent 完全无效**。我本轮在远程容器完成全部修复，本地钩子一次都不会触发。且 PR #2 合并时 `check_runs` 实测为 `total_count: 0` —— 没有任何东西拦得住 | **W1 建 GitHub Actions**，把强制点从本地移到 PR。钩子降级为"快速本地反馈"，不再承担门禁职责 |
| C2 | `validate.sh` 硬编码 `python manage.py test monitor.tests monitor.tests_phase8` | 该列表**已经漏了** `tests_phase7.py`；本轮新增的 `tests_bugfix.py`(87)、`tests_concurrency.py`(8) 也不在内。硬编码测试清单必然随时间腐烂 —— 新写的测试跑不到，等于没写 | 改为 `python manage.py test monitor`（自动发现）。文档同时把"55 个用例"更正为**实际 150 个** |
| C3 | `validate.sh` 缺 `makemigrations --check` | 模型改了却忘记生成 migration，是 Django 项目最高频的"本地能跑、部署即炸"。本轮我改了 3 处模型，全靠这条命令兜住 | backend 序列插入第 3 步 `makemigrations --check --dry-run` |

### S.2 三处需要补充

| # | H 方案未覆盖 | 为什么重要 |
|---|-------------|-----------|
| A1 | 验证结果只有 stdout，契约是"scrape `=== [阶段名] ===` 文本" | Agent 消费 stdout 是脆的（改一个字就断）。补 `--json` 结构化输出（W2.3） |
| A2 | 测试**必须**有 PostgreSQL 才能跑，H 方案把这归为"环境问题" | 这恰恰是"93 次编辑 0 次验证"的**真正成因**：验证门槛越高，跳过率越高。我本轮为了跑测试，花在装 venv/装依赖/initdb 上的时间超过写测试本身。W3 做**测试分层**，让 `unit` 层零外部依赖、秒级返回 |
| A3 | 完全未涉及**系统自身的可观测性** | 这是本项目最讽刺的缺口：一个监控平台，自己没被监控。BUG-113 的哨兵线程异常退出后**永久静默**，没有任何信号——修复后线程会自愈了，但"谁来发现它曾经死过"仍然无解。W4 补自监控 |

### S.3 一处需要反对

H 方案 §3.2 提议把验证记录 `validation_record` 表建在 **monitor 应用的业务库**里（P3）。

**不建议这样做。** 理由：工程工具链的遥测数据与业务数据混在同一个库、同一套 migration 里，
会带来三个具体麻烦：① 每次跑 CI 都往业务库写数据，测试库/生产库被污染；
② 该表的 migration 进入业务 migration 链，回滚业务版本时会连带它；
③ 开发机跑一次 validate 就要求业务库可连，与 A2 的"降低验证门槛"直接冲突。

本方案的替代：验证遥测落 **`.validation/history.jsonl`**（本地文件，gitignore），
CI 侧用 Actions 自带的 artifact/summary。**零 schema、零依赖、零污染**（详见 §3.1）。

---

## 一、概要设计

### 1.1 现状诊断（基于实测，非推测）

**D1 — 零 CI，合并无门禁**
```
$ gh api .../pulls/2/check-runs  →  {"total_count": 0}
$ ls .github/workflows/          →  不存在
```
PR #2 携带 5758 行变更、3 个数据库迁移，合并时没有任何自动检查。
唯一的质量保证是"我自己跑了测试"——这不可复制、不可审计。

**D2 — 采集层几乎零测试覆盖**

| 模块 | 代码量 | 测试引用 |
|------|--------|---------|
| `monitor/checkers/`（6 种数据库的指标采集） | **6077 行** | **0** |
| `monitor/detectors/` | 546 行 | 间接少量 |
| `monitor/llm/` | 1145 行 | tests_phase8 部分覆盖 |

`checkers/` 是整个系统的数据入口，6000 行、6 种数据库方言、大量 f-string 拼 SQL，
却一行测试都没有。原因很实际：它需要真实数据库。
**而 `docker-compose.dev.yml` 里其实已经躺着 MySQL 8.0 / Oracle XE 21 / PostgreSQL 16 /
GBase / TDSQL 五套容器** —— 基础设施是现成的，只是从未与测试连线。这是投入产出比最高的一块空白。

**D3 — 系统性的 fail-open 姿态**
```
except Exception: pass                →  169 处
except ...: pass（含具体异常类型）      →  192 处
except Exception + 仅 logger.debug     →   46 处
```
对一个**监控系统**而言，这个姿态是反的：静默降级意味着"监控停了但没人知道"。
本轮 BUG-113（哨兵线程死亡不重启）、BUG-136（审计记录静默丢失）、
BUG-137（LLM 校验静默跳过）都是这个模式的具体实例。它们被修了，但**模式还在**。

**D4 — 配置治理缺失**
- BUG-138：4 个限流阈值在 import 时求值，配置项写了不生效
- BUG-127：`ASH_INTERVAL_SEC` 默认值一处 5、一处 15，直接导致 AAS 计算偏差 3 倍
- 全项目 `getattr(settings, 'X', 默认值)` 散落各处，默认值无单一事实源、无校验

**D5 — 依赖漂移**
`requirements.txt`(24 行) 与 `requirements.lock`(20 行) 不一致；
本轮实测 `jsonschema`/`oracledb`/`apscheduler` 在纯净环境缺失，
其中 `jsonschema` 缺失直接导致 LLM 输出校验静默失效（BUG-137）。

**D6 — 前端零测试**
`frontend/src/` 16K 行，唯一验证手段是 `npm run build`（只能发现语法/引用错误）。
本轮我改了 9 个前端文件，行为正确性完全靠人眼。

### 1.2 目标与非目标

**目标（按优先级）**
1. **G1** 合并到 master 的每一行代码，都经过自动化门禁 —— 不依赖任何人的自觉
2. **G2** 本地验证做到"零外部依赖、10 秒内出结果"，让验证比不验证更省事
3. **G3** 采集层（6077 行）建立真实数据库集成测试，覆盖 6 种方言的关键路径
4. **G4** 系统具备自监控能力：采集停摆、线程死亡、写入失败能被主动发现并告警
5. **G5** 配置项有单一事实源、有启动校验、运行期可调
6. **G6** fail-open 模式收敛为分级失败策略，静默降级必须留痕

**非目标**
- 不重构业务逻辑、不改 API 语义（本轮 38 项修复已完成正确性整改）
- 不追求测试覆盖率数字指标（覆盖 `checkers/` 关键路径 > 覆盖率百分比）
- 不引入 Kubernetes/服务网格等基础设施改造
- 不做前端 E2E（Playwright 等）—— 列为 P3，先补组件级测试

### 1.3 总体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│ W1 CI 门禁层（强制，不可绕过）                                          │
│ .github/workflows/ci.yml                                             │
│   ├ lint-and-check  : compileall + manage.py check + migration drift │
│   ├ test-unit       : 无外部依赖，~10s                                │
│   ├ test-integration: PG service 容器，全量 150 用例                   │
│   ├ test-dialect    : MySQL/PG/Oracle 容器，checkers 方言测试(W3.3)    │
│   ├ frontend        : npm ci + build + vitest                        │
│   └ security        : 依赖漏洞 + 密钥扫描                              │
└──────────────┬───────────────────────────────────────────────────────┘
               │ 同一套检查，本地可复现
┌──────────────▼───────────────────────────────────────────────────────┐
│ W2 本地验证层（快速反馈）                                               │
│ scripts/validate.sh [unit|backend|frontend|all] [--json]             │
│   unit 模式：SQLite 内存库，零 Docker 依赖，10s                         │
└──────────────┬───────────────────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────────────────┐
│ W3 测试基础设施层                                                       │
│   L1 unit        : 纯逻辑，SQLite，无外部服务                           │
│   L2 integration : PostgreSQL（自有库语义：事务/约束/迁移）              │
│   L3 dialect     : 真实 MySQL/PG/Oracle 容器 → checkers 方言正确性      │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ W4 自监控层（业务能力，本方案唯一新增 Schema）                            │
│   ComponentHeartbeat 表 + /api/v1/system/health|components 接口       │
│   哨兵/采集器/流水线消费者上报心跳 → 缺失即告警（谁来监控监控系统）         │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ W5 配置治理    W6 失败姿态整改    W7 协作 harness（继承 Agent H）        │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.4 分期计划

| 期 | 工作流 | 交付物 | 工作量 | 前置 |
|----|--------|--------|--------|------|
| **P1** | W1 CI + W2 validate.sh + W7 harness | ci.yml、validate.sh、AGENTS.md、hooks | 1–2 天 | 无 |
| **P2** | W3 测试分层（L1/L2）+ W5 配置治理 | settings 单一事实源、unit 层可跑 | 2–3 天 | P1 |
| **P3** | W4 自监控 + W3-L3 方言测试 | 心跳表/接口、checkers 集成测试 | 3–5 天 | P2 |
| **P4** | W6 失败姿态整改 + 前端组件测试 | 分级失败策略、vitest | 3–5 天 | P3 |

**P1 是硬前置**：没有 CI，后面所有质量投入都可能被一次绕过验证的合并冲掉。

---

## 二、详细设计说明书（照图施工）

### W1 — CI 流水线

#### 2.1.1 `.github/workflows/ci.yml`（新增）

```yaml
name: CI

on:
  pull_request:
    branches: [master]
  push:
    branches: [master]

concurrency:
  # 同一 PR 的新推送取消旧运行，省 Actions 额度
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  DJANGO_SECRET_KEY: ci-only-not-a-real-secret
  DB_MONITOR_SECRET_KEY: ci-only-not-a-real-secret
  TIMESCALEDB_ENABLED: 'False'
  ES_ENABLED: 'False'
  PYTHONDONTWRITEBYTECODE: '1'

jobs:
  # ── 静态检查：秒级，最先失败 ───────────────────────────────
  static:
    name: 静态检查
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip
      - name: 安装依赖
        run: |
          python -m pip install -U pip
          pip install -r requirements.txt
      - name: 语法编译
        run: python -m compileall -q monitor dbmonitor
      - name: Django 系统检查
        run: python manage.py check
      - name: 迁移漂移检查
        # 模型改了却没生成 migration → 本地能跑、部署即炸
        run: python manage.py makemigrations --check --dry-run
      - name: 依赖完整性
        # 防 BUG-137 复现：requirements 里声明了但装不上/装漏
        run: python scripts/check_deps.py

  # ── 单元测试：无外部服务，最快反馈 ──────────────────────────
  test-unit:
    name: 单元测试（无外部依赖）
    runs-on: ubuntu-latest
    needs: static
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: pip }
      - run: pip install -r requirements.txt
      - name: 运行 unit 层
        env:
          DJANGO_SETTINGS_MODULE: dbmonitor.settings_test_unit
        run: python manage.py test monitor --tag unit -v 2

  # ── 集成测试：真 PostgreSQL，跑全量 ────────────────────────
  test-integration:
    name: 集成测试（PostgreSQL）
    runs-on: ubuntu-latest
    needs: static
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: db_monitor
        ports: ['5432:5432']
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: pip }
      - run: pip install -r requirements.txt
      - name: 全量测试
        env:
          POSTGRES_HOST: 127.0.0.1
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
        run: python manage.py test monitor -v 1
      - name: 随机顺序复跑（查测试间耦合）
        env:
          POSTGRES_HOST: 127.0.0.1
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
        run: python manage.py test monitor --shuffle -v 1

  # ── 方言测试：真实 MySQL/PG，覆盖 checkers ──────────────────
  test-dialect:
    name: 方言测试（MySQL/PG）
    runs-on: ubuntu-latest
    needs: static
    # Oracle 容器体积大启动慢，仅在 master 推送与打了标签的 PR 上跑
    if: github.event_name == 'push' || contains(github.event.pull_request.labels.*.name, 'test-dialect')
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres, POSTGRES_DB: db_monitor }
        ports: ['5432:5432']
        options: --health-cmd pg_isready --health-interval 10s --health-retries 5
      mysql:
        image: mysql:8.0
        env: { MYSQL_ROOT_PASSWORD: root123, MYSQL_DATABASE: testdb }
        ports: ['3306:3306']
        options: >-
          --health-cmd "mysqladmin ping -h 127.0.0.1 -uroot -proot123"
          --health-interval 10s --health-retries 10
      target-pg:
        image: postgres:16
        env: { POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres, POSTGRES_DB: targetdb }
        ports: ['5433:5432']
        options: --health-cmd pg_isready --health-interval 10s --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11', cache: pip }
      - run: pip install -r requirements.txt
      - name: 方言集成测试
        env:
          POSTGRES_HOST: 127.0.0.1
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          DIALECT_MYSQL_DSN: mysql://root:root123@127.0.0.1:3306/testdb
          DIALECT_PG_DSN: postgresql://postgres:postgres@127.0.0.1:5433/targetdb
        run: python manage.py test monitor.tests_dialect -v 2

  # ── 前端 ───────────────────────────────────────────────
  frontend:
    name: 前端构建与测试
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
        working-directory: frontend
      - run: npm run build
        working-directory: frontend
      - name: 组件测试（存在时）
        working-directory: frontend
        run: npm test --if-present -- --run

  # ── 安全 ───────────────────────────────────────────────
  security:
    name: 安全扫描
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - name: 依赖已知漏洞
        run: |
          pip install pip-audit
          pip-audit -r requirements.txt --ignore-vuln GHSA-placeholder || true
        # 注：首次接入设为不阻断（|| true），积累基线后改为强制
      - name: 密钥扫描
        run: python scripts/scan_secrets.py
```

**设计取舍说明**

| 决策 | 理由 |
|------|------|
| `static` 作为所有 job 的 `needs` 前置 | 语法错误时不必启动数据库容器，省时间省额度 |
| `test-unit` 与 `test-integration` 拆开 | unit 秒级反馈；integration 慢但全面。开发者先看 unit 结果 |
| `test-dialect` 默认不在 PR 上跑 | Oracle XE 镜像 >2GB、启动 2–3 分钟。用 label `test-dialect` 按需触发，master 推送必跑 |
| `--shuffle` 复跑一遍 | 本轮实测：`tests_concurrency` 曾在特定顺序下暴露 BUG-119 未修净。顺序敏感的测试必须被抓出来 |
| `pip-audit` 首期 `|| true` | 存量依赖必然有历史 CVE，一上来就阻断会让人直接关掉 CI。先出报告、定基线、再收紧 |
| `concurrency.cancel-in-progress` | 连续推送时取消旧运行 |

#### 2.1.2 分支保护配置（GitHub 仓库设置，手工一次）

`Settings → Branches → Add rule`，`master` 分支：

| 配置项 | 值 | 说明 |
|--------|----|------|
| Require a pull request before merging | ✅ | 禁止直推 master |
| Require status checks to pass | ✅ | 勾选 `静态检查`、`单元测试（无外部依赖）`、`集成测试（PostgreSQL）`、`前端构建与测试` |
| Require branches to be up to date | ✅ | 防止"各自都绿、合了就红" |
| Do not allow bypassing | ✅（含管理员） | 否则门禁形同虚设 |

> ⚠️ 这一步是 W1 的**关键**。ci.yml 只是产生信号，分支保护才让信号有约束力。
> 不做这步，等于装了报警器但不接电源。

#### 2.1.3 `scripts/check_deps.py`（新增）

```python
#!/usr/bin/env python
"""依赖完整性检查：requirements.txt 里声明的包必须真的可导入。

动因（BUG-137）：jsonschema 在 requirements.txt 中声明了，但纯净环境实测缺失，
而 llm/schemas.py 当时的行为是"缺了就跳过校验并放行" —— LLM 输出的结构校验
被静默关闭，而这些输出会驱动 RCA 结论与自动修复预案。
依赖缺失必须在 CI 阶段暴露，而不是等运行时静默降级。
"""
import importlib
import re
import sys
from pathlib import Path

# 包名 → 导入名（不一致的显式映射）
IMPORT_NAME = {
    'psycopg2-binary': 'psycopg2',
    'PyMySQL': 'pymysql',
    'python-dotenv': 'dotenv',
    'elasticsearch': 'elasticsearch',
    'scikit-learn': 'sklearn',
    'APScheduler': 'apscheduler',
    'Django': 'django',
    'PyYAML': 'yaml',
    'pyodbc': 'pyodbc',
}
# 平台相关/可选依赖：缺失只告警不失败
OPTIONAL = {'pyodbc'}


def parse_requirements(path: Path):
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or line.startswith('-'):
            continue
        name = re.split(r'[<>=!~\[]', line, 1)[0].strip()
        if name:
            yield name


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    missing, optional_missing = [], []
    for pkg in parse_requirements(root / 'requirements.txt'):
        mod = IMPORT_NAME.get(pkg, pkg.replace('-', '_'))
        try:
            importlib.import_module(mod)
        except Exception as e:
            (optional_missing if pkg in OPTIONAL else missing).append(f'{pkg} ({mod}): {e}')

    for item in optional_missing:
        print(f'  [可选] 未安装: {item}')
    if missing:
        print('依赖完整性检查失败，以下声明的包无法导入：')
        for item in missing:
            print(f'  - {item}')
        print('\n这类缺失会让依赖它的代码走进静默降级分支（参见 BUG-137）。')
        return 1
    print('依赖完整性检查通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

#### 2.1.4 `scripts/scan_secrets.py`（新增）

Agent H 的 AGENTS.md 把"禁止提交密钥"写成红线，但**没有任何东西检查它**。
红线需要有执行者：

```python
#!/usr/bin/env python
"""密钥扫描：拦截疑似硬编码凭据进入仓库。

扫描范围：git 跟踪的文本文件（不扫 node_modules/migrations/lock 文件）。
策略：宁可误报也不漏报，误报用 # noqa: secret 显式豁免。
"""
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    ('私钥块',        re.compile(r'-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----')),
    ('AWS AccessKey', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('Slack Token',   re.compile(r'\bxox[baprs]-[0-9A-Za-z-]{10,}')),
    ('GitHub Token',  re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}\b')),
    ('通用密钥赋值',   re.compile(
        r'(?i)\b(password|passwd|secret|token|api_?key|access_?key)\b\s*[:=]\s*'
        r'["\'][^"\'\s${}]{8,}["\']')),
]
SKIP_DIRS = {'node_modules', 'migrations', '.git', 'dist', 'staticfiles', '__pycache__', '.venv', 'venv'}
SKIP_FILES = {'package-lock.json', 'requirements.lock'}
# 明显是占位/示例的值不算
PLACEHOLDER = re.compile(
    r'(?i)(change[-_]?me|your[-_]|example|placeholder|xxx+|\*{4,}|dummy|sample|test[-_]?only|ci-only)')


def tracked_files():
    out = subprocess.run(['git', 'ls-files'], capture_output=True, text=True, check=True)
    for line in out.stdout.splitlines():
        p = Path(line)
        if set(p.parts) & SKIP_DIRS or p.name in SKIP_FILES:
            continue
        if p.suffix in {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.woff', '.woff2'}:
            continue
        yield p


def main() -> int:
    hits = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if 'noqa: secret' in line or PLACEHOLDER.search(line):
                continue
            for label, pat in PATTERNS:
                if pat.search(line):
                    hits.append((path, lineno, label, line.strip()[:100]))
    if hits:
        print('检测到疑似硬编码凭据：')
        for path, lineno, label, snippet in hits:
            print(f'  {path}:{lineno}  [{label}]  {snippet}')
        print('\n确认为误报时，在该行加注释 `# noqa: secret`。')
        return 1
    print('密钥扫描通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

---

### W2 — 本地验证入口

#### 2.2.1 `scripts/validate.sh`（新增，替代 H 方案 §2.2）

相对 H 版的改动都在注释里标了原因：

```bash
#!/usr/bin/env bash
# DB-AIOps 改动验证统一入口
#
# 用法: scripts/validate.sh [unit|backend|frontend|all] [--json]
#   unit     : 零外部依赖，SQLite 内存库，~10s —— 编辑循环里用这个
#   backend  : 完整后端（需 PostgreSQL），含迁移漂移检查
#   frontend : npm build (+ vitest)
#   all      : backend + frontend
# 退出码: 0=全部通过; 1=任一检查失败; 2=用法/环境错误
#
# 与 CI 的关系：本脚本与 .github/workflows/ci.yml 检查同一批内容，
# 本地先跑一遍可以避免把必然失败的 PR 推上去。CI 才是强制门禁。
#
# 注：用 bash 而非 zsh —— CI runner 与多数 Linux 开发机默认无 zsh。
set -uo pipefail

MODE="all"
JSON=0
for arg in "$@"; do
  case "$arg" in
    unit|backend|frontend|all) MODE="$arg" ;;
    --json) JSON=1 ;;
    -h|--help) echo "用法: $0 [unit|backend|frontend|all] [--json]"; exit 0 ;;
    *) echo "未知参数: $arg" >&2; echo "用法: $0 [unit|backend|frontend|all] [--json]" >&2; exit 2 ;;
  esac
done

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 2

# 虚拟环境：兼容 venv/ 与 .venv/ 两种惯例
for v in .venv venv; do
  [ -f "$v/bin/activate" ] && { . "$v/bin/activate"; break; }
done

STAGES=()   # 形如 "阶段名|状态|耗时ms"
FAIL=0

_run() {   # _run <阶段名> <命令...>
  local name="$1"; shift
  local start end rc
  start=$(date +%s%3N 2>/dev/null || echo $(( $(date +%s) * 1000 )))
  [ "$JSON" -eq 0 ] && echo "=== [$name] ==="
  if [ "$JSON" -eq 1 ]; then "$@" >/dev/null 2>&1; rc=$?; else "$@"; rc=$?; fi
  end=$(date +%s%3N 2>/dev/null || echo $(( $(date +%s) * 1000 )))
  if [ $rc -eq 0 ]; then
    STAGES+=("$name|pass|$((end-start))")
  else
    STAGES+=("$name|fail|$((end-start))")
    FAIL=1
    [ "$JSON" -eq 0 ] && echo "FAIL: $name"
  fi
  return $rc
}

run_unit() {
  # 关键改动（对比 H 方案）：unit 层用独立 settings，走 SQLite 内存库，
  # 不需要 Docker、不需要 PostgreSQL。降低验证门槛才能真正提高验证率。
  DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_unit \
    _run "unit 测试" python manage.py test monitor --tag unit -v 1
}

run_backend() {
  _run "语法编译"    python -m compileall -q monitor dbmonitor || return 1
  _run "Django 系统检查" python manage.py check                || return 1
  # 新增（H 方案缺失）：模型改了没生成 migration 是最高频的"本地能跑、部署即炸"
  _run "迁移漂移检查" python manage.py makemigrations --check --dry-run || return 1
  _run "依赖完整性"  python scripts/check_deps.py               || return 1
  # 改动（H 方案硬编码了测试模块清单，已漏掉 tests_phase7 等）：
  # 改为自动发现，新增测试文件无需改脚本
  _run "全量测试"    python manage.py test monitor -v 1         || return 1
  return 0
}

run_frontend() {
  ( cd frontend && { [ -d node_modules ] || npm ci; } && npm run build ) \
    && _run "前端构建" true || _run "前端构建" false
  if [ -f frontend/package.json ] && grep -q '"test"' frontend/package.json; then
    ( cd frontend && npm test -- --run ) && _run "前端测试" true || _run "前端测试" false
  fi
}

case "$MODE" in
  unit)     run_unit ;;
  backend)  run_backend ;;
  frontend) run_frontend ;;
  all)      run_backend; run_frontend ;;
esac

if [ "$JSON" -eq 1 ]; then
  printf '{"mode":"%s","exit_code":%d,"stages":[' "$MODE" "$FAIL"
  for i in "${!STAGES[@]}"; do
    IFS='|' read -r n s d <<< "${STAGES[$i]}"
    [ "$i" -gt 0 ] && printf ','
    printf '{"name":"%s","status":"%s","duration_ms":%s}' "$n" "$s" "$d"
  done
  printf '],"git_rev":"%s"}\n' "$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
else
  echo
  for st in "${STAGES[@]}"; do
    IFS='|' read -r n s d <<< "$st"
    printf '  %-16s %-4s %sms\n' "$n" "$s" "$d"
  done
  [ $FAIL -eq 0 ] && echo "===> 验证全部通过" || echo "===> 验证失败，禁止提交"
fi
exit $FAIL
```

#### 2.2.2 `--json` 输出契约（补 A1）

Agent 消费 stdout 文本是脆的。结构化输出：

```json
{
  "mode": "backend",
  "exit_code": 1,
  "stages": [
    {"name": "语法编译",        "status": "pass", "duration_ms": 1820},
    {"name": "Django 系统检查",  "status": "pass", "duration_ms": 940},
    {"name": "迁移漂移检查",     "status": "fail", "duration_ms": 1100}
  ],
  "git_rev": "297c19e"
}
```

Agent 只需读 `exit_code` 与首个 `status == "fail"` 的 `name`，无需解析人类文案。

#### 2.2.3 `scripts/pre-commit` 与安装脚本

沿用 Agent H 的设计（§2.4），但**明确降级定位**：

```bash
#!/usr/bin/env bash
# pre-commit: 秒级本地反馈。
#
# 定位说明（相对 Agent H 方案的调整）：
# 本钩子**不是**质量门禁 —— 它是本地文件、不随仓库分发、可用 --no-verify 跳过、
# 且对在云端容器里工作的 Agent 完全无效。真正的门禁是 GitHub Actions + 分支保护。
# 这里只做秒级检查，帮开发者在本地提前发现低级错误。
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 0
for v in .venv venv; do [ -f "$v/bin/activate" ] && { . "$v/bin/activate"; break; }; done

STAGED_PY=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)
if [ -n "$STAGED_PY" ]; then
  echo "[pre-commit] 语法检查 $(echo "$STAGED_PY" | wc -l | tr -d ' ') 个文件"
  # shellcheck disable=SC2086
  python -m compileall -q $STAGED_PY || { echo "[pre-commit] 语法错误"; exit 1; }
  python manage.py check >/dev/null 2>&1 || { echo "[pre-commit] manage.py check 未通过"; exit 1; }
fi
python scripts/scan_secrets.py >/dev/null 2>&1 || {
  echo "[pre-commit] 疑似密钥，运行 python scripts/scan_secrets.py 查看"; exit 1; }
echo "[pre-commit] PASS"
```

`scripts/install-hooks.sh` 沿用 H 方案原样（`cp` + `chmod +x`）。

---

### W3 — 测试基础设施分层

#### 2.3.1 分层定义

| 层 | tag | 外部依赖 | 目标耗时 | 覆盖对象 |
|----|-----|---------|---------|---------|
| L1 unit | `unit` | 无（SQLite 内存） | < 15s | 纯逻辑：阻塞树算法、指纹、等待类映射、schema 校验、聚合窗口 |
| L2 integration | `integration` | PostgreSQL | < 60s | ORM 语义：事务、约束、迁移、并发行锁 |
| L3 dialect | `dialect` | MySQL/PG/Oracle 容器 | < 5min | `checkers/`(6077 行) 与 `sentinel` 的 SQL 方言正确性 |

#### 2.3.2 `dbmonitor/settings_test_unit.py`（新增）

```python
"""unit 层测试配置：零外部依赖。

存在的意义（对应诊断 D2/A2）：完整测试需要 PostgreSQL 容器，
门槛一高，编辑循环里就没人跑了 —— 这正是"93 次编辑 0 次验证"的成因。
本配置让纯逻辑测试在 10 秒内跑完，且不需要任何 Docker。
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
# 外部服务一律关闭，避免用例误触真实依赖
TIMESCALEDB_ENABLED = False
ES_ENABLED = False
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']  # 仅测试提速
LOGGING_CONFIG = None   # 保留测试中的 assertLogs 行为
```

> ⚠️ **重要限制，必须写清楚**：SQLite 不支持 `select_for_update` 的真实行锁语义，
> 也没有部分唯一索引的完整行为。因此 **BUG-105/BUG-119 这类并发用例必须标 `integration`**，
> 不能放进 unit 层，否则会得到"过了但其实没验"的假绿。
> 这是分层最容易踩的坑，评审时请重点确认标签划分。

#### 2.3.3 现有测试打标（改造）

```python
from django.test import TestCase, tag

@tag('unit')          # 纯算法，无 DB 语义依赖
class Bug111DeadlockCycleTests(TestCase): ...

@tag('integration')   # 依赖 PostgreSQL 行锁
class AuditExecuteConcurrencyTests(TransactionTestCase): ...
```

**打标清单（照此执行）**

| 测试类 | 标签 | 依据 |
|--------|------|------|
| `Bug111DeadlockCycleTests` | unit | 纯图算法 |
| `Bug126AasZeroFillTests` | unit | 游标已 mock |
| `Bug101TimeseriesPoolTests` | unit | 连接池已 mock |
| `Bug128BatchWriteTests` / `Bug129DropHypertableTests` | unit | 全 mock |
| `Bug112RedisBusTests` | unit | redis 已 mock |
| `Bug114OracleObjCacheTests` | unit | 纯内存 LRU |
| `Bug123WaitSecsTests` / `Bug124OracleSqlTextTests` | unit | 游标已 mock |
| `Bug106LoginThrottleTests` | unit | LocMemCache |
| `Bug103PerfAuthorizationTests` | integration | 需真实用户/权限表 |
| `Bug105AuditWorkflowTests` | **integration** | 依赖 `select_for_update` |
| `Bug119PlanCurrentTests` | **integration** | 依赖部分唯一索引 |
| `Bug130AllowedDatabaseFkTests` | **integration** | 依赖外键级联 |
| `tests_concurrency.py` 全部 | **integration** | 全部依赖真实并发语义 |

#### 2.3.4 L3 方言测试 `monitor/tests_dialect.py`（新增）

这是本方案**投入产出比最高**的一块：`checkers/` 6077 行零覆盖，
而 `docker-compose.dev.yml` 里 MySQL/Oracle/PG 容器**早就有了**，只是没接上测试。

```python
# -*- coding: utf-8 -*-
"""L3 方言测试：针对真实数据库验证 checkers/ 与 sentinel 的 SQL 正确性。

为什么必须用真实数据库：checkers/ 里是 6 种数据库的方言 SQL
（information_schema / v$session / pg_stat_activity ...），
mock 游标只能验证"代码路径走到了"，验证不了"这条 SQL 在目标库上真的能跑、
真的返回预期结构"。本轮审计发现的 BUG-123（MySQL 锁等待字段取错）、
BUG-124（Oracle 未采 sql_text）都属于"语法没错但语义错"，只有真库能抓。

跳过策略：未提供对应 DSN 环境变量时 skip，本地开发不受影响。
"""
import os
import unittest
from urllib.parse import urlparse

from django.test import TransactionTestCase, tag

from monitor.crypto import encrypt_password
from monitor.models import DatabaseConfig

MYSQL_DSN = os.environ.get('DIALECT_MYSQL_DSN')
PG_DSN = os.environ.get('DIALECT_PG_DSN')
ORACLE_DSN = os.environ.get('DIALECT_ORACLE_DSN')


def _cfg_from_dsn(dsn, db_type, name):
    u = urlparse(dsn)
    return DatabaseConfig.objects.create(
        name=name, db_type=db_type, host=u.hostname, port=u.port,
        username=u.username, password=encrypt_password(u.password or ''),
        service_name=(u.path or '/').lstrip('/') or None, is_active=True)


@tag('dialect')
@unittest.skipUnless(MYSQL_DSN, 'DIALECT_MYSQL_DSN 未设置')
class MySQLDialectTests(TransactionTestCase):
    def setUp(self):
        self.cfg = _cfg_from_dsn(MYSQL_DSN, 'mysql', 'dialect-mysql')

    def _conn(self):
        from monitor.db_connector import DbConnector
        return DbConnector.get_connection(self.cfg, statement_timeout_ms=5000, readonly=True)

    def test_connection_applies_statement_timeout(self):
        """BUG-109 回归：语句超时必须真的生效，而不只是"调用了 SET"。"""
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT @@SESSION.max_execution_time AS t")
            row = cur.fetchone()
            self.assertEqual(int(row['t']), 5000)
        finally:
            conn.close()

    def test_ash_sampling_returns_expected_shape(self):
        """ASH 采样 SQL 必须能在真实 MySQL 上执行并返回约定字段。"""
        from monitor.sentinel import sample_sessions
        conn = self._conn()
        try:
            cur = conn.cursor()
            rows = sample_sessions(cur, 'mysql')
        finally:
            conn.close()
        self.assertIsInstance(rows, list)
        for r in rows:
            for key in ('session_id', 'wait_class', 'sql_digest',
                        'is_blocked', 'wait_secs', 'trx_age_secs'):
                self.assertIn(key, r, f'ASH 行缺字段 {key}')

    def test_blocking_edge_reports_lock_wait_not_trx_age(self):
        """BUG-123 回归（真库版）：制造真实锁等待，验证 wait_secs 是锁等待时长
        而不是事务年龄。这是 mock 测不出来的 —— mock 里两个值都是我们自己填的。"""
        import threading
        import time
        from monitor.sentinel import sample_sessions

        setup = self._conn()
        cur = setup.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS lock_probe (id INT PRIMARY KEY, v INT)")
        cur.execute("REPLACE INTO lock_probe VALUES (1, 0)")
        setup.commit()

        holder = self._conn()
        hcur = holder.cursor()
        hcur.execute("BEGIN")
        hcur.execute("UPDATE lock_probe SET v = v + 1 WHERE id = 1")
        time.sleep(4)                      # 让持锁事务先"老"起来

        waiter = self._conn()
        err = []

        def block():
            try:
                wcur = waiter.cursor()
                wcur.execute("BEGIN")
                wcur.execute("UPDATE lock_probe SET v = v + 1 WHERE id = 1")
            except Exception as e:
                err.append(e)

        t = threading.Thread(target=block, daemon=True)
        t.start()
        time.sleep(2)                      # 等待者已阻塞约 2s，持锁事务已 6s

        probe = self._conn()
        try:
            rows = sample_sessions(probe.cursor(), 'mysql')
        finally:
            probe.close()

        blocked = [r for r in rows if r.get('is_blocked')]
        self.assertTrue(blocked, '应能观测到被阻塞会话')
        w = blocked[0]
        self.assertLess(w['wait_secs'], 4,
                        f"wait_secs 应为锁等待时长(~2s)，实际 {w['wait_secs']} "
                        f"—— 疑似又取成了事务年龄(BUG-123)")

        holder.rollback(); holder.close()
        try:
            waiter.rollback(); waiter.close()
        except Exception:
            pass
        cur.execute("DROP TABLE IF EXISTS lock_probe"); setup.commit(); setup.close()

    def test_explain_capture_on_real_table(self):
        """plan_capture 对真实表的 EXPLAIN 必须产出可解析的计划。"""
        from monitor.plan_capture import capture
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS plan_probe (id INT PRIMARY KEY, name VARCHAR(32))")
            conn.commit()
            plan = capture(self.cfg, 'digest-probe',
                           sql_text='SELECT * FROM plan_probe WHERE id = 1',
                           source='manual', conn=conn)
            self.assertIsNotNone(plan, 'EXPLAIN 应成功采集')
            self.assertTrue(plan.plan_text)
            cur.execute("DROP TABLE IF EXISTS plan_probe"); conn.commit()
        finally:
            conn.close()


@tag('dialect')
@unittest.skipUnless(PG_DSN, 'DIALECT_PG_DSN 未设置')
class PostgresDialectTests(TransactionTestCase):
    def setUp(self):
        self.cfg = _cfg_from_dsn(PG_DSN, 'pgsql', 'dialect-pg')

    def _conn(self, readonly=True):
        from monitor.db_connector import DbConnector
        return DbConnector.get_connection(self.cfg, statement_timeout_ms=3000, readonly=readonly)

    def test_statement_timeout_actually_cancels(self):
        """BUG-109 回归：pg_sleep(10) 必须在 3s 内被取消。"""
        import psycopg2
        conn = self._conn()
        try:
            cur = conn.cursor()
            with self.assertRaises(psycopg2.errors.QueryCanceled):
                cur.execute("SELECT pg_sleep(10)")
        finally:
            conn.close()

    def test_no_idle_in_transaction_left_behind(self):
        """BUG-110 回归（本方案最该守住的一条）：
        采集连接查询后不得停留在 idle in transaction —— 否则压住 xmin，
        阻塞被监控库的 VACUUM，监控工具反过来损害被监控对象。"""
        from monitor.sentinel import sample_sessions
        conn = self._conn()
        try:
            cur = conn.cursor()
            sample_sessions(cur, 'pgsql')
            observer = self._conn()
            ocur = observer.cursor()
            ocur.execute(
                "SELECT state FROM pg_stat_activity "
                "WHERE pid = %s", (conn.get_backend_pid(),))
            state = (ocur.fetchone() or [None])[0]
            observer.close()
            self.assertNotEqual(state, 'idle in transaction',
                                '采集连接残留在事务中，会压住 xmin 阻塞 VACUUM')
        finally:
            conn.close()

    def test_readonly_session_rejects_writes(self):
        """采集连接必须只读：即便某条采集 SQL 写错，也不能改动被监控库。"""
        import psycopg2
        conn = self._conn(readonly=True)
        try:
            cur = conn.cursor()
            with self.assertRaises(psycopg2.Error):
                cur.execute("CREATE TABLE should_not_exist (id int)")
        finally:
            conn.close()
```

**新增依赖**：无（用的都是既有驱动）。
**CI 接入**：见 §2.1.1 `test-dialect` job。
**本地运行**：
```bash
docker-compose -f docker-compose.dev.yml up -d mysql postgres-monitor
export DIALECT_MYSQL_DSN=mysql://root:root123@127.0.0.1:3306/testdb
export DIALECT_PG_DSN=postgresql://postgres:postgres@127.0.0.1:5433/targetdb
python manage.py test monitor.tests_dialect -v 2
```

---

### W4 — 系统自监控（本方案唯一新增业务能力）

#### 2.4.1 问题陈述

BUG-113 修复后，哨兵线程异常会被捕获、线程死亡会被 `_refresh()` 重建。
但仍有一个洞没堵：**如果整个哨兵进程没起来，或者 Redis 消费者挂了，谁来发现？**

现状是没有人。系统能监控 6 种外部数据库，却对自己的 5 类内部组件一无所知：

| 组件 | 进程 | 挂掉的后果 | 当前可发现性 |
|------|------|-----------|-------------|
| 采集器 | `start_monitor` | 所有指标停止更新 | 只能靠人看图表变平 |
| 哨兵 | `start_sentinel` | ASH/阻塞检测全停，性能中心空白 | 无 |
| 流水线消费者 | `start_pipeline` | 事件不再转成事故 | 无 |
| 时序写入 | 进程内 | 图表断点 | 只有日志 |
| 通知发送 | 进程内 | 告警发不出去 | 无 |

**"告警发不出去"这条尤其致命**：系统看起来一切正常，实际上已经聋了。

#### 2.4.2 设计

心跳上报 + 缺失检测，复用既有告警链路：

```
各组件主循环 ──每 N 秒──> ComponentHeartbeat.report(component, instance, meta)
                                    │ (upsert, 只有一行/组件+实例)
                                    ▼
              HeartbeatMonitor（挂在既有采集调度里，每 60s 跑一次）
                                    │
                   last_beat_at 超过 stale_after_sec ?
                                    │ 是
                                    ▼
                  AlertManager.fire('component_down', ...) 走既有通知链路
```

**关键设计取舍**：不引入 Prometheus/StatsD 等新组件。
理由是本项目本身就有完整的告警链路（AlertManager + NotificationRule + 三渠道），
自监控复用它即可，避免"为了监控监控系统而再引入一套监控系统"的无限递归。

#### 2.4.3 心跳上报接入点（照图施工）

`monitor/self_monitor.py`（新增）：

```python
# -*- coding: utf-8 -*-
"""系统自监控：组件心跳上报与失联检测。

动因：本项目能监控 6 种外部数据库，却对自身的采集器/哨兵/流水线消费者
一无所知。BUG-113 之前，哨兵线程异常退出后永久静默，监控大盘毫无异常，
直到有人发现某个库的性能数据断了几天。

设计原则：复用既有告警链路（AlertManager），不引入新的监控组件。
"""
import logging
import os
import socket

from django.utils import timezone

logger = logging.getLogger(__name__)

# 组件编码 → (显示名, 心跳间隔秒, 判定失联秒)
# 失联阈值取心跳间隔的 3 倍余量，避免单次抖动误报
COMPONENTS = {
    'collector': ('指标采集器', 60, 300),
    'sentinel': ('哨兵/ASH采样', 30, 180),
    'pipeline': ('事件流水线消费者', 60, 300),
    'notifier': ('通知发送器', 300, 1200),
}


def instance_id() -> str:
    """区分同一组件的多副本部署。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def report(component: str, meta: dict = None) -> None:
    """上报一次心跳。失败只记日志，绝不影响主流程。"""
    if component not in COMPONENTS:
        logger.warning("[self_monitor] 未知组件编码: %s", component)
        return
    try:
        from monitor.models import ComponentHeartbeat
        ComponentHeartbeat.objects.update_or_create(
            component=component, instance=instance_id(),
            defaults={'last_beat_at': timezone.now(),
                      'meta': meta or {}, 'status': 'up'},
        )
    except Exception as e:
        logger.debug("[self_monitor] 心跳上报失败 %s: %s", component, e)


def check_stale(now=None) -> list:
    """检测失联组件，返回 [{component, instance, silent_sec, display}]。

    由 HeartbeatMonitor 周期调用。已标记 down 的不重复返回（去重交给 AlertManager）。
    """
    from monitor.models import ComponentHeartbeat
    now = now or timezone.now()
    stale = []
    for hb in ComponentHeartbeat.objects.all():
        spec = COMPONENTS.get(hb.component)
        if not spec:
            continue
        display, _interval, stale_after = spec
        silent = (now - hb.last_beat_at).total_seconds()
        if silent > stale_after:
            stale.append({'component': hb.component, 'instance': hb.instance,
                          'silent_sec': int(silent), 'display': display,
                          'stale_after': stale_after})
    return stale


def run_heartbeat_check() -> int:
    """扫描失联组件并发告警。返回本轮发现的失联数。

    复用 AlertManager：去重、静默窗口、通知规则、升级策略全都现成。
    """
    from monitor.alert_manager import AlertManager
    from monitor.models import ComponentHeartbeat, DatabaseConfig

    stale = check_stale()
    # 自监控告警不绑定具体纳管实例，挂在"系统"伪实例上；
    # 无该实例时降级为只记日志，不因为缺配置而让自监控失效
    sys_cfg = DatabaseConfig.objects.filter(name='__system__').first()
    for item in stale:
        msg = (f"{item['display']}（{item['instance']}）已 {item['silent_sec']} 秒"
               f"未上报心跳，超过 {item['stale_after']} 秒阈值")
        logger.error("[self_monitor] %s", msg)
        ComponentHeartbeat.objects.filter(
            component=item['component'], instance=item['instance']).update(status='down')
        if sys_cfg:
            AlertManager(sys_cfg).fire(
                alert_type='component_down', metric_key=item['component'],
                title=f"[自监控] {item['display']} 失联", description=msg,
                severity='critical')
    # 恢复：曾 down 且现在心跳正常的，解除告警
    stale_keys = {(s['component'], s['instance']) for s in stale}
    for hb in ComponentHeartbeat.objects.filter(status='down'):
        if (hb.component, hb.instance) not in stale_keys:
            hb.status = 'up'
            hb.save(update_fields=['status'])
            if sys_cfg:
                AlertManager(sys_cfg).resolve('component_down', hb.component)
    return len(stale)
```

**接入点清单（逐处照改）**

| 文件 | 位置 | 插入代码 |
|------|------|---------|
| `monitor/sentinel.py` | `InstanceSentinel.run_loop()` 循环内 `finally` 之后 | `from monitor.self_monitor import report; report('sentinel', {'config_id': self.config.id})` |
| `monitor/sentinel.py` | `SentinelManager.run()` 循环内 | `report('sentinel', {'instances': len(self.sentinels)})` |
| `monitor/management/commands/start_monitor.py` | 每轮采集结束 | `report('collector', {'db_count': len(configs)})` |
| `monitor/management/commands/start_monitor.py` | 与 `flush_expired_aggregations()` 同处 | `run_heartbeat_check()` |
| 流水线消费者 | 主循环 | `report('pipeline')` |
| `monitor/alert_manager.py` | `_send_to_channels()` 成功后 | `report('notifier')` |

---

### W5 — 配置治理

#### 2.5.1 问题

- BUG-138：`getattr(settings, 'LOGIN_MAX_ATTEMPTS', 5)` 在模块导入时求值 → 配置项形同虚设
- BUG-127：`ASH_INTERVAL_SEC` 默认值一处 5、一处 15 → AAS 计算偏差 3 倍
- 无启动校验：配置写错（如 `TIMESCALEDB_POOL_MAX=0`）要到运行时才炸

#### 2.5.2 `monitor/appconf.py`（新增，配置单一事实源）

```python
# -*- coding: utf-8 -*-
"""运行期配置单一事实源。

三条铁律（都是本轮踩过的坑）：
1. **调用时求值**，不在模块导入时固化 —— 否则配置项写了不生效（BUG-138），
   且 override_settings 无效导致安全逻辑无法被测试覆盖。
2. **默认值只在此处定义一次** —— 散落各处的 getattr 默认值会漂移（BUG-127
   的 ASH_INTERVAL_SEC 一处 5 一处 15，直接导致 AAS 计算偏差 3 倍）。
3. **启动时校验**，非法值立刻失败，不拖到运行期。
"""
from dataclasses import dataclass
from typing import Any, Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class Spec:
    name: str
    default: Any
    cast: Callable[[Any], Any]
    validate: Callable[[Any], bool] = lambda v: True
    hint: str = ''


SPECS = {
    # 采集与哨兵
    'ASH_INTERVAL_SEC':        Spec('ASH_INTERVAL_SEC', 5, int, lambda v: 1 <= v <= 300,
                                    '1–300 秒；与 session_ash_1m 的 sample_gap_sec 语义绑定'),
    'SENTINEL_INTERVAL_SEC':   Spec('SENTINEL_INTERVAL_SEC', 8, int, lambda v: 1 <= v <= 300),
    'SENTINEL_FAIL_THRESHOLD': Spec('SENTINEL_FAIL_THRESHOLD', 3, int, lambda v: v >= 1),
    'SENTINEL_CONN_MAX_AGE_SEC': Spec('SENTINEL_CONN_MAX_AGE_SEC', 1800, int, lambda v: v >= 0),
    # 目标库与时序库
    'TARGET_DB_STATEMENT_TIMEOUT_MS': Spec('TARGET_DB_STATEMENT_TIMEOUT_MS', 5000, int,
                                           lambda v: 500 <= v <= 60000),
    'TIMESCALEDB_POOL_MAX':    Spec('TIMESCALEDB_POOL_MAX', 16, int, lambda v: 1 <= v <= 200),
    # 安全
    'LOGIN_MAX_ATTEMPTS':      Spec('LOGIN_MAX_ATTEMPTS', 5, int, lambda v: v >= 1),
    'LOGIN_FAIL_WINDOW_SEC':   Spec('LOGIN_FAIL_WINDOW_SEC', 600, int, lambda v: v >= 30),
    'LOGIN_LOCKOUT_SEC':       Spec('LOGIN_LOCKOUT_SEC', 900, int, lambda v: v >= 30),
    'LOGIN_MAX_ATTEMPTS_PER_USER': Spec('LOGIN_MAX_ATTEMPTS_PER_USER', 20, int, lambda v: v >= 1),
    'TRUSTED_PROXY_DEPTH':     Spec('TRUSTED_PROXY_DEPTH', 1, int, lambda v: v >= 1),
    'API_KEY_TTL_SEC':         Spec('API_KEY_TTL_SEC', 90 * 86400, int, lambda v: v >= 300),
}


def get(name: str):
    """读取配置（调用时求值）。"""
    spec = SPECS.get(name)
    if spec is None:
        raise KeyError(f'未登记的配置项: {name}（请先在 appconf.SPECS 中声明）')
    raw = getattr(settings, name, spec.default)
    try:
        value = spec.cast(raw)
    except Exception as e:
        raise ImproperlyConfigured(f'{name}={raw!r} 类型非法: {e}') from e
    if not spec.validate(value):
        raise ImproperlyConfigured(
            f'{name}={value!r} 取值非法。{spec.hint or "请查阅 appconf.SPECS"}')
    return value


def validate_all() -> list:
    """启动自检：返回错误消息列表，空列表表示全部合法。"""
    errors = []
    for name in SPECS:
        try:
            get(name)
        except ImproperlyConfigured as e:
            errors.append(str(e))
    return errors
```

#### 2.5.3 接入 Django system check（启动即校验）

`monitor/checks.py`（新增）：

```python
from django.core.checks import Error, register


@register()
def check_appconf(app_configs, **kwargs):
    """把配置校验挂到 manage.py check —— CI 与本地验证都会跑到。"""
    from monitor.appconf import validate_all
    return [Error(msg, id='monitor.E001') for msg in validate_all()]
```

在 `monitor/apps.py` 的 `ready()` 中 `from monitor import checks  # noqa`。

#### 2.5.4 存量替换（逐处照改）

| 文件 | 原写法 | 改为 |
|------|--------|------|
| `monitor/auth.py` | `_login_max_attempts()` 等 4 个函数 | `appconf.get('LOGIN_MAX_ATTEMPTS')` |
| `monitor/sentinel.py` | `ash_interval_sec()` / `sentinel_interval_sec()` | `appconf.get('ASH_INTERVAL_SEC')` |
| `monitor/db_connector.py` | `_stmt_timeout_ms()` | `appconf.get('TARGET_DB_STATEMENT_TIMEOUT_MS')` |
| `monitor/timeseries.py` | `getattr(settings, 'TIMESCALEDB_POOL_MAX', 16)` | `appconf.get('TIMESCALEDB_POOL_MAX')` |

---

### W6 — 失败姿态整改

#### 2.6.1 问题

169 处 `except Exception: pass`。对监控系统而言这是反的：静默降级 = 监控停了但没人知道。

#### 2.6.2 分级策略

不搞"全部改成抛异常"（会把系统改脆），而是按**失败的业务含义**分三级：

| 级别 | 语义 | 处理 | 适用 |
|------|------|------|------|
| **L1 可忽略** | 失败不影响正确性 | `logger.debug` + 继续 | 缓存预热、可选字段补查（如 Oracle 对象名） |
| **L2 需留痕** | 功能降级，结果仍可用但不完整 | `logger.warning` + **计数器 +1** + 响应标 `degraded` | ASH 单次采样失败、某项指标采不到 |
| **L3 必须失败** | 静默放行会导致错误结论 | 抛异常/返回错误 | LLM 输出校验（BUG-137）、SQL 安全校验、权限判定 |

#### 2.6.3 `monitor/degrade.py`（新增）

```python
# -*- coding: utf-8 -*-
"""降级留痕：让"静默降级"变成"有记录的降级"。

169 处 except-pass 里，多数本身是合理的（可选字段补查失败不该让整轮采集崩）。
问题不在于捕获异常，而在于**捕获之后没有任何痕迹** —— 一个功能可能已经
降级运行了几个月，没人知道。这里提供统一的计数与查询入口。
"""
import logging
import threading
from collections import Counter

logger = logging.getLogger(__name__)

_COUNTS = Counter()
_LOCK = threading.Lock()


def note(scope: str, reason: str = '', exc: Exception = None) -> None:
    """记一次降级。scope 形如 'ash.oracle_objname'、'notify.dingtalk'。"""
    with _LOCK:
        _COUNTS[scope] += 1
        count = _COUNTS[scope]
    # 前 3 次与每 100 次打 warning，其余 debug —— 既不淹没日志也不彻底静默
    level = logging.WARNING if count <= 3 or count % 100 == 0 else logging.DEBUG
    logger.log(level, '[degrade] %s (第 %d 次)%s%s', scope, count,
               f' 原因={reason}' if reason else '',
               f' 异常={exc.__class__.__name__}: {exc}' if exc else '')


def snapshot() -> dict:
    """当前累计降级计数，供 /api/v1/system/degradations 暴露。"""
    with _LOCK:
        return dict(_COUNTS)


def reset() -> None:
    with _LOCK:
        _COUNTS.clear()
```

#### 2.6.4 改造优先级（不要求一次改完 169 处）

| 批次 | 范围 | 处理 |
|------|------|------|
| B1 | 采集主路径 `checkers/*.py`、`sentinel.py`（约 40 处） | L2：加 `degrade.note()` |
| B2 | 通知发送 `notifications.py`、`alert_manager.py`（约 15 处） | L2：加计数，"告警发不出去"必须可见 |
| B3 | 安全相关：SQL 校验、权限、schema 校验（约 10 处） | L3：改为显式失败 |
| B4 | 其余 | 逐步随功能改动迁移，不单独立项 |

---

### W7 — 协作 harness（继承 Agent H）

#### 2.7.1 `AGENTS.md`

采用 Agent H §2.1 的内容框架，修正三处事实错误并补充：

```markdown
# DB-AIOps 项目协作指南

## 项目概述
Django 后端 + React 前端的数据库智能运维平台：纳管 MySQL/PostgreSQL/Oracle/达梦/GBase/TDSQL，
提供监控采集、告警引擎、性能中心（对标 Oracle EMCC）、事故诊断、自治修复、巡检报告。

## 目录结构
- `dbmonitor/`      Django 工程配置（settings/urls）
- `monitor/`        核心业务应用
  - `checkers/`     6 种数据库的指标采集器（6077 行，方言密集区）
  - `detectors/`    L1/L2/L3 异常检测
  - `llm/`          LLM 调用与输出 schema 校验
  - `management/commands/`  常驻进程：start_monitor / start_sentinel / start_pipeline
- `frontend/`       React + Vite（src/pages 页面、src/services/api.js 接口封装）
- `scripts/`        开发与验证脚本
- `docker-compose.dev.yml`  开发基础设施（含 MySQL/Oracle/PG/GBase/TDSQL 被监控库容器）

## 启动
    docker-compose -f docker-compose.dev.yml up -d
    python manage.py migrate && python manage.py runserver 0.0.0.0:8000
    cd frontend && npm run dev

## 编辑后验证（强制）
| 改动范围 | 命令 | 耗时 |
|---------|------|------|
| 编辑循环中随时 | `scripts/validate.sh unit` | ~10s，无需 Docker |
| 提交前（后端） | `scripts/validate.sh backend` | ~1min，需 PostgreSQL |
| 提交前（前端） | `scripts/validate.sh frontend` | ~30s |
| 两侧都改 | `scripts/validate.sh all` | ~2min |

Agent 消费结果请加 `--json`，读 `exit_code` 与首个 `status=="fail"` 的 `name`。

**CI 才是强制门禁**：PR 必须通过 `.github/workflows/ci.yml` 的静态检查、单元测试、
集成测试、前端构建四项。本地验证只是提前发现问题，不能替代 CI。

## 改动数据库模型时
1. 先出设计文档（参考 `BUGFIX_DESIGN.md` 的粒度）
2. `python manage.py makemigrations`
3. **迁移必须处理存量脏数据**：加约束前先清理违约行（参考 `0019`/`0021` 的写法）
4. `scripts/validate.sh backend` 会用 `makemigrations --check` 拦住"改了模型没生成迁移"

## 红线约束
- 禁止提交 `.env`、密钥、密码（`scripts/scan_secrets.py` 会在 pre-commit 与 CI 拦截）
- 不得删除或覆盖已有 migration 文件
- 新增配置项必须登记到 `monitor/appconf.py` 的 SPECS（否则运行期读不到、也不会被校验）
- 捕获异常后不得完全静默：按 `monitor/degrade.py` 的分级留痕

## 已知陷阱（踩过的坑，别再踩）
- 时序库访问必须用 `get_timeseries_storage().cursor()` 上下文管理器借还连接，
  不要持有裸连接（BUG-101：单连接多线程共用会导致结果集串台）
- 目标库连接一律经 `DbConnector.get_connection(cfg, statement_timeout_ms=..., readonly=True)`，
  不要直接调驱动（BUG-109/110：漏超时会拖垮 Web 层，漏 readonly 会污染被监控库）
- 并发相关的测试必须标 `@tag('integration')`：SQLite 没有真实行锁语义，
  放进 unit 层会得到假绿（参见 W3.2 的限制说明）
- 涉及"唯一性"的业务不变量，光靠应用层加锁不够，要有数据库约束兜底（BUG-119）
```

#### 2.7.2 Memory 治理

沿用 Agent H §2.5 操作步骤，无异议。

---

## 三、数据库设计

### 3.1 变更范围声明

| 工作流 | Schema 影响 |
|--------|------------|
| W1 CI、W2 验证、W5 配置、W6 降级、W7 harness | **无任何数据库变更** |
| W3 测试分层 | 无（仅新增 settings 模块与测试文件） |
| **W4 自监控** | **新增 1 张表 `ComponentHeartbeat`** |

**明确反对 Agent H §3.2 的 `validation_record` 表**（理由见序章 S.3）。
验证遥测的落地方案：

| 场景 | 落地 | 理由 |
|------|------|------|
| 本地 | `.validation/history.jsonl`（追加写，`.gitignore`） | 零 schema、零依赖，不需要业务库可连 |
| CI | Actions `$GITHUB_STEP_SUMMARY` + artifact | 天然有留存与 UI，无需自建 |

### 3.2 新增表 `ComponentHeartbeat`

```python
class ComponentHeartbeat(models.Model):
    """组件心跳（W4 自监控）。

    每个 (component, instance) 一行，upsert 更新，不做时序留存 ——
    历史趋势不是目标，"现在还活着吗"才是。行数上界 = 组件数 × 副本数，
    通常 < 20 行，无需分区或清理策略。
    """
    COMPONENT_CHOICES = (
        ('collector', '指标采集器'),
        ('sentinel', '哨兵/ASH采样'),
        ('pipeline', '事件流水线消费者'),
        ('notifier', '通知发送器'),
    )
    STATUS_CHOICES = (('up', '正常'), ('down', '失联'))

    component = models.CharField(max_length=32, choices=COMPONENT_CHOICES,
                                 verbose_name="组件")
    instance = models.CharField(max_length=128,
                                verbose_name="实例标识",
                                help_text="hostname:pid，区分多副本部署")
    last_beat_at = models.DateTimeField(db_index=True, verbose_name="最后心跳时间")
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default='up',
                              verbose_name="状态")
    meta = models.JSONField(default=dict, blank=True, verbose_name="附加信息",
                            help_text="如采集实例数、队列积压等，仅供展示")
    create_time = models.DateTimeField(auto_now_add=True, verbose_name="首次上报")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "组件心跳"
        verbose_name_plural = "组件心跳列表"
        unique_together = [('component', 'instance')]
        indexes = [models.Index(fields=['status', 'last_beat_at'])]

    def __str__(self):
        return f"{self.get_component_display()}@{self.instance} ({self.status})"
```

**表结构**

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| id | BIGSERIAL | PK | |
| component | VARCHAR(32) | NOT NULL, 联合唯一 | 组件编码 |
| instance | VARCHAR(128) | NOT NULL, 联合唯一 | hostname:pid |
| last_beat_at | TIMESTAMPTZ | NOT NULL, 索引 | 失联判定依据 |
| status | VARCHAR(8) | NOT NULL, 默认 'up' | up/down |
| meta | JSONB | 默认 {} | 展示用附加信息 |
| create_time | TIMESTAMPTZ | auto_now_add | |
| update_time | TIMESTAMPTZ | auto_now | |

**范式说明**：单实体、无传递依赖，满足 3NF。
`meta` 为 JSONB 但**不参与查询与判定**（仅前端展示），
不构成 4NF 违例 —— 与本项目此前 4NF 改造中拆掉的那些"多值 JSON 列"性质不同：
那些列的元素需要被独立查询/过滤，这里不需要。

**迁移** `0022_componentheartbeat.py`：纯新增表，无数据迁移，无回滚风险。

### 3.3 需要的种子数据

自监控告警需要一个承载实例（`AlertLog.config` 为必填）：

```python
# monitor/management/commands/init_self_monitor.py
DatabaseConfig.objects.get_or_create(
    name='__system__',
    defaults={'db_type': 'mysql', 'host': 'localhost', 'port': 0,
              'username': '-', 'password': '', 'is_active': False},
)
```

> 设计权衡：这是个"伪实例"，不优雅。
> 更干净的做法是给 `AlertLog.config` 也加 `null=True`（本轮已给 `AuditLog.config` 这样做了）。
> **建议采纳后者**，把伪实例方案作为不想改模型时的退路。
> 若采纳，追加迁移 `0023_alertlog_config_nullable.py`，
> 并复核 `alert_manager.py` 中所有 `self.config.xxx` 的空值分支。

---

## 四、接口设计

### 4.1 新增业务 API（W4 自监控）

均挂在 `/api/v1/system/` 下，权限 `Perm.DASHBOARD_VIEW`（能看仪表盘就能看系统健康）。

#### 4.1.1 `GET /api/v1/system/health`

系统整体健康，供顶栏指示灯与外部探活使用。

**请求**：无参数
**响应 200**
```json
{
  "status": "degraded",
  "checked_at": "2026-08-09T07:30:00Z",
  "components": [
    {"component": "collector", "display": "指标采集器", "status": "up",
     "instances": 1, "last_beat_at": "2026-08-09T07:29:40Z", "silent_sec": 20},
    {"component": "sentinel", "display": "哨兵/ASH采样", "status": "down",
     "instances": 1, "last_beat_at": "2026-08-09T07:20:00Z", "silent_sec": 600}
  ],
  "dependencies": {
    "database":    {"status": "ok"},
    "timescaledb": {"status": "ok"},
    "redis":       {"status": "ok"},
    "elasticsearch": {"status": "disabled"}
  },
  "degradations": {"ash.oracle_objname": 12, "notify.dingtalk": 3}
}
```

| 字段 | 说明 |
|------|------|
| `status` | `ok`（全部正常）/ `degraded`（有组件失联或有降级计数）/ `down`（数据库不可用） |
| `components[].silent_sec` | 距上次心跳秒数，前端据此显示"20 秒前" |
| `dependencies` | 复用既有 `monitor/healthcheck.py` 的探测结果 |
| `degradations` | 来自 `degrade.snapshot()`，暴露"正在静默降级的功能" |

**状态码**：`200` 正常返回（即使 status=down，便于监控系统抓取）；`401` 未认证；`403` 无权限

> 设计说明：**故意不用 5xx 表达不健康**。探活接口返回 5xx 会让上游 LB
> 把这个节点摘掉，而此时恰恰需要它继续提供诊断信息。健康状态放在 body 里。

#### 4.1.2 `GET /api/v1/system/components`

组件明细，供运维页表格展示。

**请求参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status` | string | 否 | `up`/`down` 过滤 |

**响应 200**
```json
{
  "total": 4,
  "items": [
    {"component": "sentinel", "display": "哨兵/ASH采样",
     "instance": "dbaiops-worker-1:12345", "status": "down",
     "last_beat_at": "2026-08-09T07:20:00Z", "silent_sec": 600,
     "stale_after_sec": 180, "meta": {"instances": 5}}
  ]
}
```

#### 4.1.3 `GET /api/v1/system/degradations`

当前累计降级计数（W6）。

**响应 200**
```json
{
  "since_process_start": true,
  "items": [
    {"scope": "ash.oracle_objname", "count": 12},
    {"scope": "notify.dingtalk", "count": 3}
  ]
}
```

> 注：计数是**进程内**的，多副本部署时各副本独立。
> 需要全局视图时再考虑落库，本期不做（避免为了统计而引入写放大）。

### 4.2 前端接入

`frontend/src/services/api.js` 追加：

```js
export const systemAPI = {
  health: () => api.get('/system/health'),
  components: (params = {}) => api.get('/system/components', { params }),
  degradations: () => api.get('/system/degradations'),
}
```

`EMLayout.jsx` 顶栏加健康指示灯（复用既有 60s 轮询定时器，不新增定时器）：
`ok` 绿点 / `degraded` 黄点+失联组件数 / `down` 红点，点击跳 `/system/health` 页。

### 4.3 工具链命令契约

#### 4.3.1 `scripts/validate.sh`

| 项 | 契约 |
|----|------|
| 调用 | `scripts/validate.sh [unit\|backend\|frontend\|all] [--json]`，缺省 `all` |
| 退出码 | `0` 通过；`1` 任一检查失败；`2` 用法错误 |
| `--json` | stdout 仅输出单行 JSON（见 §2.2.2），各阶段原始输出被抑制 |
| 幂等 | 只读 + 自动创建/销毁测试库，可重复执行 |
| 耗时 | unit ~10s；backend ~60s；frontend ~30s（含 npm ci 首次 ~90s） |

backend 检查序列（顺序固定，前序失败即终止）：

| 序 | 阶段 | 命令 | 捕获缺陷类别 |
|----|------|------|-------------|
| 1 | 语法编译 | `compileall -q monitor dbmonitor` | 语法错误 |
| 2 | 系统检查 | `manage.py check` | 配置/模型/URL 错误 + appconf 校验（W5） |
| 3 | 迁移漂移 | `makemigrations --check --dry-run` | 改了模型没生成迁移 |
| 4 | 依赖完整性 | `scripts/check_deps.py` | 声明了但装不上（防 BUG-137） |
| 5 | 全量测试 | `manage.py test monitor` | 业务回归（当前 150 用例） |

#### 4.3.2 CI 契约

| Job | 触发 | 阻断合并 | 依赖服务 |
|-----|------|---------|---------|
| `静态检查` | PR + push | ✅ | 无 |
| `单元测试（无外部依赖）` | PR + push | ✅ | 无 |
| `集成测试（PostgreSQL）` | PR + push | ✅ | postgres:16 |
| `方言测试（MySQL/PG）` | push 到 master，或 PR 打 `test-dialect` 标签 | ❌（信息性） | mysql:8.0 + postgres×2 |
| `前端构建与测试` | PR + push | ✅ | 无 |
| `安全扫描` | PR + push | ❌（首期观察） | 无 |

#### 4.3.3 pre-commit 契约

| 项 | 契约 |
|----|------|
| 触发 | `git commit` |
| 检查 | staged `.py` 的 `compileall` + `manage.py check` + 全仓密钥扫描 |
| 退出码 | `0` 放行；`1` 拦截 |
| 跳过 | `git commit --no-verify` |
| 定位 | **本地快速反馈，非门禁**。门禁在 CI + 分支保护 |

---

## 五、验收标准

### 5.1 验收清单

| # | 用例 | 操作 | 预期 |
|---|------|------|------|
| V1 | CI 在 PR 上运行 | 开一个改 1 行的 PR | 4 个必需 job 全部出现并通过 |
| V2 | CI 能拦住坏代码 | PR 中引入语法错误 | `静态检查` 失败，合并按钮被禁用 |
| V3 | 迁移漂移被拦 | 改模型但不生成 migration | `静态检查` 的迁移漂移步骤失败 |
| V4 | 分支保护生效 | 尝试直推 master | 被拒绝 |
| V5 | unit 层零依赖 | 停掉所有 Docker，`scripts/validate.sh unit` | 退出码 0，< 15s |
| V6 | JSON 契约 | `scripts/validate.sh unit --json \| python -m json.tool` | 合法 JSON，含 exit_code/stages |
| V7 | 依赖缺失被发现 | `pip uninstall jsonschema` 后跑 `check_deps.py` | 退出码 1，明确指出 jsonschema |
| V8 | 密钥扫描 | 临时加 `password = "Real1Passw0rd!"` | `scan_secrets.py` 退出码 1 并定位行号 |
| V9 | 方言测试真跑 | 起 MySQL 容器，跑 `tests_dialect` | MySQL 用例通过，未配 DSN 的 skip |
| V10 | 心跳失联告警 | 停掉哨兵进程，等超过 180s | `/api/v1/system/health` 显示 sentinel down，产生 `component_down` 告警 |
| V11 | 心跳恢复 | 重启哨兵 | status 回 up，告警自动 resolve |
| V12 | 配置校验 | 设 `ASH_INTERVAL_SEC=0` | `manage.py check` 报 `monitor.E001` |
| V13 | 配置运行期生效 | `override_settings(LOGIN_MAX_ATTEMPTS=2)` | 2 次失败即锁定（防 BUG-138 回归） |
| V14 | 降级留痕 | 触发一次 ASH 补查失败 | `/api/v1/system/degradations` 出现对应 scope |
| V15 | 全量回归 | `python manage.py test monitor` | 150+ 用例全通过 |

### 5.2 完成定义（DoD）

**P1 完成**：V1–V4、V6 通过；`master` 分支保护已开启且含 4 项必需检查。
**P2 完成**：V5、V7、V12、V13 通过；unit 层用例数 ≥ 60。
**P3 完成**：V9、V10、V11 通过；`checkers/` 关键路径（连接、超时、只读、ASH 形状）有真库覆盖。
**P4 完成**：V8、V14 通过；B1–B3 批次降级留痕改造完成。

### 5.3 量化目标

| 指标 | 现状 | P1 后 | P4 后 |
|------|------|-------|-------|
| 合并前自动检查项 | 0 | 4 | 6 |
| 无外部依赖可跑的测试 | 0 | 0 | ≥ 60 |
| `checkers/` 测试覆盖 | 0 行 | 0 | 关键路径覆盖 |
| 系统自身组件可观测性 | 无 | 无 | 4 类组件心跳 |
| 完全静默的异常吞没 | 169 处 | 169 | B1–B3 范围内归零 |

---

## 六、风险与回滚

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| CI 拖慢合并节奏 | 中 | 中 | static 前置快速失败；dialect 不阻断 PR；`cancel-in-progress` 省额度 |
| Actions 额度不足（私有仓库） | 中 | 中 | 免费额度 2000 分钟/月；本方案单次 PR 约 6–8 分钟，约 250 次/月够用。超限则把 integration 降为仅 master 跑 |
| SQLite 与 PostgreSQL 行为差异导致 unit 层假绿 | **中高** | **高** | 已在 W3.2 明确标注；并发/约束类用例强制标 `integration`；**评审时必须逐条确认打标正确性** |
| `pip-audit` 报出大量存量 CVE | 高 | 低 | 首期 `\|\| true` 不阻断，先建基线 |
| 心跳表写入频繁 | 低 | 低 | upsert 单行，最快 30s 一次 × 组件数，量级可忽略 |
| `__system__` 伪实例污染实例列表 | 中 | 低 | `is_active=False` + 列表接口过滤；**建议改用 AlertLog.config 可空**（§3.3） |
| 密钥扫描误报打断提交 | 中 | 低 | 提供 `# noqa: secret` 豁免；占位符正则已排除常见示例值 |

**回滚方案**

| 工作流 | 回滚 |
|--------|------|
| W1 | 删 `.github/workflows/ci.yml` + 关闭分支保护 |
| W2/W7 | 删 `scripts/*.sh`、`AGENTS.md`、`rm .git/hooks/pre-commit` |
| W3 | 删 `settings_test_unit.py`、`tests_dialect.py`、移除 `@tag` |
| W4 | 迁移 `0022` 可逆（纯新增表）；移除接入点的 `report()` 调用 |
| W5 | `appconf.get()` 退回 `getattr(settings, ...)`；删 `checks.py` 注册 |
| W6 | `degrade.note()` 是纯附加调用，删除即恢复 |

---

## 七、实施顺序总清单

| 序 | 工作流 | 交付物 | 验收 |
|----|--------|--------|------|
| 1 | W1 | `.github/workflows/ci.yml`、`scripts/check_deps.py`、`scripts/scan_secrets.py` | V1、V2、V3 |
| 2 | W1 | GitHub 分支保护配置（手工） | V4 |
| 3 | W2 | `scripts/validate.sh`、`scripts/pre-commit`、`scripts/install-hooks.sh` | V6 |
| 4 | W7 | `AGENTS.md`、`.qoder/rules/validation.md`、Memory 治理 | 文件存在；同名 Memory ≤ 1 |
| 5 | W5 | `monitor/appconf.py`、`monitor/checks.py`、存量替换 | V12、V13 |
| 6 | W3 | `settings_test_unit.py`、现有测试打标 | V5 |
| 7 | W3 | `monitor/tests_dialect.py` + CI dialect job | V9 |
| 8 | W4 | `ComponentHeartbeat` 模型 + 迁移 + `self_monitor.py` + 接入点 | V10、V11 |
| 9 | W4 | 3 个系统 API + 前端指示灯 | 接口可访问、指示灯变色 |
| 10 | W6 | `degrade.py` + B1–B3 批次改造 | V8、V14 |
| 11 | — | 全量回归 | V15 |

---

## 附录 A：与 Agent H 方案的逐条对照

| H 方案条目 | 本方案处理 | 说明 |
|-----------|-----------|------|
| §2.1 AGENTS.md | **采纳并扩充**（W7.1） | 修正测试用例数（55→150）；补"已知陷阱"章节；补 unit/backend 分层命令 |
| §2.2 validate.sh | **重写**（W2.1） | 改 bash；测试改自动发现；加迁移漂移与依赖检查；加 unit 模式与 `--json` |
| §2.3 Qoder Rule | **采纳** | 内容微调以对齐新的分层命令 |
| §2.4 pre-commit | **采纳但降级定位**（W2.3） | 从"门禁"降为"本地快速反馈"，门禁移至 CI；补密钥扫描 |
| §2.5 Memory 治理 | **完全采纳** | 无异议 |
| §3.1 无 DB 变更 | **部分采纳** | 工具链部分确实无变更；但 W4 自监控需要 1 张表 |
| §3.2 validation_record 表 | **不采纳** | 反对把工具链遥测写进业务库（S.3）；改用 JSONL + CI artifact |
| §4 接口设计 | **扩充** | H 方案只有命令契约；本方案补 3 个自监控 REST API |
| §5 验收清单 | **采纳并扩充** | 8 项 → 15 项，覆盖 CI/自监控/配置校验 |
| §6 风险回滚 | **采纳并扩充** | 补 CI 额度、SQLite 行为差异等风险 |
| 「不建 CI，本地闭环优先」 | **反对** | 本地钩子对云端 Agent 无效、可 `--no-verify` 跳过、不随仓库分发。CI 是 P1 而非远期 |
