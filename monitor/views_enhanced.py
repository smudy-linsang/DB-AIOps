# -*- coding: utf-8 -*-
"""
增强视图 - JSON API 部分
========================

模板渲染视图（dashboard, detail, db_list, db_create, db_edit, db_delete,
remediation_list）已迁移至 React SPA（frontend/）。
本文件仅保留返回 JSON 的 API 视图，供兼容使用。

新增功能请统一在 api_views.py 中以 Class-Based View 实现。

安全说明（BUG-001/003/014 修复）：
- 所有接口统一加 @require_auth 身份认证，写操作叠加细粒度权限校验
- Token 采用 Authorization 头传递（非 Cookie），故 @csrf_exempt 与项目 CBV 保持一致
- 异常统一记录服务端日志，客户端仅返回通用错误，避免泄露内部细节
"""

import json
import logging

from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from monitor.models import DatabaseConfig, MonitorLog, AuditLog
from monitor.baseline_engine import BaselineEngine
from monitor.intelligent_baseline_engine import IntelligentBaselineEngine
from monitor.rca_engine import RCAEngine
from monitor.auth import require_auth, require_permission, Perm

logger = logging.getLogger(__name__)


def _error(message: str, status: int = 500) -> JsonResponse:
    """统一错误响应：客户端仅见通用消息，细节落服务端日志"""
    return JsonResponse({'success': False, 'error': message}, status=status)


# ── Legacy JSON API 端点 ─────────────────────────────────
# 以下接口已被 api_views.py 中的 CBV 替代，保留用于向后兼容

@csrf_exempt
@require_auth
@require_permission(Perm.METRICS_VIEW)
def api_latest_metrics(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    latest_log = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
    if not latest_log:
        return JsonResponse({'error': 'No data'})
    try:
        data = json.loads(latest_log.message)
        return JsonResponse({
            'status': latest_log.status,
            'time': latest_log.create_time.isoformat(),
            'metrics': data,
        })
    except Exception:
        logger.exception("[api_latest_metrics] 解析监控日志失败 config_id=%s", config_id)
        return _error('读取监控数据失败')


@csrf_exempt
@require_auth
@require_permission(Perm.BASELINE_VIEW)
def api_baseline(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    try:
        engine = BaselineEngine(config)
        report = engine.get_full_baseline_report(days=7)
        return JsonResponse(report)
    except Exception:
        logger.exception("[api_baseline] 基线分析失败 config_id=%s", config_id)
        return _error('基线分析失败')


@csrf_exempt
@require_auth
@require_permission(Perm.BASELINE_VIEW)
def api_intelligent_baseline(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    try:
        days = int(request.GET.get('days', 14))
    except (TypeError, ValueError):
        return _error('参数 days 必须为整数', status=400)
    try:
        engine = IntelligentBaselineEngine(config, history_days=days)
        report = engine.get_full_baseline_report(days=days)
        return JsonResponse(report, safe=False)
    except Exception:
        logger.exception("[api_intelligent_baseline] 智能基线分析失败 config_id=%s", config_id)
        return _error('智能基线分析失败')


@csrf_exempt
@require_auth
@require_permission(Perm.METRICS_VIEW)
def api_anomaly_detection(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    latest_log = MonitorLog.objects.filter(config=config, status='UP').order_by('-create_time').first()
    if not latest_log:
        return JsonResponse({'error': 'No data'}, status=404)
    try:
        current_data = json.loads(latest_log.message)
        engine = IntelligentBaselineEngine(config)
        anomalies = engine.check_current_against_baseline(current_data, use_periodic=True)
        return JsonResponse({
            'config_name': config.name,
            'check_time': latest_log.create_time.isoformat(),
            'anomalies': anomalies,
            'anomaly_count': len(anomalies),
        })
    except Exception:
        logger.exception("[api_anomaly_detection] 异常检测失败 config_id=%s", config_id)
        return _error('异常检测失败')


@csrf_exempt
@require_auth
@require_permission(Perm.BASELINE_VIEW)
def api_baseline_trend(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    metric_key = request.GET.get('metric_key', 'active_connections')
    try:
        window_hours = int(request.GET.get('window_hours', 24))
    except (TypeError, ValueError):
        return _error('参数 window_hours 必须为整数', status=400)
    try:
        engine = IntelligentBaselineEngine(config)
        trend = engine.detect_trend(metric_key, window_hours=window_hours)
        periodic = engine.calculate_periodic_baseline(metric_key, 'hour_dow')
        current_baseline = engine.get_current_period_baseline(metric_key)
        return JsonResponse({
            'config_name': config.name,
            'metric_key': metric_key,
            'trend': trend,
            'periodic_baseline': periodic,
            'current_baseline': current_baseline,
        })
    except Exception:
        logger.exception("[api_baseline_trend] 基线趋势分析失败 config_id=%s", config_id)
        return _error('基线趋势分析失败')


@csrf_exempt
@require_auth
@require_permission(Perm.METRICS_VIEW)
def api_rca(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    try:
        engine = RCAEngine(config)
        report = engine.analyze()
        return JsonResponse(report)
    except Exception:
        logger.exception("[api_rca] 根因分析失败 config_id=%s", config_id)
        return _error('根因分析失败')


def health_check(request):
    # 健康探针保持匿名可用（供负载均衡/编排系统探活），不返回敏感信息
    return JsonResponse({
        'status': 'ok',
        'timestamp': timezone.now().isoformat(),
        'version': '0.1.0',
    })


# ── 自愈审批 JSON API ─────────────────────────────────────
# 以下接口返回 JSON，前端通过 Axios 调用

@csrf_exempt
@require_auth
@require_permission(Perm.TICKETS_APPROVE)
def approve_operation(request, audit_id):
    """批准操作"""
    from monitor.auto_remediation_engine import AutoRemediationEngine

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '仅支持 POST 请求'}, status=405)

    try:
        audit = AuditLog.objects.get(id=audit_id)
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': '审计记录不存在'}, status=404)

    approver = request.user.username if request.user.is_authenticated else 'system'
    try:
        engine = AutoRemediationEngine(audit.config)
        success, message = engine.approve_operation(audit_id, approver)
        return JsonResponse({'success': success, 'message': message})
    except Exception:
        logger.exception("[approve_operation] 审批失败 audit_id=%s", audit_id)
        return _error('审批操作失败')


@csrf_exempt
@require_auth
@require_permission(Perm.TICKETS_APPROVE)
def reject_operation(request, audit_id):
    """拒绝操作"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '仅支持 POST 请求'}, status=405)

    reason = request.POST.get('reason', '')
    try:
        audit = AuditLog.objects.get(id=audit_id)
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': '审计记录不存在'}, status=404)

    try:
        engine = AutoRemediationEngine(audit.config)
        success, message = engine.reject_operation(audit_id, reason)
        return JsonResponse({'success': success, 'message': message})
    except Exception:
        logger.exception("[reject_operation] 拒绝失败 audit_id=%s", audit_id)
        return _error('拒绝操作失败')


@csrf_exempt
@require_auth
@require_permission(Perm.AUDIT_LOGS_VIEW)
def get_audit_detail(request, audit_id):
    try:
        audit = AuditLog.objects.get(id=audit_id)
        return JsonResponse({
            'success': True,
            'audit': {
                'id': audit.id,
                'config_name': audit.config.name,
                'db_type': audit.config.db_type,
                'action_type': audit.action_type,
                'action_display': audit.get_action_type_display(),
                'risk_level': audit.risk_level,
                'risk_display': audit.get_risk_level_display(),
                'status': audit.status,
                'status_display': audit.get_status_display(),
                'description': audit.description,
                'sql_command': audit.sql_command,
                'rollback_command': audit.rollback_command or '',
                'executor': audit.executor or '',
                'create_time': audit.create_time.isoformat() if audit.create_time else None,
                'execute_time': audit.execute_time.isoformat() if audit.execute_time else None,
                'execution_result': audit.execution_result or '',
            },
        })
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)


@csrf_exempt
@require_auth
@require_permission(Perm.TICKETS_EXECUTE)
def execute_operation(request, audit_id):
    """执行操作"""
    from monitor.auto_remediation_engine import AutoRemediationEngine
    from monitor.db_connector import get_db_connection, close_db_connection

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '仅支持 POST 请求'}, status=405)

    try:
        audit = AuditLog.objects.get(id=audit_id)
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': '审计记录不存在'}, status=404)

    if audit.status != 'approved':
        return JsonResponse({
            'success': False,
            'message': f"操作状态为 '{audit.status}'，只能执行已批准的工单",
        }, status=400)

    executor = request.user.username if request.user.is_authenticated else 'system'
    conn = None
    try:
        conn = get_db_connection(audit.config)
        engine = AutoRemediationEngine(audit.config)
        success, message = engine.execute_operation(
            audit_id=audit_id,
            executor=executor,
            db_connection=conn,
        )
        audit.refresh_from_db()
        return JsonResponse({
            'success': success,
            'message': message,
            'execution_result': audit.execution_result or message,
            'status': audit.status,
        })
    except Exception as e:
        logger.exception("[execute_operation] 执行失败 audit_id=%s", audit_id)
        try:
            audit.status = 'failed'
            audit.execution_result = f"执行异常: {str(e)}"
            audit.save()
        except Exception:
            logger.exception("[execute_operation] 写入失败状态亦失败 audit_id=%s", audit_id)
        return _error('执行操作失败')
    finally:
        if conn:
            close_db_connection(conn)


@csrf_exempt
@require_auth
@require_permission(Perm.DATABASES_TOGGLE_ACTIVE)
def db_toggle_active(request, config_id):
    """切换数据库启用/禁用状态"""
    config = get_object_or_404(DatabaseConfig, id=config_id)
    if request.method == 'POST':
        config.is_active = not config.is_active
        config.save(update_fields=['is_active'])
        logger.info("[db_toggle_active] %s 切换数据库 %s 启用状态为 %s",
                    request.user.username, config.name, config.is_active)
        return JsonResponse({'success': True, 'is_active': config.is_active})
    return JsonResponse({'success': False}, status=405)
