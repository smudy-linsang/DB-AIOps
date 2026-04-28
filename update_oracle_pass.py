#!/usr/bin/env python
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from monitor.models import DatabaseConfig

# Update Oracle password
oracle_db = DatabaseConfig.objects.filter(db_type='oracle').first()
if oracle_db:
    print(f'Found Oracle DB: {oracle_db.name}')
    print(f'Current password stored: {oracle_db.password[:30]}...')
    # Use set_password to properly encrypt
    oracle_db.set_password('oracle123')
    oracle_db.save()
    print(f'Updated Oracle password (encrypted): {oracle_db.password[:30]}...')
else:
    print('No Oracle database found')
