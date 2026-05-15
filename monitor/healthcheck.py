"""
平台自监控健康检查模块

提供 /healthcheck/ 端点供 Docker / K8s 探活使用。
检查范围：
- Django ORM 数据库连通性
- TimescaleDB 连通性
- Elasticsearch 连通性
- Redis 连通性
- APScheduler 任务状态
- 最近采集活跃度
"""

import logging
from datetime import datetime, timedelta

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

logger = logging.getLogger(__name__)


class PlatformHealthCheckView(View):
    """
    平台自监控健康检查 API

    GET /healthcheck/
    供 Docker HEALTHCHECK 或 K8s liveness/readiness probe 使用。
    - 200: 平台健康
    - 503: 平台不健康（关键组件不可用）
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request):
        checks = {}
        overall_healthy = True

        # 1. Django ORM 数据库连通性
        checks['database'] = self._check_django_db()

        # 2. TimescaleDB 连通性
        checks['timescaledb'] = self._check_timescaledb()

        # 3. Elasticsearch 连通性
        checks['elasticsearch'] = self._check_elasticsearch()

        # 4. Redis 连通性
        checks['redis'] = self._check_redis()

        # 5. 采集活跃度（最近 10 分钟有采集记录）
        checks['collector'] = self._check_collector_activity()

        # 6. APScheduler 状态
        checks['scheduler'] = self._check_scheduler()

        # 判定整体健康状态
        # 只有数据库（ORM）不可用时才算不健康，其他组件降级
        if checks['database']['status'] != 'ok':
            overall_healthy = False

        # TimescaleDB 或 ES 不可用视为降级而非不健康
        degraded_components = [
            k for k, v in checks.items()
            if v.get('status') not in ('ok', 'disabled')
        ]

        if degraded_components and overall_healthy:
            overall_status = 'degraded'
        elif overall_healthy:
            overall_status = 'healthy'
        else:
            overall_status = 'unhealthy'

        response_data = {
            'status': overall_status,
            'timestamp': datetime.now().isoformat(),
            'checks': checks,
        }

        status_code = 200 if overall_status in ('healthy', 'degraded') else 503
        return JsonResponse(response_data, status=status_code)

    def _check_django_db(self) -> dict:
        """检查 Django ORM 数据库连通性"""
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return {'status': 'ok', 'message': 'Django ORM connection is alive'}
        except Exception as e:
            logger.error(f"[HealthCheck] Django DB 检查失败: {e}")
            return {'status': 'error', 'message': str(e)}

    def _check_timescaledb(self) -> dict:
        """检查 TimescaleDB 连通性"""
        try:
            from django.conf import settings
            if not getattr(settings, 'TIMESCALEDB_ENABLED', False):
                return {'status': 'disabled', 'message': 'TimescaleDB not enabled'}
            from monitor.timeseries import get_timeseries_storage
            ts = get_timeseries_storage()
            if ts.enabled and ts._get_connection():
                return {'status': 'ok', 'message': 'TimescaleDB connection is alive'}
            return {'status': 'error', 'message': 'TimescaleDB enabled but connection failed'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _check_elasticsearch(self) -> dict:
        """检查 Elasticsearch 连通性"""
        try:
            from monitor.elasticsearch_engine import get_es_client
            client = get_es_client()
            if client and client.ping():
                info = client.info()
                return {
                    'status': 'ok',
                    'message': f"ES {info.get('version', {}).get('number', 'unknown')} is alive",
                }
            return {'status': 'error', 'message': 'ES client created but ping failed'}
        except Exception as e:
            return {'status': 'disabled', 'message': f'ES not available: {str(e)[:100]}'}

    def _check_redis(self) -> dict:
        """检查 Redis 连通性"""
        try:
            from django.core.cache import cache
            # 尝试读写一个测试键
            test_key = '_healthcheck_test'
            cache.set(test_key, 'ok', 10)
            value = cache.get(test_key)
            if value == 'ok':
                return {'status': 'ok', 'message': 'Redis connection is alive'}
            return {'status': 'error', 'message': f'Redis read/write mismatch: got {value}'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)[:100]}

    def _check_collector_activity(self) -> dict:
        """检查采集活跃度"""
        try:
            from monitor.models import MonitorLog
            recent_time = datetime.now() - timedelta(minutes=10)
            active_count = MonitorLog.objects.filter(
                create_time__gte=recent_time
            ).values('config_id').distinct().count()
            total_active = MonitorLog.objects.filter(
                create_time__gte=recent_time, status='UP'
            ).count()
            return {
                'status': 'ok' if active_count > 0 else 'idle',
                'message': f'{active_count} databases with recent data, {total_active} UP records in last 10min',
                'active_databases': active_count,
            }
        except Exception as e:
            return {'status': 'error', 'message': str(e)[:100]}

    def _check_scheduler(self) -> dict:
        """检查调度器状态（简化：基于最近采集记录判断）"""
        try:
            from monitor.models import MonitorLog
            latest = MonitorLog.objects.order_by('-create_time').first()
            if latest:
                age_sec = (datetime.now() - latest.create_time).total_seconds()
                if age_sec < 300:  # 5分钟内有记录
                    return {'status': 'ok', 'message': f'Last collection {int(age_sec)}s ago'}
                else:
                    return {'status': 'warning', 'message': f'Last collection {int(age_sec)}s ago (stale)'}
            return {'status': 'idle', 'message': 'No collection records found'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)[:100]}
