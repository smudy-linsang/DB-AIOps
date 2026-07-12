# -*- coding: utf-8 -*-
"""Phase 6A: pipeline_worker 进程入口。

用法: python manage.py start_pipeline [--once]
"""
import datetime

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Phase 6A pipeline_worker: 消费 events/diagnosis/verify 三流"

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true',
                            help='各流处理一批后退出(调试)')

    def handle(self, *args, **options):
        if not getattr(settings, 'PIPELINE_ENABLED', True):
            self.stdout.write("PIPELINE_ENABLED=False, 退出")
            return

        if options.get('once'):
            from monitor.pipeline import process_pending_once
            print(f"[{datetime.datetime.now()}] pipeline 单批模式 (--once)")
            counts = process_pending_once()
            print(f"  处理计数: {counts}")
            return

        print(f"[{datetime.datetime.now()}] pipeline_worker 启动")
        from monitor.pipeline import Pipeline
        p = Pipeline()
        try:
            p.run()
        except (KeyboardInterrupt, SystemExit):
            print("\npipeline_worker 停止")
            p.stop()
