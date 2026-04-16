import pyodbc
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

conn_str = 'DRIVER={DM8 ODBC DRIVER};SERVER=localhost:5236;UID=SYSDBA;PWD=Abcd@1234;'
conn = pyodbc.connect(conn_str, timeout=5)
cur = conn.cursor()

# 查找 PAGE_SIZE
print('=== V$LOCK columns ===')
try:
    cur.execute('SELECT TOP 1 * FROM V$LOCK')
    print([d[0] for d in cur.description])
except Exception as e:
    print(f'Error: {e}')

print()
print('=== V$SESSIONS columns ===')
try:
    cur.execute('SELECT TOP 1 * FROM V$SESSIONS')
    print([d[0] for d in cur.description])
except Exception as e:
    print(f'Error: {e}')

cur.close()
conn.close()