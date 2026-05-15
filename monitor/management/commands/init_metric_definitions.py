# -*- coding: utf-8 -*-
"""
初始化指标定义元数据

从各 checker 模块提取所有采集指标，为每个 metric_key 创建 MetricDefinition 记录。
包含指标显示名、单位、适用数据库类型、告警方向等元信息。

用法:
    python manage.py init_metric_definitions
"""

from django.core.management.base import BaseCommand
from monitor.models import MetricDefinition


# 各数据库类型采集的指标定义
# 格式: metric_key -> {display_name, unit, db_types, alert_direction, sigma_k, is_capacity, description}
METRIC_DEFINITIONS = {
    # === 通用指标 ===
    'active_connections': {
        'display_name': '活跃连接数',
        'unit': 'count',
        'db_types': ['oracle', 'mysql', 'pgsql', 'dm', 'gbase', 'tdsql'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'fixed_warn_val': 80,
        'is_capacity': False,
        'description': '当前活跃的数据库连接数',
    },
    'max_connections': {
        'display_name': '最大连接数',
        'unit': 'count',
        'db_types': ['oracle', 'mysql', 'pgsql', 'dm', 'gbase', 'tdsql'],
        'alert_direction': 'both',
        'is_capacity': True,
        'description': '数据库配置的最大连接数',
    },
    'conn_usage_pct': {
        'display_name': '连接使用率',
        'unit': 'pct',
        'db_types': ['oracle', 'mysql', 'pgsql', 'dm', 'gbase', 'tdsql'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'fixed_warn_val': 80,
        'is_capacity': True,
        'description': '当前连接数/最大连接数 × 100%',
    },
    'qps': {
        'display_name': '每秒查询数',
        'unit': 'qps',
        'db_types': ['mysql', 'pgsql', 'gbase', 'tdsql'],
        'alert_direction': 'down',
        'sigma_k': 2.0,
        'fixed_warn_val': None,
        'is_capacity': False,
        'description': '数据库每秒执行的查询数量',
    },
    'tps': {
        'display_name': '每秒事务数',
        'unit': 'tps',
        'db_types': ['mysql', 'pgsql', 'oracle'],
        'alert_direction': 'down',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '数据库每秒执行的事务数量',
    },

    # === 表空间/存储指标 ===
    'tablespace_usage_pct': {
        'display_name': '表空间使用率',
        'unit': 'pct',
        'db_types': ['oracle', 'pgsql', 'dm'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'fixed_warn_val': 85,
        'is_capacity': True,
        'description': '表空间已使用百分比',
    },
    'database_size_mb': {
        'display_name': '数据库大小',
        'unit': 'mb',
        'db_types': ['mysql', 'pgsql', 'gbase', 'tdsql'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': True,
        'description': '数据库总大小(MB)',
    },

    # === 性能指标 ===
    'slow_queries_total': {
        'display_name': '慢查询总数',
        'unit': 'count',
        'db_types': ['mysql', 'pgsql', 'gbase', 'tdsql', 'oracle'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '检测到的慢查询总数',
    },
    'slow_queries_active': {
        'display_name': '活跃慢查询数',
        'unit': 'count',
        'db_types': ['mysql', 'pgsql', 'gbase', 'tdsql', 'oracle'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '当前正在执行的慢查询数',
    },
    'buffer_pool_hit_pct': {
        'display_name': '缓冲池命中率',
        'unit': 'pct',
        'db_types': ['mysql', 'oracle'],
        'alert_direction': 'down',
        'sigma_k': 2.0,
        'fixed_warn_val': 90,
        'is_capacity': False,
        'description': '数据缓冲池缓存命中率',
    },
    'lock_wait_count': {
        'display_name': '锁等待次数',
        'unit': 'count',
        'db_types': ['oracle', 'mysql', 'pgsql', 'dm'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '当前锁等待的会话数',
    },

    # === Oracle 专用 ===
    'sga_target_mb': {
        'display_name': 'SGA目标大小',
        'unit': 'mb',
        'db_types': ['oracle'],
        'alert_direction': 'both',
        'is_capacity': True,
        'description': 'Oracle SGA 目标内存大小',
    },
    'pga_target_mb': {
        'display_name': 'PGA目标大小',
        'unit': 'mb',
        'db_types': ['oracle'],
        'alert_direction': 'both',
        'is_capacity': True,
        'description': 'Oracle PGA 目标内存大小',
    },
    'redo_switches_per_hour': {
        'display_name': '日志切换频率',
        'unit': 'count',
        'db_types': ['oracle'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '每小时 Redo 日志切换次数',
    },
    'enqueue_waits': {
        'display_name': '队列锁等待',
        'unit': 'count',
        'db_types': ['oracle'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': 'Oracle 队列锁等待次数',
    },
    'library_cache_hit_pct': {
        'display_name': '库缓存命中率',
        'unit': 'pct',
        'db_types': ['oracle'],
        'alert_direction': 'down',
        'sigma_k': 2.0,
        'fixed_warn_val': 95,
        'is_capacity': False,
        'description': 'Oracle 库缓存命中率',
    },
    'dict_cache_hit_pct': {
        'display_name': '字典缓存命中率',
        'unit': 'pct',
        'db_types': ['oracle'],
        'alert_direction': 'down',
        'sigma_k': 2.0,
        'fixed_warn_val': 90,
        'is_capacity': False,
        'description': 'Oracle 数据字典缓存命中率',
    },

    # === PostgreSQL 专用 ===
    'blks_read_per_sec': {
        'display_name': '磁盘读取块数/秒',
        'unit': 'count',
        'db_types': ['pgsql'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '每秒从磁盘读取的数据块数',
    },
    'blks_hit_per_sec': {
        'display_name': '缓存命中块数/秒',
        'unit': 'count',
        'db_types': ['pgsql'],
        'alert_direction': 'both',
        'is_capacity': False,
        'description': '每秒从共享缓冲区命中的数据块数',
    },
    'deadlock_count': {
        'display_name': '死锁次数',
        'unit': 'count',
        'db_types': ['pgsql'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '检测到的死锁数量',
    },
    'replication_lag_seconds': {
        'display_name': '复制延迟(秒)',
        'unit': 'sec',
        'db_types': ['pgsql', 'mysql'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'fixed_warn_val': 30,
        'is_capacity': False,
        'description': '从库复制延迟秒数',
    },
    'wal_pending_bytes': {
        'display_name': 'WAL待写入大小',
        'unit': 'mb',
        'db_types': ['pgsql'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': 'PostgreSQL WAL 待写入字节数',
    },

    # === MySQL/TDSQL/GBase 专用 ===
    'innodb_buffer_pool_hit_pct': {
        'display_name': 'InnoDB缓冲池命中率',
        'unit': 'pct',
        'db_types': ['mysql', 'gbase', 'tdsql'],
        'alert_direction': 'down',
        'sigma_k': 2.0,
        'fixed_warn_val': 95,
        'is_capacity': False,
        'description': 'InnoDB 缓冲池缓存命中率',
    },
    'thread_running': {
        'display_name': '运行中线程数',
        'unit': 'count',
        'db_types': ['mysql', 'gbase', 'tdsql'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '当前正在运行的线程数',
    },
    'bytes_received_per_sec': {
        'display_name': '入站流量/秒',
        'unit': 'mb',
        'db_types': ['mysql', 'gbase', 'tdsql'],
        'alert_direction': 'both',
        'is_capacity': False,
        'description': '每秒接收的数据量',
    },
    'bytes_sent_per_sec': {
        'display_name': '出站流量/秒',
        'unit': 'mb',
        'db_types': ['mysql', 'gbase', 'tdsql'],
        'alert_direction': 'both',
        'is_capacity': False,
        'description': '每秒发送的数据量',
    },

    # === 达梦专用 ===
    'dm_sorts_per_sec': {
        'display_name': '排序次数/秒',
        'unit': 'count',
        'db_types': ['dm'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '达梦数据库每秒排序操作数',
    },
    'dm_sessions': {
        'display_name': '会话数',
        'unit': 'count',
        'db_types': ['dm'],
        'alert_direction': 'up',
        'sigma_k': 2.0,
        'is_capacity': False,
        'description': '达梦当前会话数',
    },

    # === 集群指标 ===
    'dw_replication_health': {
        'display_name': 'ADG复制健康度',
        'unit': 'count',
        'db_types': ['oracle'],
        'alert_direction': 'down',
        'is_capacity': False,
        'description': 'Oracle Active Data Guard 复制健康状态',
    },
    'dsc_cluster_health': {
        'display_name': 'DSC集群健康度',
        'unit': 'count',
        'db_types': ['dm'],
        'alert_direction': 'down',
        'is_capacity': False,
        'description': '达梦 DSC 集群健康状态',
    },
    'gbase_cluster_health': {
        'display_name': 'GBase集群健康度',
        'unit': 'count',
        'db_types': ['gbase'],
        'alert_direction': 'down',
        'is_capacity': False,
        'description': 'GBase 集群健康状态',
    },
    'tdsql_cluster_health': {
        'display_name': 'TDSQL集群健康度',
        'unit': 'count',
        'db_types': ['tdsql'],
        'alert_direction': 'down',
        'is_capacity': False,
        'description': 'TDSQL 集群健康状态',
    },
}


class Command(BaseCommand):
    help = '初始化指标定义元数据（MetricDefinition）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='强制覆盖已存在的定义（默认跳过）',
        )

    def handle(self, *args, **options):
        force = options['force']
        created_count = 0
        updated_count = 0
        skipped_count = 0

        for metric_key, defn in METRIC_DEFINITIONS.items():
            try:
                obj, created = MetricDefinition.objects.update_or_create(
                    metric_key=metric_key,
                    defaults={
                        'display_name': defn.get('display_name', metric_key),
                        'unit': defn.get('unit', ''),
                        'db_types': defn.get('db_types', []),
                        'alert_direction': defn.get('alert_direction', 'both'),
                        'sigma_k': defn.get('sigma_k', 2.0),
                        'fixed_warn_val': defn.get('fixed_warn_val'),
                        'is_capacity': defn.get('is_capacity', False),
                        'description': defn.get('description', ''),
                    }
                )

                if created:
                    created_count += 1
                else:
                    if force:
                        updated_count += 1
                    else:
                        skipped_count += 1

            except Exception as e:
                self.stderr.write(f"  [ERROR] {metric_key}: {e}")

        self.stdout.write(self.style.SUCCESS(
            f"指标定义初始化完成: 新建={created_count} 更新={updated_count} 跳过={skipped_count} "
            f"总计={len(METRIC_DEFINITIONS)}"
        ))
