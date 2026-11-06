# DB-009 交付报告：DM/GBase 实库矩阵（方言对齐 + GBase 矩阵）

**结论：有条件通过**

> 一句话：**DM 方言对齐段完全通过** —— 订正前 `monitor/checkers/dm.py` 的 52 条采集 SQL 有 29 条在真 DM8 上执行失败，订正后 47 条**全部执行通过**，且在 **pyodbc 与达梦官方 dmPython 2.5.32 两条驱动通道上各跑一遍均 47/47**；
> **GBase 矩阵段通过** —— 目标库已通（DB-017 记录的「未启动」前提已过期）、DB-017 §4.6.3 的两个真实缺陷已修、新增 `GbaseDialectTests` **5 条实跑全过**。
> **「有条件」的唯一原因**：平台运行时 `.venv`（Python 3.11/cp311）仍装不上 dmPython（官方 wheel 是 cp314，ABI 不匹配），
> 因此 **DM 检测器在平台进程里仍然不注册** —— 这一项属环境适配，不是方言问题，但它决定了 DM 检测器何时能在平台里真正生效。

| 段 | 任务单要求 | 结果 | 判据 |
|---|---|---|---|
| ① DM 方言对齐（B/D 批） | 修 29 条不可跑 SQL + `start_monitor.sql_stat` 分支 + sentinel DM ASH 分支，逐条实库验证 | ✅ | 29 → **0**；47/47 双通道；用例 2/2 |
| ② GBase 矩阵 | 启动目标库；补当前版本用例与 ASH 回归可跑范围 | ✅ | 目标库已通；用例 5/5；两个缺陷已修 |
| ③ 方言矩阵更新 | 更新 `V3.0_DIALECT_MATRIX.md` | ✅ | v2.0：6 方言 **5 个有实跑证据** |
| 闸门 | `validate.sh` unit + backend | ✅ | unit exit 0 / **423 tests OK**；backend exit 0 / **603 tests OK (skipped=29)** |
| frontend | — | 不适用 | 本单无前端改动 |

---

## 1. 本轮范围、分工与执行方式

- **执行**：MMMan（team lead）。接单回执 `docs/pm/reports/DB-009-receipt-m-team.md`（commit `9317910`）。
- **分工**：代码改动由 Fire 驱动的 CLI 会话先做，**桌面端独立验收并负责最终收口**（审阅代码、订正语义、跑闸门、写报告、提交推送）。
  CLI 会话在 DB-009 上连续跑飞 4 次（Fire 服务重试链缺陷，已修），其产出**未采信为结论**，只作为待审代码。
- **验收纪律**：桌面端**不读对方结论**，只读**最终代码**，用真库逐条打 SQL 判「能不能跑」。
- **口径纪律**：方言对齐**只换视图/列，不改指标含义**；DM8 无对应视图时用**合法空集占位（`WHERE 1=0`）+ 注释**，不伪造数据。

---

## 2. DM8 方言对齐

### 2.1 根因：22 个 V$ 视图在 DM8 里根本不存在

`monitor/checkers/dm.py` 原实现按**另一版 DM 视图体系**书写。DB-017 §4.1 实测出 52 条 SQL 里 **29 条失败**；
桌面端用 AST 逐个探测 40 个 V$ 视图，得到**确切的失败形态**：

| 错误码 | 含义 | 条数 |
|---|---|---|
| `-2106` | 无效的表或视图名 | **15** |
| `-2111` | 无效的列名 | **13** |
| `-2207` | 无法解析的成员访问表达式（`l.SESS_ID`，`V$LOCK` 无该列） | （含在上述 13 内） |
| `-2007` | 语法分析出错（`DBA_TABLES` 上连写两个 `WHERE`） | **1** |

**DM8 里不存在的 22 个 V$ 视图**（逐个实测，非推断）：
`V$SESSION_WAIT`、`V$TEMP_SPACE_USAGE`、`V$SYSTEM_INFO`、`V$SQLSTATS`、`V$ARCH_DEST`、
`V$DEST_PENDING`、`V$CLUSTER`、`V$DSC_INSTANCES`、`V$GLOBAL_LATCH`、`V$LOG`、`V$LOGIN`、
`V$TRX_WAIT`、`V$SYSFILES`、`V$SPACE`、`V$BF_POOL`、`V$HUGE_TS`、`V$DISK_STAT`、`V$ARCHIVE`、
`V$BACKUP`、`V$SESS_STAT`、`V$LOCK_USED`、`V$CONTENT`。

另外 `V$BUFFER` **存在但为 0 行**，且没有 `REQUEST_R_SORT` / `CACHE_R_SORT` 列（-2111）。

> **实测坑（写给下一棒）**：DM8 的**数据字典不列 V$ 动态视图** —— 查 `ALL_TAB_COLUMNS` / `DBA_OBJECTS`
> 都拿不到 `V$BUFFERPOOL` / `V$TRX` / `V$LOCK` 的列（`DBA_OBJECTS` 只列 10 个 V$ 对象）。
> 列结构只能靠 `SELECT * ... WHERE 1=0` + `cursor.description` 取。本轮所有改法的列选择**全部**以此为依据，
> 不按 MySQL 习惯推断。

### 2.2 29 条逐条改法

| # | 原写法（失败） | 改后 | 依据 |
|---|---|---|---|
| 1 | `V$INSTANCE.MODE` | `V$INSTANCE.MODE$` | 列名带 `$` |
| 2 | `V$SESSION_WAIT` 会话等待计数 | `V$LOCK WHERE BLOCKED=1`（**后订正**为 `COUNT(DISTINCT TRX_ID)`） | `V$SESSION_WAIT` 不存在；`V$LOCK` 有 `BLOCKED`/`TRX_ID` |
| 3 | `V$TEMP_SPACE_USAGE` 临时表空间 | `V$TABLESPACE WHERE TYPE$=2` + `V$PARAMETER.GLOBAL_PAGE_SIZE` | `PAGE` 是 `V$TABLESPACE` 真列（16384） |
| 4 | `V$SYSTEM_INFO.SQL_COUNT` | `V$SYSSTAT WHERE NAME='sql executed count'` | `V$SYSSTAT` 列 = `ID/CLASSID/NAME/STAT_VAL` |
| 5 | `V$SYSTEM_INFO.TRAN_COUNT` | `V$SYSSTAT 'transaction total count'` | 同上 |
| 6 | `V$BUFFER.REQUEST_R_SORT/CACHE_R_SORT` | `V$BUFFERPOOL` 逻辑读/物理读 | `V$BUFFER` 无这两列且 0 行 |
| 7 | `V$LOCK l JOIN V$SESSIONS s ON l.SESS_ID=s.SESS_ID` | `V$LOCK l → V$TRX t ON l.TRX_ID=t.ID → V$SESSIONS s ON t.SESS_ID=s.SESS_ID` | `V$LOCK` **无** `SESS_ID` 列 |
| 8 | `V$SESSION_WAIT` 等待事件分布 | `V$WAIT_HISTORY` 按 `WAIT_CLASS` 聚合 | `V$SESSION_WAIT` 不存在 |
| 9 | `V$SESSIONS.PROGRAM/IP/HOST` | `CLNT_IP/CLNT_HOST/APPNAME` | `V$SESSIONS` 57 列实测 |
| 10 | `V$SESSIONS.SQL_TEXT/TRANSACTION_ID` | `SUBSTR(SQL_TEXT,1,200)` + `TRX_ID` | 同上 |
| 11 | `V$SQLSTATS` 慢 SQL | `V$SQL_HISTORY GROUP BY SQL_ID, TOP_SQL_TEXT` | `V$SQLSTATS` 不存在；`V$SQL_HISTORY` 有 `TIME_USED(ms)`/`N_LOGIC_READ`/`N_PHY_READ`/`AFFECTED_ROWS` |
| 12 | `V$BUFFER` 池字段 | `V$BUFFERPOOL`（**后订正** `active_count`） | `V$BUFFER` 0 行 |
| 13 | `V$DATABASE.ARCHIVE_MODE` | `V$DATABASE.ARCH_MODE` | 列名 |
| 14 | `V$ARCH_DEST` 归档目的地 | `V$ARCH_STATUS`（`ARCH_TYPE/ARCH_DEST/ARCH_STATUS/ARCH_SRC`） | `V$ARCH_DEST` 不存在；`V$ARCH_STATUS` 4 列实测 |
| 15 | `V$DATABASE.MODE$` | `V$DATABASE.ROLE$`（0 → PRIMARY） | `V$DATABASE` 无 `MODE$` |
| 16 | `V$ARCH_DEST` 实时归档 | `V$ARCH_STATUS` | 同 14 |
| 17 | `V$RLOG` 主备同步字段 | **桌面端订正** → `V$ARCH_STATUS.ARCH_STATUS` | `CUR_OFFSET` 是字节偏移、`ALMOST_FULL_STATUS` 是日志空间水位，**都不是同步进度** |
| 18 | `V$DEST_PENDING` 主备延迟 | `V$ARCH_FILE WHERE 1=0`（合法空集占位） | `V$DEST_PENDING` 不存在；DW 专有 |
| 19 | `V$CLUSTER` DSC 集群 | `V$INSTANCE` + **桌面端订正** `V$DATABASE.DSC_NODES` | `V$CLUSTER` 不存在 |
| 20 | `V$DSC_INSTANCES` | `V$ARCH_FILE WHERE 1=0` | DSC 专有 |
| 21 | `V$GLOBAL_LATCH` | `V$ARCH_FILE WHERE 1=0` | DSC 专有 |
| 22 | `V$GLOBAL_LATCH WHERE BLOCKING_LRU>0` | `V$LOCK WHERE BLOCKED=1` | 同上 |
| 23 | `count(*) FROM V$LOG` | `count(*) FROM V$ARCH_FILE` | `V$LOG` 不存在 |
| 24 | `V$INSTANCE.LOG_SIZE` | `V$RLOG.TOTAL_SPACE` | `V$INSTANCE` 无 `LOG_SIZE` |
| 25 | `count(*) FROM V$LOGIN` | 置 `None`（**不可得**，不伪造） | `V$LOGIN` 不存在 |
| 26 | `V$TRX.TRX_START_TIME` | `V$TRX.ID/STATUS` + `V$SESSIONS.CREATE_TIME`（**后订正**加 `STATUS='ACTIVE'`） | `V$TRX` 25 列里**没有事务开始时间** |
| 27 | `V$DEADLOCK_HISTORY.DEADLOCK_TIME` | `V$DEADLOCK_HISTORY.HAPPEN_TIME` | 列名 |
| 28 | `V$RLOG WHERE STATUS='ACTIVE'` | `V$RLOG WHERE ALMOST_FULL_STATUS <> 'SAFE'` | `V$RLOG` 无 `STATUS` 列 |
| 29 | `DBA_TABLES ... WHERE ... WHERE`（语法错） | `SELECT * FROM (...) WHERE ROWNUM <= 20` | DM8 不支持同一层两个 `WHERE` |

### 2.3 桌面端复核订正的 6 处「指标含义偷换」

CLI 会话的实现把 SQL 改通了，但其中 **6 处把「别的指标的值」塞进了输出契约字段** ——
违反「冻结输入语义」，已逐条订正并重跑审计：

| # | 字段 | CLI 会话实现 | 问题 | 订正后 | 实测取值 |
|---|---|---|---|---|---|
| 1 | `session_wait_count` | `COUNT(*) FROM V$LOCK WHERE BLOCKED=1` | 告警模板语义是「**等待会话数**」，数锁条数会把同一会话放大 | `COUNT(DISTINCT TRX_ID)` | 0（当前无阻塞） |
| 2 | `buffer_pools[].active_count` | `N_LOGIC_READS` | **逻辑读次数**冒充活跃页数 | 置 `0` + 注释（`V$BUFFERPOOL` 无活跃页列，老实现的 `V$BUFFER.ACTIVE_COUNT` 在 DM8 不存在） | 0 |
| 3 | `rlog_sync_status` | `V$RLOG.CUR_OFFSET` → `apply_seq`、`ALMOST_FULL_STATUS` → `sync_status` | 字节偏移与日志水位**都不是主备同步进度** | 改查 `V$ARCH_STATUS.ARCH_STATUS`（真值列） | `[]`（本机未开归档） |
| 4 | `dsc_node_count` | `len(dsc_cluster_info)` | `V$INSTANCE` 恒有本实例一行 ⇒ **单机被报成「1 节点集群」** | `V$DATABASE.DSC_NODES`（真值列） | 1（真值即 1） |
| 5 | `long_transactions` | `(SYSDATE - s.CREATE_TIME) > 60` | 会话年龄当事务年龄；且不加事务状态过滤 ⇒ **长空闲会话被当成长事务** | 加 `t.STATUS='ACTIVE'` + 注释说明口径 | `[]` |
| 6 | `login_count` | `try: login_count = None except: login_count = 0` | `except` 分支把「不可得」渲染成「登录数为 0」 | 去掉无意义 try，统一 `None` | `None` |

另订正 2 处**口径漂移**：

- `cache_hit_ratio`：CLI 会话实现算出的值是 **11.66%**。桌面端核对 DM8 自带的
  `V$BUFFERPOOL.RAT_HIT` 真值列：**本方公式 `1 - N_PHY/(N_LOGIC+N_PHY)` = 98.29%，
  与 `RAT_HIT` 加权总体 0.9829 完全一致**（逐池核对：`NORMAL` 池 1-22/189=0.8836 ≡ RAT_HIT 0.8836，
  `FAST` 池 1-12/81546=0.999853 ≡ RAT_HIT 0.999853）。⇒ **11.66% 是错的**，
  已改回缓冲池命中率口径（`RAT_HIT` 即 DM8 官方定义）。
- `slow_queries.avg_time`：由 `SUM(TIME_USED)` 改为 `AVG(TIME_USED)`（原写法让「平均耗时」变成「累计耗时」）。

### 2.4 订正前 / 订正后对照（同一份脚本、同一条真库）

| 版本 | SQL 条数 | 实库执行通过 | 失败 |
|---|---|---|---|
| 订正前（`git show HEAD:monitor/checkers/dm.py`） | 52 | 23 | **29** |
| 订正后（工作树） | 47 | **47** | **0** |

**两条驱动通道各跑一遍，结果一致**：

| 通道 | 结果 | 证据 |
|---|---|---|
| pyodbc（平台连接器 `_connect_dm`） | **47/47** | `db009_dm_sql_audit.json` |
| **达梦官方 dmPython 2.5.32 直连** | **47/47** | `db009_dm_sql_audit_dmpython.json` |

### 2.5 检测器级（`collect_metrics`）结果

| 项 | 订正前 | 订正后 |
|---|---|---|
| 产出 key 数 | 67 | 67 |
| 核心标量为 None 的字段 | 1（`login_count`） | 1（`login_count`，**不可得**） |
| 空列表字段 | 12 | 12 |
| `buffer_pools` | `[]` | **28 条**（`V$BUFFERPOOL`） |
| `cache_hit_ratio` | 11.66（口径错） | **98.19**（≡ 官方 `RAT_HIT` 口径） |
| `active_connections` / `max_connections` / `conn_usage_pct` | 1 / 10000 / 0.01 | 1 / 10000 / 0.01 |

> 检测器级是用**进程内 dmPython 桩**绕过 `import dmPython` 的注册闸门后跑的：
> 桩的 `connect()` **显式抛错、不提供任何假连接**，真连接一律走平台连接器。
> 官方驱动下的真连接复验见 §2.4 第二行。

---

## 3. `start_monitor` 与 sentinel 的 DM 分支

### 3.1 `start_monitor.sql_stat`

原实现把 `dm` 与 `oracle` 合并走 `v$sqlstats` —— 实测 **DM8 无此视图（-2106）**。
已拆出独立 DM 分支，改用 `V$SQL_HISTORY` 按 `SQL_ID` 聚合（DM8 支持 `LIMIT`，但 `ROWNUM` 需外层包裹）。

实测（AST 取源码真实 SQL + 直接调用平台真实方法 `Command._snapshot_sqlstat`）：
- 源码 SQL 在真 DM8 上执行 → **20 行**；
- 平台真实方法调用 → **不报错**，增量行 0（**首轮差分无增量属预期**：`DeltaTracker` 是进程内状态）。

### 3.2 sentinel DM ASH 分支

原实现把 `dm` 归入 Oracle 家族分支，用 `v$session`（单数）与 `V$SESSIONS.USER_ID` —— 实测两者均不可用。
已拆出独立 `_ash_dm()`，按 DM8 实测列改写：`V$SESSIONS`（复数）/ `CLNT_IP` / `CLNT_HOST` / `APPNAME` / `TRX_ID`。

桌面端复核补强 2 处：
- **阻塞判定**：`V$SESSIONS` 没有阻塞列 ⇒ 改由 `V$LOCK.BLOCKED` 得出，关联键是 **`TRX_ID`**（`V$LOCK` 没有 `SESS_ID`）。
- **`active_secs`**：原恒为 0 ⇒ 改用 `(SYSDATE - CREATE_TIME)*86400`。

实测：`sample_sessions(cur,'dm')` → **1 行真实会话**，字段齐全，`wait_class=on_cpu`，digest 可算。
`golden_metrics(cur,'dm')` → `{threads_connected:1, max_connections:10000, conn_usage_pct:0.01}`
（DM8 的会话视图是 `V$SESSIONS`、上限参数名是 `MAX_SESSIONS`，原实现用的是 `v$session` / `sessions`）。

---

## 4. GBase 矩阵

### 4.1 DB-017 的前提已过期

DB-017 §5 记录的「目标库未启动（10061）」**已不成立**：GBase 8a 目标库现在可连
（`127.0.0.1:5258`，`8.6.2.43-R7-free.110605`，真 GBase 8a，非 MySQL 协议模拟）。
⇒ **G-G1 闭合**。

### 4.2 DB-017 §4.6.3 的两个真实缺陷 —— 已修

| 缺陷 | 根因 | 修法 | 订正后实测 |
|---|---|---|---|
| `sample_sessions` 在 GBase 上**整段抛错** | 哨兵 MySQL 家族分支硬查 `information_schema.innodb_trx`，GBase 8a 无此表 | 改为「先试完整版，失败退回只采 `processlist`」并 degrade 留痕 | **不抛错**；另开连接制造活跃会话后**采到 1 行真实会话**（`wait_class=on_cpu`） |
| `active_connections` 为 `None` | GBase 采集器只产出 `threads_running`，缺核心契约字段 | 补齐该字段，口径与 `mysql.py` 一致（active = threads_running） | `GbaseChecker.collect_metrics` → **51 keys**，`active_connections=1`，**核心标量字段无 None**（订正前 50 keys 且为 `None`） |

### 4.3 补齐覆盖缺口：新增 `GbaseDialectTests` 5 条并实跑全过

```
Ran 5 tests in 0.426s
OK
test_connection_succeeds_on_real_gbase ... ok        # 版本号特征 8.6.2，确认不是 MySQL 端点
test_ash_sampling_does_not_raise ... ok              # 回归 DB-017 §4.6.3 问题 ①
test_collect_metrics_returns_core_contract ... ok    # 回归 DB-017 §4.6.3 问题 ②
test_connection_usage_is_consistent ... ok
test_golden_metrics_available ... ok
```

这两条性质此前**没有任何自动化信号能发现** —— 根因是仓库里**根本没有 GBase 测试类**（覆盖缺口，不是环境缺口）。

### 4.4 GBase 的能力边界（**不是缺陷，不开缺陷单**）

GBase 8a 的 `performance_schema` 只有 7 张 GBase 自有表
（`CACHE_CELL_STATUS_INFO` / `CACHE_USAGE_INFO` / `DISK_USAGE_INFO` / `HEAP_USAGE_INFO` /
`MEMORY_USAGE_INFO` / `SESSION_MEMORY_USAGE_INFO` / `TABLES`），**没有** MySQL 的
`events_statements_current` / `events_statements_summary_by_digest` / `data_lock_waits`；
也没有 `SHOW ENGINE INNODB STATUS`（引擎是 MRG_GSSYS / CSV / EXPRESS / GsSYS / MEMORY，无 InnoDB）。

⇒ GBase 上**不可用**的面（哨兵会打 `degrade` 留痕，属**能力边界**）：

| 面 | 状态 | 留痕键 |
|---|---|---|
| ASH 会话主体（processlist） | ✅ 可用，采到真实会话 | — |
| ASH 原生 digest | ❌ 不可用（指纹降级为文本归一化） | `ash.mysql_native_digest` |
| **阻塞检测** | ❌ 不可用（`performance_schema` 与 `information_schema` 两条路径均缺表） | `ash.mysql_blocking_edges` |
| 锁明细 | ❌ 不可用 | `ash.mysql_lock_detail` |
| 慢 SQL digest 采集 | ❌ 不可用（`monitor/sqlstat_collect.py` 依赖 `events_statements_*`） | `sqlstat.digest_columns` |
| 语句超时保护 | ❌ 不存在（GBase 不支持 `max_statement_time` / `max_execution_time`） | `db_connector.mysql_statement_timeout` |

---

## 5. 缺口清单与复通条件

| # | 缺口 | 现状（实测） | 复通条件 | 归属 |
|---|---|---|---|---|
| G-D1 | DM **巡检检测器层**在平台运行时不可用 | 平台进程用 `.venv`（**Python 3.11 / cp311**），官方 dmPython 2.5.32 是 **cp314** wheel ⇒ ABI 不匹配 ⇒ `import dmPython` 失败 ⇒ `DamengChecker` 不注册 | 给 `.venv` 装 **cp311 版** dmPython（官方 wheel 可得；本机已有 cp314 版可作参照）⇒ `CHECKER_MAP` 出现 `dm` | **环境适配**（非方言问题；建议并入 DB-017 / DB-018 的驱动口径） |
| G-D2 | DM **慢 SQL 面** | 已修：`start_monitor` DM 分支改走 `V$SQL_HISTORY`，实测 20 行 | 闭合（待 G-D1 闭合后在平台进程内复验一次差分产出） | 本单已修 |
| G-D3 | DM **ASH 面** | 已修：sentinel 拆出 `_ash_dm()`，实测 1 行真实会话 | 闭合（用例 `test_ash_sampling_runs` 已实跑通过） | 本单已修 |
| G-D4 | DM **连接数派生指标** | 已修：`golden_metrics` DM 分支改走 `V$SESSIONS` / `MAX_SESSIONS`，实测 `{1, 10000, 0.01}` | 闭合 | 本单已修 |
| G-D5 | DM **29 条巡检 SQL** | 已修：29 → 0，47/47 双驱动通道通过 | 闭合 | 本单已修 |
| G-G1 | GBase **实库连通 / 方言 / ASH 回归** | 目标库已通（前提已过期）；用例 5/5 通过；两个缺陷已修 | 闭合 | 本单已闭合 |
| G-G2 | GBase **无自动化方言用例** | 已补 `GbaseDialectTests` 5 条并实跑通过 | 闭合 | 本单已闭合 |
| G-G3 | GBase **历史连通记录的容器已不可复现** | 目标库现版本 `8.6.2.43-R7-free.110605`，与历史记录**同版本**，可复现 | 闭合 | 本单已核实 |
| G-N1 | 正式机 17.192 相关全部项 | 用户 2026-11-06 决定不动 | 上线后由 PM 另派 | 未动 |
| G-N2 | 内网凭据相关全部项 | 凭据不供给、不落盘、不进版本库 | 同上 | 未动 |
| — | TDSQL | 环境里没有真 TDSQL 实例（`tdsql-sim` 实为 `mysql:8.0`） | 厂商版实例 | 不在本单范围 |

---

## 6. 环境副作用（如实披露）

1. **未重启任何常驻进程** —— 采集器 `start_monitor`（PID 48048，11-05 20:31:56 起）与 API `runserver`（PID 54884）
   **全程未重启**，G-11 的 24h 采集连续性未被打断。本单所有实测都是**旁路连接**完成的。
2. **未新增/删除任何数据库实例**，未改平台库存密里的任何凭据。
3. **GBase 用例与探测会新建 `DatabaseConfig` 记录**（`dialect-gbase` / `dialect-dm`），位于单元测试的临时库，**不污染生产库**。
4. **DM 检测器级验证用了进程内 dmPython 桩**（只在验证进程内、只绕过 import gate、**不提供任何假连接**），
   **未修改仓库任何依赖**、未向 `.venv` 安装任何东西。
5. **官方 dmPython 2.5.32 早前已由 CLI 会话装在本机用户级 Python 3.14**（cp314）——
   本单只**使用**它做直连复验，未新增安装、未改动该环境。
6. **未改 `docs/pm/STATUS.md` / `docs/pm/DECISIONS.md`**（仅 muse 维护）。
7. **未动 Xinchuang 链路**、未碰 DB-001 设计工作。

---

## 7. 闸门证据

| 闸门 | 命令 | 结果 |
|---|---|---|
| unit | `scripts/validate.sh unit` | **exit 0** —— 红线扫描 pass；**423 tests OK** |
| backend | `scripts/validate.sh backend` | **exit 0** —— 语法编译 / Django 系统检查 / 迁移漂移 / 依赖完整性 / 文档版本一致 / 全量测试 全 pass；**603 tests OK (skipped=29)** |
| frontend | — | **不适用**（本单无前端改动） |

> backend 全量测试里的 29 条 skip 是**方言用例未设 DSN 时的正常 skip**（mysql/pgsql/oracle 各自的 DSN 未设），
> 不是断言失败。DB-009 新增的 `GbaseDialectTests` 与 `DamengDialectTests` 均在**带 DSN 的独立运行**中实跑通过（见 §4.3、§5）。

---

## 8. 证据清单

`docs/pm/reports/evidence/DB-009-m-team/` —— **45 份**（产物 + 可复现脚本 + manifest + README）。

| 关键结论 | 证据文件 |
|---|---|
| 订正前 29 条失败 / 订正后 47 条全通过 | `db009_dm_before_after.json` |
| 47/47（pyodbc 通道） | `db009_dm_sql_audit.json` |
| **47/47（达梦官方 dmPython 2.5.32 直连）** | `db009_dm_sql_audit_dmpython.json` |
| 22 个 V$ 视图不存在（逐个实测） | `db009_dm_inventory.json` |
| DM8 动态视图真实列名（数据字典查不到 V$） | `db009_dm_viewcols.json`、`db009_dm_dict_no_dynamic_views.json` |
| 缓存命中率口径核对（98.29% ≡ RAT_HIT 0.9829） | `db009_dm_cachehit_ratio_check.json` |
| 检测器级快照（DM 67 keys / GBase 51 keys） | `db009_checker_run_final.json` |
| 端到端（start_monitor 真实方法 + sentinel 双分支） | `db009_e2e_verify.json` |
| GBase 真实会话并发采样 | `db009_gbase_ash_live.json` |
| GBase 用例 5/5 实跑输出 | `db009_gbase_dialect_tests.txt` |
| DM 用例 2/2 实跑输出 | `db009_dm_dialect_tests_py314.txt` |
| GBase 游标状态对照试验（未能复现） | `db009_gbase_cursor_state.json` |

**脱敏**：本机地址/端口/账号/主机名/实例名/用户目录统一掩码；口令全程不入证据（只经库存密在内存解密，或经环境变量传给子进程）。
敏感扫描：本机地址 0 命中、端口 0 命中、账号 0 命中、口令类关键字 0 命中。

---

## 9. 遗留观察（**不作为缺陷单**）

1. **GBase 游标状态**：首版端到端脚本里 `golden_metrics` 曾返回过 `{}`（说明内部抛异常被吞）。
   针对性做了 3 组对照试验（`ash→golden` / `golden→ash` / `ash→裸执行`），**全部正常、未能复现**，
   且 GBase 黄金状态量在平台连接器下实测取值正常（`{threads_connected:1, max_connections:100, conn_usage_pct:1.0}`）。
   ⇒ **不定性为平台缺陷**，仅记为脚手架观察。
2. **两个脚手架自伤**（均属验收脚本，已在本报告口径中订正，**未写成平台缺陷**）：
   ① 首版用普通 tuple 游标测 `golden_metrics` 报 TypeError —— 平台连接器是 DictCursor，**代码本身没问题**；
   ② 首版用正则把 `start_monitor` 的 SQL 拼出来导致拼错 —— 改用 **AST 取源码片段**后正常。
3. `V$ARCH_STATUS` / `V$ARCH_FILE` 在本机 DM8 实例上**没有行**（未开归档）⇒ 相关字段为空列表，
   是**该实例的实况**，不是「取不到」。

---

## 10. 建议 PM 裁决

1. **G-D1（唯一剩余阻塞项）**：建议把「给 `.venv` 装 cp311 版 dmPython」并入 DB-017 / DB-018 的驱动口径批次。
   在此之前，**DM 检测器在平台进程里不会生效**，DM 采集面只有直连旁路证据。
   ⚠️ 注意：装驱动需要**重启平台进程**才能加载，会打断 G-11 的 24h 采集连续性 ⇒
   **建议 11-07 12:00 G-11 回填完成之后再安排**。
2. **tdsql** 仍是 6 方言里唯一零覆盖项，需要厂商版实例（`tdsql-sim` 实为 `mysql:8.0`，不能充数）。
3. **本单不需要另立缺陷单** —— DM 方言对齐（29 条）与 GBase 两个缺陷均在本单内已修并实跑通过。

---

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-06 | MMMan | 首版交付：DM 方言对齐 29→0（47/47 双驱动通道）、DM 用例 2/2、GBase 目标库已通 + 用例 5/5 + 两个缺陷已修、方言矩阵升 v2.0、双闸门 exit 0 |
