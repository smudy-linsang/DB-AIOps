# 数据库监控指标体系设计文档

## 1. 文档目标

本文档定义 DB-AIOps 监控系统对目标数据库的完整采集指标体系。指标设计的核心原则：**采集指标的丰富程度决定了 DBA 在做判断和决策时的准确度**。

---

## 2. 指标分类总览

### 2.1 指标大类

| 类别 | 代码标识 | 说明 | 优先级 |
|------|----------|------|--------|
| **基础信息** | `basic` | 数据库版本、实例名、运行时间等 | P0 |
| **连接与会话** | `session` | 连接数、会话状态、活跃度 | P0 |
| **空间使用** | `space` | 表空间、数据文件、存储使用率 | P0 |
| **性能指标** | `performance` | QPS、TPS、响应时间、吞吐量 | P0 |
| **等待事件** | `wait` | 等待事件统计、锁等待 | P1 |
| **会话详情** | `session_detail` | 详细会话信息、阻塞关系 | P1 |
| **SQL统计** | `sql` | 慢查询、Top SQL、高负载SQL | P1 |
| **对象统计** | `object` | 表、索引、分区统计信息 | P2 |
| **复制与集群** | `replication` | 主从复制、集群状态 | P1 |
| **配置参数** | `config` | 关键配置项当前值 | P2 |
| **缓冲池** | `buffer` | 缓存命中率、缓冲池使用 | P1 |
| **事务统计** | `transaction` | 事务数、回滚/提交比 | P2 |
| **日志统计** | `log` | 日志产生量、归档状态 | P1 |
| **安全审计** | `security` | 登录失败、权限变更 | P2 |
| **高可用** | `ha` | 故障切换、节点状态 | P1 |
| **资源限制** | `resource` | 配额、限制使用情况 | P2 |

---

## 3. 各数据库当前实现状态

| 数据库 | Oracle | MySQL | PostgreSQL | DM8 | Gbase8a | TDSQL |
|--------|--------|-------|------------|-----|---------|-------|
| **基础信息** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **连接会话** | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **空间使用** | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **性能指标** | ⚠️ QPS | ⚠️ QPS | ❌ | ❌ | ❌ | ❌ |
| **等待事件** | ⚠️ 锁 | ⚠️ 锁 | ⚠️ 锁 | ❌ | ❌ | ❌ |
| **会话详情** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **SQL统计** | ⚠️ 慢查询 | ⚠️ 慢查询 | ⚠️ 慢查询 | ❌ | ❌ | ❌ |
| **对象统计** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **复制集群** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **配置参数** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **缓冲池** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **事务统计** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **日志统计** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **安全审计** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **高可用** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **资源限制** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

**图例**: ✅ 已实现  ⚠️ 部分实现  ❌ 未实现

---

## 4. Oracle 完整指标清单

### 4.1 基础信息 (basic)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `version` | 数据库版本 | `v$version` | ✅ |
| `instance_name` | 实例名 | `v$instance` | ✅ |
| `db_name` | 数据库名 | `v$database` | ❌ |
| `db_unique_name` | 数据库唯一名 | `v$database` | ❌ |
| `open_mode` | 打开模式 | `v$database` | ❌ |
| `log_mode` | 归档模式 | `v$database` | ❌ |
| `uptime_seconds` | 运行时间(秒) | `v$instance` | ✅ |
| `host_name` | 主机名 | `v$instance` | ❌ |

### 4.2 连接与会话 (session)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `active_sessions` | 活跃会话数 | `gv$session WHERE status='ACTIVE'` | ✅ |
| `inactive_sessions` | 空闲会话数 | `gv$session WHERE status='INACTIVE'` | ❌ |
| `total_sessions` |总会话数 | `gv$session` | ❌ |
| `max_connections` | 最大连接数 | `v$parameter WHERE name='processes'` | ✅ |
| `conn_usage_pct` | 连接使用率 | 计算值 | ✅ |
| `session_limit_usage` | 会话限制使用率 | `v$resource_limit` | ❌ |
| `background_sessions` | 后台会话数 | `gv$session WHERE type='BACKGROUND'` | ❌ |
| `oracle_dedicated_servers` | 专用服务器数 | `v$circuit` | ❌ |

### 4.3 空间使用 (space)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `tablespaces` | 表空间列表(名称、总空间、已用空间、使用率) | `dba_data_files + dba_free_space` | ✅ |
| `temp_tablespaces` | 临时表空间 | `dba_temp_files + v$sort_usage` | ❌ |
| `undo_tablespaces` | UNDO表空间 | `dbaUNDO_tablespaces` | ❌ |
| `datafile_count` | 数据文件数量 | `dba_data_files` | ❌ |
| `datafile_size_total` | 数据文件总大小(GB) | `dba_data_files` | ❌ |
| `archived_logs_size` | 归档日志大小/天 | `v$archived_log` | ❌ |
| `controlfile_size` | 控制文件大小 | `v$controlfile` | ❌ |
| `FRA_used_pct` | 快速恢复区使用率 | `v$flash_recovery_area_usage` | ❌ |

### 4.4 性能指标 (performance)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `qps` | 每秒SQL执行次数 | `v$sysstat WHERE name='execute count'` | ✅ |
| `tps` | 每秒事务数 | `v$sysstat WHERE name IN ('user commits','user rollbacks')` | ❌ |
| `logical_reads` | 逻辑读次数/秒 | `v$sysstat` | ❌ |
| `physical_reads` | 物理读次数/秒 | `v$sysstat` | ❌ |
| `physical_writes` | 物理写次数/秒 | `v$sysstat` | ❌ |
| `redo_generation_bytes` | Redo产生速率(bytes/s) | `v$sysstat` | ❌ |
| `parse_count_total` | 总解析次数 | `v$sysstat` | ❌ |
| `parse_count_hard` | 硬解析次数 | `v$sysstat` | ❌ |
| `parse_count_soft` | 软解析次数 | `v$sysstat` | ❌ |
| `executions_count` | SQL执行总次数 | `v$sql` | ❌ |
| `buffer_hit_ratio` | 缓冲池命中率 | `v$buffer_pool_statistics` | ❌ |
| `library_cache_hit_ratio` | 库缓存命中率 | `v$librarycache` | ❌ |
| `cpu_used_seconds` | CPU使用时间(秒) | `v$sys_time_model` | ❌ |
| `db_time_seconds` | 数据库时间(秒) | `v$sys_time_model` | ❌ |

### 4.5 等待事件 (wait)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `locks` | 锁等待列表 | `gv$session` | ✅ |
| `lock_wait_count` | 当前锁等待数 | `gv$session WHERE blocking_session IS NOT NULL` | ❌ |
| `top_wait_events` | Top 10等待事件 | `v$system_event` | ❌ |
| `enqueue_waits` | Enqueue等待次数 | `v$system_event` | ❌ |
| `latch_waits` | Latch等待次数 | `v$latch` | ❌ |
| `buffer_busy_waits` | 缓冲区忙等待 | `v$system_event` | ❌ |
| `db_file_scattered_read` | 离散读等待(全表扫描) | `v$system_event` | ❌ |
| `db_file_sequential_read` | 顺序读等待(索引读取) | `v$system_event` | ❌ |
| `log_file_sync` | 日志文件同步等待 | `v$system_event` | ❌ |

### 4.6 会话详情 (session_detail)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `session_list` | 完整会话列表 | `gv$session` | ❌ |
| `session_waits` | 会话当前等待 | `gv$session_wait` | ❌ |
| `session_program` | 会话使用的程序 | `gv$session` | ❌ |
| `session_machine` | 会话来源机器 | `gv$session` | ❌ |
| `session_event` | 会话等待事件详情 | `gv$session_event` | ❌ |
| `blocked_sessions` | 被阻塞的会话 | `gv$session` | ❌ |
| `session_sql_text` | 会话执行的SQL | `gv$session + gv$sql` | ❌ |

### 4.7 SQL统计 (sql)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `slow_queries_active` | 当前慢查询数 | `gv$session` | ✅ |
| `top_sql_by_buffer_gets` | 按逻辑读Top SQL | `v$sql` | ❌ |
| `top_sql_by_disk_reads` | 按物理读Top SQL | `v$sql` | ❌ |
| `top_sql_by_executions` | 按执行次数Top SQL | `v$sql` | ❌ |
| `top_sql_by_elapsed_time` | 按耗时Top SQL | `v$sql` | ❌ |
| `sql_with_full_table_scans` | 全表扫描SQL | `v$sql` | ❌ |
| `sql_with_high_version_count` | 高版本计数SQL | `v$sql` | ❌ |
| `sql_plan_baseline` | 执行计划基线 | `dba_sql_plan_baselines` | ❌ |

### 4.8 对象统计 (object)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `table_count` | 表数量 | `dba_tables` | ❌ |
| `index_count` | 索引数量 | `dba_indexes` | ❌ |
| `table_size_top20` | Top 20表大小(GB) | `dba_segments` | ❌ |
| `index_size_top20` | Top 20索引大小(GB) | `dba_segments` | ❌ |
| `table_fragmentation` | 表碎片率 | `dba_tab_statistics` | ❌ |
| `index_usage_stats` | 索引使用统计 | `dba_index_usage_statistics` | ❌ |
| `stale_statistics` | 统计信息过期对象 | `dba_tab_stale_statistics` | ❌ |
| `chained_rows` | 表行链接数 | `dba_chained_rows` | ❌ |

### 4.9 复制与集群 (replication)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `rac_instances` | RAC实例数 | `gv$instance` | ❌ |
| `rac_current_instance` | 当前实例号 | `v$instance` | ❌ |
| `rac_interconnect` | 互联网络流量 | `gv$cluster_interconnect` | ❌ |
| `dg_role` | DataGuard角色 | `v$database` | ❌ |
| `dg_apply_lag` |  Apply延迟(秒) | `v$archive_dest_status` | ❌ |
| `dg_transport_lag` | 传输延迟(秒) | `v$archive_dest_status` | ❌ |
| `dg_gap` | 日志Gap数 | `v$archive_gap` | ❌ |

### 4.10 配置参数 (config)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `sga_target` | SGA目标大小 | `v$sga` | ❌ |
| `sga_max_size` | SGA最大大小 | `v$sga` | ❌ |
| `pga_aggregate_target` | PGA目标大小 | `v$parameter` | ❌ |
| `shared_pool_size` | 共享池大小 | `v$sga` | ❌ |
| `db_cache_size` | 缓冲池大小 | `v$sga` | ❌ |
| `log_buffer` | 日志缓冲区大小 | `v$parameter` | ❌ |
| `open_cursors` | 打开游标数 | `v$parameter` | ❌ |
| `session_cached_cursors` | 会话缓存游标 | `v$parameter` | ❌ |

### 4.11 缓冲池 (buffer)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `buffer_pool_size` | 缓冲池总大小 | `v$buffer_pool` | ❌ |
| `buffer_busy_wait_ratio` | 缓冲忙等待率 | `v$system_event` | ❌ |
| `cache_hit_ratio` | 缓存命中率 | `v$buffer_pool_statistics` | ❌ |
| `physical_reads_disabled` | 禁用物理读数 | `v$instance_cache_advice` | ❌ |

### 4.12 事务统计 (transaction)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `transaction_count` | 当前事务数 | `v$transaction` | ❌ |
| `committed_transactions` | 已提交事务数 | `v$sysstat` | ❌ |
| `rolled_back_transactions` | 已回滚事务数 | `v$sysstat` | ❌ |
| `commit_ratio` | 提交率 | 计算值 | ❌ |
| ` flashback_size` | Flashback日志大小 | `v$flash_recovery_area_usage` | ❌ |

### 4.13 日志统计 (log)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `redo_log_switches` | 日志切换次数/天 | `v$log_history` | ❌ |
| `archive_log_count` | 归档日志数量 | `v$archived_log` | ❌ |
| `archive_dest_status` | 归档目的地状态 | `v$archive_dest_status` | ❌ |
| `alert_log_errors` | 告警日志错误数 | `x$dbgalertext` | ❌ |

### 4.14 安全审计 (security)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `failed_login_count` | 登录失败次数 | `dbaaudit_trail` | ❌ |
| `privilege_usage` | 权限使用审计 | `dba_priv_audit_opts` | ❌ |
| `session_failure_count` | 会话失败次数 | `aud$` | ❌ |

### 4.15 高可用 (ha)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `dataguard_role` | DataGuard角色 | `v$database` | ❌ |
| `flashback_on` | Flashback是否启用 | `v$database` | ❌ |
| `protection_mode` | 保护模式 | `v$database` | ❌ |
| `protection_level` | 保护级别 | `v$database` | ❌ |

### 4.16 资源限制 (resource)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `process_usage` | 进程使用情况 | `v$resource_limit` | ❌ |
| `session_usage` | 会话使用情况 | `v$resource_limit` | ❌ |
| `parallel_max_servers` | 最大并行服务器 | `v$parameter` | ❌ |

---

## 5. MySQL 完整指标清单

### 5.1 基础信息 (basic)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `version` | MySQL版本 | `SELECT VERSION()` | ✅ |
| `server_id` | 服务器ID | `@@server_id` | ❌ |
| `datadir` | 数据目录 | `@@datadir` | ❌ |
| `port` | 端口 | `@@port` | ❌ |
| `socket` | Socket路径 | `@@socket` | ❌ |
| `uptime_seconds` | 运行时间 | `SHOW GLOBAL STATUS LIKE 'Uptime'` | ✅ |
| `running_threads` | 运行的线程数 | `SHOW GLOBAL STATUS LIKE 'Threads_running'` | ❌ |

### 5.2 连接与会话 (session)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `threads_connected` | 当前连接数 | `SHOW GLOBAL STATUS 'Threads_connected'` | ✅ |
| `threads_running` | 活跃线程数 | `SHOW GLOBAL STATUS 'Threads_running'` | ✅ |
| `threads_cached` | 缓存线程数 | `SHOW GLOBAL STATUS 'Threads_cached'` | ❌ |
| `max_connections` | 最大连接数 | `SHOW VARIABLES 'max_connections'` | ✅ |
| `conn_usage_pct` | 连接使用率 | 计算值 | ✅ |
| `connection_errors_max_connections` | 连接超限错误 | `SHOW GLOBAL STATUS` | ❌ |
| `connection_errors_internal` | 内部连接错误 | `SHOW GLOBAL STATUS` | ❌ |
| `aborted_connects` | 中止的连接数 | `SHOW GLOBAL STATUS 'Aborted_connects'` | ❌ |
| `aborted_clients` | 中止的客户端数 | `SHOW GLOBAL STATUS 'Aborted_clients'` | ❌ |

### 5.3 空间使用 (space)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `database_sizes` | 各数据库大小(MB) | `information_schema.tables` | ✅ |
| `innodb_file_per_table` | 独立表空间模式 | `@@innodb_file_per_table` | ❌ |
| `innodb_data_path` | InnoDB数据路径 | `@@innodb_data_home_dir` | ❌ |
| `innodb_tablespaces` | InnoDB表空间 | `information_schema.innodb_tablespaces` | ❌ |
| `innodb_temp_size` | 临时表空间大小 | `information_schema.innodb_temp` | ❌ |
| `binlog_space` | Binlog总大小 | `SHOW MASTER LOGS` | ❌ |
| `relaylog_space` | Relaylog总大小 | `SHOW SLAVE STATUS` | ❌ |
| `undo_space` | Undo表空间大小 | `information_schema.innodb_tablespaces` | ❌ |

### 5.4 性能指标 (performance)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `qps` | 每秒查询数 | `Questions / Uptime` | ✅ |
| `tps` | 每秒事务数 | `(Com_commit + Com_rollback) / Uptime` | ❌ |
| `tps_read_write` | 读写 TPS | 计算值 | ❌ |
| `tps_read_only` | 只读 TPS | 计算值 | ❌ |
| `key_read_requests` | 键读请求数 | `SHOW GLOBAL STATUS 'Key_read_requests'` | ❌ |
| `key_reads` | 键读物理IO | `SHOW GLOBAL STATUS 'Key_reads'` | ❌ |
| `key_write_requests` | 键写请求数 | `SHOW GLOBAL STATUS 'Key_write_requests'` | ❌ |
| `key_writes` | 键写物理IO | `SHOW GLOBAL STATUS 'Key_writes'` | ❌ |
| `key_buffer_usage` | 键缓存使用率 | 1 - key_blocks_unused * key_cache_block_size / key_buffer_size | ❌ |
| `innodb_rows_read` | InnoDB行读取数 | `SHOW GLOBAL STATUS 'Innodb_rows_read'` | ❌ |
| `innodb_rows_inserted` | InnoDB行插入数 | `SHOW GLOBAL STATUS 'Innodb_rows_inserted'` | ❌ |
| `innodb_rows_updated` | InnoDB行更新数 | `SHOW GLOBAL STATUS 'Innodb_rows_updated'` | ❌ |
| `innodb_rows_deleted` | InnoDB行删除数 | `SHOW GLOBAL STATUS 'Innodb_rows_deleted'` | ❌ |
| `innodb_buffer_pool_reads` | 缓冲池物理读 | `SHOW GLOBAL STATUS 'Innodb_buffer_pool_reads'` | ❌ |
| `innodb_buffer_pool_read_requests` | 缓冲池逻辑读 | `SHOW GLOBAL STATUS 'Innodb_buffer_pool_read_requests'` | ❌ |
| `innodb_buffer_pool_pages_total` | 缓冲池总页数 | `SHOW GLOBAL STATUS 'Innodb_buffer_pool_pages_total'` | ❌ |
| `innodb_buffer_pool_pages_free` | 缓冲池空闲页 | `SHOW GLOBAL STATUS 'Innodb_buffer_pool_pages_free'` | ❌ |
| `innodb_buffer_pool_hit_ratio` | 缓冲池命中率 | 1 - reads / read_requests | ❌ |
| `table_open_cache` | 表缓存大小 | `@@table_open_cache` | ❌ |
| `table_open_cache_hits` | 表缓存命中 | `SHOW GLOBAL STATUS 'Table_open_cache_hits'` | ❌ |
| `table_open_cache_misses` | 表缓存未命中 | `SHOW GLOBAL STATUS 'Table_open_cache_misses'` | ❌ |
| `opened_files` | 已打开文件数 | `SHOW GLOBAL STATUS 'Opened_files'` | ❌ |
| `open_files_limit` | 打开文件限制 | `SHOW VARIABLES 'open_files_limit'` | ❌ |

### 5.5 等待事件 (wait)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `locks` | 锁等待列表 | `performance_schema.data_lock_waits` | ✅ |
| `lock_waits` | 锁等待总次数 | `SHOW GLOBAL STATUS 'Innodb_row_lock_waits'` | ❌ |
| `lock_time_avg` | 平均锁等待时间(ms) | `information_schema.innodb_metrics` | ❌ |
| `lock_current_waits` | 当前锁等待数 | `information_schema.innodb_trx` | ❌ |
| `table_locks_immediate` | 立即获得的表锁 | `SHOW GLOBAL STATUS 'Table_locks_immediate'` | ❌ |
| `table_locks_waited` | 等待的表锁 | `SHOW GLOBAL STATUS 'Table_locks_waited'` | ❌ |

### 5.6 会话详情 (session_detail)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `processlist` | 完整进程列表 | `SHOW PROCESSLIST` | ❌ |
| `processlist_count` | 各状态进程数 | `information_schema.PROCESSLIST` | ❌ |
| `full_processlist` | 完整信息进程列表 | `performance_schema.events_statements_current` | ❌ |
| `session_variables` | 会话变量 | `SHOW SESSION VARIABLES` | ❌ |

### 5.7 SQL统计 (sql)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `slow_queries_total` | 慢查询总数 | `SHOW GLOBAL STATUS 'Slow_queries'` | ✅ |
| `long_query_time` | 慢查询阈值(秒) | `SHOW VARIABLES 'long_query_time'` | ✅ |
| `top_sql_by_latency` | 按延迟Top SQL | `performance_schema.events_statements_summary_by_digest` | ❌ |
| `top_sql_by_exec_count` | 按执行次数Top SQL | `performance_schema.events_statements_summary_by_digest` | ❌ |
| `top_sql_by_rows_examined` | 按扫描行数Top SQL | `performance_schema.events_statements_summary_by_digest` | ❌ |
| `full_table_scans` | 全表扫描SQL统计 | `performance_schema.events_statements_summary_by_digest` | ❌ |
| `prepared_stmt_count` | 预处理语句数 | `SHOW GLOBAL STATUS 'Prepared_stmt_count'` | ❌ |
| `com_*_counter` | 各类型SQL计数 | `SHOW GLOBAL STATUS LIKE 'Com_%'` | ❌ |

### 5.8 对象统计 (object)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `table_count` | 表总数 | `information_schema.TABLES` | ❌ |
| `table_size_top20` | Top 20表大小(MB) | `information_schema.tables` | ❌ |
| `index_count` | 索引总数 | `information_schema.statistics` | ❌ |
| `index_size_total` | 索引总大小(MB) | `information_schema.tables` | ❌ |
| `table_fragmentation_pct` | 表碎片率 | 计算值 | ❌ |
| `unused_indexes` | 未使用索引 | `performance_schema.table_io_waits_summary_by_index_usage` | ❌ |
| `redundant_indexes` | 冗余索引 | `performance_schema.table_io_waits_summary_by_index_usage` | ❌ |

### 5.9 复制与集群 (replication)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `binlog_format` | Binlog格式 | `@@binlog_format` | ❌ |
| `binlog_position` | Binlog位置 | `SHOW MASTER STATUS` | ❌ |
| `slave_io_running` | Slave IO运行状态 | `SHOW SLAVE STATUS` | ❌ |
| `slave_sql_running` | Slave SQL运行状态 | `SHOW SLAVE STATUS` | ❌ |
| `seconds_behind_master` | 复制延迟(秒) | `SHOW SLAVE STATUS` | ❌ |
| `relay_log_space` | Relaylog空间 | `SHOW SLAVE STATUS` | ❌ |
| `slave_last_error` | 从机最后错误 | `SHOW SLAVE STATUS` | ❌ |
| `gtid_mode` | GTID模式 | `@@gtid_mode` | ❌ |
| `group_replication` | 组复制状态 | `performance_schema.replication_group_members` | ❌ |

### 5.10 配置参数 (config)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `innodb_buffer_pool_size` | 缓冲池大小 | `@@innodb_buffer_pool_size` | ❌ |
| `innodb_log_file_size` | 日志文件大小 | `@@innodb_log_file_size` | ❌ |
| `innodb_log_files_in_group` | 日志文件数量 | `@@innodb_log_files_in_group` | ❌ |
| `innodb_flush_log_at_trx_commit` | 刷日志策略 | `@@innodb_flush_log_at_trx_commit` | ❌ |
| `sync_binlog` | 同步Binlog | `@@sync_binlog` | ❌ |
| `max_connections` | 最大连接数 | `@@max_connections` | ❌ |
| `table_open_cache` | 表缓存 | `@@table_open_cache` | ❌ |
| `thread_cache_size` | 线程缓存 | `@@thread_cache_size` | ❌ |
| `query_cache_type` | 查询缓存类型 | `@@query_cache_type` | ❌ |
| `key_buffer_size` | 键缓存大小 | `@@key_buffer_size` | ❌ |

### 5.11 缓冲池 (buffer)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `innodb_buffer_pool_size` | 缓冲池大小 | `@@innodb_buffer_pool_size` | ❌ |
| `innodb_buffer_pool_pages` | 缓冲池页数 | `SHOW GLOBAL STATUS` | ❌ |
| `innodb_buffer_pool_dirty_pages` | 脏页数 | `SHOW GLOBAL STATUS 'Innodb_buffer_pool_bytes_dirty'` | ❌ |
| `innodb_buffer_pool_free_pages` | 空闲页数 | `SHOW GLOBAL STATUS` | ❌ |
| `innodb_buffer_pool_read_ahead` | 预读页数 | `SHOW GLOBAL STATUS` | ❌ |

### 5.12 事务统计 (transaction)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `innodb_trx_count` | 当前事务数 | `information_schema.innodb_trx` | ❌ |
| `innodb_trx_committed` | 已提交事务 | `SHOW GLOBAL STATUS 'Com_commit'` | ❌ |
| `innodb_trx_rolled_back` | 已回滚事务 | `SHOW GLOBAL STATUS 'Com_rollback'` | ❌ |
| `undo_logs` | Undo日志数 | `information_schema.innodb_trx` | ❌ |

### 5.13 日志统计 (log)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `binlog_size` | Binlog总大小 | `SHOW MASTER LOGS` | ❌ |
| `error_log_size` | 错误日志大小 | `SHOW VARIABLES 'log_error'` | ❌ |
| `general_log_size` | 通用日志大小 | `SHOW VARIABLES 'general_log_file'` | ❌ |
| `slow_query_log_size` | 慢查询日志大小 | `SHOW VARIABLES 'slow_query_log_file'` | ❌ |

### 5.14 安全审计 (security)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `failed_connection_attempts` | 失败连接尝试 | `SHOW GLOBAL STATUS 'Aborted_connects'` | ❌ |
| `max_used_connections` | 历史最大连接 | `SHOW GLOBAL STATUS 'Max_used_connections'` | ❌ |
| `ssl_enabled` | SSL是否启用 | `@@have_ssl` | ❌ |
| `password_expire_days` | 密码过期天数 | `@@default_password_lifetime` | ❌ |

### 5.15 高可用 (ha)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `cluster_status` | 集群状态 | `SHOW NDB STATUS` | ❌ |
| `cluster_node_count` | 集群节点数 | `information_schema.ndb_cluster` | ❌ |
| `auto_increment_offset` | 自增偏移 | `@@auto_increment_offset` | ❌ |
| `auto_increment_increment` | 自增步长 | `@@auto_increment_increment` | ❌ |

### 5.16 资源限制 (resource)
| 指标名 | 说明 | SQL/变量 | 当前状态 |
|--------|------|----------|----------|
| `max_connections` | 最大连接数 | `@@max_connections` | ❌ |
| `max_connect_errors` | 最大连接错误 | `@@max_connect_errors` | ❌ |
| `max_updates` | 最大更新次数 | `@@max_updates` | ❌ |
| `max_heap_table_size` | 最大内存表大小 | `@@max_heap_table_size` | ❌ |
| `max_tmp_tables` | 最大临时表数 | `@@max_tmp_tables` | ❌ |

---

## 6. PostgreSQL 完整指标清单

### 6.1 基础信息 (basic)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `version` | 数据库版本 | `SELECT version()` | ✅ |
| `server_version_num` | 服务器版本号 | `SHOW server_version_num` | ❌ |
| `pg_data_directory` | 数据目录 | `SHOW data_directory` | ❌ |
| `port` | 端口 | `SHOW port` | ❌ |
| `uptime_seconds` | 运行时间 | `pg_postmaster_start_time()` | ✅ |
| `current_database` | 当前数据库 | `current_database()` | ❌ |

### 6.2 连接与会话 (session)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `active_connections` | 活跃连接数 | `pg_stat_activity WHERE state='active'` | ✅ |
| `idle_connections` | 空闲连接数 | `pg_stat_activity WHERE state='idle'` | ❌ |
| `idle_in_transaction` | 事务中空闲 | `pg_stat_activity WHERE state='idle in transaction'` | ❌ |
| `total_connections` | 总连接数 | `pg_stat_activity` | ❌ |
| `max_connections` | 最大连接数 | `SHOW max_connections` | ✅ |
| `conn_usage_pct` | 连接使用率 | 计算值 | ✅ |
| `waiting_connections` | 等待中的连接 | `pg_stat_activity WHERE waiting=true` | ❌ |
| `autovacuum_workers` | _autovacuum工作进程 | `pg_stat_activity` | ❌ |

### 6.3 空间使用 (space)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `database_sizes` | 数据库大小 | `pg_database_size()` | ✅ |
| `tablespace_sizes` | 表空间大小 | `pg_tablespace_size()` | ❌ |
| `total_tablespace_size` | 总表空间大小 | `pg_tablespace_size()` | ✅ |
| `relation_sizes` | 对象大小 | `pg_relation_size()` | ❌ |
| `database_size_top10` | Top 10数据库 | `pg_database` | ❌ |
| `schema_sizes` | 各Schema大小 | `information_schema.schemata` | ❌ |

### 6.4 性能指标 (performance)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `db_size_bytes` | 数据库总大小 | `pg_database_size()` | ❌ |
| `numbackends` | 当前后端数 | `pg_stat_database` | ❌ |
| `xact_commit` | 提交事务数 | `pg_stat_database` | ❌ |
| `xact_rollback` | 回滚事务数 | `pg_stat_database` | ❌ |
| `blks_read` | 块读取数 | `pg_stat_database` | ❌ |
| `blks_hit` | 块命中数 | `pg_stat_database` | ❌ |
| `blk_read_time` | 块读取时间(ms) | `pg_stat_database` | ❌ |
| `blk_write_time` | 块写入时间(ms) | `pg_stat_database` | ❌ |
| `tps` | TPS | `xact_commit + xact_rollback / Uptime` | ❌ |
| `cache_hit_ratio` | 缓存命中率 | `blks_hit / (blks_hit + blks_read)` | ❌ |
| `shared_buffers` | 共享缓存大小 | `SHOW shared_buffers` | ❌ |
| `effective_cache_size` | 有效缓存大小 | `SHOW effective_cache_size` | ❌ |
| `work_mem` | 工作内存 | `SHOW work_mem` | ❌ |
| `maintenance_work_mem` | 维护工作内存 | `SHOW maintenance_work_mem` | ❌ |
| `checkpoint_completion_target` | 检查点目标 | `SHOW checkpoint_completion_target` | ❌ |
| `wal_buffers` | WAL缓冲区 | `SHOW wal_buffers` | ❌ |

### 6.5 等待事件 (wait)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `locks` | 锁等待列表 | `pg_locks + pg_stat_activity` | ✅ |
| `lock_waits` | 锁等待数 | `pg_stat_activity WHERE waiting=true` | ❌ |
| `deadlocks` | 死锁数 | `pg_stat_database` | ❌ |
| `temp_files` | 临时文件数 | `pg_stat_database` | ❌ |
| `temp_bytes` | 临时文件大小 | `pg_stat_database` | ❌ |
| `wait_events_type` | 等待事件类型统计 | `pg_stat_activity` | ❌ |

### 6.6 会话详情 (session_detail)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `session_list` | 会话列表 | `pg_stat_activity` | ❌ |
| `session_by_state` | 按状态分组会话 | `pg_stat_activity` | ❌ |
| `session_by_application` | 按应用分组会话 | `pg_stat_activity` | ❌ |
| `long_running_queries` | 长时间运行查询 | `pg_stat_activity` | ❌ |
| `session_memory_usage` | 会话内存使用 | `pg_stat_activity` | ❌ |

### 6.7 SQL统计 (sql)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `slow_queries_active` | 当前慢查询 | `pg_stat_activity` | ✅ |
| `total_query_count` | 查询总数 | `pg_stat_statements` | ❌ |
| `top_sql_by_calls` | 按调用次数Top | `pg_stat_statements` | ❌ |
| `top_sql_by_total_time` | 按总时间Top | `pg_stat_statements` | ❌ |
| `top_sql_by_rows` | 按返回行数Top | `pg_stat_statements` | ❌ |
| `top_sql_by_shared_blks` | 按共享块Top | `pg_stat_statements` | ❌ |
| `seq_scans` | 顺序扫描次数 | `pg_stat_user_tables` | ❌ |
| `idx_scans` | 索引扫描次数 | `pg_stat_user_indexes` | ❌ |

### 6.8 对象统计 (object)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `table_count` | 表总数 | `pg_tables` | ❌ |
| `table_size_top20` | Top 20表大小 | `pg_relation_size()` | ❌ |
| `index_count` | 索引总数 | `pg_indexes` | ❌ |
| `index_size_top20` | Top 20索引大小 | `pg_relation_size()` | ❌ |
| `table_bloat` | 表膨胀率 | `pgstattuple` | ❌ |
| `index_bloat` | 索引膨胀率 | `pgstatindex` | ❌ |
| `seq_scan_ratio` | 顺序扫描比例 | `pg_stat_user_tables` | ❌ |
| `unused_indexes` | 未使用索引 | `pg_stat_user_indexes` | ❌ |
| `tables_needing_vacuum` | 需要VACUUM的表 | `pg_stat_user_tables` | ❌ |
| `tables_needing_analyze` | 需要ANALYZE的表 | `pg_stat_user_tables` | ❌ |

### 6.9 复制与集群 (replication)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `replication_slots` | 复制槽数量 | `pg_replication_slots` | ❌ |
| `replication_lag` | 复制延迟 | `pg_stat_replication` | ❌ |
| `wal_lag` | WAL延迟 | `pg_stat_replication` | ❌ |
| `streaming_replicas` | 流复制从机数 | `pg_stat_replication` | ❌ |
| `physical_replication_type` | 物理复制类型 | `pg_stat_replication` | ❌ |
| `logical_replication_type` | 逻辑复制类型 | `pg_replication_origin` | ❌ |
| `bdr_nodes` | BDR节点数 | `bdr.nodes` | ❌ |

### 6.10 配置参数 (config)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `shared_buffers` | 共享缓存 | `SHOW shared_buffers` | ❌ |
| `effective_cache_size` | 有效缓存 | `SHOW effective_cache_size` | ❌ |
| `maintenance_work_mem` | 维护内存 | `SHOW maintenance_work_mem` | ❌ |
| `work_mem` | 工作内存 | `SHOW work_mem` | ❌ |
| `max_connections` | 最大连接 | `SHOW max_connections` | ❌ |
| `max_worker_processes` | 最大工作进程 | `SHOW max_worker_processes` | ❌ |
| `wal_level` | WAL级别 | `SHOW wal_level` | ❌ |
| `archive_mode` | 归档模式 | `SHOW archive_mode` | ❌ |
| `checkpoint_timeout` | 检查点超时 | `SHOW checkpoint_timeout` | ❌ |
| `random_page_cost` | 随机页成本 | `SHOW random_page_cost` | ❌ |
| `effective_io_concurrency` | 有效IO并发 | `SHOW effective_io_concurrency` | ❌ |

### 6.11 缓冲池 (buffer)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `shared_buffers_used` | 已用共享缓存 | `pg_stat_bgwriter` | ❌ |
| `shared_buffers_checkpoint` | 检查点缓存 | `pg_stat_bgwriter` | ❌ |
| `shared_buffers_backend` | 后端缓存 | `pg_stat_bgwriter` | ❌ |
| `buffers_alloc` | 已分配缓冲区 | `pg_stat_bgwriter` | ❌ |
| `buffers_backend_fsync` | 后端 fsync | `pg_stat_bgwriter` | ❌ |

### 6.12 事务统计 (transaction)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `active_transactions` | 活跃事务 | `pg_stat_activity` | ❌ |
| `committed_transactions` | 已提交事务 | `pg_stat_database` | ❌ |
| `rolled_back_transactions` | 已回滚事务 | `pg_stat_database` | ❌ |
| `transaction_id_age` | 事务ID年龄 | `pg_database` | ❌ |
| `xmin horizon` | Xmin边界 | `pg_replication_slots` | ❌ |

### 6.13 日志统计 (log)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `log_directory` | 日志目录 | `SHOW log_directory` | ❌ |
| `log_filename` | 日志文件名 | `SHOW log_filename` | ❌ |
| `log_file_size` | 日志文件大小 | `pg_stat_file()` | ❌ |
| `log_connections` | 连接日志 | `SHOW log_connections` | ❌ |
| `log_disconnections` | 断开日志 | `SHOW log_disconnections` | ❌ |
| `log_duration` | 执行时间日志 | `SHOW log_duration` | ❌ |
| `log_min_duration_statement` | 最小记录时长 | `SHOW log_min_duration_statement` | ❌ |

### 6.14 安全审计 (security)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `failed_auth_attempts` | 认证失败 | `pg_stat_database` | ❌ |
| `superuser_reserved_connections` | 超级用户保留连接 | `SHOW superuser_reserved_connections` | ❌ |
| `ssl_enabled` | SSL启用 | `SHOW ssl` | ❌ |
| `password_encryption` | 密码加密方式 | `SHOW password_encryption` | ❌ |

### 6.15 高可用 (ha)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `is_in_recovery` | 是否在恢复 | `pg_is_in_recovery()` | ❌ |
| `is_primary` | 是否主库 | `pg_is_in_recovery()` | ❌ |
| `last_wal_receive_lsn` | 最后接收LSN | `pg_stat_replication` | ❌ |
| `last_wal_replay_lsn` | 最后回放LSN | `pg_stat_replication` | ❌ |
| `replay_lag_bytes` | 回放延迟(字节) | `pg_stat_replication` | ❌ |

### 6.16 资源限制 (resource)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `max_connections` | 最大连接 | `SHOW max_connections` | ❌ |
| `max_prepared_transactions` | 最大预处理事务 | `SHOW max_prepared_transactions` | ❌ |
| `max_locks_per_transaction` | 每事务最大锁 | `SHOW max_locks_per_transaction` | ❌ |
| `max_worker_processes` | 最大工作进程 | `SHOW max_worker_processes` | ❌ |
| `shared_memory_size` | 共享内存大小 | `SHOW shared_memory_size_in_huge_pages` | ❌ |

---

## 7. 达梦 (DM8) 完整指标清单

### 7.1 基础信息 (basic)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `version` | 数据库版本 | `SELECT banner FROM V$VERSION` | ✅ |
| `db_name` | 数据库名 | `V$INSTANCE` | ❌ |
| `mode` | 数据库模式 | `V$INSTANCE` | ❌ |
| `start_time` | 启动时间 | `V$INSTANCE` | ❌ |
| `uptime_seconds` | 运行时间 | `V$INSTANCE` | ✅ |
| `arch_mode` | 归档模式 | `V$DATABASE` | ❌ |

### 7.2 连接与会话 (session)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `sessions` | 当前会话数 | `V$SESSIONS` | ✅ |
| `active_sessions` | 活跃会话数 | `V$SESSIONS WHERE STATE='ACTIVE'` | ❌ |
| `max_sessions` | 最大会话数 | `V$PARAMETER WHERE NAME='MAX_SESSIONS'` | ✅ |
| `session_usage_pct` | 会话使用率 | 计算值 | ❌ |
| `session_wait_count` | 等待会话数 | `V$SESSION_WAIT` | ❌ |

### 7.3 空间使用 (space)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `tablespaces` | 表空间列表 | `V$TABLESPACE + V$DATAFILE` | ✅ |
| `datafile_count` | 数据文件数 | `V$DATAFILE` | ❌ |
| `temp_tablespace` | 临时表空间 | `V$SORT_OVERFLOW` | ❌ |
| `undo_tablespace` | UNDO表空间 | `V$UNDOFIL` | ❌ |
| `archive_size` | 归档日志大小 | `V$ARCH_FILE` | ❌ |

### 7.4 性能指标 (performance)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `qps` | 每秒查询数 | `V$SYSTEM_INFO` | ❌ |
| `tps` | 每秒事务数 | `V$SYSTEM_INFO` | ❌ |
| `sql_count` | SQL执行次数 | `V$SQL` | ❌ |
| `tran_count` | 事务执行次数 | `V$TRANSACTIONS` | ❌ |
| `read_pages` | 读取页数 | `V$BUFFER` | ❌ |
| `write_pages` | 写入页数 | `V$BUFFER` | ❌ |
| `buffer_hit_ratio` | 缓冲池命中率 | `V$BUFFER` | ❌ |

### 7.5 等待事件 (wait)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `locks` | 锁等待列表 | `V$LOCK` | ✅ |
| `lock_waits` | 锁等待数 | `V$LOCK WHERE BLOCKED=1` | ❌ |
| `wait_events` | 等待事件 | `V$SYSTEM_EVENT` | ❌ |
| `enqueue_waits` | Enqueue等待 | `V$ENQUEUE` | ❌ |
| `latch_waits` | Latch等待 | `V$LATCH` | ❌ |

### 7.6 会话详情 (session_detail)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `session_list` | 会话列表 | `V$SESSIONS` | ❌ |
| `session_sql` | 会话SQL | `V$SESSIONS + V$SQL` | ❌ |
| `blocked_sessions` | 阻塞会话 | `V$SESSIONS WHERE TRX_ID IN (SELECT TRX_ID FROM V$LOCK WHERE BLOCKED=1)` | ❌ |

### 7.7 SQL统计 (sql)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `top_sql_by_time` | 按时间Top SQL | `V$SQL` | ❌ |
| `top_sql_by_exec` | 按执行次数Top SQL | `V$SQL` | ❌ |
| `slow_sql` | 慢SQL | `V$SQL_HISTORY` | ❌ |
| `sql_errors` | SQL错误 | `V$ERROR` | ❌ |

### 7.8 对象统计 (object)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `table_count` | 表数量 | `SYSDBA.SYSOBJECTS` | ❌ |
| `index_count` | 索引数量 | `SYSDBA.SYSOBJECTS` | ❌ |
| `table_size` | 表大小 | `V$STATIC_TABLES` | ❌ |

### 7.9 复制与集群 (replication)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `archiving_enabled` | 归档是否启用 | `V$DATABASE` | ❌ |
| `archive_dest` | 归档目的地 | `V$ARCH_DEST` | ❌ |
| `archive_gap` | 归档Gap | `V$ARCH_FILE` | ❌ |

### 7.10 配置参数 (config)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `buffer` | 缓冲池大小 | `V$PARAMETER WHERE NAME='BUFFER'` | ❌ |
| `sort_buffer_size` | 排序缓冲区 | `V$PARAMETER WHERE NAME='SORT_BUF_SIZE'` | ❌ |
| `redo_log_size` | Redo日志大小 | `V$PARAMETER WHERE NAME='MLOG_BUF_SIZE'` | ❌ |

### 7.11 缓冲池 (buffer)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `buffer_pages` | 缓冲页数 | `V$BUFFER` | ❌ |
| `buffer_hit_ratio` | 命中率 | `V$BUFFER` | ❌ |
| `dirty_pages` | 脏页数 | `V$BUFFER` | ❌ |

### 7.12 事务统计 (transaction)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `active_trans` | 活跃事务 | `V$TRANSACTIONS` | ❌ |
| `commited_trans` | 已提交事务 | `V$TRANSACTIONS` | ❌ |

### 7.13 安全审计 (security)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `login_failed` | 登录失败 | `V$LOGIN` | ❌ |

---

## 8. Gbase8a 完整指标清单

### 8.1 基础信息 (basic)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `version` | 数据库版本 | `SELECT VERSION()` | ✅ |
| `cluster_name` | 集群名称 | `SHOW GCLUSTER CLUSTER` | ❌ |
| `node_count` | 节点数量 | `information_schema.GCLUSTER_NODES` | ❌ |
| `uptime_seconds` | 运行时间 | `SHOW GLOBAL STATUS LIKE 'Uptime'` | ❌ |

### 8.2 连接与会话 (session)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `threads_connected` | 当前连接数 | `SHOW GLOBAL STATUS 'Threads_connected'` | ❌ |
| `threads_running` | 活跃线程 | `SHOW GLOBAL STATUS 'Threads_running'` | ❌ |
| `max_connections` | 最大连接 | `SHOW VARIABLES 'max_connections'` | ❌ |

### 8.3 空间使用 (space)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `datadir` | 数据目录 | `SHOW VARIABLES 'datadir'` | ❌ |
| `data_size` | 数据大小 | `information_schema.FILES` | ❌ |
| `gcware_size` | GCWare大小 | `information_schema.FILES` | ❌ |

### 8.4 性能指标 (performance)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `qps` | QPS | `SHOW GLOBAL STATUS` | ❌ |
| `tps` | TPS | `SHOW GLOBAL STATUS` | ❌ |
| `cluster_commands` | 集群命令统计 | `SHOW GLOBAL STATUS` | ❌ |

### 8.5 等待事件 (wait)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `locks` | 锁等待 | `information_schema.INNODB_LOCK_WAITS` | ❌ |
| `deadlocks` | 死锁 | `SHOW GLOBAL STATUS 'Innodb_deadlocks'` | ❌ |

### 8.6 会话详情 (session_detail)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `processlist` | 进程列表 | `SHOW PROCESSLIST` | ❌ |
| `gcnode_session` | 各节点会话 | `information_schema.GCLUSTER_SESSION` | ❌ |

### 8.7 SQL统计 (sql)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `slow_queries` | 慢查询 | `SHOW GLOBAL STATUS 'Slow_queries'` | ❌ |
| `long_query_time` | 慢查询阈值 | `SHOW VARIABLES 'long_query_time'` | ❌ |

### 8.8 复制与集群 (replication)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `cluster_nodes` | 集群节点 | `information_schema.GCLUSTER_NODES` | ✅ |
| `node_state` | 节点状态 | `information_schema.GCLUSTER_NODES` | ❌ |
| `sync_mode` | 同步模式 | `SHOW GCLUSTER CLUSTER` | ❌ |
| `rebalance_state` | 负载均衡状态 | `SHOW GCLUSTER REBALANCE` | ❌ |

### 8.9 高可用 (ha)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `ha_mode` | HA模式 | `SHOW GCLUSTER CLUSTER` | ❌ |
| `failover_enabled` | 故障切换启用 | `SHOW VARIABLES` | ❌ |

---

## 9. TDSQL 完整指标清单

### 9.1 基础信息 (basic)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `version` | 数据库版本 | `SELECT VERSION()` | ✅ |
| `db_type` | 数据库类型 | `SHOW VARIABLES 'version_comment'` | ❌ |
| `shard_count` | 分片数量 | `SHOW VARIABLES` | ❌ |
| `uptime_seconds` | 运行时间 | `SHOW GLOBAL STATUS 'Uptime'` | ❌ |

### 9.2 连接与会话 (session)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `threads_connected` | 当前连接数 | `SHOW GLOBAL STATUS 'Threads_connected'` | ❌ |
| `threads_running` | 活跃线程 | `SHOW GLOBAL STATUS 'Threads_running'` | ❌ |
| `max_connections` | 最大连接 | `SHOW VARIABLES 'max_connections'` | ❌ |

### 9.3 空间使用 (space)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `shard_sizes` | 各分片大小 | `information_schema.TABLES` | ❌ |
| `total_size` | 总大小 | `information_schema.FILES` | ❌ |

### 9.4 性能指标 (performance)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `qps` | QPS | `SHOW GLOBAL STATUS` | ❌ |
| `tps` | TPS | `SHOW GLOBAL STATUS` | ❌ |
| `shard_qps` | 各分片QPS | `SHOW SHARD STATUS` | ❌ |
| `shard_tps` | 各分片TPS | `SHOW SHARD STATUS` | ❌ |

### 9.5 等待事件 (wait)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `locks` | 锁等待 | `performance_schema.data_lock_waits` | ❌ |
| `shard_locks` | 分片锁 | `information_schema.innodb_trx` | ❌ |

### 9.6 会话详情 (session_detail)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `processlist` | 进程列表 | `SHOW PROCESSLIST` | ❌ |
| `shard_processlist` | 分片进程 | `SHOW PROCESSLIST` | ❌ |

### 9.7 SQL统计 (sql)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `slow_queries` | 慢查询 | `SHOW GLOBAL STATUS 'Slow_queries'` | ❌ |
| `long_query_time` | 慢查询阈值 | `SHOW VARIABLES 'long_query_time'` | ❌ |
| `top_sql_by_latency` | 按延迟Top SQL | `performance_schema.events_statements_summary_by_digest` | ❌ |

### 9.8 复制与集群 (replication)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `shard_count` | 分片数量 | `SHOW VARIABLES` | ❌ |
| `shard_status` | 分片状态 | `SHOW SHARD STATUS` | ✅ |
| `shard_replication` | 分片复制 | `SHOW SHARD STATUS` | ❌ |
| `sync_binlog` | Binlog同步 | `SHOW VARIABLES 'sync_binlog'` | ❌ |
| `gtid_mode` | GTID模式 | `SHOW VARIABLES 'gtid_mode'` | ❌ |

### 9.9 高可用 (ha)
| 指标名 | 说明 | SQL/视图 | 当前状态 |
|--------|------|----------|----------|
| `set_name` | SET名称 | `SHOW VARIABLES 'set_name'` | ❌ |
| `ha_mode` | HA模式 | `SHOW VARIABLES` | ❌ |
| `switch_enabled` | 切换启用 | `SHOW VARIABLES` | ❌ |
| `primary_shard` | 主分片 | `SHOW SHARD STATUS` | ❌ |

---

## 10. 开发优先级与计划

### 10.1 第一优先级 (P0) - 核心可用性指标

**目标**: 确保数据库可连接、可监控

| 序号 | 指标类别 | 包含数据库 | 工作量 |
|------|----------|-----------|--------|
| 1 | 会话详情 | Oracle/MySQL/PostgreSQL/DM8 | 中 |
| 2 | Top SQL统计 | Oracle/MySQL/PostgreSQL/DM8 | 大 |
| 3 | 缓冲池指标 | Oracle/MySQL/PostgreSQL/DM8 | 中 |

### 10.2 第二优先级 (P1) - 性能分析指标

**目标**: 支持性能分析和调优

| 序号 | 指标类别 | 包含数据库 | 工作量 |
|------|----------|-----------|--------|
| 4 | 等待事件详情 | Oracle/MySQL/PostgreSQL/DM8 | 大 |
| 5 | 事务统计 | Oracle/MySQL/PostgreSQL/DM8 | 中 |
| 6 | 复制与集群 | Oracle/MySQL/PostgreSQL/Gbase/TDSQL | 大 |
| 7 | 日志统计 | Oracle/MySQL/PostgreSQL/DM8 | 中 |
| 8 | 配置参数 | Oracle/MySQL/PostgreSQL/DM8 | 中 |

### 10.3 第三优先级 (P2) - 运维辅助指标

**目标**: 完善运维能力

| 序号 | 指标类别 | 包含数据库 | 工作量 |
|------|----------|-----------|--------|
| 9 | 对象统计 | Oracle/MySQL/PostgreSQL/DM8 | 大 |
| 10 | 安全审计 | Oracle/MySQL/PostgreSQL/DM8 | 中 |
| 11 | 高可用指标 | Oracle/MySQL/PostgreSQL/Gbase/TDSQL | 中 |
| 12 | 资源限制 | Oracle/MySQL/PostgreSQL/DM8 | 小 |

---

## 11. 开发建议

### 11.1 数据结构设计

建议在 `MonitorLog` 模型中增加 `metrics` JSONField 用于存储扩展指标:

```python
class MonitorLog(models.Model):
    # ... 现有字段 ...
    metrics = models.JSONField(default=dict, verbose_name="扩展指标")
```

### 11.2 采集策略

| 指标类别 | 建议采集频率 | 说明 |
|----------|--------------|------|
| 基础/连接/空间 | 每5分钟 | 常规监控 |
| 性能/TPS/QPS | 每1分钟 | 高频指标 |
| SQL/等待事件 | 每5分钟 | 分析用 |
| 对象统计 | 每小时 | 变更不频繁 |
| 配置参数 | 每天1次 | 变更极少 |

### 11.3 告警规则建议

| 指标 | 阈值建议 | 级别 |
|------|----------|------|
| conn_usage_pct | > 80% | 警告 |
| conn_usage_pct | > 95% | 严重 |
| tablespace used_pct | > 85% | 警告 |
| tablespace used_pct | > 95% | 严重 |
| lock_waits | > 10 | 警告 |
| qps 变化率 | 异常波动 | 警告 |
| buffer_hit_ratio | < 90% | 警告 |

---

## 12. 总结

本文档定义了6种目标数据库的完整监控指标体系：

| 数据库 | 总指标数 | 已实现 | 部分实现 | 未实现 |
|--------|----------|--------|----------|--------|
| Oracle | 120+ | 12 | 6 | 100+ |
| MySQL | 100+ | 10 | 4 | 90+ |
| PostgreSQL | 110+ | 8 | 3 | 100+ |
| DM8 | 50+ | 5 | 1 | 45+ |
| Gbase8a | 30+ | 2 | 0 | 30+ |
| TDSQL | 40+ | 2 | 0 | 40+ |

**核心结论**: 当前系统仅实现了约10%的监控指标，需要大量开发工作来充实采集能力。
