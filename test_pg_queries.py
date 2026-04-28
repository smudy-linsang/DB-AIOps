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
    
    queries = [
        "SELECT version()",
        "SHOW server_version_num",
        "SHOW data_directory",
        "SHOW port",
        "SELECT extract(epoch from (now() - pg_postmaster_start_time()))",
        "SELECT current_database()",
        "SELECT inet_server_addr()",
        "SELECT inet_server_port()",
    ]
    
    for query in queries:
        try:
            cursor.execute(query)
            result = cursor.fetchone()
            print('Query OK: %s -> %s' % (query[:50], str(result)[:50]))
        except Exception as e:
            print('Query FAILED: %s -> %s' % (query[:50], str(e)))
    
    conn.close()
except Exception as e:
    print('PostgreSQL: Connection failed')
    print('Error:', e)
