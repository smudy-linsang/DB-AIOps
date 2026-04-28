#!/usr/bin/env python
import oracledb

print('Testing Oracle connection with oracledb...')
# Try with oracle123 password
try:
    dsn = oracledb.makedsn('localhost', 1521, service_name='XE')
    conn = oracledb.connect(user='system', password='oracle123', dsn=dsn)
    print('Oracle (system/oracle123): SUCCESS')
    cursor = conn.cursor()
    cursor.execute('SELECT VERSION FROM V$INSTANCE')
    result = cursor.fetchone()
    print(f'  Version: {result[0]}')
    cursor.close()
    conn.close()
except Exception as e:
    print(f'Oracle (system/oracle123): FAILED - {e}')

# Try with sys user
try:
    dsn = oracledb.makedsn('localhost', 1521, service_name='XE')
    conn = oracledb.connect(user='sys', password='oracle123', dsn=dsn, mode=oracledb.SYSDBA)
    print('Oracle (sys/oracle123): SUCCESS')
    cursor = conn.cursor()
    cursor.execute('SELECT VERSION FROM V$INSTANCE')
    result = cursor.fetchone()
    print(f'  Version: {result[0]}')
    cursor.close()
    conn.close()
except Exception as e:
    print(f'Oracle (sys/oracle123): FAILED - {e}')
