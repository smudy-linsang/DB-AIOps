# -*- coding: utf-8 -*-
"""
管理命令：告警阈值模板批量初始化（多模板支持 v4.0）
v4.0 - 创建 AlertTemplate 模板组，并在其中创建 AlertThresholdTemplate 规则

运行方式：
    python manage.py init_alert_templates
    python manage.py init_alert_templates --dry-run    # 仅预览，不写入
    python manage.py init_alert_templates --reset       # 删除旧模板后重新创建
"""

from django.core.management.base import BaseCommand
from monitor.models import AlertTemplate, AlertThresholdTemplate

# ====================================================================
# 默认告警阈值模板配置
# 基于业界最佳实践和 DBA 经验值
# ====================================================================

DEFAULT_TEMPLATES = {
    'oracle': [
        {
            'metric_key': 'conn_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70.0,
            'error_threshold': 85.0,
            'critical_threshold': 95.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'Oracle 会话连接使用率',
        },
        {
            'metric_key': 'active_connections',
            'rule_type': 'static_threshold',
            'warn_threshold': 200.0,
            'error_threshold': 400.0,
            'critical_threshold': 600.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'Oracle 活跃会话数',
        },
        {
            'metric_key': 'buffer_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95.0,
            'error_threshold': 90.0,
            'critical_threshold': 85.0,
            'direction': 'down',
            'persistence_count': 5,
            'description': 'Oracle Buffer Cache 命中率',
        },
        {
            'metric_key': 'library_cache_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95.0,
            'error_threshold': 90.0,
            'critical_threshold': 85.0,
            'direction': 'down',
            'persistence_count': 5,
            'description': 'Oracle Library Cache 命中率',
        },
        {
            'metric_key': 'tablespace_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 80.0,
            'error_threshold': 90.0,
            'critical_threshold': 95.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'Oracle 表空间使用率（最大值）',
        },
        {
            'metric_key': 'lock_wait_count',
            'rule_type': 'static_threshold',
            'warn_threshold': 5.0,
            'error_threshold': 20.0,
            'critical_threshold': 50.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'Oracle 锁等待数量',
        },
        {
            'metric_key': 'datafile_size_total_gb',
            'rule_type': 'static_threshold',
            'warn_threshold': 500.0,
            'error_threshold': 800.0,
            'critical_threshold': 1000.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'Oracle 数据文件总大小(GB)',
        },
    ],

    'mysql': [
        {
            'metric_key': 'conn_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70.0,
            'error_threshold': 85.0,
            'critical_threshold': 95.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'MySQL 连接使用率',
        },
        {
            'metric_key': 'threads_running',
            'rule_type': 'static_threshold',
            'warn_threshold': 50.0,
            'error_threshold': 100.0,
            'critical_threshold': 200.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'MySQL 活跃线程数',
        },
        {
            'metric_key': 'aborted_connects',
            'rule_type': 'static_threshold',
            'warn_threshold': 10.0,
            'error_threshold': 50.0,
            'critical_threshold': 100.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'MySQL 异常断开连接数',
        },
        {
            'metric_key': 'innodb_buffer_pool_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95.0,
            'error_threshold': 90.0,
            'critical_threshold': 85.0,
            'direction': 'down',
            'persistence_count': 5,
            'description': 'InnoDB Buffer Pool 命中率',
        },
        {
            'metric_key': 'innodb_deadlocks',
            'rule_type': 'static_threshold',
            'warn_threshold': 1.0,
            'error_threshold': 5.0,
            'critical_threshold': 10.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'InnoDB 死锁次数',
        },
        {
            'metric_key': 'seconds_behind_master',
            'rule_type': 'static_threshold',
            'warn_threshold': 10.0,
            'error_threshold': 30.0,
            'critical_threshold': 60.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'MySQL 主从复制延迟(秒)',
        },
        {
            'metric_key': 'table_open_cache_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95.0,
            'error_threshold': 90.0,
            'critical_threshold': 80.0,
            'direction': 'down',
            'persistence_count': 5,
            'description': 'MySQL 表缓存命中率',
        },
        {
            'metric_key': 'thread_cache_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 90.0,
            'error_threshold': 80.0,
            'critical_threshold': 70.0,
            'direction': 'down',
            'persistence_count': 5,
            'description': 'MySQL 线程缓存命中率',
        },
        {
            'metric_key': 'slow_queries',
            'rule_type': 'static_threshold',
            'warn_threshold': 10.0,
            'error_threshold': 50.0,
            'critical_threshold': 100.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'MySQL 慢查询数量',
        },
        {
            'metric_key': 'innodb_log_waits_ps',
            'rule_type': 'static_threshold',
            'warn_threshold': 10.0,
            'error_threshold': 50.0,
            'critical_threshold': 100.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'InnoDB 日志等待/s',
        },
    ],

    'pgsql': [
        {
            'metric_key': 'conn_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70.0,
            'error_threshold': 85.0,
            'critical_threshold': 95.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'PostgreSQL 连接使用率',
        },
        {
            'metric_key': 'idle_in_transaction',
            'rule_type': 'static_threshold',
            'warn_threshold': 5.0,
            'error_threshold': 20.0,
            'critical_threshold': 50.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'PostgreSQL 事务中空闲会话数',
        },
        {
            'metric_key': 'waiting_connections',
            'rule_type': 'static_threshold',
            'warn_threshold': 3.0,
            'error_threshold': 10.0,
            'critical_threshold': 30.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'PostgreSQL 等待连接数',
        },
        {
            'metric_key': 'cache_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95.0,
            'error_threshold': 90.0,
            'critical_threshold': 85.0,
            'direction': 'down',
            'persistence_count': 5,
            'description': 'PostgreSQL 缓存命中率',
        },
        {
            'metric_key': 'deadlocks',
            'rule_type': 'static_threshold',
            'warn_threshold': 1.0,
            'error_threshold': 3.0,
            'critical_threshold': 5.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'PostgreSQL 死锁次数',
        },
        {
            'metric_key': 'replication_lag_bytes',
            'rule_type': 'static_threshold',
            'warn_threshold': 1048576.0,
            'error_threshold': 10485760.0,
            'critical_threshold': 104857600.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'PostgreSQL 流复制延迟(字节)',
        },
        {
            'metric_key': 'wal_replay_lag_ms',
            'rule_type': 'static_threshold',
            'warn_threshold': 1000.0,
            'error_threshold': 5000.0,
            'critical_threshold': 10000.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'PostgreSQL WAL 回放延迟(ms)',
        },
        {
            'metric_key': 'transaction_id_age',
            'rule_type': 'static_threshold',
            'warn_threshold': 100000000.0,
            'error_threshold': 500000000.0,
            'critical_threshold': 1000000000.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'PostgreSQL 事务ID年龄（防回卷）',
        },
        {
            'metric_key': 'temp_files',
            'rule_type': 'static_threshold',
            'warn_threshold': 100.0,
            'error_threshold': 500.0,
            'critical_threshold': 1000.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'PostgreSQL 临时文件数',
        },
    ],

    'dm': [
        {
            'metric_key': 'conn_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70.0,
            'error_threshold': 85.0,
            'critical_threshold': 95.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': '达梦 会话连接使用率',
        },
        {
            'metric_key': 'active_sessions',
            'rule_type': 'static_threshold',
            'warn_threshold': 100.0,
            'error_threshold': 200.0,
            'critical_threshold': 400.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': '达梦 活跃会话数',
        },
        {
            'metric_key': 'session_wait_count',
            'rule_type': 'static_threshold',
            'warn_threshold': 10.0,
            'error_threshold': 50.0,
            'critical_threshold': 100.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': '达梦 等待会话数',
        },
        {
            'metric_key': 'buffer_hit_ratio',
            'rule_type': 'static_threshold',
            'warn_threshold': 95.0,
            'error_threshold': 90.0,
            'critical_threshold': 85.0,
            'direction': 'down',
            'persistence_count': 5,
            'description': '达梦 缓冲池命中率',
        },
        {
            'metric_key': 'deadlock_count',
            'rule_type': 'static_threshold',
            'warn_threshold': 1.0,
            'error_threshold': 5.0,
            'critical_threshold': 10.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': '达梦 死锁次数',
        },
        {
            'metric_key': 'apply_delay_total',
            'rule_type': 'static_threshold',
            'warn_threshold': 1000.0,
            'error_threshold': 5000.0,
            'critical_threshold': 10000.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': '达梦 DW 备库应用延迟(ms)',
        },
        {
            'metric_key': 'dest_pending',
            'rule_type': 'static_threshold',
            'warn_threshold': 1000.0,
            'error_threshold': 10000.0,
            'critical_threshold': 100000.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': '达梦 DW 待发送日志数',
        },
        {
            'metric_key': 'failed_logins',
            'rule_type': 'static_threshold',
            'warn_threshold': 10.0,
            'error_threshold': 50.0,
            'critical_threshold': 100.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': '达梦 失败登录次数',
        },
    ],

    'gbase': [
        {
            'metric_key': 'conn_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70.0,
            'error_threshold': 85.0,
            'critical_threshold': 95.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'GBase 连接使用率',
        },
        {
            'metric_key': 'threads_running',
            'rule_type': 'static_threshold',
            'warn_threshold': 50.0,
            'error_threshold': 100.0,
            'critical_threshold': 200.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'GBase 活跃线程数',
        },
    ],

    'tdsql': [
        {
            'metric_key': 'conn_usage_pct',
            'rule_type': 'static_threshold',
            'warn_threshold': 70.0,
            'error_threshold': 85.0,
            'critical_threshold': 95.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'TDSQL 连接使用率',
        },
        {
            'metric_key': 'threads_running',
            'rule_type': 'static_threshold',
            'warn_threshold': 50.0,
            'error_threshold': 100.0,
            'critical_threshold': 200.0,
            'direction': 'up',
            'persistence_count': 3,
            'description': 'TDSQL 活跃线程数',
        },
    ],
}


DB_TYPE_LABELS = {
    'oracle': 'Oracle',
    'mysql': 'MySQL',
    'pgsql': 'PostgreSQL',
    'dm': '达梦',
    'gbase': 'GBase 8a',
    'tdsql': 'TDSQL',
}


class Command(BaseCommand):
    help = '初始化/更新告警阈值模板组（多模板模式 v4.0 - 幂等可重复执行）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='仅预览将要创建/更新的模板组和规则，不实际写入数据库',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='删除所有现有模板组和规则后重新创建',
        )
        parser.add_argument(
            '--db-type',
            type=str,
            default=None,
            help='仅初始化指定数据库类型的模板 (oracle/mysql/pgsql/dm/gbase/tdsql)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        reset = options['reset']
        db_type_filter = options['db_type']

        if dry_run:
            self.stdout.write(self.style.WARNING('=== DRY RUN 模式：不会实际写入数据库 ===\n'))

        if reset and not dry_run:
            # 先删除规则再删除模板组（避免级联外键问题）
            rule_count = AlertThresholdTemplate.objects.all().count()
            tg_count = AlertTemplate.objects.all().count()
            AlertThresholdTemplate.objects.all().delete()
            AlertTemplate.objects.all().delete()
            self.stdout.write(self.style.WARNING(
                f'已清空所有模板：{tg_count} 个模板组 + {rule_count} 条规则'
            ))

        created_tg = 0
        updated_tg = 0
        created_rules = 0
        updated_rules = 0

        db_types_to_init = DEFAULT_TEMPLATES.keys() if db_type_filter is None else [db_type_filter]

        for db_type in db_types_to_init:
            if db_type not in DEFAULT_TEMPLATES:
                self.stdout.write(self.style.ERROR(
                    f'不支持的数据库类型: {db_type}。'
                    f'支持的类型: {", ".join(DEFAULT_TEMPLATES.keys())}'
                ))
                continue

            rule_configs = DEFAULT_TEMPLATES[db_type]
            label = DB_TYPE_LABELS.get(db_type, db_type)
            self.stdout.write(f'\n处理 [{db_type}] {label} 模板...')

            # 创建或获取默认模板组
            tg_name = f'{label}-默认模板'
            if dry_run:
                existing_tg = AlertTemplate.objects.filter(
                    name=tg_name, db_type=db_type
                ).first()
                if existing_tg:
                    self.stdout.write(f'  [UPDATE] 模板组: {tg_name}')
                    updated_tg += 1
                    tg = existing_tg
                else:
                    self.stdout.write(f'  [CREATE] 模板组: {tg_name}')
                    created_tg += 1
                    tg = None  # dry-run 不创建
            else:
                tg, is_new = AlertTemplate.objects.update_or_create(
                    name=tg_name,
                    db_type=db_type,
                    defaults={
                        'is_default': True,
                        'description': f'{label}数据库默认告警阈值模板组（{len(rule_configs)} 个指标）',
                    }
                )
                action = 'CREATE' if is_new else 'UPDATE'
                self.stdout.write(f'  [{action}] 模板组: {tg_name}')
                if is_new:
                    created_tg += 1
                else:
                    updated_tg += 1

            # 为每个指标创建/更新规则
            for rule_cfg in rule_configs:
                metric_key = rule_cfg['metric_key']
                defaults = {
                    'rule_type': rule_cfg['rule_type'],
                    'warn_threshold': rule_cfg.get('warn_threshold'),
                    'error_threshold': rule_cfg.get('error_threshold'),
                    'critical_threshold': rule_cfg.get('critical_threshold'),
                    'direction': rule_cfg.get('direction', 'both'),
                    'is_enabled': True,
                    'description': rule_cfg.get('description', ''),
                    'db_type': db_type,
                    'display_name': rule_cfg.get('display_name', metric_key),
                }

                if dry_run:
                    if tg:
                        existing_rule = AlertThresholdTemplate.objects.filter(
                            template=tg, metric_key=metric_key
                        ).first()
                    else:
                        existing_rule = None

                    if existing_rule:
                        self.stdout.write(f'    [UPDATE] {metric_key} ({rule_cfg.get("description", "")})')
                        updated_rules += 1
                    else:
                        self.stdout.write(f'    [CREATE] {metric_key} ({rule_cfg.get("description", "")})')
                        created_rules += 1
                else:
                    rule_obj, is_new = AlertThresholdTemplate.objects.update_or_create(
                        template=tg,
                        metric_key=metric_key,
                        defaults=defaults,
                    )
                    action = 'CREATE' if is_new else 'UPDATE'
                    self.stdout.write(f'    [{action}] {metric_key} ({rule_cfg.get("description", "")})')
                    if is_new:
                        created_rules += 1
                    else:
                        updated_rules += 1

        # 汇总
        self.stdout.write('\n' + '=' * 60)
        total_tg = created_tg + updated_tg
        total_rules = created_rules + updated_rules
        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'[DRY RUN] 预览完成: '
                f'模板组 新建 {created_tg} / 更新 {updated_tg} (合计 {total_tg}), '
                f'规则 新建 {created_rules} / 更新 {updated_rules} (合计 {total_rules})'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'初始化完成: '
                f'模板组 新建 {created_tg} / 更新 {updated_tg} (合计 {total_tg}), '
                f'规则 新建 {created_rules} / 更新 {updated_rules} (合计 {total_rules})'
            ))

        # 按类型统计（非 dry-run）
        if not dry_run and total_tg > 0:
            self.stdout.write('\n按数据库类型统计:')
            for db_type in db_types_to_init:
                tg_count = AlertTemplate.objects.filter(db_type=db_type).count()
                rule_count = AlertThresholdTemplate.objects.filter(template__db_type=db_type).count()
                label = DB_TYPE_LABELS.get(db_type, db_type)
                self.stdout.write(f'  {label}: {tg_count} 个模板组, {rule_count} 条规则')
