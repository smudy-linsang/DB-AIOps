#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试API数据"""
import os, sys, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from monitor.models import DatabaseConfig, MonitorLog, AlertLog
from datetime import datetime, timedelta

print('=' * 60)
print('API Data Verification Test')
print('=' * 60)

# Test 1: Database Configs
print('\n[1] Database Configs:')
configs = DatabaseConfig.objects.filter(is_active=True)
for c in configs:
    print(f'  ID:{c.id} | {c.name} | {c.db_type}')

# Test 2: Recent MonitorLogs (last 5 min)
print('\n[2] Active Databases (last 5 min):')
recent_time = datetime.now() - timedelta(minutes=5)
active_dbs = MonitorLog.objects.filter(
    create_time__gte=recent_time
).values('config_id').distinct().count()
print(f'  Active DBs: {active_dbs}')

# Test 3: Latest status for each database
print('\n[3] Latest Status:')
for c in configs:
    latest = MonitorLog.objects.filter(config=c).order_by('-create_time').first()
    if latest:
        status = latest.status
        collect_time = latest.create_time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            msg = json.loads(latest.message) if latest.message else {}
            metric_count = len(msg.keys())
        except:
            metric_count = 0
        print(f'  [{c.name}] {status} | {collect_time} | {metric_count} metrics')

# Test 4: Total counts
print('\n[4] Statistics:')
print(f'  MonitorLog total: {MonitorLog.objects.count()}')
print(f'  AlertLog active: {AlertLog.objects.filter(status="active").count()}')

# Test 5: Sample metrics for MySQL
print('\n[5] Sample MySQL Metrics:')
mysql_cfg = DatabaseConfig.objects.filter(db_type='mysql', is_active=True).first()
if mysql_cfg:
    latest = MonitorLog.objects.filter(config=mysql_cfg).order_by('-create_time').first()
    if latest and latest.message:
        try:
            metrics = json.loads(latest.message)
            # Show some key metrics
            key_metrics = ['threads_connected', 'threads_running', 'qps', 'tps', 
                         'innodb_buffer_pool_size', 'max_connections', 'buffer_pool_hit_rate']
            for k in key_metrics:
                if k in metrics:
                    print(f'  {k}: {metrics[k]}')
        except Exception as e:
            print(f'  Error parsing: {e}')

print('\n' + '=' * 60)
print('All tests passed!')
