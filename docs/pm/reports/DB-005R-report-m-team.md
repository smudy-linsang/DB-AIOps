# DB-005R 交付报告：外网先行重测（G-12 部分 / G-13 / S-1）

- 任务单：`docs/pm/orders/DB-005R-005复测回归确认.md`
- 口径补充单：`docs/pm/orders/DB-005R-补充-外网先行内网延后.md`（2026-11-06 muse 下达，执行：MMMan）
- 统筹单：`docs/pm/orders/DB-015-DB-004-005-005R改派MMMan外网测试.md`
- 接单回执：`docs/pm/reports/DB-005R-receipt-m-team.md`（2026-11-06 10:00）
- 执行：MMMan（M-team team lead）
- 交付时间：2026-11-06 16:05（回执 ETA 2026-11-06 18:00，**提前约 2 小时交付**）
- 证据目录：`docs/pm/reports/evidence/DB-005R-m-team/`（**17 份**：9 份实测产物 + 6 份可复现脚本 + `README-evidence.md` 清单 + `manifest.json`）

## 结论

**有条件通过（外网段）** —— 本轮把 G-12 的「平台排序逻辑」半边、G-13、S-1 三项**在真实 MySQL 8.0 上实测走通**：人为删索引制造的退化被平台**实测命中**（`slow_user_sort` 判定「退化」，倍率 **36.69**，impact_ms 7107），计划变更有直接证据（`Index lookup` → `Sort + Table scan`，计划哈希 `b9e5ae404a8d…` → `b3b33cfb6ef8…`），锁等待（LOCK_WAIT，lock_ms 18000）、扫描效率（scan_efficiency 44620.5）、优化建议（I-01/I-02 ↔ I-03 随索引存在与否正确切换）、慢 SQL 告警（18001ms）均实测命中。**G-8 / G-9 仍为缺口**（需真 TDSQL 多 set）、**G-10 门禁维持关闭**、**G-11 待 24h 采集时窗成熟后回填**；**缺陷单来源仍为空**，不硬修、不代提。

---

## 1. 本轮范围与口径

### 1.1 两段执行（按补充单）

| 段 | 内容 | 本轮状态 |
|---|---|---|
| **第一段（外网）** | G-12 部分、G-13、S-1 ＋ G-8/G-9 记缺口、G-10 不涉及、G-11 记「待时窗成熟」 | **本报告 = 第一段交付** |
| **第二段（内网）** | 全量复测 | 按补充单延后至新版本上线、环境统一就绪后，由用户转达 Lingma；**不在本次范围** |

原「凭据恢复后全量复测」口径已被补充单作废，本报告不再沿用。

### 1.2 本轮跑了什么 / 没跑什么

**跑了**：G-12 的「平台排序逻辑」半边（可独立于 set 拓扑验证）、G-13（全量）、S-1（链路级回归）。
**没跑**：G-8 / G-9（无真 TDSQL 拓扑，不硬编）、G-10（门禁按设计关闭，本段不涉及）、G-11（时窗未成熟，明日回填）。
**未改业务代码、未改他人文件、未触碰 `STATUS.md` / `DECISIONS.md`、G-10 开关未动、正式机 17.192 未动、凭据不落盘不入库。**

---

## 2. 执行环境

### 2.1 平台与采集器（采集连续性 = G-11 的前提）

| 组件 | 状态 | 核实时间 |
|---|---|---|
| 仓库 `C:\DB-AIOps`（main） | HEAD `daa264b`，与 origin 一致（0/0），工作区干净 | 2026-11-06 16:04 |
| Web `manage.py runserver 127.0.0.1:8009 --noreload` | PID 54884，**起于 2026-11-05 20:29:12，全程未重启** | 2026-11-06 16:04 |
| 采集器 `manage.py start_monitor` | PID 48048，**起于 2026-11-05 20:31:56，全程未重启** | 2026-11-06 16:04 |

> 采集器**自 11-05 20:31:56 起连续运行未中断**，这是 G-11（昨日同时段环比）可在 11-07 回填的前提。本轮**没有重启采集器**——`monitor/sqlstat_collect.py` 的 `DeltaTracker` 是进程内状态，重启会打断 24h 采集连续性（G-11 直接不可测）。

### 2.2 S-1 目标库与纳管实例

| 项 | 值 |
|---|---|
| 目标库 | MySQL **8.0.45**（容器 `tdsql-mysql-test`），**直连 13306**，不经 mock proxy |
| 业务库 | `aiops_probe_shop`（表 `orders`，150000 行） |
| 平台实例 | 新增 **config id=19**「M-team probe-mysql-8.0」，`db_type=mysql`，`127.0.0.1:13306`，`username=root`，`collect_interval_sec=60`，`is_active=True` |
| 既有实例 | **id=18 及他人 id=1/2/3/9/10/11/15/16 一律未动** |

### 2.3 ★「真实 MySQL 8.0」的准确含义（防过度解读）

回执把 S-1 写成「在 `db_type=mysql` 的**真实 MySQL 8.0 实例**上」。为免后续被读成「另一套独立环境」，这里把边界写死：

- **「真实」指目标库是真 MySQL 8.0.45**：原生协议、原生 `performance_schema`、原生 `EXPLAIN FORMAT=TREE/JSON`，**不走 mock proxy 的指令集**。
- **但它与 mock 靶场 `tdsql-mysql-test` 是同一个容器实例**——DB-005 经 proxy 15002 打到它，S-1 直连 13306 打到它。**两者的差别是「接入路径 + 平台侧 `db_type`」，不是「另一台机器」**。
- 因此 S-1 真正新增的覆盖是三点：
  1. 走 `db_type=mysql` 的采集/解析/建议代码路径（DB-005 走的是 `db_type=tdsql`）；
  2. **直连链路**，把 proxy 从因果链里彻底摘掉；
  3. **独立实例 id=19**，`sql_stat` 行与 id=18 隔离，环比基线窗不被 DB-005 的历史数据污染。
- **不成立的说法**：S-1 不是「在另一套独立环境上交叉验证了 DB-005」。跨环境的交叉验证要等真 TDSQL。

---

## 3. S-1 编排与实测结果

### 3.1 P0~P7 阶段日志（`db005r_s1_log.json`）

| 阶段 | 时刻 | 结果 |
|---|---|---|
| P0 建实例（mysql / 真实 MySQL 8.0）+ 账号口令轮换 | 15:25:40 | ok（CID=19） |
| P1a 索引补回 `idx_user_created` | 15:25:43 | rc=0 |
| P1b **场景 A** workload（12min，**有索引**） | 15:25:43 → 15:37:44 | rc=0 |
| P2 计划采集 **before**（索引在位） | 15:37:45 | rc=0 |
| P3a **索引删除** | 15:37:45 | rc=0 |
| P3b 锁等待注入（场景 B 窗内） | 15:40:05 | rc=0（A 持锁 20s，B 等锁 20.0s） |
| P3c **场景 B** workload（12min，**索引已删**） | 15:37:45 → 15:49:45 | rc=0 |
| P4 计划采集 **after**（索引已删） | 15:49:47 | rc=0 |
| P5 API 链路级回归（22 项） | 15:49:48 | rc=0 |
| P6 A-2 专项 | 15:49:49 | rc=0（digest `6171e5b3b12f5cc4016eacc2545f3359`） |
| P7 G-13 探针 | 15:49:51 | rc=0 |

全程 15:25:39 → 15:49:51，**P0~P7 全 ok=True**。

### 3.2 窗口口径（本轮最容易误判的一点）

| 用途 | 窗口 | 是否跨场景 |
|---|---|---|
| 慢 SQL 列表（C-1/C-2/C-3） | 相对窗 `time=30m`，取样时刻 **15:49:48** ⇒ 实际覆盖 ≈15:19:48–15:49:48 | **是，横跨 A/B** |
| 环比退化（R-1/R-2/R-3） | 显式 `from=15:37:45&to=15:49:45`，基线 `base_from=15:25:43&base_to=15:37:44` | 否，两窗各自干净 |

> 慢列表的 30m 相对窗**包含场景 A 的后 8 分钟**，所以它的 `avg_ms` 天然低于 R-1 显式窗（场景 B 全窗）的 `avg_ms`（如 `slow_user_sort`：9.545 vs 17.502）。**两个数不等是口径差异，不是数据打架。**
> R-1/R-2/R-3 全部走显式窗（`from/to` 显式传参并 `urlencode`），不受此影响。

### 3.3 验收清单结论表（22 项）

| # | 判据 | 判定 | 实测要点 |
|---|---|---|---|
| **C-1** | 慢 SQL 列表非空 + basis=window | **PASS** | count=27 basis=window warning=None untrusted=[] |
| **C-2** | 排序：慢查询排前、点查不进前列 | **PASS** | 列表下标（0 基）：lockwait_update=0（第 1）、agg_no_shardkey=1（第 2）、**slow_user_sort=2（第 3）**、range_no_key=3（第 4）、**pk_lookup=7（第 8）**；脚本判据 `sus_pos<3 且 pk_pos>3` ⇒ PASS。Top：1) lockwait UPDATE avg_ms=18001.0 exec=1；2) agg avg_ms=36.57 exec=284；3) slow_user_sort avg_ms=9.545 exec=781；4) range avg_ms=14.18 exec=266 |
| **C-3** | 单位：avg_ms 与客户端平均耗时同数量级 | **OBSERVED** | 平台 avg_ms：slow_user_sort=9.545、pk_lookup=0.353、agg=36.57、range=14.18；客户端（**S-1 实测日志**）：场景A 2.7/2.5/43.5/19.0 ms，场景B 23.1/2.6/41.8/17.2 ms（平台=库内 `SUM_TIMER_WAIT`，客户端=驱动端观测；**只比同量级与同趋势，不要求相等**） |
| **A-1** | 优化建议：I-01 复合索引 DDL 可执行、不误报 D-01 | **PASS** | advice_codes=['I-01','I-02']；DDL `ALTER TABLE \`orders\` ADD INDEX \`idx_user_id_created_at\` (\`user_id\`, \`created_at\`);` |
| **A-2** | 索引存在时不重复建议同一索引 | **PASS**（专项脚本） | 索引缺失→I-01/I-02；`index add`（exit=0）→**I-03**；`index drop` 复原→I-01/I-02。verdict 三项全 true |
| **A-5** | 表名/列名真实存在、无系统表建议 | **OBSERVED** | 建议列 (user_id, created_at) 均真实存在，无系统表 |
| **B-1** | 耗时构成三段：CPU 未采集时不得显示 100% CPU | **PASS** | `cpu_known=false`、`cpu_ms=null`，并给出开启 `events_statements_cpu` consumer 的方法与 my.cnf 持久化提示 |
| **B-2** | 每次执行明细含扫描效率/全表扫描结论 | **PASS** | finding_codes=['SCAN_INEFFICIENT','FULL_SCAN']；per_exec rows_examined=139546.4 / rows_sent=3.1 / sort_rows=2.9；ratios scan_efficiency=44620.5、full_scan_share=0.93、no_index_share=0.93 |
| **B-3** | 锁等待：平台汇总出 LOCK_WAIT | **PASS** | finding_codes=['LOCK_WAIT']；lock_ms=18000.0，lock_share=1.0 |
| **R-1** | 退化：slow_user_sort 状态=退化且倍数>1.5 | **PASS** | status=**regressed**，avg_ratio=**36.69**，exec_ratio=1.14，impact_ms=7107；当前窗 416 次 / 17.502 ms / 每次扫描 139546.4 行；基线窗 365 次 / 0.477 ms / 2.9 行 |
| **R-2** | 退化：点查 pk_lookup 未退化 | **PASS** | pk_lookup 不在 items（接口对 stable 不返回），counts.stable=23；平台口径 0.355 ms vs 0.35 ms ⇒ 倍率 **1.014**（<1.5 即未判退化） |
| **R-3** | 新增语义：基线窗未出现的 SQL 判「新增」 | **PASS** | lockwait UPDATE status=new baseline=None；new 项 3 条：['d92061db99b8','0b230517d4f6','4d4f0bd83b61'] |
| **B-7** | EXPLAIN ANALYZE 守卫 | **门禁未放行** | HTTP 403 `{"code":"DISABLED","message":"EXPLAIN ANALYZE 未开启（配置项 PERF_EXPLAIN_ANALYZE_ENABLED）"}` |
| **C-5** | 慢 SQL 告警 | **PASS** | slow_query 告警 1 条 @2026-11-06 15:46:59 (+08)：平均耗时 **18001.0ms**（执行 1 次），摘要为注入的 UPDATE |
| **C-4p** | 单 set 口径等价对账 | **OBSERVED** | 平台 exec_count vs 手工 `COUNT_STAR` 同量级（agg 284/626、slow_user_sort 781/1843、range 266/579、pk_lookup 5530/12525、lockwait UPDATE 1/4 —— 平台为**窗口增量**、手工为 p_s **累计**，前者必然更小；量级关系一致，无异常放大） |
| **C-4 / A-3 / A-4 / B-4 / B-5 / B-6 / R-4** | 外网不可跑 | **记缺口** | 分别需 set 级真值 / groupshard 拓扑 / EXPLAIN ANALYZE 门禁 / 满 24h 时窗 |

**合计：PASS 10、OBSERVED 4、门禁未放行 1、记缺口 7（含 B-3b 见 §3.8 实为 PASS）。**

接口返回的判定阈值：`avg_ratio=1.5`、`min_delta_ms=5.0`；分类计数 `{"regressed":1,"new":3,"improved":0,"gone":0,"stable":23}`。

### 3.4 退化检测（R-1/R-2/R-3）：两口径交叉核对

| SQL | 平台口径倍率 | 客户端口径（场景 A → 场景 B） | 判读 |
|---|---|---|---|
| `slow_user_sort` | **36.69**（0.477ms → 17.502ms） | **2.7ms → 23.1ms（8.6 倍）** | **两口径一致指向退化**；机制清晰：每次扫描行数 2.9 → 139546.4，计划由 `Index lookup` 变为 `Sort + Table scan` |
| `agg_no_shardkey` | 未判退化（stable） | 43.5ms → 41.8ms（持平） | **一致**：该语句本就全表聚合、不依赖被删的复合索引，未退化，符合预期 |
| `range_no_key` | 未判退化（stable） | 19.0ms → 17.2ms（持平） | **一致**：虽计划由 `Index scan` 变为 `Table scan`，但该语句不带 `user_id` 等值条件，删索引不改变其代价，未退化符合预期 |
| `pk_lookup` | 未判退化（倍率 1.014） | 2.5ms → 2.6ms（持平） | **一致**：主键点查不受影响 |
| `lockwait_update` | new（impact_ms 18001） | 注入项 | 符合预期 |

> 与 DB-005 的差异要说清：DB-005 里 `agg_no_shardkey`（1.51）与 `range_no_key`（2.77）被平台判为 regressed，但客户端侧并未退化，DB-005 报告已如实标注「口径不一致、不作归因」。**S-1 在集中式 MySQL 上这两条不再被判退化**，与客户端口径吻合 —— 这是 S-1 相对 DB-005 的一处**正向收敛**（集中式环境下口径差异消失），仍不构成对 TDSQL 多 set 场景的断言。

### 3.5 计划变更的直接证据（SqlPlan 表核验）

`slow_user_sort`（digest `6171e5b3b12f5cc4016eacc2545f3359`）在 `SqlPlan` 表中：

| config | plan_hash | cost_total | is_current | captured_at | 计划文本要点 |
|---|---|---|---|---|---|
| **19（S-1）** | `b9e5ae404a8d67f3afed0b88b46e2c7f` | 0.35 | False | 2026-11-06 07:37:45 UTC | `Limit: 10` → **`Index lookup on orders using idx_user_created`** |
| **19（S-1）** | `b3b33cfb6ef878231ebb1b189dd83ac1` | 15257.8 | **True** | 2026-11-06 07:49:46 UTC | `Limit: 10` → **`Sort: orders.created_at DESC`** → `Filter` → **`Table scan on orders`** |
| 18（DB-005） | `b9e5ae404a8d67f3afed0b88b46e2c7f` | 1.4 | False | 2026-11-05 12:44:21 UTC | `Index lookup using idx_user_created` |
| 18（DB-005） | `72597b207ace250608a9fcc53ca05ced` | 15257.8 | True | 2026-11-05 12:50:03 UTC | `Sort` + `Table scan` |

**这是计划变更最硬的证据**：同一个 digest、**同一个实例（cid=19）**上，前后两次计划哈希不同、代价从 0.35 涨到 15257.8、访问方式由索引查找变为全表扫描。

再与 DB-005（cid=18）对照：**退化前**两套实例的计划哈希**完全一致**（都是 `b9e5ae404a8d…`，因为两轮 workload 命中了同一个 `user_id=31794` 字面量）；**退化后**哈希不同（`72597b20…` vs `b3b33cfb6ef8…`），差异来自计划文本里的 `user_id` 字面量不同（12236 vs 42577）——**计划形态（`Sort` + `Table scan`）完全一致**。

**如实记录一处差异**：本轮环比接口返回的 `plan_changed_at` 为 `None`（DB-005 同项返回了 `2026-11-05T12:50:03`）。计划变更本身由上表直接取证成立；接口该字段本轮为空的原因未定位，**如实列出、不作过度解读、也不写成平台缺陷**。

### 3.6 耗时构成（B-1/B-2/B-3）

- **B-1**：CPU 未采集时**没有**显示「100% CPU」——`cpu_known=false`、`cpu_ms=null`，并给出可执行的补采指引（`UPDATE performance_schema.setup_consumers SET ENABLED='YES' WHERE NAME='events_statements_cpu'`，以及 my.cnf 持久化项）。
- **B-2**：`SCAN_INEFFICIENT` + `FULL_SCAN` 双结论成立，每次执行扫描 139546.4 行却只返回 3.1 行，扫描效率 44620.5，full_scan_share 0.93。
- **B-3**：平台汇总出 `LOCK_WAIT`，lock_ms 18000.0、lock_share 1.0（该语句平均耗时 18001ms 中 18000ms 是锁等待）。

### 3.7 优化建议（A-1/A-2/A-5）

A-2 专项脚本（`db005r_s1_a2.json`）三段状态机实测：

| 阶段 | advice codes | 含义 |
|---|---|---|
| 索引已删（场景 B 末态） | I-01、I-02 | 复合索引 + 可选覆盖索引 |
| `index add`（exit=0） | **I-03** | 「表 orders 已有可用索引」，**不再建议建同一索引** |
| `index drop` 复原 | I-01、I-02 | 复原后回到缺索引建议 |

**verdict 三项全 true**：`A-1_索引缺失时给出I-01` / `A-2_索引存在时不重复建议同一索引` / `复原后回到I-01`。这与 DB-005 当年记录的「I-01 → I-03」一致。

> **★方法学订正（如实披露）**：A-2 首跑曾给出「verdict 为 false」的假阴性。根因在**我方验收脚本**——脚本调 workload 时用 `os.environ` 读 `TDSQL_WL_*`，而 API 子进程的环境变量里没有这些键，导致 `index add`/`index drop` 双双 exit=1、索引根本没被加回去，于是「索引存在」这一步根本不存在。**补齐环境变量重跑后 verdict 全 true。**
> 这条属于**脚本/env 缺陷**，不是平台缺陷；同一条纪律在 DB-005 报告里也用过一次（「先确认采集器常驻，再判断平台没采到数据」）。此处记录，以免后续复现者踩同一个坑。

### 3.8 告警（C-5 / B-3b）

- **C-5 PASS**：慢 SQL 告警 1 条 @2026-11-06 15:46:59 (+08)，最慢 SQL 平均耗时 **18001.0ms**（执行 1 次）。
- **B-3b 平台侧实际 PASS，但验收脚本漏记**——这是本轮特意回查库内数据挖出来的一条：
  - 库内 `AlertLog` id=**4675**，`alert_type=lock`，severity=critical，**create_time 2026-11-06 07:39:58 UTC（15:39:58 +08）**，resolved_at 07:41:58 UTC，描述含「凶手 (Blocker): 26442 / 受害 (Waiter): 26443 / 已阻塞时长 10 秒」——**落在 S-1 场景 B 窗内，平台确实产出了锁等待告警**。
  - 但该告警挂在 **config_id=18**（mock proxy 那个实例）而非 19：两者指向同一台 MySQL 后端，**会话级阻塞分析按实例轮询，cid=18 先采到**。
  - S-1 验收脚本查的是 `/api/v1/databases/19/alerts/`。该接口按 `config_id` 过滤（`monitor/api_views.py` `DatabaseAlertsView`，**无状态过滤**），所以 cid=19 名下只有 slow_query 与 health 两条，脚本里 B-3b 那一行根本不会被写入（脚本仅在存在 lock 告警时才记该项）。
  - **结论：B-3b 实为 PASS（平台行为正确），本轮是脚本观测口径漏记**；**不作为平台缺陷**，也不改动 DB-005 已有的 B-3b PASS 记录。
  - 附带观察：本轮另有一条 `health` 告警（健康评分 60.2 分 D 级，performance=0.0，提示「当前 5609 个活跃慢查询」）——这是平台对慢查询的正常评分反应，非缺陷。

### 3.9 ★口径自查订正：C-3 的「客户端侧」数字是脚本写死的常量

DB-005 用的验收脚本 `db005_api_check3.py` 里，C-3 的客户端侧数字是**硬编码字符串**：

```python
'客户端侧（workload 打印）：slow_user_sort 23.4ms、pk_lookup 4.5ms、'
'agg 42.3ms、range 18.0ms（...）'
```

DB-005 那轮这四个数与 `run_B_client_summary.txt` 完全一致（23.4 / 4.5 / 42.3 / 18.0），所以 DB-005 的 C-3 是**人工誊写当时日志**的结果、结论没错；但**脚本本身不会重算**，S-1 原样复用后这四个数就变成了**过期常量**（S-1 实测是 23.1 / 2.6 / 41.8 / 17.2，其中 `pk_lookup` 差了近 2 倍）。

**处置**：本报告 C-3 一栏的客户端数字**一律以 S-1 实测日志为准**（入库文件 `s1_run_A_client_summary.txt` / `s1_run_B_client_summary.txt`；原始副本 `C:\dispatcher\probe_out\s1_run_A.log` / `s1_run_B.log` 不入库），证据 JSON 中残留的旧常量已在 §3.3 旁注明。**该问题属验收脚本自身的口径缺陷，不写成平台缺陷**，也**不改动 DB-005 报告**（DB-005 的 C-3 结论本身成立）。

---

## 4. G-8..G-13 逐项回填

| 缺口 | DB-005 时的状态 | **本轮（DB-005R 外网段）** | 依据 |
|---|---|---|---|
| **G-8** set 级真值（连带 C-4、B-4） | 不可得（mock 无 set 拓扑） | **仍为缺口，不硬编** | mock 无 set 级真值；集中式 MySQL 无分片语义。跨 set 对账与按 set 分布**无有效替代**，测了不构成证据 |
| **G-9** A-3/A-4 分片键语义（D-01 广播全 set、D-05 proxy 汇聚聚合） | 不可得 | **仍为缺口** | 需 groupshard 实例与真实 set 数；S-1 是集中式单库，**不存在**分片语义 |
| **G-10** B-5/B-6/B-7 EXPLAIN ANALYZE | 门禁关闭 | **本段不涉及，开关未动** | 补充单明示维持关闭；本轮实测 403 `DISABLED` 属**按设计关闭**，非缺陷 |
| **G-11** R-4 日周期 / 昨日同时段环比 | 需跑满一天 | **待时窗成熟，2026-11-07 12:00 回填同一份报告** | 采集器自 2026-11-05 20:31:56 连续运行未中断，满 24h 后即可用同一接口 `baseline=day` 重跑 |
| **G-12** C-2「slow_user_sort 排第一」 | 靶场语义差异 | **一半闭合、一半仍缺** | ✅「**平台排序逻辑**」已可验并 PASS：平台按窗口总耗时排序，慢查询在前列（slow_user_sort 列表下标 2 ⇒ 第 3 位）、点查不进前列（pk_lookup 下标 7 ⇒ 第 8 位）——该结论与 set 拓扑无关。❌「slow_user_sort 排第一」这一**具体结论**在 S-1 仍不成立，原因与 DB-005 同源（窗口内存在更慢的注入语句 `lockwait_update` 第 1 位、`agg_no_shardkey` 第 2 位），**属场景/靶场差异，不是平台缺陷**；真 TDSQL 多 set 环境下才可判定 |
| **G-13** 手工 EXPLAIN 对 `INTERVAL ? HOUR` 形态返回 502 | 观察，优雅失败 | **已定性并三方互证（§6），仍不作为缺陷** | 见 §6 |

---

## 5. 缺口清单（本轮更新）

| # | 缺口项 | 原因 | 复通条件 |
|---|---|---|---|
| G-8 | set 级真值｜连带 C-4、B-4 | 需真 TDSQL 多 set 拓扑；mock 无 set 级真值，集中式 MySQL 无分片语义 | 新版本上线、内网环境就绪后，由用户转达 Lingma 在真 TDSQL 上重跑 |
| G-9 | A-3 / A-4 分片键语义 | 需 groupshard 实例与真实 set 数 | 同上 |
| G-10 | B-5 / B-6 / B-7 EXPLAIN ANALYZE | 门禁按设计关闭（V-7 内网结论「无法判定」），实测 403 `DISABLED` | V-7 结论明确后由 PM 决定是否放行开关 |
| G-11 | R-4 日周期 / 昨日同时段环比 | 昨日同窗数据需采集满 24h（采集器 11-05 20:31:56 起连续运行） | **2026-11-07 12:00 用同一接口重跑并回填本报告** |
| G-12（余半） | C-2「slow_user_sort 排第一」 | 需真 TDSQL 多 set 广播代价；S-1 集中式环境下被更慢的注入语句与全表聚合语句压住 | 同 G-8 |
| G-13 | 手工 EXPLAIN 对 `INTERVAL ? HOUR` 形态 502 | 平台按「语句不被目标库接受」优雅失败（502 + 明确错误），非崩溃、非错数据 | 如产品侧希望支持，需在采集侧保留带字面量的可执行样例；**属产品口径选择，不在本单范围** |

---

## 6. G-13 三方互证（已定性）

**问题**：平台把 performance_schema 采到的、**本身以 `EXPLAIN` 开头**的 digest 再拿去 `EXPLAIN`，返回 502 `EXPLAIN_FAIL`。

| 侧 | 对象 | 结果 |
|---|---|---|
| 平台（直连 MySQL 8.0） | cid=19 慢列表中 2 个平台自身 EXPLAIN digest | **2/2 全部 502** `{"code":"EXPLAIN_FAIL","message":"计划采集失败(语句不支持或目标库拒绝)"}` |
| 平台（经 mock proxy） | cid=18 慢列表中 8 个平台自身 EXPLAIN digest（探针取前 6 个，脚本 `uniq[:6]`） | **6/6 全部 502**，与直连侧**同样的错误体** |
| 目标库直证 | `EXPLAIN FORMAT=JSON EXPLAIN FORMAT=JSON SELECT …` | **1064**（语法错误，双重 EXPLAIN） |
| 目标库直证 | `SELECT … WHERE created_at >= NOW() - INTERVAL ? HOUR`（占位符形态） | **1064**（MySQL 不支持该占位符写法） |
| 目标库直证 | `SELECT … WHERE created_at >= NOW() - INTERVAL 21 HOUR`（字面量形态） | **成功**，返回 `354` |

**结论**：502 的语义是「**目标库拒绝该语句**」，与 proxy 无关（直连侧同样 502、同样错误体）；平台行为是**优雅失败**——返回明确的 502 + 可读错误信息，不崩溃、不返回错数据、不静默吞掉。**不构成缺陷单**；是否要支持「对平台自身 EXPLAIN 语句的二次 EXPLAIN」属产品口径选择，不在本单范围。

---

## 7. 缺陷单：来源仍为空

**本单不产出缺陷单。** 沿用 DB-004 / DB-005 / DB-015 确立的口径：**只有「与平台实现相关且外网可稳定复现」的问题才提缺陷单；靶场能力边界导致的 FAIL/SKIP 一律不作为缺陷单**。

| 本轮观察到的非 PASS 项 | 定性 | 是否提缺陷单 |
|---|---|---|
| G-8 / G-9 不可跑、set 级真值不可得 | 内网真 TDSQL 依赖未满足（用户已决定上线前不安排内网工作） | 否 |
| B-5/B-6/B-7 403 `DISABLED` | 门禁按设计关闭 | 否 |
| C-2「slow_user_sort 排第一」不成立 | 场景/靶场差异（窗口内有更慢的注入语句与全表聚合语句） | 否 |
| G-13 502 EXPLAIN_FAIL | 平台对「目标库拒绝语句」的**优雅失败** | 否（记为观察，§6） |
| 环比接口 `plan_changed_at=None` | 计划变更已由 `SqlPlan` 表直接取证；该字段为空原因未定位 | 否（**如实列出，未定性为缺陷**） |
| A-2 首跑假阴性 | **我方验收脚本 env 缺陷** | 否（§3.7 已订正重跑） |
| C-3 客户端侧数字过期 | **我方验收脚本硬编码常量** | 否（§3.9 已订正，报告以实测日志为准） |
| B-3b 脚本未记录 | 告警实际挂在 cid=18，脚本只查 cid=19 | 否（§3.8，平台行为正确） |

**结论：DB-006（GG）缺陷单来源仍为空，按其单据「空转说明归档，不硬修」处理。我方不代提、不代修。**

---

## 8. 环境副作用披露（如实记录，未隐瞒）

### 8.1 共享开发库上的改动

- 新增纳管实例 **config id=19**「M-team probe-mysql-8.0」（mysql / 13306 / collect_interval 60s / is_active）。**未改动**既有 id=18 及他人 id=1/2/3/9/10/11/15/16。
- 平台本地账号 `mteam`（super_admin）的口令在 S-1 的 **P0 阶段已轮换**为本次随机值（`u.set_password(...)` → `u.save()`；脚本 `reproduce_s1_run.py` / `reproduce_reset_pw.py` 随证据入库）；口令**只经进程环境变量 `MTEAM_PW` 传入，不落盘、不入库**。
- 在目标库 `aiops_probe_shop.orders` 上**删除**了索引 `idx_user_created`（场景 B 制造退化），A-2 专项脚本末尾又执行了一次 `index drop`（脚本语义里的「复原」＝回到**场景 B 末态**，不是回到场景 A）。**收尾状态：索引缺失，与 DB-005 收尾状态一致。**已实测复核：`SHOW INDEX FROM orders` ⇒ 仅 `PRIMARY`；`SELECT COUNT(*)` ⇒ 150000 行。
- 采集器与 API 进程**全程未重启**（保住 G-11 的 24h 采集连续性）。
- 本轮**未执行** `init_roles --force`（DB-005 已执行过，权限码 `sql_monitoring.view` 已落库，本轮接口全部 200，无需重建）。
- 上述均发生在**共享开发环境**；他人若见新增实例 id=19 或该库索引状态变化，可据此复核。

### 8.2 采集噪声（他人实例）

采集器对他人既有实例同样轮询，这些实例的目标库在本机未启动，会产生 `collect.*` 降级。**我方未改动这些实例**，但降级记录会出现在共享日志中。

### 8.3 证据脱敏

入库证据在写入前后各做一次扫描：本地地址、端口、账号在入库副本中**统一掩码**（`<local-host>` / `<platform-port>` / `<target-port>` / `<proxy-port>` / `<account>`）；口令一律不出现在证据中（只经环境变量传入进程）。

- 硬清单：`127.0.0.1` / `15002` / `13306` / `8009` / `tdsql_test_2024` / `Mteam-Local-` / `enc:` / `PWD=` / `root@` / `SYSDBA/` / `eyJ` ＋ 正则（真实 Bearer 令牌、字面量口令）
- **扫描结果：命中 0**（`manifest.json` 内 `scan_hits: 0`）
- 原始运行副本保留在本机 `C:\dispatcher\probe_out\`，**不入库**。
- 仓库 `.gitignore` 含 `*.log`，故三份日志类证据入库时统一改名为 `.txt`（`s1_run_A_client_summary.txt` / `s1_run_B_client_summary.txt` / `s1_lockwait_injection.txt`，沿用 DB-005 的 `run_B_client_summary.txt` 先例），**内容逐字节等同原始日志**（仅做 §8.3 的掩码处理）。

---

## 9. G-11 回填计划

| 项 | 内容 |
|---|---|
| 缺口 | R-4 日周期 / 昨日同时段环比 |
| 前提 | 采集器自 2026-11-05 20:31:56 起**连续运行未中断**（已核，PID 48048）⇒ 满 24h 后昨日同窗有数据 |
| 动作 | 用**同一接口、同一验收脚本**重跑 `baseline=day`（`/api/v1/databases/19/perf/sql-regression/` 的日周期/昨日同时段口径），把「昨日同时段」与「日周期 SQL」两项结果**回填同一份本报告** |
| ETA | **2026-11-07 12:00**（与接单回执一致） |
| 阻塞 | 无。**唯一风险是采集器被重启**——`DeltaTracker` 是进程内状态，重启即打断 24h 连续性。本轮已明确不重启 |

---

## 10. 对后续的交棒

### 10.1 内网段（第二段）

按补充单：新版本上线、内网环境统一就绪后，**由用户转达 Lingma 执行**，ETA 届时再定。本报告 §4/§5 的缺口清单与复通条件即为内网段的输入。

### 10.2 复测时必须避开的五个坑（DB-005 已踩 3 个，本轮又踩 2 个）

1. **采集器必须常驻**：`start_monitor --once` 永远不产出 delta，`sql_stat` 恒 0 行（DB-005 踩过）。
2. **环比接口的 `from/to` 必须 `urlencode`**：ISO 串里的 `+08:00` 未编码时 `+` 被当空格，`parse_datetime` 失败 ⇒ 接口**静默回退**默认窗口＋昨日基线 ⇒ 全部条目误判为「新增」（DB-005 踩过）。
3. **基线窗终点要避开变更时刻**：采集行时间戳落在每分钟 :58，把「横跨删索引时刻」那一行纳入基线会把退化倍率算低（DB-005 踩过）。
4. **验收脚本的环境变量必须显式透传**：workload 子进程拿不到 `TDSQL_WL_*` 就会让 index add/drop 静默 exit=1，产生假阴性（本轮 A-2 首踩，已订正）。
5. **别信脚本里写死的常量**：C-3 的客户端侧数字是硬编码的 DB-005 期数值，跨轮次复用即过期（本轮踩，已订正）。

### 10.3 复通条件

- **G-11**：采集满 24h 后同脚本同命令重跑，回填本报告。
- **G-8 / G-9 / G-12（余半）**：真 TDSQL 多 set 环境就绪后重跑 C-4、B-4、A-3、A-4 与「slow_user_sort 排第一」的判定。
- **G-10**：V-7 内网结论明确后由 PM 决定是否放行 `PERF_EXPLAIN_ANALYZE_ENABLED`。
- **G-13**：若产品决定支持「对平台自身 EXPLAIN 语句的二次 EXPLAIN」，需在采集侧保留带字面量的可执行样例。

---

## 11. 结论

**有条件通过（外网段）。**

- **已闭合**：G-12 的「平台排序逻辑」半边（PASS，慢查询在前列、点查不进前列）；G-13（已定性为优雅失败，三方互证）；S-1 链路级回归（22 项：PASS 10 / OBSERVED 4 / 门禁 1 / 记缺口 7，另 B-3b 实为 PASS）。核心链路在**真实 MySQL 8.0 直连**上再次实测走通，退化倍率 36.69、计划变更哈希 `b9e5ae404a8d…` → `b3b33cfb6ef8…`、LOCK_WAIT 18000ms、I-01↔I-03 切换，全部有据。
- **未闭合且不硬编**：G-8、G-9、G-12 余半、G-10（门禁按设计关闭）；G-11 待 2026-11-07 12:00 回填。
- **缺陷单**：仍为空，DB-006 按空转说明归档。
- **我方自查订正 3 处**（A-2 脚本 env 假阴性、C-3 脚本硬编码常量、B-3b 脚本观测口径漏记），均属我方验收脚本，**未写成平台缺陷**。

---

## 12. 成本披露

| 项 | 值 |
|---|---|
| S-1 实测耗时 | 2026-11-06 15:25:39 → 15:49:51（24 分 12 秒） |
| 证据 | 17 份（9 份实测产物 + 6 份可复现脚本 + README 清单 + manifest.json），敏感扫描命中 0 |
| 业务代码改动 | **0** |
| 他人文件改动 | **0** |
| 共享环境副作用 | 新增实例 id=19；`orders` 表索引 `idx_user_created` 删除后**保持缺失态**（与 DB-005 收尾状态一致）；`mteam` 口令轮换 |
| 采集器重启 | **未重启**（保 G-11 的 24h 连续性） |
| 验证闸门 | `scripts/validate.sh unit` **exit 0**（红线扫描 pass、unit 测试 pass）；unit **423 tests OK**（时点 HEAD `daa264b`） |

---

## 13. 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-06 | MMMan | 第一段（外网）交付：G-12 部分、G-13、S-1 实测回填；含 3 处我方验收脚本自查订正；缺口清单更新；缺陷单仍为空 |
| v1.1 | 2026-11-07 | MMMan | （待办）G-11 回填：日周期 / 昨日同时段环比实测结果回填 §3.3 与 §9 |
