通过

# DB-029 修复交付与复验

2026-11-19。修复提交为 `3fd935127d6ba79f0925762a7493f6868c1b9350`。本轮直接验证修复版本。首轮快照评审仍见原记录。[DB-029-R1-review-O.md](DB-029-R1-review-O.md)

## 修复与实测

| 缺陷 | 修复内容 | 实测读数 |
| --- | --- | --- |
| D-01 告警中心详情打不开 | 为告警表接入页面提供的详情回调，沿用现有告警详情弹窗。新增从告警中心点击详情的组件回归。 | 修复前打开 `/alerts`，点已恢复告警的「详情」后弹窗数量为 0，浏览器报 `onView is not defined`。修复后走同一路径，弹窗可读，显示恢复依据「采集器实测」、观测值 `0`、阈值 `80`。 |
| D-02 实时推送停用无提示并反复重连 | 前端消费 `degraded` 事件，在内容区展示实际原因，顶部健康入口显示降级。收到服务端明确停用后立即结束连接。普通断线仍保留退避，退出页面时取消等待中的重连。 | `USE_REDIS_CACHE=False` 下真实 Django SSE 返回 `degraded`。修复前 3.5 秒内发送 3 次请求，页面无提示。修复后生产构建发起 1 次请求，35 秒内 0 次重连。点击「系统降级」进入系统健康，提示保持可见。单测还验证 60 秒无重连，以及暂时断线后可重连和退出时取消定时器。 |
| D-03 事故加载 DOM 嵌套 | 用块级容器承载加载状态和 Spin。 | 延迟转发真实事故详情请求，浏览器读到加载提示，容器为 `div`，随后正常进入交接与证据。 |
| D-04 纳管表单挂载 | 新增和编辑表单在调用 Form 实例前已挂载。放弃修改时清空表单，继续编辑时保留输入。 | 空表提交拦截并提示名称与主机必填，数据库写请求为 0。继续编辑会保留输入，放弃后重开已清空。打开已有配置时字段正常回填。 |
| D-05 Ant Design 弃用 | 登录卡片使用 `variant`，纳管弹窗更新挂载方式，确认弹窗使用应用上下文，巡检页签采用 `items`。 | 在 Chromium 加载 Vite 开发构建，打开登录页、纳管弹窗及确认框，切换巡检页签。与本单相关的 DOM、Form 和弃用警告为 0。 |
| D-06 表单可访问性与自动填充 | 为相关搜索、筛选和表单控件补上稳定标识、可读标签和对应的自动填充属性。区分应用登录凭据和纳管连接字段。 | 浏览器核对登录 2 个、纳管两张表单 18 个、巡检弹窗 3 个可见字段，均有标识和自动填充属性。 |
| D-07 最后采集时间英文 | 实例列表的相对采集时间使用中文，保留绝对时间提示。 | 浏览器实例行读数为「10 分钟前」。 |

上一轮自验覆盖了值班队列摘要，漏测告警中心「详情」入口。本次把该入口加进可复跑的浏览器脚本和前端测试。SSE 的真实浏览器验收覆盖 Redis 停用场景，普通网络断线和恢复由前端单测覆盖。

## 门禁和证据

- `scripts/validate.sh unit --json` 通过。前端生产构建及 100 项测试通过，新增 3 项，npm 高危审计 0 项。
- `scripts/validate.sh backend` 在本地 PostgreSQL 通过。650 项中 619 通过、31 跳过、0 失败。
- Chromium 156 对真实 Django 和本地开发 PostgreSQL 执行两轮验收。生产构建与开发构建各通过 7 项，均无未捕获页面异常和本单相关前端警告。登录、值班队列、事故交接等既有 30 个流程复跑通过，临时合成业务数据已清理。[生产构建实测 JSON](evidence/DB-029-20261119/production-build.json) · [开发构建实测 JSON](evidence/DB-029-20261119/development-build.json)
- GitHub Actions 对修复提交的 7 项检查全部通过，涵盖静态检查、前端、单元、PostgreSQL 常规及随机顺序集成、MySQL/PG 方言、Oracle 方言和安全扫描。[修复提交 CI](https://github.com/smudy-linsang/DB-AIOps/actions/runs/44455103493)

## 复跑

在已启动的隔离 Django 开发实例和本地 PostgreSQL 上运行。Django 设置 `USE_REDIS_CACHE=False`，默认访问 `127.0.0.1:8010`。脚本沿用受保护的本地验收账号文件和 system Chromium。

```bash
bash scripts/validate.sh frontend
set -a
source /workspace/dbaiops-runtime/runtime.env
set +a
.venv/bin/python scripts/seed-experience-uat.py
.venv/bin/python scripts/seed-reliability-uat.py
trap '.venv/bin/python scripts/seed-reliability-uat.py cleanup' EXIT
.venv/bin/python scripts/seed-reliability-uat.py recover
UAT_SSE_OBSERVE_MS=35000 node scripts/db029-uat.cjs
```

开发构建复验可用 Vite 启动并指向同一 Django API，设置 `UAT_ORIGIN` 为 Vite 的地址后复跑。脚本会记录目标控制台告警、页面异常及页面实际读数。
