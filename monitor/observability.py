"""
平台可观测性模块 v2.0

职责：
- Prometheus 格式指标暴露
- 平台自监控
- 健康检查端点
- 性能指标收集
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List

from django.http import HttpResponse
from django.utils import timezone

logger = logging.getLogger(__name__)


class PrometheusExporter:
    """Prometheus 格式指标导出器"""

    def __init__(self):
        self._metrics_cache = {}
        self._cache_time = None
        self._cache_ttl = 30  # 缓存30秒

    def export(self) -> str:
        """导出 Prometheus 格式的指标"""
        # 检查缓存
        if self._cache_time and (time.time() - self._cache_time) < self._cache_ttl:
            return self._metrics_cache.get('output', '')

        lines = []

        try:
            from monitor.models import (
                DatabaseConfig, MonitorLog, AlertLog,
                AuditLog, PlatformMetric
            )

            now = timezone.now()
            five_min_ago = now - timedelta(minutes=5)
            one_hour_ago = now - timedelta(hours=1)

            # ==========================================
            # 数据库配置指标
            # ==========================================
            total_dbs = DatabaseConfig.objects.count()
            active_dbs = DatabaseConfig.objects.filter(is_active=True).count()

            lines.append('# HELP dbmonitor_databases_total Total number of database configurations')
            lines.append('# TYPE dbmonitor_databases_total gauge')
            lines.append(f'dbmonitor_databases_total {total_dbs}')

            lines.append('# HELP dbmonitor_databases_active Number of active database configurations')
            lines.append('# TYPE dbmonitor_databases_active gauge')
            lines.append(f'dbmonitor_databases_active {active_dbs}')

            # 按类型统计
            from django.db.models import Count
            type_stats = DatabaseConfig.objects.filter(is_active=True).values('db_type').annotate(count=Count('id'))
            lines.append('# HELP dbmonitor_databases_by_type Number of databases by type')
            lines.append('# TYPE dbmonitor_databases_by_type gauge')
            for stat in type_stats:
                lines.append(f'dbmonitor_databases_by_type{{db_type="{stat["db_type"]}"}} {stat["count"]}')

            # ==========================================
            # 采集指标
            # ==========================================
            # 最近5分钟有采集的数据库数
            recent_collected = MonitorLog.objects.filter(
                create_time__gte=five_min_ago
            ).values('config_id').distinct().count()

            lines.append('# HELP dbmonitor_collected_databases_5min Databases collected in last 5 minutes')
            lines.append('# TYPE dbmonitor_collected_databases_5min gauge')
            lines.append(f'dbmonitor_collected_databases_5min {recent_collected}')

            # 采集成功率（最近1小时）
            total_logs_1h = MonitorLog.objects.filter(create_time__gte=one_hour_ago).count()
            up_logs_1h = MonitorLog.objects.filter(create_time__gte=one_hour_ago, status='UP').count()
            success_rate = (up_logs_1h / total_logs_1h * 100) if total_logs_1h > 0 else 0

            lines.append('# HELP dbmonitor_collect_success_rate_1h Collection success rate in last hour (%)')
            lines.append('# TYPE dbmonitor_collect_success_rate_1h gauge')
            lines.append(f'dbmonitor_collect_success_rate_1h {success_rate:.2f}')

            # 最近1小时采集总数
            lines.append('# HELP dbmonitor_collect_total_1h Total collections in last hour')
            lines.append('# TYPE dbmonitor_collect_total_1h counter')
            lines.append(f'dbmonitor_collect_total_1h {total_logs_1h}')

            # UP/DOWN 状态统计
            down_dbs = MonitorLog.objects.filter(
                create_time__gte=five_min_ago, status='DOWN'
            ).values('config_id').distinct().count()

            lines.append('# HELP dbmonitor_databases_down Databases currently DOWN')
            lines.append('# TYPE dbmonitor_databases_down gauge')
            lines.append(f'dbmonitor_databases_down {down_dbs}')

            # ==========================================
            # 告警指标
            # ==========================================
            active_alerts = AlertLog.objects.filter(status='active')
            total_active = active_alerts.count()

            lines.append('# HELP dbmonitor_alerts_active Total number of active alerts')
            lines.append('# TYPE dbmonitor_alerts_active gauge')
            lines.append(f'dbmonitor_alerts_active {total_active}')

            # 按严重程度统计
            severity_stats = active_alerts.values('severity').annotate(count=Count('id'))
            lines.append('# HELP dbmonitor_alerts_by_severity Active alerts by severity')
            lines.append('# TYPE dbmonitor_alerts_by_severity gauge')
            for stat in severity_stats:
                lines.append(f'dbmonitor_alerts_by_severity{{severity="{stat["severity"]}"}} {stat["count"]}')

            # 按类型统计
            type_alert_stats = active_alerts.values('alert_type').annotate(count=Count('id'))
            lines.append('# HELP dbmonitor_alerts_by_type Active alerts by type')
            lines.append('# TYPE dbmonitor_alerts_by_type gauge')
            for stat in type_alert_stats:
                lines.append(f'dbmonitor_alerts_by_type{{alert_type="{stat["alert_type"]}"}} {stat["count"]}')

            # ==========================================
            # 工单指标
            # ==========================================
            pending_tickets = AuditLog.objects.filter(status='pending').count()
            lines.append('# HELP dbmonitor_tickets_pending Number of pending approval tickets')
            lines.append('# TYPE dbmonitor_tickets_pending gauge')
            lines.append(f'dbmonitor_tickets_pending {pending_tickets}')

            # ==========================================
            # 自定义平台指标
            # ==========================================
            custom_metrics = PlatformMetric.objects.all()
            if custom_metrics.exists():
                lines.append('# HELP dbmonitor_custom Custom platform metrics')
                lines.append('# TYPE dbmonitor_custom gauge')
                for metric in custom_metrics:
                    label_str = ''
                    if metric.labels:
                        label_parts = [f'{k}="{v}"' for k, v in metric.labels.items()]
                        label_str = '{' + ','.join(label_parts) + '}'
                    lines.append(f'dbmonitor_custom_{metric.name}{label_str} {metric.value}')

            output = '\n'.join(lines) + '\n'
            self._metrics_cache['output'] = output
            self._cache_time = time.time()

            return output

        except Exception as e:
            logger.error(f"[Prometheus] 指标导出失败: {e}")
            return f'# ERROR: {e}\n'


class HealthChecker:
    """平台健康检查器"""

    @staticmethod
    def check() -> Dict:
        """执行全面的平台健康检查"""
        from monitor.models import DatabaseConfig, MonitorLog, AlertLog

        now = timezone.now()
        five_min_ago = now - timedelta(minutes=5)

        # 数据库连接检查
        db_status = 'ok'
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception as e:
            db_status = f'error: {e}'

        # 采集状态检查
        active_dbs = DatabaseConfig.objects.filter(is_active=True).count()
        recent_collected = MonitorLog.objects.filter(
            create_time__gte=five_min_ago
        ).values('config_id').distinct().count()
        collection_rate = (recent_collected / active_dbs * 100) if active_dbs > 0 else 0

        # 告警状态检查
        active_alerts = AlertLog.objects.filter(status='active').count()
        critical_alerts = AlertLog.objects.filter(status='active', severity='critical').count()

        # 整体健康状态判定
        overall_status = 'healthy'
        issues = []

        if db_status != 'ok':
            overall_status = 'unhealthy'
            issues.append(f'数据库连接异常: {db_status}')

        if collection_rate < 80:
            overall_status = 'degraded' if overall_status == 'healthy' else overall_status
            issues.append(f'采集覆盖率偏低: {collection_rate:.1f}%')

        if critical_alerts > 0:
            if overall_status == 'healthy':
                overall_status = 'warning'
            issues.append(f'存在 {critical_alerts} 个严重告警')

        return {
            'status': overall_status,
            'timestamp': now.isoformat(),
            'components': {
                'database': db_status,
                'api': 'ok',
                'collector': 'ok' if collection_rate >= 80 else 'degraded',
            },
            'metrics': {
                'active_databases': active_dbs,
                'collected_5min': recent_collected,
                'collection_rate': round(collection_rate, 1),
                'active_alerts': active_alerts,
                'critical_alerts': critical_alerts,
            },
            'issues': issues,
        }


# 全局单例
_prometheus_exporter = None


def get_prometheus_exporter() -> PrometheusExporter:
    """获取 Prometheus 导出器单例"""
    global _prometheus_exporter
    if _prometheus_exporter is None:
        _prometheus_exporter = PrometheusExporter()
    return _prometheus_exporter


def prometheus_metrics_view(request):
    """Prometheus 指标端点视图"""
    exporter = get_prometheus_exporter()
    output = exporter.export()
    return HttpResponse(output, content_type='text/plain; version=0.0.4; charset=utf-8')
