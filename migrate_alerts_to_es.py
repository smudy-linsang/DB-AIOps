#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移历史告警到 Elasticsearch"""
import os
import sys
import warnings
warnings.filterwarnings('ignore')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')

import django
django.setup()

from monitor.models import AlertLog
from monitor.elasticsearch_engine import index_alert, init_indices, get_es_client
from django.utils import timezone

def migrate_alerts():
    print("=" * 60)
    print("迁移历史告警到 Elasticsearch")
    print("=" * 60)
    
    # 初始化 ES 索引
    print("\n[1/3] 初始化 ES 索引...")
    init_result = init_indices()
    print(f"  结果: {init_result}")
    
    # 获取所有告警
    print("\n[2/3] 迁移告警数据...")
    alerts = AlertLog.objects.select_related('config').all().order_by('-create_time')
    total = alerts.count()
    print(f"  总告警数: {total}")
    
    success = 0
    failed = 0
    
    for i, alert in enumerate(alerts):
        try:
            db_name = alert.config.name if alert.config else 'unknown'
            db_type = alert.config.db_type if alert.config else 'unknown'
            
            ok = index_alert(
                alert_id=alert.id,
                config_id=alert.config_id,
                db_name=db_name,
                db_type=db_type,
                alert_type=alert.alert_type,
                severity=alert.severity,
                status=alert.status,
                title=alert.title,
                description=alert.description,
                metric_key=alert.metric_key,
                fired_at=alert.create_time,
                resolved_at=alert.resolved_at
            )
            if ok:
                success += 1
            else:
                failed += 1
            
            if (i + 1) % 100 == 0:
                print(f"  进度: {i + 1}/{total}")
        except Exception as e:
            failed += 1
    
    print(f"\n  迁移结果: 成功 {success}, 失败 {failed}")
    
    # 验证
    print("\n[3/3] 验证 ES 数据...")
    client = get_es_client()
    if client:
        client.indices.refresh(index='db_alerts_*')
        count = client.count(index='db_alerts_*')['count']
        print(f"  ES 告警文档数: {count}")
    
    print("\n" + "=" * 60)
    print("迁移完成！")
    print("=" * 60)

if __name__ == '__main__':
    migrate_alerts()
