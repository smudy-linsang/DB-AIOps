#!/usr/bin/env python3
"""Test metrics API - raw output"""
import os, sys, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dbmonitor.settings')
import django; django.setup()
from django.conf import settings
settings.ALLOWED_HOSTS.append('testserver')
from django.test import Client
from monitor.auth import TokenManager

token = TokenManager.generate_token(1)
print("TOKEN:", token)

c = Client(SERVER_NAME='testserver')

# Test tablespace - raw
resp = c.get('/api/v1/databases/3/metrics/', {
    'metric': 'tablespace_SYSTEM_used_pct',
    'time': '1h',
    '_t': '1'
}, HTTP_AUTHORIZATION=f'Bearer {token}')
print("TABLESPACE RAW RESPONSE:")
print(json.dumps(json.loads(resp.content), ensure_ascii=False, indent=2)[:2000])

print("\n\n")
# Test wait_event - raw
resp2 = c.get('/api/v1/databases/3/metrics/', {
    'metric': 'wait_event_db file sequential read',
    'time': '24h',
    '_t': '1'
}, HTTP_AUTHORIZATION=f'Bearer {token}')
print("WAIT_EVENT RAW RESPONSE:")
print(json.dumps(json.loads(resp2.content), ensure_ascii=False, indent=2)[:2000])

# Check config IDs
print("\n\n--- Checking DatabaseConfig ---")
from monitor.models import DatabaseConfig
for cfg in DatabaseConfig.objects.all():
    print(f"  ID={cfg.id}: name={cfg.name}, db_type={cfg.db_type}")
