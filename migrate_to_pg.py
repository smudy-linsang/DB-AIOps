#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据迁移脚本：从 SQLite 迁移配置数据到 PostgreSQL
只迁移配置类数据，不迁移监控日志（监控日志存 ES）
"""
import os
import sys
import json
import sqlite3
from datetime import datetime

# 设置 Django 环境
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')

import django
django.setup()

from django.contrib.auth.models import User, Group
from django.contrib.contenttypes.models import ContentType
from monitor.models import (
    DatabaseConfig, AlertLog, AuditLog, UserProfile,
    BusinessSystem, MetricDefinition, BaselineModel,
    PredictionResult, HealthScore, AlertSilenceWindow,
    AlertNotificationLog, ApprovalStep, ApprovalRecord,
    PlatformMetric
)

def migrate_data():
    """从 SQLite 迁移数据到 PostgreSQL"""
    
    # 连接 SQLite
    sqlite_conn = sqlite3.connect('db.sqlite3')
    sqlite_conn.row_factory = sqlite3.Row
    cursor = sqlite_conn.cursor()
    
    print("=" * 60)
    print("开始数据迁移: SQLite -> PostgreSQL")
    print("=" * 60)
    
    # 1. 迁移用户
    print("\n[1/6] 迁移用户...")
    cursor.execute("SELECT id, username, first_name, last_name, email, is_staff, is_active, is_superuser, date_joined, password FROM auth_user")
    users = cursor.fetchall()
    user_count = 0
    for row in users:
        user, created = User.objects.get_or_create(
            username=row['username'],
            defaults={
                'first_name': row['first_name'] or '',
                'last_name': row['last_name'] or '',
                'email': row['email'] or '',
                'is_staff': bool(row['is_staff']),
                'is_active': bool(row['is_active']),
                'is_superuser': bool(row['is_superuser']),
                'date_joined': row['date_joined'],
                'password': row['password'],  # 保持加密密码
            }
        )
        if created:
            user_count += 1
    print(f"  迁移用户: {user_count} 个")
    
    # 2. 迁移数据库配置
    print("\n[2/6] 迁移数据库配置...")
    cursor.execute("""
        SELECT id, name, db_type, host, port, username, password,
               service_name, is_active, create_time
        FROM monitor_databaseconfig
    """)
    configs = cursor.fetchall()
    config_count = 0
    config_id_map = {}  # SQLite ID -> PostgreSQL ID
    
    for row in configs:
        config, created = DatabaseConfig.objects.get_or_create(
            name=row['name'],
            host=row['host'],
            port=row['port'],
            defaults={
                'db_type': row['db_type'],
                'username': row['username'],
                'password': row['password'],  # 保持加密密码
                'service_name': row['service_name'] or '',
                'is_active': bool(row['is_active']),
            }
        )
        if created:
            config_count += 1
        config_id_map[row['id']] = config.id
    print(f"  迁移数据库配置: {config_count} 个")
    
    # 3. 迁移告警日志（最近 30 天）
    print("\n[3/6] 迁移告警日志（最近 30 天）...")
    cursor.execute("""
        SELECT id, config_id, alert_type, metric_key, severity, title, description,
               status, last_notified_at, resolved_at, create_time
        FROM monitor_alertlog
        WHERE create_time >= datetime('now', '-30 days')
        ORDER BY create_time DESC
        LIMIT 1000
    """)
    alerts = cursor.fetchall()
    alert_count = 0
    
    for row in alerts:
        new_config_id = config_id_map.get(row['config_id'])
        if not new_config_id:
            continue
        
        try:
            alert, created = AlertLog.objects.get_or_create(
                config_id=new_config_id,
                title=row['title'],
                create_time=row['create_time'],
                defaults={
                    'alert_type': row['alert_type'] or '',
                    'severity': row['severity'] or 'warning',
                    'status': row['status'] or 'active',
                    'description': row['description'] or '',
                    'metric_key': row['metric_key'] or '',
                    'last_notified_at': row['last_notified_at'] or row['create_time'],
                    'resolved_at': row['resolved_at'],
                }
            )
            if created:
                alert_count += 1
        except Exception as e:
            pass  # 跳过重复或错误数据
    print(f"  迁移告警日志: {alert_count} 条")
    
    # 4. 迁移审计日志（最近 30 天）
    print("\n[4/6] 迁移审计日志（最近 30 天）...")
    cursor.execute("""
        SELECT id, config_id, action_type, description, sql_command, risk_level,
               status, approver, approve_time, executor, execute_time,
               execution_result, create_time
        FROM monitor_auditlog
        WHERE create_time >= datetime('now', '-30 days')
        ORDER BY create_time DESC
        LIMIT 500
    """)
    audits = cursor.fetchall()
    audit_count = 0
    
    for row in audits:
        new_config_id = config_id_map.get(row['config_id'])
        if not new_config_id:
            continue
        
        try:
            audit, created = AuditLog.objects.get_or_create(
                config_id=new_config_id,
                sql_command=row['sql_command'][:500] if row['sql_command'] else '',
                create_time=row['create_time'],
                defaults={
                    'action_type': row['action_type'] or 'query',
                    'risk_level': row['risk_level'] or 'low',
                    'status': row['status'] or 'pending',
                    'description': row['description'] or '',
                    'approver': row['approver'] or '',
                    'approve_time': row['approve_time'],
                    'executor': row['executor'] or '',
                    'execute_time': row['execute_time'],
                    'execution_result': row['execution_result'] or '',
                }
            )
            if created:
                audit_count += 1
        except Exception as e:
            pass
    print(f"  迁移审计日志: {audit_count} 条")
    
    # 5. 迁移用户配置
    print("\n[5/6] 迁移用户配置...")
    cursor.execute("""
        SELECT id, user_id, role, allowed_databases
        FROM monitor_userprofile
    """)
    profiles = cursor.fetchall()
    profile_count = 0
    
    for row in profiles:
        try:
            user = User.objects.get(id=row['user_id'])
            profile, created = UserProfile.objects.get_or_create(
                user=user,
                defaults={
                    'role': row['role'] or 'viewer',
                    'allowed_databases': row['allowed_databases'] or '',
                }
            )
            if created:
                profile_count += 1
        except Exception as e:
            pass
    print(f"  迁移用户配置: {profile_count} 个")
    
    # 6. 迁移其他配置表
    print("\n[6/6] 迁移其他配置...")
    
    # 告警静默窗口
    try:
        cursor.execute("SELECT COUNT(*) FROM monitor_alertsilencewindow")
        silence_count = cursor.fetchone()[0]
        print(f"  告警静默窗口: {silence_count} 条（跳过，需手动配置）")
    except:
        pass
    
    # 审批步骤
    try:
        cursor.execute("SELECT COUNT(*) FROM monitor_approvalstep")
        step_count = cursor.fetchone()[0]
        print(f"  审批步骤: {step_count} 条（跳过，需手动配置）")
    except:
        pass
    
    sqlite_conn.close()
    
    print("\n" + "=" * 60)
    print("数据迁移完成！")
    print("=" * 60)
    
    # 验证
    print("\n=== PostgreSQL 数据验证 ===")
    print(f"  用户数: {User.objects.count()}")
    print(f"  数据库配置: {DatabaseConfig.objects.count()}")
    print(f"  告警日志: {AlertLog.objects.count()}")
    print(f"  审计日志: {AuditLog.objects.count()}")
    print(f"  用户配置: {UserProfile.objects.count()}")
    
    # 显示数据库配置
    print("\n=== 数据库配置列表 ===")
    for config in DatabaseConfig.objects.all():
        print(f"  [{config.id}] {config.name} ({config.db_type}) - {config.host}:{config.port}")


if __name__ == '__main__':
    migrate_data()
