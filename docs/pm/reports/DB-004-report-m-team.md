# DB-004 交付报告：TDSQL 验证准备（外网版）

- 任务单：`docs/pm/orders/DB-004-TDSQL内网验证准备.md`
- 统筹单：`docs/pm/orders/DB-015-DB-004-005-005R改派MMMan外网测试.md`
- 口径补充：`docs/pm/orders/DB-015-补充-凭据暂缓记缺口.md`
- 执行：MMMan（M-team team lead）
- 交付时间：2026-11-05 19:58（STATUS 登记的 ETA 为 2026-11-05 18:00，**实际超期 1 小时 58 分**，如实记备，不做粉饰）

## 结论

**有条件通过** —— 外网环境就绪达成，V-1~V-9 验证清单交付，探针打码行为书面确认完成；内网真 TDSQL 依赖项按补充单口径逐项记缺口，未硬编、未伪造。

## 1. 环境就绪回执

### 1.1 执行环境

本机 Windows 11 桌面端（MiniMax Code 桌面端；PowerShell 5.1 + Git Bash），非云端隔离沙箱。

### 1.2 目标数据库路径（结论来源分三档）

| 目标 | 地址 | 状态 | 本单是否取结论 |
|---|---|---|---|
| 外网 mock 靶场 · MySQL 直连（对照 A） | 127.0.0.1:13306（容器 `tdsql-mysql-test`，mysql:8.0.45，库 `tdsql_test`） | Up | **是** |
| 外网 mock 靶场 · 经 proxy（对照 B） | 127.0.0.1:15002（容器 `tdsql-proxy`，`tdsql-proxy:mock-v1.0`） | Up | **是** |
| 内网真 TDSQL | 10.243.16.252:3306 / 10.243.17.192:3306 | TCP 可达（实测 True）；**凭据按用户 2026-11-05 决定暂不供给，正式机 17.192 暂不动** | **否，全部记缺口** |

旁列备查（与本单探针无直接依赖）：`tdsql-zk`（zookeeper:3.8，2181）、`sqlcheck-redis`（redis:7-alpine，6380）。

> **TCP 可达 ≠ 实测通过**：本单对内网真 TDSQL 不作任何结论。

### 1.3 TDSQL 兼容路径判定（关键定性）

**mock proxy 不是慢 SQL 透传链路的等价靶场。** 其源码实现的是 DB-001 方向的原厂指令集：

- `/*proxy*/show status` / `/*proxy*/show backends` / `/*proxy*/show config`
- `/*proxy*/show table with noshardkey_allset | with shardkey | without shardkey`（G14 原厂统计指令）
- 分布式 DDL 语法过滤与下推（`CREATE TABLE ... shardkey=xxx` / `TDSQL_DISTRIBUTED BY ...`）
- 其余常规 SQL / DML / 事务 / EXPLAIN 100% 透明透传底层 MySQL 8

其源码对 `allsets` 与 `sets:` 的检索**零命中** ⇒ 平台慢 SQL 链路所用的 `/*sets:allsets*/` 透传语法在该靶场**必然不生效**；探针依赖的 `show routes` / `show config` 为真 TDSQL 语法，在 MySQL 侧报 1064（实测 `routes_error`、`config_error` 均为 1064 语法错）。

**因此 V-1 / V-2 / V-4 / V-5 / V-9 在 mock 上的 FAIL/SKIP 属靶场能力边界，不是平台缺陷。**

### 1.4 隔离测试库、只读与超时

- **隔离库**：mock 侧 `tdsql_test`（容器 `MYSQL_DATABASE`），与任何业务库物理隔离（独立容器实例）。
- **只读**：探针默认只读；V-7 用「只读事务 ＋ `WHERE 1=0` 的 UPDATE ＋ ROLLBACK」双保险，只读未生效也不会改动任何行；账号无 UPDATE 权限时该项记「无法判定」。
- **超时**：V-8 用 `SELECT SLEEP(3)` ＋ 500ms 超时；连接侧 connect_timeout=10 / read_timeout=30。
- **输出打码**：探针默认打码主机/账号/端口，已实测确认（见 §3）。

### 1.5 凭据通道

- 通道：环境变量 `TDSQL_PROBE_HOST` / `TDSQL_PROBE_PORT` / `TDSQL_PROBE_USER` / `TDSQL_PROBE_PASSWORD` / `TDSQL_PROBE_DB`（AGENTS.md 凭据通道约定），**不落盘、不进版本库**。
- 本单实测所用为 mock 靶场凭据，只存在于进程环境变量；仓库根已核**无 `.env*` 文件**。
- 内网真 TDSQL 凭据按用户 2026-11-05 决定暂缓供给 ⇒ 相关项一律记缺口。

### 1.6 本地验证闸门

`scripts/validate.sh unit`：**exit 0**；红线扫描 pass（921ms）、unit pass（34733ms），**414 tests OK**。
时点：2026-11-05 19:5x，HEAD `0d017db`。测试条数随上游变动，此结论只对当次 HEAD 快照成立。

## 2. V-1~V-9 验证清单

| 探针 | 判据（真 TDSQL） | 外网可跑性 | mock 对照实测值 | 真 TDSQL 结论 | 缺口定性 |
|---|---|---|---|---|---|
| **V-1** | `/*sets:allsets*/ SELECT 1` 每 set 一行且带 `info`=set 名 | **不可跑**（靶场不实现该语法） | A/B 均 **FAIL**：返回 1 行但无 `info` 列，列=`['x']` | **缺口 G-1** | 靶场边界，非平台缺陷 |
| **V-2** | 透传查 `performance_schema.events_statements_summary_by_digest`，每 set 一行 | **不可跑** | A/B 均 **FAIL**：可查询但无 `info` 列（A 计数 2 / B 计数 19） | **缺口 G-2** | 靶场边界 |
| **V-3** | digest 扩展列 `SUM_CPU_TIME` / `QUERY_SAMPLE_TEXT` 存在；CPU consumer 开启 | **可跑（部分）** | A/B 均 **WARN**：扩展列齐全，但 `events_statements_cpu` consumer 未全开 ⇒「耗时构成」CPU 显示"未采集" | **缺口 G-3**（真 TXSQL 扩展列与 consumer 状态未验证） | mock 侧仅能证明 MySQL 8.0.45 具备该两列 |
| **V-4** | 透传 `SHOW GLOBAL STATUS`，每 set 一行 | **不可跑** | A/B 均 **FAIL**：无 `info` 列（值可取到，如 `Questions`） | **缺口 G-4** | 靶场边界 |
| **V-5** | `/*sets:<某set>*/ SELECT @@hostname` 返回该 set 主节点 | **不可跑** | A/B 均 **SKIP**：探针用 `show routes` 取 set 名，mock 侧为 1064 语法错，无真实 set 拓扑 | **缺口 G-5** | 靶场边界 |
| **V-6** | 透传 `EXPLAIN ANALYZE` 返回 TREE 计划 | **可跑** | A/B 均 **PASS**：返回 TREE 计划（A 首个节点 `-> Aggregate: count(0)`；B 同构） | 链路可用 | **门禁**：V-7 未通过禁止开 EXPLAIN ANALYZE |
| **V-7** | 只读事务经 proxy 生效（写被拒 1792） | **可跑（对照）** | A/B 均 **PASS**：只读事务内的写被拒绝（1792） | **内网结论「无法判定」＝缺口 G-6** | mock 为 MySQL 原生只读事务行为，**非 proxy 语义**，仅作对照参考 |
| **V-8** | `max_execution_time` 经 proxy 生效（约 500ms 返回） | **可跑** | A **PASS** 约 0.27s；B **PASS** 约 0.52s（期望 ≈0.5s） | 链路可用 | 结论限于 mock |
| **V-9** | 集中式实例读写分离时透传是否落主 | **不可跑** | A/B 均 **SKIP**：无 set 拓扑、无 `show routes` | **缺口 G-7** | 靶场边界 |
| **V-10** | 端到端：平台慢 SQL 列表 / SQL 详情 / 退化检测与手工 `/*sets:allsets*/` 一致 | 属 DB-005 范围 | 本单不判 | 依赖 set 级真值 ⇒ **缺口 G-8** | 超出本单 |

合计：mock 对照 A/B 各 **PASS 3 / WARN 1 / FAIL 3 / SKIP 2**（A 探针时刻 19:56:58、B 19:57:05）。

## 3. 打码书面确认（补充单要求的交付前提）

- **实测方法**：两份探针 JSON 在入库前后各做一次敏感 token 扫描，检索 `127.0.0.1`／`root`／mock 密码／`13306`／`15002`。
- **实测结果**：两份 JSON **全部 0 命中（判定 CLEAN）**。
- **结论**：**探针脚本输出默认打码行为成立，且已实测确认。** 本报告以「默认打码已实测」为准，旧表述「输出不打码」与「已默认打码」的自相矛盾项就此消除。
- **证据**：`docs/pm/reports/evidence/DB-004-m-team/mock_direct_13306.json`、`mock_proxy_15002.json`（入库前已复检，无敏感 token）。

## 4. 缺口清单（按补充单口径逐项记，不硬编）

| # | 缺口项 | 原因 | 复通条件 |
|---|---|---|---|
| G-1 | V-1 allsets 透传带 `info` 列（真 TDSQL 语义） | 内网凭据暂缓供给；mock 靶场不实现该语法 | 内网凭据恢复后用同一脚本重跑 |
| G-2 | V-2 透传查 performance_schema digest 表（带 `info` 列） | 同上 | 同上 |
| G-3 | V-3 TXSQL 专有面：digest 扩展列在真 TXSQL 8.0.33 的存在性、`events_statements_cpu` consumer 开启状态 | mock 侧虽两列齐全但 consumer 未全开；真 TXSQL 未验证 | 同上 ＋ 内网按测试指南开启 consumer |
| G-4 | V-4 透传 `SHOW GLOBAL STATUS`（带 `info` 列） | 同 G-1 | 同上 |
| G-5 | V-5 指定单 set 透传（`show routes` 取 set 名） | mock 无真实 set 拓扑 | 同上 |
| G-6 | **V-7 内网结论「无法判定」** | 真 proxy 下只读事务行为未验证；mock 的 PASS 是 MySQL 原生行为，非 proxy 语义，仅作对照参考 | 同上 |
| G-7 | V-9 读写分离时透传是否落主 | mock 无 set 拓扑、无 `show routes` | 同上 |
| G-8 | 平台慢 SQL「每 set 真值 / 单位 / 窗口 / 退化标记」的真 TDSQL 语义 | 依赖 set 级真值，未获真 TDSQL 结论 | 同上 |

**风险提示**：G-8 未闭合 ⇒ DB-005 的 A-1..5 与 B 类语义在外网只能得到**链路级**结论，**set 级真值不可得**，不得以 mock 值冒充。

## 5. 对 DB-005 的范围收窄（交棒）

**外网可跑（可出结论）**

- V-6 透传 EXPLAIN ANALYZE 返回 TREE 计划（mock 两目标均 PASS）
- V-8 `MAX_EXECUTION_TIME` 经 proxy 生效（mock 经 proxy ≈0.52s，期望 ≈0.5s）
- 平台采集 → 慢 SQL 列表 → SQL 详情 → 退化检测的**链路级**回归（用 mock 走通全链）

**外网不可跑（记缺口，不出结论）**：V-1 / V-2 / V-3 / V-4 / V-5 / V-7（内网口径）/ V-9。

**缺陷单口径（重要）**：只有「与平台实现相关且外网可稳定复现」的问题才提缺陷单；**mock 靶场能力边界导致的 FAIL/SKIP 一律不作为缺陷单**，避免把靶场限制误报为平台缺陷。
据此，DB-006（GG）在本轮外网实测下**缺陷单来源可能为空**；若为空，按 DB-006 单据「空转说明归档」处理，不硬修。

**凭据恢复后的回填路径**：内网只读凭据恢复供给后，用同一脚本、同一命令对真 TDSQL 重跑 V-1~V-9，结论表可直接与本报告 §2 对齐回填，无需重写报告骨架。

## 6. 结论

**有条件通过** —— 外网环境就绪达成、V-1~V-9 验证清单交付、打码行为书面确认完成、凭据通道与门禁明确；内网真 TDSQL 依赖项 8 条按补充单口径逐条记缺口并写明复通条件。

## 7. 成本披露

搜索 0 次；抓取 0 次。本轮为本地只读核验 ＋ mock 靶场只读探测（2 个目标各跑一次 V-1~V-9）＋ 一次 `scripts/validate.sh unit`；**未改业务代码、未改他人文件、未触碰 `STATUS.md` / `DECISIONS.md`**；新增内容仅为本报告与已脱敏证据两份 JSON。

## 8. 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-05 | MMMan | 首版：环境就绪回执 ＋ V-1~V-9 验证清单 ＋ 探针打码书面确认 ＋ 8 条缺口（逐条含复通条件）＋ DB-005 范围收窄与缺陷单口径；结论「有条件通过」 |
