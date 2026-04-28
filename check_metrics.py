import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django
django.setup()
from monitor.models import MonitorLog, DatabaseConfig

print('=== 数据库配置 ===')
for db in DatabaseConfig.objects.all():
    print(f'{db.id}: {db.name} ({db.db_type}) - {db.host}:{db.port}')

print()
print('=== 最新采集记录 ===')
# 获取每个数据库配置的最新记录
for db in DatabaseConfig.objects.all():
    latest = MonitorLog.objects.filter(config=db).order_by('-create_time')[:1]
    for log in latest:
        print(f'{log.config.name} ({log.config.db_type}): {log.status} @ {log.create_time}')