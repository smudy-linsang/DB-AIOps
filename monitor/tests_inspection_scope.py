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


@tag('unit')
class InspectionAliasRegistrationTests(TestCase):
    """DB-016: 010B 首批 8 项同名可调用未注册别名接入与调度验证。"""

    def setUp(self):
        self.mine = make_db('insp-alias-mine', port=3306, db_type='mysql')
        self.others = make_db('insp-alias-others', port=3307, db_type='oracle')
        all_perms = [v for k, v in vars(Perm).items() if not k.startswith('_') and isinstance(v, str)]
        self.user = make_user('alias_dba', RoleCode.DBA, all_perms, allowed_dbs=[self.mine.id])
        self.zero_user = make_user('alias_zero', RoleCode.READONLY, [], allowed_dbs=[self.mine.id])
        self.client = Client()

    def test_registry_contains_all_8_aliases(self):
        """验证 8 项别名均已进入 DETECTOR_REGISTRY 且总注册键数达到 34。"""
        from monitor.inspection_executor import DETECTOR_REGISTRY
        expected_aliases = [
            'INS-COM-INVALID-OBJ',
            'INS-COM-RECYCLEBIN',
            'INS-COM-LONG-TXN',
            'INS-COM-FK-NO-INDEX',
            'INS-COM-STAT-STALE',
            'INS-COM-BACKUP',
            'INS-COM-FAILED-LOGIN',
            'INS-ORA-SCN-HEADROOM',
        ]
        for item_id in expected_aliases:
            self.assertIn(item_id, DETECTOR_REGISTRY, f'别名 {item_id} 未在 DETECTOR_REGISTRY 中登记')
            self.assertTrue(callable(DETECTOR_REGISTRY[item_id]), f'别名 {item_id} 对应的注册项不可调用')
        self.assertGreaterEqual(len(DETECTOR_REGISTRY), 34)

    def test_non_db_detectors_execute_cleanly(self):
        """验证 INS-COM-BACKUP 与 INS-COM-FAILED-LOGIN 在平台内部数据模型上正常执行。"""
        from monitor.inspection_executor import InspectionExecutor
        from monitor.models import AuditLog, MonitorLog

        executor = InspectionExecutor()

        # 1) INS-COM-FAILED-LOGIN
        login_item = {'item_id': 'INS-COM-FAILED-LOGIN', 'title': '失败登录异常'}
        res_login = executor._run_single(self.mine, login_item, 'daily')
        self.assertEqual(res_login.status, 'ok')
        self.assertIn('24h 登录失败 0 次', res_login.summary)

        # 模拟产生失败登录审计日志
        AuditLog.objects.create(
            config=self.mine,
            action_type='API_CREATE',
            description='login failed for test user',
            status='failed',
        )
        res_login2 = executor._run_single(self.mine, login_item, 'daily')
        self.assertEqual(res_login2.metrics.get('failed_logins_24h'), 1)

        # 2) INS-COM-BACKUP
        backup_item = {'item_id': 'INS-COM-BACKUP', 'title': '备份状态'}
        res_bk = executor._run_single(self.mine, backup_item, 'daily')
        self.assertEqual(res_bk.status, 'warning')
        self.assertEqual(res_bk.summary, '无备份记录')

        # 模拟添加备份记录
        MonitorLog.objects.create(
            config=self.mine,
            status='UP',
            message='backup snapshot completed successfully',
        )
        res_bk2 = executor._run_single(self.mine, backup_item, 'daily')
        self.assertEqual(res_bk2.status, 'ok')

    def test_dialect_aware_detectors_graceful_skip(self):
        """验证方言不匹配时的优雅 skip 行为与清晰原因呈现。"""
        from monitor.inspection_executor import InspectionExecutor
        executor = InspectionExecutor()

        # INS-ORA-SCN-HEADROOM 在 mysql 实例上应优雅 skip
        scn_item = {'item_id': 'INS-ORA-SCN-HEADROOM', 'title': 'SCN Headroom'}
        res_scn = executor._run_single(self.mine, scn_item, 'daily')
        self.assertEqual(res_scn.status, 'skip')

        # INS-COM-RECYCLEBIN 在 mysql 实例上应优雅 skip
        rb_item = {'item_id': 'INS-COM-RECYCLEBIN', 'title': '回收站清理检查'}
        res_rb = executor._run_single(self.mine, rb_item, 'weekly')
        self.assertEqual(res_rb.status, 'skip')

        # INS-COM-INVALID-OBJ 在 mysql 实例上应优雅 skip
        inv_item = {'item_id': 'INS-COM-INVALID-OBJ', 'title': '无效对象检查'}
        res_inv = executor._run_single(self.mine, inv_item, 'daily')
        self.assertEqual(res_inv.status, 'skip')

        # INS-COM-FK-NO-INDEX 在 mysql 实例上应优雅 skip
        fk_item = {'item_id': 'INS-COM-FK-NO-INDEX', 'title': '外键无索引'}
        res_fk = executor._run_single(self.mine, fk_item, 'weekly')
        self.assertEqual(res_fk.status, 'skip')

    def test_run_for_db_with_8_items_full_chain(self):
        """验证指定 8 项别名时 run_for_db 及 API 触发全链路贯通，并遵循双闸校验。"""
        from monitor.inspection_executor import InspectionExecutor
        from monitor.models import InspectionFinding, InspectionRun

        login(self.client, self.user)
        # 1. 触发范围内实例并执行
        alias_ids = ['INS-COM-BACKUP', 'INS-COM-FAILED-LOGIN']
        executor = InspectionExecutor()
        run_id = executor.run_for_db(self.mine, level='daily', item_ids=alias_ids)
        run = InspectionRun.objects.get(run_id=run_id)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.total_items, 2)
        self.assertEqual(run.executed_items, 2)
        self.assertGreaterEqual(InspectionFinding.objects.filter(run=run).count(), 1)

        # 2. 详情接口全链路校验
        r_detail = self.client.get(f'/api/v1/inspection/runs/{run.run_id}/')
        self.assertEqual(r_detail.status_code, 200)
        detail_data = r_detail.json()
        self.assertEqual(detail_data['run']['run_id'], run.run_id)
        self.assertEqual(detail_data['run']['executed_items'], 2)

        # 3. 双闸校验：范围外实例被拒绝（404）且无记录生成
        r_other = self.client.post(
            '/api/v1/inspection/runs/trigger/',
            data=json.dumps({'config_id': self.others.id, 'level': 'daily'}),
            content_type='application/json'
        )
        self.assertEqual(r_other.status_code, 404)

        # 4. 双闸校验：零权限账号被拒绝（403）
        client_zero = Client()
        login(client_zero, self.zero_user)
        r_zero = client_zero.post(
            '/api/v1/inspection/runs/trigger/',
            data=json.dumps({'config_id': self.mine.id, 'level': 'daily'}),
            content_type='application/json'
        )
        self.assertEqual(r_zero.status_code, 403)


@tag('unit')
class DamengDriverVisibilityTests(TestCase):
    """
    DB-018: DM 检测器静默不注册修复验证套件。

    验证两项核心缺陷修复：
    1. 依赖声明分层：requirements.txt 区分 pyodbc（连接层）与 dmPython（DM 检测器依赖）；
    2. 缺驱动可见性：纳管 DM 实例缺 dmPython 时，检测器不注册具备明确可见信号：
       - 注册/启动日志告警；
       - start_monitor 采集轮询不再静默跳过，记录告警并生成状态为 DOWN 的 MonitorLog；
       - Celery 异步任务记录告警并生成 MonitorLog；
       - 巡检结论与报告中明确注明「DM 检测器未注册：缺 dmPython」，并生成环境维度 Finding；
       - API 接口透出 conclusion。
    """

    def setUp(self):
        self.dm_db = make_db('test-dm-instance', db_type='dm', port=5236)
        self.user = make_user('dm_user', RoleCode.DBA, [Perm.DATABASES_VIEW], allowed_dbs=[self.dm_db.id])
        self.client = Client()
        login(self.client, self.user)

    def test_requirements_declaration_layered(self):
        """验证 requirements.txt 分层写清连接层与检测器依赖。"""
        import os
        req_path = os.path.join(os.path.dirname(__file__), '..', 'requirements.txt')
        with open(req_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn('pyodbc>=5.0.0', content)
        self.assertIn('达梦数据库连接层驱动', content)
        self.assertIn('dmPython', content)
        self.assertIn('达梦检测器依赖', content)

    def test_checker_unregistered_visible_signals(self):
        """验证缺 dmPython 时注册表行为与告警日志。"""
        from monitor.checkers import CHECKER_MAP, get_checker, is_dm_checker_available
        self.assertFalse(is_dm_checker_available())
        self.assertIsNone(CHECKER_MAP.get('dm'))

        with self.assertLogs('monitor.checkers', level='WARNING') as cm:
            checker = get_checker('dm')
            self.assertIsNone(checker)
            self.assertTrue(any('DM 检测器未注册：缺 dmPython' in output for output in cm.output))

    def test_start_monitor_dm_produces_visible_signal(self):
        """验证 start_monitor 采集轮询达梦实例时不静默跳过，产生 DOWN 日志与明确报错。"""
        from monitor.management.commands.start_monitor import Command
        from monitor.models import MonitorLog

        cmd = Command()
        MonitorLog.objects.filter(config=self.dm_db).delete()

        with self.assertLogs('monitor.management.commands.start_monitor', level='WARNING') as cm:
            cmd._run_single_check(self.dm_db)
            self.assertTrue(any('DM 检测器未注册：缺 dmPython' in output for output in cm.output))

        last_log = MonitorLog.objects.filter(config=self.dm_db).order_by('-create_time').first()
        self.assertIsNotNone(last_log, '纳管 DM 实例采集后未产生 MonitorLog，被静默跳过')
        self.assertEqual(last_log.status, 'DOWN')
        self.assertIn('DM 检测器未注册：缺 dmPython', last_log.message)

    def test_celery_task_dm_produces_visible_signal(self):
        """验证 Celery 异步任务对纳管 DM 实例的可见信号。"""
        from monitor.models import MonitorLog
        from monitor.tasks import collect_single_db

        MonitorLog.objects.filter(config=self.dm_db).delete()

        with self.assertLogs('monitor.tasks', level='WARNING') as cm:
            res = collect_single_db(self.dm_db.id)
            self.assertEqual(res.get('status'), 'skipped')
            self.assertEqual(res.get('reason'), 'missing_dmPython')
            self.assertEqual(res.get('message'), 'DM 检测器未注册：缺 dmPython')
            self.assertTrue(any('DM 检测器未注册：缺 dmPython' in output for output in cm.output))

        last_log = MonitorLog.objects.filter(config=self.dm_db).order_by('-create_time').first()
        self.assertIsNotNone(last_log)
        self.assertEqual(last_log.status, 'DOWN')
        self.assertIn('DM 检测器未注册：缺 dmPython', last_log.message)

    def test_inspection_executor_and_reports_dm_visibility(self):
        """验证巡检执行器、报告生成器及 API 对缺驱动的可见性。"""
        from monitor.inspection_executor import InspectionExecutor
        from monitor.inspection_report_generator import InspectionReportGenerator
        from monitor.models import InspectionFinding, InspectionRun

        # 1. 执行器 run()
        executor = InspectionExecutor(self.dm_db, level='daily')
        result = executor.run()
        self.assertEqual(result.get('conclusion'), 'DM 检测器未注册：缺 dmPython')
        self.assertEqual(result.get('summary', {}).get('conclusion'), 'DM 检测器未注册：缺 dmPython')

        # 2. 检查 Run 与 Finding
        run = InspectionRun.objects.get(run_id=result['run_id'])
        self.assertEqual((run.summary or {}).get('conclusion'), 'DM 检测器未注册：缺 dmPython')
        finding = InspectionFinding.objects.filter(run=run, item_code='INS-DM-DRIVER-MISSING').first()
        self.assertIsNotNone(finding, '未生成 DM 驱动缺失的巡检发现项')
        self.assertEqual(finding.severity, 'warning')
        self.assertIn('DM 检测器未注册：缺 dmPython', finding.raw_data.get('summary', ''))

        # 3. 报告生成器
        gen = InspectionReportGenerator(run)
        md_report = gen.to_markdown()
        self.assertIn('**巡检结论**: **DM 检测器未注册：缺 dmPython**', md_report)

        text_report = gen.to_text()
        self.assertIn('巡检结论: DM 检测器未注册：缺 dmPython', text_report)

        dict_report = gen.to_dict()
        self.assertEqual(dict_report['run'].get('conclusion'), 'DM 检测器未注册：缺 dmPython')

        # 4. API 接口透出
        r_detail = self.client.get(f'/api/v1/inspection/runs/{run.run_id}/')
        self.assertEqual(r_detail.status_code, 200)
        self.assertEqual(r_detail.json()['run'].get('conclusion'), 'DM 检测器未注册：缺 dmPython')

        r_list = self.client.get('/api/v1/inspection/runs/')
        self.assertEqual(r_list.status_code, 200)
        dm_runs = [item for item in r_list.json()['runs'] if item['run_id'] == run.run_id]
        self.assertTrue(len(dm_runs) > 0)
        self.assertEqual(dm_runs[0].get('conclusion'), 'DM 检测器未注册：缺 dmPython')




@tag('unit')
class InspectionBatch2Part1ImplementationTests(TestCase):
    """DB-022: 010B 补实现上半 23 项新检测器与注册表验证。"""

    def setUp(self):
        self.mysql_db = make_db('insp-b2-mysql', port=3306, db_type='mysql')
        self.oracle_db = make_db('insp-b2-oracle', port=1521, db_type='oracle')
        self.pgsql_db = make_db('insp-b2-pg', port=5432, db_type='pgsql')
        self.tdsql_db = make_db('insp-b2-tdsql', port=3308, db_type='tdsql')
        self.items_23 = [
            'INS-COM-USER-LOCKED',
            'INS-COM-PWD-EXPIRE',
            'INS-COM-CAP-7D',
            'INS-COM-CAP-30D',
            'INS-COM-DEADLOCK-24H',
            'INS-COM-MISSING-INDEX',
            'INS-COM-PART-NEEDED',
            'INS-COM-SPARSE-INDEX',
            'INS-COM-PARAM-DEV',
            'INS-COM-ARCH-GAP',
            'INS-COM-LOCK-WAIT',
            'INS-COM-BLOCK-SESSION',
            'INS-COM-OPEN-CURSOR',
            'INS-COM-TEMP-USAGE',
            'INS-COM-UNDO-USAGE',
            'INS-ORA-AWR-TOP-EVT',
            'INS-ORA-AWR-TOP-SQL',
            'INS-ORA-STA-OUTPUT',
            'INS-ORA-SEG-ADV',
            'INS-ORA-FRA',
            'INS-ORA-CTRL-BAK',
            'INS-ORA-ARCH-DELETE',
            'INS-ORA-DBA-USERS',
        ]

    def test_registry_contains_all_23_new_items(self):
        """验证 23 项新检测器均在 DETECTOR_REGISTRY 中登记且总键数达到 57。"""
        from monitor.inspection_executor import DETECTOR_REGISTRY
        self.assertGreaterEqual(len(DETECTOR_REGISTRY), 57)
        for item_id in self.items_23:
            self.assertIn(item_id, DETECTOR_REGISTRY, f'规则 {item_id} 未在 DETECTOR_REGISTRY 中登记')
            self.assertTrue(callable(DETECTOR_REGISTRY[item_id]), f'规则 {item_id} 对应的检测器不可调用')

    def test_dialect_aware_graceful_skips(self):
        """验证方言不适用项优雅返回 skip 并带有明确说明。"""
        from monitor.inspection_executor import DetectionContext, DETECTOR_REGISTRY

        # 1. UNDO-USAGE on pgsql -> skip (堆内 MVCC 无独立 Undo)
        ctx_pg_undo = DetectionContext(
            db_config={'id': self.pgsql_db.id, 'name': self.pgsql_db.name, 'db_type': 'pgsql'},
            db_type='pgsql',
            item={'item_id': 'INS-COM-UNDO-USAGE', 'title': 'Undo 使用率'}
        )
        res_pg_undo = DETECTOR_REGISTRY['INS-COM-UNDO-USAGE'](ctx_pg_undo)
        self.assertEqual(res_pg_undo.status, 'skip')
        self.assertIn('堆内 MVCC', res_pg_undo.summary)

        # 2. PART-NEEDED & TEMP-USAGE on TDSQL -> skip (分布式 Shard 架构)
        for item_id in ['INS-COM-PART-NEEDED', 'INS-COM-TEMP-USAGE']:
            ctx_tdsql = DetectionContext(
                db_config={'id': self.tdsql_db.id, 'name': self.tdsql_db.name, 'db_type': 'tdsql'},
                db_type='tdsql',
                item={'item_id': item_id, 'title': item_id}
            )
            res = DETECTOR_REGISTRY[item_id](ctx_tdsql)
            self.assertEqual(res.status, 'skip')
            self.assertIn('分布式 Shard', res.summary)

        # 3. ARCH-GAP & OPEN-CURSOR on MySQL / PG -> skip (仅支持 oracle, dm)
        for item_id in ['INS-COM-ARCH-GAP', 'INS-COM-OPEN-CURSOR']:
            for dbt in ['mysql', 'pgsql']:
                ctx = DetectionContext(
                    db_config={'id': 1, 'name': 'test', 'db_type': dbt},
                    db_type=dbt,
                    item={'item_id': item_id, 'title': item_id}
                )
                res = DETECTOR_REGISTRY[item_id](ctx)
                self.assertEqual(res.status, 'skip')

        # 4. Oracle 专属 8 项在 MySQL 下优雅 skip
        ora_items = [
            'INS-ORA-AWR-TOP-EVT', 'INS-ORA-AWR-TOP-SQL', 'INS-ORA-STA-OUTPUT',
            'INS-ORA-SEG-ADV', 'INS-ORA-FRA', 'INS-ORA-CTRL-BAK',
            'INS-ORA-ARCH-DELETE', 'INS-ORA-DBA-USERS',
        ]
        for item_id in ora_items:
            ctx = DetectionContext(
                db_config={'id': self.mysql_db.id, 'name': self.mysql_db.name, 'db_type': 'mysql'},
                db_type='mysql',
                item={'item_id': item_id, 'title': item_id}
            )
            res = DETECTOR_REGISTRY[item_id](ctx)
            self.assertEqual(res.status, 'skip', f'{item_id} 在 MySQL 下未能优雅 skip')

    def test_mock_query_detection_results(self):
        """验证检测器在目标库查询正常/异常时的 Finding 和 Metrics 解析。"""
        from monitor.inspection_executor import DetectionContext, GenericDetector

        class FakeCursor:
            def __init__(self, fetchall_data=None, fetchone_data=None, description=None):
                self._fa = fetchall_data or []
                self._fo = fetchone_data
                self.description = description or [('col1',), ('col2',)]
            def execute(self, sql, params=None):
                pass
            def fetchall(self):
                return self._fa
            def fetchone(self):
                return self._fo

        class FakeConn:
            def __init__(self, cur):
                self._cur = cur
            def cursor(self):
                return self._cur
            def close(self):
                pass

        class FakeConnector:
            def __init__(self, cur):
                self._conn = FakeConn(cur)
            def connect(self):
                return self._conn
            def close(self):
                pass

        # 1. detect_locked_users 发现锁定用户
        fc_locked = FakeConnector(FakeCursor(fetchall_data=[('app_user', 'LOCKED', None)]))
        ctx_locked = DetectionContext(
            db_config={'id': self.oracle_db.id, 'name': self.oracle_db.name, 'db_type': 'oracle'},
            db_type='oracle',
            item={'item_id': 'INS-COM-USER-LOCKED', 'title': '账号锁定检查'},
            db_connector=fc_locked
        )
        res_locked = GenericDetector.detect_locked_users(ctx_locked)
        self.assertEqual(res_locked.status, 'warning')
        self.assertIn('app_user', str(res_locked.findings))

        # 2. detect_dba_users 发现 DBA 账号
        fc_dba = FakeConnector(FakeCursor(fetchall_data=[('SYSTEM', 'YES'), ('SCOTT', 'NO')]))
        ctx_dba = DetectionContext(
            db_config={'id': self.oracle_db.id, 'name': self.oracle_db.name, 'db_type': 'oracle'},
            db_type='oracle',
            item={'item_id': 'INS-ORA-DBA-USERS', 'title': 'DBA 权限账号'},
            db_connector=fc_dba
        )
        res_dba = GenericDetector.detect_dba_users(ctx_dba)
        self.assertEqual(res_dba.status, 'ok')
        self.assertEqual(len(res_dba.metrics.get('dba_users', [])), 2)

    def test_executor_run_single_for_all_23_items(self):
        """验证 InspectionExecutor._run_single 对 23 项均能正常派发无未捕获异常。"""
        executor = InspectionExecutor()
        for item_id in self.items_23:
            item = {'item_id': item_id, 'title': f'Title of {item_id}'}
            res = executor._run_single(self.mysql_db, item, 'daily')
            self.assertIsNotNone(res)
            self.assertIn(res.status, ('ok', 'warning', 'critical', 'skip', 'error'))
