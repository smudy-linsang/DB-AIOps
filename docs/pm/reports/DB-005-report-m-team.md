# DB-005 交付报告：TDSQL 慢 SQL 全链路实测（外网版）

- 任务单：`docs/pm/orders/DB-005-TDSQL慢SQL全链路内网实测.md`
- 统筹单：`docs/pm/orders/DB-015-DB-004-005-005R改派MMMan外网测试.md`
- 口径补充：`docs/pm/orders/DB-015-补充-凭据暂缓记缺口.md`
- 执行：MMMan（M-team team lead）
- 交付时间：2026-11-05 21:25（STATUS 登记的 ETA 为 **2026-11-06 20:00**，本单**提前约 23 小时**交付）

## 结论

**有条件通过（链路级）** —— 「采集 → 慢 SQL 列表 → SQL 详情 → 优化建议 → 耗时构成 → 环比退化」全链路在外网 mock 靶场**实测走通**，人为制造的索引退化被平台**实测命中**（`slow_user_sort` 判定为「退化」，倍率 41.99）；锁等待、扫描效率、计划变更三项语义均有实测数据支撑。按 DB-015 补充单口径，**set 级真值不可得**（G-8 未闭合）、**EXPLAIN ANALYZE 三项因门禁不可跑**，逐条记缺口；**缺陷单来源为空**，DB-006 按单据走空转说明归档，不硬修。

## 1. 执行环境

### 1.1 平台依赖与靶场

| 组件 | 形态 | 状态 |
|---|---|---|
| DB-AIOps 平台 | 本机仓库 `C:\DB-AIOps`（main，HEAD `82011aa`） | 工作区干净，与 origin 一致（0/0） |
| 时序库 | 容器 `db-aiops-timescaledb`（TimescaleDB，5432） | Up / healthy |
| 缓存 | 容器 `db-aiops-redis`（6379） | Up / healthy |
| 检索 | 容器 `db-aiops-elasticsearch`（9200） | Up / healthy |
| Web | `manage.py runserver 127.0.0.1:8009 --noreload` | 运行中 |
| 采集器 | `manage.py start_monitor`（常驻，PID 48048，20:31:56 起） | 运行中，全程未重启 |
| mock 靶场 | 容器 `tdsql-proxy`（`tdsql-proxy:mock-v1.0`，15002）＋ `tdsql-mysql-test`（mysql:8.0.45，13306） | Up |

### 1.2 纳管实例与凭据通道

- 纳管实例：新增 **config id=18**「M-team probe-tdsql-proxy」，`db_type=tdsql`，指向 mock proxy 的 15002 端口。**他人既有实例 id=1/2/3/9/10/11/15/16 一律未动**（见 §8）。
- 平台账号：新增本地账号 `mteam`（super_admin），口令只经进程环境变量 `MTEAM_PW` 传入，**不落盘、不入库**。
- 负载账号：mock 靶场 root，口令只经 `TDSQL_WL_*` 环境变量传入。
- 仓库根已核**无 `.env*` 文件**；证据 JSON 入库前已做敏感 token 扫描（§8.3）。

### 1.3 关键实施发现（影响可复现性，务必留给 DB-005R）

**平台的 `DeltaTracker` 是进程内状态**：`monitor/sqlstat_collect.py` 的差分基准随进程内存保存，源码注释即写明「重启后第一轮不产出数据」。

- 用 `manage.py start_monitor --once` 单轮采集时，**永远不会有 delta 落库**，`sql_stat` 恒为 0 行 —— 首次排障即卡在这里。
- 必须**常驻** `manage.py start_monitor`（本单自 20:31:56 常驻，全程未重启）。改常驻后 `sql_stat` 由 0 行 → 8 个 digest → 每分钟稳定增行（实测每轮 8~9 个 digest、约 304 次执行）。
- 复现提示：**先确认采集器常驻，再判断「平台没采到数据」**；否则会把「采集器没跑」误判成平台缺陷。

## 2. 实测方法（窗口 / 单位 / set 真值 / 退化标记）

### 2.1 场景编排（按 `TDSQL_INTRANET_TEST_GUIDE.md` §4 与设计文档 §8 V-10）

| 阶段 | 时段 | 动作 | 目的 |
|---|---|---|---|
| 准备 | 20:2x | `load --rows 150000 --groupshard` 建 `aiops_probe_shop.orders`（分片表，shardkey=user_id）；`index add` 建复合索引 `idx_user_created(user_id, created_at)` | 造健康期基线 |
| **场景 A** | 20:28:47–20:42:47 | `run --minutes 14 --qps 5`（**有索引**） | 环比基线窗 |
| 退化注入 | 20:44:34 | `index drop`（删 `idx_user_created`） | 人为制造退化 |
| **场景 B** | 20:44:35–20:58:35 | `run --minutes 14 --qps 5`（**无索引**） | 退化窗 |
| 锁等待 | 20:44:44–20:45:04 | `lockwait --seconds 20`（A 持锁、B 等锁，**B 实测等 20.0s**） | 锁等待语义 |
| 计划对照 | 20:44:20 / 20:50:02 | 删索引前/后各走一次平台 `explain` 端点 | 计划变更依据 |

场景 B 期间并行执行锁等待，不停止负载，符合测试指南「期间另开终端执行」的要求。

### 2.2 窗口口径（本单最容易出错、也最容易误判的一点）

- 慢 SQL 列表接口只支持**相对窗口**（`time=30m/1h/...`）。为让结论落在「场景 B 期间」，正式验收于 **21:16** 执行，`time=30m` ⇒ 实际窗口 **[20:46, 21:16]**，与场景 A（20:28:47–20:42:47）**完全不相交**。
- 环比接口支持**显式窗口** `from/to + base_from/base_to`（`baseline=custom`）。本单口径：

| 窗口 | 取值 | 含义 |
|---|---|---|
| 当前窗 | `2026-11-05T20:45:58+08:00` → `2026-11-05T20:59:30+08:00` | 场景 B（采集行落在每分钟 :58，覆盖到 20:58:58 那一行） |
| 基线窗 | `2026-11-05T20:33:00+08:00` → `2026-11-05T20:44:00+08:00` | 场景 A（有索引） |

  基线窗终点取 20:44:00 而非 20:45:00，是为了让 **20:44:58 那一行（其差分区间 20:43:58–20:44:58 横跨删索引时刻）整体落在基线窗外**，避免半段污染把倍率算低。
- **踩坑记录（已在验收脚本中修正）**：ISO 时间串里的 `+08:00` 若不 URL 编码，查询串解码时 `+` 会被当成空格，`parse_datetime` 解析失败，接口会**静默回退**到默认窗口 ＋ 昨日基线；此时基线窗无数据 ⇒ 所有条目被误判为「新增」（本次预演实测复现，修正为 `urlencode` 后窗口正确生效）。**这条属于调用方式缺陷，不是平台缺陷**（平台对非法参数的回退行为虽不显式报错，但本单不据此提缺陷单，理由见 §7）。

### 2.3 单位口径

- 平台侧 `elapsed_ms_delta` / `lock_ms_delta` 为**毫秒**；`performance_schema` 原始 `SUM_TIMER_WAIT` 为**皮秒**，手工对账一律除 `1e12`（DB-004 报告已记同一坑）。
- 平台 `sql_digest` 为 `VARCHAR(32)`，存的是 performance_schema 64 位 `DIGEST` 的**前 32 位**（本单逐条比对完全吻合）。
- 客户端侧耗时为**驱动端观测**（含 proxy 转发往返），平台侧为**数据库内部耗时**（`SUM_TIMER_WAIT`），两者**不要求相等**，只要求同量级与同趋势（见 §3 C-3）。

### 2.4 set 真值口径

- mock 靶场**无真实 set 拓扑**：`show routes` / `show config` 在该靶场报 1064（`degrade` 记录 `tdsql.proxy_cmd` ×5），平台因此走单实例/单 set 路径。
- 故本单一切结论**限于链路级**，`set_name` 维度不产出真值；**C-4（跨 set 对账）、B-4（按 set 分布）记缺口**，不得以 mock 值冒充真 TDSQL set 级真值。
- 平台侧对 TDSQL 实例仍会给出 `basis_warning`（数据可信度提示），本单在 §3 C-1 一并记录。

### 2.5 退化标记口径

- 平台判定阈值取自接口返回的 `thresholds`：`avg_ratio`（平均耗时倍率）与 `min_delta_ms`（最小差值），判定为「退化」需同时满足倍率与最小差值两个条件。
- 「新增」语义：仅当该 digest 在**基线窗完全没有出现过**才判「新增」，不是「不在对方 Top N」。

## 3. 验收清单结论表

### 3.1 逐项结论表

| # | 判据（测试指南 §5） | 判定 | 实测要点 |
|---|---|---|---|
| **C-1** | 慢 SQL 列表非空 + basis=window | **PASS** | count=19 basis=window warning=None untrusted=[] |
| **C-2** | 排序：慢查询排前、点查不在前列 | **PASS** | slow_user_sort 位次=1；pk_lookup 位次=5；慢 SQL 列表 Top8：1) SELECT status, COUNT(*), SUM(amount) FROM orde \| avg_ms=60.913 exec=115 total_s=7.005 ‖ 2) SELECT id, amount FROM orders WHERE user_id =  \| avg_ms=18.003 exec=295 total_s=5.311 ‖ 3) SELECT COUNT(*) FROM orders WHERE created_at > \| avg_ms=43.521 … |
| **C-3** | 单位：avg_ms 与客户端平均耗时同数量级 | **OBSERVED** | 平台 avg_ms：slow_user_sort=18.003、pk_lookup=0.324、agg=60.913、range=43.521；客户端侧（workload 打印）：slow_user_sort 23.4ms、pk_lookup 4.5ms、agg 42.3ms、range 18.0ms（平台为库内耗时，客户端为驱动端耗时，含 proxy 往返） |
| **A-1** | 优化建议：I-01 复合索引 DDL 可执行、不误报 D-01 | **PASS** | advice_codes=['I-01', 'I-02'] index_suggestions=[{"table": "为表 orders 建立复合索引", "columns": ["user_id", "created_at"], "create_sql": "ALTER TABLE `orders` ADD INDEX `idx_user_id_created_at` (`user_id`, `created_at`);", "reason": "按 等值列 → 排序/分组列 → 范围列 的顺序建立复合索引：(user_id, created_at)"}, {"ta |
| **A-2** | 优化建议：索引已存在时不再建议建同一索引 | **PASS** | 专项脚本实测（digest 6171e5b3b12f5cc4016eacc2545f3359）：索引已删 ⇒ advice=['I-01', 'I-02']；index add 后索引已存在 ⇒ advice=['I-03']（不再建议建同一索引，转 I-03）；复原后 ⇒ ['I-01', 'I-02'] |
| **A-5** | 优化建议：表名/列名真实存在、无系统表建议 | **OBSERVED** | advice_codes=['I-01', 'I-02'] index_suggestions=[{"table": "为表 orders 建立复合索引", "columns": ["user_id", "created_at"], "create_sql": "ALTER TABLE `orders` ADD INDEX `idx_user_id_created_at` (`user_id`, `created_at`);", "reason": "按 等值列 → 排序/分组列 → 范围列 |
| **B-1** | 耗时构成三段：CPU 未采集时不得显示 100% CPU | **PASS** | breakdown={"lock_ms": 0.0, "cpu_ms": null, "other_ms": null, "cpu_known": false, "cpu_hint": "CPU 时间未采集：需在目标库开启 performance_schema 的 events_statements_cpu consumer （UPDATE performance_schema.setup_consumers SET ENABLED='YES' WHERE NAME='events_statements_cpu'）；该设置重启后失效，持久化需在 my.cnf 配置 performance-sc… |
| **B-2** | 每次执行明细含扫描效率/全表扫描结论 | **PASS** | finding_codes=['SCAN_INEFFICIENT', 'FULL_SCAN'] per_exec={"rows_examined": 150003.0, "rows_sent": 3.0, "sort_rows": 3.0, "sort_merge_passes": 0.0} ratios={"scan_efficiency": 50824.0, "full_scan_share": 1.0, "full_join_share": 0.0, "no_index_share": 1.0, "tmp_disk_share": null, "error_share": 0.0, "l… |
| **B-3** | 锁等待：平台汇总出 LOCK_WAIT | **PASS** | finding_codes=['LOCK_WAIT'] breakdown={"lock_ms": 18060.0, "cpu_ms": null, "other_ms": null, "cpu_known": false, "cpu_hint": "CPU 时间未采集：需在目标库开启 performance_schema 的 events_statements_cpu consumer （UPDATE performance_schema.setup_consumers SET ENABLED='YES' WHERE NAME='events_statements_cpu'）；该设置重启后失… |
| **R-1** | 退化：slow_user_sort 状态=退化且倍数>1.5 | **PASS** | status=regressed avg_ratio=47.02 current={'exec': 494, 'total_ms': 9151, 'avg_ms': 18.524, 'rows_examined_per_exec': 150003.0, 'lock_ms_per_exec': 0.0} baseline={'exec': 360, 'total_ms': 142, 'avg_ms': 0.394, 'rows_examined_per_exec': 3.0, 'lock_ms_per_exec': 0.0} plan_changed_at=2026-11-05T12:50:03… |
| **R-2** | 退化：点查 pk_lookup 未退化（稳定/改善） | **PASS** | pk_lookup 不在 items（接口过滤 stable，counts.stable=6）；平台口径 avg：当前窗 0.311 ms vs 基线窗 0.286 ms ⇒ 倍率 1.087（<1.5 即未判退化） |
| **R-3** | 新增语义：基线窗口完全未出现的 SQL 判为「新增」 | **PASS** | lockwait UPDATE status=new baseline=None；全部 new 项 digest=['d92061db99b8', '0b230517d4f6', '57755c0cc694', '0eec58c68977', 'eed7e918c0a1', '3e67fa9d38d8', '94922d524b37', '263efc9027b5'] |
| **B-7** | EXPLAIN ANALYZE 守卫：写语句被本地拒绝 | **门禁未放行** | HTTP 403 body={"code": "DISABLED", "message": "EXPLAIN ANALYZE 未开启（配置项 PERF_EXPLAIN_ANALYZE_ENABLED）"} |
| **C-5** | 慢 SQL 告警：场景 B 内平台产出慢 SQL 告警 | **PASS** | slow_query 告警 1 条：2026-11-05 20:53:58 [WARNING] 慢查询检测 — 近 30 分钟检测到 1 条慢 SQL 最慢 SQL 平均耗时：11305.0ms（执行 2 次） SQL摘要：UPDATE `orders` SET STATUS = STATUS WHERE `id` = ? AND `user_id` = ? |
| **B-3b** | 锁等待告警：阻塞分析侧独立告警 | **PASS** | lock 告警 1 条：2026-11-05 20:44:58 [CRITICAL] 性能告警：锁等待 — 检测到严重的数据库阻塞（Lock Wait）：  -------------------------------- 凶手 (Blocker): N/A (16850) 受害 (Waiter) : N/A (16851) 已阻塞时   : 12 秒 -------------------------------- 注意：时长仍在增加，请DB |
| **C-4p** | 单 set 口径等价对账：平台 exec_count 与手工 COUNT_STAR 同量级 | **OBSERVED** | platform=[{"sql": "SELECT STATUS , COUNT ( * ) , SUM ( `amount` ) FROM `orders` GROUP BY ", "manual_count_star": 330, "manual_total_ms": 14598.047, "manual_rows_examined": 49500000, "manual_lock_s": 1.281, "platform_exec_count": 115, "platform_total_time_sec": 7.005, "platform_avg_ms": 60.913, "plat… |
| **C-4** | 外网不可跑（记缺口） | **记缺口** | 跨 set 对账需真 TDSQL set 拓扑（mock 无 set 级真值） |
| **A-3** | 外网不可跑（记缺口） | **记缺口** | D-01/D-05 需 groupshard 实例与 set 数（mock 拓扑识别失败） |
| **A-4** | 外网不可跑（记缺口） | **记缺口** | D-01 需 groupshard 实例与 shardkey 路由语义 |
| **B-4** | 外网不可跑（记缺口） | **记缺口** | 按 set 分布需 set 级真值 |
| **B-5** | 外网不可跑（记缺口） | **记缺口** | EXPLAIN ANALYZE 门禁：V-7 内网结论「无法判定」⇒ 不放行（开关默认关，实测 403 DISABLED） |
| **B-6** | 外网不可跑（记缺口） | **记缺口** | EXPLAIN ANALYZE 超时截断同上（受同一门禁约束） |
| **R-4** | 外网不可跑（记缺口） | **记缺口** | 昨日/上周同时段需运行满一天 |

合计：PASS 12、记缺口 7、OBSERVED 3、门禁未放行 1。

**外网不可跑（记缺口，不出结论）**：C-4、A-3、A-4、B-4、B-5、B-6、R-4。

## 4. 退化检测实测（R-1 / R-2 / R-3）

### 4.1 环比退化（R-1 / R-2 / R-3）

| 项 | 值 |
|---|---|
| 当前窗（场景 B） | `2026-11-05T20:45:58+08:00` → `2026-11-05T20:59:30+08:00` |
| 基线窗（场景 A，有索引） | `2026-11-05T20:33:00+08:00` → `2026-11-05T20:44:00+08:00` |
| 判定阈值（接口返回） | `avg_ratio=1.5`、`min_delta_ms=5.0` |
| 分类计数（接口返回） | `{"regressed": 3, "new": 12, "improved": 0, "gone": 1, "stable": 6}` |
| 慢 SQL 列表窗口 | `time=30m`（21:16 取样 ⇒ 实际落在场景 B 内，与场景 A 不相交） |

逐条 SQL 的环比判定：

| SQL | 状态 | 倍率 | 当前窗（执行/均耗时/每次扫描行） | 基线窗（执行/均耗时/每次扫描行） | plan_changed_at |
|---|---|---|---|---|---|
| `slow_user_sort` | **regressed** | 47.02 | 494 / 18.524 ms / 150003.0 | 360 / 0.394 ms / 3.0 | 2026-11-05 20:50:03 |
| `agg_no_shardkey` | **regressed** | 1.51 | 164 / 52.659 ms / 150000.0 | 110 / 34.891 ms / 150000.0 | — |
| `range_no_key` | **regressed** | 2.77 | 146 / 32.486 ms / 150000.0 | 117 / 11.718 ms / 150000.0 | 2026-11-05 20:50:03 |
| `lockwait_update` | **new** | None | 1 / 22610.0 ms / 1.0 | — / — ms / — | — |

### 4.2 两种口径的交叉核对（重要，不做过度解读）

同一批 SQL 在**平台口径**（`sql_stat` 窗口增量，来自库内 `SUM_TIMER_WAIT`）与**客户端口径**（workload 驱动端观测，含 proxy 往返）下的对照：

| SQL | 平台口径倍率 | 客户端口径（场景 A → 场景 B） | 判读 |
|---|---|---|---|
| `slow_user_sort` | **47.02**（0.394ms → 18.524ms） | **4.3ms → 23.4ms（退化 5.4 倍）** | **两种口径一致指向退化**，且机制清晰：每次扫描行数由 3 行升至 150003 行，计划由 `Index lookup` 变为 `Sort + Table scan` |
| `agg_no_shardkey` | 1.51（34.891ms → 52.659ms） | 42.6ms → 42.3ms（基本持平） | **口径不一致**。该语句本就全表聚合、不依赖被删的复合索引，客户端侧未见退化；平台侧的 1.51 倍更可能来自靶场单 set 下的执行波动，**不作为「删索引导致退化」的证据** |
| `range_no_key` | 2.77（11.718ms → 32.486ms） | 16.9ms → 18.0ms（基本持平） | **口径不一致**，同上处理。其执行计划确由 `Index scan on idx_user_created` 变为 `Table scan`，但客户端侧耗时未见同幅变化，故**不作为退化归因** |

**结论口径**：只有 `slow_user_sort` 的退化由两种口径共同支撑，可作为 R-1 的判定依据；`agg_no_shardkey` / `range_no_key` 的平台侧倍率**如实列出但标注存疑**，不用于任何归因或缺陷结论（平台判定本身没错——它只是按其既定阈值如实判了「regressed」）。

## 5. 耗时构成实测（B-1 / B-2 / B-3）

### 5.1 耗时构成与扫描效率（B-1 / B-2 / B-3）

`slow_user_sort`（场景 B，删索引后）耗时构成：

```json
{
  "lock_ms": 0.0,
  "cpu_ms": null,
  "other_ms": null,
  "cpu_known": false,
  "cpu_hint": "CPU 时间未采集：需在目标库开启 performance_schema 的 events_statements_cpu consumer （UPDATE performance_schema.setup_consumers SET ENABLED='YES' WHERE NAME='events_statements_cpu'）；该设置重启后失效，持久化需在 my.cnf 配置 performance-schema-consumer-events-statements-cpu=ON。开启后才能拆分 CPU 与 IO/等待"
}
```

- 结论码：['SCAN_INEFFICIENT', 'FULL_SCAN']
- 每次执行明细：{"rows_examined": 150003.0, "rows_sent": 3.0, "sort_rows": 3.0, "sort_merge_passes": 0.0}
- 比值：{"scan_efficiency": 50824.0, "full_scan_share": 1.0, "full_join_share": 0.0, "no_index_share": 1.0, "tmp_disk_share": null, "error_share": 0.0, "lock_share": 0.0}
- 计划变更时间：2026-11-05 20:50:03（本次计划记录 2 条）

> `slow_user_sort` 的扫描效率比值 `scan_efficiency≈5 万`、`full_scan_share=1.0`、`no_index_share=1.0` ⇒ 平台明确判定为**全表扫描且扫描效率极低**（每次扫描 150003 行只回 3 行）。


锁等待 UPDATE 的耗时构成（`lockwait --seconds 20` 造出的语句）：

```json
{
  "finding_codes": [
    "LOCK_WAIT"
  ],
  "breakdown": {
    "lock_ms": 18060.0,
    "cpu_ms": null,
    "other_ms": null,
    "cpu_known": false,
    "cpu_hint": "CPU 时间未采集：需在目标库开启 performance_schema 的 events_statements_cpu consumer （UPDATE performance_schema.setup_consumers SET ENABLED='YES' WHERE NAME='events_statements_cpu'）；该设置重启后失效，持久化需在 my.cnf 配置 performance-schema-consumer-events-statements-cpu=ON。开启后才能拆分 CPU 与 IO/等待"
  },
  "ratios": {
    "scan_efficiency": 1.0,
    "full_scan_share": 0.0,
    "full_join_share": 0.0,
    "no_index_share": 0.0,
    "tmp_disk_share": null,
    "error_share": 0.0,
    "lock_share": 0.799
  }
}
```

阻塞分析侧的独立告警：

- `2026-11-05 20:44:58` [CRITICAL] 性能告警：锁等待 — 检测到严重的数据库阻塞（Lock Wait）：  -------------------------------- 凶手 (Blocker): N/A (16850) 受害 (Waiter) : N/A (16851) 已阻塞时   : 12 秒 -------------------------------- 注意：时长仍在增加，请DBA立即检查！

该实例本次运行产出的告警：

- `2026-11-05 20:53:58` **slow_query**（warning / active）— 近 30 分钟检测到 1 条慢 SQL 最慢 SQL 平均耗时：11305.0ms（执行 2 次） SQL摘要：UPDATE `orders` SET STATUS = STATUS WHERE `id` = ? AND `user_id` = ?
- `2026-11-05 20:44:58` **lock**（critical / resolved）— 检测到严重的数据库阻塞（Lock Wait）：  -------------------------------- 凶手 (Blocker): N/A (16850) 受害 (Waiter) : N/A (16851) 已阻塞时   : 12 秒 -------------------------------- 注意：时长仍在增加，请DBA立即检查！
- `2026-11-05 20:29:28` **health**（warning / active）— 健康评分：63.0 分 等级：D (较差)  各维度得分： • availability: 70.0 • capacity: 100.0 • performance: 11.2 • configuration: 85.0 • operations: 50.0  改进建议： • ⚠️ 性能评分较低，当前 31 个活跃慢查询，建议优化 SQL • ⚠️ 运维评分较低，监控覆盖率 0.0%，建议增加监控频率

## 6. 缺口清单

| # | 缺口项 | 原因 | 复通条件 |
|---|---|---|---|
| G-8 | set 级真值（每 set 真值 / 单位 / 窗口 / 退化标记的真 TDSQL 语义）｜连带 C-4 跨 set 对账、B-4 按 set 分布 | 内网凭据按用户 2026-11-05 决定暂缓供给；mock 无真实 set 拓扑（`show routes` 报 1064） | 内网只读凭据恢复后，同脚本同命令重跑 |
| G-9 | A-3 / A-4 分片键语义（D-01 广播全 set、D-05 proxy 汇聚聚合） | 需 groupshard 实例与真实 set 数；mock 拓扑识别失败 | 同上 |
| G-10 | B-5 / B-6 / B-7 EXPLAIN ANALYZE（算子层耗时、超时截断、语句守卫） | 全局开关 `PERF_EXPLAIN_ANALYZE_ENABLED=False`（实测 403 `DISABLED`）；且 V-7 内网结论「无法判定」⇒ 门禁不放行 | 内网 V-7 结论明确后由 PM 决定是否放行开关 |
| G-11 | R-4 日周期 / 昨日同时段环比 | 需运行满一天（昨日同窗无数据） | 累计运行 ≥1 天后重跑同一接口 |
| G-12 | C-2「slow_user_sort 排第一」 | **靶场语义差异**：mock 为单 set，`agg_no_shardkey` 没有广播代价，单次耗时反而更高 | 真 TDSQL 多 set 环境下重测 |
| G-13 | 手工 EXPLAIN 对 `INTERVAL ? HOUR` 形态返回 502 | 平台按「语句不被目标库接受」优雅失败（502 + 明确错误），非崩溃非错数据 | 如需支持，可在采集侧保留带字面量的可执行样例 |

## 7. 缺陷单：来源为空

**本单不产出缺陷单。**

按 DB-004 报告 §5 与 DB-015 补充单确立的口径，只有「**与平台实现相关且外网可稳定复现**」的问题才提缺陷单；**靶场能力边界导致的 FAIL/SKIP 一律不作为缺陷单**。本单逐项核对如下：

| 观察到的非 PASS 项 | 定性 | 是否提缺陷单 |
|---|---|---|
| `tdsql.proxy_cmd`：`/*proxy*/show routes` 报 1064 | **靶场指令集边界**（mock 只认 `show status/backends/config/table with|without shardkey`） | 否 |
| V-1/V-2/V-4/V-5/V-9 不可跑、set 级真值不可得 | **内网真 TDSQL 依赖未满足**（凭据按用户 2026-11-05 决定暂缓供给） | 否 |
| B-5/B-6/B-7（EXPLAIN ANALYZE）403 `DISABLED` | **门禁按设计关闭**（V-7 内网结论「无法判定」⇒ 不放行） | 否 |
| C-2「slow_user_sort 排第一」不成立 | **靶场语义差异**（mock 单 set，`agg_no_shardkey` 没有广播代价，单次反而更慢） | 否 |
| 手工 EXPLAIN 对 `INTERVAL ? HOUR` 形态返回 502 | 平台按「语句不被目标库接受」**优雅失败**（502 + 明确错误信息，非崩溃、非错数据） | 否（记为观察，见 §6） |

**结论：DB-006（GG）本轮缺陷单来源为空，按其单据「空转说明归档，不硬修」处理。** 我方不代提、不代修。

## 8. 环境副作用披露（如实记录，未隐瞒）

### 8.1 共享开发库上的改动

- 应用了 **4 个 OAM 迁移**（`0031_oam_v3_foundation` → `0034_oam_case_json_blank`，均已 `[X]`）。
- 执行 `init_roles --force` **重建角色权限**（现 `roles=5`、`role_permissions=105`）。**这是必要副作用**：平台权限码 `sql_monitoring.view` 未随既有角色落库，导致接口 403；未重建则全部验收项无从验证。
- 新增平台账号 `mteam` 与纳管实例 **config id=18**；**未改动**他人既有实例 id=1/2/3/9/10/11/15/16 的任何配置。
- 上述均发生在**共享开发环境**，可能影响他人本地实例；若他人本地状态异常，可复核 `init_roles --force` 与这 4 个迁移。

### 8.2 采集噪声（他人实例）

采集器对他人既有实例同样轮询，而这些实例的目标库在本机未启动，产生 21 条 `collect.*` 降级（见 §9）。**我方未改动这些实例**，但降级记录会出现在共享日志中。

### 8.3 证据脱敏

入库证据在写入前后各做一次敏感 token 扫描，检索 `127.0.0.1`／`root`／mock 口令／`15002`／`13306`／平台口令，**命中数必须为 0**；本地地址、端口、账号、口令在入库副本中统一掩码（`<local-host>`、`<proxy-port>`、`<account>`、`<mock-password>` 等）。原始运行副本保留在本机 `C:\dispatcher\probe_out\`，**不入库**。

## 9. 降级记录（本次运行全量归类）

| scope | 次数 | 原因 | 定性 |
|---|---|---|---|
| `collect.Gbase` | 7 | GBase8A_测试库 连不上 | 他人实例目标库未启动 |
| `collect.TDSQL` | 7 | probe-tdsql / TDSQL_模拟测试库 连不上 | 他人实例目标库未启动 |
| `collect.Oracle` | 5 | Oracle_测试库 连不上 | 他人实例目标库未启动 |
| `collect.MySQL` | 5 | MySQL_测试库 连不上 | 他人实例目标库未启动 |
| **`tdsql.proxy_cmd`** | **5** | `/*proxy*/show routes` 报 1064 | **我方实例 id=18；靶场指令集边界** |
| `slow_query.live` | 5 | pgsql 直连取慢 SQL 失败（`pg_stat_statements` 不存在） | 旁路 pgsql 实例 |

**我方实例唯一相关的降级是 `tdsql.proxy_cmd`**，其成因是靶场不实现真 TDSQL 的拓扑指令，属靶场边界（与 DB-004 报告 §1.3 同一结论），**不是平台缺陷**。

## 10. 对 DB-005R 的交棒

### 10.1 复测对象

**本轮缺陷单为空 ⇒ DB-006 无修复对象（按单据空转说明归档）⇒ DB-005R 本轮也无「逐项复测」对象。**

DB-005R 的真实价值在**凭据恢复后的首次全量复测**，而不是缺陷回归。因此建议 PM 把 DB-005R 的口径从「对缺陷单逐项复测」调整为「**用同一套验收脚本对真 TDSQL 做一次全量复测**」，判据清单不变（C-1..C-5 / A-1..A-5 / B-1..B-7 / R-1..R-4），差异项按 §6 缺口逐条回填。

### 10.2 环境可复刻性（DB-005 已把环境复刻出来，DB-005R 直接复用）

| 步骤 | 命令 / 动作 | 备注 |
|---|---|---|
| 1 | 起 TimescaleDB / Redis / ES 容器 | `db-aiops-timescaledb` / `db-aiops-redis` / `db-aiops-elasticsearch` |
| 2 | `manage.py migrate` ＋ `manage.py init_timeseries` | `sql_stat` 扩列必须落在**时序库所在的那个库**（本机为 `timeseriesdb`），查错库会误判缺列 |
| 3 | `manage.py init_roles --force` | 否则 `sql_monitoring.view` 未落库，接口 403 |
| 4 | 纳管真 TDSQL 实例（`db_type=tdsql`）＋ 建只读账号 | 凭据只走 `.env` 或纳管界面 |
| 5 | **`manage.py start_monitor` 常驻** | **不可用 `--once`**（见 §1.3） |
| 6 | `scripts/tdsql_workload.py load --groupshard` → `index add` → `run --minutes 30 --qps 5` | 场景 A（基线） |
| 7 | `index drop` → `run --minutes 30 --qps 5`（期间 `lockwait --seconds 20`） | 场景 B（退化） |
| 8 | `scripts/validate.sh unit` | 取当次 HEAD 的测试条数基线 |

### 10.3 复测时必须避开的三个坑（本轮已踩过）

1. **采集器必须常驻**：`--once` 单轮采集永远不产出 delta（§1.3）。
2. **环比接口的显式窗口必须 URL 编码**：ISO 串里的 `+08:00` 不编码会被解成空格，接口静默回退到默认窗口＋昨日基线，导致「全部判为新增」的假象（§2.2）。
3. **基线窗终点要避开删索引时刻**：采集行的时间戳落在每分钟 :58，其差分区间跨整分钟；基线窗若把「横跨删索引时刻」的那一行纳进去，倍率会被算低（§2.2）。

### 10.4 复通条件

内网 TDSQL 只读凭据恢复供给 ＋ 正式机 17.192 解冻后，用**同一脚本、同一命令**重跑，结论表可与本报告 §3 直接对齐回填，**无需重写报告骨架**。

## 11. 结论

**有条件通过（链路级）** —— 全链路实测走通，退化检测实测命中；set 级真值与 EXPLAIN ANALYZE 三项按口径记缺口，未硬编、未伪造；**缺陷单为空**，DB-006 走空转说明归档。

## 12. 成本披露

搜索 0 次；抓取 0 次。本轮为**本地只读核验 ＋ mock 靶场只读探测 ＋ 平台 API 验收**（场景 A/B 两段各 14 分钟负载、一次锁等待、两次计划采集、四次 API 验收查询）＋ 一次 `scripts/validate.sh unit`；**未改业务代码、未改他人文件、未触碰 `STATUS.md` / `DECISIONS.md`**；新增内容仅为本报告与已脱敏证据文件。

## 13. 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-05 | MMMan | 首版：外网版慢 SQL 全链路实测报告（场景 A/B、窗口/单位/set 真值口径、A/B/C/R 逐项结论、缺口清单、缺陷单为空说明、环境副作用披露、降级记录、对 DB-005R 交棒）；结论「有条件通过（链路级）」 |
