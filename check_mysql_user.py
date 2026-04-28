#!/usr/bin/env python
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='monitor', password='monitor123', database='testdb')
cursor = conn.cursor()
# Check what user we are
cursor.execute('SELECT CURRENT_USER()')
print('Current user:', cursor.fetchone())
# Check privileges
cursor.execute('SHOW GRANTS FOR CURRENT_USER()')
for row in cursor.fetchall():
    print('Grants:', row)
conn.close()
