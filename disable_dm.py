#!/usr/bin/env python
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from monitor.models import DatabaseConfig

# Disable DM database since there's no DM container running
dm_db = DatabaseConfig.objects.filter(db_type='dm').first()
if dm_db:
    print(f'DM: {dm_db.name}')
    print(f'  Current is_active: {dm_db.is_active}')
    dm_db.is_active = False
    dm_db.save()
    print(f'  Disabled DM database')
else:
    print('No DM database found')

print('\nDone!')
