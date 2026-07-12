# Phase 6 公共约定与契约册

> 所有子册共享。施工前必读。本册定义命名规范、字段类型映射、Redis Stream 消息 schema、
> 错误码、公共工具契约、测试基线。

---

## 1. 命名规范（强约束）

| 对象 | 规范 | 正例 | 反例 |
|------|------|------|------|
| Django 模型类 | 大驼峰单数 | `Incident` | `Incidents` |
| 模型字段 | 小写下划线 | `detected_at` | `detectedAt` |
| 外键字段 | `<关联>` + 隐含 `_id` 列 | `config`（列 `config_id`） | 直接写 `config_id` 字段 |
| **外键访问** | 序列化取 `obj.<fk>_id`，展示取 `obj.<fk>.name` | `inc.config_id` | `inc.config`（触发查询）|
| API 路径 | `/api/v1/<资源复数>/` | `/api/v1/incidents/` | `/api/v1/incident/` |
| API 响应字段 | 与前端读取逐字一致，见各册契约 | `warn_count` | 后端 `warn_count` 前端 `warning_count` |
| Redis Stream | `dbaiops:<域>` | `dbaiops:events` | `events` |
| 配置键 | 大写下划线 | `SENTINEL_INTERVAL_SEC` | `sentinelInterval` |
| 新增文件 | 见各册「文件清单」 | `monitor/sentinel.py` | — |

**外键契约铁律**（Phase 5 两次栽在这）：模型定义外键名为 `config`/`db_config`/`incident`，
则 Django 自动生成列 `config_id`/`db_config_id`/`incident_id`。序列化 JSON 时**必须**用
`obj.config_id`（不触发 DB 查询、名字确定），需要名称时用 `select_related` 后取
`obj.config.name`。**Incident 的实例外键统一命名为 `config`（不是 `db_config`）**，
以与现有 AlertLog/MonitorLog 保持一致。

---

## 2. 字段类型约定（模型字段表 → Django 字段映射）

各册数据模型章节的字段表用「逻辑类型」，按下表落成 Django 字段：

| 逻辑类型 | Django 字段 | 说明 |
|---------|-------------|------|
| `str(N)` | `CharField(max_length=N)` | 定长文本 |
| `text` | `TextField(blank=True, default='')` | 不定长 |
| `int` | `IntegerField(default=0)` | |
| `bigint` | `BigIntegerField(default=0)` | 事件序列号等 |
| `float` | `FloatField(default=0.0)` | |
| `bool` | `BooleanField(default=False)` | |
| `datetime` | `DateTimeField` | 需注明 null/auto_now/default |
| `json` | `JSONField(default=dict)` 或 `default=list` | 明确容器类型 |
| `enum(a,b,c)` | `CharField(max_length=N, choices=...)` | choices 全列 |
| `fk→X` | `ForeignKey(X, on_delete=..., related_name=...)` | 注明 on_delete |

时间统一 `USE_TZ=True` 下的 aware datetime；所有"发生/检测/解决"时间戳用
`DateTimeField(null=True, blank=True)`，创建时间用 `auto_now_add=True`。

---

## 3. Redis Stream 消息契约（跨进程唯一契约源）

**三条 Stream + 三个消费组**：

| Stream | 生产者 | 消费组 | 用途 |
|--------|--------|--------|------|
| `dbaiops:events` | sentinel_daemon, collector | `cg_detect` | 原始事件入检测 |
| `dbaiops:diagnosis` | pipeline(detect) | `cg_diag` | 事故待诊断 |
| `dbaiops:verify` | pipeline(执行修复后) | `cg_verify` | 事故待验证 |

创建方式：`XADD dbaiops:events MAXLEN ~ {PIPELINE_STREAM_MAXLEN} * <fields>`。
消费方式：`XREADGROUP GROUP cg_detect <consumer> COUNT 50 BLOCK 2000 STREAMS dbaiops:events >`，
处理成功后 `XACK`。消费组用 `XGROUP CREATE ... MKSTREAM` 幂等创建。

**消息 schema（Stream 字段全为 string，值为 JSON 序列化）**：

### 3.1 `dbaiops:events` 消息
```json
{
  "schema": "1",
  "event_uid": "EVT-<uuid4hex12>",
  "config_id": 7,
  "db_type": "tdsql",
  "source": "sentinel|collector|baseline|ml|inspection",
  "signal": "instance_down|blocked_session|conn_high|space_high|repl_broken|repl_lag|slow_surge|deadlock_surge|config_drift|baseline_deviation|...",
  "metric_key": "blocked_sessions",
  "value": 12.0,
  "threshold": 0.0,
  "severity": "critical|error|warning|info",
  "occurred_at": "2026-07-12T10:00:00.000+08:00",
  "dedup_key": "7:blocked_session",
  "detail": { "任意结构化证据": "..." }
}
```

### 3.2 `dbaiops:diagnosis` 消息
```json
{ "schema": "1", "incident_id": "INC-20260712100000-7", "config_id": 7, "trigger": "created|escalated|replan" }
```

### 3.3 `dbaiops:verify` 消息
```json
{ "schema": "1", "incident_id": "INC-...", "playbook_run_id": "PBR-...",
  "verify_metric": "blocked_sessions", "recover_expr": "== 0", "window_sec": 300, "started_at": "..." }
```

**版本化**：`schema` 字段用于未来兼容；消费者遇未知 `schema` 记警告并跳过（不崩）。

---

## 4. 信号量枚举（signal 全集，检测层与事故类别的桥梁）

`Event.signal` 与 `Incident.category` 的取值必须来自下表（本期第一批 8 类承诺范围内的信号加粗）：

| signal | 归属 category | 承诺 | 首选数据源 |
|--------|--------------|------|-----------|
| **instance_down** | availability | ✅ | sentinel |
| **blocked_session** | lock | ✅ | ash/sentinel |
| **deadlock_surge** | lock | ✅ | collector |
| **conn_high** | connection | ✅ | sentinel/collector |
| **conn_storm** | connection | ✅ | ash |
| **space_high** | capacity | ✅ | collector |
| **repl_broken** | replication | ✅ | collector |
| **repl_lag** | replication | ✅ | collector |
| **slow_surge** | performance | ✅ | ash/collector |
| **config_drift** | config | ✅ | collector |
| baseline_deviation | (按 metric 归类) | — | baseline |
| buffer_low / temp_full / undo_low / ... | performance/capacity | — | collector |

`category` 取值固定 8 类：`availability` `lock` `connection` `capacity` `replication`
`performance` `config` `other`。

---

## 5. 公共错误码（API 与内部）

| 码 | 含义 | HTTP |
|----|------|------|
| `OK` | 成功 | 200 |
| `NOT_FOUND` | 资源不存在 | 404 |
| `VALIDATION` | 参数校验失败 | 400 |
| `FORBIDDEN` | RBAC 拒绝 | 403 |
| `CONFLICT` | 状态机非法转移 | 409 |
| `PRECHECK_FAILED` | Playbook 前置检查未过 | 422 |
| `INTERNAL` | 内部错误 | 500 |

API 统一响应包络（沿用现有 `_BaseView.json_response`/`error_response`）：
成功 `{"code":"OK", ...业务字段}`；失败 `{"code":"<码>","message":"..."}`。

---

## 6. 公共工具契约（复用现有，禁止重复造）

| 需求 | 复用 | 位置 |
|------|------|------|
| Redis 客户端 | `get_redis_client()` | 新建 `monitor/redis_bus.py`，内部 `redis.from_url(settings.REDIS_URL)` |
| 目标库连接 | `DbConnector.get_connection(config)` | `monitor/db_connector.py`（已支持 oracle/mysql/pgsql/dm）|
| TDSQL 连接 | `TDSQLChecker.get_connection(config)`（带重试） | `monitor/checkers/tdsql.py` |
| 时序写入 | `get_timeseries_storage().write_metrics_batch(...)` | `monitor/timeseries.py` |
| 密码解密 | `config.get_password()` | `models.DatabaseConfig` |
| RBAC 可见库 | `get_user_database_ids(user)` | `monitor/api_views_phase5.py` |
| 认证装饰器 | `require_auth` | 现有 |

**注意**：`db_connector.py` 当前不支持 gbase/tdsql（走 mysql 协议）。6A 工单 6A-02 需扩展
DbConnector 增加 gbase/tdsql 分支（复用 pymysql），使哨兵/ASH 对六库统一取连接。

---

## 7. 测试与验收基线

- **单元测试**：新增模型的状态机转移、聚合键计算、检测规则判定式必须有单测，
  放 `monitor/tests/test_phase6_*.py`。
- **契约测试**：每个 API 工单交付一个 `curl` 脚本（放 `phase6/contracts/`），断言响应字段名
  与本册契约一致（用 `jq` 校验 key 存在）。
- **故障注入演练**：脚本放 `phase6/drills/`，每册验收章节指定。演练必须在真实/容器目标库上跑，
  重演 Phase 5「纯代码验证不可信」的教训——**代码能 import ≠ 功能可用**。
- **回归**：每子阶段结束跑 `verify_phase5.py` 同款风格的 `verify_phase6X.py`（各册交付）。
