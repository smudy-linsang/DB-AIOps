# -*- coding: utf-8 -*-
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.utils import timezone
from django.contrib.admin.views.decorators import staff_member_required
from monitor.models import DatabaseConfig, MonitorLog, AuditLog
from monitor.baseline_engine import BaselineEngine
from monitor.intelligent_baseline_engine import IntelligentBaselineEngine
from monitor.rca_engine import RCAEngine
import json

def dashboard(request):
    databases = DatabaseConfig.objects.filter(is_active=True)
    dashboard_data = []
    total_count = databases.count()
    up_count = 0
    down_count = 0
    warning_count = 0
    for db in databases:
        latest_log = MonitorLog.objects.filter(config=db).order_by('-create_time').first()
        info = {'id': db.id, 'name': db.name, 'host': f'{db.host}:{db.port}', 'type': db.get_db_type_display(), 'status': 'UNKNOWN', 'check_time': 'No data', 'details': {}, 'alerts': []}
        if latest_log:
            info['status'] = latest_log.status
            info['check_time'] = latest_log.create_time.strftime('%Y-%m-%d %H:%M')
            if latest_log.status == 'UP':
                up_count += 1
                try:
                    info['details'] = json.loads(latest_log.message)
                except: pass
            else:
                down_count += 1
        dashboard_data.append(info)
    return render(request, 'monitor/dashboard_enhanced.html', {'data': dashboard_data, 'stats': {'total': total_count, 'up': up_count, 'down': down_count, 'warning': warning_count}})

def detail(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    logs = list(MonitorLog.objects.filter(config=config).order_by('-create_time')[:50])
    logs.reverse()
    dates, connections, qps_list = [], [], []
    tbs_trend_map = {}
    all_tbs_names = set()
    for log in logs:
        try:
            data = json.loads(log.message)
            if 'tablespaces' in data:
                for tbs in data['tablespaces']:
                    all_tbs_names.add(tbs['name'])
        except: pass
    for name in all_tbs_names:
        tbs_trend_map[name] = []
    for log in logs:
        local_time = timezone.localtime(log.create_time)
        dates.append(local_time.strftime('%H:%M'))
        data = {}
        try: data = json.loads(log.message)
        except: pass
        connections.append(data.get('active_connections', 0))
        qps_list.append(data.get('qps', 0))
        current_tbs_map = {}
        if 'tablespaces' in data:
            for tbs in data['tablespaces']:
                current_tbs_map[tbs['name']] = tbs['used_pct']
        for name in all_tbs_names:
            tbs_trend_map[name].append(current_tbs_map.get(name, None))
    latest_tablespaces = []
    if logs:
        try:
            latest_data = json.loads(logs[-1].message)
            latest_tablespaces = latest_data.get('tablespaces', [])
        except: pass
    return render(request, 'monitor/detail.html', {'config': config, 'dates': json.dumps(dates), 'connections': json.dumps(connections), 'qps': json.dumps(qps_list), 'tablespaces': latest_tablespaces, 'tbs_trend_map': json.dumps(tbs_trend_map)})

def api_latest_metrics(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    latest_log = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
    if not latest_log: return JsonResponse({'error': 'No data'})
    try:
        data = json.loads(latest_log.message)
        return JsonResponse({'status': latest_log.status, 'time': latest_log.create_time.isoformat(), 'metrics': data})
    except Exception as e: return JsonResponse({'error': str(e)})

def api_baseline(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    try:
        engine = BaselineEngine(config)
        report = engine.get_full_baseline_report(days=7)
        return JsonResponse(report)
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

def api_intelligent_baseline(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    days = int(request.GET.get('days', 14))
    try:
        engine = IntelligentBaselineEngine(config, history_days=days)
        report = engine.get_full_baseline_report(days=days)
        return JsonResponse(report, safe=False)
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

def api_anomaly_detection(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    latest_log = MonitorLog.objects.filter(config=config, status='UP').order_by('-create_time').first()
    if not latest_log: return JsonResponse({'error': 'No data'}, status=404)
    try:
        current_data = json.loads(latest_log.message)
        engine = IntelligentBaselineEngine(config)
        anomalies = engine.check_current_against_baseline(current_data, use_periodic=True)
        return JsonResponse({'config_name': config.name, 'check_time': latest_log.create_time.isoformat(), 'anomalies': anomalies, 'anomaly_count': len(anomalies)})
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

def api_baseline_trend(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    metric_key = request.GET.get('metric_key', 'active_connections')
    window_hours = int(request.GET.get('window_hours', 24))
    try:
        engine = IntelligentBaselineEngine(config)
        trend = engine.detect_trend(metric_key, window_hours=window_hours)
        periodic = engine.calculate_periodic_baseline(metric_key, 'hour_dow')
        current_baseline = engine.get_current_period_baseline(metric_key)
        return JsonResponse({'config_name': config.name, 'metric_key': metric_key, 'trend': trend, 'periodic_baseline': periodic, 'current_baseline': current_baseline})
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

def api_rca(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    try:
        engine = RCAEngine(config)
        report = engine.analyze()
        return JsonResponse(report)
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

def health_check(request):
    return JsonResponse({'status': 'ok', 'timestamp': timezone.now().isoformat(), 'version': '0.1.0'})

def remediation_list(request):
    pending_ops = AuditLog.objects.filter(status='pending').order_by('-create_time')
    approved_ops = AuditLog.objects.filter(status='approved').order_by('-create_time')
    history_ops = AuditLog.objects.exclude(status__in=['pending', 'approved']).order_by('-create_time')[:50]
    return render(request, 'monitor/remediation_list.html', {'pending_ops': pending_ops, 'approved_ops': approved_ops, 'history_ops': history_ops})

def approve_operation(request, audit_id):
    """批准操作"""
    from django.contrib.auth.decorators import login_required
    from monitor.auto_remediation_engine import AutoRemediationEngine
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '仅支持 POST 请求'}, status=405)
    
    try:
        audit = AuditLog.objects.get(id=audit_id)
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': '审计记录不存在'}, status=404)
    
    # 获取当前用户作为审批人
    approver = request.user.username if request.user.is_authenticated else 'system'
    
    # 获取数据库配置
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
    
    # 获取数据库配置
    engine = AutoRemediationEngine(audit.config)
    success, message = engine.reject_operation(audit_id, reason)
    
    return JsonResponse({'success': success, 'message': message})
def get_audit_detail(request, audit_id):
    try:
        audit = AuditLog.objects.get(id=audit_id)
        return JsonResponse({'success': True, 'audit': {
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
            'execution_result': audit.execution_result or ''
        }})
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)
def execute_operation(request, audit_id):
    """执行操作"""
    from monitor.auto_remediation_engine import AutoRemediationEngine
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '仅支持 POST 请求'}, status=405)
    
    try:
        audit = AuditLog.objects.get(id=audit_id)
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': '审计记录不存在'}, status=404)
    
    # 获取执行人
    executor = request.user.username if request.user.is_authenticated else 'system'
    
    # 获取数据库连接
    try:
        engine = AutoRemediationEngine(audit.config)
        # 注意：实际执行需要数据库连接，这里简化处理
        # 完整实现需要建立数据库连接并调用 engine.execute_operation
        success, message = True, "执行功能已就绪，请确保操作已批准"
        return JsonResponse({'success': success, 'message': message})
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'执行异常: {e}'})
DB_TYPE_CHOICES = [('oracle', 'Oracle'), ('mysql', 'MySQL'), ('pgsql', 'PostgreSQL'), ('dm', 'DM8'), ('gbase', 'Gbase 8a'), ('tdsql', 'TDSQL')]
DEFAULT_PORTS = {'oracle': 1521, 'mysql': 3306, 'pgsql': 5432, 'dm': 5236, 'gbase': 5050, 'tdsql': 3306}
def db_list(request):
    databases = DatabaseConfig.objects.all().order_by('-is_active', 'name')
    db_data = []
    for db in databases:
        latest = MonitorLog.objects.filter(config=db).order_by('-create_time').first()
        db_data.append({'db': db, 'latest_status': latest.status if latest else 'UNKNOWN', 'latest_time': latest.create_time if latest else None})
    return render(request, 'monitor/db_list.html', {'db_data': db_data, 'total': len(db_data)})
def db_create(request):
    from monitor.crypto import encrypt_password
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        db_type = request.POST.get('db_type', '')
        host = request.POST.get('host', '').strip()
        port = request.POST.get('port', '')
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        service_name = request.POST.get('service_name', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        errors = []
        if not name: errors.append('Name required')
        if not db_type: errors.append('DB type required')
        if not host: errors.append('Host required')
        if not port or not str(port).isdigit(): errors.append('Port must be number')
        if not username: errors.append('Username required')
        if not password: errors.append('Password required')
        if not errors:
            DatabaseConfig.objects.create(name=name, db_type=db_type, host=host, port=int(port), username=username, password=encrypt_password(password), service_name=service_name or None, is_active=is_active)
            return redirect('db_list')
        return render(request, 'monitor/db_form.html', {'action': 'Create', 'errors': errors, 'form_data': request.POST, 'db_type_choices': DB_TYPE_CHOICES, 'default_ports': DEFAULT_PORTS})
    return render(request, 'monitor/db_form.html', {'action': 'Create', 'db_type_choices': DB_TYPE_CHOICES, 'default_ports': DEFAULT_PORTS})
def db_edit(request, config_id):
    from monitor.crypto import encrypt_password
    config = get_object_or_404(DatabaseConfig, id=config_id)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        db_type = request.POST.get('db_type', '')
        host = request.POST.get('host', '').strip()
        port = request.POST.get('port', '')
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        service_name = request.POST.get('service_name', '').strip()
        is_active = request.POST.get('is_active') == 'on'
        errors = []
        if not name: errors.append('Name required')
        if not host: errors.append('Host required')
        if not port or not str(port).isdigit(): errors.append('Port must be number')
        if not username: errors.append('Username required')
        if not errors:
            config.name = name
            config.db_type = db_type
            config.host = host
            config.port = int(port)
            config.username = username
            config.service_name = service_name or None
            config.is_active = is_active
            if password: config.password = encrypt_password(password)
            config.save()
            return redirect('db_list')
        return render(request, 'monitor/db_form.html', {'action': 'Edit', 'config': config, 'errors': errors, 'form_data': request.POST, 'db_type_choices': DB_TYPE_CHOICES, 'default_ports': DEFAULT_PORTS})
    return render(request, 'monitor/db_form.html', {'action': 'Edit', 'config': config, 'db_type_choices': DB_TYPE_CHOICES, 'default_ports': DEFAULT_PORTS})
def db_delete(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    if request.method == 'POST':
        config.delete()
        return redirect('db_list')
    return render(request, 'monitor/db_confirm_delete.html', {'config': config})
def db_toggle_active(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    if request.method == 'POST':
        config.is_active = not config.is_active
        config.save(update_fields=['is_active'])
        return JsonResponse({'success': True, 'is_active': config.is_active})
    return JsonResponse({'success': False}, status=405)
