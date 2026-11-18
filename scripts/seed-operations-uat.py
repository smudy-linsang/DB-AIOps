"""Create and remove synthetic records for browser acceptance in the cloud dev DB."""

import json
import os
import secrets
import sys
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
django.setup()

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from monitor.auth import Perm
from monitor.models import AlertLog, DatabaseConfig, Incident, InspectionRun, Role, RolePermission, UserProfile

runtime_file = Path('/workspace/dbaiops-runtime/operations-uat.json')

if settings.IS_PRODUCTION or settings.DATABASES['default'].get('HOST') != '127.0.0.1':
    raise RuntimeError('Use only the isolated local development database.')

if sys.argv[1:] == ['cleanup']:
    if runtime_file.exists():
        data = json.loads(runtime_file.read_text())
        with transaction.atomic():
            User.objects.filter(username=data['username']).delete()
            Role.objects.filter(code=data['role']).delete()
            DatabaseConfig.objects.filter(pk__in=data['db_ids'], name__startswith=data['prefix']).delete()
        runtime_file.unlink()
    print('Operations UAT fixtures removed.')
    sys.exit(0)

if runtime_file.exists():
    raise RuntimeError('Run cleanup before creating fresh UAT fixtures.')

suffix = secrets.token_hex(4)
prefix = f'UAT-OPS-{suffix}'
username = f'queue-reader-{suffix}'
role_code = f'queue_reader_{suffix}'

with transaction.atomic():
    visible = DatabaseConfig.objects.create(name=f'{prefix} 北区', db_type='pgsql', host='127.0.0.1',
                                            port=1, username='fixture', is_active=False)
    hidden = DatabaseConfig.objects.create(name=f'{prefix} 南区', db_type='pgsql', host='127.0.0.1',
                                           port=1, username='fixture', is_active=False)
    receipt_id = f'{prefix}-P1'
    for db, code, title, priority in [
        (visible, receipt_id, f'{prefix} 核心连接告警', 'P1'),
        (hidden, f'{prefix}-HIDDEN', f'{prefix} 范围外事故', 'P1'),
        *[(visible, f'{prefix}-{i:02}', f'{prefix} 巡查事项 {i:02}', 'P2') for i in range(21)],
    ]:
        Incident.objects.create(
            config=db, incident_id=code, title=title, priority=priority,
            db_type='pgsql', category='connection', dedup_key=code, occurred_at=timezone.now(),
        )
    AlertLog.objects.create(
        config=visible, title=f'{prefix} 连接池告警', status='acknowledged', severity='warning',
        alert_type='connection', description='合成记录，连接池使用达到阈值。',
    )
    InspectionRun.objects.create(
        db_config=visible, run_id=f'{prefix}-COVERAGE0', level='daily', status='partial',
        started_at=timezone.now(), duration_sec=0, executed_items=1,
        summary={'health_score': 0, 'coverage_rate': 0, 'coverage_status': 'normal'},
    )
    InspectionRun.objects.create(
        db_config=visible, run_id=f'{prefix}-UNKNOWN', level='daily', status='running',
        started_at=timezone.now(), executed_items=1,
    )
    role = Role.objects.create(code=role_code, name='验收只读值班')
    RolePermission.objects.bulk_create(
        RolePermission(role=role, permission_code=permission)
        for permission in (Perm.ALERTS_VIEW, Perm.DASHBOARD_VIEW)
    )
    generated = secrets.token_urlsafe(24)
    user = User.objects.create_user(username=username, password=generated)
    profile = UserProfile.objects.create(user=user, role=role)
    profile.allowed_databases = [visible.pk]

with os.fdopen(os.open(runtime_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
    json.dump({'username': username, 'password': generated, 'prefix': prefix,
               'receipt_id': receipt_id, 'db_ids': [visible.pk, hidden.pk], 'role': role_code}, output)
print('Operations UAT fixtures created.')
