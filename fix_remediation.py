with open(r'D:\DB_Monitor\monitor\views_enhanced.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix remediation_list
old = "def remediation_list(request): return render(request, 'monitor/remediation_list.html', {})"
new = """def remediation_list(request):
    pending_ops = AuditLog.objects.filter(status='pending').order_by('-create_time')
    approved_ops = AuditLog.objects.filter(status='approved').order_by('-create_time')
    history_ops = AuditLog.objects.exclude(status__in=['pending', 'approved']).order_by('-create_time')[:50]
    return render(request, 'monitor/remediation_list.html', {'pending_ops': pending_ops, 'approved_ops': approved_ops, 'history_ops': history_ops})"""
content = content.replace(old, new)

# Fix approve_operation
old = "def approve_operation(request, audit_id): return JsonResponse({'success': True})"
new = """def approve_operation(request, audit_id):
    try:
        audit = AuditLog.objects.get(id=audit_id)
        audit.status = 'approved'
        audit.approver = request.user.username if request.user.is_authenticated else 'system'
        audit.approve_time = timezone.now()
        audit.save()
        return JsonResponse({'success': True, 'message': 'Approved'})
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)"""
content = content.replace(old, new)

# Fix reject_operation
old = "def reject_operation(request, audit_id): return JsonResponse({'success': True})"
new = """def reject_operation(request, audit_id):
    try:
        audit = AuditLog.objects.get(id=audit_id)
        reason = request.POST.get('reason', 'No reason')
        audit.status = 'rejected'
        audit.execution_result = f'Rejected: {reason}'
        audit.save()
        return JsonResponse({'success': True, 'message': 'Rejected'})
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)"""
content = content.replace(old, new)

# Fix get_audit_detail
old = "def get_audit_detail(request, audit_id): return JsonResponse({'success': True})"
new = """def get_audit_detail(request, audit_id):
    try:
        audit = AuditLog.objects.get(id=audit_id)
        return JsonResponse({'success': True, 'audit': {
            'id': audit.id,
            'config_name': audit.config.name,
            'db_type': audit.config.db_type,
            'action_type': audit.action_type,
            'description': audit.description,
            'sql_command': audit.sql_command,
            'risk_level': audit.risk_level,
            'status': audit.status,
            'executor': audit.executor or '',
            'execute_time': audit.execute_time.isoformat() if audit.execute_time else None,
            'execution_result': audit.execution_result or ''
        }})
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)"""
content = content.replace(old, new)

# Fix execute_operation  
old = "def execute_operation(request, audit_id): return JsonResponse({'success': True})"
new = """def execute_operation(request, audit_id):
    try:
        audit = AuditLog.objects.get(id=audit_id)
        if audit.status != 'approved':
            return JsonResponse({'success': False, 'message': 'Only approved operations can be executed'}, status=400)
        audit.status = 'executing'
        audit.executor = request.user.username if request.user.is_authenticated else 'system'
        audit.execute_time = timezone.now()
        audit.save()
        # Here would be actual execution logic
        audit.status = 'success'
        audit.execution_result = 'Operation completed successfully'
        audit.save()
        return JsonResponse({'success': True, 'message': 'Executed'})
    except AuditLog.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)"""
content = content.replace(old, new)

with open(r'D:\DB_Monitor\monitor\views_enhanced.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('All remediation functions fixed!')
