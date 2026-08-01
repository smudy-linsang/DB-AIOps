# DB-AIOps 平台独立测试报告

| 项目 | 内容 |
|------|------|
| 被测系统 | DB-AIOps 数据库智能监控平台（Django 6.0.5 + React SPA） |
| 测试基线 | Git commit `98c7637`（feat phase8 AiOps中心） |
| 测试环境 | Python 3.12.13 / PostgreSQL + Elasticsearch + Redis / macOS |
| 测试方法 | 参照大型商业银行金融科技项目管理方法论，分五轮独立测试 |
| 测试轮次 | ① 静态代码审查 ② 自动化回归测试 ③ 接口测试(SIT) ④ 功能测试(UAT) ⑤ 安全专项测试(ST) |
| 测试结论 | **不通过（有条件）**：存在 2 项致命、4 项高危缺陷，须修复后复测方可投产 |
| 报告日期 | 2026-08-01 |

---

## 一、执行摘要

本次按金融科技项目质量管理要求，对 DB-AIOps 平台开展了五轮独立测试，覆盖后端 90+ 个 API 路由、核心引擎模块、前端关键页面与安全配置。

共发现 **19 项缺陷**，按严重程度分布：

| 级别 | 数量 | 说明 |
|------|------|------|
| 致命 (Critical) | 2 | 阻断投产，存在数据篡改/未授权执行风险 |
| 高危 (High) | 4 | 严重安全/功能缺陷，须优先修复 |
| 中危 (Medium) | 7 | 影响健壮性、可维护性或存在潜在风险 |
| 低危 (Low) | 6 | 代码质量与规范性问题 |

**核心风险研判**：系统存在典型的"重前端门控、轻后端鉴权"反模式——前端 UI 按角色正确隐藏了操作入口，但后端多个写接口缺少权限校验，攻击者绕过界面直接调用 API 即可越权篡改核心配置（已实证）。此类缺陷在银行生产环境属不可接受的安全风险。

---

## 二、缺陷明细

### 致命级 (Critical)

#### BUG-001 自愈操作执行接口无身份认证，仅靠 CSRF 偶然防护
- **位置**：`monitor/views_enhanced.py` `execute_operation`（L186），路由 `POST /api/v1/remediation/<id>/execute/`（urls.py L167）
- **现象**：该接口可在目标数据库上执行 SQL 操作，但函数无任何鉴权装饰器。实测匿名 POST 返回 403，但响应为 **Django CSRF HTML 错误页**，证明拦截来自 CSRF 中间件而非身份认证。
- **风险**：CSRF 防护对非浏览器客户端（脚本/工具）可被绕过（获取 CSRF cookie 后携带即可），届时匿名用户可直接在纳管数据库执行操作。属最高优先级。
- **实证**：`curl -X POST .../remediation/1/execute/` → 403（HTML CSRF 页，非 JSON 401）。
- **修复方向**：添加 `@require_auth` + `@require_role`，写操作必须双重校验。

#### BUG-002 垂直越权：只读用户可篡改/删除数据库核心配置（已实证）
- **位置**：`monitor/api_views.py` `DatabaseConfigDetailView` 的 PUT（L408）/ DELETE（L485）
- **现象**：dispatch 仅加 `require_auth`（登录校验），未加 `require_permission('databases.update'/'databases.delete')`。
- **实证**：以 `readonly_user` 登录，`PUT /api/v1/databases/6/` 成功将 host 由 `119.45.220.89` 篡改为 `127.0.0.1`，返回"数据库配置更新成功"（测试后已用 admin 恢复原值）。
- **对照**：同视图 POST 创建、用户列表接口正确返回 403，说明仅 PUT/DELETE 遗漏。前端对 readonly 用户已正确隐藏编辑/删除按钮，但后端未拦截，构成"仅前端鉴权"反模式。
- **修复方向**：PUT/DELETE 内补充 `require_permission` 校验。

---

### 高危级 (High)

#### BUG-003 多个 Legacy API 端点完全无鉴权，匿名可访问（已实证）
- **位置**：`monitor/views_enhanced.py` 共 11 个函数视图均无鉴权装饰器，且已在 urls.py 注册。
- **实证**（匿名请求返回 200 + 真实 JSON）：
  - `GET /api/metrics/6/` → 200，泄露数据库版本 `8.0.33-v24-txsql`、server_id 等
  - `GET /api/baseline/6/`、`/api/rca/6/`、`/api/anomaly-detection/6/`、`/api/baseline-trend/6/`、`/api/intelligent-baseline/6/` → 200
  - `GET /api/v1/remediation/1/detail/` → 200，泄露审计明细（配置名、操作类型、风险级别）
  - `POST /api/v1/databases/<id>/toggle-active/` 可匿名启停监控
- **修复方向**：全部添加 `@require_auth`。

#### BUG-004 慢查询三个端点缺少鉴权，匿名泄露 SQL 文本（已实证）
- **位置**：`monitor/api_views.py` `DatabaseSlowQueriesView`(L2688)、`DatabaseSlowQueryAnalysisView`(L2727)、`DatabaseSQLTextSearchView`(L2757)
- **现象**：dispatch 仅 `@csrf_exempt`，遗漏 `@require_auth`。
- **实证**：匿名访问 `/api/v1/databases/6/slow-queries/`、`/slow-queries/analysis/`、`/slow-queries/search/` 均返回 200。
- **修复方向**：补充 `@method_decorator(require_auth)`。

#### BUG-005 MySQL/GBase/TDSQL 连接测试恒报失败（功能缺陷，已单元级实证）
- **位置**：`monitor/db_connector.py` `test_connection`（L218-230）
- **现象**：MySQL 系连接使用 `DictCursor`（L115），`fetchone()` 返回 dict，但代码以 `result[0]` 整数下标取值，必抛 `KeyError: 0`，被 except 捕获后返回"连接失败: 0"。即**即使数据库实际连通，连接测试也永远失败**。项目主力库 TDSQL 属 MySQL 系，直接受影响。
- **实证**：模拟 DictCursor 返回 `{'VERSION()': '8.0.36'}`，`result[0]` 抛 `KeyError: 0`；正确写法 `next(iter(result.values()))` 返回 `8.0.36`。
- **修复方向**：`version = next(iter(result.values())) if isinstance(result, dict) else result[0]`。

#### BUG-006 接口限流与登录爆破防护完全缺失
- **位置**：`monitor/rate_limit.py`（`RateLimitMiddleware` 已实现但**从未注册**进 MIDDLEWARE，全项目零引用，属死代码）；登录接口无失败锁定/延迟/验证码。
- **实证**：连续 5 次错误密码登录均返回 401，无任何限流、锁定或延迟；`API_RATE_LIMIT` 配置项实际未生效。
- **风险**：登录接口可被暴力破解；API 可被滥用/DoS。
- **修复方向**：注册限流中间件；登录增加失败计数锁定与图形验证码。

---

### 中危级 (Medium)

#### BUG-007 审批接口使用不存在的角色编码，RBAC 逻辑失效 + 潜在崩溃
- **位置**：`monitor/api_views.py` L1307 等（`AuditLogApproveView`/`RejectView`/`ExecuteView`/`ExecuteDryRunView`）
- **现象**：`require_role(['dba_supervisor','admin'])` / `['dba_operator',...]` 使用的角色编码在系统中不存在（实际为 super_admin/dba/auditor/config_operator/readonly），导致除 super_admin 外所有用户调用均 403；L1327 调用未定义函数 `get_user_role`（应为 `get_user_role_code`），触发时 `NameError` 崩溃。
- **修复方向**：改用系统实际角色编码；修正函数名。

#### BUG-008 告警管理器重构后单元测试失效，核心告警链路失去回归覆盖
- **位置**：`monitor/tests.py`（3 个失败用例）vs `monitor/alert_manager.py` v3.0
- **现象**：`AlertManager` 重构为"通知规则驱动"后，`fire()/resolve()` 改走 `_send_to_channels()`（真实邮件/钉钉/企微），注入的 `notifier` 不再被调用，但旧测试仍断言 `notifier.call_count`，导致 3 个用例失败。`notifier` 参数已沦为半死代码（仅"规则匹配但渠道为空"边角分支使用），注入契约失效。
- **影响**：告警触发/恢复/去重核心链路无有效自动化回归保护。
- **修复方向**：重写测试以 mock 通知渠道；评估移除误导性 `notifier` 参数。

#### BUG-009 SQL 注入风险：plan_capture 的 db_name 未校验
- **位置**：`monitor/plan_capture.py` L56/L75/L137
- **现象**：`SqlExplainView` 接收用户 POST 的 `sql_text`、`db_name`，`db_name` 完全无校验直接拼入 ``USE `db_name` ``；`sql_text` 虽有首关键字白名单，但仍以 f-string 拼接执行 EXPLAIN。
- **修复方向**：`db_name` 做标识符正则白名单 `^[a-zA-Z0-9_]+$`；`sql_text` 加强解析校验。

#### BUG-010 Agent 深度排查并发竞态，可产生重复轨迹
- **位置**：`monitor/api_views_phase8.py` `InvestigateView`（L192-213）
- **现象**：互斥检查（查 running 轨迹）与轨迹创建不在同一原子窗口——`AgentTrace` 行在子线程内才创建。并发 POST（前端双击/重试）可同时通过检查各起线程，产生两条 running 轨迹，双倍 LLM 成本，且结束时互相覆盖 `incident.rca_result['agent']`。
- **修复方向**：检查+创建移入 `transaction.atomic()` 并在主线程完成后再启动子线程。

#### BUG-011 前端深度排查轮询定时器未清理（内存/网络泄漏）
- **位置**：`frontend/src/pages/IncidentDetail.jsx` L110-128
- **现象**：`setInterval`/`setTimeout` 创建于事件处理器，组件卸载（点"返回"）时无清理路径，轮询持续最多 3 分钟并对已卸载组件 setState。
- **修复方向**：用 ref 保存定时器并在卸载 effect 中清理。

#### BUG-012 SSE 实时推送端点允许匿名订阅
- **位置**：`monitor/sse_views.py` L123-126
- **现象**：Token 为空时仍允许建立 SSE 连接，匿名可订阅 `monitor:alerts`/`monitor:metrics` 实时事件流。
- **修复方向**：Token 为空或校验失败返回 401。

#### BUG-013 Prometheus /metrics 端点无认证，信息泄露
- **位置**：`monitor/observability.py` L242-247
- **现象**：匿名可获取数据库名称、类型、数量、告警统计、采集状态等基础设施信息。
- **修复方向**：增加 IP 白名单或 Bearer Token 认证。

---

### 低危级 (Low)

| 编号 | 位置 | 问题 |
|------|------|------|
| BUG-014 | `views_enhanced.py` L43-110 | 5 个 API `except Exception` 返回 500 但无日志，且向客户端回传 `str(e)` 内部细节；`execute_operation` 失败分支缺 `status=500` |
| BUG-015 | `diagnosis_pipeline.py`/`rca_engine_v2.py`/`causal_miner.py`/`tasks_phase8.py` 等 | 多处 `except: pass` 静默吞异常零日志，故障不可观测（causal_miner 把 DB 故障静默成空结果） |
| BUG-016 | `requirements.txt` | numpy 被 3 个模块 import 但未声明（孤立模块，接入即 ImportError）；依赖普遍未锁定版本，部署复现性差 |
| BUG-017 | `elasticsearch_engine.py` L42-69 | 全局 ES 客户端重建无锁，失效客户端未 close（对比 llm/providers.py 已正确加锁） |
| BUG-018 | `dbmonitor/urls.py` L299 | SPA catch-all `re_path(r'^(?P<path>.*)$')` 使不存在的 `/api/*` 路径返回 200 HTML 而非 404 JSON，误导 API 消费方（本次测试初期即造成误判） |
| BUG-019 | `settings.py` / 部署 | dev 环境 DEBUG=True；生产部署须确保关闭。HTTP 响应缺 CSP 头（X-Frame-Options/X-Content-Type-Options/Referrer-Policy 已具备） |

---

## 三、已验证正常项（供参考）

- Phase 8 AiOps 中心接口（`/api/v1/ai-ops/*`）鉴权正常，匿名正确返回 401
- 密码加密方案合理（AES-256-GCM + 随机 nonce），API 响应未回传密码明文
- 无命令注入/反序列化风险（subprocess/eval/exec/pickle/yaml.load 均未使用）
- 前端角色菜单门控正常（readonly 用户菜单裁剪、操作按钮隐藏）
- 变更流去重、案例蒸馏、因果挖掘重算均有正确幂等/事务保护
- 前端 AiOpsCenter.jsx 质量良好（错误提示、空数据兜底、无定时器泄漏）
- Phase 7 独立测试脚本 43/43 通过；Django system check 无问题
- `.env` 未被 Git 跟踪，代码无硬编码密钥

---

## 四、自动化测试结果

| 测试集 | 结果 |
|--------|------|
| `monitor/tests.py` + `tests_phase8.py`（Django） | 50 用例，47 通过 / **3 失败**（BUG-008） |
| `monitor/tests_phase7.py`（独立脚本） | 43/43 通过 |
| Django system check | 0 issues |

---

## 五、修复优先级建议

1. **立即修复（投产阻断项）**：BUG-001、BUG-002、BUG-003、BUG-004 —— 全部为鉴权/越权类安全缺陷。
2. **本迭代修复**：BUG-005（影响 TDSQL 连接测试可用性）、BUG-006（爆破防护）、BUG-007（审批流程失效）。
3. **下一迭代**：BUG-008 ~ BUG-013。
4. **持续改进**：BUG-014 ~ BUG-019 及代码质量项。

## 六、测试方法说明与局限

- 本轮测试为只读分析 + 黑盒/灰盒接口与界面验证，**未修改任何业务代码**；测试中临时修改的数据（配置6 host、测试用户密码）已恢复/隔离。
- 局限：本地无真实 MySQL/Oracle 实例，连接层缺陷（BUG-005）通过单元级模拟实证；LLM 相关链路因未配置真实 API Key 未做端到端验证。
- 建议投产前补充：渗透测试（含 CSRF 绕过验证）、性能/容量测试、灾备切换演练。
