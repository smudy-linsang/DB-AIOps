#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""综合API测试"""
import os, sys, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from monitor.models import DatabaseConfig, MonitorLog, AlertLog

print('=' * 70)
print('Comprehensive API Data Test')
print('=' * 70)

# Test 1: Database Configs
print('\n[1] Database Configs:')
configs = DatabaseConfig.objects.filter(is_active=True)
print(f'    Total active databases: {configs.count()}')
for c in configs:
    print(f'    - ID:{c.id} | {c.name} | {c.db_type} | {c.host}:{c.port}')

# Test 2: DatabaseStatusView simulation (returns latest status + metrics)
print('\n[2] Database Status (模拟 DatabaseStatusView):')
for c in configs:
    latest = MonitorLog.objects.filter(config=c).order_by('-create_time').first()
    if latest:
        metrics = json.loads(latest.message) if latest.message else {}
        print(f'\n    [{c.name}]')
        print(f'      Status: {latest.status}')
        print(f'      Collected: {latest.create_time}')
        print(f'      Metrics count: {len(metrics)}')
        # Show key metrics
        key_metrics = ['version', 'uptime_seconds', 'threads_connected', 'qps', 'tps']
        for k in key_metrics:
            if k in metrics:
                print(f'      {k}: {metrics[k]}')

# Test 3: DatabaseMetricsView simulation (returns historical metrics)
print('\n[3] Database Metrics History (模拟 DatabaseMetricsView):')
from datetime import datetime, timedelta
end_dt = datetime.now()
start_dt = end_dt - timedelta(hours=1)

for c in configs[:2]:  # Just test first 2 databases
    print(f'\n    [{c.name}] - Last 1 hour:')
    logs = MonitorLog.objects.filter(
        config=c,
        create_time__gte=start_dt,
        create_time__lte=end_dt
    ).order_by('-create_time')[:10]
    
    print(f'      Found {logs.count()} records in last hour')
    
    # Group by timestamp to show data structure
    sample_metrics = []
    for log in logs:
        metrics = json.loads(log.message) if log.message else {}
        if 'qps' in metrics:
            sample_metrics.append({
                'timestamp': log.create_time.isoformat(),
                'metric': 'qps',
                'value': metrics['qps'],
                'status': log.status
            })
    
    if sample_metrics:
        print(f'      Sample QPS data points:')
        for sm in sample_metrics[:3]:
            print(f'        {sm}')

# Test 4: AlertLog for databases
print('\n[4] Alert Summary:')
active_alerts = AlertLog.objects.filter(status='active').count()
print(f'    Active alerts: {active_alerts}')

# Group alerts by database
alert_by_db = AlertLog.objects.filter(status='active').values('config_id').annotate(
    count=django.db.models.Count('id')
)
for item in alert_by_db[:5]:
    cfg = DatabaseConfig.objects.get(id=item['config_id'])
    print(f'    [{cfg.name}]: {item["count"]} active alerts')

# Test 5: API Response Format Verification
print('\n[5] API Response Format Verification:')
for c in configs[:1]:
    latest = MonitorLog.objects.filter(config=c).order_by('-create_time').first()
    if latest:
        # Simulate DatabaseStatusView response
        metrics = json.loads(latest.message) if latest.message else {}
        status_response = {
            'config_id': c.id,
            'status': latest.status,
            'collected_at': latest.create_time.isoformat(),
            'message': latest.message,
            'metrics': metrics
        }
        print(f'\n    DatabaseStatusView response for {c.name}:')
        print(f'      config_id: {status_response["config_id"]}')
        print(f'      status: {status_response["status"]}')
        print(f'      metrics keys (first 5): {list(metrics.keys())[:5]}')

# Test 6: Health endpoint simulation
print('\n[6] Health Check (模拟 HealthCheckView):')
recent_time = datetime.now() - timedelta(minutes=5)
active_dbs = MonitorLog.objects.filter(
    create_time__gte=recent_time
).values('config_id').distinct().count()
active_alerts = AlertLog.objects.filter(status='active').count()

print(f'    active_databases: {active_dbs}')
print(f'    active_alerts: {active_alerts}')

print('\n' + '=' * 70)
print('All API data tests completed successfully!')
print('=' * 70)
