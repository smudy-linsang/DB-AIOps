# Oracle 数据库完整指标展示分析

## 1. Oracle 采集的指标类别

### 1.1 基础信息 (basic)
- version: 数据库版本
- instance_name: 实例名
- host_name: 主机名
- db_version: 数据库版本
- startup_time: 启动时间
- archiver: 归档模式
- db_name: 数据库名
- db_unique_name: 唯一名
- open_mode: 打开模式
- database_role: 数据库角色
- log_mode: 日志模式
- uptime_seconds: 运行时间

### 1.2 连接与会话 (session)
- active_sessions: 活跃会话
- inactive_sessions: 非活跃会话
- background_sessions: 后台会话
- total_sessions:总会话
- max_connections: 最大连接数
- conn_usage_pct: 连接使用率

### 1.3 空间使用 (space)
- tablespaces: 表空间列表 (name, total_mb, used_mb, used_pct)
- temp_tablespaces: 临时表空间 (name, size_mb)
- undo_tablespaces: UNDO表空间 (name, status, size_mb)
- datafile_count: 数据文件数量
- datafile_size_total_gb: 数据文件总大小

### 1.4 性能指标 (performance)
- exec_count: 执行次数
- qps: 每秒查询数
- commits: 提交数
- rollbacks: 回滚数
- tps: 每秒事务数
- logical_reads: 逻辑读
- physical_reads: 物理读
- physical_writes: 物理写
- redo_generation_bytes: Redo生成量
- parse_count_total: 总解析数
- parse_count_hard: 硬解析数

### 1.5 锁等待 (wait)
- locks: 锁等待列表 (blocker_id, waiter_id, seconds, wait_event...)
- lock_wait_count: 锁等待数量
- top_wait_events: Top等待事件 (event, total_waits, time_waited, average_wait)

### 1.6 会话详情 (session_detail)
- session_list: 会话列表
- blocked_sessions: 被阻塞会话

### 1.7 SQL统计 (sql)
- slow_queries_active: 当前慢查询数
- top_sql_by_buffer_gets: Top SQL (buffer gets)
- top_sql_by_disk_reads: Top SQL (disk reads)
- top_sql_by_executions: Top SQL (executions)

### 1.8 缓冲池 (buffer)
- buffer_pools: 缓冲池列表
- buffer_cache_mb: 缓冲缓存大小
- shared_pool_mb: 共享池大小
- java_pool_mb: Java池大小
- large_pool_mb: 大池大小
- pga_used_mb: PGA使用大小
- buffer_hit_ratio: 缓冲命中率
- library_cache_hit_ratio: 库缓存命中率
- cpu_used_seconds: CPU使用时间
- db_time_seconds: DB时间

### 1.9 事务统计 (transaction)
- active_transactions: 活跃事务数
- row_lock_contention: 行锁争用
- committed_transactions: 已提交事务
- rolled_back_transactions: 已回滚事务

### 1.10 对象统计 (object)
- table_count: 表数量
- index_count: 索引数量
- table_size_top20: Top 20表大小
- index_size_top20: Top 20索引大小
- stale_statistics: 统计信息过期对象
- partition_count: 分区数量

### 1.11 复制与集群 (replication)
- rac_instance_count: RAC实例数量
- rac_instances: RAC实例列表
- dg_database_role: DataGuard角色
- dg_protection_mode: DataGuard保护模式
- dg_protection_level: DataGuard保护级别

### 1.12 RAC互联网络 (Interconnect)
- rac_interconnects: RAC互联列表
- ic_bytes_sent_total: 发送字节总数
- ic_bytes_received_total: 接收字节总数

## 2. 前端现有问题

### 2.1 API数据结构问题
- 前端调用 `/databases/${id}/` 而非 `/api/v1/databases/${id}/status/`
- 需要确保 metrics 数据被正确传递到组件

### 2.2 前端展示结构问题
- 当前只展示少量核心指标
- 需要按类别组织所有指标
- 需要添加各类指标的图表和表格展示

## 3. 开发计划

### Phase 1: 数据层修复
1. 修复前端 API 调用路径
2. 确保所有 metrics 数据正确获取

### Phase 2: 基础信息展示
1. 基本信息卡片
2. 实例信息
3. 数据库信息

### Phase 3: 会话与连接
1. 会话统计卡片
2. 会话列表表格
3. 连接使用趋势图

### Phase 4: 空间管理
1. 表空间列表表格
2. 表空间使用趋势图
3. 数据文件统计

### Phase 5: 性能指标
1. QPS/TPS 趋势图
2. 逻辑读/物理读趋势图
3. 命中率展示

### Phase 6: 锁与等待
1. 锁等待列表
2. Top等待事件表格
3. 锁争用趋势

### Phase 7: SQL分析
1. Top SQL表格
2. SQL统计图表

### Phase 8: SGA/PGA
1. 内存池统计
2. 内存使用趋势图

### Phase 9: RAC/集群
1. RAC实例列表
2. DataGuard状态
3. 集群互联统计