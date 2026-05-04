# DB-AIOps 下一阶段开发设计方案 v3.0

> **设计日期**: 2026-05-03
> **当前版本**: v2.2 → **目标版本**: v3.0
> **阶段主题**: 从框架完整走向数据完整
> **预计工期**: P0(2-4周) + P1(3-6周) = 总计 5-10 周

---

## 第一部分：高阶设计 (High-Level Design)

### 1.1 架构演进总览

```
v2.2 现状                              v3.0 目标
┌────────────────────┐           ┌────────────────────────────┐
│ start_monitor.py   │           │ start_monitor.py (调度器)    │
│ 4622行 单体文件     │           │ ~300行 编排层               │
│ 6 Checker + 调度    │  ────▶   │ monitor/checkers/           │
│ + 引擎调用          │           │  ├── oracle.py              │
│                    │           │  ├── mysql.py               │
│                    │           │  ├── pgsql.py               │
│                    │           │  ├── dm.py                  │
│                    │           │  ├── gbase.py               │
│                    │           │  └── tdsql.py               │
└────────────────────┘           └────────────────────────────┘

┌────────────────────┐           ┌────────────────────────────┐
│ 仅写 MonitorLog    │           │ 三分流写入管道               │
│ (PG ORM JSON)      │           │ MonitorLog (PG ORM)         │
│                    │  ────▶    │ + TimescaleDB (时序指标)     │
│                    │           │ + Elasticsearch (搜索聚合)   │
└────────────────────┘           └────────────────────────────┘

┌────────────────────┐           ┌────────────────────────────┐
│ Oracle 专用详情页   │           │ DB 类型自适应详情页           │
│ DatabaseDetail.jsx  │  ────▶    │ DatabaseDetail.jsx          │
│ 硬编码 Oracle 指标  │           │ + DB_TYPE_METRICS 配置      │
└────────────────────┘           └────────────────────────────┘
```

### 1.2 数据流重新设计

```
                    ┌──────────────────────┐
                    │   start_monitor.py   │
                    │   (统一调度器)         │
                    └──────┬───────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
    ┌───────────┐  ┌───────────┐  ┌───────────┐
    │ Oracle    │  │  MySQL    │  │  PGSQL    │  ... (6个Checker)
    │ Checker   │  │  Checker  │  │  Checker  │
    └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
          │              │              │
          └──────────────┼──────────────┘
                         │
                    process_result()
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ MonitorLog  │  │ TimescaleDB │  │Elasticsearch│
│ (PG ORM)    │  │ (时序指标)   │  │ (全量文档)   │
│ 兼容保留     │  │ 数值型指标   │  │ 搜索/聚合    │
└─────────────┘  └──────┬──────┘  └──────┬──────┘
                        │                │
                        ▼                ▼
              ┌─────────────────────────────────┐
              │        查询路由层                 │
              │  DatabaseMetricsView            │
              │  DatabasePredictionView         │
              │  - 时序查询 → TimescaleDB        │
              │  - 搜索聚合 → Elasticsearch      │
              │  - 兼容回退 → MonitorLog         │
              └─────────────────────────────────┘
```

### 1.3 模块重构规划

```
monitor/
├── management/commands/
│   └── start_monitor.py          # 仅保留调度器 (~300行)
│
├── checkers/                     # NEW: Checker 模块目录
│   ├── __init__.py               # 导出 CHECKER_MAP
│   ├── base.py                   # BaseDBChecker 基类
│   ├── oracle.py                 # OracleChecker
│   ├── mysql.py                  # MySQLChecker
│   ├── pgsql.py                  # PostgreSQLChecker
│   ├── dm.py                     # DamengChecker
│   ├── gbase.py                  # GbaseChecker
│   └── tdsql.py                  # TDSQLChecker
│
├── engines/                      # (已有, 保持不变)
│   ├── alert_engine.py
│   ├── baseline_engine.py
│   ├── rca_engine.py
│   └── ...
│
├── storage/                      # (已有, 补充集成)
│   ├── timeseries.py             # TimescaleDB 读写
│   └── elasticsearch_engine.py   # ES 读写
│
├── models.py                     # 数据模型
├── api_views.py                  # REST API
├── auth.py                       # 认证授权
└── ...                           # 其他模块保持不变
```

### 1.4 前端自适应架构

```
frontend/src/
├── pages/
│   └── DatabaseDetail.jsx        # 重构为 DB 类型自适应
│       - 从 props.dbType 决定渲染内容
│       - 引入 DB_METRICS_CONFIG 配置对象
│
├── config/
│   └── dbMetricsConfig.js        # NEW: 各DB类型指标配置
│       - 每种 DB 类型的指标分组
│       - 指标显示名称、格式化函数
│       - 图表颜色映射
│       - 阈值配置
│
├── components/
│   └── MetricsChart.jsx          # 通用指标图表组件
│   └── MetricCard.jsx            # NEW: 通用指标卡片组件
│   └── TablespacePanel.jsx       # NEW: 表空间面板组件
│   └── SessionPanel.jsx          # NEW: 会话面板组件
```

---

## 第二部分：详细设计 (Detailed Design)

---

### P0-1：打通多层存储写入管道

#### 2.1.1 背景

当前 `process_result()` 已经实现了部分 TimescaleDB 写入（第 4368-4383 行），但存在以下问题：
1. ES 写入完全缺失 —— `index_metrics()` 未在任何数据管道中被调用
2. TimescaleDB 写入仅提取顶层数值型指标，嵌套结构的指标（如 `tablespaces` 列表中的 `used_pct`）被忽略
3. 没有写入失败的重试/降级机制
4. API 查询层仍全部走 `MonitorLog` 表，未利用 TimescaleDB 的时序查询性能优势

#### 2.1.2 设计方案

**A. 完善 process_result() 中的写入管道**

在 `start_monitor.py` 的 `process_result()` 方法中，保持现有逻辑并增强：

```python
def process_result(self, config, current_status, data):
    # ... 现有告警逻辑保持不变 ...
    
    # --- 3. 记录监控日志 (兼容保留) ---
    MonitorLog.objects.create(
        config=config,
        status=current_status,
        message=json.dumps(data, ensure_ascii=False, default=str)
    )
    
    # --- 4. 写入 TimescaleDB (增强版) ---
    self._write_to_timescaledb(config, current_status, data)
    
    # --- 5. 写入 Elasticsearch (新增) ---
    self._write_to_elasticsearch(config, current_status, data)
```

**B. TimescaleDB 写入增强**

改造 `_write_to_timescaledb()` 方法，提取所有嵌套数值型指标：

```python
def _write_to_timescaledb(self, config, current_status, data):
    """写入 TimescaleDB - 深入提取所有数值型指标"""
    try:
        from monitor.timeseries import get_timeseries_storage
        ts = get_timeseries_storage()
        if not ts.enabled:
            return
        
        numeric_metrics = {}
        
        def extract_metrics(obj, prefix=''):
            """递归提取嵌套结构中的数值型指标"""
            if isinstance(obj, dict):
                for k, v in obj.items():
                    full_key = f"{prefix}.{k}" if prefix else k
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        numeric_metrics[full_key] = float(v)
                    elif isinstance(v, dict):
                        extract_metrics(v, full_key)
                    elif isinstance(v, list):
                        # 处理列表中的数值（如 tablespaces 的 used_pct）
                        for i, item in enumerate(v):
                            if isinstance(item, dict):
                                extract_metrics(item, f"{full_key}[{i}]")
        
        extract_metrics(data)
        
        if numeric_metrics:
            ts.write_metrics_batch(config.id, numeric_metrics, status=current_status)
        
        ts.write_snapshot(config.id, current_status, data)
    except Exception as e:
        logger.warning(f"[TSDB] TimescaleDB 写入失败 (将降级到 MonitorLog): {e}")
```

**C. Elasticsearch 写入新增**

```python
def _write_to_elasticsearch(self, config, current_status, data):
    """写入 Elasticsearch - 支持全文搜索和聚合分析"""
    try:
        from monitor.elasticsearch_engine import index_metrics
        index_metrics(
            config_id=config.id,
            db_type=config.db_type,
            db_name=config.name,
            host=config.host,
            port=config.port,
            environment=config.environment or '',
            status=current_status,
            metrics=data,
            collect_duration_ms=0,  # 可从 checker 返回中获取
        )
    except Exception as e:
        logger.warning(f"[ES] Elasticsearch 写入失败 (非关键路径): {e}")
```

**D. API 查询层路由优化**

修改 `DatabaseMetricsView`，优先从 TimescaleDB 查询时序数据，回退到 MonitorLog：

```python
# monitor/api_views.py - DatabaseMetricsView.get() 修改

def get(self, request, config_id: int):
    # 检查是否是简单时序查询（单个指标、指定时间范围）
    metric_name = request.GET.get('metric')
    hours = int(request.GET.get('hours', 24))
    
    if metric_name:
        # 优先从 TimescaleDB 查询
        try:
            from monitor.timeseries import get_timeseries_storage
            ts = get_timeseries_storage()
            if ts.enabled:
                granularity = request.GET.get('granularity', 'raw')
                results = ts.query_metric_history(
                    config_id, metric_name, hours=hours, granularity=granularity
                )
                if results:
                    return self.success_response({
                        'metrics': results,
                        'source': 'timescaledb'
                    })
        except Exception:
            pass  # 降级到 MonitorLog
    
    # 降级：从 MonitorLog 表查询 (保持现有逻辑)
    # ... 现有代码 ...
```

**E. ES 搜索增强**

在 `AlertListView` 和 `DatabaseAlertsView` 中添加 ES 搜索支持：

- 当请求包含 `search` 参数时，优先使用 ES 的全文搜索能力
- 当请求包含 `aggregation` 参数时，使用 ES 的聚合能力生成趋势数据
- ES 不可用时自动降级到 PostgreSQL ORM 查询

#### 2.1.3 影响范围

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `monitor/management/commands/start_monitor.py` | 修改 | 增强 `process_result()`，新增 `_write_to_timescaledb()`, `_write_to_elasticsearch()` |
| `monitor/timeseries.py` | 无需修改 | API 已完备 |
| `monitor/elasticsearch_engine.py` | 无需修改 | API 已完备 |
| `monitor/api_views.py` | 修改 | `DatabaseMetricsView` 添加 TimescaleDB 查询优先路由 |

---

### P0-2：补充 MySQL/PostgreSQL/DM8 指标采集

#### 2.2.1 背景

根据 `METRICS_DESIGN.md` 分析，当前指标覆盖率：

| DB 类型 | 当前指标数 | 目标指标数 | 覆盖率 | P0 补充目标 |
|---------|-----------|-----------|--------|------------|
| MySQL | ~45 | ~100 | ~45% | +25 项 → ~70% |
| PostgreSQL | ~35 | ~110 | ~32% | +25 项 → ~55% |
| DM8 | ~30 | ~50 | ~60% | +10 项 → ~80% |

#### 2.2.2 MySQL 补充指标清单

优先级按 **对告警/诊断的直接影响** 排序：

**P0-A (高优先级 - 直接影响告警质量):**

| # | 指标名 | 说明 | 数据源 | 当前状态 |
|---|--------|------|--------|---------|
| 1 | `innodb_buffer_pool_hit_ratio` | InnoDB 缓冲池命中率 | `SHOW GLOBAL STATUS` | ❌ |
| 2 | `innodb_buffer_pool_pages_free` | 缓冲池空闲页数 | `SHOW GLOBAL STATUS 'Innodb_buffer_pool_pages_free'` | ❌ |
| 3 | `innodb_buffer_pool_pages_dirty` | 缓冲池脏页比例 | 计算值 | ❌ |
| 4 | `innodb_rows_read/s` | InnoDB 行读取速率 | `SHOW GLOBAL STATUS` (差值) | ❌ |
| 5 | `innodb_rows_inserted/s` | InnoDB 行插入速率 | `SHOW GLOBAL STATUS` (差值) | ❌ |
| 6 | `innodb_rows_updated/s` | InnoDB 行更新速率 | `SHOW GLOBAL STATUS` (差值) | ❌ |
| 7 | `innodb_rows_deleted/s` | InnoDB 行删除速率 | `SHOW GLOBAL STATUS` (差值) | ❌ |
| 8 | `table_open_cache_hit_ratio` | 表缓存命中率 | 计算: hits/(hits+misses) | ❌ |
| 9 | `open_files_usage` | 打开文件使用率 | `Opened_files` / `open_files_limit` | ❌ |
| 10 | `aborted_connects` | 中止连接数 | `SHOW GLOBAL STATUS 'Aborted_connects'` | ❌ |

**P0-B (中优先级 - 增强诊断能力):**

| # | 指标名 | 说明 | 数据源 |
|---|--------|------|--------|
| 11 | `binlog_size_total_mb` | Binlog 总大小 | `SHOW MASTER LOGS` |
| 12 | `slave_io_running` | 从库 IO 线程状态 | `SHOW SLAVE STATUS` |
| 13 | `slave_sql_running` | 从库 SQL 线程状态 | `SHOW SLAVE STATUS` |
| 14 | `seconds_behind_master` | 从库复制延迟 | `SHOW SLAVE STATUS` |
| 15 | `gtid_mode` | GTID 模式 | `@@gtid_mode` |
| 16 | `binlog_format` | Binlog 格式 | `@@binlog_format` |
| 17 | `innodb_log_file_size_mb` | InnoDB 日志文件大小 | `@@innodb_log_file_size` |
| 18 | `max_connect_errors_reached` | 是否达到最大连接错误 | `@@max_connect_errors` 对比 |
| 19 | `innodb_deadlocks` | InnoDB 死锁计数 | `SHOW GLOBAL STATUS 'Innodb_deadlocks'` |
| 20 | `slow_queries_rate` | 慢查询产生速率 | `Slow_queries` 差值 |

**P0-C (低优先级 - 运维辅助):**

| # | 指标名 | 说明 | 数据源 |
|---|--------|------|--------|
| 21 | `key_buffer_hit_ratio` | MyISAM 键缓存命中率 | `SHOW GLOBAL STATUS` |
| 22 | `thread_cache_hit_ratio` | 线程缓存命中率 | `SHOW GLOBAL STATUS` |
| 23 | `tmp_table_disk_rate` | 磁盘临时表比例 | `SHOW GLOBAL STATUS` |
| 24 | `innodb_buffer_pool_read_requests/s` | 缓冲池逻辑读速率 | `SHOW GLOBAL STATUS` (差值) |
| 25 | `innodb_buffer_pool_reads/s` | 缓冲池物理读速率 | `SHOW GLOBAL STATUS` (差值) |

#### 2.2.3 PostgreSQL 补充指标清单

**P0-A (高优先级):**

| # | 指标名 | 说明 | 数据源 |
|---|--------|------|--------|
| 1 | `cache_hit_ratio` | 缓存命中率 | `pg_stat_database.blks_hit / (blks_hit + blks_read)` |
| 2 | `tps` | 每秒事务数 | `(xact_commit + xact_rollback) / uptime` |
| 3 | `deadlocks` | 死锁计数 | `pg_stat_database.deadlocks` |
| 4 | `temp_files_count` | 临时文件数 | `pg_stat_database.temp_files` |
| 5 | `temp_bytes_total` | 临时文件总大小 | `pg_stat_database.temp_bytes` |
| 6 | `idle_in_transaction_count` | 事务中空闲连接 | `pg_stat_activity WHERE state='idle in transaction'` |
| 7 | `waiting_connections` | 等待中连接数 | `pg_stat_activity WHERE wait_event IS NOT NULL` |
| 8 | `autovacuum_workers` | 自动 VACUUM 工作进程 | `pg_stat_activity WHERE backend_type='autovacuum worker'` |
| 9 | `longest_query_seconds` | 最长运行查询时间 | `pg_stat_activity WHERE state='active'` |
| 10 | `replication_lag_bytes` | 复制延迟(字节) | `pg_stat_replication` |

**P0-B (中优先级):**

| # | 指标名 | 说明 | 数据源 |
|---|--------|------|--------|
| 11 | `blk_read_time_ms` | 块读取耗时 | `pg_stat_database` |
| 12 | `blk_write_time_ms` | 块写入耗时 | `pg_stat_database` |
| 13 | `buffers_checkpoint` | 检查点写入缓冲数 | `pg_stat_bgwriter` |
| 14 | `buffers_backend` | 后端写入缓冲数 | `pg_stat_bgwriter` |
| 15 | `buffers_backend_fsync` | 后端 fsync 次数 | `pg_stat_bgwriter` |
| 16 | `maxwritten_clean` | 清理扫描最大写入 | `pg_stat_bgwriter` |
| 17 | `transaction_id_age` | 事务 ID 年龄 | `age(pg_current_xact_id(), xmin)` |
| 18 | `wal_write_lag` | WAL 写入延迟 | `pg_stat_replication` |
| 19 | `wal_flush_lag` | WAL 刷盘延迟 | `pg_stat_replication` |
| 20 | `wal_replay_lag` | WAL 重放延迟 | `pg_stat_replication` |

**P0-C (低优先级):**

| # | 指标名 | 说明 | 数据源 |
|---|--------|------|--------|
| 21 | `seq_scans_total` | 全表扫描总数 | `pg_stat_user_tables.sum(seq_scan)` |
| 22 | `idx_scans_total` | 索引扫描总数 | `pg_stat_user_tables.sum(idx_scan)` |
| 23 | `n_live_tup_total` | 活跃元组总数 | `pg_stat_user_tables.sum(n_live_tup)` |
| 24 | `n_dead_tup_total` | 死元组总数 | `pg_stat_user_tables.sum(n_dead_tup)` |
| 25 | `database_size_bytes` | 数据库总大小 | `pg_database_size(current_database())` |

#### 2.2.4 DM8 补充指标清单

**P0-A (高优先级):**

| # | 指标名 | 说明 | 数据源 |
|---|--------|------|--------|
| 1 | `buffer_hit_ratio` | 缓冲池命中率 | `V$BUFFER` |
| 2 | `active_sessions` | 活跃会话数 | `V$SESSIONS WHERE STATE='ACTIVE'` |
| 3 | `session_usage_pct` | 会话使用率 | `COUNT(V$SESSIONS) / MAX_SESSIONS` |
| 4 | `arch_mode` | 归档模式 | `V$DATABASE` |
| 5 | `db_name` | 数据库名 | `V$INSTANCE` |
| 6 | `tps` | 每秒事务数 | `V$SYSTEM_INFO` |
| 7 | `deadlock_count` | 死锁计数 | `V$DEADLOCK_HISTORY` |
| 8 | `temp_usage` | 临时表空间使用率 | `V$SORT_OVERFLOW` |
| 9 | `undo_usage` | UNDO 使用率 | `V$UNDOFIL` |
| 10 | `arch_gap` | 归档间隙 | `V$ARCH_FILE` |

#### 2.2.5 实现方式

每个 Checker 的 `collect_metrics()` 方法中，在现有指标采集之后追加新的指标采集代码块。遵循现有代码风格：

```python
# 示例: MySQLChecker 补充 innodb_buffer_pool_hit_ratio
# 在 collect_metrics() 方法中追加

# --- P0: InnoDB 缓冲池命中率 ---
try:
    cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")
    pool_reads = int(cursor.fetchone()[1])
    cursor.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read_requests'")
    pool_read_requests = int(cursor.fetchone()[1])
    if pool_read_requests > 0:
        result['innodb_buffer_pool_hit_ratio'] = round(
            (1 - pool_reads / pool_read_requests) * 100, 2
        )
except Exception as e:
    pass  # 静默降级，不影响其他指标采集
```

#### 2.2.6 影响范围

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `monitor/management/commands/start_monitor.py` | 修改 | MySQLChecker 追加 ~25 个指标；PostgreSQLChecker 追加 ~25 个指标；DamengChecker 追加 ~10 个指标 |
| `METRICS_DESIGN.md` | 修改 | 更新各 DB 类型的实现状态标记 |

---

### P0-3：前端支持多 DB 类型详情页

#### 2.3.1 背景

`DatabaseDetail.jsx` 当前硬编码 Oracle 指标分类（如 SGA、PGA、RAC 等），MySQL/PostgreSQL 等数据库的详情页缺少对应的分类展示。

#### 2.3.2 设计方案

**A. 创建 DB 类型指标配置 (frontend/src/config/dbMetricsConfig.js)**

```javascript
// frontend/src/config/dbMetricsConfig.js
// 每种数据库类型的指标分组配置

export const DB_TYPE_LABELS = {
  oracle: 'Oracle',
  mysql: 'MySQL',
  pgsql: 'PostgreSQL',
  dm: '达梦DM8',
  gbase: 'GBase 8a',
  tdsql: 'TDSQL',
};

export const DB_METRIC_CATEGORIES = {
  oracle: [
    {
      key: 'basic',
      title: '基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'instance_name', label: '实例名', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'log_mode', label: '归档模式', format: 'text' },
        { key: 'db_role', label: '数据库角色', format: 'text' },
      ]
    },
    {
      key: 'session',
      title: '连接与会话',
      metrics: [
        { key: 'active_sessions', label: '活跃会话', format: 'number' },
        { key: 'max_connections', label: '最大连接数', format: 'number' },
        { key: 'conn_usage_pct', label: '连接使用率', format: 'percent' },
        { key: 'total_sessions', label: '总会话数', format: 'number' },
      ]
    },
    {
      key: 'sga',
      title: 'SGA 内存',
      metrics: [
        { key: 'sga_total_mb', label: 'SGA 总量(MB)', format: 'size_mb' },
        { key: 'sga_target_mb', label: 'SGA Target(MB)', format: 'size_mb' },
        { key: 'buffer_cache_mb', label: 'Buffer Cache(MB)', format: 'size_mb' },
        { key: 'shared_pool_mb', label: 'Shared Pool(MB)', format: 'size_mb' },
        { key: 'sga_used_mb', label: 'SGA 已用(MB)', format: 'size_mb' },
      ]
    },
    {
      key: 'pga',
      title: 'PGA 内存',
      metrics: [
        { key: 'pga_target_mb', label: 'PGA Target(MB)', format: 'size_mb' },
        { key: 'pga_used_mb', label: 'PGA 已用(MB)', format: 'size_mb' },
        { key: 'pga_max_mb', label: 'PGA 最大(MB)', format: 'size_mb' },
      ]
    },
    {
      key: 'performance',
      title: '性能指标',
      metrics: [
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'tps', label: 'TPS', format: 'number' },
        { key: 'buffer_hit_ratio', label: 'Buffer命中率', format: 'percent' },
        { key: 'library_cache_hit_ratio', label: 'Library Cache命中率', format: 'percent' },
      ]
    },
    {
      key: 'rac',
      title: 'RAC 集群',
      showWhen: (data) => data.rac_active === true,
      metrics: [
        { key: 'rac_instances', label: '实例数', format: 'number' },
        { key: 'rac_current_instance', label: '当前实例号', format: 'number' },
        { key: 'gcs_msgs_sent', label: 'GCS 消息发送/s', format: 'number' },
        { key: 'ges_msgs_sent', label: 'GES 消息发送/s', format: 'number' },
        { key: 'gc_cr_blocks_served', label: 'GC CR 块服务/s', format: 'number' },
        { key: 'gc_current_blocks_served', label: 'GC Current 块服务/s', format: 'number' },
      ]
    },
    {
      key: 'adg',
      title: 'ADG 备库',
      showWhen: (data) => data.dg_role && data.dg_role !== 'PRIMARY',
      metrics: [
        { key: 'dg_role', label: 'DG 角色', format: 'text' },
        { key: 'dg_apply_lag_seconds', label: 'Apply 延迟(秒)', format: 'number' },
        { key: 'dg_transport_lag_seconds', label: '传输延迟(秒)', format: 'number' },
        { key: 'dg_mrp_status', label: 'MRP 进程状态', format: 'text' },
        { key: 'dg_sequence', label: 'DG 日志序列号', format: 'number' },
      ]
    },
    {
      key: 'wait',
      title: '等待事件',
      type: 'table',
      columns: [
        { key: 'event', title: '事件名称' },
        { key: 'total_waits', title: '等待次数' },
        { key: 'time_waited', title: '等待时间(秒)' },
        { key: 'avg_wait_ms', title: '平均等待(ms)' },
      ]
    },
    {
      key: 'tablespace',
      title: '表空间',
      type: 'table',
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'total_mb', title: '总大小(MB)' },
        { key: 'used_mb', title: '已用(MB)' },
        { key: 'free_mb', title: '剩余(MB)' },
        { key: 'used_pct', title: '使用率' },
        { key: 'auto_extensible', title: '自动扩展' },
      ]
    },
    {
      key: 'tablespace_undo',
      title: 'Undo 表空间',
      type: 'table',
      showWhen: (data) => data.hasOwnProperty('undo_tablespaces'),
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'total_mb', title: '总大小(MB)' },
        { key: 'used_mb', title: '已用(MB)' },
        { key: 'free_mb', title: '剩余(MB)' },
        { key: 'used_pct', title: '使用率' },
      ]
    },
    {
      key: 'tablespace_temp',
      title: 'Temp 表空间',
      type: 'table',
      showWhen: (data) => data.hasOwnProperty('temp_tablespaces'),
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'total_mb', title: '总大小(MB)' },
        { key: 'used_mb', title: '已用(MB)' },
        { key: 'free_mb', title: '剩余(MB)' },
        { key: 'used_pct', title: '使用率' },
      ]
    },
  ],

  mysql: [
    {
      key: 'basic',
      title: '基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'server_id', label: 'Server ID', format: 'number' },
      ]
    },
    {
      key: 'session',
      title: '连接与会话',
      metrics: [
        { key: 'threads_connected', label: '当前连接数', format: 'number' },
        { key: 'threads_running', label: '活跃线程数', format: 'number' },
        { key: 'max_connections', label: '最大连接数', format: 'number' },
        { key: 'conn_usage_pct', label: '连接使用率', format: 'percent' },
        { key: 'aborted_connects', label: '异常断开', format: 'number' },
      ]
    },
    {
      key: 'innodb_buffer',
      title: 'InnoDB 缓冲池',
      metrics: [
        { key: 'innodb_buffer_pool_size_mb', label: '缓冲池大小(MB)', format: 'size_mb' },
        { key: 'innodb_buffer_pool_hit_ratio', label: '命中率', format: 'percent' },
        { key: 'innodb_buffer_pool_pages_free', label: '空闲页数', format: 'number' },
        { key: 'innodb_buffer_pool_pages_dirty', label: '脏页比例', format: 'percent' },
      ]
    },
    {
      key: 'performance',
      title: '性能指标',
      metrics: [
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'tps', label: 'TPS', format: 'number' },
        { key: 'innodb_rows_read_ps', label: '行读取/s', format: 'number' },
        { key: 'innodb_rows_inserted_ps', label: '行插入/s', format: 'number' },
        { key: 'innodb_rows_updated_ps', label: '行更新/s', format: 'number' },
        { key: 'innodb_rows_deleted_ps', label: '行删除/s', format: 'number' },
        { key: 'innodb_deadlocks', label: '死锁次数', format: 'number' },
      ]
    },
    {
      key: 'replication',
      title: '主从复制',
      showWhen: (data) => data.replication_role,
      metrics: [
        { key: 'replication_role', label: '复制角色', format: 'text' },
        { key: 'slave_io_running', label: 'IO线程', format: 'status' },
        { key: 'slave_sql_running', label: 'SQL线程', format: 'status' },
        { key: 'seconds_behind_master', label: '延迟(秒)', format: 'number' },
        { key: 'gtid_mode', label: 'GTID模式', format: 'text' },
      ]
    },
    {
      key: 'innodb_io',
      title: 'InnoDB IO',
      metrics: [
        { key: 'innodb_data_reads_ps', label: '数据读取/s', format: 'number' },
        { key: 'innodb_data_writes_ps', label: '数据写入/s', format: 'number' },
        { key: 'innodb_log_waits_ps', label: '日志等待/s', format: 'number' },
        { key: 'innodb_os_log_written_ps', label: '日志写入量/s', format: 'bytes' },
      ]
    },
    {
      key: 'cache',
      title: '缓存效率',
      metrics: [
        { key: 'table_open_cache_hit_ratio', label: '表缓存命中率', format: 'percent' },
        { key: 'key_buffer_hit_ratio', label: 'Key Buffer命中率', format: 'percent' },
        { key: 'thread_cache_hit_ratio', label: '线程缓存命中率', format: 'percent' },
      ]
    },
    {
      key: 'space',
      title: '空间使用',
      type: 'table',
      columns: [
        { key: 'name', title: '数据库名' },
        { key: 'size_mb', title: '大小(MB)' },
        { key: 'table_count', title: '表数量' },
      ]
    },
    {
      key: 'tablespace_innodb',
      title: 'InnoDB 表空间',
      type: 'table',
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'total_mb', title: '总大小(MB)' },
        { key: 'used_mb', title: '已用(MB)' },
        { key: 'free_mb', title: '剩余(MB)' },
      ]
    },
    {
      key: 'top_sql',
      title: 'Top SQL',
      type: 'table',
      columns: [
        { key: 'sql_text', title: 'SQL文本' },
        { key: 'exec_count', title: '执行次数' },
        { key: 'total_latency', title: '总耗时' },
        { key: 'rows_examined', title: '扫描行数' },
      ]
    },
  ],

  pgsql: [
    {
      key: 'basic',
      title: '基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'current_database', label: '当前数据库', format: 'text' },
        { key: 'is_in_recovery', label: '恢复模式', format: 'boolean' },
      ]
    },
    {
      key: 'session',
      title: '连接与会话',
      metrics: [
        { key: 'active_connections', label: '活跃连接', format: 'number' },
        { key: 'idle_connections', label: '空闲连接', format: 'number' },
        { key: 'idle_in_transaction', label: '事务中空闲', format: 'number' },
        { key: 'total_connections', label: '总连接数', format: 'number' },
        { key: 'max_connections', label: '最大连接数', format: 'number' },
        { key: 'conn_usage_pct', label: '连接使用率', format: 'percent' },
        { key: 'waiting_connections', label: '等待连接', format: 'number' },
      ]
    },
    {
      key: 'performance',
      title: '性能指标',
      metrics: [
        { key: 'cache_hit_ratio', label: '缓存命中率', format: 'percent' },
        { key: 'tps', label: 'TPS', format: 'number' },
        { key: 'deadlocks', label: '死锁次数', format: 'number' },
        { key: 'temp_files', label: '临时文件数', format: 'number' },
        { key: 'temp_bytes_mb', label: '临时文件大小(MB)', format: 'size_mb' },
        { key: 'blk_read_time_ms', label: '块读耗时(ms)', format: 'number' },
        { key: 'blk_write_time_ms', label: '块写耗时(ms)', format: 'number' },
      ]
    },
    {
      key: 'bgwriter',
      title: '后台写入',
      metrics: [
        { key: 'buffers_checkpoint', label: 'CKPT写入', format: 'number' },
        { key: 'buffers_backend', label: '后端写入', format: 'number' },
        { key: 'buffers_clean', label: '清理写入', format: 'number' },
        { key: 'maxwritten_clean', label: '清理最大写入', format: 'number' },
        { key: 'buffers_backend_fsync', label: '后端Fsync', format: 'number' },
      ]
    },
    {
      key: 'replication',
      title: '流复制',
      showWhen: (data) => data.replication_lag_bytes !== undefined,
      metrics: [
        { key: 'replication_lag_bytes', label: '复制延迟(字节)', format: 'bytes' },
        { key: 'wal_write_lag_ms', label: 'WAL写延迟(ms)', format: 'number' },
        { key: 'wal_flush_lag_ms', label: 'WAL刷延迟(ms)', format: 'number' },
        { key: 'wal_replay_lag_ms', label: 'WAL回放延迟(ms)', format: 'number' },
      ]
    },
    {
      key: 'autovacuum',
      title: 'AutoVacuum',
      metrics: [
        { key: 'autovacuum_workers', label: '工作进程数', format: 'number' },
        { key: 'n_dead_tup_total', label: '死元组总数', format: 'number' },
        { key: 'transaction_id_age', label: '事务ID年龄', format: 'number' },
      ]
    },
    {
      key: 'space',
      title: '空间使用',
      type: 'table',
      columns: [
        { key: 'name', title: '数据库/表空间' },
        { key: 'size_mb', title: '大小(MB)' },
      ]
    },
    {
      key: 'locks',
      title: '锁等待',
      type: 'table',
      columns: [
        { key: 'blocked_pid', title: '被阻塞PID' },
        { key: 'blocking_pid', title: '阻塞者PID' },
        { key: 'blocked_query', title: '被阻塞查询' },
        { key: 'lock_type', title: '锁类型' },
      ]
    },
  ],

  dm: [
    {
      key: 'basic',
      title: '基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'db_name', label: '数据库名', format: 'text' },
        { key: 'arch_mode', label: '归档模式', format: 'text' },
        { key: 'mode_status', label: '数据库模式', format: 'text' },
      ]
    },
    {
      key: 'session',
      title: '连接与会话',
      metrics: [
        { key: 'sessions', label: '当前会话数', format: 'number' },
        { key: 'active_sessions', label: '活跃会话', format: 'number' },
        { key: 'max_sessions', label: '最大会话数', format: 'number' },
        { key: 'session_usage_pct', label: '会话使用率', format: 'percent' },
      ]
    },
    {
      key: 'buffer',
      title: '缓冲池',
      metrics: [
        { key: 'buffer_hit_ratio', label: '命中率', format: 'percent' },
        { key: 'buffer_pages', label: '缓冲页数', format: 'number' },
        { key: 'read_pages', label: '读取页数', format: 'number' },
        { key: 'write_pages', label: '写入页数', format: 'number' },
      ]
    },
    {
      key: 'performance',
      title: '性能指标',
      metrics: [
        { key: 'tps', label: 'TPS', format: 'number' },
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'deadlock_count', label: '死锁次数', format: 'number' },
      ]
    },
    {
      key: 'dsc',
      title: 'DSC 集群',
      showWhen: (data) => data.dsc_active === true,
      metrics: [
        { key: 'dsc_nodes', label: '集群节点数', format: 'number' },
        { key: 'dsc_voting_disk', label: '投票盘状态', format: 'text' },
      ]
    },
    {
      key: 'tablespace',
      title: '表空间',
      type: 'table',
      columns: [
        { key: 'name', title: '表空间名' },
        { key: 'total_mb', title: '总大小(MB)' },
        { key: 'used_mb', title: '已用(MB)' },
        { key: 'free_mb', title: '剩余(MB)' },
        { key: 'used_pct', title: '使用率' },
      ]
    },
    {
      key: 'locks',
      title: '锁等待',
      type: 'table',
      columns: [
        { key: 'blocker_session', title: '阻塞会话' },
        { key: 'waiter_session', title: '等待会话' },
        { key: 'lock_type', title: '锁类型' },
        { key: 'duration_seconds', title: '持续时间(秒)' },
      ]
    },
  ],

  gbase: [
    {
      key: 'basic',
      title: '基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'cluster_name', label: '集群名称', format: 'text' },
        { key: 'node_count', label: '节点数量', format: 'number' },
      ]
    },
    {
      key: 'session',
      title: '连接与会话',
      metrics: [
        { key: 'threads_connected', label: '当前连接数', format: 'number' },
        { key: 'threads_running', label: '活跃线程', format: 'number' },
        { key: 'max_connections', label: '最大连接数', format: 'number' },
      ]
    },
    {
      key: 'performance',
      title: '性能指标',
      metrics: [
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'tps', label: 'TPS', format: 'number' },
      ]
    },
    {
      key: 'cluster',
      title: '集群状态',
      type: 'table',
      columns: [
        { key: 'node_name', title: '节点名' },
        { key: 'node_ip', title: 'IP地址' },
        { key: 'node_state', title: '状态' },
        { key: 'sync_mode', title: '同步模式' },
      ]
    },
  ],

  tdsql: [
    {
      key: 'basic',
      title: '基础信息',
      metrics: [
        { key: 'version', label: '版本', format: 'text' },
        { key: 'uptime_seconds', label: '运行时间', format: 'duration' },
        { key: 'shard_count', label: '分片数量', format: 'number' },
      ]
    },
    {
      key: 'session',
      title: '连接与会话',
      metrics: [
        { key: 'threads_connected', label: '当前连接数', format: 'number' },
        { key: 'threads_running', label: '活跃线程', format: 'number' },
        { key: 'max_connections', label: '最大连接数', format: 'number' },
      ]
    },
    {
      key: 'performance',
      title: '性能指标',
      metrics: [
        { key: 'qps', label: 'QPS', format: 'number' },
        { key: 'tps', label: 'TPS', format: 'number' },
      ]
    },
    {
      key: 'shard',
      title: '分片状态',
      type: 'table',
      columns: [
        { key: 'shard_name', title: '分片名' },
        { key: 'status', title: '状态' },
        { key: 'qps', title: 'QPS' },
        { key: 'replication_lag', title: '复制延迟(秒)' },
      ]
    },
  ],
};

// 格式化函数
export const formatMetricValue = (value, format) => {
  if (value === null || value === undefined) return '-';
  switch (format) {
    case 'percent': return `${Number(value).toFixed(1)}%`;
    case 'size_mb': return `${Number(value).toFixed(0)} MB`;
    case 'bytes': 
      if (value > 1073741824) return `${(value/1073741824).toFixed(1)} GB`;
      if (value > 1048576) return `${(value/1048576).toFixed(1)} MB`;
      if (value > 1024) return `${(value/1024).toFixed(1)} KB`;
      return `${value} B`;
    case 'duration':
      const s = Number(value);
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      if (d > 0) return `${d}d ${h}h`;
      if (h > 0) return `${h}h ${m}m`;
      return `${m}m`;
    case 'number': return Number(value).toLocaleString();
    case 'boolean': return value ? '是' : '否';
    case 'status': return value === 'Yes' || value === true ? '🟢 运行中' : '🔴 已停止';
    case 'text':
    default: return String(value);
  }
};

// 获取指标的阈值颜色
export const getMetricThresholdColor = (value, metricKey) => {
  if (value === null || value === undefined) return '#999';
  const thresholds = {
    conn_usage_pct: { warn: 70, error: 85, critical: 95 },
    tablespace_used_pct: { warn: 80, error: 90, critical: 95 },
    innodb_buffer_pool_hit_ratio: { warn_low: 95, error_low: 90 },
    cache_hit_ratio: { warn_low: 95, error_low: 90 },
    seconds_behind_master: { warn: 10, error: 30, critical: 60 },
    deadlocks: { warn: 1, error: 5 },
  };
  const t = thresholds[metricKey];
  if (!t) return '#52c41a'; // 默认绿色
  if (t.critical && value >= t.critical) return '#ff4d4f';
  if (t.error && value >= t.error) return '#fa8c16';
  if (t.warn && value >= t.warn) return '#faad14';
  if (t.critical_low && value <= t.critical_low) return '#ff4d4f';
  if (t.error_low && value <= t.error_low) return '#fa8c16';
  if (t.warn_low && value <= t.warn_low) return '#faad14';
  return '#52c41a';
};
```

**B. 重构 DatabaseDetail.jsx**

核心思路：从 DB 类型配置中动态读取要展示的指标分类，渲染通用组件。

```jsx
// 重构后的 DatabaseDetail.jsx 核心逻辑

import { DB_METRIC_CATEGORIES, DB_TYPE_LABELS, formatMetricValue, getMetricThresholdColor } from '../config/dbMetricsConfig';

const DatabaseDetail = () => {
  const { id } = useParams();
  const [dbConfig, setDbConfig] = useState(null);
  const [metricData, setMetricData] = useState({});
  const [loading, setLoading] = useState(true);
  
  // 获取当前 DB 类型的指标分类配置
  const categories = useMemo(() => {
    if (!dbConfig) return [];
    return DB_METRIC_CATEGORIES[dbConfig.db_type] || [];
  }, [dbConfig]);

  // 渲染指标分类区块
  const renderCategory = (category) => {
    // 检查 showWhen 条件
    if (category.showWhen && !category.showWhen(metricData)) {
      return null;
    }

    if (category.type === 'table') {
      return renderTableCategory(category);
    }
    return renderMetricCardsCategory(category);
  };

  // 渲染指标卡片分类
  const renderMetricCardsCategory = (category) => {
    return (
      <Card title={category.title} key={category.key} style={{ marginBottom: 16 }}>
        <Row gutter={[16, 16]}>
          {category.metrics.map(metric => {
            const value = metricData[metric.key];
            const color = getMetricThresholdColor(value, metric.key);
            return (
              <Col span={6} key={metric.key}>
                <Card 
                  size="small" 
                  hoverable 
                  onClick={() => handleMetricClick(metric.key, metric.label, value)}
                >
                  <Statistic
                    title={metric.label}
                    value={formatMetricValue(value, metric.format)}
                    valueStyle={{ color, fontSize: 20 }}
                  />
                </Card>
              </Col>
            );
          })}
        </Row>
      </Card>
    );
  };

  // 渲染表格分类
  const renderTableCategory = (category) => {
    const dataSource = metricData[category.key] || [];
    return (
      <Card title={category.title} key={category.key} style={{ marginBottom: 16 }}>
        <Table
          dataSource={dataSource}
          columns={category.columns.map(col => ({
            title: col.title,
            dataIndex: col.key,
            key: col.key,
            render: (val) => {
              if (col.key === 'used_pct') {
                return <Progress percent={Number(val).toFixed(1)} size="small" 
                  strokeColor={val > 90 ? '#ff4d4f' : val > 80 ? '#faad14' : '#52c41a'} />;
              }
              if (col.key === 'sql_text') {
                return <Text ellipsis style={{ maxWidth: 400 }}>{val}</Text>;
              }
              return formatMetricValue(val, 'text');
            }
          }))}
          rowKey={(record, idx) => record.name || record.event || idx}
          size="small"
          pagination={dataSource.length > 10 ? { pageSize: 10 } : false}
        />
      </Card>
    );
  };

  return (
    <div>
      <PageHeader title={`${dbConfig?.name} - ${DB_TYPE_LABELS[dbConfig?.db_type]}`} />
      {categories.map(renderCategory)}
    </div>
  );
};
```

#### 2.3.3 影响范围

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `frontend/src/config/dbMetricsConfig.js` | **新建** | 所有 DB 类型的指标分组配置 |
| `frontend/src/pages/DatabaseDetail.jsx` | **重构** | 从硬编码 Oracle 改为配置驱动 |
| `frontend/src/components/MetricCard.jsx` | **新建** | 通用指标卡片组件 |
| `frontend/src/components/TablespacePanel.jsx` | **新建** | 表空间面板组件 |
| `frontend/src/components/SessionPanel.jsx` | **新建** | 会话面板组件 |

---

### P1-1：统一 Celery 与 ThreadPool 采集体系

#### 2.4.1 背景

当前存在两个独立的采集路径：
- `start_monitor.py` 的 `monitor_job()` → `ThreadPoolExecutor` → `_run_single_check()`
- `tasks.py` 的 `collect_single_db()` → Celery Worker

两者互不通信，基线计算/容量预测等周期性任务在 Celery beat 中也有独立调度。

#### 2.4.2 设计方案

**方案 A（推荐）：start_monitor.py 作为调度层，Celery 作为执行层**

```
┌──────────────────────────────┐
│  start_monitor.py (调度器)    │
│  - 每60秒触发 monitor_job()   │
│  - 查询活跃数据库列表          │
│  - 异步 dispatch 到 Celery    │
│  - 等待结果、处理告警          │
└──────────┬───────────────────┘
           │ dispatch
           ▼
┌──────────────────────────────┐
│  Celery Workers              │
│  - collect_single_db(config)  │
│  - 独立进程/容器执行          │
│  - 支持水平扩展               │
│  - 超时自动 kill              │
└──────────────────────────────┘
```

**实现步骤:**

1. 修改 `monitor_job()` 使用 Celery 异步调用：

```python
def monitor_job(self):
    print(f"\n[{datetime.datetime.now()}] --- 开始新一轮巡检 ---")
    
    configs = list(DatabaseConfig.objects.filter(is_active=True))
    if not configs:
        return
    
    from monitor.tasks import collect_single_db
    
    # 异步 dispatch 到 Celery workers
    async_results = {}
    for cfg in configs:
        result = collect_single_db.delay(cfg.id)
        async_results[cfg] = result
    
    # 等待结果 (带超时)
    for cfg, result in async_results.items():
        try:
            status, data = result.get(timeout=COLLECT_TIMEOUT_SEC)
            self.process_result(cfg, status, data)
        except Exception as e:
            self.process_result(cfg, 'DOWN', {'error': str(e)})
```

2. 修改 `tasks.py` 的 `collect_single_db` 返回结构化结果：

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def collect_single_db(self, config_id):
    """采集单个数据库，返回 (status, data) 元组"""
    from monitor.models import DatabaseConfig
    from monitor.db_connector import DbConnector
    from monitor.checkers import get_checker
    
    try:
        config = DatabaseConfig.objects.get(id=config_id)
        checker_class = get_checker(config.db_type)
        if not checker_class:
            return ('UNKNOWN', {'error': f'不支持的数据库类型: {config.db_type}'})
        
        conn = DbConnector.get_connection(config)
        checker = checker_class()
        data = checker.collect_metrics(config, conn)
        conn.close()
        return ('UP', data)
    except Exception as e:
        return ('DOWN', {'error': str(e)})
```

3. 统一 Celery beat schedule 中的周期性任务触发源：

   - 基线和容量预测由 Celery beat 独立触发（保持现有 tasks.py 逻辑）
   - 采集由 `start_monitor.py` 调度，通过 Celery 执行
   - 移除 `tasks.py` 中与 `start_monitor.py` 重复的采集调度

#### 2.4.3 影响范围

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `monitor/management/commands/start_monitor.py` | 修改 | `monitor_job()` 改用 Celery dispatch |
| `monitor/tasks.py` | 修改 | `collect_single_db()` 返回结构化结果 |
| `monitor/checkers/__init__.py` | 新建 | 提供 `get_checker()` 工厂函数 |

---

### P1-2：实施数据库列表 UX 改进方案

#### 2.5.1 背景

详见 [`plans/database-list-ux-design.md`](plans/database-list-ux-design.md)，该方案已详细定义了改进目标。

#### 2.5.2 设计方案

按照原方案的 Phase 1/2/3 执行：

**Phase 1 - 基础增强：**

1. 重构数据获取逻辑，使用 `Promise.all` 批量并行请求 status/health/alerts API
2. 添加健康分列（绿≥80 / 黄≥60 / 红<60 颜色编码）
3. 添加告警数徽章列（数字徽章，红>0 / 绿=0）
4. 优化卡片统计区（新增健康库/亚健康库/问题库/告警中库统计）

**Phase 2 - 高级功能：**

5. 添加关键指标列（CPU使用率、连接使用率、表空间使用率）
6. 实现智能排序（问题库优先 = `(100-健康分)*10 + 告警数*5 + (DOWN?1000:0)`）
7. 添加排序切换下拉菜单
8. 添加数据时效提示

**Phase 3 - 性能优化：**

9. 前端缓存机制（status 30s / health 5min / alerts 1min）
10. 加载骨架屏优化
11. 防抖刷新 (debounce 2s)

#### 2.5.3 影响范围

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `frontend/src/pages/DatabaseList.jsx` | 重构 | 按照 Phase 1/2/3 计划修改 |

---

### P1-3：告警阈值模板批量初始化

#### 2.6.1 背景

`AlertThresholdTemplate` 模型已就绪，但没有任何初始数据。需要为每种 DB 类型的每个指标创建默认阈值模板。

#### 2.6.2 设计方案

编写 Django 管理命令 `init_alert_templates`，从指标清单批量创建：

```python
# monitor/management/commands/init_alert_templates.py

from django.core.management.base import BaseCommand
from monitor.models import AlertThresholdTemplate, MetricDefinition

# 默认告警阈值配置（基于业界标准）
DEFAULT_TEMPLATES = {
    'oracle': [
        {
            'metric_key': 'conn_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70, 'error_threshold': 85, 'critical_threshold': 95,
            'direction': 'up', 'persistence_count': 3,
        },
        {
            'metric_key': 'tablespace_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 80, 'error_threshold': 90, 'critical_threshold': 95,
            'direction': 'up', 'persistence_count': 3,
        },
        {
            'metric_key': 'buffer_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95, 'error_threshold': 90, 'critical_threshold': 85,
            'direction': 'down', 'persistence_count': 5,
        },
        # ... 更多指标
    ],
    'mysql': [
        {
            'metric_key': 'conn_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70, 'error_threshold': 85, 'critical_threshold': 95,
            'direction': 'up', 'persistence_count': 3,
        },
        {
            'metric_key': 'innodb_buffer_pool_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95, 'error_threshold': 90, 'critical_threshold': 85,
            'direction': 'down', 'persistence_count': 5,
        },
        {
            'metric_key': 'seconds_behind_master',
            'rule_type': 'static_threshold',
            'warn_threshold': 10, 'error_threshold': 30, 'critical_threshold': 60,
            'direction': 'up', 'persistence_count': 3,
        },
        {
            'metric_key': 'innodb_deadlocks',
            'rule_type': 'static_threshold',
            'warn_threshold': 1, 'error_threshold': 5, 'critical_threshold': 10,
            'direction': 'up', 'persistence_count': 3,
        },
        # ... 更多指标
    ],
    'pgsql': [
        {
            'metric_key': 'conn_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70, 'error_threshold': 85, 'critical_threshold': 95,
            'direction': 'up', 'persistence_count': 3,
        },
        {
            'metric_key': 'cache_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95, 'error_threshold': 90, 'critical_threshold': 85,
            'direction': 'down', 'persistence_count': 5,
        },
        {
            'metric_key': 'deadlocks',
            'rule_type': 'static_threshold',
            'warn_threshold': 1, 'error_threshold': 3, 'critical_threshold': 5,
            'direction': 'up', 'persistence_count': 3,
        },
        {
            'metric_key': 'replication_lag_bytes',
            'rule_type': 'static_threshold',
            'warn_threshold': 1048576, 'error_threshold': 10485760, 'critical_threshold': 104857600,
            'direction': 'up', 'persistence_count': 3,
        },
        # ... 更多指标
    ],
    'dm': [
        {
            'metric_key': 'session_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70, 'error_threshold': 85, 'critical_threshold': 95,
            'direction': 'up', 'persistence_count': 3,
        },
        {
            'metric_key': 'buffer_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95, 'error_threshold': 90, 'critical_threshold': 85,
            'direction': 'down', 'persistence_count': 5,
        },
        # ... 更多指标
    ],
}

class Command(BaseCommand):
    help = '初始化/更新告警阈值模板（幂等操作）'
    
    def handle(self, *args, **options):
        created = 0
        updated = 0
        
        for db_type, templates in DEFAULT_TEMPLATES.items():
            for tpl in templates:
                obj, is_new = AlertThresholdTemplate.objects.update_or_create(
                    db_type=db_type,
                    metric_key=tpl['metric_key'],
                    defaults={
                        'rule_type': tpl['rule_type'],
                        'warn_threshold': tpl.get('warn_threshold'),
                        'error_threshold': tpl.get('error_threshold'),
                        'critical_threshold': tpl.get('critical_threshold'),
                        'direction': tpl.get('direction', 'both'),
                        'persistence_count': tpl.get('persistence_count', 3),
                        'is_enabled': True,
                    }
                )
                if is_new:
                    created += 1
                else:
                    updated += 1
        
        self.stdout.write(self.style.SUCCESS(
            f'模板初始化完成: 新建 {created}, 更新 {updated}'
        ))
```

运行方式：
```bash
python manage.py init_alert_templates
```

#### 2.6.3 影响范围

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `monitor/management/commands/init_alert_templates.py` | **新建** | 告警模板批量初始化命令 |

---

### P1-4：拆分 start_monitor.py

#### 2.7.1 背景

`start_monitor.py` 当前 4622 行，违反单一职责原则。需要将 6 个 Checker 类提取到独立文件。

#### 2.7.2 设计方案

**A. 目录结构**

```
monitor/
├── checkers/
│   ├── __init__.py          # 导出 CHECKER_MAP 和工厂函数
│   ├── base.py              # BaseDBChecker 基类
│   ├── oracle.py            # OracleChecker (~950行)
│   ├── mysql.py             # MySQLChecker (~870行)
│   ├── pgsql.py             # PostgreSQLChecker (~720行)
│   ├── dm.py                # DamengChecker (~730行)
│   ├── gbase.py             # GbaseChecker (~450行)
│   └── tdsql.py             # TDSQLChecker (~510行)
│
├── management/commands/
│   └── start_monitor.py     # 仅保留 Command 类 + process_result (~500行)
```

**B. base.py 内容**

```python
# monitor/checkers/base.py

class BaseDBChecker:
    """数据库检查器基类"""
    
    def __init__(self, command_instance=None):
        self.cmd = command_instance
    
    def get_connection(self, config):
        """获取数据库连接 - 子类实现"""
        raise NotImplementedError
    
    def collect_metrics(self, config, conn):
        """采集指标 - 子类实现"""
        raise NotImplementedError
    
    def check(self, config):
        """统一检查入口"""
        status = 'UP'
        result_data = {}
        conn = None
        
        try:
            conn = self.get_connection(config)
            result_data = self.collect_metrics(config, conn)
        except Exception as e:
            status = 'DOWN'
            result_data = {"error": str(e)}
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
        
        if self.cmd:
            self.cmd.process_result(config, status, result_data)
        else:
            return status, result_data
    
    def db_label(self):
        return self.__class__.__name__.replace('Checker', '')
```

**C. __init__.py 内容**

```python
# monitor/checkers/__init__.py

from .base import BaseDBChecker
from .oracle import OracleChecker
from .mysql import MySQLChecker
from .pgsql import PostgreSQLChecker
from .dm import DamengChecker
from .gbase import GbaseChecker
from .tdsql import TDSQLChecker

CHECKER_MAP = {
    'oracle': OracleChecker,
    'mysql': MySQLChecker,
    'pgsql': PostgreSQLChecker,
    'dm': DamengChecker,
    'gbase': GbaseChecker,
    'tdsql': TDSQLChecker,
}

def get_checker(db_type: str):
    """获取 Checker 类"""
    return CHECKER_MAP.get(db_type)
```

**D. start_monitor.py 改造后**

删除所有 Checker 类定义（约 4100 行），保留：
- `Command` 类（调度器）
- `process_result()` 方法（引擎集成 + 告警逻辑）
- `_run_phase2_analysis()` 方法
- `_write_to_timescaledb()` 方法
- `_write_to_elasticsearch()` 方法
- `send_alert()` 方法
- `_build_lock_msg()` 方法

CHECKER_MAP 改为从 `monitor.checkers` 导入。

**E. tasks.py 适配**

```python
# monitor/tasks.py - 适配拆分后的导入

from monitor.checkers import get_checker  # 替代原来的直接导入

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def collect_single_db(self, config_id):
    # ... 
    checker_class = get_checker(config.db_type)
    # ...
```

#### 2.7.3 影响范围

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `monitor/checkers/__init__.py` | **新建** | CHECKER_MAP + 工厂函数 |
| `monitor/checkers/base.py` | **新建** | BaseDBChecker 基类 |
| `monitor/checkers/oracle.py` | **新建** | OracleChecker (从 start_monitor.py 提取) |
| `monitor/checkers/mysql.py` | **新建** | MySQLChecker (从 start_monitor.py 提取) |
| `monitor/checkers/pgsql.py` | **新建** | PostgreSQLChecker (从 start_monitor.py 提取) |
| `monitor/checkers/dm.py` | **新建** | DamengChecker (从 start_monitor.py 提取) |
| `monitor/checkers/gbase.py` | **新建** | GbaseChecker (从 start_monitor.py 提取) |
| `monitor/checkers/tdsql.py` | **新建** | TDSQLChecker (从 start_monitor.py 提取) |
| `monitor/management/commands/start_monitor.py` | **重构** | 移除 Checker 类，保留调度器 |
| `monitor/tasks.py` | 修改 | 导入路径适配 |

---

### P2-1：Redis/MongoDB 监控 (概要设计)

**Redis 监控：**
- 实现 `RedisChecker`，通过 `redis` Python 库连接
- 执行 `INFO` 命令采集所有 section（Server/Clients/Memory/Persistence/Stats/Replication/CPU/Cluster）
- 解析为结构化指标数据
- 特殊指标：`keyspace_hits` / `keyspace_misses` → 缓存命中率；`used_memory_rss` → 内存使用；`connected_clients` → 连接数

**MongoDB 监控：**
- 实现 `MongoChecker`，通过 `pymongo` 连接
- 执行 `serverStatus` 命令采集：connections/opcounters/locks/wiredTiger/network/globalLock
- 执行 `replSetGetStatus` 获取复制集状态

---

### P2-2：Gbase8a/TDSQL 分布式特性 (概要设计)

**Gbase8a：**
- 补充 `SHOW GCLUSTER STATUS` 集群状态采集
- 补充 `information_schema.GCLUSTER_NODES` 节点详情
- 补充数据分布均衡性检查

**TDSQL：**
- 补充 `SHOW SHARD STATUS` 分片详细状态
- 补充 ZK 节点状态（如果可访问）
- 补充 Proxy 连接池状态

---

### P2-3：Dashboard 增强 (概要设计)

- 全局健康热力图：N×M 网格，每个数据库一个色块（绿/黄/红）
- 趋势对比图：选择多个数据库 + 同一指标（如连接数），叠加折线对比
- 复制拓扑图：使用 D3/ReactFlow 绘制主从复制关系

---

## 第三部分：实施计划

### 3.1 开发顺序

```
Week 1-2:  P0-1 存储管道打通
           ├── Day 1-3: 增强 TimescaleDB 写入（递归提取嵌套指标）
           ├── Day 4-5: 新增 ES 写入管道
           └── Day 6-10: API 查询路由优化 + 测试

Week 3-4:  P0-2 MySQL 指标补充
           ├── Day 1-3: MySQLChecker 追加 25 个指标
           ├── Day 4-6: PostgreSQLChecker 追加 25 个指标
           ├── Day 7-8: DamengChecker 追加 10 个指标
           └── Day 9-10: 联调测试

Week 5-6:  P0-3 前端多DB适配
           ├── Day 1-2: 创建 dbMetricsConfig.js 配置
           ├── Day 3-5: 重构 DatabaseDetail.jsx
           ├── Day 6-7: 创建通用组件 (MetricCard/TablespacePanel)
           └── Day 8-10: 联调测试

Week 6-7:  P1-4 拆分 start_monitor.py
           ├── Day 1-2: 创建 checkers/ 目录结构
           ├── Day 3-4: 提取 6 个Checker 到独立文件
           ├── Day 5-6: 修改 start_monitor.py 适配
           └── Day 7: 回归测试

Week 8:    P1-2 数据库列表 UX 改进
           ├── Day 1-3: Phase 1 (健康分/告警徽章/统计区)
           ├── Day 4-5: Phase 2 (指标列/智能排序)
           └── Day 6: Phase 3 (缓存/性能优化)

Week 9:    P1-3 告警阈值模板初始化
           ├── Day 1-2: 编写 init_alert_templates 命令
           ├── Day 3: 测试运行
           └── Day 4: 阈值校准

Week 10:   P1-1 统一采集体系
           ├── Day 1-3: 改造 monitor_job() 为 Celery dispatch
           ├── Day 4-5: 适配 tasks.py
           └── Day 6: 端到端测试
```

### 3.2 里程碑

| 里程碑 | 时间 | 交付物 |
|--------|------|--------|
| M1: 存储管道完成 | Week 2 结束 | TimescaleDB + ES 写入正常，API 查询走新路径 |
| M2: 指标补充完成 | Week 4 结束 | MySQL/PG/DM 指标覆盖率提升至 55-80% |
| M3: 前端适配完成 | Week 6 结束 | 所有 DB 类型都有适配的详情页 |
| M4: P1 全部完成 | Week 10 结束 | codebase 重构 + UX 改进 + 统一采集 |

### 3.3 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| TimescaleDB 连接不稳定 | 低 | 高 | 写入失败自动降级到 MonitorLog，不影响核心采集 |
| ES 集群不可用 | 中 | 中 | ES 为非关键路径，写入失败仅记录日志 |
| 指标补充引入采集性能下降 | 中 | 中 | 每个新指标独立 try/except，设置采集总超时 |
| start_monitor.py 拆分引入回归 | 低 | 高 | 先建新文件，保留旧文件备份，分步切换 |
| Celery 改造后采集延迟增加 | 低 | 中 | 保持 ThreadPool 作为降级方案 |

---

## 第四部分：验收标准

### P0 验收标准

- [ ] **P0-1**: TimescaleDB 中可查询到所有数值型指标（含嵌套提取的）。ES 中可查询到完整的采集文档。API 查询指标时可在响应中看到 `source: 'timescaledb'`。
- [ ] **P0-2**: MySQL 指标数从 ~45 增至 ≥70。PostgreSQL 指标数从 ~35 增至 ≥60。DM8 指标数从 ~30 增至 ≥40。所有新指标在 Dashboard 中可见。
- [ ] **P0-3**: 打开 MySQL/PostgreSQL/DM8 数据库详情页，能看到对应的指标分类（如 MySQL 的 InnoDB 缓冲池、PG 的 AutoVacuum 等），不再是 Oracle 专用页面。

### P1 验收标准

- [ ] **P1-1**: `monitor_job()` 通过 Celery 异步执行采集任务。Celery worker 日志可见采集节点执行记录。
- [ ] **P1-2**: 数据库列表页新增健康分、告警数、CPU、连接数、表空间列。问题库自动置顶。卡片统计区显示健康/亚健康/问题库统计。
- [ ] **P1-3**: 运行 `python manage.py init_alert_templates` 后，`AlertThresholdTemplate` 表中有 4+ 种 DB 类型的默认模板。
- [ ] **P1-4**: `start_monitor.py` 从 4622 行缩减至 ~500 行。`monitor/checkers/` 目录下有 6 个独立 Checker 文件。回归测试全部通过。

---

## 附录 A：文件变更清单汇总

| # | 文件 | 操作 | 优先级 |
|---|------|------|--------|
| 1 | `monitor/management/commands/start_monitor.py` | 修改（增强管道 + 拆分） | P0/P1 |
| 2 | `monitor/api_views.py` | 修改（查询路由优化） | P0 |
| 3 | `monitor/checkers/__init__.py` | **新建** | P1 |
| 4 | `monitor/checkers/base.py` | **新建** | P1 |
| 5 | `monitor/checkers/oracle.py` | **新建** (提取) | P1 |
| 6 | `monitor/checkers/mysql.py` | **新建** (提取 + 补充指标) | P0/P1 |
| 7 | `monitor/checkers/pgsql.py` | **新建** (提取 + 补充指标) | P0/P1 |
| 8 | `monitor/checkers/dm.py` | **新建** (提取 + 补充指标) | P0/P1 |
| 9 | `monitor/checkers/gbase.py` | **新建** (提取) | P1 |
| 10 | `monitor/checkers/tdsql.py` | **新建** (提取) | P1 |
| 11 | `monitor/tasks.py` | 修改（适配新导入 + 返回格式） | P1 |
| 12 | `monitor/management/commands/init_alert_templates.py` | **新建** | P1 |
| 13 | `frontend/src/config/dbMetricsConfig.js` | **新建** | P0 |
| 14 | `frontend/src/pages/DatabaseDetail.jsx` | 重构 | P0 |
| 15 | `frontend/src/pages/DatabaseList.jsx` | 修改（UX 改进） | P1 |
| 16 | `frontend/src/components/MetricCard.jsx` | **新建** | P0 |
| 17 | `frontend/src/components/TablespacePanel.jsx` | **新建** | P0 |
| 18 | `frontend/src/components/SessionPanel.jsx` | **新建** | P0 |
| 19 | `METRICS_DESIGN.md` | 修改（更新状态标记） | P0 |

---

> **下一阶段主题：「从框架完整走向数据完整」**
>
> 完成以上 P0/P1 后，DB-AIOps 将从一个"框架完整但数据不足"的平台进化为"数据充沛、存储高效、前端体验一致"的生产级数据库运维平台。
