# -*- coding: utf-8 -*-
"""
增强视图 - JSON API 部分
========================

模板渲染视图（dashboard, detail, db_list, db_create, db_edit, db_delete,
remediation_list）已迁移至 React SPA（frontend/）。
本文件仅保留返回 JSON 的 API 视图，供兼容使用。

新增功能请统一在 api_views.py 中以 Class-Based View 实现。
"""

from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.utils import timezone

from monitor.models import DatabaseConfig, MonitorLog, AuditLog
from monitor.baseline_engine import BaselineEngine
from monitor.intelligent_baseline_engine import IntelligentBaselineEngine
from monitor.rca_engine import RCAEngine
import json


# ── Legacy JSON API 端点 ─────────────────────────────────
# 以下接口已被 api_views.py 中的 CBV 替代，保留用于向后兼容

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
    except Exception as e:
        return JsonResponse({'error': str(e)})


def api_baseline(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    try:
        engine = BaselineEngine(config)
        report = engine.get_full_baseline_report(days=7)
        return JsonResponse(report)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def api_intelligent_baseline(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    days = int(request.GET.get('days', 14))
    try:
        engine = IntelligentBaselineEngine(config, history_days=days)
        report = engine.get_full_baseline_report(days=days)
        return JsonResponse(report, safe=False)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


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
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def api_baseline_trend(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    metric_key = request.GET.get('metric_key', 'active_connections')
    window_hours = int(request.GET.get('window_hours', 24))
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
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def api_rca(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    try:
        engine = RCAEngine(config)
        report = engine.analyze()
        return JsonResponse(report)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def health_check(request):
    return JsonResponse({
        'status': 'ok',
        'timestamp': timezone.now().isoformat(),
        'version': '0.1.0',
    })


# ── 自愈审批 JSON API ─────────────────────────────────────
# 以下接口返回 JSON，前端通过 Axios 调用

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
    engine = AutoRemediationEngine(audit.config)
    success, message = engine.approve_operation(audit_id, approver)
    return JsonResponse({'success': success, 'message': message})


def reject_operation(request, audit_id):
    """拒绝操作"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '仅支持 POST 请求'}, status=405)

    reason = request.POST.get('reason', '')
    try:
        audit = AuditLog.objects.get(id=audit_id)
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': '审计记录不存在'}, status=404)

    engine = AutoRemediationEngine(audit.config)
    success, message = engine.reject_operation(audit_id, reason)
    return JsonResponse({'success': success, 'message': message})


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
        try:
            audit.status = 'failed'
            audit.execution_result = f"执行异常: {str(e)}"
            audit.save()
        except Exception:
            pass
        return JsonResponse({'success': False, 'message': f'执行异常: {e}'})
    finally:
        if conn:
            close_db_connection(conn)


def db_toggle_active(request, config_id):
    """切换数据库启用/禁用状态"""
    config = get_object_or_404(DatabaseConfig, id=config_id)
    if request.method == 'POST':
        config.is_active = not config.is_active
        config.save(update_fields=['is_active'])
        return JsonResponse({'success': True, 'is_active': config.is_active})
    return JsonResponse({'success': False}, status=405)
