import re

with open(r'D:\DB_Monitor\monitor\views_enhanced.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix db_create
old = "def db_create(request): return render(request, 'monitor/db_form.html', {'action': 'Create', 'db_type_choices': DB_TYPE_CHOICES, 'default_ports': DEFAULT_PORTS})"
new = '''def db_create(request):
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
    return render(request, 'monitor/db_form.html', {'action': 'Create', 'db_type_choices': DB_TYPE_CHOICES, 'default_ports': DEFAULT_PORTS})'''
content = content.replace(old, new)

# Fix db_edit
old = "def db_edit(request, config_id): return render(request, 'monitor/db_form.html', {'action': 'Edit', 'db_type_choices': DB_TYPE_CHOICES, 'default_ports': DEFAULT_PORTS})"
new = '''def db_edit(request, config_id):
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
    return render(request, 'monitor/db_form.html', {'action': 'Edit', 'config': config, 'db_type_choices': DB_TYPE_CHOICES, 'default_ports': DEFAULT_PORTS})'''
content = content.replace(old, new)

# Fix db_delete
old = "def db_delete(request, config_id): return redirect('db_list')"
new = '''def db_delete(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    if request.method == 'POST':
        config.delete()
        return redirect('db_list')
    return render(request, 'monitor/db_confirm_delete.html', {'config': config})'''
content = content.replace(old, new)

# Fix db_toggle_active
old = "def db_toggle_active(request, config_id): return JsonResponse({'success': True})"
new = '''def db_toggle_active(request, config_id):
    config = get_object_or_404(DatabaseConfig, id=config_id)
    if request.method == 'POST':
        config.is_active = not config.is_active
        config.save(update_fields=['is_active'])
        return JsonResponse({'success': True, 'is_active': config.is_active})
    return JsonResponse({'success': False}, status=405)'''
content = content.replace(old, new)

with open(r'D:\DB_Monitor\monitor\views_enhanced.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('All functions fixed!')
