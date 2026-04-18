import re

with open(r'D:\DB_Monitor\monitor\views_enhanced.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = "def db_list(request): return render(request, 'monitor/db_list.html', {'db_data': [], 'total': 0})"
new = '''def db_list(request):
    databases = DatabaseConfig.objects.all().order_by('-is_active', 'name')
    db_data = []
    for db in databases:
        latest = MonitorLog.objects.filter(config=db).order_by('-create_time').first()
        db_data.append({'db': db, 'latest_status': latest.status if latest else 'UNKNOWN', 'latest_time': latest.create_time if latest else None})
    return render(request, 'monitor/db_list.html', {'db_data': db_data, 'total': len(db_data)})'''

content = content.replace(old, new)

with open(r'D:\DB_Monitor\monitor\views_enhanced.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')
