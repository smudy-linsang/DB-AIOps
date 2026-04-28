#!/usr/bin/env python
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from monitor.models import DatabaseConfig

print('Database Configurations:')
for db in DatabaseConfig.objects.all():
    print(f'\n{db.name} ({db.db_type}):')
    print(f'  Host: {db.host}:{db.port}')
    print(f'  Username: {db.username}')
    print(f'  Password: {db.password[:50]}...' if len(db.password) > 50 else f'  Password: {db.password}')
    print(f'  Service Name: {db.service_name}')
    # Try to decrypt
    try:
        decrypted = db.get_password()
        print(f'  Decrypted Password: {decrypted}')
    except Exception as e:
        print(f'  Decryption Error: {e}')
