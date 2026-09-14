# -*- coding: utf-8 -*-
"""V2.6 W1: 核心 CRUD / 读取视图的权限闸与数据范围闸回归测试。

背景（为什么要有这个文件）：
- `tests_v2_review.V2DataScopeTests` 只覆盖了 `api_views_v2.py`（v2 接口族），
  而 V2.6 审计发现的越权集中在 `api_views.py`（老 CRUD 视图），无人覆盖；
- 同类越权在本项目已复发五次（BUG-103 → REV-01 → R25-01 → RT-01 → V2.6），
  且 lint 规则此前只认 .all()/.filter()/.exclude()，对 .get() 检出率为 0。

铁律（AGENTS.md）：**权限与数据范围是两道独立的闸**。
本文件刻意把两类用例分开：
- ScopeXxx：给足权限、限定数据范围 → 验证"范围闸"；
- PermissionGateXxx：零权限、数据范围包含目标 → 验证"权限闸"。
合在一起测会被 403 短路，等于没测到范围。
"""
import json

from django.test import Client, TestCase, tag

from monitor.auth import Perm, RoleCode
from monitor.models import DatabaseConfig
from monitor.tests_bugfix import login, make_db, make_user


@tag('unit')
class DatabaseDetailScopeTests(TestCase):
    """W1-01/02/03: DatabaseConfigDetailView 的 get/put/delete 三向越权。

    修复前实测：GET 返回 200 并泄露 host/port/username；
    DELETE 返回 200 且目标实例被级联删除（含全部监控数据）。
    """

    def setUp(self):
        self.mine = make_db('w1-mine', port=3306)
        self.others = make_db('w1-others', port=3307)
        # 权限齐备，仅数据范围受限 —— 否则会被权限闸短路，测不到范围
        self.user = make_user(
            'w1scoped', RoleCode.DBA,
            [Perm.DATABASES_VIEW, Perm.DATABASES_UPDATE, Perm.DATABASES_DELETE,
             Perm.DATABASE_DETAIL_VIEW],
            allowed_dbs=[self.mine.id])
        self.client = Client()
        login(self.client, self.user)

    def test_get_rejects_out_of_scope_database(self):
        r = self.client.get(f'/api/v1/databases/{self.others.id}/')
        self.assertEqual(r.status_code, 404, '越权读取了范围外实例的配置详情')
        self.assertNotIn('w1-others', r.content.decode('utf-8'))

    def test_get_allows_in_scope_database(self):
        r = self.client.get(f'/api/v1/databases/{self.mine.id}/')
        self.assertEqual(r.status_code, 200, '范围内实例被误拒（过度收紧）')
        self.assertIn('w1-mine', r.content.decode('utf-8'))

    def test_put_rejects_out_of_scope_database(self):
        r = self.client.put(
            f'/api/v1/databases/{self.others.id}/',
            data=json.dumps({'host': '10.0.0.99', 'port': 3306,
                             'username': 'root', 'password': 'x'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 404, '越权可篡改范围外实例配置')
        self.others.refresh_from_db()
        self.assertNotEqual(self.others.host, '10.0.0.99', '范围外实例被篡改')

    def test_delete_rejects_out_of_scope_database(self):
        r = self.client.delete(f'/api/v1/databases/{self.others.id}/')
        self.assertEqual(r.status_code, 404, '越权可删除范围外实例')
        self.assertTrue(
            DatabaseConfig.objects.filter(id=self.others.id).exists(),
            '越权删除了范围外实例及其全部监控数据')

    def test_response_is_404_not_403(self):
        """越权与不存在必须同响应，否则 403 会泄露"该 id 存在"。"""
        r_missing = self.client.get('/api/v1/databases/999999/')
        r_denied = self.client.get(f'/api/v1/databases/{self.others.id}/')
        self.assertEqual(r_missing.status_code, r_denied.status_code,
                         '越权与不存在的响应码不一致，可被用于枚举实例 id')


@tag('unit')
class DatabaseDetailPermissionTests(TestCase):
    """W1-01 权限闸：零权限账号即使在实际数据范围内也必须被拒。"""

    def setUp(self):
        self.db = make_db('w1-perm-db', port=3306)
        self.nobody = make_user('w1nobody', RoleCode.READONLY, [],
                                allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.nobody)

    def test_get_requires_detail_permission(self):
        r = self.client.get(f'/api/v1/databases/{self.db.id}/')
        self.assertEqual(r.status_code, 403,
                         '权限闸失效：无 database_detail.view 也可读详情')


@tag('unit')
class OtherViewScopeTests(TestCase):
    """W1-04 类别甲：§3.4.3 清单其余视图的数据范围回归。"""

    def setUp(self):
        self.mine = make_db('w1-ov-mine', port=3306)
        self.others = make_db('w1-ov-others', port=3307)
        self.user = make_user(
            'w1ovscoped', RoleCode.DBA,
            [Perm.DATABASES_VIEW, Perm.METRICS_VIEW, Perm.SQL_MONITORING_VIEW,
             Perm.BUSINESS_TOPOLOGY_VIEW, Perm.BUSINESS_TOPOLOGY_MANAGE,
             Perm.CAPACITY_VIEW, Perm.ALERT_CONFIG_VIEW,
             Perm.ALERT_CONFIG_MANAGE, Perm.DATABASE_DETAIL_VIEW],
            allowed_dbs=[self.mine.id])
        self.client = Client()
        login(self.client, self.user)

    def test_performance_hub_rejects_out_of_scope(self):
        r = self.client.get(f'/api/v1/databases/{self.others.id}/performance-hub/')
        self.assertEqual(r.status_code, 404, '越权读取范围外实例性能聚合')

    def test_metrics_history_rejects_out_of_scope(self):
        r = self.client.get(f'/api/v1/databases/{self.others.id}/metrics/history/')
        self.assertEqual(r.status_code, 404, '越权读取范围外实例指标历史')

    def test_topology_get_rejects_out_of_scope(self):
        r = self.client.get(f'/api/v1/databases/{self.others.id}/topology/')
        self.assertEqual(r.status_code, 404, '越权读取范围外实例拓扑')

    def test_topology_post_rejects_out_of_scope(self):
        r = self.client.post(
            f'/api/v1/databases/{self.others.id}/topology/',
            data=json.dumps({'role': 'primary', 'topology_type': 'single'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 404, '越权改写范围外实例拓扑')

    def test_impact_rejects_out_of_scope(self):
        r = self.client.get(f'/api/v1/databases/{self.others.id}/impact/')
        self.assertEqual(r.status_code, 404, '越权读取范围外实例业务影响面')

    def test_slow_queries_rejects_out_of_scope(self):
        r = self.client.get(f'/api/v1/databases/{self.others.id}/slow-queries/')
        self.assertEqual(r.status_code, 404, '越权读取范围外实例慢查询(SQL原文)')

    def test_slow_query_analysis_rejects_out_of_scope(self):
        r = self.client.get(f'/api/v1/databases/{self.others.id}/slow-queries/analysis/')
        self.assertEqual(r.status_code, 404, '越权读取范围外实例慢查询分析')

    def test_sql_text_search_rejects_out_of_scope(self):
        r = self.client.get(
            f'/api/v1/databases/{self.others.id}/slow-queries/search/?q=select')
        self.assertEqual(r.status_code, 404, '越权对范围外实例做 SQL 全文检索')

    def test_predict_now_rejects_out_of_scope(self):
        r = self.client.post(f'/api/v1/databases/{self.others.id}/predict-now/')
        self.assertEqual(r.status_code, 404, '越权对范围外实例触发容量预测')

    def test_alert_overrides_rejects_out_of_scope(self):
        r = self.client.get(f'/api/v1/databases/{self.others.id}/alert-overrides/')
        self.assertEqual(r.status_code, 404, '越权读取范围外实例告警覆盖配置')

    def test_assigned_template_rejects_out_of_scope(self):
        r = self.client.get(f'/api/v1/databases/{self.others.id}/assigned-template/')
        self.assertEqual(r.status_code, 404, '越权读取范围外实例模板分配')


@tag('unit')
class PermissionGateTests(TestCase):
    """W1-04 类别乙：以下视图此前只挂 require_auth，无 require_permission。

    实测（V2.6）：12 个视图仅认证不鉴权 —— 任何登录用户（含零权限账号）
    可读取慢查询 SQL 原文、指标历史、拓扑结构、业务影响面。
    前端 ROUTE_PERMISSION_MAP 本就按权限隐藏这些页面，后端却没设闸。
    """

    def setUp(self):
        self.db = make_db('w1-pg-db', port=3306)
        # 零权限，但数据范围含目标实例 → 确保测到的是"权限"而非"范围"
        self.zero = make_user('w1zero', RoleCode.READONLY, [],
                              allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.zero)

    def _assert_403(self, path, method='get'):
        r = getattr(self.client, method)(path)
        self.assertEqual(r.status_code, 403,
                         f'权限闸失效：零权限账号可访问 {method.upper()} {path}')

    def test_metrics_views_require_permission(self):
        for path in (f'/api/v1/databases/{self.db.id}/performance-hub/',
                     f'/api/v1/databases/{self.db.id}/metrics/history/'):
            with self.subTest(path=path):
                self._assert_403(path)

    def test_sql_monitoring_views_require_permission(self):
        for path in (f'/api/v1/databases/{self.db.id}/slow-queries/',
                     f'/api/v1/databases/{self.db.id}/slow-queries/analysis/',
                     f'/api/v1/databases/{self.db.id}/slow-queries/search/?q=select'):
            with self.subTest(path=path):
                self._assert_403(path)

    def test_topology_and_impact_require_permission(self):
        for path in (f'/api/v1/databases/{self.db.id}/topology/',
                     f'/api/v1/databases/{self.db.id}/impact/'):
            with self.subTest(path=path):
                self._assert_403(path)

    def test_status_view_requires_permission(self):
        self._assert_403(f'/api/v1/databases/{self.db.id}/status/')

    def test_capacity_and_alert_config_require_permission(self):
        self._assert_403(f'/api/v1/databases/{self.db.id}/alert-overrides/')
        self._assert_403(f'/api/v1/databases/{self.db.id}/predict-now/', 'post')
