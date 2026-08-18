# -*- coding: utf-8 -*-
"""全局视图的数据范围与权限回归防线。

RT-01：容量总览/拓扑总览等"全局"视图此前只有 require_auth，既无权限校验也无
数据范围过滤，零权限账号即可读到全部实例的名称与主机地址。

这一类越权已经复发四次（BUG-103 → REV-01 → R25-01 → RT-01），每次都是
"新写视图时忘了套范围"。修复方式是统一到 DatabaseConfig.objects.visible_to()，
并由 scripts/lint_redlines.py 的 [未套数据范围] 规则在提交时拦截遗漏。
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
        # 关键：这里必须用"有权限、但数据范围受限"的账号。
        # 若用零权限账号，会先被 403 挡住，于是"没泄露"是权限拦下的结果，
        # 数据范围这一维根本没被测到 —— 权限与范围是两道独立的闸，各测各的。
        from monitor.auth import Perm
        self.user = make_user(
            'rt-scoped', RoleCode.DBA,
            [Perm.METRICS_VIEW, Perm.DATABASES_VIEW], allowed_dbs=[self.mine.id])
        self.client = Client()
        login(self.client, self.user)

    def _assert_no_leak(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200,
                         f'{path} 应对有权限账号放行，才谈得上验证数据范围')
        body = response.content.decode('utf-8', 'ignore')
        self.assertNotIn(
            '越权探针-生产库', body,
            f'{path} 向范围外泄露了实例名称')
        self.assertNotIn(
            '10.77.77.77', body,
            f'{path} 向范围外泄露了实例主机地址')

    def test_capacity_overview_respects_scope(self):
        self._assert_no_leak('/api/v1/capacity/overview/')

    def test_topology_overview_respects_scope(self):
        self._assert_no_leak('/api/v1/topology/overview/')

    def test_dashboard_and_metric_endpoints_respect_scope(self):
        for path in ('/api/v1/dashboard/stats/',
                     '/api/v1/dashboard/charts/',
                     '/api/v1/alert-rules/available-metrics/?db_type=oracle'):
            with self.subTest(path=path):
                self._assert_no_leak(path)


@tag('unit')
class GlobalViewPermissionTests(TestCase):
    """这些"全局"视图此前只有 require_auth —— 任何登录账号都能读。

    补 require_permission 后，无对应权限的账号必须被挡在 403。
    """

    ENDPOINTS = (
        ('/api/v1/capacity/overview/', 'metrics.view'),
        ('/api/v1/topology/overview/', 'databases.view'),
        ('/api/v1/dashboard/stats/', 'metrics.view'),
        ('/api/v1/dashboard/charts/', 'metrics.view'),
        ('/api/v1/alert-rules/available-metrics/?db_type=oracle', 'metrics.view'),
    )

    def test_endpoints_require_explicit_permission(self):
        zero = make_user('scope-zero', RoleCode.READONLY, [], allowed_dbs=[])
        client = Client()
        login(client, zero)
        for path, _perm in self.ENDPOINTS:
            with self.subTest(path=path):
                self.assertEqual(
                    client.get(path).status_code, 403,
                    f'{path} 对零权限账号仍然放行')

    def test_endpoints_allow_holder_of_required_permission(self):
        from monitor.auth import Perm
        holder = make_user(
            'scope-holder', RoleCode.DBA,
            [Perm.METRICS_VIEW, Perm.DATABASES_VIEW], allowed_dbs=[])
        client = Client()
        login(client, holder)
        for path, _perm in self.ENDPOINTS:
            with self.subTest(path=path):
                self.assertEqual(
                    client.get(path).status_code, 200,
                    f'{path} 把有权限的账号也挡住了（过度收紧）')


@tag('unit')
class RedlineScopeRuleTests(TestCase):
    """红线扫描必须能拦住"请求路径裸查 DatabaseConfig"。

    这条规则是防第五次复发的机制本身，必须有测试守住。
    """

    def _lint(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'lint_redlines', 'scripts/lint_redlines.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_rule_flags_unscoped_query_in_request_path(self):
        lint = self._lint()
        added = {'monitor/api_views.py': set(range(1, 100000))}
        hits = lint.check_unscoped_config_queries(['monitor/api_views.py'], added)
        self.assertEqual(
            hits, [],
            f'现网代码仍有未套数据范围的请求路径查询: {hits}')

    def test_diff_base_env_extends_scan_range(self):
        import os
        from unittest.mock import patch
        lint = self._lint()
        # 干净检出 + CI + 传入基线 → 扫描整段推送而不是只扫最后一个提交
        with patch.object(lint, '_git', side_effect=lambda *a: '' if a[:2] == ('diff', 'HEAD') else 'abc123\n'), \
                patch.dict(os.environ, {'CI': '1', 'REDLINE_DIFF_BASE': 'basesha'}):
            self.assertEqual(lint._diff_args(), ('basesha', 'HEAD'))
        # 全零基线（新分支首推）应回退到 HEAD^
        with patch.object(lint, '_git', side_effect=lambda *a: '' if a[:2] == ('diff', 'HEAD') else 'abc123\n'), \
                patch.dict(os.environ, {'CI': '1', 'REDLINE_DIFF_BASE': '0' * 40}):
            self.assertEqual(lint._diff_args(), ('HEAD^', 'HEAD'))
