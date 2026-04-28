#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试登录API"""
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

import json
from monitor.auth import login_user

# Test login with admin
print("Testing admin login...")
result = login_user('admin', 'admin123')
if result:
    print(f"  SUCCESS: Token received")
    print(f"  User: {result['user']}")
else:
    print("  FAILED: Invalid credentials")

print()

# Test login with operator
print("Testing operator login...")
result = login_user('operator', 'operator123')
if result:
    print(f"  SUCCESS: Token received")
    print(f"  User: {result['user']}")
else:
    print("  FAILED: Invalid credentials")
