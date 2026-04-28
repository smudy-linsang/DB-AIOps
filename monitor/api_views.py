"""
REST API 视图模块
=================

Phase 3.6 核心模块 - REST API 扩展与 RBAC 完善

实现设计文档 5.2 节的外部 API：
- /api/v1/databases/ - 数据库配置列表
- /api/v1/databases/{id}/status/ - 数据库状态
- /api/v1/databases/{id}/metrics/ - 历史指标
- /api/v1/databases/{id}/baseline/ - 基线模型
- /api/v1/databases/{id}/prediction/ - 容量预测
- /api/v1/databases/{id}/health/ - 健康评分
- /api/v1/alerts/ - 告警列表
- /api/v1/auditlogs/ - 运维工单
- /api/v1/health/ - 平台健康检查

Author: DB-AIOps Team
"""

import json
from datetime import datetime, timedelta
from typing import Optional

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth.models import User

from .models import MonitorLog, AlertLog, AuditLog, UserProfile
from .auth import require_auth, require_role, get_user_role, get_user_database_ids, get_user_permissions, is_admin


class JSONResponseMixin:
    """JSON 响应混入类"""
    
    def json_response(self, data: dict, status: int = 200) -> JsonResponse:
        """返回 JSON 响应"""
        return JsonResponse(data, status=status, safe=False)
    
    def error_response(self, message: str, status: int = 400) -> JsonResponse:
        """返回错误响应"""
        return JsonResponse({'error': message}, status=status)
    
    def success_response(self, data: dict = None, message: str = "OK") -> JsonResponse:
        """返回成功响应"""
        response = {'status': 'success', 'message': message}
        if data is not None:
            response['data'] = data
        return JsonResponse(response, safe=False)


class HealthCheckView(JSONResponseMixin, View):
    """平台健康检查 API"""
    
    def get(self, request):
        """
        GET /api/v1/health/
        平台自身健康检查（供外部监控探活）
        """
        # 检查数据库连接
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            db_status = "ok"
        except Exception as e:
            db_status = f"error: {str(e)}"
        
        # 检查采集状态（最近5分钟有日志的数据库数）
        recent_time = datetime.now() - timedelta(minutes=5)
        active_dbs = MonitorLog.objects.filter(
            create_time__gte=recent_time
        ).values('config_id').distinct().count()
        
        # 检查活跃告警数
        active_alerts = AlertLog.objects.filter(status='active').count()
        
        data = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'components': {
                'database': db_status,
                'api': 'ok',
                'collector': 'ok'
            },
            'metrics': {
                'active_databases': active_dbs,
                'active_alerts': active_alerts
            }
        }
        
        return self.json_response(data)


class LoginView(JSONResponseMixin, View):
    """用户登录 API"""
    
    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def post(self, request):
        """
        POST /api/v1/auth/login/
        用户登录，获取Token
        """
        import json
        
        try:
            data = json.loads(request.body)
        except:
            return self.error_response('Invalid JSON body', 400)
        
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not username or not password:
            return self.error_response('Username and password are required', 400)
        
        from .auth import login_user
        result = login_user(username, password)
        
        if not result:
            return self.error_response('Invalid username or password', 401)
        
        return self.json_response({
            'status': 'success',
            'message': 'Login successful',
            'token': result['token'],
            'user': result['user']
        })


class LogoutView(JSONResponseMixin, View):
    """用户登出 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def post(self, request):
        """
        POST /api/v1/auth/logout/
        用户登出，撤销Token
        """
        from .auth import logout_user
        
        token = getattr(request, 'auth_token', None)
        if token:
            logout_user(token)
        
        return self.json_response({
            'status': 'success',
            'message': 'Logout successful'
        })


class DatabaseListView(JSONResponseMixin, View):
    """数据库配置列表 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request):
        """
        GET /api/v1/databases/
        获取所有数据库配置列表（不含密码）
        """
        # 获取用户可见的数据库ID列表（RBAC 数据范围过滤）
        allowed_db_ids = get_user_database_ids(request.user)
        
        # 查询数据库配置
        from .models import DatabaseConfig
        
        if allowed_db_ids is not None:
            configs = DatabaseConfig.objects.filter(id__in=allowed_db_ids, is_active=True)
        else:
            configs = DatabaseConfig.objects.filter(is_active=True)
        
        result = []
        for config in configs:
            # 获取最新采集状态
            latest_log = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
            
            result.append({
                'id': config.id,
                'name': config.name,
                'db_type': config.db_type,
                'host': config.host,
                'port': config.port,
                'service_name': config.service_name or '',
                'is_active': config.is_active,
                'status': latest_log.status if latest_log else 'UNKNOWN',
                'last_collect_time': latest_log.create_time.isoformat() if latest_log else None,
                'create_time': config.create_time.isoformat() if config.create_time else None
            })
        
        return self.json_response({
            'total': len(result),
            'databases': result
        })


class DatabaseStatusView(JSONResponseMixin, View):
    """数据库状态 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request, config_id: int):
        """
        GET /api/v1/databases/{id}/status/
        获取指定数据库当前状态（最新采集结果）
        """
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 获取最新采集日志
        latest = MonitorLog.objects.filter(
            config_id=config_id
        ).order_by('-create_time').first()
        
        if not latest:
            return self.error_response('No data for this database', 404)
        
        # 解析存储的指标数据
        try:
            metrics = json.loads(latest.message) if latest.message else {}
        except (json.JSONDecodeError, TypeError):
            metrics = {}
        
        data = {
            'config_id': config_id,
            'status': latest.status,
            'collected_at': latest.create_time.isoformat(),
            'message': latest.message,
            'metrics': metrics
        }
        
        return self.json_response(data)


class DatabaseMetricsView(JSONResponseMixin, View):
    """历史指标 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request, config_id: int):
        """
        GET /api/v1/databases/{id}/metrics/
        查询历史指标，支持时间范围和指标名过滤
        """
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 解析查询参数
        start_time = request.GET.get('start')
        end_time = request.GET.get('end')
        time_param = request.GET.get('time')  # 支持 1h, 6h, 24h, 7d 等格式
        metric_name = request.GET.get('metric')
        limit = int(request.GET.get('limit', 1000))
        
        # 解析时间范围
        if start_time:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        elif time_param:
            # 解析时间参数 (1h, 6h, 24h, 7d, 30d)
            time_map = {
                '1h': 1,
                '6h': 6,
                '12h': 12,
                '24h': 24,
                '7d': 168,
                '30d': 720
            }
            hours = time_map.get(time_param, 24)
            start_dt = datetime.now() - timedelta(hours=hours)
        else:
            start_dt = datetime.now() - timedelta(hours=24)
        
        if end_time:
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        else:
            end_dt = datetime.now()
        
        # 查询日志
        logs = MonitorLog.objects.filter(
            config_id=config_id,
            create_time__gte=start_dt,
            create_time__lte=end_dt
        ).order_by('-create_time')[:limit]
        
        result = []
        for log in logs:
            try:
                metrics = json.loads(log.message) if log.message else {}
            except (json.JSONDecodeError, TypeError):
                metrics = {}
            
            # 过滤指定指标
            if metric_name:
                # 处理表空间指标 (格式: tablespace_<name>_used_pct)
                if metric_name.startswith('tablespace_'):
                    # 格式: tablespace_<name>_used_pct -> 去掉前缀后分割
                    # 例如: tablespace_SYSTEM_used_pct -> SYSTEM
                    tbs_metric = metric_name[11:]  # 去掉 'tablespace_' 前缀 -> 'SYSTEM_used_pct'
                    tbs_parts = tbs_metric.rsplit('_', 2)  # ['SYSTEM', 'used', 'pct']
                    if len(tbs_parts) >= 3 and tbs_parts[-2] == 'used' and tbs_parts[-1] == 'pct':
                        tbs_name = tbs_parts[0]
                        found = False
                        # 从 tablespaces 查找 (主表空间有 used_pct)
                        tablespaces = metrics.get('tablespaces', [])
                        for tbs in tablespaces:
                            if tbs.get('name') == tbs_name:
                                result.append({
                                    'timestamp': log.create_time.isoformat(),
                                    'metric': metric_name,
                                    'value': tbs.get('used_pct'),
                                    'status': log.status
                                })
                                found = True
                                break
                        # 如果没找到，尝试 temp_tablespaces (可能没有 used_pct)
                        if not found:
                            temp_tablespaces = metrics.get('temp_tablespaces', [])
                            for tbs in temp_tablespaces:
                                if tbs.get('name') == tbs_name:
                                    # temp_tablespaces 没有 used_pct，跳过或返回 null
                                    result.append({
                                        'timestamp': log.create_time.isoformat(),
                                        'metric': metric_name,
                                        'value': None,
                                        'status': log.status
                                    })
                                    found = True
                                    break
                        # 如果没找到，尝试 undo_tablespaces
                        if not found:
                            undo_tablespaces = metrics.get('undo_tablespaces', [])
                            for tbs in undo_tablespaces:
                                if tbs.get('name') == tbs_name:
                                    result.append({
                                        'timestamp': log.create_time.isoformat(),
                                        'metric': metric_name,
                                        'value': None,
                                        'status': log.status
                                    })
                                    break
                # 处理等待事件指标 (格式: wait_event_<event_name>)
                elif metric_name.startswith('wait_event_'):
                    event_name = metric_name[10:]  # 去掉 'wait_event_' 前缀
                    found = False
                    # 从 top_wait_events 中查找
                    top_wait_events = metrics.get('top_wait_events', [])
                    for evt in top_wait_events:
                        if evt.get('event') == event_name:
                            result.append({
                                'timestamp': log.create_time.isoformat(),
                                'metric': metric_name,
                                'value': evt.get('total_waits'),
                                'status': log.status
                            })
                            found = True
                            break
                    # 如果没找到，从 wait_events 中查找
                    if not found:
                        wait_events = metrics.get('wait_events', [])
                        for evt in wait_events:
                            if evt.get('event') == event_name:
                                result.append({
                                    'timestamp': log.create_time.isoformat(),
                                    'metric': metric_name,
                                    'value': evt.get('total_waits'),
                                    'status': log.status
                                })
                                break
                # 普通指标直接查找
                elif metric_name in metrics:
                    result.append({
                        'timestamp': log.create_time.isoformat(),
                        'metric': metric_name,
                        'value': metrics[metric_name],
                        'status': log.status
                    })
            else:
                # 返回所有简单指标（排除数组类型）
                for key, value in metrics.items():
                    if isinstance(value, (str, int, float, bool)):
                        result.append({
                            'timestamp': log.create_time.isoformat(),
                            'metric': key,
                            'value': value,
                            'status': log.status
                        })
        
        return self.json_response({
            'config_id': config_id,
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat(),
            'count': len(result),
            'metrics': result
        })


class DatabaseBaselineView(JSONResponseMixin, View):
    """基线模型 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request, config_id: int):
        """
        GET /api/v1/databases/{id}/baseline/
        获取基线模型数据
        """
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 查询基线模型
        from .models import BaselineModel
        
        baselines = BaselineModel.objects.filter(db_config_id=config_id)
        
        result = []
        for bl in baselines:
            result.append({
                'metric_key': bl.metric_key,
                'time_slot': bl.time_slot,
                'sample_count': bl.sample_count,
                'mean': float(bl.mean) if bl.mean else None,
                'std': float(bl.std) if bl.std else None,
                'p90': float(bl.p90) if bl.p90 else None,
                'p95': float(bl.p95) if bl.p95 else None,
                'p99': float(bl.p99) if bl.p99 else None,
                'normal_min': float(bl.normal_min) if bl.normal_min else None,
                'normal_max': float(bl.normal_max) if bl.normal_max else None,
                'data_sufficient': bl.data_sufficient,
                'updated_at': bl.updated_at.isoformat() if bl.updated_at else None
            })
        
        return self.json_response({
            'config_id': config_id,
            'total_baselines': len(result),
            'baselines': result
        })


class DatabasePredictionView(JSONResponseMixin, View):
    """容量预测 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request, config_id: int):
        """
        GET /api/v1/databases/{id}/prediction/
        获取最新容量预测结果
        """
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 查询最新预测结果
        from .models import PredictionResult
        
        predictions = PredictionResult.objects.filter(
            db_config_id=config_id
        ).order_by('-generated_at')[:10]  # 最近10条
        
        result = []
        for pred in predictions:
            result.append({
                'metric_key': pred.metric_key,
                'resource_name': pred.resource_name,
                'current_value': float(pred.current_value) if pred.current_value else None,
                'monthly_growth_rate': float(pred.monthly_growth_rate) if pred.monthly_growth_rate else None,
                'predicted_warn_date': pred.predicted_warn_date.isoformat() if pred.predicted_warn_date else None,
                'predicted_crit_date': pred.predicted_crit_date.isoformat() if pred.predicted_crit_date else None,
                'model_used': pred.model_used,
                'confidence': float(pred.confidence) if pred.confidence else None,
                'recommendation': pred.recommendation,
                'generated_at': pred.generated_at.isoformat() if pred.generated_at else None
            })
        
        return self.json_response({
            'config_id': config_id,
            'predictions': result
        })


class DatabaseHealthView(JSONResponseMixin, View):
    """健康评分 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request, config_id: int):
        """
        GET /api/v1/databases/{id}/health/
        获取健康评分历史
        """
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 解析查询参数
        days = int(request.GET.get('days', 30))
        start_date = datetime.now().date() - timedelta(days=days)
        
        # 查询健康评分
        from .models import HealthScore
        
        scores = HealthScore.objects.filter(
            db_config_id=config_id,
            score_date__gte=start_date
        ).order_by('-score_date')
        
        result = []
        for score in scores:
            result.append({
                'score_date': score.score_date.isoformat(),
                'total_score': float(score.total_score) if score.total_score else None,
                'availability_score': float(score.availability_score) if score.availability_score else None,
                'capacity_score': float(score.capacity_score) if score.capacity_score else None,
                'performance_score': float(score.performance_score) if score.performance_score else None,
                'config_score': float(score.config_score) if score.config_score else None,
                'ops_score': float(score.ops_score) if score.ops_score else None,
                'score_detail': score.score_detail
            })
        
        return self.json_response({
            'config_id': config_id,
            'days': days,
            'scores': result
        })


class AlertListView(JSONResponseMixin, View):
    """告警列表 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request):
        """
        GET /api/v1/alerts/
        查询告警列表，支持状态/级别/时间过滤
        """
        # 解析查询参数
        status = request.GET.get('status')
        severity = request.GET.get('severity')
        config_id = request.GET.get('config_id')
        start_time = request.GET.get('start')
        end_time = request.GET.get('end')
        limit = int(request.GET.get('limit', 100))
        
        # RBAC - 只能看有权限的数据库告警
        allowed_db_ids = get_user_database_ids(request.user)
        
        # 构建查询
        queryset = AlertLog.objects.all().order_by('-create_time')
        
        if status:
            queryset = queryset.filter(status=status)
        if severity:
            queryset = queryset.filter(severity=severity)
        if config_id:
            if allowed_db_ids is not None and config_id not in allowed_db_ids:
                return self.error_response('Permission denied', 403)
            queryset = queryset.filter(config_id=config_id)
        elif allowed_db_ids is not None:
            queryset = queryset.filter(config_id__in=allowed_db_ids)
        
        if start_time:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            queryset = queryset.filter(create_time__gte=start_dt)
        if end_time:
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            queryset = queryset.filter(create_time__lte=end_dt)
        
        queryset = queryset[:limit]
        
        result = []
        for alert in queryset:
            result.append({
                'id': alert.id,
                'config_id': alert.config_id,
                'alert_type': alert.alert_type,
                'severity': alert.severity,
                'metric_key': alert.metric_key,
                'title': alert.title,
                'description': alert.description,
                'status': alert.status,
                'last_notified_at': alert.last_notified_at.isoformat() if alert.last_notified_at else None,
                'resolved_at': alert.resolved_at.isoformat() if alert.resolved_at else None,
                'create_time': alert.create_time.isoformat() if alert.create_time else None
            })
        
        return self.json_response({
            'total': len(result),
            'alerts': result
        })


class AlertAcknowledgeView(JSONResponseMixin, View):
    """告警确认 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_role(['dba_operator', 'dba_supervisor', 'admin']))
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def post(self, request, alert_id: int):
        """
        POST /api/v1/alerts/{id}/acknowledge/
        确认告警
        """
        try:
            alert = AlertLog.objects.get(id=alert_id)
        except AlertLog.DoesNotExist:
            return self.error_response('Alert not found', 404)
        
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and alert.config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 更新告警状态
        alert.status = 'acknowledged'
        alert.save()
        
        return self.json_response({
            'status': 'success',
            'message': 'Alert acknowledged',
            'alert_id': alert_id
        })


class DatabaseAlertsView(JSONResponseMixin, View):
    """数据库关联告警列表 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request, config_id: int):
        """
        GET /api/v1/databases/{id}/alerts/
        获取指定数据库的告警列表
        """
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 解析查询参数
        status = request.GET.get('status')
        severity = request.GET.get('severity')
        limit = int(request.GET.get('limit', 100))
        
        # 构建查询
        queryset = AlertLog.objects.filter(config_id=config_id).order_by('-create_time')
        
        if status:
            queryset = queryset.filter(status=status)
        if severity:
            queryset = queryset.filter(severity=severity)
        
        queryset = queryset[:limit]
        
        result = []
        for alert in queryset:
            result.append({
                'id': alert.id,
                'config_id': alert.config_id,
                'alert_type': alert.alert_type,
                'severity': alert.severity,
                'metric_key': alert.metric_key,
                'title': alert.title,
                'description': alert.description,
                'status': alert.status,
                'last_notified_at': alert.last_notified_at.isoformat() if alert.last_notified_at else None,
                'resolved_at': alert.resolved_at.isoformat() if alert.resolved_at else None,
                'create_time': alert.create_time.isoformat() if alert.create_time else None
            })
        
        return self.json_response({
            'config_id': config_id,
            'total': len(result),
            'alerts': result
        })


class AuditLogListView(JSONResponseMixin, View):
    """运维工单列表 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request):
        """
        GET /api/v1/auditlogs/
        查询运维工单列表
        """
        # 解析查询参数
        status = request.GET.get('status')
        risk_level = request.GET.get('risk_level')
        config_id = request.GET.get('config_id')
        limit = int(request.GET.get('limit', 100))
        
        # RBAC - 只能看有权限的数据库工单
        allowed_db_ids = get_user_database_ids(request.user)
        
        # 构建查询
        queryset = AuditLog.objects.all().order_by('-create_time')
        
        if status:
            queryset = queryset.filter(status=status)
        if risk_level:
            queryset = queryset.filter(risk_level=risk_level)
        if config_id:
            if allowed_db_ids is not None and config_id not in allowed_db_ids:
                return self.error_response('Permission denied', 403)
            queryset = queryset.filter(config_id=config_id)
        elif allowed_db_ids is not None:
            queryset = queryset.filter(config_id__in=allowed_db_ids)
        
        queryset = queryset[:limit]
        
        result = []
        for log in queryset:
            result.append({
                'id': log.id,
                'config_id': log.config_id,
                'action_type': log.action_type,
                'risk_level': log.risk_level,
                'description': log.description,
                'sql_command': log.sql_command,
                'status': log.status,
                'approver': log.approver,
                'approve_time': log.approve_time.isoformat() if log.approve_time else None,
                'executor': log.executor,
                'execute_time': log.execute_time.isoformat() if log.execute_time else None,
                'execution_result': log.execution_result,
                'create_time': log.create_time.isoformat() if log.create_time else None
            })
        
        return self.json_response({
            'total': len(result),
            'auditlogs': result
        })


class AuditLogApproveView(JSONResponseMixin, View):
    """工单审批 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_role(['dba_supervisor', 'admin']))
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def post(self, request, audit_id: int):
        """
        POST /api/v1/auditlogs/{id}/approve/
        审批通过
        """
        try:
            audit_log = AuditLog.objects.get(id=audit_id)
        except AuditLog.DoesNotExist:
            return self.error_response('Audit log not found', 404)
        
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and audit_log.config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 获取用户角色
        role = get_user_role(request.user)
        
        # 更新审批状态
        if role == 'dba_supervisor':
            if not audit_log.approver_1:
                audit_log.approver_1 = request.user.username
                audit_log.approve_1_at = datetime.now()
                # 如果是高风险工单，需要第二级审批
                if audit_log.risk_level == 'high':
                    audit_log.status = 'pending_approval_2'
                else:
                    audit_log.status = 'approved'
            elif not audit_log.approver_2:
                audit_log.approver_2 = request.user.username
                audit_log.approve_2_at = datetime.now()
                audit_log.status = 'approved'
        elif role == 'admin':
            # admin 可以直接审批
            if not audit_log.approver_1:
                audit_log.approver_1 = request.user.username
                audit_log.approve_1_at = datetime.now()
            audit_log.status = 'approved'
        
        audit_log.save()
        
        return self.json_response({
            'status': 'success',
            'message': 'Audit log approved',
            'audit_id': audit_id,
            'new_status': audit_log.status
        })


class AuditLogRejectView(JSONResponseMixin, View):
    """工单拒绝 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_role(['dba_supervisor', 'admin']))
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def post(self, request, audit_id: int):
        """
        POST /api/v1/auditlogs/{id}/reject/
        拒绝工单
        """
        try:
            audit_log = AuditLog.objects.get(id=audit_id)
        except AuditLog.DoesNotExist:
            return self.error_response('Audit log not found', 404)
        
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and audit_log.config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        audit_log.status = 'rejected'
        audit_log.save()
        
        return self.json_response({
            'status': 'success',
            'message': 'Audit log rejected',
            'audit_id': audit_id
        })


class AuditLogExecuteView(JSONResponseMixin, View):
    """工单执行 API - 实际执行已批准的SQL操作"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_role(['dba_operator', 'dba_supervisor', 'admin']))
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def post(self, request, audit_id: int):
        """
        POST /api/v1/auditlogs/{id}/execute/
        执行已批准的工单
        
        返回:
            {
                'status': 'success' | 'failed',
                'message': str,
                'execution_result': str,
                'affected_rows': int
            }
        """
        from monitor.db_connector import get_db_connection, close_db_connection
        from monitor.auto_remediation_engine import AutoRemediationEngine
        
        try:
            # 1. 获取审计记录
            audit_log = AuditLog.objects.get(id=audit_id)
        except AuditLog.DoesNotExist:
            return self.error_response('Audit log not found', 404)
        
        # 2. RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and audit_log.config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 3. 检查状态
        if audit_log.status != 'approved':
            return self.error_response(
                f"操作状态为 '{audit_log.status}'，只能执行已批准的工单",
                400
            )
        
        # 4. 获取数据库配置
        config = audit_log.config
        
        # 5. 获取数据库连接
        conn = None
        try:
            conn = get_db_connection(config)
        except Exception as e:
            audit_log.status = 'failed'
            audit_log.execution_result = f"数据库连接失败: {str(e)}"
            audit_log.save()
            return self.json_response({
                'status': 'failed',
                'message': '数据库连接失败',
                'execution_result': str(e)
            }, status=500)
        
        # 6. 执行操作
        try:
            # 使用 AutoRemediationEngine 执行
            engine = AutoRemediationEngine(config)
            success, message = engine.execute_operation(
                audit_id=audit_id,
                executor=request.user.username,
                db_connection=conn
            )
            
            # 7. 更新审计记录
            audit_log.refresh_from_db()  # 刷新获取最新状态
            
            return self.json_response({
                'status': 'success' if success else 'failed',
                'message': message,
                'execution_result': audit_log.execution_result or message,
                'audit_id': audit_id,
                'new_status': audit_log.status
            })
            
        except Exception as e:
            # 执行失败
            audit_log.status = 'failed'
            audit_log.execution_result = f"执行异常: {str(e)}"
            audit_log.save()
            
            return self.json_response({
                'status': 'failed',
                'message': '执行异常',
                'execution_result': str(e),
                'audit_id': audit_id
            }, status=500)
            
        finally:
            # 8. 关闭连接
            close_db_connection(conn)


class AuditLogExecuteDryRunView(JSONResponseMixin, View):
    """工单预执行API - 仅验证SQL语法不实际执行"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_role(['dba_operator', 'dba_supervisor', 'admin']))
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def post(self, request, audit_id: int):
        """
        POST /api/v1/auditlogs/{id}/dry-run/
        预执行工单（仅验证SQL语法）
        
        返回:
            {
                'status': 'valid' | 'invalid',
                'message': str,
                'sql_preview': str
            }
        """
        from monitor.db_connector import get_db_connection, close_db_connection
        
        try:
            audit_log = AuditLog.objects.get(id=audit_id)
        except AuditLog.DoesNotExist:
            return self.error_response('Audit log not found', 404)
        
        # RBAC 检查
        allowed_db_ids = get_user_database_ids(request.user)
        if allowed_db_ids is not None and audit_log.config_id not in allowed_db_ids:
            return self.error_response('Permission denied', 403)
        
        # 获取数据库连接
        config = audit_log.config
        conn = None
        try:
            conn = get_db_connection(config)
        except Exception as e:
            return self.json_response({
                'status': 'invalid',
                'message': f"数据库连接失败: {str(e)}",
                'sql_preview': audit_log.sql_command
            })
        
        try:
            cursor = conn.cursor()
            
            # 解析SQL命令
            sql_commands = []
            for line in audit_log.sql_command.split(';'):
                line = line.strip()
                if line and not line.startswith('--'):
                    sql_commands.append(line)
            
            # 尝试解析每条SQL（不执行）
            parsed = []
            for sql in sql_commands:
                try:
                    # 使用 EXPLAIN 或 DESCRIBE 验证语法
                    db_type = config.db_type.lower()
                    if db_type == 'oracle':
                        test_sql = f"EXPLAIN PLAN FOR {sql}"
                    elif db_type in ['mysql', 'gbase', 'tdsql']:
                        test_sql = f"EXPLAIN {sql}"
                    elif db_type in ['pgsql', 'postgresql']:
                        test_sql = f"EXPLAIN {sql}"
                    else:
                        test_sql = sql
                    
                    cursor.execute(test_sql)
                    parsed.append({'sql': sql, 'status': 'valid'})
                except Exception as e:
                    parsed.append({'sql': sql, 'status': 'invalid', 'error': str(e)})
            
            cursor.close()
            
            all_valid = all(p['status'] == 'valid' for p in parsed)
            
            return self.json_response({
                'status': 'valid' if all_valid else 'invalid',
                'message': '所有SQL语法验证通过' if all_valid else '部分SQL语法验证失败',
                'sql_preview': audit_log.sql_command,
                'parsed_commands': parsed
            })
            
        except Exception as e:
            return self.json_response({
                'status': 'invalid',
                'message': f"预执行失败: {str(e)}",
                'sql_preview': audit_log.sql_command
            })
        finally:
            close_db_connection(conn)


# =============================================================================
# 用户管理 API (Phase 3.6 扩展)
# =============================================================================

class UserListView(JSONResponseMixin, View):
    """用户列表 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_role(['admin']))
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request):
        """
        GET /api/v1/users/
        获取用户列表
        """
        users = User.objects.all().order_by('-date_joined')
        
        result = []
        for user in users:
            try:
                profile = user.userprofile
                role = profile.role
                allowed_dbs = profile.allowed_databases
            except:
                role = 'read_only_observer'
                allowed_dbs = []
            
            result.append({
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'is_active': user.is_active,
                'is_staff': user.is_staff,
                'role': role,
                'allowed_databases': allowed_dbs,
                'date_joined': user.date_joined.isoformat() if user.date_joined else None,
                'last_login': user.last_login.isoformat() if user.last_login else None
            })
        
        return self.json_response({
            'total': len(result),
            'users': result
        })
    
    def post(self, request):
        """
        POST /api/v1/users/
        创建用户
        """
        import json
        
        try:
            data = json.loads(request.body)
        except:
            return self.error_response('Invalid JSON body', 400)
        
        username = data.get('username', '').strip()
        password = data.get('password', '')
        email = data.get('email', '').strip()
        role = data.get('role', 'read_only_observer')
        allowed_databases = data.get('allowed_databases', [])
        
        # 验证
        if not username:
            return self.error_response('Username is required', 400)
        if not password:
            return self.error_response('Password is required', 400)
        if User.objects.filter(username=username).exists():
            return self.error_response('Username already exists', 400)
        
        # 创建用户
        user = User.objects.create_user(
            username=username,
            password=password,
            email=email
        )
        
        # 创建用户配置
        UserProfile.objects.create(
            user=user,
            role=role,
            allowed_databases=allowed_databases
        )
        
        return self.json_response({
            'status': 'success',
            'message': 'User created',
            'user_id': user.id
        }, status=201)


class UserDetailView(JSONResponseMixin, View):
    """用户详情 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    @method_decorator(require_role(['admin']))
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request, user_id: int):
        """
        GET /api/v1/users/{id}/
        获取用户详情
        """
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return self.error_response('User not found', 404)
        
        try:
            profile = user.userprofile
            role = profile.role
            allowed_dbs = profile.allowed_databases
        except:
            role = 'read_only_observer'
            allowed_dbs = []
        
        return self.json_response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_active': user.is_active,
            'is_staff': user.is_staff,
            'role': role,
            'allowed_databases': allowed_dbs,
            'date_joined': user.date_joined.isoformat() if user.date_joined else None,
            'last_login': user.last_login.isoformat() if user.last_login else None
        })
    
    def put(self, request, user_id: int):
        """
        PUT /api/v1/users/{id}/
        更新用户信息
        """
        import json
        
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return self.error_response('User not found', 404)
        
        try:
            data = json.loads(request.body)
        except:
            return self.error_response('Invalid JSON body', 400)
        
        # 更新字段
        if 'email' in data:
            user.email = data['email'].strip()
        if 'is_active' in data:
            user.is_active = data['is_active']
        
        user.save()
        
        # 更新用户配置
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if 'role' in data:
            profile.role = data['role']
        if 'allowed_databases' in data:
            profile.allowed_databases = data['allowed_databases']
        profile.save()
        
        return self.json_response({
            'status': 'success',
            'message': 'User updated'
        })


class UserPasswordView(JSONResponseMixin, View):
    """用户密码修改 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def put(self, request, user_id: int):
        """
        PUT /api/v1/users/{id}/password/
        修改用户密码
        """
        import json
        
        # 只有管理员或本人可以修改密码
        if request.user.id != user_id and not is_admin(request.user):
            return self.error_response('Permission denied', 403)
        
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return self.error_response('User not found', 404)
        
        try:
            data = json.loads(request.body)
        except:
            return self.error_response('Invalid JSON body', 400)
        
        password = data.get('password', '')
        if not password:
            return self.error_response('Password is required', 400)
        
        if len(password) < 8:
            return self.error_response('Password must be at least 8 characters', 400)
        
        user.set_password(password)
        user.save()
        
        return self.json_response({
            'status': 'success',
            'message': 'Password updated'
        })


class CurrentUserView(JSONResponseMixin, View):
    """当前用户 API"""
    
    @method_decorator(csrf_exempt)
    @method_decorator(require_auth)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    def get(self, request):
        """
        GET /api/v1/users/me/
        获取当前用户信息
        """
        user = request.user
        
        try:
            profile = user.userprofile
            role = profile.role
            allowed_dbs = profile.allowed_databases
        except:
            role = 'read_only_observer'
            allowed_dbs = []
        
        return self.json_response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_active': user.is_active,
            'role': role,
            'permissions': get_user_permissions(user),
            'allowed_databases': allowed_dbs,
            'date_joined': user.date_joined.isoformat() if user.date_joined else None,
            'last_login': user.last_login.isoformat() if user.last_login else None
        })


# =============================================================================
# API 路由映射（供 urls.py 使用）
# =============================================================================

api_views = {
    'health_check': HealthCheckView.as_view,
    'login': LoginView.as_view,
    'logout': LogoutView.as_view,
    'database_list': DatabaseListView.as_view,
    'database_status': DatabaseStatusView.as_view,
    'database_metrics': DatabaseMetricsView.as_view,
    'database_baseline': DatabaseBaselineView.as_view,
    'database_prediction': DatabasePredictionView.as_view,
    'database_health': DatabaseHealthView.as_view,
    'alert_list': AlertListView.as_view,
    'alert_acknowledge': AlertAcknowledgeView.as_view,
    'auditlog_list': AuditLogListView.as_view,
    'auditlog_approve': AuditLogApproveView.as_view,
    'auditlog_reject': AuditLogRejectView.as_view,
    'auditlog_execute': AuditLogExecuteView.as_view,
    'auditlog_dry_run': AuditLogExecuteDryRunView.as_view,
    'user_list': UserListView.as_view,
    'user_detail': UserDetailView.as_view,
    'user_password': UserPasswordView.as_view,
    'current_user': CurrentUserView.as_view,
}