/**
 * dbMetricsConfig.js - 多数据库类型前端指标配置
 * v3.0: 统一管理所有 6 种数据库类型的指标分类、显示名称、格式化规则和阈值
 *
 * 数据库类型：oracle / mysql / pgsql / dm / gbase / tdsql
 */

// 数据库类型中文标签
export const DB_TYPE_LABELS = {
  oracle: 'Oracle',
  mysql: 'MySQL',
  pgsql: 'PostgreSQL',
  dm: '达梦 DM8',
  gbase: 'GBase 8a',
  tdsql: 'TDSQL',
};

// 数据库类型图标颜色
export const DB_TYPE_COLORS = {
  oracle: '#f5222d',
  mysql: '#1890ff',
  pgsql: '#336791',
  dm: '#ee2222',
  gbase: '#00a854',
  tdsql: '#108ee9',
};

/**
 * 每种数据库类型的指标分类配置
 *
 * 每个分类包含：
 *   key: 唯一标识
 *   title: 卡片标题
 *   type: 'cards'(默认) | 'table'
 *   showWhen: 可选函数，接收 data 对象，返回是否显示
 *   metrics: type='cards' 时使用，列表
 *     - key: 指标字段名
 *     - label: 显示名称
 *     - format: 格式化类型 (number|percent|size_mb|bytes|duration|text|boolean|status)
 *     - clickable: 是否可点击下钻 (默认 true)
 *   columns: type='table' 时使用，列表
 *     - key: 字段名
 *     - title: 列标题
 *     - format: 格式化类型
 */
export const DB_METRIC_CATEGORIES = {

  // ==================== Oracle ====================
  oracle: [
    {
      key: 'basic',
      title: 'Oracle 实例信息',
      metrics: [
        { key: 'instance_name', label: '实例名', format: 'text' },
        { key: 'host_name', label: '主机名', format: 'text' },
        { key: 'version', label: '数据库版本', format: 'text' },
        { key: 'open_mode', label: '打开模式', format: 'text' },
        { key: 'database_role', label: '数据库角色', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
      ],
    },
    {
      key: 'session',
      title: 'Oracle 会话与连接',
      metrics: [
        { key: 'active_connections', label: '活跃会话', format: 'number', highlight: true, fallbackKey: 'active_sessions' },
        { key: 'total_connections', label: '总会话数', format: 'number', fallbackKey: 'total_sessions' },
        { key: 'conn_usage_pct', label: '连接使用率', format: 'percent', highlight: true },
        { key: 'max_connections', label: '最大连接数', format: 'number', fallbackKey: 'max_conn' },
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'tps', label: 'TPS', format: 'number' },
      ],
    },
    {
      key: 'performance',
      title: 'Oracle 性能指标',
      metrics: [
        { key: 'logical_reads', label: '逻辑读', format: 'number' },
        { key: 'physical_reads', label: '物理读', format: 'number' },
        { key: 'physical_writes', label: '物理写', format: 'number' },
        { key: 'buffer_hit_ratio', label: '缓冲命中率', format: 'percent', highlight: true },
        { key: 'library_cache_hit_ratio', label: '库缓存命中率', format: 'percent', highlight: true },
        { key: 'redo_generation_bytes', label: 'Redo生成量', format: 'bytes' },
        { key: 'exec_count', label: '执行次数', format: 'number' },
        { key: 'commits', label: '事务提交', format: 'number' },
        { key: 'rollbacks', label: '事务回滚', format: 'number' },
        { key: 'parse_count_total', label: '总解析数', format: 'number' },
        { key: 'parse_count_hard', label: '硬解析数', format: 'number' },
        { key: 'db_time_seconds', label: 'DB Time', format: 'number' },
      ],
    },
    {
      key: 'memory',
      title: 'Oracle 内存池 (SGA/PGA)',
      metrics: [
        { key: 'buffer_cache_mb', label: 'Buffer Cache', format: 'size_mb' },
        { key: 'shared_pool_mb', label: 'Shared Pool', format: 'size_mb' },
        { key: 'java_pool_mb', label: 'Java Pool', format: 'size_mb' },
        { key: 'large_pool_mb', label: 'Large Pool', format: 'size_mb' },
        { key: 'pga_used_mb', label: 'PGA Used', format: 'size_mb' },
      ],
    },
    {
      key: 'rac',
      title: 'Oracle RAC 集群',
      showWhen: (data) => data.rac_instances && data.rac_instances.length > 0,
      metrics: [
        { key: 'rac_instance_count', label: 'RAC实例数', format: 'number' },
        { key: 'dg_database_role', label: 'DG角色', format: 'text' },
        { key: 'dg_protection_mode', label: '保护模式', format: 'text' },
      ],
    },
    {
      key: 'datafile_stats',
      title: 'Oracle 数据文件统计',
      metrics: [
        { key: 'datafile_count', label: '数据文件数量', format: 'number' },
        { key: 'datafile_size_total_gb', label: '数据文件总大小', format: 'size_mb' },
      ],
    },
    {
      key: 'transaction_stats',
      title: 'Oracle 事务统计',
      metrics: [
        { key: 'active_transactions', label: '活跃事务数', format: 'number' },
        { key: 'row_lock_contention', label: '行锁争用', format: 'number' },
        { key: 'committed_transactions', label: '已提交事务', format: 'number' },
        { key: 'rolled_back_transactions', label: '已回滚事务', format: 'number' },
      ],
    },
    {
      key: 'object_stats',
      title: 'Oracle 对象统计',
      metrics: [
        { key: 'table_count', label: '表数量', format: 'number' },
        { key: 'index_count', label: '索引数量', format: 'number' },
        { key: 'partition_count', label: '分区数量', format: 'number' },
      ],
    },
    // === 表格型分类 ===
    {
      key: 'tablespaces',
      title: 'Oracle 表空间使用',
      type: 'table',
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'total_mb', title: '总大小(MB)', format: 'number' },
        { key: 'used_mb', title: '已使用(MB)', format: 'number' },
        { key: 'used_pct', title: '使用率', format: 'percent' },
      ],
      rowClick: 'tablespace',
    },
    {
      key: 'temp_tablespaces',
      title: 'Oracle 临时表空间',
      type: 'table',
      showWhen: (data) => data.temp_tablespaces && data.temp_tablespaces.length > 0,
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
      ],
    },
    {
      key: 'undo_tablespaces',
      title: 'Oracle UNDO表空间',
      type: 'table',
      showWhen: (data) => data.undo_tablespaces && data.undo_tablespaces.length > 0,
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'status', title: '状态' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
      ],
    },
    {
      key: 'locks',
      title: 'Oracle 锁等待',
      type: 'table',
      columns: [
        { key: 'blocker_id', title: '阻塞者' },
        { key: 'blocker_user', title: '阻塞者用户' },
        { key: 'waiter_id', title: '等待者' },
        { key: 'waiter_user', title: '等待者用户' },
        { key: 'seconds', title: '等待时间(秒)' },
        { key: 'wait_event', title: '等待事件' },
      ],
    },
    {
      key: 'top_wait_events',
      title: 'Oracle Top等待事件',
      type: 'table',
      columns: [
        { key: 'event', title: '事件名' },
        { key: 'total_waits', title: '总等待次数', format: 'number' },
        { key: 'time_waited', title: '总等待时间(ms)', format: 'number' },
        { key: 'average_wait', title: '平均等待时间(ms)', format: 'number' },
      ],
      rowClick: 'waitEvent',
    },
    {
      key: 'session_list',
      title: 'Oracle 会话列表',
      type: 'table',
      columns: [
        { key: 'sid_serial', title: 'SID/Serial' },
        { key: 'username', title: '用户名' },
        { key: 'status', title: '状态' },
        { key: 'program', title: '程序' },
        { key: 'machine', title: '机器' },
        { key: 'wait_event', title: '等待事件' },
        { key: 'seconds_in_wait', title: '等待秒数' },
        { key: 'sql_id', title: 'SQL ID' },
      ],
      pagination: true,
    },
    {
      key: 'top_sql_by_buffer_gets',
      title: 'Oracle Top SQL (Buffer Gets)',
      type: 'table',
      showWhen: (data) => data.top_sql_by_buffer_gets && data.top_sql_by_buffer_gets.length > 0,
      columns: [
        { key: 'sql_id', title: 'SQL ID' },
        { key: 'sql_text', title: 'SQL文本' },
        { key: 'buffer_gets', title: 'Buffer Gets', format: 'number' },
        { key: 'disk_reads', title: 'Disk Reads', format: 'number' },
        { key: 'executions', title: '执行次数', format: 'number' },
        { key: 'buffer_gets_per_exec', title: 'Gets/执行', format: 'number' },
      ],
    },
    {
      key: 'rac_instances',
      title: 'Oracle RAC 实例列表',
      type: 'table',
      showWhen: (data) => data.rac_instances && data.rac_instances.length > 0,
      columns: [
        { key: 'inst_id', title: '实例ID' },
        { key: 'instance_name', title: '实例名' },
        { key: 'host_name', title: '主机名' },
        { key: 'status', title: '状态' },
      ],
    },
    {
      key: 'table_size_top20',
      title: 'Oracle Top 20 表大小',
      type: 'table',
      showWhen: (data) => data.table_size_top20 && data.table_size_top20.length > 0,
      columns: [
        { key: 'owner', title: '所有者' },
        { key: 'table_name', title: '表名' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
      ],
    },
    {
      key: 'index_size_top20',
      title: 'Oracle Top 20 索引大小',
      type: 'table',
      showWhen: (data) => data.index_size_top20 && data.index_size_top20.length > 0,
      columns: [
        { key: 'owner', title: '所有者' },
        { key: 'index_name', title: '索引名' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
      ],
    },
  ],

  // ==================== MySQL ====================
  mysql: [
    {
      key: 'basic',
      title: 'MySQL 基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'server_id', label: 'Server ID', format: 'number' },
        { key: 'host_name', label: '主机名', format: 'text' },
      ],
    },
    {
      key: 'session',
      title: 'MySQL 连接与会话',
      metrics: [
        { key: 'threads_connected', label: '当前连接数', format: 'number' },
        { key: 'threads_running', label: '活跃线程数', format: 'number', highlight: true },
        { key: 'max_connections', label: '最大连接数', format: 'number' },
        { key: 'conn_usage_pct', label: '连接使用率', format: 'percent', highlight: true },
        { key: 'aborted_connects', label: '异常断开', format: 'number' },
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'tps', label: 'TPS', format: 'number' },
      ],
    },
    {
      key: 'innodb_buffer',
      title: 'InnoDB 缓冲池',
      metrics: [
        { key: 'innodb_buffer_pool_size_mb', label: '缓冲池大小(MB)', format: 'size_mb' },
        { key: 'innodb_buffer_pool_hit_ratio', label: '命中率', format: 'percent', highlight: true },
        { key: 'innodb_buffer_pool_pages_free', label: '空闲页数', format: 'number' },
        { key: 'innodb_buffer_pool_pages_dirty', label: '脏页数', format: 'number' },
        { key: 'buffer_dirty_ratio', label: '脏页比例', format: 'percent' },
      ],
    },
    {
      key: 'performance',
      title: 'MySQL 性能指标',
      metrics: [
        { key: 'innodb_rows_read_ps', label: '行读取/s', format: 'number' },
        { key: 'innodb_rows_inserted_ps', label: '行插入/s', format: 'number' },
        { key: 'innodb_rows_updated_ps', label: '行更新/s', format: 'number' },
        { key: 'innodb_rows_deleted_ps', label: '行删除/s', format: 'number' },
        { key: 'innodb_deadlocks', label: '死锁次数', format: 'number', highlight: true },
        { key: 'slow_queries', label: '慢查询数', format: 'number' },
      ],
    },
    {
      key: 'replication',
      title: 'MySQL 主从复制',
      showWhen: (data) => data.replication_role,
      metrics: [
        { key: 'replication_role', label: '复制角色', format: 'text' },
        { key: 'slave_io_running', label: 'IO线程', format: 'status' },
        { key: 'slave_sql_running', label: 'SQL线程', format: 'status' },
        { key: 'seconds_behind_master', label: '延迟(秒)', format: 'number', highlight: true },
        { key: 'gtid_mode', label: 'GTID模式', format: 'text' },
      ],
    },
    {
      key: 'innodb_io',
      title: 'InnoDB IO',
      metrics: [
        { key: 'innodb_data_reads_ps', label: '数据读取/s', format: 'number' },
        { key: 'innodb_data_writes_ps', label: '数据写入/s', format: 'number' },
        { key: 'innodb_log_waits_ps', label: '日志等待/s', format: 'number' },
        { key: 'innodb_os_log_written_ps', label: '日志写入量/s', format: 'bytes' },
      ],
    },
    {
      key: 'cache',
      title: 'MySQL 缓存效率',
      metrics: [
        { key: 'table_open_cache_hit_ratio', label: '表缓存命中率', format: 'percent' },
        { key: 'key_buffer_hit_ratio', label: 'Key Buffer命中率', format: 'percent' },
        { key: 'thread_cache_hit_ratio', label: '线程缓存命中率', format: 'percent' },
      ],
    },
    {
      key: 'security',
      title: 'MySQL 安全',
      showWhen: (data) => data.ssl_enabled !== undefined,
      metrics: [
        { key: 'ssl_enabled', label: 'SSL状态', format: 'text' },
        { key: 'ssl_cipher', label: 'SSL加密套件', format: 'text' },
      ],
    },
    // === 表格型分类 ===
    {
      key: 'space_list',
      title: 'MySQL 数据库空间',
      type: 'table',
      showWhen: (data) => data.space_list && data.space_list.length > 0,
      columns: [
        { key: 'name', title: '数据库名' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
        { key: 'table_count', title: '表数量' },
      ],
    },
    {
      key: 'innodb_tablespaces',
      title: 'InnoDB 表空间',
      type: 'table',
      showWhen: (data) => data.innodb_tablespaces && data.innodb_tablespaces.length > 0,
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'total_mb', title: '总大小(MB)', format: 'number' },
        { key: 'used_mb', title: '已用(MB)', format: 'number' },
        { key: 'free_mb', title: '剩余(MB)', format: 'number' },
      ],
    },
    {
      key: 'session_list',
      title: 'MySQL 会话列表',
      type: 'table',
      columns: [
        { key: 'id', title: '连接ID' },
        { key: 'user', title: '用户名' },
        { key: 'host', title: '来源主机' },
        { key: 'db', title: '数据库' },
        { key: 'command', title: '命令' },
        { key: 'time_seconds', title: '耗时(秒)' },
        { key: 'state', title: '状态' },
        { key: 'info', title: 'SQL信息' },
      ],
      pagination: true,
    },
    {
      key: 'top_sql',
      title: 'MySQL Top SQL',
      type: 'table',
      showWhen: (data) => data.top_sql && data.top_sql.length > 0,
      columns: [
        { key: 'sql_text', title: 'SQL文本' },
        { key: 'exec_count', title: '执行次数', format: 'number' },
        { key: 'total_latency', title: '总耗时' },
        { key: 'rows_examined', title: '扫描行数', format: 'number' },
      ],
    },
    {
      key: 'locks',
      title: 'MySQL 锁等待',
      type: 'table',
      columns: [
        { key: 'blocker_trx_id', title: '阻塞事务ID' },
        { key: 'waiter_trx_id', title: '等待事务ID' },
        { key: 'lock_mode', title: '锁模式' },
        { key: 'lock_table', title: '锁表' },
        { key: 'wait_seconds', title: '等待秒数' },
      ],
    },
    {
      key: 'unused_indexes',
      title: 'MySQL 未使用索引',
      type: 'table',
      showWhen: (data) => data.unused_indexes && data.unused_indexes.length > 0,
      columns: [
        { key: 'schema_name', title: '数据库名' },
        { key: 'table_name', title: '表名' },
        { key: 'index_name', title: '索引名' },
      ],
    },
    {
      key: 'redundant_indexes',
      title: 'MySQL 冗余索引',
      type: 'table',
      showWhen: (data) => data.redundant_indexes && data.redundant_indexes.length > 0,
      columns: [
        { key: 'schema_name', title: '数据库名' },
        { key: 'table_name', title: '表名' },
        { key: 'redundant_index', title: '冗余索引' },
        { key: 'dominant_index', title: '可替代索引' },
      ],
    },
    {
      key: 'table_size_top20',
      title: 'MySQL Top 20 表大小',
      type: 'table',
      showWhen: (data) => data.table_size_top20 && data.table_size_top20.length > 0,
      columns: [
        { key: 'schema_name', title: '数据库名' },
        { key: 'table_name', title: '表名' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
      ],
    },
  ],

  // ==================== PostgreSQL ====================
  pgsql: [
    {
      key: 'basic',
      title: 'PostgreSQL 基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'current_database', label: '当前数据库', format: 'text' },
        { key: 'is_in_recovery', label: '恢复模式', format: 'boolean' },
      ],
    },
    {
      key: 'session',
      title: 'PostgreSQL 连接与会话',
      metrics: [
        { key: 'active_connections', label: '活跃连接', format: 'number', highlight: true },
        { key: 'idle_connections', label: '空闲连接', format: 'number' },
        { key: 'idle_in_transaction', label: '事务中空闲', format: 'number', highlight: true },
        { key: 'total_connections', label: '总连接数', format: 'number' },
        { key: 'max_connections', label: '最大连接数', format: 'number' },
        { key: 'conn_usage_pct', label: '连接使用率', format: 'percent', highlight: true },
        { key: 'waiting_connections', label: '等待连接', format: 'number' },
      ],
    },
    {
      key: 'performance',
      title: 'PostgreSQL 性能指标',
      metrics: [
        { key: 'cache_hit_ratio', label: '缓存命中率', format: 'percent', highlight: true },
        { key: 'tps', label: 'TPS', format: 'number' },
        { key: 'deadlocks', label: '死锁次数', format: 'number', highlight: true },
        { key: 'temp_files', label: '临时文件数', format: 'number' },
        { key: 'temp_bytes_mb', label: '临时文件大小(MB)', format: 'size_mb' },
        { key: 'blk_read_time_ms', label: '块读耗时(ms)', format: 'number' },
        { key: 'blk_write_time_ms', label: '块写耗时(ms)', format: 'number' },
      ],
    },
    {
      key: 'bgwriter',
      title: 'PostgreSQL 后台写入',
      metrics: [
        { key: 'buffers_checkpoint', label: 'CKPT写入', format: 'number' },
        { key: 'buffers_backend', label: '后端写入', format: 'number' },
        { key: 'buffers_clean', label: '清理写入', format: 'number' },
        { key: 'maxwritten_clean', label: '清理最大写入', format: 'number' },
        { key: 'buffers_backend_fsync', label: '后端Fsync', format: 'number' },
      ],
    },
    {
      key: 'replication',
      title: 'PostgreSQL 流复制',
      showWhen: (data) => data.replication_lag_bytes !== undefined || data.replication_active === true,
      metrics: [
        { key: 'replication_lag_bytes', label: '复制延迟(字节)', format: 'bytes' },
        { key: 'wal_write_lag_ms', label: 'WAL写延迟(ms)', format: 'number' },
        { key: 'wal_flush_lag_ms', label: 'WAL刷延迟(ms)', format: 'number' },
        { key: 'wal_replay_lag_ms', label: 'WAL回放延迟(ms)', format: 'number' },
        { key: 'replication_status', label: '复制状态', format: 'text' },
      ],
    },
    {
      key: 'autovacuum',
      title: 'PostgreSQL AutoVacuum',
      metrics: [
        { key: 'autovacuum_workers', label: '工作进程数', format: 'number' },
        { key: 'n_dead_tup_total', label: '死元组总数', format: 'number' },
        { key: 'transaction_id_age', label: '事务ID年龄', format: 'number', highlight: true },
      ],
    },
    // === 表格型分类 ===
    {
      key: 'space_list',
      title: 'PostgreSQL 数据库/表空间',
      type: 'table',
      showWhen: (data) => data.space_list && data.space_list.length > 0,
      columns: [
        { key: 'name', title: '名称' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
      ],
    },
    {
      key: 'locks',
      title: 'PostgreSQL 锁等待',
      type: 'table',
      columns: [
        { key: 'blocked_pid', title: '被阻塞PID' },
        { key: 'blocking_pid', title: '阻塞者PID' },
        { key: 'blocked_query', title: '被阻塞查询' },
        { key: 'lock_type', title: '锁类型' },
        { key: 'blocked_user', title: '被阻塞用户' },
        { key: 'blocking_user', title: '阻塞者用户' },
        { key: 'wait_duration', title: '等待时长' },
      ],
    },
    {
      key: 'session_list',
      title: 'PostgreSQL 会话列表',
      type: 'table',
      columns: [
        { key: 'pid', title: 'PID' },
        { key: 'usename', title: '用户名' },
        { key: 'application_name', title: '应用名' },
        { key: 'client_addr', title: '来源IP' },
        { key: 'state', title: '状态' },
        { key: 'wait_event', title: '等待事件' },
        { key: 'query', title: '当前查询' },
      ],
      pagination: true,
    },
    {
      key: 'top_sql',
      title: 'PostgreSQL Top SQL',
      type: 'table',
      showWhen: (data) => data.top_sql && data.top_sql.length > 0,
      columns: [
        { key: 'query', title: '查询文本' },
        { key: 'calls', title: '调用次数', format: 'number' },
        { key: 'total_time_ms', title: '总耗时(ms)', format: 'number' },
        { key: 'mean_time_ms', title: '平均耗时(ms)', format: 'number' },
        { key: 'rows', title: '返回行数', format: 'number' },
      ],
    },
    {
      key: 'table_size_top20',
      title: 'PostgreSQL Top 20 表大小',
      type: 'table',
      showWhen: (data) => data.table_size_top20 && data.table_size_top20.length > 0,
      columns: [
        { key: 'schema_name', title: '模式名' },
        { key: 'table_name', title: '表名' },
        { key: 'total_size_mb', title: '总大小(MB)', format: 'number' },
        { key: 'table_size_mb', title: '表数据(MB)', format: 'number' },
        { key: 'indexes_size_mb', title: '索引大小(MB)', format: 'number' },
      ],
    },
    {
      key: 'unused_indexes',
      title: 'PostgreSQL 未使用索引',
      type: 'table',
      showWhen: (data) => data.unused_indexes && data.unused_indexes.length > 0,
      columns: [
        { key: 'schemaname', title: '模式名' },
        { key: 'tablename', title: '表名' },
        { key: 'indexname', title: '索引名' },
        { key: 'index_size_mb', title: '索引大小(MB)', format: 'number' },
      ],
    },
    {
      key: 'tables_needing_vacuum',
      title: 'PostgreSQL 需VACUUM的表',
      type: 'table',
      showWhen: (data) => data.tables_needing_vacuum && data.tables_needing_vacuum.length > 0,
      columns: [
        { key: 'schemaname', title: '模式名' },
        { key: 'tablename', title: '表名' },
        { key: 'n_dead_tup', title: '死元组数', format: 'number' },
        { key: 'last_vacuum', title: '上次VACUUM' },
        { key: 'last_autovacuum', title: '上次AutoVacuum' },
      ],
    },
    {
      key: 'sequences',
      title: 'PostgreSQL 序列使用率',
      type: 'table',
      showWhen: (data) => data.sequences && data.sequences.length > 0,
      columns: [
        { key: 'sequence_name', title: '序列名' },
        { key: 'last_value', title: '当前值', format: 'number' },
        { key: 'max_value', title: '最大值', format: 'number' },
        { key: 'usage_pct', title: '使用率', format: 'percent' },
      ],
    },
  ],

  // ==================== 达梦 DM8 ====================
  dm: [
    {
      key: 'basic',
      title: '达梦 DM8 基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'instance_name', label: '实例名', format: 'text' },
        { key: 'host_name', label: '主机名', format: 'text' },
        { key: 'db_mode', label: '数据库模式', format: 'text' },
        { key: 'arch_mode', label: '归档模式', format: 'text' },
      ],
    },
    {
      key: 'session',
      title: '达梦 连接与会话',
      metrics: [
        { key: 'active_sessions', label: '活跃会话', format: 'number', highlight: true },
        { key: 'total_sessions', label: '总会话数', format: 'number' },
        { key: 'max_sessions', label: '最大会话数', format: 'number' },
        { key: 'conn_usage_pct', label: '连接使用率', format: 'percent', highlight: true },
        { key: 'session_wait_count', label: '等待会话', format: 'number' },
      ],
    },
    {
      key: 'buffer',
      title: '达梦 缓冲池',
      metrics: [
        { key: 'buffer_hit_ratio', label: '命中率', format: 'percent', highlight: true },
        { key: 'buffer_size_mb', label: '缓冲池大小(MB)', format: 'size_mb' },
      ],
    },
    {
      key: 'performance',
      title: '达梦 性能指标',
      metrics: [
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'tps', label: 'TPS', format: 'number' },
        { key: 'sql_count', label: 'SQL执行数', format: 'number' },
        { key: 'tran_count', label: '事务数', format: 'number' },
      ],
    },
    {
      key: 'transaction',
      title: '达梦 事务统计',
      metrics: [
        { key: 'active_transactions', label: '活跃事务数', format: 'number' },
        { key: 'idle_transactions', label: '空闲事务数', format: 'number' },
      ],
    },
    {
      key: 'dw_cluster',
      title: '达梦 DW 主备集群',
      showWhen: (data) => data.dw_replication_health !== undefined,
      metrics: [
        { key: 'dm_instance_mode', label: '实例模式', format: 'text' },
        { key: 'dm_database_mode', label: '数据库模式', format: 'text' },
        { key: 'realtime_archive_dest', label: '实时归档目标', format: 'text' },
        { key: 'rlog_sync_status', label: 'RLOG同步状态', format: 'text' },
        { key: 'dest_pending', label: '待发送', format: 'number' },
        { key: 'apply_delay_total', label: '应用延迟(ms)', format: 'number' },
        { key: 'dw_replication_health', label: 'DW集群健康', format: 'text', highlight: true },
      ],
    },
    {
      key: 'dsc_cluster',
      title: '达梦 DSC 集群',
      showWhen: (data) => data.dsc_cluster_health !== undefined,
      metrics: [
        { key: 'dsc_node_count', label: '集群节点数', format: 'number' },
        { key: 'dsc_primary_node', label: '主节点', format: 'text' },
        { key: 'dsc_lock_contention_count', label: '锁争用次数', format: 'number' },
        { key: 'dsc_cluster_health', label: 'DSC集群健康', format: 'text', highlight: true },
      ],
    },
    {
      key: 'log_stats',
      title: '达梦 日志统计',
      metrics: [
        { key: 'log_count', label: '日志文件数', format: 'number' },
        { key: 'log_size', label: '日志大小', format: 'bytes' },
        { key: 'login_count', label: '登录次数', format: 'number' },
        { key: 'failed_logins', label: '失败登录', format: 'number' },
      ],
    },
    // === 表格型分类 ===
    {
      key: 'tablespaces',
      title: '达梦 表空间',
      type: 'table',
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'total_mb', title: '总大小(MB)', format: 'number' },
        { key: 'used_mb', title: '已用(MB)', format: 'number' },
        { key: 'free_mb', title: '剩余(MB)', format: 'number' },
        { key: 'used_pct', title: '使用率', format: 'percent' },
      ],
      rowClick: 'tablespace',
    },
    {
      key: 'temp_tablespaces',
      title: '达梦 临时表空间',
      type: 'table',
      showWhen: (data) => data.temp_tablespaces && data.temp_tablespaces.length > 0,
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
      ],
    },
    {
      key: 'datafile_stats',
      title: '达梦 数据文件统计',
      type: 'table',
      showWhen: (data) => data.datafile_stats && data.datafile_stats.length > 0,
      columns: [
        { key: 'file_name', title: '文件名' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
        { key: 'status', title: '状态' },
      ],
    },
    {
      key: 'locks',
      title: '达梦 锁等待',
      type: 'table',
      columns: [
        { key: 'blocker_session', title: '阻塞会话' },
        { key: 'waiter_session', title: '等待会话' },
        { key: 'lock_type', title: '锁类型' },
        { key: 'duration_seconds', title: '持续时间(秒)' },
      ],
    },
    {
      key: 'wait_events',
      title: '达梦 等待事件',
      type: 'table',
      columns: [
        { key: 'event', title: '事件名' },
        { key: 'total_waits', title: '总等待次数', format: 'number' },
        { key: 'time_waited', title: '总等待时间(ms)', format: 'number' },
      ],
      rowClick: 'waitEvent',
    },
    {
      key: 'session_list',
      title: '达梦 会话列表',
      type: 'table',
      columns: [
        { key: 'session_id', title: '会话ID' },
        { key: 'user_name', title: '用户名' },
        { key: 'state', title: '状态' },
        { key: 'program', title: '程序' },
        { key: 'client_host', title: '来源主机' },
        { key: 'wait_event', title: '等待事件' },
        { key: 'sql_text', title: 'SQL文本' },
      ],
      pagination: true,
    },
    {
      key: 'slow_queries',
      title: '达梦 慢查询',
      type: 'table',
      showWhen: (data) => data.slow_queries && data.slow_queries.length > 0,
      columns: [
        { key: 'sql_text', title: 'SQL文本' },
        { key: 'exec_time_ms', title: '执行时间(ms)', format: 'number' },
        { key: 'exec_count', title: '执行次数', format: 'number' },
      ],
    },
    {
      key: 'top_sql',
      title: '达梦 Top SQL',
      type: 'table',
      showWhen: (data) => data.top_sql && data.top_sql.length > 0,
      columns: [
        { key: 'sql_text', title: 'SQL文本' },
        { key: 'exec_count', title: '执行次数', format: 'number' },
        { key: 'total_time_ms', title: '总耗时(ms)', format: 'number' },
      ],
    },
    {
      key: 'buffer_pools',
      title: '达梦 缓冲池详情',
      type: 'table',
      showWhen: (data) => data.buffer_pools && data.buffer_pools.length > 0,
      columns: [
        { key: 'pool_name', title: '缓冲池名' },
        { key: 'total_mb', title: '总大小(MB)', format: 'number' },
        { key: 'used_mb', title: '已用(MB)', format: 'number' },
        { key: 'hit_ratio', title: '命中率', format: 'percent' },
      ],
    },
    {
      key: 'dsc_instances',
      title: '达梦 DSC 实例列表',
      type: 'table',
      showWhen: (data) => data.dsc_instances && data.dsc_instances.length > 0,
      columns: [
        { key: 'node_name', title: '节点名' },
        { key: 'host_name', title: '主机名' },
        { key: 'status', title: '状态' },
        { key: 'is_primary', title: '是否主节点' },
      ],
    },
    {
      key: 'config_params',
      title: '达梦 关键配置参数',
      type: 'table',
      showWhen: (data) => data.config_params && data.config_params.length > 0,
      columns: [
        { key: 'name', title: '参数名' },
        { key: 'value', title: '当前值' },
        { key: 'default_value', title: '默认值' },
      ],
    },
  ],

  // ==================== GBase 8a ====================
  gbase: [
    {
      key: 'basic',
      title: 'GBase 基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'host_name', label: '主机名', format: 'text' },
      ],
    },
    {
      key: 'session',
      title: 'GBase 连接与会话',
      metrics: [
        { key: 'threads_connected', label: '当前连接数', format: 'number' },
        { key: 'threads_running', label: '活跃线程', format: 'number', highlight: true },
        { key: 'max_connections', label: '最大连接数', format: 'number' },
        { key: 'conn_usage_pct', label: '连接使用率', format: 'percent', highlight: true },
      ],
    },
    {
      key: 'performance',
      title: 'GBase 性能指标',
      metrics: [
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'tps', label: 'TPS', format: 'number' },
      ],
    },
    {
      key: 'cluster',
      title: 'GBase 集群状态',
      metrics: [
        { key: 'gbase_cluster_health', label: '集群健康状态', format: 'text', highlight: true },
        { key: 'gbase_node_count', label: '节点总数', format: 'number' },
      ],
    },
    // === 表格型分类 ===
    {
      key: 'cm_nodes',
      title: 'GBase 管理节点',
      type: 'table',
      showWhen: (data) => data.cm_nodes && data.cm_nodes.length > 0,
      columns: [
        { key: 'node_name', label: '节点名' },
        { key: 'node_ip', title: 'IP地址' },
        { key: 'node_state', title: '状态' },
        { key: 'sync_mode', title: '同步模式' },
      ],
    },
    {
      key: 'dn_nodes',
      title: 'GBase 数据节点',
      type: 'table',
      showWhen: (data) => data.dn_nodes && data.dn_nodes.length > 0,
      columns: [
        { key: 'node_name', label: '节点名' },
        { key: 'node_ip', title: 'IP地址' },
        { key: 'node_state', title: '状态' },
        { key: 'replica_count', title: '副本数' },
      ],
    },
    {
      key: 'replica_info',
      title: 'GBase 副本状态',
      type: 'table',
      showWhen: (data) => data.replica_info && data.replica_info.length > 0,
      columns: [
        { key: 'schema_name', title: '数据库' },
        { key: 'table_name', title: '表名' },
        { key: 'replica_count', title: '副本数' },
        { key: 'healthy_replicas', title: '健康副本数' },
        { key: 'replica_status', title: '状态' },
      ],
    },
    {
      key: 'session_list',
      title: 'GBase 会话列表',
      type: 'table',
      columns: [
        { key: 'id', title: '连接ID' },
        { key: 'user', title: '用户名' },
        { key: 'host', title: '来源主机' },
        { key: 'db', title: '数据库' },
        { key: 'command', title: '命令' },
        { key: 'time_seconds', title: '耗时(秒)' },
        { key: 'state', title: '状态' },
      ],
      pagination: true,
    },
    {
      key: 'cluster_issues',
      title: 'GBase 集群问题',
      type: 'table',
      showWhen: (data) => data.cluster_issues && data.cluster_issues.length > 0,
      columns: [
        { key: 'type', title: '问题类型' },
        { key: 'node', title: '受影响节点' },
        { key: 'description', title: '描述' },
      ],
    },
  ],

  // ==================== TDSQL ====================
  tdsql: [
    {
      key: 'basic',
      title: 'TDSQL 基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'host_name', label: '主机名', format: 'text' },
        { key: 'tdsql_conn_type', label: '连接类型', format: 'text' },
        { key: 'ssl_enabled', label: 'SSL状态', format: 'boolean' },
        { key: 'ssl_cipher', label: 'SSL加密套件', format: 'text' },
      ],
    },
    {
      key: 'session',
      title: 'TDSQL 连接与会话',
      metrics: [
        { key: 'threads_connected', label: '当前连接数', format: 'number' },
        { key: 'threads_running', label: '活跃线程', format: 'number', highlight: true },
        { key: 'max_connections', label: '最大连接数', format: 'number' },
        { key: 'conn_usage_pct', label: '连接使用率', format: 'percent', highlight: true },
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'tps', label: 'TPS', format: 'number' },
      ],
    },
    {
      key: 'innodb_buffer',
      title: 'TDSQL InnoDB 缓冲池',
      metrics: [
        { key: 'innodb_buffer_pool_size_mb', label: '缓冲池大小(MB)', format: 'size_mb' },
        { key: 'innodb_buffer_pool_hit_ratio', label: '命中率', format: 'percent', highlight: true },
        { key: 'innodb_buffer_pool_pages_free', label: '空闲页数', format: 'number' },
        { key: 'innodb_buffer_pool_pages_dirty', label: '脏页数', format: 'number' },
        { key: 'buffer_dirty_ratio', label: '脏页比例', format: 'percent' },
      ],
    },
    {
      key: 'performance',
      title: 'TDSQL 性能指标',
      metrics: [
        { key: 'innodb_rows_read_ps', label: '行读取/s', format: 'number' },
        { key: 'innodb_rows_inserted_ps', label: '行插入/s', format: 'number' },
        { key: 'innodb_rows_updated_ps', label: '行更新/s', format: 'number' },
        { key: 'innodb_rows_deleted_ps', label: '行删除/s', format: 'number' },
        { key: 'innodb_deadlocks', label: '死锁次数', format: 'number', highlight: true },
        { key: 'slow_queries', label: '慢查询数', format: 'number' },
      ],
    },
    {
      key: 'innodb_io',
      title: 'TDSQL InnoDB IO',
      metrics: [
        { key: 'innodb_data_reads_ps', label: '数据读取/s', format: 'number' },
        { key: 'innodb_data_writes_ps', label: '数据写入/s', format: 'number' },
        { key: 'innodb_log_waits_ps', label: '日志等待/s', format: 'number' },
        { key: 'innodb_os_log_written_ps', label: '日志写入量/s', format: 'bytes' },
      ],
    },
    {
      key: 'cache',
      title: 'TDSQL 缓存效率',
      metrics: [
        { key: 'table_open_cache_hit_ratio', label: '表缓存命中率', format: 'percent' },
        { key: 'key_buffer_hit_ratio', label: 'Key Buffer命中率', format: 'percent' },
        { key: 'thread_cache_hit_ratio', label: '线程缓存命中率', format: 'percent' },
      ],
    },
    {
      key: 'cluster',
      title: 'TDSQL 集群状态',
      metrics: [
        { key: 'tdsql_cluster_health', label: '集群健康', format: 'text', highlight: true },
        { key: 'tdsql_zk_node_count', label: 'ZK节点数', format: 'number' },
        { key: 'tdsql_proxy_count', label: 'Proxy节点数', format: 'number' },
        { key: 'tdsql_dn_total_count', label: '数据节点总数', format: 'number' },
        { key: 'tdsql_dn_primary_count', label: '主数据节点', format: 'number' },
      ],
    },
    // === 表格型分类 ===
    {
      key: 'zk_nodes',
      title: 'TDSQL ZooKeeper 节点',
      type: 'table',
      showWhen: (data) => data.zk_nodes && data.zk_nodes.length > 0,
      columns: [
        { key: 'node_name', title: '节点名' },
        { key: 'node_ip', title: 'IP地址' },
        { key: 'center', title: '中心' },
        { key: 'status', title: '状态' },
      ],
    },
    {
      key: 'proxy_nodes',
      title: 'TDSQL Proxy 节点',
      type: 'table',
      showWhen: (data) => data.proxy_nodes && data.proxy_nodes.length > 0,
      columns: [
        { key: 'node_name', title: '节点名' },
        { key: 'node_ip', title: 'IP地址' },
        { key: 'center', title: '中心' },
        { key: 'status', title: '状态' },
        { key: 'conn_count', title: '连接数' },
      ],
    },
    {
      key: 'dn_nodes',
      title: 'TDSQL 数据节点 (分片)',
      type: 'table',
      showWhen: (data) => data.dn_nodes && data.dn_nodes.length > 0,
      columns: [
        { key: 'node_name', title: '节点名' },
        { key: 'node_ip', title: 'IP地址' },
        { key: 'shard_name', title: '分片名' },
        { key: 'replica_role', title: '副本角色' },
        { key: 'center', title: '中心' },
        { key: 'status', title: '状态' },
        { key: 'replication_lag', title: '复制延迟(秒)' },
      ],
    },
    {
      key: 'session_list',
      title: 'TDSQL 会话列表',
      type: 'table',
      columns: [
        { key: 'id', title: '连接ID' },
        { key: 'user', title: '用户名' },
        { key: 'host', title: '来源主机' },
        { key: 'db', title: '数据库' },
        { key: 'command', title: '命令' },
        { key: 'time_seconds', title: '耗时(秒)' },
        { key: 'state', title: '状态' },
      ],
      pagination: true,
    },
    {
      key: 'top_sql',
      title: 'TDSQL Top SQL',
      type: 'table',
      showWhen: (data) => data.top_sql_by_latency && data.top_sql_by_latency.length > 0,
      columns: [
        { key: 'sql_text', title: 'SQL文本' },
        { key: 'schema_name', title: '数据库' },
        { key: 'exec_count', title: '执行次数', format: 'number' },
        { key: 'total_latency_sec', title: '总耗时(秒)', format: 'number' },
        { key: 'avg_latency_sec', title: '平均耗时(秒)', format: 'number' },
        { key: 'rows_examined', title: '扫描行数', format: 'number' },
        { key: 'no_index_used', title: '未用索引', format: 'number' },
      ],
    },
    {
      key: 'unused_indexes',
      title: 'TDSQL 未使用索引',
      type: 'table',
      showWhen: (data) => data.unused_indexes && data.unused_indexes.length > 0,
      columns: [
        { key: 'schema_name', title: '数据库名' },
        { key: 'table_name', title: '表名' },
        { key: 'index_name', title: '索引名' },
      ],
    },
    {
      key: 'cluster_issues',
      title: 'TDSQL 集群问题',
      type: 'table',
      showWhen: (data) => data.cluster_issues && data.cluster_issues.length > 0,
      columns: [
        { key: 'type', title: '问题类型' },
        { key: 'component', title: '组件' },
        { key: 'node', title: '受影响节点' },
        { key: 'description', title: '描述' },
      ],
    },
    {
      key: 'cross_center_sync',
      title: 'TDSQL 跨中心同步状态',
      type: 'table',
      showWhen: (data) => data.cross_center_sync && data.cross_center_sync.length > 0,
      columns: [
        { key: 'from_center', title: '源中心' },
        { key: 'to_center', title: '目标中心' },
        { key: 'sync_status', title: '同步状态' },
        { key: 'lag_seconds', title: '延迟(秒)' },
      ],
    },
    {
      key: 'table_size_top20',
      title: 'TDSQL Top 20 表大小',
      type: 'table',
      showWhen: (data) => data.table_size_top20 && data.table_size_top20.length > 0,
      columns: [
        { key: 'schema', title: '数据库名' },
        { key: 'table_name', title: '表名' },
        { key: 'size_mb', title: '大小(MB)', format: 'number' },
        { key: 'rows', title: '行数', format: 'number' },
      ],
    },
  ],
};

/**
 * 格式化指标值
 */
export const formatMetricValue = (value, format) => {
  if (value === null || value === undefined) return '-';
  switch (format) {
    case 'percent':
      return `${Number(value).toFixed(1)}%`;
    case 'size_mb':
      return `${Number(value).toFixed(0)} MB`;
    case 'bytes':
      if (value > 1073741824) return `${(value / 1073741824).toFixed(1)} GB`;
      if (value > 1048576) return `${(value / 1048576).toFixed(1)} MB`;
      if (value > 1024) return `${(value / 1024).toFixed(1)} KB`;
      return `${value} B`;
    case 'duration': {
      const s = Number(value);
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      if (d > 0) return `${d}d ${h}h`;
      if (h > 0) return `${h}h ${m}m`;
      if (m > 0) return `${m}m`;
      return `${s.toFixed(0)}s`;
    }
    case 'number':
      return Number(value).toLocaleString();
    case 'boolean':
      return value ? '是' : '否';
    case 'status':
      return value === 'Yes' || value === true || value === 'Yes' ? '🟢 运行中' : '🔴 已停止';
    case 'text':
    default:
      return String(value);
  }
};

/**
 * 获取格式化的值（纯数字，用于图表等）
 */
export const getMetricRawValue = (value) => {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'number') return value.toFixed(2);
  return String(value);
};

/**
 * 指标阈值颜色配置
 */
const THRESHOLD_CONFIG = {
  conn_usage_pct: { warn: 70, error: 85, critical: 95 },
  tablespace_used_pct: { warn: 80, error: 90, critical: 95 },
  innodb_buffer_pool_hit_ratio: { warn_low: 95, error_low: 90 },
  cache_hit_ratio: { warn_low: 95, error_low: 90 },
  buffer_hit_ratio: { warn_low: 95, error_low: 90 },
  library_cache_hit_ratio: { warn_low: 95, error_low: 90 },
  seconds_behind_master: { warn: 10, error: 30, critical: 60 },
  deadlocks: { warn: 1, error: 5 },
  innodb_deadlocks: { warn: 1, error: 5 },
  active_connections: { warn: 80, error: 150, critical: 200 },
};

/**
 * 获取指标阈值颜色
 */
export const getMetricThresholdColor = (value, metricKey) => {
  if (value === null || value === undefined) return '#999';
  const t = THRESHOLD_CONFIG[metricKey];
  if (!t) return undefined; // 未定义阈值时不强制颜色

  // 向上方向阈值 (值越大越危险)
  if (t.critical && value >= t.critical) return '#ff4d4f';
  if (t.error && value >= t.error) return '#fa8c16';
  if (t.warn && value >= t.warn) return '#faad14';

  // 向下方向阈值 (值越小越危险)
  if (t.critical_low && value <= t.critical_low) return '#ff4d4f';
  if (t.error_low && value <= t.error_low) return '#fa8c16';
  if (t.warn_low && value <= t.warn_low) return '#faad14';

  return '#52c41a';
};

/**
 * 获取数据库类型对应的指标分类
 */
export const getMetricCategories = (dbType) => {
  if (!dbType) return [];
  return DB_METRIC_CATEGORIES[dbType?.toLowerCase()] || [];
};
