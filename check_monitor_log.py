#!/usr/bin/env python
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from monitor.models import MonitorLog, DatabaseConfig

# Get most recent log for each database
logs = MonitorLog.objects.order_by('-create_time')
dbs_seen = set()
for log in logs:
    if log.config_id not in dbs_seen:
        dbs_seen.add(log.config_id)
        print(f'\n=== {log.config.name} ({log.config.db_type}) ===')
        print(f'Status: {log.status}')
        print(f'Time: {log.create_time}')
        print(f'Raw Message: {repr(log.message[:500])}')
