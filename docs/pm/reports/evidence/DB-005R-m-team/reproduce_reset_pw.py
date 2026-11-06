# -*- coding: utf-8 -*-
"""重置 M-team 本地开发账号 mteam 的密码（仅本地开发库，DB-005 实测用）。

不写入任何入库文件；密码只经环境变量 MTEAM_PW 传入验收脚本。
"""
import os
import sys

sys.path.insert(0, r'C:\DB-AIOps')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
import django  # noqa: E402
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402

U = get_user_model()
pw = os.environ['MTEAM_PW']
u = U.objects.filter(username='mteam').first()
if not u:
    print('NO_USER')
    sys.exit(2)
u.set_password(pw)
u.save()
print('reset ok id=%s is_active=%s' % (u.id, u.is_active))
