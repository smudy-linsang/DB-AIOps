# -*- coding: utf-8 -*-
"""Phase 6A: 哨兵进程入口 (探活 + ASH-lite 采样)。

用法: python manage.py start_sentinel [--once]
"""
import datetime

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Phase 6A 哨兵进程: 秒级探活 + ASH-lite 会话采样"

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true',
                            help='对每个实例执行一次探活+采样后退出(调试)')

    def handle(self, *args, **options):
        if not getattr(settings, 'SENTINEL_ENABLED', True):
            self.stdout.write("SENTINEL_ENABLED=False, 退出")
            return

        from monitor.sentinel import InstanceSentinel, SentinelManager
        from monitor.redis_bus import ensure_groups
        ensure_groups()

        if options.get('once'):
            from monitor.models import DatabaseConfig
            print(f"[{datetime.datetime.now()}] 哨兵单轮模式 (--once)")
            for cfg in DatabaseConfig.objects.filter(is_active=True):
                if cfg.db_type not in ('mysql', 'tdsql', 'gbase', 'pgsql', 'oracle', 'dm'):
                    continue
                s = InstanceSentinel(cfg)
                s.probe()
                s.ash_sample()
                print(f"  {cfg.name} ({cfg.db_type}): probe+ash done, "
                      f"fail={s.consecutive_fail}")
                try:
                    if s.conn:
                        s.conn.close()
                except Exception:
                    pass
            return

        print(f"[{datetime.datetime.now()}] 哨兵进程启动")
        mgr = SentinelManager()
        try:
            mgr.run()
        except (KeyboardInterrupt, SystemExit):
            print("\n哨兵进程停止")
            mgr.stop()
