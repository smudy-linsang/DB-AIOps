#!/usr/bin/env python
# -*- coding: utf-8 -*-
import psycopg2

try:
    conn = psycopg2.connect(
        host='localhost', 
        port=5432, 
        user='postgres', 
        password='postgres123', 
        database='postgres'
    )
    print('PostgreSQL: Connected successfully')
    cursor = conn.cursor()
    cursor.execute('SELECT version()')
    print('Version:', cursor.fetchone()[0])
    cursor.execute('SELECT 1')
    print('Query test: OK')
    conn.close()
except Exception as e:
    print('PostgreSQL: Connection failed')
    print('Error:', e)
