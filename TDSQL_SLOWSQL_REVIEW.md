# TDSQL 慢 SQL 能力专项评估与上线就绪判断

**评估对象**：`master` @ `fbfc814`（V3.0 OAM 前端 + 方言矩阵之后）
**日期**：2026-10-21
**方法**：通读 TDSQL 相关全部代码路径 + 在真实 MySQL 8.0.46 上用 40 万行负载实跑平台代码。
所有标「✅实测」的结论都可用 `docs/review/tdsql/repro_slowsql.py` 重跑。

> **修复状态（2026-10-21 更新，分支 `master-kvohrf`）**：T-01 ~ T-16 已全部按 `TDSQL_SLOWSQL_DESIGN.md`
> 修复，下表"验证"一栏说明每项在哪一层被验证过。凡标"待内网"的，都依赖 TDSQL proxy 的真实行为，
> 只能按 `TDSQL_INTRANET_TEST_GUIDE.md` 在内网确认 —— **在内网验证通过前，本文 §0 的结论仍然成立。**
>
> | 编号 | 修复要点 | 验证 |
> |---|---|---|
> | T-01 / T-02 | 慢 SQL 读取侧重写：`sql_stat` 时间窗为主、直连累计值为辅（显式标注口径）；真实秒数；按总耗时排序 | 真实 MySQL 8 + PG 端到端；dialect 用例 |
> | T-03 / T-12 | 按 set 透传采集 digest（allsets → 逐 set → 降级并标红），按 (set, digest) 差分后跨 set 求和；按活跃窗口取数，不再取累计 Top 100 | 假 proxy 游标单测（每轮 400）；**待内网 V-1/V-2** |
> | T-04 | TDSQL 专有路径补齐单测（采集、透传降级、拓扑、锁、Top SQL 合并） | unit |
> | T-05 / T-06 | 新建议引擎：表 / 别名解析、核对已有索引、改写规则、证据规则、分片键规则 D-01 ~ D-08 | unit 41 例；**待内网 A-1 ~ A-5** |
> | T-07 | 「耗时构成」：锁 / CPU / 其他（CPU 未采集时明示，不再 100% CPU）+ 每次执行明细 + 按 set 分布 + EXPLAIN ANALYZE（只读事务 + 超时，截断检测） | 真实 MySQL 8；**待内网 V-6 ~ V-8** |
> | T-08 | 环比：全量 digest，昨日 / 上周 / 上一窗口自动基线，退化与计划变更关联；日周期 SQL 识别与逐次运行比较 | PG 集成测试；**待内网 R-1 ~ R-4** |
> | T-09 | AI 诊断工具改走同一慢 SQL 入口 | unit |
> | T-10 | 工具目录 TDSQL 描述按 proxy + set 重写 | 人工核对官方文档 |
> | T-11 | 慢日志解析支持 5.6 / 5.7 / 8.0 格式 | 6/6（原 0） |
> | T-13 ~ T-16 | 采集器留痕、`trx_wait_started`、QPS/TPS 改用区间增量、目标库连接一律 readonly + 超时 | unit |
>
> **关于环境的边界**：本环境没有真实 TDSQL（proxy + set）。TDSQL 的标准 MySQL 接口部分
> 用 MySQL 8.0 实测（TDSQL 内核 TXSQL 8.0 与之同源，`performance_schema` 结构一致）；
> **proxy 路由相关的结论（T-03）是用平台真实代码做的仿真**，其前提来自团队自己在真实
> TDSQL 上的实测记录（`checkers/tdsql.py` 文件头："groupshard 模式下 SHOW GLOBAL STATUS
> 等语句由 proxy 路由到某一个后端 set……实测同一实例两轮 slow_queries 分别为 10 和 0"）。

---

## 0. 结论

**不建议以"成熟的自动化智能数据库运维平台"对 TDSQL 上线。**

平台对 TDSQL 能可靠做的是**集群拓扑与主备健康监控**（proxy 网关命令、set 主备、
shardkey 分布 —— 这部分代码针对真实 TDSQL 写过，质量不错）。但**慢 SQL 这条链路对 TDSQL
基本不可用**：慢 SQL 列表接口和慢 SQL 告警**恒为空**，修好这一处后还会**把最快的 SQL
排第一**；分布式实例的 SQL 趋势数据会被 proxy 路由**系统性污染**；"时间花在哪"只能显示
**一根"CPU"柱子**；优化建议**会给出无法执行或无效的 DDL**，且完全不懂分片键。

这与团队自己的 SIT/UAT 报告一致：`V3.0_DIALECT_MATRIX.md` 明确写 TDSQL 实库覆盖为零
（`tdsql-sim` 的镜像是 `mysql:8.0`），500 实例容量、长稳、主备切换、五方签字均未关闭。

---

## 1. 四个问题的直接回答

| 问题 | 回答 | 依据 |
|---|---|---|
| **能准确抓出 TDSQL 的慢 SQL 吗？** | **不能。** 慢 SQL 列表 / 分析 / 文本检索三个接口和 10 分钟一次的慢 SQL 告警，对 TDSQL **永远返回空**，界面显示"当前无慢查询数据"。另一条 `sql_stat` 链路（性能中心用的）在集中式实例上数据是对的，在**分布式实例上被路由污染** | T-01 ✅实测、T-03 仿真 |
| **能给出正确的优化建议吗？** | **部分能，但会出错，且不懂 TDSQL。** 单表等值 + 排序这类简单形态能给出正确的复合索引；但 JOIN 时把别表的列建到本表上（DDL 必然失败）、`LIKE '%x%'` 推 B-tree 索引（无效）、函数包列不提示改写；**没有任何分片键相关建议** —— 而这恰恰是 TDSQL 分布式实例最常见、代价最大的问题 | T-05 ✅实测、T-06 |
| **能对周期性慢 SQL 和问题做环比吗？** | **只有手工的两窗口对比，且口径不对。** 对比只比 ASH 活跃秒的 Top 10，不比执行次数、平均耗时、单次扫描行数；需要人工选时间窗，没有"昨日同期 / 上周同期"自动基线，没有周期性 SQL 识别；Top 10 之外的 SQL 进入 Top 10 会被误标"新出现"。分布式实例上，参与对比的趋势数据本身已被污染 | T-08、T-03 |
| **能看清一条 SQL 的执行时间花在哪吗？** | **不能。** MySQL 8 / TXSQL 的会话状态几乎恒为 `executing`，平台据此一律归为 `on_cpu`，所以每条 SQL 的耗时构成都是"100% CPU"。真正能回答这个问题的数据（`EXPLAIN ANALYZE` 逐算子实际耗时、语句级锁时间 / 临时表落盘 / 扫描行数比、TDSQL proxy 层与后端 set 层耗时）平台一项都没采 | T-07 ✅实测 |

---

## 2. 发现清单

### P0 —— 功能实际不工作

#### T-01 慢 SQL 接口与慢 SQL 告警对 TDSQL / MySQL / GBase 恒为空（✅实测）

- **位置**：`monitor/slow_query_engine.py` `_collect_mysql_slow_queries` 与 `_fallback_collect_from_logs`
- **根因（两层）**：
  1. `DbConnector` 对 MySQL 系返回 `DictCursor`（`db_connector.py:143`），而该函数按下标取列
     `row[0]`、`row[1]`…… → **每次必抛 `KeyError(0)`**
  2. 异常被 `collect_slow_queries_from_db` 的 `except` 吞掉，回落到读 `MonitorLog.detail['slow_queries']`；
     但 TDSQL 采集器写入的 `slow_queries` 是**整数计数器**（`checkers/tdsql.py:288`），
     `isinstance(..., list)` 为假 → 返回 `[]`
- **实测**：库里有一条平均 92ms、单次扫描 40 万行只返回 8 行的全表扫描 SQL，平台返回 0 条，
  分析结论 `['当前无慢查询数据']`
- **影响面**：`/slow-queries`、`/slow-queries/analysis`、`/sql-search` 三个接口；
  `start_monitor` D5 慢查询告警（`start_monitor.py:960`）对这三种方言**从未能触发**。这是 BUG-111
  "数据库在出问题而系统报正常"同一类缺陷

#### T-02 修好 T-01 之后：时间放大 1000 倍，且把最快的 SQL 排第一（✅实测）

- **位置**：同上函数
- **根因**：
  1. `performance_schema` 计时单位是**皮秒**，代码 `SUM_TIMER_WAIT / 1000000000` 得到的是**毫秒**，
     却命名为 `*_sec`。慢 SQL 阈值 `> 1 秒` 实际变成 `> 1 毫秒`
  2. 阈值判断用的是**累计总耗时**而不是单次耗时；排序键 `total_time_sec * exec_count`
     把执行次数**乘了两遍**
- **实测**：

  | SQL | 真实平均耗时 | 平台报告 |
  |---|---|---|
  | 两表 JOIN | 6.50 秒 | 6500.36 "秒" |
  | 全表扫描 + filesort | 0.091 秒 | 90.68 "秒" |
  | 主键点查（600 次） | **0.0001 秒** | 0.089 "秒"，**被排在第一位** |

  对 DBA 的实际效果是：平台会告诉他"最该优化的是那条 0.1 毫秒的主键查询"。
  采集器 `checkers/tdsql.py:506` 用的是正确的 `/1e12`，两处口径不一致

#### T-03 分布式（groupshard）实例的 SQL 趋势被 proxy 路由系统性污染（仿真，前提为团队实测记录）

- **位置**：`start_monitor._snapshot_sqlstat`（写 `sql_stat` 时序表，性能中心、SQL 详情、
  计划采集都读它）；`_compute_counter_deltas`（`slow_queries_delta`，验证回路用它判恢复）
- **根因**：两者都对**累计计数器**做"本轮 − 上轮"差分。groupshard 下 proxy 把
  `performance_schema` / `SHOW GLOBAL STATUS` 路由到**某一个** set，且每轮可能不同 ——
  于是差分变成"set B 的累计值 − set A 的累计值"
- **仿真**（用平台真实函数，4 个 set，每轮全集群真值 400 次执行）：

  | 轮次 | proxy 路由到 | 平台写入的 exec_delta |
  |---|---|---|
  | 0 | set_1 | （无数据） |
  | 1 | set_3 | （无数据，差分为负被"回绕保护"丢弃） |
  | 2 | set_2 | **2600**（真值 400） |
  | 3 | set_2 | 100（真值 400，只看到一个 set） |
  | 4 | set_4 | **5100**（真值 400） |
  | 5 | set_1 | （无数据） |

  趋势图会出现 6~13 倍的假尖刺和空洞。只要 set 之间存在数据倾斜（TDSQL 最典型的问题），
  平均耗时也会随路由跳变。**环比在这种数据上没有意义**
- **集中式（noshard）实例**：只有一个 set，不受此影响；但开了读写分离（`rw_split`）时，
  查询有可能被路由到备机 —— 需要在真实环境确认

#### T-04 TDSQL 专有代码路径零测试覆盖

- `docker-compose.dev.yml` 的 `tdsql-sim` 是 `mysql:8.0`，没有 proxy，`/*proxy*/` 网关命令、
  groupshard 分支、shardkey 解析**从未在 CI 或本地测试里执行过**
- 全仓 `monitor/tests*.py` 中 `groupshard` / `/*proxy*/` / `show routes` 零命中
- 团队报告已自承（`V3.0_DIALECT_MATRIX.md`），此处列入是因为它决定了 T-03 这类问题
  **只能在生产上被发现**

### P1 —— 结果会误导

#### T-05 索引建议会给出无法执行或无效的 DDL（✅实测）

`SqlDetailView` 调用 `IndexAdvisor` 的结果：

| SQL 形态 | 平台建议 | 问题 |
|---|---|---|
| `orders o JOIN users u ... WHERE u.city = ? AND o.status = ?` | `orders(city, status)` | `city` 是 `users` 的列，**DDL 必然报 Unknown column** |
| `WHERE note LIKE '%abc%'` | `orders(note)` | B-tree 索引对前导通配**无效**，只会增加写放大 |
| `WHERE DATE(created_at) = ?` | （无建议、无提示） | 漏掉最常见的改写建议：改成 `created_at >= ? AND created_at < ?` |
| `WHERE user_id = ? ORDER BY created_at DESC LIMIT 10` | `(user_id)`、`(created_at)`、`(user_id, created_at)` | 第三条正确（实测 100ms → 0.2ms），但前两条是冗余噪声 |

另外：`selectivity` 恒为 1.0（不读真实统计信息）、不核对表上已有索引、
`slow_query_engine._identify_patterns` 的"全表扫描"关键字条件是 SQL 里含 `FROM `
（每条 SELECT 都满足），实际起作用的只有"扫描行 / 返回行 > 100"一条；"排序"模式只要含
`ORDER BY` 就命中；输出的建议是不针对具体 SQL 的固定模板文案（"使用 EXPLAIN 分析……"）。

#### T-06 没有任何分片键相关的诊断与建议

TDSQL 分布式实例里，一条 SQL 慢的头号原因通常不是缺索引，而是：

- `WHERE` 不带分片键 → proxy **广播到全部 set** 再归并
- 两张分片表 JOIN 不在分片键上 → 跨 set 关联
- `ORDER BY ... LIMIT` / 聚合在 proxy 层归并 → 每个 set 都要返回 N 行
- 分片键取值倾斜 → 某个 set 成为热点

平台已经采集了 `show shardkey`（`tdsql_shard_tables`），但慢 SQL、索引建议、RCA
**都没有使用它**。`index_advisor.py`、`slow_query_engine.py`、`plan_capture.py` 中 `shard` 零命中。

#### T-07 "时间花在哪"对 MySQL 8 / TXSQL 恒为 100% CPU（✅实测）

- **位置**：`sentinel._ash_mysql_family` 用 `processlist.state` 当等待事件；
  `detectors/wait_class._classify_mysql` 把 `executing` 归为 `on_cpu`
- **实测**：一条运行中、主要耗在全表扫描上的查询，平台采样为 `state='executing' → on_cpu`。
  MySQL 8.0.18 之后几乎所有运行中的语句都是 `executing`，所以 SQL 详情页的耗时构成是一根 CPU 柱子
- 对照（同一条全表扫描 SQL，MySQL 8 原生能给出的）：

  ```
  EXPLAIN ANALYZE:
  -> Limit: 10 row(s)                         (actual time=100..100 rows=8)
     -> Sort: created_at DESC                 (actual time=100..100 rows=8)
        -> Filter: (user_id = 4242)           (actual time=15.8..100 rows=8)
           -> Table scan on orders            (actual time=0.868..83.9 rows=400000)
  ```

  一眼可见：100ms 里 84ms 花在扫 40 万行上，只为返回 8 行。加上 `(user_id, created_at)`
  后变为 `Index lookup ... (reverse) actual time=0.154..0.198`，排序消失。
  **平台采集计划用的是 `EXPLAIN FORMAT=JSON`（估算值），拿不到这些实际耗时**

#### T-08 环比只比 ASH 活跃秒 Top 10

- **位置**：`api_views_perf.CompareView` / `_window_block`
- 只对比 `session_ash_1m` 的活跃秒（采样估计），**不对比 `sql_stat` 的执行次数、平均耗时、
  每次扫描行数**；"这条 SQL 今天跑了多少次、每次多慢、比上周慢了几倍"回答不了
- 两个窗口各取 Top 10 再求并集：A 窗排第 11 的 SQL 在 B 窗冲到第 1，会被标成 `new: true`
- 时间窗必须手工传 `a_from/a_to/b_from/b_to`，没有"昨日同期 / 上周同期"自动基线，
  没有周期性 SQL（如每日批处理）的自动识别与逐日比较

#### T-09 AI 诊断看不到任何慢 SQL（✅实测）

- **位置**：`monitor/llm/tools.py` `_t_slow_queries`
- 读取的 `SlowQueryLog` 模型**不存在**，`ImportError` 被吞掉，恒返回
  `{'slow_queries': [], 'note': '慢查询表不可用'}`。LLM 诊断链里的"近期慢查询 Top"工具从未有过数据

#### T-10 V3.0 工具目录里的 TDSQL 描述混入了别的产品

- **位置**：`monitor/oam/tool_catalog.py`
- `meta.resource_policy` 的 tdsql 描述写"TDSQL/DWS 的 `PGXC_WLM_SESSION_INFO`……"——
  这是华为 DWS / GaussDB 的视图，**TDSQL MySQL 版没有**；多处用"协调节点 / 数据节点 / shard_id"，
  这是 PG-XC 系术语，TDSQL MySQL 版是"proxy 网关 + set"
- `db.top_sql` / `db.slow_query` 描述写"需按分片分别取后归并"，**但没有任何代码实现归并**
- 这些描述会被策略模型当作工具语义读进上下文（论文 §5.1.2 的核心机制），写错就是在诱导幻觉

### P2 —— 质量与一致性

| 编号 | 问题 | 位置 |
|---|---|---|
| T-11 | 慢日志解析器的正则是 MySQL 5.6 格式（`# Time: 260101 12:00:00`），MySQL 8 的 ISO 时间与 `User@Host` 格式都匹配不上，实测 2 条解析出 0 条；且**无任何调用者**，平台实际不读慢日志 | `slow_query_engine.MySQLSlowQueryParser` |
| T-12 | `sql_stat` 每轮只取**累计**耗时 Top 100：新近变慢但历史累计小的 SQL 进不了 Top 100，直到累计值爬上来；SQL 跌出又回到 Top 100 时，增量跨越多个周期却记在一个周期上 → 假尖刺 | `start_monitor._snapshot_sqlstat` |
| T-13 | TDSQL 采集器 12 处 `except Exception: pass / return []`，违反 AGENTS.md「捕获异常后不得完全静默」红线。`performance_schema` 未开时，Top SQL 显示"空"而不是"采集不可用"——**无数据和无问题在界面上无法区分**（MySQL 采集器 19 处，同类问题） | `checkers/tdsql.py` |
| T-14 | 采集器 `_locks` 的等待秒用 `trx_started`（事务年龄）。哨兵侧 BUG-123 已修成 `trx_wait_started`，采集器这份没修 | `checkers/tdsql.py:484` |
| T-15 | `qps` / `tps` 是"自启动以来累计 / uptime"，实例跑了一个月后基本是一条直线，反映不出当前负载 | `checkers/tdsql.py:135` |
| T-16 | 取目标库连接未传 `readonly=True` 与 `statement_timeout_ms`，违反 AGENTS.md 已知陷阱 BUG-109/110 | `slow_query_engine.collect_slow_queries_from_db`；`start_monitor._collect_perf_extras`（`sql_stat` 采集与每小时计划采集共用这条连接） |

---

## 3. 改造设计（TDSQL 慢 SQL 链路）

目标是回答清楚那四个问题。按"采集 → 分解 → 建议 → 环比"四层，每层都先保证数据对。

### 3.1 采集：按 set 采，再归并

**原则：凡是累计计数器，必须按「(实例, set, digest)」做差分，归并只发生在差分之后。**

| 方案 | 做法 | 前提 |
|---|---|---|
| A. 透传到指定 set（首选） | 用 `show routes` 拿到 set 列表，对每个 set 发 `/*sets:set_xxx*/ SELECT ... FROM performance_schema.events_statements_summary_by_digest` | 需在贵方 proxy 版本上确认透传语法可用（proxy 对不支持的网关命令会报 694，见采集器文件头） |
| B. 直连各 set 主节点 | `show routes` 给出了每个 set 的 `master_ip:port`，监控账号直连 | 网络可达 + 各 set 上开通监控账号 |
| C. 管控面 API | TDSQL 管控平台 / 云 API 的慢日志与 SQL 统计接口 | 有管控面权限；可同时拿到 proxy 侧数据 |

采集字段从现在的 6 个扩到能回答"时间花在哪"的一组（MySQL 8.0.28+/TXSQL 8.0 均有，已在 8.0.46 上核对）：
`DIGEST`、`QUERY_SAMPLE_TEXT`（带字面量的样例，EXPLAIN 用）、`COUNT_STAR`、`SUM_TIMER_WAIT`、
`SUM_LOCK_TIME`、`SUM_CPU_TIME`、`SUM_ROWS_EXAMINED`、`SUM_ROWS_SENT`、
`SUM_CREATED_TMP_DISK_TABLES`、`SUM_SORT_MERGE_PASSES`、`SUM_SELECT_SCAN`、`SUM_SELECT_FULL_JOIN`、
`SUM_NO_INDEX_USED`、`SUM_ERRORS`、`FIRST_SEEN`、`LAST_SEEN`。

归并后每个 digest 同时保留**全集群合计**与**各 set 分布**（最大 set / 中位 set 之比即倾斜度）。

Top N 的选取改为按**本周期增量**排序，而不是按累计值（修 T-12）。

### 3.2 "时间花在哪"：三层分解

| 层 | 回答什么 | 数据来源 |
|---|---|---|
| **proxy 层** | 端到端耗时里，多少在 proxy（路由、归并、排序），多少在后端 set | proxy 慢日志 / 管控面（方案 C）；`/*proxy*/show processlist` 的 `Time(ms)` 与 `backend_info` 可做实时近似 |
| **语句层** | 后端耗时里：锁等了多久、CPU 多少、扫了多少行换回多少行、有没有落盘临时表、有没有多轮归并排序 | 3.1 扩充后的 digest 字段，按周期增量算"每次执行均值" |
| **算子层** | 具体是哪个步骤慢（全表扫描？回表？排序？JOIN？） | `EXPLAIN ANALYZE` 逐算子实际耗时（见 T-07 对照） |

`EXPLAIN ANALYZE` **会真实执行语句**，必须设硬约束：只对 `SELECT`；只在备机或透传到备节点执行；
走 `DbConnector.get_connection(cfg, statement_timeout_ms=..., readonly=True)`；默认关闭，按实例开启；
手工触发要走审批（沿用 `SessionKillView` 的工单链路）。

会话采样（ASH）的等待分类，对 MySQL 系改用 `performance_schema.events_waits_current`
（IO / 锁 / 互斥 / 网络）而不是 `processlist.state`，开销可控时再启用。

### 3.3 建议：先修错，再补 TDSQL 专有规则

1. **先修 T-05 的错误形态**：JOIN 列按别名归属到正确的表；`LIKE '%x%'` 不建议 B-tree，
   改提示全文索引或改写；函数包列提示改写为区间条件；去掉被复合索引覆盖的冗余单列建议；
   建议前读 `information_schema.statistics` 核对已有索引、读真实基数代替恒为 1.0 的 selectivity
2. **补 TDSQL 专有规则**（输入：SQL 解析结果 + `tdsql_shard_tables` + 各 set 分布）：
   - `WHERE` 不含分片键 → "广播到全部 N 个 set"，给出带分片键的改写建议
   - 两张分片表的 JOIN 键不是双方分片键 → "跨 set 关联"
   - 分片表与广播表混用、`ORDER BY ... LIMIT` 深分页 → 提示 proxy 归并代价
   - 各 set 同一 digest 的执行次数或耗时显著不均 → 分片键倾斜
3. **每条建议附可验证的预期**：给出 DDL 的同时给出"预期去掉哪个算子"，并在备机上用
   `EXPLAIN ANALYZE` 做前后对照后再推荐（T-07 对照里 100ms → 0.2ms 就是这种证据）

### 3.4 环比：自动基线 + 周期识别

- 对比数据源改为 `sql_stat` 的周期增量（3.1 修好之后），指标包括执行次数、平均耗时、
  每次扫描行数、每次锁时间，而不只是 ASH 活跃秒
- 自动基线：每个 digest 默认对比**昨日同时段**与**上周同时段**，无需人工选窗
- 周期 SQL 识别：对每个 digest 的执行时间分布做日 / 周周期检测，识别出批处理类 SQL 后，
  按"每次运行"逐日比较（例："每日 02:00 的对账 SQL，今天耗时是过去 7 天中位数的 3.2 倍"）
- 分类输出：**新增**（基线期从未出现，按全量判断而非 Top 10）/ **消失** / **退化**（超过阈值）/ **改善**；
  退化与计划变更（`plan_hash` 变化，已有）、DDL 变更（`ChangeEvent`，已有）自动关联

---

## 4. 上线判断与建议顺序

### 4.1 总体判断

| 维度 | 状态 |
|---|---|
| TDSQL 拓扑与主备健康 | **可用**（针对真实 TDSQL 写过，但无自动化测试保护） |
| TDSQL 慢 SQL 发现 | **不可用**（T-01、T-02、T-03） |
| TDSQL 优化建议 | **不可信**（T-05、T-06） |
| 周期性环比 | **雏形**（T-08） |
| SQL 耗时分解 | **不可用**（T-07） |
| AI 诊断对 TDSQL 的支撑 | **不可用**（T-09、T-10） |
| 团队自己的上线前置项 | 500 实例容量、长稳、主备切换、五方签字、dm/gbase/tdsql 实库覆盖 **均未关闭**（`V3.0_SIT_UAT_TEST_REPORT.md` §7.3） |

建议的上线口径：**MySQL / PostgreSQL / Oracle 可以按"监控 + 辅助诊断"试点；
TDSQL 只上拓扑与健康监控，慢 SQL 与优化建议在完成下列 P0 / P1 之前不对 DBA 开放**。

### 4.2 建议顺序

| 阶段 | 内容 | 验收 |
|---|---|---|
| **第 0 步：取得真实 TDSQL 测试环境** | 至少一个集中式 + 一个分布式（≥2 set）实例，接入 CI 的 `test-dialect` 方言任务 | 没有它，T-03 这类问题只能在生产上发现。这一步不做，后面的修复都无法验收 |
| P0（约 1 周） | 修 T-01、T-02、T-09、T-16；把 `repro_slowsql.py` 的 api / units / llmtool 改写成正向回归用例 | 复现脚本对应条目反转为通过 |
| P0（约 1~2 周） | 按 3.1 方案 A 或 B 实现按 set 采集与差分后归并（修 T-03、T-12） | 在真实分布式实例上：各 set 增量之和 = 全集群真实执行次数 |
| P1（约 2 周） | 3.3 第 1、2 点（修错 + 分片键规则）；修 T-10 的工具描述 | 用一组带标注的 TDSQL 慢 SQL 样例评测建议正确率，作为 V3.0 基准的一部分 |
| P1（约 2 周） | 3.2 语句层 + 算子层分解；3.4 自动基线与周期识别 | SQL 详情页能给出"锁 / 扫描 / 排序 / 落盘 / proxy"的分项耗时 |
| P2 | T-11、T-13、T-14、T-15；proxy 层分解（依赖方案 C） | — |

---

## 附录：复现

```bash
# 1. 准备一个 MySQL 8.0 / TXSQL 8.0 测试实例，执行负载脚本
mysql < docs/review/tdsql/workload.sql

# 2. 凭据走环境变量（遵守凭据通道约定），账号只需 SELECT + PROCESS
export PROBE_MYSQL_HOST=... PROBE_MYSQL_PORT=3306 PROBE_MYSQL_USER=... PROBE_MYSQL_PASSWORD=...

# 3. 跑全部复现（T-01 / T-02 / T-05 / T-03 / T-09）
.venv/bin/python docs/review/tdsql/repro_slowsql.py all

# T-11：传入真实慢日志路径
.venv/bin/python docs/review/tdsql/repro_slowsql.py slowlog /path/to/slow.log

# T-07：先在另一个会话里跑一条数秒的查询，再执行
.venv/bin/python docs/review/tdsql/repro_slowsql.py ash
```

本次实测输出（MySQL 8.0.46，40 万行）：

```
[T-01] collect_slow_queries 抛 KeyError(0)：DictCursor 下按下标取列
[T-01] API 入口返回 0 条；分析结论 = ['当前无慢查询数据']
[T-02] 引擎 avg_time_sec= 6500.3593  真值 avg_sec=  6.5004  SELECT COUNT ( * ) FROM `orders` `a` JOIN ...
[T-02] 引擎 avg_time_sec=   90.6813  真值 avg_sec=  0.0907  SELECT `id` , `amount` FROM `orders` WHERE `user_i...
[T-02] 引擎 avg_time_sec=    0.0890  真值 avg_sec=  0.0001  SELECT `id` FROM `orders` WHERE `id` = ?
[T-02] 引擎排第一的是: n=600 SELECT `id` FROM `orders` WHERE `id` = ?
[T-05] JOIN 列归错表: ['orders(city, status)', 'orders(user_id, id)']
[T-05] 前导通配 LIKE: ['orders(note)', 'orders(note, id)']
[T-05] 函数包列: （无建议、无提示）
[T-03] 第2轮 路由到 set_2: exec_delta=2600   （真值 400）
[T-03] 第4轮 路由到 set_4: exec_delta=5100   （真值 400）
[T-09] {'slow_queries': [], 'note': '慢查询表不可用'}
[T-11] 日志条目 2，平台解析出 0
[T-07] state='executing' -> wait_class='on_cpu'
```
