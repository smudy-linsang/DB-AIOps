# -*- coding: utf-8 -*-
"""
Phase 8A: 存量案例向量回填。

把 AlertCase 中 embedding_indexed=False 的案例批量向量化写入 ES db_cases。
用法:
    python manage.py backfill_case_vectors [--batch 50]
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Phase 8A: 存量案例批量向量化回填到 ES db_cases'

    def add_arguments(self, parser):
        parser.add_argument('--batch', type=int, default=50, help='每批处理条数')

    def handle(self, *args, **options):
        from monitor.case_rag_v2 import backfill_all_cases
        result = backfill_all_cases(batch=options['batch'])
        self.stdout.write(self.style.SUCCESS(f'回填完成: {result}'))
