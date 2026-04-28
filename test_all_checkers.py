#!/usr/bin/env python
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()

from monitor.management.commands.start_monitor import OracleChecker, MySQLChecker, PostgreSQLChecker

class MockConfig:
    def __init__(self, username, password, host, port, service_name=None):
        self.username = username
        self._password = password
        self.host = host
        self.port = port
        self.service_name = service_name
    
    def get_password(self):
        return self._password

# Test Oracle
print("Testing Oracle connection...")
oracle_checker = OracleChecker()
oracle_config = MockConfig('system', 'oracle123', 'localhost', 1521, 'XE')
try:
    conn = oracle_checker.get_connection(oracle_config)
    print("  Oracle: SUCCESS")
    conn.close()
except Exception as e:
    print(f"  Oracle: FAILED - {e}")

# Test MySQL
print("Testing MySQL connection...")
mysql_checker = MySQLChecker()
mysql_config = MockConfig('monitor', 'monitor123', 'localhost', 3306)
try:
    conn = mysql_checker.get_connection(mysql_config)
    print("  MySQL: SUCCESS")
    conn.close()
except Exception as e:
    print(f"  MySQL: FAILED - {e}")

# Test PostgreSQL
print("Testing PostgreSQL connection...")
pg_checker = PostgreSQLChecker()
pg_config = MockConfig('postgres', 'postgres123', 'localhost', 5432)
try:
    conn = pg_checker.get_connection(pg_config)
    print("  PostgreSQL: SUCCESS")
    conn.close()
except Exception as e:
    print(f"  PostgreSQL: FAILED - {e}")

print("\nDone!")
