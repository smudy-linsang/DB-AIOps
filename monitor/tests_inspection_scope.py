# -*- coding: utf-8 -*-
"""
DB-010A: 巡检安全双闸、执行契约与覆盖呈现回归测试套件。

遵循 AGENTS.md 核心铁律：
- 权限闸与数据范围闸必须独立验证，不可合并（合并会被 403 短路无法验证范围闸）；
- 请求路径查询实例必须经过 DatabaseConfig.objects.visible_to(request.user)；
- 手动巡检入口不能产生 400，契约必须前后贯通（API -> 执行器 -> 持久化 -> 详情展示）；
- 覆盖呈现必须区分未评估（unassessed）、覆盖不足（insufficient_coverage）、正常（normal）。
"""
import json
from unittest.mock import patch

from django.test import Client, TestCase, tag
from django.utils import timezone

from monitor.auth import Perm, RoleCode
from monitor.inspection_executor import DetectionResult, InspectionExecutor
from monitor.models import AlertLog, DatabaseConfig, InspectionFinding, InspectionRun
from monitor.tests_bugfix import login, make_db, make_user


@tag('unit')
class InspectionPermissionGateTests(TestCase):
    """验证 Phase 5 巡检与 RCA 各入口的 RBAC 权限闸（零权限阻断）。"""

    def setUp(self):
        self.db = make_db('insp-perm-db', port=3306)
        # 零权限，但拥有该实例的数据范围 -> 确保测到的是"功能权限"而非"数据范围"
        self.zero_user = make_user('insp_zero', RoleCode.READONLY, [], allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.zero_user)

    def test_inspection_runs_list_requires_permission(self):
        r = self.client.get('/api/v1/inspection/runs/')
        self.assertEqual(r.status_code, 403, '权限闸失效：零权限用户可访问巡检列表')

    def test_inspection_run_trigger_requires_permission(self):
        before = InspectionRun.objects.filter(db_config=self.db).count()
        r = self.client.post(
            '/api/v1/inspection/runs/trigger/',
            data=json.dumps({'config_id': self.db.id, 'level': 'daily'}),
            content_type='application/json'
        )
        self.assertEqual(r.status_code, 403, '权限闸失效：零权限用户可触发巡检')
        after = InspectionRun.objects.filter(db_config=self.db).count()
        self.assertEqual(before, after, '零权限用户调用触发接口产生了幽灵运行记录')

    def test_inspection_run_detail_requires_permission(self):
        run = InspectionRun.objects.create(
            run_id='INSP-PERM-TEST-001',
            db_config=self.db,
            level='daily',
            status='completed',
            started_at=timezone.now(),
        )
        r = self.client.get(f'/api/v1/inspection/runs/{run.run_id}/')
        self.assertEqual(r.status_code, 403, '权限闸失效：零权限用户可读取巡检详情')

    def test_inspection_items_list_requires_permission(self):
        r = self.client.get('/api/v1/inspection/items/')
        self.assertEqual(r.status_code, 403, '权限闸失效：零权限用户可读取巡检项定义')

    def test_inspection_patterns_list_requires_permission(self):
        r = self.client.get('/api/v1/inspection/patterns/')
        self.assertEqual(r.status_code, 403, '权限闸失效：零权限用户可读取问题模式列表')

    def test_alert_rca_detail_requires_permission(self):
        alert = AlertLog.objects.create(
            config=self.db,
            title='Test Alert',
            severity='warning',
            alert_type='cpu_high',
        )
        r = self.client.get(f'/api/v1/alerts/{alert.id}/rca/')
        self.assertEqual(r.status_code, 403, '权限闸失效：零权限用户可读取 RCA 详情')

    def test_alert_rca_quick_requires_permission(self):
        r = self.client.post(
            '/api/v1/alerts/quick-rca/',
            data=json.dumps({'data': {}, 'db_type': 'mysql'}),
            content_type='application/json'
        )
        self.assertEqual(r.status_code, 403, '权限闸失效：零权限用户可调用快速 RCA')


@tag('unit')
class InspectionScopeGateTests(TestCase):
    """验证巡检与 RCA 接口的数据范围闸（越权防泄露与防写入）。"""

    def setUp(self):
        self.mine = make_db('insp-scope-mine', port=3306)
        self.others = make_db('insp-scope-others', port=3307)
        # 权限齐备（包含所有 Phase 5 所需权限），但数据范围严格限定于 self.mine
        all_perms = [v for k, v in vars(Perm).items() if not k.startswith('_') and isinstance(v, str)]
        self.scoped_user = make_user('insp_scoped', RoleCode.DBA, all_perms, allowed_dbs=[self.mine.id])
        self.client = Client()
        login(self.client, self.scoped_user)

    def test_trigger_rejects_out_of_scope_database(self):
        before = InspectionRun.objects.filter(db_config=self.others).count()
        r = self.client.post(
            '/api/v1/inspection/runs/trigger/',
            data=json.dumps({'config_id': self.others.id, 'level': 'daily'}),
            content_type='application/json'
        )
        self.assertEqual(r.status_code, 404, '范围闸失效：允许向范围外实例触发巡检')
        after = InspectionRun.objects.filter(db_config=self.others).count()
        self.assertEqual(before, after, '范围外实例被触发后遗留了运行记录')

    def test_detail_rejects_out_of_scope_database_run(self):
        secret_marker = {'leak_check': 'SECRET_LEAK_TARGET_DB_DATA'}
        others_run = InspectionRun.objects.create(
            run_id='INSP-SCOPE-OTHER-001',
            db_config=self.others,
            level='daily',
            status='completed',
            started_at=timezone.now(),
        )
        InspectionFinding.objects.create(
            run=others_run,
            item_code='FINDING-001',
            title='Confidential finding',
            category='security',
            severity='critical',
            raw_data=secret_marker,
        )
        r = self.client.get(f'/api/v1/inspection/runs/{others_run.run_id}/')
        self.assertEqual(r.status_code, 404, '范围闸失效：允许读取范围外实例的巡检详情')
        content = r.content.decode('utf-8')
        self.assertNotIn('insp-scope-others', content, '越权泄漏了范围外实例名称')
        self.assertNotIn('SECRET_LEAK_TARGET_DB_DATA', content, '越权泄漏了范围外 Finding 数据')

    def test_list_filters_out_of_scope_database_runs(self):
        InspectionRun.objects.create(
            run_id='INSP-MINE-RUN',
            db_config=self.mine,
            level='daily',
            status='completed',
            started_at=timezone.now(),
        )
        InspectionRun.objects.create(
            run_id='INSP-OTHER-RUN',
            db_config=self.others,
            level='daily',
            status='completed',
            started_at=timezone.now(),
        )
        # 全量列表只应返回 mine 的记录
        r = self.client.get('/api/v1/inspection/runs/')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        returned_ids = [run['run_id'] for run in data['runs']]
        self.assertIn('INSP-MINE-RUN', returned_ids)
        self.assertNotIn('INSP-OTHER-RUN', returned_ids)

        # 显式过滤范围外实例应返回空
        r2 = self.client.get(f'/api/v1/inspection/runs/?config_id={self.others.id}')
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()['total'], 0)

    def test_alert_rca_rejects_out_of_scope_alert(self):
        alert = AlertLog.objects.create(
            config=self.others,
            title='Others Alert',
            severity='critical',
            alert_type='cpu_high',
        )
        r = self.client.get(f'/api/v1/alerts/{alert.id}/rca/')
        self.assertEqual(r.status_code, 404, '范围闸失效：允许读取范围外实例的告警 RCA')


@tag('unit')
class InspectionExecutionContractTests(TestCase):
    """验证 InspectionExecutor 契约统一与 API 全链路执行。"""

    def setUp(self):
        self.db = make_db('insp-exec-db', port=3306)
        all_perms = [v for k, v in vars(Perm).items() if not k.startswith('_') and isinstance(v, str)]
        self.user = make_user('insp_dba', RoleCode.DBA, all_perms, allowed_dbs=[self.db.id])
        self.client = Client()
        login(self.client, self.user)

    def test_manual_trigger_api_full_chain_success(self):
        """测试 POST /api/v1/inspection/runs/trigger/ 成功返回 200 且正常生成 Run 与 findings。"""
        def mock_detector(ctx):
            return DetectionResult(
                item_id=ctx.item.get('item_id', 'TEST-01'),
                item_title=ctx.item.get('title', 'Test Check'),
                status='ok',
                summary='检测正常通过',
            )

        with patch.object(InspectionExecutor, '_run_single', side_effect=mock_detector):
            r = self.client.post(
                '/api/v1/inspection/runs/trigger/',
                data=json.dumps({'config_id': self.db.id, 'level': 'daily'}),
                content_type='application/json'
            )

        self.assertEqual(r.status_code, 200, f'手动触发巡检失败: {r.content.decode("utf-8")}')
        data = r.json()
        self.assertIn('run_id', data)
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(data['coverage_status'], 'normal')

        # 验证数据库落库
        run = InspectionRun.objects.get(run_id=data['run_id'])
        self.assertEqual(run.db_config, self.db)
        self.assertEqual(run.triggered_by, 'api')
        self.assertIsNotNone(run.finished_at)

        # 验证 GET 详情全链路闭环
        detail_res = self.client.get(f'/api/v1/inspection/runs/{run.run_id}/')
        self.assertEqual(detail_res.status_code, 200)
        detail_data = detail_res.json()
        self.assertEqual(detail_data['run']['run_id'], run.run_id)
        self.assertEqual(detail_data['run']['db_name'], self.db.name)
        self.assertIn('findings', detail_data)

    def test_executor_both_calling_styles_contract(self):
        """测试 InspectionExecutor 同时兼容实例初始化调用与方法传参调用。"""
        # 方式 1: InspectionExecutor(config, level=level).run()
        executor1 = InspectionExecutor(self.db, level='daily')
        def mock_detector(ctx):
            return DetectionResult(item_id='MOCK', item_title='Mock Item', status='ok')

        with patch.object(InspectionExecutor, '_run_single', side_effect=mock_detector):
            res1 = executor1.run()
        self.assertIsInstance(res1, dict)
        self.assertEqual(res1['status'], 'completed')
        self.assertTrue(res1['run_id'].startswith('INSP-'))

        # 方式 2: InspectionExecutor().run_for_db(config, level=level)
        executor2 = InspectionExecutor(max_workers=2, default_timeout=15)
        with patch.object(InspectionExecutor, '_run_single', side_effect=mock_detector):
            run_id2 = executor2.run_for_db(self.db, level='daily')
        self.assertTrue(run_id2.startswith('INSP-'))


@tag('unit')
class InspectionCoveragePresentationTests(TestCase):
    """验证健康分与覆盖率状态判定（未评估 / 覆盖不足 / 正常）。"""

    def test_coverage_status_unassessed_when_executed_is_zero(self):
        """当执行项数为 0 时，判定为 unassessed，健康分为 0.0。"""
        status, rate = InspectionExecutor._calc_coverage_status(0, 0, 0, 0, skip=80)
        self.assertEqual(status, 'unassessed')
        self.assertEqual(rate, 0.0)

        score = InspectionExecutor._calc_health_score(0, 0, 0, 0, skip=80)
        self.assertEqual(score, 0.0)

    def test_coverage_status_insufficient_when_skips_dominate(self):
        """当 1 项 ok 但 79 项 skip 时，判定为 insufficient_coverage，不得冒充覆盖充分。"""
        status, rate = InspectionExecutor._calc_coverage_status(1, 0, 0, 0, skip=79)
        self.assertEqual(status, 'insufficient_coverage')
        self.assertAlmostEqual(rate, 1.2, places=1)

        # 单独评分依然基于已执行项，但 summary 中有明确覆盖不足标记
        score = InspectionExecutor._calc_health_score(1, 0, 0, 0, skip=79)
        self.assertEqual(score, 100.0)

    def test_coverage_status_normal_when_coverage_sufficient(self):
        """当有效执行项超过 50% 时，判定为 normal。"""
        status, rate = InspectionExecutor._calc_coverage_status(8, 2, 0, 0, skip=2)
        self.assertEqual(status, 'normal')
        self.assertAlmostEqual(rate, 83.3, places=1)

        score = InspectionExecutor._calc_health_score(8, 2, 0, 0, skip=2)
        # (8 * 1.0 + 2 * 0.6) / 10 * 100 = 92.0
        self.assertEqual(score, 92.0)
