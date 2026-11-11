| # | 定义 item_id | 标题 | 级别 | 适用方言 | 声明函数 | 同名实现类 | 注册键命中 | 测试引用 | 分层判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `INS-COM-INVALID-OBJ` | 无效对象检查 | daily | oracle,dm,gbase | `detect_invalid_objects` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 2 | `INS-COM-RECYCLEBIN` | 回收站清理检查 | weekly | oracle,dm | `detect_recyclebin` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 3 | `INS-COM-USER-LOCKED` | 账号锁定检查 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_locked_users` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 4 | `INS-COM-PWD-EXPIRE` | 密码即将过期 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_expiring_passwords` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 5 | `INS-COM-CAP-7D` | 7天容量预测 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_7d_capacity` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 6 | `INS-COM-CAP-30D` | 30天容量预测 | weekly | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_30d_capacity` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 7 | `INS-COM-CONN-PEAK` | 连接峰值检查 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_conn_peak` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_connections_usage)** |
| 8 | `INS-COM-LONG-TXN` | 长事务检查 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_long_transactions` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 9 | `INS-COM-DEADLOCK-24H` | 24h 死锁次数 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_deadlock_count` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 10 | `INS-COM-SLOW-LOG` | 慢查询日志检查 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_slow_query_log` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_slow_query_burst)** |
| 11 | `INS-COM-INDEX-USAGE` | 索引使用率检查 | weekly | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_unused_indexes` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_index_usage)** |
| 12 | `INS-COM-MISSING-INDEX` | 缺失索引检查 | weekly | oracle,mysql,pgsql,dm | `detect_missing_indexes` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 13 | `INS-COM-FK-NO-INDEX` | 外键无索引 | weekly | oracle,mysql,pgsql,dm | `detect_fk_no_index` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 14 | `INS-COM-PART-NEEDED` | 大表未分区 | monthly | oracle,mysql,pgsql,dm,gbase | `detect_large_table_not_partitioned` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 15 | `INS-COM-STAT-STALE` | 统计信息过期 | weekly | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_stale_stats` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 16 | `INS-COM-SPARSE-INDEX` | 稀疏索引 | monthly | oracle,mysql,pgsql,dm | `detect_sparse_index` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 17 | `INS-COM-BLEVEL-HIGH` | 高 BLEVEL 索引 | monthly | oracle,mysql,pgsql,dm | `detect_high_blevel_index` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_index_health)** |
| 18 | `INS-COM-SEQ-MAX` | 序列接近 MAX | daily | oracle,dm,pgsql | `detect_seq_near_max` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_sequence_usage)** |
| 19 | `INS-COM-SEQ-NO-CACHE` | 序列无 CACHE | monthly | oracle,dm | `detect_seq_no_cache` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_sequence_usage)** |
| 20 | `INS-COM-PARAM-DEV` | 参数偏离最佳实践 | monthly | oracle,mysql,pgsql,dm,gbase | `detect_param_deviation` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 21 | `INS-COM-BACKUP` | 备份状态 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_backup_status` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 22 | `INS-COM-ARCH-GAP` | 归档间隙 | daily | oracle,dm | `detect_archive_gap` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 23 | `INS-COM-LOG-SWITCH` | 日志切换频率 | daily | oracle,dm | `detect_log_switches` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_log_switch_freq)** |
| 24 | `INS-COM-LOCK-WAIT` | 当前锁等待 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_lock_waits` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 25 | `INS-COM-BLOCK-SESSION` | 被阻塞会话 | daily | oracle,mysql,pgsql,dm,gbase,tdsql | `detect_blocked_sessions` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 26 | `INS-COM-OPEN-CURSOR` | 打开游标数 | daily | oracle,dm | `detect_open_cursors` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 27 | `INS-COM-TEMP-USAGE` | 临时表空间使用 | daily | oracle,mysql,pgsql,dm,gbase | `detect_temp_usage` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 28 | `INS-COM-UNDO-USAGE` | Undo 使用率 | daily | oracle,dm,pgsql | `detect_undo_usage` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 29 | `INS-COM-FRAG-TABLE` | 高并行度表 | monthly | oracle,dm,gbase | `detect_high_parallel` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_table_fragmentation)** |
| 30 | `INS-COM-FAILED-LOGIN` | 失败登录异常 | daily | oracle,mysql,pgsql,dm | `detect_failed_logins` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 31 | `INS-ORA-AWR-SNAP` | AWR 快照间隔 | weekly | oracle | `detect_awr_interval` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_awr_config)** |
| 32 | `INS-ORA-AWR-RET` | AWR 保留周期 | monthly | oracle | `detect_awr_retention` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_awr_config)** |
| 33 | `INS-ORA-AWR-TOP-EVT` | 业务高峰 Top Event | weekly | oracle | `detect_awr_top_event` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 34 | `INS-ORA-AWR-TOP-SQL` | 业务高峰 Top SQL | weekly | oracle | `detect_awr_top_sql` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 35 | `INS-ORA-SCN-HEADROOM` | SCN Headroom | daily | oracle | `detect_scn_headroom` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 36 | `INS-ORA-STA-OUTPUT` | SQL Tuning Advisor 结果 | weekly | oracle | `detect_sta_recommendations` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 37 | `INS-ORA-SEG-ADV` | Segment Advisor 结果 | weekly | oracle | `detect_segment_advisor` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 38 | `INS-ORA-AUTO-MW` | 自动维护窗口 | monthly | oracle | `detect_maintenance_window` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_auto_task_status)** |
| 39 | `INS-ORA-FRA` | FRA 使用率 | daily | oracle | `detect_fra_usage` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 40 | `INS-ORA-CTRL-BAK` | 控制文件备份 | weekly | oracle | `detect_ctrlfile_backup` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 41 | `INS-ORA-ARCH-DELETE` | 归档日志清理策略 | weekly | oracle | `detect_arch_deletion_policy` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 42 | `INS-ORA-DBA-USERS` | DBA 权限账号 | monthly | oracle | `detect_dba_users` | GenericDetector | 是 | tests_inspection_scope.py | **直接命中** |
| 43 | `INS-ORA-TS-AUTOEXT` | 表空间自动扩展 | monthly | oracle,dm | `detect_ts_autoextend` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_disk_space)** |
| 44 | `INS-ORA-LOB-PCT` | LOB PCTVERSION 异常 | monthly | oracle | `detect_lob_pctversion` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 45 | `INS-ORA-DBLINK` | 残留 DBLINK | monthly | oracle | `detect_dblinks` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 46 | `INS-ORA-DIR-OBJ` | Directory 对象 | monthly | oracle | `detect_directory_objects` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 47 | `INS-ORA-RMAN-AGE` | RMAN 备份距今 | daily | oracle | `detect_rman_backup_age` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 48 | `INS-ORA-DG-LAG` | DataGuard Lag | daily | oracle | `detect_dg_lag` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 49 | `INS-ORA-DG-STATUS` | DataGuard 状态 | daily | oracle | `detect_dg_status` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 50 | `INS-ORA-RAC-VIP` | RAC VIP 状态 | daily | oracle | `detect_rac_vip` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 51 | `INS-ORA-REDOLOG-SIZE` | Redo Log 大小 | monthly | oracle | `detect_redolog_size` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 52 | `INS-ORA-OPT-STATS-GATHER` | 自动统计信息收集 | weekly | oracle | `detect_auto_stats_gather` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_auto_task_status)** |
| 53 | `INS-ORA-OPEN-FILE` | 打开文件数 | daily | oracle | `detect_open_files` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 54 | `INS-ORA-LOB-CACHE` | LOB CACHE 读取 | monthly | oracle | `detect_lob_cache_hit` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 55 | `INS-ORA-ASH-USAGE` | ASH 内存使用 | monthly | oracle | `detect_ash_memory` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 56 | `INS-ORA-RMAN-CONF` | RMAN 配置 | monthly | oracle | `detect_rman_config` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 57 | `INS-ORA-AUDIT-TRAIL` | 审计日志 | weekly | oracle | `detect_audit_trail` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 58 | `INS-ORA-PGA-LEAK` | PGA 泄漏 | monthly | oracle | `detect_pga_leak` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 59 | `INS-ORA-IO-STAT` | IO 分布 | monthly | oracle | `detect_io_distribution` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 60 | `INS-ORA-EXADATA` | Exadata 特性 | monthly | oracle | `detect_exadata_features` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 61 | `INS-MYSQL-BINLOG-DELAY` | Binlog Dump 延迟 | daily | mysql,tdsql | `detect_binlog_dump_delay` | GenericDetector | 否 | 无测试引用 | **语义相近可复用 (GenericDetector.detect_replication_lag)** |
| 62 | `INS-MYSQL-GTID` | GTID 一致性 | weekly | mysql,tdsql | `detect_gtid_consistency` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 63 | `INS-MYSQL-SEMI-SYNC` | 半同步复制状态 | daily | mysql,tdsql | `detect_semi_sync` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 64 | `INS-MYSQL-EVENT` | Event 调度器 | weekly | mysql,tdsql | `detect_event_scheduler` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 65 | `INS-MYSQL-FK-CASCADE` | 危险外键级联 | monthly | mysql,tdsql | `detect_dangerous_fk` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 66 | `INS-MYSQL-TMP-DISK` | 磁盘临时表比例 | daily | mysql,tdsql | `detect_tmp_disk_ratio` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 67 | `INS-PG-WAL-RECV` | WAL Receiver 状态 | daily | pgsql | `detect_wal_receiver` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 68 | `INS-PG-INACTIVE-SLOT` | 失效复制槽 | daily | pgsql | `detect_inactive_repl_slot` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 69 | `INS-PG-VACUUM-FREEZE` | Freeze Age | daily | pgsql | `detect_freeze_age` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 70 | `INS-PG-BLOAT` | 表/索引膨胀 | weekly | pgsql | `detect_bloat` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 71 | `INS-PG-EXT-OUTDATED` | 扩展版本 | monthly | pgsql | `detect_extension_versions` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 72 | `INS-PG-STMT-TIMEOUT` | Statement Timeout | monthly | pgsql | `detect_statement_timeout` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 73 | `INS-DM-DSC` | DSC 节点状态 | daily | dm | `detect_dsc_nodes` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 74 | `INS-DM-DW-STANDBY` | 主备同步状态 | daily | dm | `detect_dw_standby` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 75 | `INS-DM-ARCH` | 归档状态 | daily | dm | `detect_dm_arch` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 76 | `INS-DM-RLOG` | RLOG 同步状态 | daily | dm | `detect_dm_rlog` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 77 | `INS-GBASE-BALANCE` | 节点负载均衡 | daily | gbase | `detect_gbase_node_balance` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 78 | `INS-GBASE-DIST` | 数据分布均衡 | weekly | gbase | `detect_gbase_distribution` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 79 | `INS-GBASE-NODE-DOWN` | 节点宕机 | daily | gbase | `detect_gbase_node_down` | Batch010bDetector | 是 | tests_inspection_db023.py | **直接命中** |
| 80 | `INS-TDSQL-SHARD` | Set 主备健康 | daily | tdsql | `detect_tdsql_set_health` | TDSQLDetector | 是 | 无测试引用 | **直接命中** |
| 81 | `INS-TDSQL-PROXY` | Proxy 网关状态 | daily | tdsql | `detect_tdsql_proxy` | TDSQLDetector | 是 | 无测试引用 | **直接命中** |
| 82 | `INS-TDSQL-SHARDKEY` | 分片表配置 | daily | tdsql | `detect_tdsql_shardkey` | TDSQLDetector | 是 | 无测试引用 | **直接命中** |
