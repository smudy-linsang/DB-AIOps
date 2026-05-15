# -*- coding: utf-8 -*-
"""
数据库密码轮换命令

检查即将过期的监控账号密码，生成告警提醒，支持自动轮换（生成随机密码 + ALTER USER）。

用法:
    python manage.py rotate_db_passwords --check    # 仅检查，不执行轮换
    python manage.py rotate_db_passwords --execute  # 执行轮换
"""

import secrets
import string
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from monitor.models import DatabaseConfig, AlertLog


class Command(BaseCommand):
    help = '数据库监控账号密码轮换'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help='仅检查过期状态，不执行轮换')
        parser.add_argument('--execute', action='store_true', help='执行密码轮换')
        parser.add_argument('--db-id', type=int, help='指定轮换的数据库ID（不指定则检查所有）')

    def handle(self, *args, **options):
        check_only = options['check']
        execute = options['execute']
        db_id = options.get('db_id')

        if not check_only and not execute:
            check_only = True  # 默认只检查

        configs = DatabaseConfig.objects.filter(is_active=True)
        if db_id:
            configs = configs.filter(id=db_id)

        now = timezone.now()
        expired_count = 0
        rotated_count = 0

        for config in configs:
            # 检查密码过期时间
            changed_at = config.password_changed_at
            expiry_days = config.password_expiry_days

            if changed_at is None:
                # 无记录，使用创建时间或标记为需要轮换
                days_since = 999
            else:
                days_since = (now - changed_at).days

            days_remaining = expiry_days - days_since

            if days_remaining > 14:
                continue  # 还有2周以上，跳过

            expired_count += 1

            if days_remaining <= 0:
                status_msg = '已过期'
            elif days_remaining <= 1:
                status_msg = '明天过期'
            elif days_remaining <= 7:
                status_msg = f'{days_remaining}天后过期'
            else:
                status_msg = f'{days_remaining}天后过期'

            self.stdout.write(f"  [{config.name}] 密码{status_msg} (过期天数={expiry_days})")

            # 生成告警
            if days_remaining <= 7:
                severity = 'critical' if days_remaining <= 1 else 'warning'
                existing = AlertLog.objects.filter(
                    config=config, alert_type='password_expiry', status='active'
                ).exists()
                if not existing:
                    AlertLog.objects.create(
                        config=config,
                        alert_type='password_expiry',
                        metric_key='password_expiry',
                        severity=severity,
                        title=f'[密码过期] {config.name} 监控账号密码{status_msg}',
                        description=f'数据库 {config.name} 的监控账号密码将在{status_msg}。请及时轮换密码。',
                        status='active',
                    )

            # 执行轮换
            if execute and days_remaining <= 0:
                success = self._rotate_password(config)
                if success:
                    rotated_count += 1
                    self.stdout.write(self.style.SUCCESS(f"  [OK] {config.name} 密码已轮换"))
                else:
                    self.stdout.write(self.style.ERROR(f"  [FAIL] {config.name} 密码轮换失败"))

        if check_only:
            self.stdout.write(f"\n检查完成: {expired_count} 个数据库密码即将/已过期")
        else:
            self.stdout.write(f"\n轮换完成: {rotated_count}/{expired_count} 个成功")

    def _rotate_password(self, config):
        """执行密码轮换"""
        try:
            from monitor.db_connector import DbConnector
            new_password = self._generate_password()
            conn = DbConnector.get_connection(config)
            try:
                cursor = conn.cursor()
                # 根据数据库类型生成 ALTER USER 语句
                if config.db_type == 'mysql':
                    sql = f"ALTER USER '{config.username}'@'%' IDENTIFIED BY '{new_password}'"
                elif config.db_type == 'pgsql':
                    sql = f"ALTER USER {config.username} WITH PASSWORD '{new_password}'"
                elif config.db_type == 'oracle':
                    sql = f"ALTER USER {config.username} IDENTIFIED BY \"{new_password}\""
                else:
                    self.stdout.write(f"  不支持 {config.db_type} 类型自动轮换")
                    return False

                cursor.execute(sql)
                conn.commit()
                cursor.close()

                # 更新配置中的密码
                config.set_password(new_password)
                config.password_changed_at = timezone.now()
                config.save(update_fields=['password', 'password_changed_at'])

                return True
            finally:
                DbConnector.close_connection(conn)
        except Exception as e:
            self.stdout.write(f"  轮换异常: {e}")
            return False

    @staticmethod
    def _generate_password(length=20):
        """生成随机密码"""
        alphabet = string.ascii_letters + string.digits + '!@#$%^&*'
        return ''.join(secrets.choice(alphabet) for _ in range(length))
