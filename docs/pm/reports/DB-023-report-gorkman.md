**结论：有条件通过**（v1.0。下半 34 项检测器已实现、已登记、脚本游标逐项实测了命中 / 未命中 / 方言 skip / 查询异常。MySQL 系 5 项与 PostgreSQL 6 项另在本机 MariaDB 10.11 与 PostgreSQL 16.15 上执行过。Oracle 16 项、达梦 4 项、GBase 3 项没有可连接的实库，SQL 未在对应引擎上跑过。这是条件，不是把「函数可调用」写成通过。）

# DB-023 010B 补实现——下半 34 项交付报告

- **版本**：v1.0
- **任务单**：`docs/pm/orders/DB-023-010B补实现-下半.md`（v1.0）
- **执行人**：GorkMan
- **接单回执**：`docs/pm/reports/DB-023-ack-gorkman.md`（2026-11-10 23:17 America/Los_Angeles，commit `164a46d`）
- **报告成文时间**：2026-11-10 23:40（America/Los_Angeles，PST）
- **实现提交**：`5ec50b63f4e338d32bb2fb19f40b04d5a9fc4d9d`（变基到 DB-022 的 `342bb08` 之上）
- **代码**：`monitor/inspection_detectors_010b.py` 的 `Batch010bDetector`；`monitor/inspection_executor.py` 的 `_register_db023_detectors()` 在字面量之外 `update` 注册表。执行入口仍是 `InspectionExecutor._run_single`。

---

## 一、计数

| 口径 | 本单动手前 | 本单之后 |
|---|---:|---:|
| `DETECTOR_REGISTRY` 键数 | 57（DB-016 的 34 + DB-022 的 23，提交 `342bb08`） | **91** |
| 直接命中 `direct`（`ALL_ITEMS.item_id` 出现在注册表） | 34（原 11 + DB-022 的 23） | **68** |
| 本单新增键 / 新增 direct | — | **+34 / +34** |

评估基线（DB-020，当时还没有 DB-022）是 registry_keys 34、direct 11。DB-022 先落库，本单变基后没有改它的 23 个键，也没有挂 `INS-ORA-OPT-STATS-GATHER`、`INS-MYSQL-BINLOG-DELAY`。测试 `test_registry_counts_and_does_not_touch_existing_keys` 断言：基线键 57、总键 91、direct 68、两个待证项不在注册表、DB-022 的 23 个键仍在且不在本单字典里。

映射 `docs/pm/reports/evidence/DB-001/inspection-82-mapping.json` 的 summary 同步为 `registry_keys 91`、`direct 68`、`missing_impl_eval_batch2 0`。`tested` 按「tested 字段指向测试文件」重数是 65（原 8 + DB-022 的 23 + 本单 34）。DB-022 把 summary.tested 留在 8，和它自己行上的测试文件名不一致；这次按行重数，避免再留一个对不上的合计。两个可复用待证项的 `kind` 仍是「语义相近可复用」。

---

## 二、实现口径

- 方言不在声明列表里：返回 `skip`，摘要 `不适用当前方言: {db_type} (仅支持 ...)`，不调用 `get_connector`。
- 形态不适用（非 Data Guard、非 RAC、非 Exadata、非多节点 DSC、无 DataWatch、无实时 RLOG、主库没有 WAL receiver、GTID 未开、半同步未安装或未启用、GBase 节点数不足 2）：也是 `skip`，原因与方言 skip 分开写。查不到视图若表示「不是这种形态」（Exadata 的 `ORA-00942`），不当成故障。
- 查询抛错，或 GBase 节点/分布清单为空：状态 `error`，并 `degrade.note`。空清单不能写成均衡或「没有宕机」。
- 注册表阈值的 `error` 档不占用结果状态 `error`（那个状态表示检测过程失败）。三档记在 `metrics['threshold_band']`：`warn` / `error` / `critical`。结果状态只把 critical 档写成 `critical`，warn 与 error 档写成 `warning`。
- 连接沿用 `get_connector()`，即 `DbConnector.get_connection(..., readonly=True)`。010A 的 `require_permission(Perm.DATABASES_VIEW)` 与 `visible_to` 没有改。测试里零权限仍是 403，范围外实例仍是 404，且不产生 `InspectionRun`。

---

## 三、逐项：实现 → 登记 → 验证 → 不适用

登记列都是「已进入 `DETECTOR_REGISTRY`，键即 item_id」。脚本列都是 `monitor/tests_inspection_db023.py`：命中、未命中、方言 skip（不连接）、查询异常四条都跑过检测函数。下表「实库」只写本机真的执行过的结果。

### Oracle 16 项（实库：无。本环境没有 Oracle 监听，也没有纳管凭据）

| # | 规则 | 方法 | 判定对象 | 脚本 | 形态 skip |
|---:|---|---|---|---|---|
| 36 | `INS-ORA-LOB-PCT` | `detect_lob_pctversion` | `dba_lobs.pctversion > 10` 的个数，阈值 1/5/20 | 20 个 → critical；0 个 → ok | 仅 oracle |
| 37 | `INS-ORA-DBLINK` | `detect_dblinks` | `dba_db_links` 对照 `v$sql` 样本（上限 5000）里是否出现 `@链路名` | 100 个未见引用 → critical；0 个链路 → ok | 仅 oracle。样本窗外的 SQL 可能把仍在使用的链路判成残留 |
| 38 | `INS-ORA-DIR-OBJ` | `detect_directory_objects` | 非默认目录名，或路径含 `..` / 落在 `/tmp`、`/etc` 等 | 50 个 → critical；只有 `DATA_PUMP_DIR` → ok | 仅 oracle |
| 39 | `INS-ORA-RMAN-AGE` | `detect_rman_backup_age` | `v$rman_backup_job_details` 最近一次 `DB FULL` 且 `COMPLETED` 的小时数，阈值 24/48/72 | 80 小时 → critical；1 小时 → ok；空值 → critical「无已完成的 RMAN 全库备份」。不复用 I007 | 仅 oracle |
| 40 | `INS-ORA-DG-LAG` | `detect_dg_lag` | `v$dataguard_stats.apply lag` 解析成秒，阈值 30/300/1800。间隔字符串不直接 `int()` | `+00 00:40:00` → 2400 秒 critical；`+00 00:00:05` → ok | 非 Data Guard：skip「未配置 Data Guard」 |
| 41 | `INS-ORA-DG-STATUS` | `detect_dg_status` | 备库目的地 `v$archive_dest_status` 与备库 MRP | 目的地 ERROR → critical；VALID → ok | 同上 |
| 42 | `INS-ORA-RAC-VIP` | `detect_rac_vip` | `gv$instance` 与 `local_listener` 的 HOST。实例 status 不代替 VIP | 两个实例同一 HOST → critical；HOST 不同 → ok | `gv$instance` 不足 2：skip「非 RAC」 |
| 43 | `INS-ORA-REDOLOG-SIZE` | `detect_redolog_size` | `v$log.bytes` 的最小值（MB），越小越差，阈值 1024/512/200 | 100 MB → critical；2048 MB → ok。不读 `v$log_history` | 仅 oracle |
| 45 | `INS-ORA-OPEN-FILE` | `detect_open_files` | 先看 `v$resource_limit` 的 files/open_files；没有该资源时用数据文件+临时文件+日志+控制文件个数 | 60000 → critical；3 → ok。不把 processes/sessions 当成打开文件数 | 仅 oracle。字典合计通常远小于 10000，OS 文件描述符不在这些视图里 |
| 46 | `INS-ORA-LOB-CACHE` | `detect_lob_cache_hit` | `1 - physical reads direct (lob) / lob reads`，越小越差，阈值 50/30/10 | 5% → critical；90% → ok；无 LOB 读取 → ok 且写明没有样本 | 仅 oracle |
| 47 | `INS-ORA-ASH-USAGE` | `detect_ash_memory` | `v$sgastat` 的 ASH buffers / `_ash_size`，阈值 80/90/95。不用平台 ASH 采样 | 96% → critical；10% → ok；没有 ASH buffers 或读不到 `_ash_size` → error | 仅 oracle |
| 48 | `INS-ORA-RMAN-CONF` | `detect_rman_config` | `v$rman_configuration`：保留策略 NONE/0、优化未开、未配置压缩、设备类型异常、排除系统表空间。不把缺省 REDUNDANCY 1 当成故障，也不检查归档删除策略（那是 DB-022 的 `INS-ORA-ARCH-DELETE`） | 上述 5 项同时成立 → critical；优化开且压缩 BASIC、保留 REDUNDANCY 2 → ok | 仅 oracle |
| 49 | `INS-ORA-AUDIT-TRAIL` | `detect_audit_trail` | `audit_trail` 与统一审计。不用平台 `AuditLog`，也不用顾问规则表 | NONE 且统一审计未开 → critical；`DB` → ok | 仅 oracle |
| 50 | `INS-ORA-PGA-LEAK` | `detect_pga_leak` | `total PGA allocated / aggregate PGA target parameter`，阈值 80/95/110 | 120% → critical；10% → ok | target 为 0 且 `memory_target>0`：skip「由 memory_target 托管」 |
| 51 | `INS-ORA-IO-STAT` | `detect_io_distribution` | `v$filestat` 联 `v$datafile`。IO ≥ 2 倍均值的文件个数，阈值 1/5/10 | 10 个热点 → critical；均匀 → ok；总 IO 为 0 → ok 且写明没有样本。不读 `v$system_event` | 仅 oracle |
| 52 | `INS-ORA-EXADATA` | `detect_exadata_features` | 先 `v$cell`。有存储单元才看 `cell_offload_processing` 与 cell 是否 ONLINE | 5 个 OFFLINE 且 offload FALSE → critical；1 个 ONLINE 且 TRUE → ok | `ORA-00942` 或 `v$cell` 无行：skip「不是特性关闭」 |

### MySQL / TDSQL 5 项

脚本之外，在本机 MariaDB 10.11.14（127.0.0.1:3306，库 `dbaiops`）上执行。`db_type=tdsql` 走同一套 SQL，外键与临时表两项的实库结果与 mysql 相同，没有方言 skip。

| # | 规则 | 方法 | 判定对象 | 脚本 | 实库 |
|---:|---|---|---|---|---|
| 54 | `INS-MYSQL-GTID` | `detect_gtid_consistency` | `gtid_mode` 开启后，对比 `SHOW SLAVE STATUS` 的 Retrieved/Executed GTID，按区间做差。差集为 0 但 `Auto_Position=0` 计 1 | 差 6 个事务 → critical；集合一致且 Auto_Position=1 → ok | **skip**：MariaDB 10.11 没有 `gtid_mode`（只有 `gtid_current_pos` 等）。未在 MySQL 官方服务器上对比主从 GTID |
| 55 | `INS-MYSQL-SEMI-SYNC` | `detect_semi_sync` | 插件变量与 `Rpl_semi_sync_*_status` | 已启用且状态 OFF → critical；状态 ON → ok | 变量存在但全是 OFF → skip「半同步未启用」。把 `rpl_semi_sync_master_enabled` 设为 ON 后，状态也是 ON → ok「半同步已生效」。本机没有「已启用但状态 OFF」的实库样本 |
| 56 | `INS-MYSQL-EVENT` | `detect_event_scheduler` | `event_scheduler`；OFF/DISABLED 且仍有 ENABLED 事件则升到 critical | OFF 且 2 个启用事件 → critical；ON → ok | 先 OFF 且有 1 个事件 → critical；再 ON → ok「启用中的事件 1 个」 |
| 57 | `INS-MYSQL-FK-CASCADE` | `detect_dangerous_fk` | `information_schema.referential_constraints` 的 DELETE/UPDATE CASCADE 个数，阈值 5/20/50。不是 I108 的外键缺索引 | 50 个 → critical；0 个 → ok | 建了 5 个 `ON DELETE CASCADE` → warning（warn 档）。mysql 与 tdsql 两次都是 5 |
| 58 | `INS-MYSQL-TMP-DISK` | `detect_tmp_disk_ratio` | `Created_tmp_disk_tables / Created_tmp_tables * 100`，与采集器同一口径，阈值 25/50/75 | 80% → critical；1% → ok | 16.2%～16.5% → ok |

方言 skip：oracle/pgsql/dm/gbase。GTID 未开启、当前实例没有复制从库状态、半同步未安装或未启用，都是形态 skip。

### PostgreSQL 6 项

脚本之外，在本机 PostgreSQL 16.15（库 `db_monitor`）上执行。连接器会给会话加上 `statement_timeout`，检测器在 `source` 为 session/client/command line 时不采用这个值。

| # | 规则 | 方法 | 判定对象 | 脚本 | 实库 |
|---:|---|---|---|---|---|
| 59 | `INS-PG-WAL-RECV` | `detect_wal_receiver` | `pg_is_in_recovery()` 为真才读 `pg_stat_wal_receiver`。不读 `pg_stat_replication`，也不用 I008 的秒数阈值 | 备库无 receiver 行 → critical；streaming → ok | 主库 → skip「当前不是备库，无 WAL receiver」 |
| 60 | `INS-PG-INACTIVE-SLOT` | `detect_inactive_repl_slot` | `pg_replication_slots.active` 为 false 的个数，阈值 1/3/5 | 5 个 → critical；没有 → ok | 0 个 → ok；创建未使用的物理槽 `db023_unused` 后 1 个 → warning。测完已删除该槽 |
| 61 | `INS-PG-VACUUM-FREEZE` | `detect_freeze_age` | `age(relfrozenxid)` 的最大值，阈值 1e8/1.8e8/2e8。不用 `n_dead_tup` | 2e8 → critical；1000 → ok | 用户表 `db023_live` 的最大 age = 1 → ok |
| 62 | `INS-PG-BLOAT` | `detect_bloat` | `relpages` 与 `pg_stats` 平均宽度估算的膨胀率 > 50% 的对象个数，阈值 5/20/50。不是死元组比例，也不是按尺寸取大表 | 50 个 → critical；0 个 → ok | 首次 SQL 在 PG 16 上报 `round(double precision, integer)` 不存在，已改为 `::numeric` 后再跑：0 个对象超过 50% → ok |
| 63 | `INS-PG-EXT-OUTDATED` | `detect_extension_versions` | `pg_extension.extversion` 与 `pg_available_extensions.default_version` 不同的个数。默认版本为空的不计入过期 | 5 个不一致 → critical；一致 → ok；只有空默认版本 → ok 且写明不能证明过期 | 已安装扩展与默认版本一致 → ok |
| 64 | `INS-PG-STMT-TIMEOUT` | `detect_statement_timeout` | 配置文件或角色设置。会话注入值忽略 | 配置为 0 → critical；配置 30000 ms → ok；source=session 且文件和角色都没有 → critical，并记下被忽略的会话值 | 实库 source 被连接器注入，文件和角色均未设置 → critical「数据库与角色均未设置 statement_timeout」。没有把平台自己的超时当成应用已配置 |

方言 skip：非 pgsql。

### 达梦 4 项（实库：无。本环境没有达梦实例，也没有 dmPython）

对象沿用 DB-009 已经量过的列：`DSC_NODES` 在单实例上是 1，不用 `V$INSTANCE` 行数；归档看 `ARCH_MODE` 与 `V$ARCH_STATUS`；不把 `V$RLOG` 的偏移或水位当成同步进度；不把 PRIMARY 直接标成主备健康。

| # | 规则 | 方法 | 判定对象 | 脚本 | 形态 skip |
|---:|---|---|---|---|---|
| 65 | `INS-DM-DSC` | `detect_dsc_nodes` | `DSC_NODES>=2` 后读 `V$DSC_EP_INFO`（没有则试 `V$DSC_INSTANCES`）。明细失败或行数短于 DSC_NODES，不见的节点不算健康 | 3 个 DOWN → critical；2 个 OPEN → ok | `DSC_NODES<=1`：skip「非多节点 DSC」 |
| 66 | `INS-DM-DW-STANDBY` | `detect_dw_standby` | `V$ARCH_STATUS` 里非 LOCAL 的目的地。状态含 ERROR/INVALID 等为异常 | REALTIME+ERROR → critical；REALTIME+VALID → ok | 没有非本地目的地：skip「未配置 DataWatch，不把 PRIMARY 标成健康」 |
| 67 | `INS-DM-ARCH` | `detect_dm_arch` | `ARCH_MODE`。`N` 为未开启；`Y` 再看目的地与 `V$ARCH_FILE` 计数 | `ARCH_MODE=N` → critical；`Y` 且目的地 VALID → ok | 仅 dm。单实例也要查归档，这不是 skip |
| 68 | `INS-DM-RLOG` | `detect_dm_rlog` | 只看 `ARCH_TYPE` 为 REALTIME/TIMELY/RLOG 的 `V$ARCH_STATUS`。SQL 不含 `V$RLOG` | REALTIME+ERROR → critical；VALID → ok | 只有 LOCAL 或没有这类行：skip「无实时 RLOG 同步对象」 |

### GBase 3 项（实库：无。本环境没有 GBase）

| # | 规则 | 方法 | 判定对象 | 脚本 | 形态 skip / 空结果 |
|---:|---|---|---|---|---|
| 69 | `INS-GBASE-BALANCE` | `detect_gbase_node_balance` | `gcluster_v$node_info` 的 CPU_USED、MEM_USED 极差百分比，阈值 30/50/80 | 10 对 90 → critical；40 对 50 → ok | 节点数 &lt; 2：skip。0 行 → error「不能把空结果当成均衡」 |
| 70 | `INS-GBASE-DIST` | `detect_gbase_distribution` | `gcluster_v$table_distribution` 按节点汇总 `DATA_SIZE` 的极差。不用副本状态视图冒充分布 | 10 对 90 → critical；40 对 50 → ok | 同上。视图名是本项新写的；实库不存在时检测结果是 error，不是均衡 |
| 71 | `INS-GBASE-NODE-DOWN` | `detect_gbase_node_down` | 同一节点视图里状态不含 ONLINE/ACTIVE/HEALTHY/NORMAL 的个数，阈值 1/2/3 | 3 个 OFFLINE → critical；全 ONLINE → ok | 0 行 → error「不能把空结果当成无宕机」。单节点在线仍是检测，不 skip |

方言 skip：非 gbase。

---

## 四、验证证据

### 1. 脚本游标（34/34）

`scripts/validate.sh unit --json`（提交 `5ec50b6` 之后、含注册表计数断言）：

```json
{"mode":"unit","exit_code":0,"stages":[{"name":"红线扫描","status":"pass","duration_ms":75},{"name":"unit 测试","status":"pass","duration_ms":10711}],"git_rev":"5ec50b6"}
```

`ruff check ... --select F821,F601,F811`：All checks passed。

### 2. 提交前 `scripts/validate.sh all`（exit 0）

| 阶段 | 结果 | 耗时 |
|---|---|---:|
| 语法编译 | pass | 42ms |
| Django 系统检查 | pass | 377ms |
| 迁移漂移检查 | pass | 431ms |
| 依赖完整性 | pass | 346ms |
| 文档版本一致 | pass | 25ms |
| 全量测试 | pass | 18499ms（613 项，skipped=29，OK） |
| 前端构建 | pass | vite built in 846ms（门禁把收尾命令记成 3ms） |
| 前端测试 | pass | vitest 7 files / 68 tests |

前端构建与测试的退出码是 0。本单没有改前端源码。

### 3. 实库边界

本机没有 Docker，没有 `.env`，3306/5432 以外没有数据库监听。为 MySQL 系和 PostgreSQL 装了本机 MariaDB 10.11.14 与 PostgreSQL 16.15，用检测器自己的 `get_connector` 连上去跑。Oracle、达梦、GBase 没有可连的实例，不把脚本游标的结果写成这三种引擎的实库通过。

内网未动，没有索取凭据，没有重启采集器。

### 4. CI

代码推送后回填。见版本记录。

---

## 五、条件与未尽

1. Oracle 16 项、达梦 4 项、GBase 3 项的 SQL 没有在对应引擎执行。GBase 分布用的是 `gcluster_v$table_distribution`；若现场视图名不同，检测会 error，不会报成均衡。
2. GTID 一致性没有 MySQL 官方服务器上的主从差集实测。MariaDB 无 `gtid_mode`，实库结果是 skip。
3. 半同步「已配置但未生效」只在脚本游标里打到 critical。MariaDB 上开关打开后状态即为 ON。
4. DBLINK「未使用」只对照库缓存里最多 5000 条含 `@` 的 SQL。
5. 打开文件数在没有 `v$resource_limit.files` 时退回数据文件合计，到不了 10000 的常见 OS 描述符量级。
6. 膨胀率是页数与统计宽度的估算，不是 `pgstattuple`。
7. 阈值 error 档在巡检 Run 的计数里落在 warning。`metrics.threshold_band` 仍区分 warn 与 error。

不需要 PM 改派。若要把结论从「有条件通过」改成「通过」，需要补 Oracle / 达梦 / GBase 实库，以及一台有 `gtid_mode` 的 MySQL 主从。本单不挂两个待证别名。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-10 | GorkMan | 交付报告。时间为 America/Los_Angeles。CI 结果待推送后回填 |
