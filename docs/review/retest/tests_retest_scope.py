# -*- coding: utf-8 -*-
"""v2.5 整改复测中新发现的越权端点（既有问题，非本次整改引入）。

见 V2.5_REMEDIATION_RETEST.md 的 RT-01。
放在此处的原因与用法见同目录 README.md —— 修复后请移回 monitor/。
"""
from django.test import Client, TestCase, tag

from monitor.auth import RoleCode
from monitor.crypto import encrypt_password
from monitor.models import DatabaseConfig
from monitor.tests_bugfix import login, make_db, make_user


@tag('unit')
class GlobalOverviewScopeTests(TestCase):
    """RT-01：容量总览与拓扑总览只有 require_auth，既无权限校验也无数据范围。

    零权限、零数据范围的账号即可读到全部纳管实例的名称与主机地址。
    这两个端点在 api_views.py 里，本次整改未触碰，属既有缺陷。
    """

    def setUp(self):
        self.mine = make_db('retest-mine', port=3306)
        self.secret = DatabaseConfig.objects.create(
            name='越权探针-生产库', db_type='oracle', host='10.77.77.77',
            port=1521, username='u', password=encrypt_password('p'), is_active=True)
        # 最低权限：无任何 Perm，数据范围为空
        self.user = make_user('rt-lowest', RoleCode.READONLY, [], allowed_dbs=[])
        self.client = Client()
        login(self.client, self.user)

    def _assert_no_leak(self, path):
        response = self.client.get(path)
        body = response.content.decode('utf-8', 'ignore')
        self.assertNotIn(
            '越权探针-生产库', body,
            f'{path} 向零权限账号泄露了范围外实例名称')
        self.assertNotIn(
            '10.77.77.77', body,
            f'{path} 向零权限账号泄露了范围外实例主机地址')

    def test_capacity_overview_respects_scope(self):
        self._assert_no_leak('/api/v1/capacity/overview/')

    def test_topology_overview_respects_scope(self):
        self._assert_no_leak('/api/v1/topology/overview/')
