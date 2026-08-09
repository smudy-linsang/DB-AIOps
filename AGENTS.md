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

## 提交规范
- 提交信息格式：`feat(scope): 中文描述` / `fix(scope): ...` / `docs(scope): ...`
- 完成改动后自行 commit 并 push 到 origin master

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
  放进 unit 层会得到假绿（参见 PROJECT_IMPROVEMENT_DESIGN.md W3.2 的限制说明）
- 涉及"唯一性"的业务不变量，光靠应用层加锁不够，要有数据库约束兜底（BUG-119）
