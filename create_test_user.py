#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""创建测试用户"""
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from django.contrib.auth.models import User
from monitor.models import UserProfile

# Create admin user
try:
    # Check if user exists
    if User.objects.filter(username='admin').exists():
        print('Admin user already exists')
    else:
        # Create Django auth user
        user = User.objects.create_user(
            username='admin',
            email='admin@example.com',
            password='admin123',
            is_staff=True,
            is_superuser=True
        )
        # Create profile
        profile = UserProfile.objects.create(
            user=user,
            role='admin',
            allowed_databases=None  # Can access all databases
        )
        print('Created admin user: admin / admin123')
except Exception as e:
    print(f'Error creating admin: {e}')

# Create a normal operator user
try:
    if User.objects.filter(username='operator').exists():
        print('Operator user already exists')
    else:
        user = User.objects.create_user(
            username='operator',
            email='operator@example.com',
            password='operator123',
            is_staff=False,
            is_superuser=False
        )
        profile = UserProfile.objects.create(
            user=user,
            role='user',
            allowed_databases=None  # Can access all databases
        )
        print('Created operator user: operator / operator123')
except Exception as e:
    print(f'Error creating operator: {e}')

# List all users
print('\nAll users:')
for u in User.objects.all():
    profile = getattr(u, 'profile', None)
    print(f'  - {u.username} | {u.email} | role: {profile.role if profile else "N/A"}')
