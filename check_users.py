#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查用户"""
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from monitor.models import UserProfile

# List all users
all_users = UserProfile.objects.all()
print(f'Total users: {all_users.count()}')
for u in all_users:
    print(f'  - {u.username} | {u.email} | is_admin: {u.is_admin}')
