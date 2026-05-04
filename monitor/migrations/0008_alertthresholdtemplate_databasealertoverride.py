from django.db import migrations, models
import django.db.models.deletion


DEFAULT_TEMPLATES = [
    # Oracle
    ('oracle', 'tablespace_usage_pct', '表空间使用率', 'threshold', 'up', 80.0, 85.0, 90.0, None, None, None, '%'),
    ('oracle', 'conn_usage_pct', '连接数使用率', 'threshold', 'up', 75.0, 85.0, 95.0, None, None, None, '%'),
    ('oracle', 'active_sessions', '活动会话数', 'baseline_amplitude', 'both', None, None, None, 10.0, 20.0, 50.0, 'count'),
    ('oracle', 'lock_count', '锁等待数量', 'threshold', 'up', 5.0, 10.0, 20.0, None, None, None, 'count'),
    ('oracle', 'buffer_cache_hit_ratio', '缓冲区命中率', 'threshold', 'down', 95.0, 90.0, 80.0, None, None, None, '%'),
    # MySQL
    ('mysql', 'conn_usage_pct', '连接数使用率', 'threshold', 'up', 75.0, 85.0, 95.0, None, None, None, '%'),
    ('mysql', 'active_connections', '活动连接数', 'baseline_amplitude', 'both', None, None, None, 10.0, 20.0, 50.0, 'count'),
    ('mysql', 'slow_queries', '慢查询数', 'baseline_amplitude', 'up', None, None, None, 10.0, 30.0, 100.0, 'count'),
    ('mysql', 'innodb_buffer_hit_ratio', 'InnoDB缓冲池命中率', 'threshold', 'down', 95.0, 90.0, 80.0, None, None, None, '%'),
    # PostgreSQL
    ('pgsql', 'conn_usage_pct', '连接数使用率', 'threshold', 'up', 75.0, 85.0, 95.0, None, None, None, '%'),
    ('pgsql', 'active_connections', '活动连接数', 'baseline_amplitude', 'both', None, None, None, 10.0, 20.0, 50.0, 'count'),
    ('pgsql', 'deadlock_count', '死锁数量', 'baseline_amplitude', 'up', None, None, None, 10.0, 50.0, 200.0, 'count'),
    # 达梦
    ('dm', 'tablespace_usage_pct', '表空间使用率', 'threshold', 'up', 80.0, 85.0, 90.0, None, None, None, '%'),
    ('dm', 'conn_usage_pct', '连接数使用率', 'threshold', 'up', 75.0, 85.0, 95.0, None, None, None, '%'),
    ('dm', 'active_sessions', '活动会话数', 'baseline_amplitude', 'both', None, None, None, 10.0, 20.0, 50.0, 'count'),
    # Gbase
    ('gbase', 'conn_usage_pct', '连接数使用率', 'threshold', 'up', 75.0, 85.0, 95.0, None, None, None, '%'),
    ('gbase', 'active_connections', '活动连接数', 'baseline_amplitude', 'both', None, None, None, 10.0, 20.0, 50.0, 'count'),
    # TDSQL
    ('tdsql', 'conn_usage_pct', '连接数使用率', 'threshold', 'up', 75.0, 85.0, 95.0, None, None, None, '%'),
    ('tdsql', 'active_connections', '活动连接数', 'baseline_amplitude', 'both', None, None, None, 10.0, 20.0, 50.0, 'count'),
    # MongoDB
    ('mongo', 'active_connections', '活动连接数', 'baseline_amplitude', 'both', None, None, None, 10.0, 20.0, 50.0, 'count'),
    ('mongo', 'op_latency_ms', '操作延迟', 'baseline_amplitude', 'up', None, None, None, 20.0, 50.0, 100.0, 'ms'),
    # Redis
    ('redis', 'memory_usage_pct', '内存使用率', 'threshold', 'up', 70.0, 80.0, 90.0, None, None, None, '%'),
    ('redis', 'connected_clients', '连接客户端数', 'baseline_amplitude', 'both', None, None, None, 10.0, 20.0, 50.0, 'count'),
]


def seed_templates(apps, schema_editor):
    AlertThresholdTemplate = apps.get_model('monitor', 'AlertThresholdTemplate')
    objs = []
    for row in DEFAULT_TEMPLATES:
        (db_type, metric_key, display_name, rule_type, direction,
         warn_threshold, error_threshold, critical_threshold,
         warn_amp, error_amp, critical_amp, unit) = row
        objs.append(AlertThresholdTemplate(
            db_type=db_type,
            metric_key=metric_key,
            display_name=display_name,
            rule_type=rule_type,
            direction=direction,
            warn_threshold=warn_threshold,
            error_threshold=error_threshold,
            critical_threshold=critical_threshold,
            warn_amplitude_pct=warn_amp,
            error_amplitude_pct=error_amp,
            critical_amplitude_pct=critical_amp,
            unit=unit,
            is_enabled=True,
        ))
    AlertThresholdTemplate.objects.bulk_create(objs)


def unseed_templates(apps, schema_editor):
    apps.get_model('monitor', 'AlertThresholdTemplate').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0007_metricdefinition_alertnotificationlog_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='AlertThresholdTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('db_type', models.CharField(choices=[('oracle', 'Oracle'), ('mysql', 'MySQL'), ('redis', 'Redis'), ('pgsql', 'PostgreSQL'), ('mongo', 'MongoDB'), ('dm', '达梦数据库'), ('gbase', 'Gbase 8a'), ('tdsql', 'TDSQL')], max_length=20, verbose_name='数据库类型')),
                ('metric_key', models.CharField(max_length=100, verbose_name='指标键')),
                ('display_name', models.CharField(max_length=100, verbose_name='指标显示名')),
                ('rule_type', models.CharField(choices=[('threshold', '固定阈值'), ('baseline_amplitude', '基线振幅')], default='threshold', max_length=30, verbose_name='规则类型')),
                ('direction', models.CharField(choices=[('up', '上升触发'), ('down', '下降触发'), ('both', '双向触发')], default='up', max_length=10, verbose_name='触发方向')),
                ('warn_threshold', models.FloatField(blank=True, null=True, verbose_name='一级告警阈值(warning)')),
                ('error_threshold', models.FloatField(blank=True, null=True, verbose_name='二级告警阈值(error)')),
                ('critical_threshold', models.FloatField(blank=True, null=True, verbose_name='三级告警阈值(critical)')),
                ('warn_amplitude_pct', models.FloatField(blank=True, null=True, verbose_name='一级振幅阈值(%) warning')),
                ('error_amplitude_pct', models.FloatField(blank=True, null=True, verbose_name='二级振幅阈值(%) error')),
                ('critical_amplitude_pct', models.FloatField(blank=True, null=True, verbose_name='三级振幅阈值(%) critical')),
                ('unit', models.CharField(blank=True, max_length=20, null=True, verbose_name='单位')),
                ('is_enabled', models.BooleanField(default=True, verbose_name='是否启用')),
                ('description', models.TextField(blank=True, null=True, verbose_name='描述')),
                ('create_time', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('update_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '告警阈值模板',
                'verbose_name_plural': '告警阈值模板列表',
                'ordering': ['db_type', 'metric_key'],
            },
        ),
        migrations.AddConstraint(
            model_name='alertthresholdtemplate',
            constraint=models.UniqueConstraint(fields=('db_type', 'metric_key'), name='unique_template_db_metric'),
        ),
        migrations.CreateModel(
            name='DatabaseAlertOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('db_config', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alert_overrides', to='monitor.databaseconfig', verbose_name='数据库')),
                ('metric_key', models.CharField(max_length=100, verbose_name='指标键')),
                ('rule_type', models.CharField(blank=True, choices=[('threshold', '固定阈值'), ('baseline_amplitude', '基线振幅')], max_length=30, null=True, verbose_name='规则类型覆盖')),
                ('direction', models.CharField(blank=True, choices=[('up', '上升触发'), ('down', '下降触发'), ('both', '双向触发')], max_length=10, null=True, verbose_name='触发方向覆盖')),
                ('warn_threshold', models.FloatField(blank=True, null=True, verbose_name='一级告警阈值覆盖')),
                ('error_threshold', models.FloatField(blank=True, null=True, verbose_name='二级告警阈值覆盖')),
                ('critical_threshold', models.FloatField(blank=True, null=True, verbose_name='三级告警阈值覆盖')),
                ('warn_amplitude_pct', models.FloatField(blank=True, null=True, verbose_name='一级振幅阈值覆盖(%)')),
                ('error_amplitude_pct', models.FloatField(blank=True, null=True, verbose_name='二级振幅阈值覆盖(%)')),
                ('critical_amplitude_pct', models.FloatField(blank=True, null=True, verbose_name='三级振幅阈值覆盖(%)')),
                ('is_enabled', models.BooleanField(default=True, verbose_name='是否启用')),
                ('note', models.TextField(blank=True, null=True, verbose_name='备注')),
                ('create_time', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('update_time', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '数据库告警配置覆盖',
                'verbose_name_plural': '数据库告警配置覆盖列表',
                'ordering': ['db_config', 'metric_key'],
            },
        ),
        migrations.AddConstraint(
            model_name='databasealertoverride',
            constraint=models.UniqueConstraint(fields=('db_config', 'metric_key'), name='unique_override_db_metric'),
        ),
        migrations.RunPython(seed_templates, unseed_templates),
    ]
