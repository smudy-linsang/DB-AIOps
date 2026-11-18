"""Create a local-only browser acceptance account without printing credentials."""
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django

django.setup()
from django.conf import settings
from django.contrib.auth.models import User
from monitor.models import Role, UserProfile

if settings.IS_PRODUCTION or settings.DATABASES['default'].get('HOST') not in ('127.0.0.1', 'localhost'):
    raise RuntimeError('UAT seeding is restricted to a local development database')
path = Path('/workspace/dbaiops-runtime/uat-account.json')
if not path.exists():
    if User.objects.filter(username='ux_acceptance').exists():
        raise RuntimeError('Existing account without a matching UAT credential file; refusing to overwrite')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'username': 'ux_acceptance', 'password': secrets.token_urlsafe(24)}))
    path.chmod(0o600)
account = json.loads(path.read_text())
user, _ = User.objects.get_or_create(username=account['username'])
user.set_password(account['password'])
user.save()
role, _ = Role.objects.get_or_create(code='super_admin', defaults={'name': '验收管理员'})
UserProfile.objects.update_or_create(user=user, defaults={'role': role})
print('Local UAT account initialized; credentials remain in the protected runtime file.')
