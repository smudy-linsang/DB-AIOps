#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DB-AIOps 测试环境数据库连接验证
验证 Oracle, MySQL, PostgreSQL, Dameng 数据库连接
"""

import os
import sys

def test_postgresql():
    """测试 PostgreSQL 连接"""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host='localhost',
            port=5432,
            user='postgres',
            password='postgres123',
            database='postgres',
            connect_timeout=10
        )
        version = conn.server_version
        conn.close()
        return True, f"PostgreSQL {version}"
    except Exception as e:
        return False, str(e)

def test_mysql():
    """测试 MySQL 连接"""
    try:
        import pymysql
        conn = pymysql.connect(
            host='localhost',
            port=3306,
            user='root',
            password='root123',
            connect_timeout=10
        )
        version = conn.server_version
        conn.close()
        return True, f"MySQL {version}"
    except Exception as e:
        return False, str(e)

def test_oracle():
    """测试 Oracle 连接"""
    try:
        import oracledb
        conn = oracledb.connect(
            user='system',
            password='oracle123',
            host='localhost',
            port=1521,
            service_name='XEPDB1'
        )
        version = conn.version
        conn.close()
        return True, f"Oracle {version}"
    except Exception as e:
        return False, str(e)

def test_dameng():
    """测试达梦数据库连接"""
    try:
        import pyodbc
        conn_str = "DRIVER={DM8 ODBC DRIVER};SERVER=localhost:5236;UID=SYSDBA;PWD=Abcd@1234;"
        conn = pyodbc.connect(conn_str, timeout=10)
        conn.close()
        return True, "Dameng (ODBC)"
    except Exception as e:
        return False, str(e)

def main():
    print("=" * 60)
    print("DB-AIOps 测试环境数据库连接验证")
    print("=" * 60)
    
    databases = [
        ("PostgreSQL", test_postgresql),
        ("MySQL", test_mysql),
        ("Oracle", test_oracle),
        ("Dameng", test_dameng),
    ]
    
    results = {}
    for name, test_func in databases:
        print(f"\n[{name}] 正在测试...", end=" ")
        success, info = test_func()
        results[name] = (success, info)
        if success:
            print(f"✅ {info}")
        else:
            print(f"❌ {info}")
    
    print("\n" + "=" * 60)
    print("验证结果汇总")
    print("=" * 60)
    
    success_count = sum(1 for s, _ in results.values() if s)
    total_count = len(results)
    
    for name, (success, info) in results.items():
        status = "✅" if success else "❌"
        print(f"  {status} {name}: {info}")
    
    print(f"\n通过: {success_count}/{total_count}")
    
    if success_count == total_count:
        print("\n🎉 所有数据库连接验证通过！")
        return 0
    else:
        print(f"\n⚠️  {total_count - success_count} 个数据库连接失败")
        return 1

if __name__ == "__main__":
    sys.exit(main())