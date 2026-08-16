# -*- coding: utf-8 -*-
"""独立复审用的复现测试（v2.0）—— 见 V2.0_INDEPENDENT_REVIEW.md。

这些用例不是要"通过"，而是**钉住复审中发现的缺陷**：
每条都能在当前 master 上稳定复现一个真实问题。

## 为什么全都标了 @expectedFailure

断言是真跑的，缺陷也是真存在的。标记只是把"已知未修"这个事实**写进测试记录**，
好让它们现在就能进仓库而不至于让 master 挂红（本仓库直推 master、CI 是事后信号，
红了就是真红，长期挂红会让红灯失去意义）。

这**不是**把测试跳过去换绿灯：
- 用例照常执行，断言照常求值，只是失败被记为"预期失败"；
- **一旦对应缺陷被修好，用例会转为 unexpected success，整个测试套件随即判失败。**
  也就是说它会主动提醒你"这条可以摘标记了"，而不是悄悄变绿。

## 修复流程

修好某一项后，删掉那条用例上的 `@unittest.expectedFailure`，它就从"缺陷复现"
转正为"回归防线"。全部摘完时，本文件即成为 v2.0 的常规回归集。
"""
import json
import unittest

from django.test import Client, TestCase, tag
from django.utils import timezone

from monitor.auth import Perm, RoleCode
from monitor.crypto import encrypt_password
from monitor.models import DatabaseConfig, Incident
from monitor.tests_bugfix import login


def _cfg(name='rev-db'):
    return DatabaseConfig.objects.create(
        name=name, db_type='mysql', host='127.0.0.1', port=3306,
        username='u', password=encrypt_password('p'), is_active=True)


def _incident(cfg, status='open', title='锁等待告警'):
    now = timezone.now()
    return Incident.objects.create(
        incident_id=f'INC-{status}-{title}-{now.timestamp()}',
        config=cfg, db_type=cfg.db_type, category='lock', title=title,
        priority='P1', status=status, dedup_key=f'k-{status}-{now.timestamp()}',
        occurred_at=now)


@tag('unit')
class CopilotIncidentSeverityTests(TestCase):
    """REV-05：Incident 没有 severity 字段，copilot._get_database_context 直接读它。

    只要目标库存在任意一条 Incident，Copilot 问答与一键体检都会 AttributeError。
    也就是说：越是"有故障、真正需要它"的实例，这两个功能越必然崩。
    """

    @unittest.expectedFailure
    def test_database_context_survives_incident(self):
        from monitor.copilot import _get_database_context
        cfg = _cfg()
        _incident(cfg)
        ctx = _get_database_context(cfg)   # 当前会抛 AttributeError
        self.assertIn('recent_incidents', ctx)

    @unittest.expectedFailure
    def test_quick_assessment_survives_incident(self):
        from monitor.copilot import generate_quick_health_assessment
        cfg = _cfg('rev-db-2')
        _incident(cfg)
        res = generate_quick_health_assessment(cfg.id)  # 当前会抛 AttributeError
        self.assertIn('overall_score', res)


@tag('unit')
class QuickAssessmentFabricatedMetricsTests(TestCase):
    """REV-07：无任何采集数据时，体检用编造的默认值（cpu=35 / conn=20 / disk=45）
    给出"性能与负载 100 分""容量规划 100 分"。对零数据实例输出满分是虚假保证。
    """

    @unittest.expectedFailure
    def test_no_metrics_must_not_yield_full_marks(self):
        from monitor.copilot import generate_quick_health_assessment
        cfg = _cfg('rev-db-nometric')
        res = generate_quick_health_assessment(cfg.id)
        dims = {d['name']: d['score'] for d in res['dimensions']}
        self.assertNotEqual(
            dims.get('性能与负载'), 100,
            '没有任何指标数据时，性能维度不应该拿满分（当前用默认值 cpu=35/conn=20 编出来的）')
        self.assertNotEqual(
            dims.get('容量规划'), 100,
            '没有任何指标数据时，容量维度不应该拿满分（当前用默认值 disk=45 编出来的）')


@tag('unit')
class ConnUsageUnitConfusionTests(TestCase):
    """REV-08：conn_usage 缺省回退到 threads_connected（绝对连接数），
    却拿去和百分比阈值 85 比。90 个连接 / 上限 2000（4.5%）会被判成"连接池接近饱和"。
    """

    @unittest.expectedFailure
    def test_absolute_connection_count_is_not_a_percentage(self):
        import json
        from monitor.copilot import generate_quick_health_assessment
        from monitor.models import MonitorLog
        cfg = _cfg('rev-db-conn')
        MonitorLog.objects.create(
            config=cfg, status='UP',
            message=json.dumps({'threads_connected': 90, 'max_connections': 2000}))
        res = generate_quick_health_assessment(cfg.id)
        titles = [r['title'] for r in res['risk_items']]
        self.assertNotIn(
            '连接池接近饱和', titles,
            '90/2000 = 4.5% 的连接使用率被误判为饱和：把绝对连接数当成了百分比')


@tag('unit')
class V2DataScopeTests(TestCase):
    """REV-01：v2 接口族缺数据范围隔离，是 BUG-103 的原地复发。

    对照组就在同一个仓库里：`api_views_phase8.py` 写了 `_get_config()` /
    `_get_incident()`，每个端点都过一遍 `get_user_database_ids`；
    而 `api_views_v2.py` 直接 `DatabaseConfig.objects.filter(id=config_id)`。

    最严重的是 execute-safely —— 越权的不是"看数据"，是"对别人的库下运维动作"。
    """

    def setUp(self):
        from monitor.tests_bugfix import make_db, make_user
        self.mine = make_db('scope-mine', port=3306)
        self.others = make_db('scope-others', port=3307)
        # 数据范围只到 self.mine，但拥有 v2 端点要求的全部权限
        self.user = make_user(
            'v2scoped', RoleCode.DBA,
            [Perm.METRICS_VIEW, Perm.ALERTS_VIEW, Perm.TICKETS_EXECUTE],
            allowed_dbs=[self.mine.id])
        self.client = Client()
        login(self.client, self.user)

    @unittest.expectedFailure
    def test_blocking_graph_rejects_out_of_scope_database(self):
        r = self.client.get(f'/api/v2/databases/{self.others.id}/blocking-graph/')
        self.assertIn(r.status_code, (403, 404),
                      '越权读取了不在数据范围内的实例的阻塞拓扑')

    @unittest.expectedFailure
    def test_dryrun_rejects_out_of_scope_database(self):
        r = self.client.post(
            '/api/v2/playbooks/execute-dryrun/',
            data=json.dumps({'playbook_code': 'KILL_ROOT_BLOCKER',
                             'config_id': self.others.id}),
            content_type='application/json')
        self.assertIn(r.status_code, (403, 404),
                      '越权对不在数据范围内的实例做了自愈预演')

    @unittest.expectedFailure
    def test_execute_rejects_out_of_scope_database(self):
        r = self.client.post(
            '/api/v2/playbooks/execute-safely/',
            data=json.dumps({'playbook_code': 'KILL_ROOT_BLOCKER',
                             'config_id': self.others.id,
                             'username': 'trade_user', 'session_id': '1'}),
            content_type='application/json')
        self.assertIn(r.status_code, (403, 404),
                      '越权对不在数据范围内的实例执行了自愈剧本——这是运维动作，不只是读数据')

    @unittest.expectedFailure
    def test_warroom_rejects_out_of_scope_incident(self):
        inc = _incident(self.others, title='别人的库的故障')
        r = self.client.get(f'/api/v2/incidents/{inc.incident_id}/warroom-context/')
        self.assertIn(r.status_code, (403, 404),
                      '越权读取了不在数据范围内的事故全景（含库名/库类型/因果链）')


@tag('unit')
class PlaybookExecutionIsFakeTests(TestCase):
    """REV-02：execute_playbook 不连接任何目标库、不下发任何动作，
    却写下 status='success' 并返回"执行成功！阻塞资源已释放"。

    这不是"功能没做完"，是**在故障处置现场谎报成功**，且留下失真的审计记录。
    """

    @unittest.expectedFailure
    def test_execute_must_not_claim_success_without_doing_anything(self):
        from unittest import mock
        from monitor.playbook_engine_v2 import PlaybookExecutor
        from monitor.tests_bugfix import make_db
        cfg = make_db('fake-exec', port=3310)
        with mock.patch('monitor.db_connector.DbConnector.get_connection') as conn:
            res = PlaybookExecutor.execute_playbook(
                'KILL_ROOT_BLOCKER', cfg, 'admin',
                {'username': 'app_user', 'session_id': '1845'})
        self.assertFalse(
            res.get('status') == 'success' and conn.call_count == 0,
            '一次都没有连接目标数据库，却返回了 success —— 谎报执行成功')


@tag('unit')
class RealtimeActiveIncidentStatusTests(TestCase):
    """REV-06：v2 实时活跃故障用 status__in=['open','investigating']，
    但 'investigating' 不在 STATUS_CHOICES 里，而真正"处理中"的四个状态
    （diagnosing/plan_ready/executing/verifying）反而被漏掉。
    结果：故障一旦被人接手开始诊断，就从 WarRoom 活跃列表里消失。
    """

    @unittest.expectedFailure
    def test_in_progress_incidents_must_stay_active(self):
        valid = {c[0] for c in Incident.STATUS_CHOICES}
        self.assertNotIn('investigating', valid,
                         'investigating 本就不是合法状态值')
        cfg = _cfg('rev-db-status')
        for st in ('diagnosing', 'plan_ready', 'executing', 'verifying'):
            _incident(cfg, status=st)
        active = Incident.objects.filter(status__in=['open', 'investigating']).count()
        self.assertEqual(
            active, 4,
            '处理中的 4 条事故被 v2 的活跃过滤条件全部漏掉')
