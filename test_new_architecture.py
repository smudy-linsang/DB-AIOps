#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""新架构验证测试"""
import os
import sys
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')

import django
django.setup()

from django.conf import settings
from django.contrib.auth.models import User
from monitor.models import DatabaseConfig, AlertLog, UserProfile

print('=' * 60)
print('DB Monitor 新架构验证测试')
print('=' * 60)

# 1. PostgreSQL 配置数据验证
print('\n[1/4] PostgreSQL 配置数据验证')
print(f'  数据库引擎: {settings.DATABASES["default"]["ENGINE"]}')
print(f'  数据库名: {settings.DATABASES["default"]["NAME"]}')
print(f'  数据库主机: {settings.DATABASES["default"]["HOST"]}')
print(f'  用户数: {User.objects.count()}')
print(f'  数据库配置: {DatabaseConfig.objects.count()}')
print(f'  告警日志: {AlertLog.objects.count()}')
print(f'  用户配置: {UserProfile.objects.count()}')

for config in DatabaseConfig.objects.all():
    print(f'    [{config.id}] {config.name} ({config.db_type}) - {config.host}:{config.port}')

# 2. Elasticsearch 连接验证
print('\n[2/4] Elasticsearch 连接验证')
from monitor.elasticsearch_engine import check_es_health, init_indices, get_total_docs, get_db_count
health = check_es_health()
print(f'  ES 状态: {health["status"]}')
print(f'  ES 版本: {health.get("version", "unknown")}')
print(f'  集群名: {health.get("cluster_name", "unknown")}')
print(f'  节点数: {health.get("number_of_nodes", 0)}')

# 3. ES 索引初始化
print('\n[3/4] ES 索引初始化')
init_result = init_indices()
print(f'  初始化结果: {init_result}')
print(f'  指标文档总数: {get_total_docs()}')
print(f'  数据库数量: {get_db_count()}')

# 4. ES 写入/查询测试
print('\n[4/4] ES 写入/查询测试')
from monitor.elasticsearch_engine import index_metrics, query_latest_metrics, get_es_client

# 写入测试数据
ok = index_metrics(
    config_id=1, db_type='mysql', db_name='MySQL_测试库',
    host='127.0.0.1', port=3306, environment='dev',
    status='UP',
    metrics={
        'cpu_usage': 45.2, 'memory_usage': 68.5,
        'threads_connected': 120, 'max_connections': 500,
        'qps': 1500.5, 'tps': 300.2,
        'innodb_buffer_pool_hit_ratio': 99.5,
        'slow_queries': 3
    }
)
print(f'  写入结果: {ok}')

time.sleep(2)

# 强制刷新
client = get_es_client()
client.indices.refresh(index='db_metrics_2026_04')

# 查询测试
data = query_latest_metrics(1)
if data:
    print(f'  查询结果: config_id={data["config_id"]}, status={data["status"]}')
    metrics = data.get('metrics', {})
    print(f'    cpu_usage: {metrics.get("cpu_usage")}')
    print(f'    threads_connected: {metrics.get("threads_connected")}')
    print(f'    qps: {metrics.get("qps")}')
    print(f'    innodb_buffer_pool_hit_ratio: {metrics.get("innodb_buffer_pool_hit_ratio")}')
else:
    print('  查询结果: 无数据')

print('\n' + '=' * 60)
print('架构验证完成！')
print('=' * 60)
print('\n新架构总结:')
print('  配置数据 -> PostgreSQL (Docker TimescaleDB 容器, db_monitor 库)')
print('  指标数据 -> Elasticsearch (Docker ES 容器, db_metrics_* 索引)')
print('  告警数据 -> PostgreSQL + ES 双写')
print('  API 查询 -> 优先 ES，回退 PostgreSQL')
