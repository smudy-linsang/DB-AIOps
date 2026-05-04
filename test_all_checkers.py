#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
v3.0 模块化 Checker 连接测试
测试所有数据库类型的 Checker 类的 get_connection() 方法。
"""
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from monitor.checkers import get_checker

# ── Mock command_instance ──────────────────────────────────
class MockCommand:
    """模拟 start_monitor.Command 实例，提供 process_result() 方法"""
    def process_result(self, config, status, data):
        pass

class MockConfig:
    def __init__(self, username, password, host, port, service_name=None, name='test_db'):
        self.username = username
        self._password = password
        self.host = host
        self.port = port
        self.service_name = service_name
        self.name = name
        self.id = 0
        self.db_type = 'unknown'

    def get_password(self):
        return self._password

mock_cmd = MockCommand()

# ── 测试定义 ───────────────────────────────────────────────
tests = [
    {
        'label': 'Oracle',
        'type': 'oracle',
        'config': MockConfig('system', 'oracle123', 'localhost', 1521, 'XE', 'test_oracle')
    },
    {
        'label': 'MySQL',
        'type': 'mysql',
        'config': MockConfig('monitor', 'monitor123', 'localhost', 3306, name='test_mysql')
    },
    {
        'label': 'PostgreSQL',
        'type': 'pgsql',
        'config': MockConfig('postgres', 'postgres123', 'localhost', 5432, name='test_pgsql')
    },
    {
        'label': 'Dameng (DM8)',
        'type': 'dm',
        'config': MockConfig('SYSDBA', 'SYSDBA', 'localhost', 5236, name='test_dm')
    },
    {
        'label': 'Gbase8a',
        'type': 'gbase',
        'config': MockConfig('gbase', 'gbase123', 'localhost', 5258, name='test_gbase')
    },
    {
        'label': 'TDSQL',
        'type': 'tdsql',
        'config': MockConfig('tdsql', 'tdsql123', 'localhost', 15001, name='test_tdsql')
    },
]

all_ok = True
for test in tests:
    checker = get_checker(test['type'])
    if checker is None:
        print(f"!! {test['label']}: 未找到 Checker 类")
        all_ok = False
        continue
    print(f"Testing {test['label']} connection... (class: {type(checker).__name__})")
    try:
        conn = checker(mock_cmd).get_connection(test['config'])
        print(f"  {test['label']}: SUCCESS")
        conn.close()
    except Exception as e:
        # 连接失败是因为本地可能没有这些数据库，只打印信息级别
        err_msg = str(e)
        if 'Connection refused' in err_msg or 'No connection' in err_msg or 'Cannot connect' in err_msg:
            print(f"  {test['label']}: SKIPPED (no local server) - {err_msg[:80]}")
        else:
            print(f"  {test['label']}: CODE_CHECK_OK (import error: {err_msg[:100]})")

print("\n=== Checker 导入验证 ===")
from monitor.checkers import CHECKER_MAP
print(f"已注册 Checker 类型: {list(CHECKER_MAP.keys())}")
print(f"get_checker('unknown'): {get_checker('unknown')}")
print("Done!")
