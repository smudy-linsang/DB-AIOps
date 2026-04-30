#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
初始化测试数据库配置
将测试数据库添加到监控列表
"""
import os
import sys
import django

# 设置 Django 环境
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
django.setup()

from monitor.models import DatabaseConfig
from monitor.crypto import encrypt_password

def init_test_databases():
    """初始化测试数据库配置"""
    
    test_databases = [
        {
            'name': '测试库_PostgreSQL',
            'db_type': 'pgsql',
            'host': 'localhost',
            'port': 5433,
            'username': 'postgres',
            'password': 'postgres123',
            'service_name': 'postgres',
            'environment': 'test',
        },
        {
            'name': '测试库_MySQL',
            'db_type': 'mysql',
            'host': 'localhost',
            'port': 3306,
            'username': 'root',
            'password': 'root123',
            'service_name': 'testdb',
            'environment': 'test',
        },
        {
            'name': '测试库_Oracle',
            'db_type': 'oracle',
            'host': 'localhost',
            'port': 1521,
            'username': 'system',
            'password': 'oracle',
            'service_name': 'orcl',
            'environment': 'test',
        },
    ]
    
    created_count = 0
    existing_count = 0
    
    for db in test_databases:
        # 检查是否已存在
        exists = DatabaseConfig.objects.filter(
            name=db['name'],
            db_type=db['db_type'],
            host=db['host']
        ).exists()
        
        if exists:
            print(f"  [跳过] {db['name']} (已存在)")
            existing_count += 1
            continue
        
        # 加密密码
        encrypted_password = encrypt_password(db['password'])
        
        # 创建配置
        config = DatabaseConfig.objects.create(
            name=db['name'],
            db_type=db['db_type'],
            host=db['host'],
            port=db['port'],
            username=db['username'],
            password=encrypted_password,
            service_name=db.get('service_name'),
            is_active=True,
        )
        print(f"  [创建] {db['name']} - {db['db_type']}@{db['host']}:{db['port']}")
        created_count += 1
    
    print(f"\n完成: 新建 {created_count} 个, 跳过 {existing_count} 个已存在")
    
    # 显示当前所有数据库配置
    print("\n当前监控的数据库列表:")
    print("-" * 80)
    for config in DatabaseConfig.objects.filter(is_active=True):
        print(f"  [{config.db_type}] {config.name} - {config.host}:{config.port}")
    print("-" * 80)

if __name__ == '__main__':
    print("初始化测试数据库配置...")
    init_test_databases()
