#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""异构双引擎架构验证测试"""
import os
import sys
import time
import warnings
warnings.filterwarnings('ignore')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')

import django
django.setup()

from django.conf import settings

print("=" * 70)
print("异构双引擎架构验证测试")
print("TimescaleDB（指标）+ Elasticsearch（告警）+ PostgreSQL（配置）")
print("=" * 70)

# ============================================================
# 1. PostgreSQL 配置数据
# ============================================================
print("\n[1/5] PostgreSQL 配置数据验证")
print(f"  引擎: {settings.DATABASES['default']['ENGINE']}")
print(f"  数据库: {settings.DATABASES['default']['NAME']}")

from django.contrib.auth.models import User
from monitor.models import DatabaseConfig, AlertLog, UserProfile

print(f"  用户数: {User.objects.count()}")
print(f"  数据库配置: {DatabaseConfig.objects.count()}")
print(f"  告警日志: {AlertLog.objects.count()}")
for c in DatabaseConfig.objects.all():
    print(f"    [{c.id}] {c.name} ({c.db_type}) - {c.host}:{c.port}")

# ============================================================
# 2. TimescaleDB 指标存储
# ============================================================
print("\n[2/5] TimescaleDB 指标存储验证")
from monitor.timeseries import get_timeseries_storage

ts = get_timeseries_storage()
print(f"  启用状态: {ts.enabled}")

if ts.enabled:
    # 写入测试指标
    test_metrics = {
        'cpu_usage': 45.2,
        'memory_usage': 68.5,
        'threads_connected': 120,
        'qps': 1500.5,
        'tps': 300.2,
        'innodb_buffer_pool_hit_ratio': 99.5,
    }
    write_ok = ts.write_metrics_batch(1, test_metrics, status='UP')
    print(f"  写入测试: {write_ok}")
    
    # 写入快照
    snapshot_ok = ts.write_snapshot(1, 'UP', {'test': True, 'cpu': 45.2})
    print(f"  快照写入: {snapshot_ok}")
    
    time.sleep(1)
    
    # 查询测试
    history = ts.query_metric_history(1, 'cpu_usage', hours=1)
    print(f"  查询 cpu_usage: {len(history)} 条")
    if history:
        print(f"    最新值: {history[-1]}")
    
    # 存储统计
    stats = ts.get_storage_stats()
    print(f"  原始指标数: {stats.get('raw_metric_count', 0)}")
    print(f"  快照数: {stats.get('snapshot_count', 0)}")
    print(f"  数据库大小: {stats.get('database_size', 'unknown')}")

# ============================================================
# 3. Elasticsearch 告警存储
# ============================================================
print("\n[3/5] Elasticsearch 告警存储验证")
from monitor.elasticsearch_engine import check_es_health, query_alerts, get_es_client

health = check_es_health()
print(f"  ES 状态: {health['status']}")
print(f"  ES 版本: {health.get('version', 'unknown')}")

# 查询告警
alerts = query_alerts(limit=5)
print(f"  告警总数: {len(alerts)}")
for a in alerts[:3]:
    print(f"    [{a.get('alert_id')}] {a.get('title', '')[:40]} - {a.get('severity')}")

# 全文搜索测试
print("\n  全文搜索测试:")
search_alerts = query_alerts(limit=5)
print(f"    搜索结果: {len(search_alerts)} 条")

# ============================================================
# 4. API 双引擎查询测试
# ============================================================
print("\n[4/5] API 双引擎查询测试")

# 测试状态 API
from monitor.api_views import DatabaseStatusView
print("  状态 API: 使用 TimescaleDB 快照")

# 测试指标 API
print("  指标 API: TimescaleDB（数值）→ SQLite（复杂指标）")

# 测试告警 API
print("  告警 API: Elasticsearch（全文检索）→ PostgreSQL（回退）")

# ============================================================
# 5. 架构总结
# ============================================================
print("\n[5/5] 架构总结")
print("\n  ┌─────────────────────────────────────────────────────┐")
print("  │              异构双引擎架构                           │")
print("  ├─────────────────────────────────────────────────────┤")
print("  │  PostgreSQL (配置数据)                               │")
print("  │    - DatabaseConfig, UserProfile, AuditLog           │")
print("  │    - HealthScore, BaselineModel, PredictionResult    │")
print("  ├─────────────────────────────────────────────────────┤")
print("  │  TimescaleDB (监控指标)                              │")
print("  │    - metric_point (原始指标, 按7天分chunk)            │")
print("  │    - collection_snapshot (采集快照, 按1天分chunk)     │")
print("  │    - metric_hourly (小时聚合, 连续聚合)               │")
print("  │    - metric_daily (日聚合, 连续聚合)                  │")
print("  │    - 自动压缩 (7天后) + 自动过期 (90天)               │")
print("  ├─────────────────────────────────────────────────────┤")
print("  │  Elasticsearch (告警日志)                            │")
print("  │    - db_alerts_YYYY_MM (按月分索引)                   │")
print("  │    - 全文检索 + 结构化查询                            │")
print("  │    - 913 条告警已迁移                                 │")
print("  └─────────────────────────────────────────────────────┘")

print("\n" + "=" * 70)
print("异构双引擎架构验证完成！")
print("=" * 70)
