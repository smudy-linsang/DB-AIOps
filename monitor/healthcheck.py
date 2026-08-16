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

        # 7. 必需后台角色心跳
        checks['workers'] = self._check_required_workers()

        # readiness 的关键依赖不能“降级后仍 200”。按启用项判断，避免负载
        # 均衡器继续把流量送到缺 TSDB/Redis/后台角色的半残节点。
        from monitor import appconf
        critical = ['database']
        if appconf.get('TIMESCALEDB_ENABLED'):
            critical.append('timescaledb')
        if appconf.get('USE_REDIS_CACHE'):
            critical.append('redis')
        if appconf.get('ES_ENABLED'):
            critical.append('elasticsearch')
        if appconf.get('READINESS_REQUIRE_WORKERS'):
            critical.append('workers')
        overall_healthy = all(checks[name].get('status') == 'ok' for name in critical)

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
            'version': self._version(),
            'timestamp': datetime.now().isoformat(),
            'checks': checks,
        }

        status_code = 200 if overall_healthy else 503
        return JsonResponse(response_data, status=status_code)

    @staticmethod
    def _version():
        from dbmonitor.version import __version__
        return __version__

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
            if ts.enabled:
                # 真正跑一条语句：只拿到连接对象不足以证明链路可用
                with ts.cursor() as cur:
                    if cur is not None:
                        cur.execute(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name='session_sample'")
                        columns = {row[0] for row in cur.fetchall()}
                        required = {'wait_class', 'wait_secs', 'trx_age_secs'}
                        missing = sorted(required - columns)
                        if missing:
                            return {
                                'status': 'error',
                                'message': f'TimescaleDB schema 缺列: {missing}',
                            }
                        return {'status': 'ok',
                                'message': 'TimescaleDB connection/schema is ready'}
            return {'status': 'error', 'message': 'TimescaleDB enabled but connection failed'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _check_elasticsearch(self) -> dict:
        """检查 Elasticsearch 连通性"""
        try:
            from monitor import appconf
            if not appconf.get('ES_ENABLED'):
                return {'status': 'disabled', 'message': 'Elasticsearch not enabled'}
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
            from monitor import appconf
            if not appconf.get('USE_REDIS_CACHE'):
                return {'status': 'disabled', 'message': 'Redis cache not enabled'}
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
            from django.utils import timezone
            from monitor.models import MonitorLog
            latest = MonitorLog.objects.order_by('-create_time').first()
            if latest:
                now = timezone.now()
                ct = latest.create_time
                if timezone.is_naive(ct):
                    ct = timezone.make_aware(ct)
                age_sec = (now - ct).total_seconds()
                if age_sec < 300:  # 5分钟内有记录
                    return {'status': 'ok', 'message': f'Last collection {int(age_sec)}s ago'}
                else:
                    return {'status': 'warning', 'message': f'Last collection {int(age_sec)}s ago (stale)'}
            return {'status': 'idle', 'message': 'No collection records found'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)[:100]}

    def _check_required_workers(self) -> dict:
        """校验 collector/sentinel/pipeline 至少一个新鲜实例。"""
        try:
            from monitor import appconf
            if not appconf.get('READINESS_REQUIRE_WORKERS'):
                return {'status': 'disabled', 'message': 'worker readiness check disabled'}
            from django.utils import timezone
            from monitor.models import ComponentHeartbeat
            from monitor.self_monitor import COMPONENTS

            now = timezone.now()
            missing, stale = [], []
            for code in ('collector', 'sentinel', 'pipeline'):
                spec = COMPONENTS[code]
                beats = list(ComponentHeartbeat.objects.filter(component=code))
                fresh = [b for b in beats if b.status == 'up' and
                         (now - b.last_beat_at).total_seconds() <= spec[2]]
                if not beats:
                    missing.append(code)
                elif not fresh:
                    stale.append(code)
            if missing or stale:
                return {'status': 'error', 'missing': missing, 'stale': stale,
                        'message': '必需后台角色未就绪'}
            return {'status': 'ok', 'message': '必需后台角色均有新鲜心跳'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)[:100]}


class LivenessView(View):
    """只证明 Web 进程能够响应，不访问任何外部依赖。"""

    def get(self, request):
        from dbmonitor.version import __version__
        return JsonResponse({'status': 'alive', 'version': __version__})


class ReadinessView(PlatformHealthCheckView):
    """生产流量就绪探针；关键依赖或后台角色失败时返回 503。"""
