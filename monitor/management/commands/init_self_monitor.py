# -*- coding: utf-8 -*-
"""W4 自监控初始化：创建承载自监控告警的"系统"伪实例。

自监控告警需要 AlertLog.config（必填外键）。这里创建一个 is_active=False
的伪实例 __system__，不参与任何采集/哨兵/列表展示。

用法: python manage.py init_self_monitor
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "初始化自监控所需的 __system__ 伪实例（幂等）"

    def handle(self, *args, **options):
        from monitor.models import DatabaseConfig
        from monitor.self_monitor import SYSTEM_CONFIG_NAME
        cfg, created = DatabaseConfig.objects.get_or_create(
            name=SYSTEM_CONFIG_NAME,
            defaults={'db_type': 'mysql', 'host': 'localhost', 'port': 0,
                      'username': '-', 'password': '', 'is_active': False},
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"已创建伪实例 {SYSTEM_CONFIG_NAME} (id={cfg.id})"))
        else:
            self.stdout.write(f"伪实例 {SYSTEM_CONFIG_NAME} 已存在 (id={cfg.id})")
