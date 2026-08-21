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

## 代码编辑后验证（强制）

**每次完成代码编辑（Write、SearchReplace 等文件修改操作）后，立即运行：**

    cd db-aiops && scripts/validate.sh unit --json

退出码 0 → 可继续下一步；非 0 → 先修复失败项并重新验证，禁止提交。

| 场景 | 命令 | 耗时 |
|------|------|------|
| 每次代码编辑后（强制） | `scripts/validate.sh unit --json` | ~10s，零外部依赖 |
| 提交前（后端） | `scripts/validate.sh backend` | ~1min，需 PostgreSQL |
| 提交前（前端） | `scripts/validate.sh frontend` | ~30s |
| 两侧都改 | `scripts/validate.sh all` | ~2min |

读取 JSON 输出的 `exit_code` 与首个 `status=="fail"` 的 `name` 定位失败项。

> pre-commit 钩子（`scripts/pre-commit`）已接入 `validate.sh unit`，仅作辅助安全网。
> 主要依赖编辑后验证，不替代它。安装或更新钩子：`bash scripts/install-hooks.sh`

**CI 是事后信号，不是合并门禁 —— 所以本地验证是你唯一的闸。**

本仓库的工作流是**直推 master**，仓库没有 ruleset、没有必需状态检查
（「允许直推」与「必需检查」在 GitHub 上互斥；且没有 PR 可拦，配了也是空转，
理由见 `PROJECT_IMPROVEMENT_DESIGN.md` 附录 B.6.2）。这意味着：

- **没有任何机制会在你推送前拦住你。** 推坏了就是 master 坏了。
- `.github/workflows/ci.yml` 在 push 之后才跑（静态检查、单元、集成、
  方言 MySQL/PG、方言 Oracle、安全扫描、前端，共 7 项）。它能告诉你推坏了，
  但拦不住你推。
- 因此**推之前请自己跑 `scripts/validate.sh`**；改到采集/方言相关代码时，
  尽量连方言测试一起跑（见 `PROJECT_IMPROVEMENT_DESIGN.md` W3 的本地运行说明）。
- **推完请回头看一眼 CI。** master 挂红就是真红 —— Oracle job 已摘掉
  `continue-on-error`，且有"确认用例真的跑了"守住绿灯含义，不存在
  "红了也没关系"的 job。看到红请立刻修或回滚，别留给下一个人。

## 改动数据库模型时
1. 先出设计文档（参考 `BUGFIX_DESIGN.md` 的粒度）
2. `python manage.py makemigrations`
3. **迁移必须处理存量脏数据**：加约束前先清理违约行（参考 `0019`/`0021` 的写法）
4. `scripts/validate.sh backend` 会用 `makemigrations --check` 拦住"改了模型没生成迁移"

## 提交规范
- 提交信息格式：`feat(scope): 中文描述` / `fix(scope): ...` / `docs(scope): ...`
- 完成改动后自行 commit 并 push 到 origin master

## 红线约束
- 禁止提交 `.env`、密钥、密码（`scripts/scan_secrets.py` 会在 pre-commit 与 CI 拦截）
- **数据库凭据传递通道约定**：数据库连接凭据（主机、端口、用户名、密码）
  只通过以下两条通道传递，**禁止在会话中明文粘贴**：
  1. `db-aiops/.env`（已被 `.gitignore` 忽略，提交受 `scripts/scan_secrets.py` 拦截）；
  2. 系统纳管配置界面（`DatabaseConfig` 模型，密码字段落库时加密存储）。
  AI Agent 在任何对话、分析工件、日志输出中均不得回显或记录真实凭据明文。
- 不得删除或覆盖已有 migration 文件
- 新增配置项必须登记到 `monitor/appconf.py` 的 SPECS（否则运行期读不到、也不会被校验）
- 捕获异常后不得完全静默：按 `monitor/degrade.py` 的分级留痕

## 已知陷阱（踩过的坑，别再踩）
- 时序库访问必须用 `get_timeseries_storage().cursor()` 上下文管理器借还连接，
  不要持有裸连接（BUG-101：单连接多线程共用会导致结果集串台）
- 目标库连接一律经 `DbConnector.get_connection(cfg, statement_timeout_ms=..., readonly=True)`，
  不要直接调驱动（BUG-109/110：漏超时会拖垮 Web 层，漏 readonly 会污染被监控库）
- 并发相关的测试必须标 `@tag('integration')`：SQLite 没有真实行锁语义，
  放进 unit 层会得到假绿（参见 PROJECT_IMPROVEMENT_DESIGN.md W3.2 的限制说明）
- 涉及"唯一性"的业务不变量，光靠应用层加锁不够，要有数据库约束兜底（BUG-119）
- **请求路径取实例一律用 `DatabaseConfig.objects.visible_to(request.user)`**，
  不要自己拼 `get_user_database_ids` 判断，更不要裸 `filter/all`。
  这类越权已复发四次（BUG-103 → REV-01 → R25-01 → RT-01），每次都是新写视图时
  忘了套范围。`scripts/lint_redlines.py` 的 `[未套数据范围]` 规则会在提交时拦截；
  确属全局查询（如名称唯一性校验）请加 `# scope-check: allow <理由>`
- 权限与数据范围是**两道独立的闸**：`require_permission` 管"能不能调这个接口"，
  `visible_to` 管"能看到哪些实例"，缺一不可。写测试时也要分开验 ——
  用零权限账号测范围会被 403 短路，等于没测到范围
