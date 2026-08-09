# Harness 提升改进方案（工程验证闭环与知识治理）设计文档

| 项目 | 内容 |
|------|------|
| 背景 | Better Harness 分析（2026-08-08）发现 2 项问题：改动验证维度仅 30 分（93 次编辑、0 次验证）；Memory 标题碰撞 1 组 |
| 目标 | 建立"编辑 → 验证 → 提交"机械化闭环；消除 Memory 标题碰撞；为 Agent 协作提供可导航的项目上下文 |
| 范围 | 工程工具链层：AGENTS.md、验证脚本、提交门禁、Qoder Rule、Memory 治理。**不涉及业务代码逻辑与业务数据库 Schema** |
| 基线 | 现有验证能力：Django 测试套件（`monitor/tests.py` 13 个 + `tests_phase8.py` 42 个用例）、`manage.py check`、`npm run build`；无任何 lint 配置、无项目级 Rules/Hooks |
| 证据来源 | `.qoder/better-harness/2026-08-08/234411-db_monitor/findings.json` |

---

## 一、概要设计

### 1.1 现状问题分析

| # | 问题 | 严重级 | 证据 | 影响 |
|---|------|--------|------|------|
| F1 | 代码变更后无验证步骤，正确性依赖人工判断 | Medium | 4 个候选 Episode 全部以 `changed-without-check` 关闭；93 次编辑事件、0 次验证命令 | 语法错误、回归缺陷只能在运行时或人工使用时暴露，修复成本高 |
| F2 | 2 条 Memory 标题完全相同（"DB-AIOps项目技术栈"） | Low | 资产完整性审查：1 个 exactCollisionGroups | 检索歧义，Agent 可能命中错误条目 |

五维评分现状（Better Harness agent-work-loop-v4）：

| 维度 | 得分 | 本方案覆盖 |
|------|------|-----------|
| 任务理解 | 55 | AGENTS.md 提供上下文导航 → 预期提升 |
| 可控执行 | 45 | validate.sh 提供受控验证入口 → 预期提升 |
| 改动验证 | 30 | **本方案核心目标** → 预期显著提升 |
| 可靠交付 | 35 | pre-commit 门禁 → 部分改善 |
| 经验沉淀 | 35 | Memory 碰撞治理 → 部分改善 |

### 1.2 改进目标与非目标

**目标**
1. 每次后端/前端代码编辑后，存在一条**确定性、可执行、结果可被 Agent 消费**的验证路径；
2. 验证路径只依赖项目既有工具（Django test runner、vite build），不引入新依赖（P1 阶段）；
3. 提交门禁在错误代码进入 Git 历史之前拦截（语法错误、系统检查失败）；
4. Agent 打开项目即可从 AGENTS.md 获得结构、命令、验证要求、红线约束；
5. Memory 标题碰撞归零。

**非目标**
- 不引入 flake8/pylint/eslint 等新依赖（列为 P2 可选增强，需另行批准）；
- 不改造业务代码、不新增业务 API、不修改业务数据库 Schema；
- 不建设 CI/CD 流水线（本地工程闭环优先，CI 为远期方向）。

### 1.3 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  指令层（Agent 导航）                                         │
│  AGENTS.md：项目结构 / 启动命令 / 验证要求 / 红线约束          │
│  .qoder/rules/validation.md：编辑后强制验证规则（可选）        │
└───────────────┬─────────────────────────────────────────────┘
                │ 声明"改了什么必须验什么"
┌───────────────▼─────────────────────────────────────────────┐
│  验证层（机械反馈）                                           │
│  scripts/validate.sh [backend|frontend|all]                 │
│   backend:  compileall → manage.py check → manage.py test   │
│   frontend: npm run build                                   │
└───────────────┬─────────────────────────────────────────────┘
                │ 验证通过后允许提交
┌───────────────▼─────────────────────────────────────────────┐
│  门禁层（提交拦截）                                           │
│  .git/hooks/pre-commit：staged .py 语法检查 + manage.py check│
│  scripts/install-hooks.sh：一键安装                          │
└───────────────┬─────────────────────────────────────────────┘
                │ 经验沉淀
┌───────────────▼─────────────────────────────────────────────┐
│  治理层（知识资产）                                           │
│  Memory 碰撞治理：检索 → 比对正文 → 合并/重命名               │
└─────────────────────────────────────────────────────────────┘
```

### 1.4 分阶段计划

| 阶段 | 内容 | 交付物 | 依赖 |
|------|------|--------|------|
| P1（本方案实施范围） | AGENTS.md + validate.sh + pre-commit 门禁 + Memory 治理 | 4 项文件 + 1 次 Memory 操作 | 无新依赖 |
| P2（可选增强，另行批准） | 引入 flake8 + 前端 eslint，纳入 validate.sh | requirements.txt 变更 | 需安装依赖 |
| P3（远期） | CI 流水线、验证记录持久化（见第三章可选设计） | — | P1 运行稳定后评估 |

---

## 二、详细设计说明书（照图施工）

### 2.1 AGENTS.md（新增，仓库根目录 `db-aiops/AGENTS.md`）

完整内容如下，直接落盘：

````markdown
# DB-AIOps 项目协作指南

## 项目概述
Django 后端 + React 前端的数据库智能运维平台（AIOps）：纳管 MySQL/PostgreSQL/Oracle/GBase/TDSQL
等多类数据库，提供监控采集、告警引擎、事故诊断、自治修复、巡检报告能力。

## 目录结构
- `dbmonitor/`：Django 工程配置（settings/urls）
- `monitor/`：核心业务应用（模型、API、检查器、检测器、管理命令）
- `monitor/management/commands/`：运维管理命令（start_monitor 等）
- `frontend/`：React + Vite 前端（src/pages 页面、src/services/api.js 接口封装）
- `scripts/`：开发与验证脚本（dev-start.sh、validate.sh 等）
- `docker-compose.dev.yml`：开发基础设施（PostgreSQL/Redis/ES/TimescaleDB）

## 启动
1. `docker-compose -f docker-compose.dev.yml up -d`（基础设施）
2. `source venv/bin/activate && python manage.py runserver 0.0.0.0:8000`
3. `cd frontend && npm run dev`（前端 3000 端口）
或一键：`scripts/dev-start.sh`

## 编辑后验证要求（强制）
- 改动 `dbmonitor/`、`monitor/` 下 Python 文件 → `scripts/validate.sh backend`
- 改动 `frontend/` 下文件 → `scripts/validate.sh frontend`
- 两侧都改 → `scripts/validate.sh all`
- 验证失败必须先修复再提交；禁止跳过验证直接 commit。

## 提交规范
- 提交信息格式：`feat(scope): 中文描述` / `fix(scope): 中文描述` / `docs(scope): ...`
- 完成改动后自行 commit 并 push 到 origin master。

## 红线约束
- 禁止提交 `.env`、密钥、密码等敏感信息（DB_MONITOR_SECRET_KEY 等）。
- 不得删除或覆盖 migrations 历史文件。
- 涉及数据库 Schema 变更必须先出设计文档并生成 migration。
````

### 2.2 验证脚本 `scripts/validate.sh`（新增）

完整内容如下，直接落盘后 `chmod +x scripts/validate.sh`：

```bash
#!/bin/zsh
# DB-AIOps 改动验证统一入口
# 用法: scripts/validate.sh [backend|frontend|all]  (缺省 all)
# 退出码: 0=全部通过; 1=任一检查失败; 2=用法/环境错误
set -u
MODE=${1:-all}
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR" || exit 2

# 激活虚拟环境（存在时）
[ -f venv/bin/activate ] && source venv/bin/activate

FAIL=0

run_backend() {
  echo "=== [backend 1/3] Python 语法编译检查 ==="
  python -m compileall -q monitor dbmonitor || { echo "FAIL: 语法错误"; return 1; }
  echo "=== [backend 2/3] Django 系统检查 ==="
  python manage.py check || { echo "FAIL: manage.py check 未通过"; return 1; }
  echo "=== [backend 3/3] 单元测试 ==="
  python manage.py test monitor.tests monitor.tests_phase8 -v 1 \
    || { echo "FAIL: 单元测试未通过"; return 1; }
  echo "backend: PASS"
  return 0
}

run_frontend() {
  echo "=== [frontend] vite build ==="
  ( cd frontend && [ -d node_modules ] || npm install; npm run build ) \
    || { echo "FAIL: 前端构建失败"; return 1; }
  echo "frontend: PASS"
  return 0
}

case "$MODE" in
  backend)  run_backend  || FAIL=1 ;;
  frontend) run_frontend || FAIL=1 ;;
  all)      run_backend  || FAIL=1
            run_frontend || FAIL=1 ;;
  *) echo "用法: $0 [backend|frontend|all]"; exit 2 ;;
esac

if [ $FAIL -eq 0 ]; then echo "===> 验证全部通过"; else echo "===> 验证失败，禁止提交"; fi
exit $FAIL
```

**前置条件**：backend 模式的单元测试需要 PostgreSQL 容器运行（测试框架创建测试库）；
容器未启动时报数据库连接错误属环境问题，不是代码问题，先执行
`docker-compose -f docker-compose.dev.yml up -d` 再重试。

### 2.3 Qoder Rule（新增，`.qoder/rules/validation.md`，可选但建议）

```markdown
---
trigger: always_on
---

# 编辑后验证规则

在本项目（db-aiops）中修改代码后，提交前必须执行对应验证：

- 修改 Python 文件（dbmonitor/、monitor/）：运行 `scripts/validate.sh backend`
- 修改前端文件（frontend/）：运行 `scripts/validate.sh frontend`
- 两者都修改：运行 `scripts/validate.sh all`

验证未通过（退出码非 0）时，先修复失败项并重新验证，禁止直接提交。
提交信息沿用 `feat(scope): 中文描述` 风格，完成后自行 commit 并 push。
提交前确认不含 .env、密钥等敏感信息。
```

### 2.4 提交门禁

#### 2.4.1 钩子内容 `scripts/pre-commit`（新增，模板文件）

只做**不依赖数据库**的快速检查（语法编译 + 系统检查），保证秒级反馈：

```bash
#!/bin/zsh
# pre-commit: 拦截语法错误与 Django 系统检查失败
# 仅当存在 staged 的 .py 文件时执行
set -u
PROJECT_DIR="$(git rev-parse --show-toplevel)"
cd "$PROJECT_DIR"
[ -f venv/bin/activate ] && source venv/bin/activate

STAGED_PY=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)
[ -z "$STAGED_PY" ] && exit 0

echo "[pre-commit] 检查 $(echo "$STAGED_PY" | wc -l | tr -d ' ') 个 staged .py 文件"
python -m compileall -q $(echo "$STAGED_PY") || { echo "[pre-commit] FAIL: 语法错误"; exit 1; }
python manage.py check >/dev/null 2>&1 || { echo "[pre-commit] FAIL: manage.py check"; exit 1; }
echo "[pre-commit] PASS"
exit 0
```

#### 2.4.2 安装脚本 `scripts/install-hooks.sh`（新增）

```bash
#!/bin/zsh
# 安装 git 钩子到本仓库 .git/hooks/
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cp "$PROJECT_DIR/scripts/pre-commit" "$PROJECT_DIR/.git/hooks/pre-commit"
chmod +x "$PROJECT_DIR/.git/hooks/pre-commit"
echo "pre-commit 钩子已安装: $PROJECT_DIR/.git/hooks/pre-commit"
```

### 2.5 Memory 标题碰撞治理（操作步骤）

碰撞对：
- **Project 作用域**："DB-AIOps项目技术栈"，memoryId `07c0b64f-1062-4d07-9a15-208c8497677e`
  （正文：Django 后端 / React+Vite 前端 / ES 告警索引 / TimescaleDB+PostgreSQL 时序存储）
- **Global 作用域**：同名 "DB-AIOps项目技术栈" 1 条（执行时通过 SearchMemory 全量检索定位）

操作步骤（照此执行）：

1. 用 `SearchMemory`（search 模式，关键词：技术栈、Django、React）召回全部同名条目，
   记录各自 memoryId、scope、正文；
2. 比对两条正文：
   - **内容实质重复** → 保留 Project 作用域条目（与本项目强绑定），用 `UpdateMemory(delete)`
     删除 Global 冗余条目；
   - **内容不同**（如 Global 条目描述其他项目或更宽泛主题）→ 用 `UpdateMemory(update)`
     为其中一条改为更精确标题（例如 "DB-AIOps项目技术栈（全局概览）" 或按其真实主题命名）；
3. 复核：再次 `SearchMemory` 确认同名条目 ≤ 1。

### 2.6 实施步骤总清单（按序执行）

| 步骤 | 操作 | 验收 |
|------|------|------|
| 1 | 创建 `db-aiops/AGENTS.md`（内容见 2.1） | 文件存在，内容完整 |
| 2 | 创建 `scripts/validate.sh`（2.2）并 `chmod +x` | `scripts/validate.sh backend` 退出码 0 |
| 3 | 创建 `.qoder/rules/validation.md`（2.3） | 文件存在 |
| 4 | 创建 `scripts/pre-commit`（2.4.1）与 `scripts/install-hooks.sh`（2.4.2）并 chmod +x | 文件存在 |
| 5 | 执行 `scripts/install-hooks.sh` | `.git/hooks/pre-commit` 可执行 |
| 6 | 执行 Memory 治理（2.5） | 同名 Memory ≤ 1 |
| 7 | 按第五章验收清单完成冒烟 | 全部 PASS |
| 8 | `git add` 新增文件 → commit → push | GitHub 可见提交 |

---

## 三、数据库设计

### 3.1 变更声明

**本方案 P1 不产生任何业务数据库变更**：

- 不新增 Django Model，不生成 migration；
- 不涉及自有库 4NF 结构（此前已完成 4NF 规范化改造，本方案不回退）；
- 不涉及纳管目标库（MySQL/PG/Oracle/GBase/TDSQL）的任何 Schema；
- 验证结果以 stdout 与 Git 钩子拦截形式消费，**无持久化需求**，因此不引入新表。

### 3.2 可选扩展（P3 远期，本期不实施）

若未来需要验证记录审计（例如统计验证通过率、失败类别分布），可新增单表：

表名 `validation_record`（monitor 应用，PostgreSQL 自有库）：

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGSERIAL | PK | 自增主键 |
| run_at | TIMESTAMPTZ | NOT NULL, 索引 | 验证执行时间 |
| mode | VARCHAR(16) | NOT NULL | backend / frontend / all |
| exit_code | SMALLINT | NOT NULL | 0=通过 |
| failed_stage | VARCHAR(32) | NULL | compile/check/test/build |
| duration_ms | INTEGER | NOT NULL | 耗时 |
| triggered_by | VARCHAR(16) | NOT NULL | manual / pre-commit / agent |
| git_rev | VARCHAR(40) | NULL | 执行时 HEAD |

该表满足 3NF（无传递依赖，单实体）。**触发条件**：P1 闭环稳定运行 ≥ 2 周且出现审计需求时另行立项。

---

## 四、接口设计

### 4.1 变更声明

**不新增、不修改任何业务 REST API**（`/api/v1/*` 全部不变）。本方案的"接口"是工程工具链的命令契约。

### 4.2 `scripts/validate.sh` 命令契约

| 项 | 契约 |
|----|------|
| 调用形式 | `scripts/validate.sh [backend\|frontend\|all]`，参数缺省 `all` |
| 工作目录 | 任意（脚本内部定位仓库根） |
| 前置条件 | backend 需 venv 与 PostgreSQL 容器；frontend 需 node/npm |
| 退出码 | `0` 全部通过；`1` 任一检查失败；`2` 用法或环境错误 |
| stdout 格式 | 每个阶段 `=== [阶段名] ===` 头 + 结尾 `backend: PASS/FAIL`、`===> 验证全部通过/验证失败，禁止提交` |
| 幂等性 | 只读 + 测试（测试库自动创建/销毁），可重复执行 |
| 超时预期 | backend 约 1–3 分钟（55 个用例）；frontend 首次含 npm install 约 1–2 分钟 |

backend 检查序列（顺序固定，前序失败即终止）：

| 序 | 检查 | 命令 | 捕获的缺陷类别 |
|----|------|------|----------------|
| 1 | 语法编译 | `python -m compileall -q monitor dbmonitor` | 语法错误 |
| 2 | 系统检查 | `python manage.py check` | 配置错误、模型定义错误、URL 冲突 |
| 3 | 单元测试 | `python manage.py test monitor.tests monitor.tests_phase8 -v 1` | 业务逻辑回归（加密、告警、升级聚合等 55 用例） |

### 4.3 pre-commit 钩子契约

| 项 | 契约 |
|----|------|
| 触发时机 | `git commit` 且暂存区含新增/修改的 `.py` 文件 |
| 检查内容 | staged .py 文件 `compileall` + `manage.py check` |
| 退出码 | `0` 放行；`1` 拦截提交 |
| 跳过方式 | `git commit --no-verify`（仅限紧急情况，须事后补验） |
| 设计取舍 | 不跑单测（避免依赖数据库、保持秒级反馈）；单测由 validate.sh 与自觉流程保证 |

### 4.4 Memory 治理操作契约

| 工具 | 用途 |
|------|------|
| SearchMemory | 召回同名条目（含 Global 作用域），获取 memoryId 与正文 |
| UpdateMemory(update) | 重命名保留条目 |
| UpdateMemory(delete) | 删除冗余条目 |

---

## 五、验收标准与验证方案

### 5.1 冒烟验收清单（实施完成后逐项执行）

| # | 用例 | 操作 | 预期 |
|---|------|------|------|
| V1 | 基线验证通过 | `scripts/validate.sh backend` | 退出码 0，输出 `backend: PASS` |
| V2 | 语法错误拦截（validate） | 在任一 .py 尾部临时加 `def broken(:`，跑 `scripts/validate.sh backend` | 退出码 1，`FAIL: 语法错误` |
| V3 | 语法错误拦截（pre-commit） | 保留 V2 破坏，`git add` 后 `git commit` | 提交被钩子拒绝 |
| V4 | 还原后放行 | 还原破坏，重新 commit | 提交成功 |
| V5 | 前端构建验证 | `scripts/validate.sh frontend` | 退出码 0，dist 产物生成 |
| V6 | 用法错误 | `scripts/validate.sh xyz` | 退出码 2，打印用法 |
| V7 | Memory 治理 | SearchMemory 检索 "DB-AIOps项目技术栈" | 同名条目 ≤ 1 |
| V8 | Agent 导航 | 新开对话询问"如何验证改动" | Agent 依据 AGENTS.md/Rule 给出 validate.sh 路径 |

### 5.2 完成定义（DoD）

- V1–V7 全部 PASS；
- 4 个新文件 + 1 个钩子安装完成并推送至 GitHub；
- Better Harness 下次分析中 `changed-without-check` 闭环比例改善（后续窗口验证）。

---

## 六、风险与回滚

| 风险 | 概率 | 缓解 |
|------|------|------|
| 单测依赖 PostgreSQL 容器，冷环境误报失败 | 中 | validate.sh 报错信息明确；AGENTS.md 写明前置条件 |
| pre-commit 影响提交速度 | 低 | 只做 compileall + check（秒级）；可用 --no-verify 应急 |
| 测试用例本身不稳定（依赖外部 ES/Redis） | 低 | 现有 55 用例在无外部服务时已可通过（用 mock/降级路径）；若个别用例环境敏感，另行修复用例而非绕过验证 |
| Memory 误删 | 低 | 治理前先比对正文；仅删除被证实冗余的条目 |

**回滚方案**：删除 `AGENTS.md`、`.qoder/rules/validation.md`、三个 scripts 文件，
执行 `rm .git/hooks/pre-commit`，即完全恢复原状；Memory 操作如需回滚，
按 SearchMemory 历史记录重建条目。
