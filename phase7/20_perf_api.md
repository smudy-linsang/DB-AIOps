# Phase 7 第二册：性能 API 层

> 工单 7B-01 ~ 7B-07。新文件 `monitor/api_views_perf.py`，路由统一挂
> `dbmonitor/urls.py`（Phase 5 教训：**工单验收第一项就是路由已注册**）。
> 所有响应样例是前后端共同契约，字段名两侧一字不改。

---

## 1. 通用约定（7B-01）

- 路由前缀: `path('api/v1/databases/<int:config_id>/perf/...')`，鉴权沿用现有
  `IsAuthenticated` + PermissionGuard 的 `perf:view`（新权限点, 默认赋 dba/admin 角色）。
- 时间参数: `from`/`to` ISO8601 或 `window=30m|1h|6h|24h|7d`（二选一, window 优先级低）。
  缺省 `window=1h`。
- 分桶: 服务端自动选桶——窗口≤2h→1m（session_ash_1m）; ≤24h→5m; >24h→30m
  （`time_bucket` 在聚合视图上二次聚合）。响应带 `bucket_sec` 告知前端。
- 实时直连类端点（sessions/blocking-tree/running-sql/explain）: 每请求独立
  `DbConnector` 连接, 单语句超时 3s（mysql `MAX_EXECUTION_TIME` hint / pg
  `SET LOCAL statement_timeout=3000` / oracle callTimeout）, 用完即关。
  失败响应 200 + `{"degraded": true, "fallback": "...", ...历史兜底数据}`。
- 错误码沿用 phase6 00_conventions（4xx 参数、502 目标库不可达以 degraded 表达）。
- 统一响应外层: `{"code":0, "data":{...}}`（与现有 API 一致）。

**验收 7B-01**: urls.py 9 条路由注册; 无权限用户 403; window/from-to 参数解析单测。

---

## 2. AAS 时间线（7B-02a）

`GET /perf/aas/?window=1h&by=wait_class`

| 参数 | 值 | 说明 |
|---|---|---|
| by | wait_class(默认)/user_name/db_name/sql_digest | 堆叠维度 |
| top | int 默认 8 | 非 wait_class 维度时取 TopN+other 合并 |

数据源 SQL（wait_class 维度）:
```sql
SELECT time_bucket(%(bucket)s, bucket) AS t, wait_class,
       SUM(active_sec)::float / EXTRACT(EPOCH FROM %(bucket)s) AS aas
FROM session_ash_1m
WHERE db_config_id=%(cid)s AND bucket BETWEEN %(f)s AND %(t)s
GROUP BY 1,2 ORDER BY 1;
```

响应样例:
```json
{"code":0,"data":{
  "bucket_sec":60,"cpu_cores":2,
  "series":[{"key":"on_cpu","points":[["2026-07-15T10:00:00Z",1.42],["2026-07-15T10:01:00Z",0.97]]},
            {"key":"user_io","points":[["2026-07-15T10:00:00Z",0.33]]}],
  "totals":{"db_time_sec":312.5,"avg_aas":1.74,"max_aas":5.2}}}
```

## 3. 顶级活动（7B-02b）

`GET /perf/top-activity/?from=...&to=...&dim=sql`（dim: sql/session/user/db/module/wait_event/object）

- 数据源: 窗口≤7d 且 dim∈(sql,user,db) → session_ash_1m; 否则 raw session_sample。
- Top SQL 附最近一条 sql_text 样例（raw 表按 digest 取 max(time) 行）与已知 plan 数。

响应样例（dim=sql）:
```json
{"code":0,"data":{"window_sec":1800,"total_active_sec":940,
 "rows":[{"key":"5t6nwtjmq76v1","pct":34.2,"active_sec":321,
          "breakdown":{"on_cpu":120,"user_io":180,"concurrency":21},
          "sql_text":"SELECT * FROM orders WHERE ...","plan_count":2,
          "sessions":14},
         {"key":"(null)","pct":11.0,"active_sec":103,"breakdown":{"on_cpu":103},
          "sql_text":null,"plan_count":0,"sessions":3}]}}
```
（dim=session 时 key=`session_id`, 附 user/program/最后 SQL; 其余维度同构。）

## 4. ASH 分面切片（7B-02c）

`GET /perf/ash-facets/?from&to&filters=wait_class:user_io,user_name:app1&dims=sql_digest,wait_event`

- filters: 逗号分隔 `列:值`, 允许列=九维白名单
  `wait_class,user_name,db_name,sql_digest,wait_event,module,program,client_host,lock_object`;
- dims: 返回每个请求维度的 Top15 分布（EMCC 左侧维度面板语义）;
- 数据源: raw session_sample（7 天内）; 超 7 天报 `{"code":40001,"msg":"明细已过保留期"}`。

响应样例:
```json
{"code":0,"data":{"matched_active_sec":412,
 "facets":{"sql_digest":[{"value":"9x1abc...","active_sec":200,"pct":48.5},
                          {"value":"(null)","active_sec":80,"pct":19.4}],
           "wait_event":[{"value":"db file sequential read","active_sec":180,"pct":43.7}]},
 "timeline":[{"t":"2026-07-15T10:00:00Z","aas":2.1}]}}
```

**验收 7B-02**: 三端点对本地 mysql 实例返回真实数据; AAS 与 raw 手工聚合误差<1%;
分面过滤叠加两条后 matched_active_sec 单调不增; 聚合端点 P95<500ms（本地 ab 压 50 次）。

---

## 5. 实时会话网格（7B-03a）

`GET /perf/sessions/`（实时直连; degraded 时回退最近一次 ASH 样本并标注）

响应样例:
```json
{"code":0,"data":{"degraded":false,"at":"2026-07-15T10:30:01Z",
 "sessions":[{"session_id":"11924","user_name":"app","client_host":"10.1.2.3:5566",
   "db_name":"testdb","command":"Query","state":"Sending data","wait_class":"user_io",
   "wait_event":"Sending data","active_secs":42,"sql_id":"4a2d3ae6...","sql_text":"SELECT ...",
   "program":null,"module":null,"is_blocked":false,"blocker_id":null,
   "lock_type":null,"lock_mode":null,"lock_object":null,"killable":true}]}}
```
- 实现直接复用 `sentinel.sample_sessions()`（同一份采样代码, 不重复实现）。

## 6. 阻塞树（7B-03b）

`GET /perf/blocking-tree/?at=now|ISO8601`
- `at=now`: 实时采样重构; `at=历史时刻`: 从 session_sample 取
  `time BETWEEN at-interval'10s' AND at`（**历史回放**能力, EMCC 没有）。
- 树重构算法（伪码）:
```
edges = {waiter: blocker}; nodes = {sid: row}
roots = {blocker for blocker in edges.values() if blocker not in edges}
tree = [build(root) for root in roots]     # 递归挂 children, 环检测(访问集合)
每节点附: user/sql_text/wait_class/lock_type/lock_mode/lock_object/wait_secs/
         subtree_waiters(递归计数, 用于"影响面"排序)
```

响应样例:
```json
{"code":0,"data":{"at":"2026-07-15T10:30:00Z","tree":[
 {"session_id":"101","role":"root_blocker","user_name":"app","active_secs":120,
  "sql_text":"UPDATE orders SET ...","subtree_waiters":3,"killable":true,
  "children":[{"session_id":"102","wait_secs":118,"lock_type":"RECORD",
    "lock_mode":"X,REC_NOT_GAP","lock_object":"testdb.orders","children":[]}]}]}}
```

**验收 7B-03**: 注入 3 层链（A 阻 B, B 阻 C）→ 树深度 3, subtree_waiters 正确;
历史 at 回放同一时刻结果一致; 环注入（理论）不死循环。

---

## 7. 运行中 SQL / 进度（7B-04）

`GET /perf/running-sql/`（实时直连, 三库统一 schema, 逼近 Real-Time SQL Monitoring）

逐库来源:
- Oracle: `v$session`(ACTIVE, sql_id 非空) LEFT JOIN `v$session_longops`
  (sofar/totalwork→progress_pct, time_remaining) ON sid+sql_id;
- PG: `pg_stat_activity`(active) LEFT JOIN `pg_stat_progress_vacuum/create_index/
  cluster/copy/analyze`（UNION 归一 phase+progress_pct=done/total）ON pid;
- MySQL: `processlist`(Query, time>1) + `performance_schema.events_stages_current`
  (WORK_COMPLETED/WORK_ESTIMATED→progress_pct, 典型 alter table)。

响应样例:
```json
{"code":0,"data":{"degraded":false,"rows":[
 {"session_id":"38","sql_id":"5t6nwtjmq76v1","sql_text":"CREATE INDEX ...",
  "user_name":"MONITOR","elapsed_sec":95,"wait_class":"user_io",
  "phase":"Sort Output","progress_pct":62.4,"est_remain_sec":58,
  "plan_available":true,"killable":true}]}}
```
progress 取不到时 `progress_pct:null`（前端显示 elapsed 计时条）。

**验收 7B-04**: Oracle 建大索引可见 longops 进度; PG VACUUM 大表可见 phase+pct;
MySQL ALTER TABLE 可见 stage 进度; 普通查询三库均出现在列表（progress null）。

---

## 8. SQL 详情 + 计划采集（7B-05）

`GET /perf/sql/<digest>/?window=24h`

响应样例:
```json
{"code":0,"data":{
 "digest":"5t6nwtjmq76v1","db_type":"oracle",
 "sql_text_sample":"SELECT o.* FROM orders o WHERE o.user_id = ?",
 "trend":{"bucket_sec":300,
   "series":{"exec_delta":[["2026-07-15T09:00:00Z",120]],
             "avg_latency_ms":[["2026-07-15T09:00:00Z",4.2]],
             "rows_delta":[["...",4800]],"reads_delta":[["...",96000]],
             "ash_active_sec":[["...",38]]}},
 "ash_breakdown":{"user_io":0.61,"on_cpu":0.31,"concurrency":0.08},
 "plans":[{"plan_hash":"3392049386","captured_at":"2026-07-15T08:00:00Z",
           "is_current":true,"cost_total":42.0,"source":"auto"},
          {"plan_hash":"771208994","captured_at":"2026-07-14T02:00:00Z",
           "is_current":false,"cost_total":19.0,"source":"auto"}],
 "plan_changed_at":"2026-07-15T07:58:00Z",
 "advisor":{"index_suggestions":[{"table":"orders","columns":["user_id"],
             "reason":"全表扫描, 谓词列无索引"}]},
 "related_incidents":[{"incident_id":"INC-...-1","title":"慢查询突增","status":"resolved"}]}}
```
- trend 源: sql_stat（exec/latency/rows/reads）+ session_ash_1m（ash_active_sec）;
- advisor 源: 复用 `index_advisor` 对 sql_text_sample 出建议（失败给空数组）;
- related_incidents: 该实例 performance/lock 类事故中 rca/events 含此 digest 的近 7d。

`GET /perf/sql/<digest>/plan/<plan_hash>/` → `{"plan_text":"...","plan_json":{...}}`
`POST /perf/sql/<digest>/explain/` → 触发 `plan_capture.capture(source='manual')`,
  同步返回新 plan（3s 超时; MySQL/PG 需 sql_text_sample 存在否则 40002）。

**验收 7B-05**: 对演练产生的慢 SQL digest, 详情页数据五段齐全; 手动 explain 生成
新计划; 计划文本树形缩进正确。

---

## 9. 期间对比（7B-06）

`GET /perf/compare/?a_from&a_to&b_from&b_to`

响应: 两窗口各自 `{aas_by_class, top_sql[10], top_wait_events[10], totals}` +
`diff` 段（按 key 对齐算差值与倍率, 新增/消失标记 `new:true/gone:true`）。
样例略长, 结构 = §2/§3 复用, 外层 `{"a":{...},"b":{...},"diff":{"top_sql":[
{"key":"...","a_active_sec":10,"b_active_sec":210,"ratio":21.0,"new":false}]}}`。

**验收 7B-06**: 取演练前后两窗口, diff 能把注入 SQL 排 ratio 第一。

---

## 10. kill 会话（7B-07, 唯一写操作, 走审批链）

`POST /perf/sessions/<session_id>/kill/` body `{"reason":"阻塞源, 等待 120s"}`

- **不直接执行**。创建 `AuditLog(action_type='EXECUTE_SQL', risk_level='high',
  sql_command='KILL <id>'|pg_terminate|alter system kill session, status='pending')`,
  复用现有 `/api/v1/auditlogs/<id>/approve|execute` 流转（含 dry-run）。
- 响应 `{"code":0,"data":{"audit_id":123,"next":"待审批"}}`; 前端在阻塞树/会话网格
  给"申请终止"按钮 + 审批人一键通过入口（有 `auditlog:approve` 权限则同屏两步）。
- 事故上下文中的 kill（作战室）仍走 Playbook 链, 两链并存各司其职（契约: 性能中心
  ad-hoc → AuditLog; 事故处置 → PlaybookRun）。

**验收 7B-07**: 无审批直接 execute 被拒; 审批后执行成功且目标会话消失;
AuditLog 记录完整（执行人/审批人/SQL/结果）。
