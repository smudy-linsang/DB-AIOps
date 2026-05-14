#!/usr/bin/env python
import django, os, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django; django.setup()
from monitor.models import MonitorLog, DatabaseConfig

db = DatabaseConfig.objects.filter(db_type='oracle').first()
log = MonitorLog.objects.filter(config=db).order_by('-create_time').first()
if log and log.message:
    msg = json.loads(log.message)
    print('All keys:', sorted(msg.keys()))
    for k, v in sorted(msg.items()):
        if isinstance(v, (list, dict)):
            print(f'  {k}: [{type(v).__name__}] len={len(v) if isinstance(v,list) else "n/a"}')
            if isinstance(v, list) and len(v) > 0:
                print(f'    sample: {json.dumps(v[0], ensure_ascii=False, default=str)[:200]}')
        else:
            print(f'  {k}: {v}')
else:
    print('No message data')
