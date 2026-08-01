# -*- coding: utf-8 -*-
"""
Phase 8A: 初始化 ES 案例向量索引 (db_cases)。

用法:
    python manage.py init_cases_index
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Phase 8A: 创建/校验 Elasticsearch 案例向量索引 db_cases'

    def handle(self, *args, **options):
        from monitor.elasticsearch_engine import init_cases_index
        ok = init_cases_index()
        if ok:
            self.stdout.write(self.style.SUCCESS('db_cases 索引就绪'))
        else:
            self.stdout.write(self.style.WARNING(
                'db_cases 索引创建失败 (ES 不可用或未启用), 案例检索将退回词法匹配'))
