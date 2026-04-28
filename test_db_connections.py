#!/usr/bin/env python
import os
import sys

# Test MySQL connection
print("Testing MySQL connection...")
try:
    import pymysql
    conn = pymysql.connect(
        host='localhost',
        port=3306,
        user='monitor',
        password='monitor123',
        database='testdb',
        connect_timeout=5
    )
    print("  MySQL: SUCCESS")
    conn.close()
except Exception as e:
    print(f"  MySQL: FAILED - {e}")

# Test PostgreSQL connection
print("Testing PostgreSQL connection...")
try:
    import psycopg2
    conn = psycopg2.connect(
        host='localhost',
        port=5432,
        user='postgres',
        password='postgres123',
        database='postgres',
        connect_timeout=5
    )
    print("  PostgreSQL: SUCCESS")
    conn.close()
except Exception as e:
    print(f"  PostgreSQL: FAILED - {e}")

# Test Oracle connection
print("Testing Oracle connection...")
try:
    import cx_Oracle
    dsn = cx_Oracle.makedsn('localhost', 1521, service_name='XE')
    conn = cx_Oracle.connect(user='system', password='oracle', dsn=dsn)
    print("  Oracle: SUCCESS")
    conn.close()
except Exception as e:
    print(f"  Oracle: FAILED - {e}")

print("\nDone!")
