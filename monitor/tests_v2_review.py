# -*- coding: utf-8 -*-
"""v2.0 独立复审缺陷转正后的常规回归集。

本文件最初用于复现 ``V2.0_INDEPENDENT_REVIEW.md`` 中的问题；v2.5 已移除全部
``expectedFailure``，现在这些用例是权限、真实数据、自愈安全、租约和供应链整改的
长期回归防线。新增缺陷不得以 expected failure 方式换取绿灯。
"""
import json
from io import StringIO
from django.test import Client, TestCase, tag
from django.test import override_settings
from django.core.management import call_command
from django.utils import timezone

from monitor.auth import Perm, RoleCode
from monitor.crypto import encrypt_password
from monitor.models import DatabaseConfig, Incident, Playbook, PlaybookRun
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

    def test_database_context_survives_incident(self):
        from monitor.copilot import _get_database_context
        cfg = _cfg()
        _incident(cfg)
        ctx = _get_database_context(cfg)
        self.assertIn('recent_incidents', ctx)

    def test_quick_assessment_survives_incident(self):
        from monitor.copilot import generate_quick_health_assessment
        cfg = _cfg('rev-db-2')
        _incident(cfg)
        res = generate_quick_health_assessment(cfg.id)
        self.assertIn('overall_score', res)


@tag('unit')
class QuickAssessmentFabricatedMetricsTests(TestCase):
    """REV-07：无任何采集数据时，体检用编造的默认值（cpu=35 / conn=20 / disk=45）
    给出"性能与负载 100 分""容量规划 100 分"。对零数据实例输出满分是虚假保证。
    """

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

    def test_blocking_graph_rejects_out_of_scope_database(self):
        r = self.client.get(f'/api/v2/databases/{self.others.id}/blocking-graph/')
        self.assertIn(r.status_code, (403, 404),
                      '越权读取了不在数据范围内的实例的阻塞拓扑')

    def test_dryrun_rejects_out_of_scope_database(self):
        r = self.client.post(
            '/api/v2/playbooks/execute-dryrun/',
            data=json.dumps({'playbook_code': 'KILL_ROOT_BLOCKER',
                             'config_id': self.others.id}),
            content_type='application/json')
        self.assertIn(r.status_code, (403, 404),
                      '越权对不在数据范围内的实例做了自愈预演')

    def test_execute_rejects_out_of_scope_database(self):
        r = self.client.post(
            '/api/v2/playbooks/execute-safely/',
            data=json.dumps({'playbook_code': 'KILL_ROOT_BLOCKER',
                             'config_id': self.others.id,
                             'username': 'trade_user', 'session_id': '1'}),
            content_type='application/json')
        self.assertIn(r.status_code, (403, 404),
                      '越权对不在数据范围内的实例执行了自愈剧本——这是运维动作，不只是读数据')

    def test_warroom_rejects_out_of_scope_incident(self):
        inc = _incident(self.others, title='别人的库的故障')
        r = self.client.get(f'/api/v2/incidents/{inc.incident_id}/warroom-context/')
        self.assertIn(r.status_code, (403, 404),
                      '越权读取了不在数据范围内的事故全景（含库名/库类型/因果链）')


@tag('unit')
class IncidentApiDataScopeTests(TestCase):
    """v1 事故处置的所有对象入口都必须复用同一数据范围。"""

    def setUp(self):
        from monitor.tests_bugfix import make_db, make_user
        self.mine = make_db('v1-scope-mine', port=3320)
        self.others = make_db('v1-scope-others', port=3321)
        self.user = make_user(
            'v1scoped', RoleCode.DBA,
            [Perm.ALERTS_VIEW, Perm.ALERTS_ACKNOWLEDGE,
             Perm.TICKETS_EXECUTE, Perm.TICKETS_APPROVE],
            allowed_dbs=[self.mine.id])
        self.client = Client()
        login(self.client, self.user)
        self.incident = _incident(self.others, title='越权处置目标')
        self.playbook = Playbook.objects.create(
            playbook_id='PB-SCOPE-TEST', name='范围测试', category='lock',
            signal='blocked_session', risk_level='high')
        self.run = PlaybookRun.objects.create(
            run_id='PBR-SCOPE-TEST', playbook=self.playbook,
            incident=self.incident, trigger_mode='approved')

    def test_incident_read_and_mutations_reject_out_of_scope(self):
        iid = self.incident.incident_id
        calls = [
            ('get', f'/api/v1/incidents/{iid}/', None),
            ('get', f'/api/v1/incidents/{iid}/timeline/', None),
            ('post', f'/api/v1/incidents/{iid}/ack/', {}),
            ('post', f'/api/v1/incidents/{iid}/close/', {'reason': 'x'}),
            ('post', f'/api/v1/incidents/{iid}/rediagnose/', {}),
            ('post', f'/api/v1/incidents/{iid}/execute/', {'playbook_id': self.playbook.playbook_id}),
        ]
        for method, path, data in calls:
            with self.subTest(path=path):
                response = getattr(self.client, method)(
                    path, data=json.dumps(data) if data is not None else None,
                    content_type='application/json')
                self.assertEqual(response.status_code, 404)

    def test_run_read_and_mutations_reject_out_of_scope(self):
        base = f'/api/v1/playbook-runs/{self.run.run_id}'
        for method, path in (
                ('get', f'{base}/'),
                ('post', f'{base}/approve/'),
                ('post', f'{base}/rollback/')):
            with self.subTest(path=path):
                if method == 'get':
                    response = self.client.get(path)
                else:
                    response = self.client.post(
                        path, data='{}', content_type='application/json')
                self.assertEqual(response.status_code, 404)


@tag('unit')
class PlaybookExecutionIsFakeTests(TestCase):
    """REV-02：execute_playbook 不连接任何目标库、不下发任何动作，
    却写下 status='success' 并返回"执行成功！阻塞资源已释放"。

    这不是"功能没做完"，是**在故障处置现场谎报成功**，且留下失真的审计记录。
    """

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

    def test_in_progress_incidents_must_stay_active(self):
        valid = {c[0] for c in Incident.STATUS_CHOICES}
        self.assertNotIn('investigating', valid,
                         'investigating 本就不是合法状态值')
        cfg = _cfg('rev-db-status')
        for st in ('diagnosing', 'plan_ready', 'executing', 'verifying'):
            _incident(cfg, status=st)
        from monitor.api_views_v2 import ACTIVE_INCIDENT_STATUSES
        active = Incident.objects.filter(status__in=ACTIVE_INCIDENT_STATUSES).count()
        self.assertEqual(active, 4, '处理中的 4 条事故必须全部保持活跃')


@tag('unit')
@override_settings(LLM_ENABLED=False, EMBED_ENABLED=False)
class CaseDistillerFourNFTests(TestCase):
    """案例 tags 是 4NF 子表，不能塞进 update_or_create defaults。"""

    def test_distill_persists_parent_once_then_tags(self):
        from monitor.case_distiller import distill_incident
        from monitor.models import AlertCase

        cfg = _cfg('distill-four-nf')
        inc = _incident(cfg, status='resolved', title='蒸馏 4NF 回归')
        inc.rca_result = {'root_causes': [{
            'summary': '根因', 'suggestions': ['处置'], 'domain': 'lock'}]}
        inc.save(update_fields=['rca_result'])
        case_id = distill_incident(inc)
        case = AlertCase.objects.get(case_id=case_id)
        self.assertEqual(case.tags, ['mysql', 'lock'])
        self.assertIsNone(distill_incident(inc))


@tag('unit')
class PlaybookBootstrapTests(TestCase):
    def test_bootstrap_is_idempotent_and_preserves_dba_customization(self):
        from monitor.models import Playbook

        call_command('init_playbooks', stdout=StringIO())
        pb = Playbook.objects.get(playbook_id='PB-LOCK-KILL-BLOCKER')
        self.assertTrue(pb.applicable_db_types)
        self.assertTrue(pb.steps)
        pb.name = 'DBA 定制名称'
        pb.save(update_fields=['name'])

        call_command('init_playbooks', stdout=StringIO())
        pb.refresh_from_db()
        self.assertEqual(pb.name, 'DBA 定制名称')


@tag('unit')
class WebhookSecurityTests(TestCase):
    def test_webhook_rejects_ssrf_and_plain_http(self):
        from monitor.notifications import _validated_webhook_url

        bad_urls = (
            'http://oapi.dingtalk.com/robot/send?access_token=x',
            'https://127.0.0.1/latest/meta-data/',
            'https://oapi.dingtalk.com.evil.example/robot/send',
            'https://user:pass@oapi.dingtalk.com/robot/send',
            'https://oapi.dingtalk.com:8443/robot/send',
        )
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                _validated_webhook_url(url, 'dingtalk')

    @override_settings(
        WEBHOOK_ALLOWED_HOSTS=('oapi.dingtalk.com', 'qyapi.weixin.qq.com'))
    def test_webhook_accepts_only_matching_official_channel(self):
        from monitor.notifications import _validated_webhook_url

        ding = 'https://oapi.dingtalk.com/robot/send?access_token=secret'
        wecom = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret'
        self.assertEqual(_validated_webhook_url(ding, 'dingtalk'), ding)
        self.assertEqual(_validated_webhook_url(wecom, 'wecom'), wecom)
        with self.assertRaises(ValueError):
            _validated_webhook_url(wecom, 'dingtalk')

    @override_settings(
        DINGTALK_WEBHOOK='https://oapi.dingtalk.com/robot/send?access_token=test-only',
        DINGTALK_SECRET='test-only-signing-secret',
        WEBHOOK_ALLOWED_HOSTS=('oapi.dingtalk.com', 'qyapi.weixin.qq.com'))
    def test_dingtalk_success_path_uses_bounded_https_request(self):
        from unittest import mock
        from monitor.notifications import send_dingtalk_alert

        with mock.patch('monitor.notifications._post_webhook',
                        return_value={'errcode': 0}) as post:
            self.assertTrue(send_dingtalk_alert('title', 'body'))
        self.assertTrue(post.call_args.args[0].startswith('https://oapi.dingtalk.com/'))
        self.assertEqual(post.call_args.args[1]['msgtype'], 'markdown')

    def test_webhook_transport_rejects_redirect_and_oversized_response(self):
        from unittest import mock
        from monitor.notifications import _post_webhook

        redirect = mock.MagicMock(status_code=302, headers={'Location': 'http://127.0.0.1/'})
        redirect.__enter__.return_value = redirect
        with mock.patch('monitor.notifications.requests.post', return_value=redirect) as post, \
                self.assertRaisesRegex(ValueError, 'redirect'):
            _post_webhook('https://oapi.dingtalk.com/robot/send', {'x': 1})
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertEqual(post.call_args.kwargs['timeout'], (3, 5))

        oversized = mock.MagicMock(status_code=200, headers={'Content-Length': '65537'})
        oversized.__enter__.return_value = oversized
        with mock.patch('monitor.notifications.requests.post', return_value=oversized), \
                self.assertRaisesRegex(ValueError, 'too large'):
            _post_webhook('https://oapi.dingtalk.com/robot/send', {'x': 1})

    def test_webhook_transport_parses_bounded_json(self):
        from unittest import mock
        from monitor.notifications import _post_webhook

        response = mock.MagicMock(status_code=200, headers={})
        response.__enter__.return_value = response
        response.iter_content.return_value = [b'{"err', b'code": 0}']
        with mock.patch('monitor.notifications.requests.post', return_value=response):
            self.assertEqual(
                _post_webhook('https://oapi.dingtalk.com/robot/send', {'x': 1}),
                {'errcode': 0})
        response.raise_for_status.assert_called_once_with()

    @override_settings(
        WECOM_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only',
        WEBHOOK_ALLOWED_HOSTS=('oapi.dingtalk.com', 'qyapi.weixin.qq.com'))
    def test_wecom_failure_is_recorded_without_leaking_url(self):
        from unittest import mock
        from monitor.notifications import send_wecom_alert

        with mock.patch('monitor.notifications._post_webhook',
                        side_effect=TimeoutError('token-bearing-url')), \
                mock.patch('monitor.degrade.note') as note, \
                self.assertLogs('monitor.notifications', level='WARNING') as logs:
            self.assertFalse(send_wecom_alert('title', 'body', '13800000000'))
        note.assert_called_once_with('notify.wecom', reason='TimeoutError')
        self.assertNotIn('token-bearing-url', '\n'.join(logs.output))

    @override_settings(ADMIN_EMAILS=['dba@example.bank'], DEFAULT_FROM_EMAIL='monitor@example.bank')
    def test_email_failure_records_degradation(self):
        from unittest import mock
        from monitor.notifications import send_email_alert

        failure = RuntimeError('smtp down')
        with mock.patch('monitor.notifications.send_mail', side_effect=failure), \
                mock.patch('monitor.degrade.note') as note:
            self.assertFalse(send_email_alert('title', 'body'))
        note.assert_called_once_with('notify.email', exc=failure)

    def test_alert_aggregator_flushes_all_channels(self):
        from types import SimpleNamespace
        from unittest import mock
        from monitor.notifications import AlertAggregator, send_alert_notification

        alert = SimpleNamespace(
            alert_type='lock', metric_key='blocked_sessions', severity='critical',
            title='blocking', description='one blocker',
            config=SimpleNamespace(name='trade-db'))
        aggregator = AlertAggregator(window_seconds=0)
        with mock.patch('monitor.notifications.send_email_alert') as email, \
                mock.patch('monitor.notifications.send_dingtalk_alert') as ding, \
                mock.patch('monitor.notifications.send_wecom_alert') as wecom:
            self.assertTrue(aggregator.add_alert(alert))
            email.assert_called_once()
            ding.assert_called_once()
            wecom.assert_called_once()

        with mock.patch('monitor.notifications.send_email_alert', return_value=True), \
                mock.patch('monitor.notifications.send_dingtalk_alert', return_value=False), \
                mock.patch('monitor.notifications.send_wecom_alert', return_value=True):
            self.assertEqual(send_alert_notification(alert), {
                'email': True, 'dingtalk': False, 'wecom': True})


@tag('unit')
class PlaybookParameterSecurityTests(TestCase):
    def test_sql_control_parameters_are_typed_and_bounded(self):
        from monitor.playbook_engine import _normalize_params

        schema = {
            'session_id': {'required': True, 'type': 'session'},
            'idle_sec': {'required': True, 'type': 'integer', 'min': 60, 'max': 3600},
            'tablespace': {'required': True, 'type': 'identifier'},
        }
        valid = _normalize_params(
            schema,
            {'session_id': '42', 'idle_sec': '600', 'tablespace': 'USERS',
             'untrusted_extra': 'DROP DATABASE prod'},
            'mysql')
        self.assertEqual(valid, {
            'session_id': '42', 'idle_sec': '600', 'tablespace': 'USERS'})

        for key, value in (
                ('session_id', '42; DROP TABLE audit_log'),
                ('idle_sec', '0 OR 1=1'),
                ('tablespace', 'USERS; DROP TABLESPACE SYSTEM')):
            supplied = {'session_id': '42', 'idle_sec': '600', 'tablespace': 'USERS'}
            supplied[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                _normalize_params(schema, supplied, 'mysql')

    def test_literals_are_quoted_and_oracle_session_is_split_for_precheck(self):
        from monitor.playbook_engine import _normalize_params, _pick_sql

        literal = _normalize_params(
            {'old_value': {'required': True, 'type': 'literal'}},
            {'old_value': "safe' OR '1'='1"}, 'mysql')
        self.assertEqual(literal['old_value'], "'safe'' OR ''1''=''1'")

        params = _normalize_params(
            {'blocker_id': {'required': True, 'type': 'session'}},
            {'blocker_id': '12,34'}, 'oracle')
        sql = _pick_sql({
            'action': 'query',
            'sql_by_db': {'oracle': 'SELECT 1 FROM v$session WHERE sid={blocker_id}'},
        }, 'oracle', params)
        self.assertIn('sid=12', sql)
        self.assertEqual(params['blocker_id'], '12,34')

    @override_settings(INCIDENT_P1_EXECUTE_FIRST=False)
    def test_mid_risk_requires_approval_by_default(self):
        from types import SimpleNamespace
        from unittest import mock
        from monitor.playbook_authz import decide_trigger

        incident = SimpleNamespace(config_id=1, priority='P1')
        playbook = SimpleNamespace(risk_level='mid', auto_execute=False)
        with mock.patch('monitor.playbook_authz._circuit_broken', return_value=False), \
                mock.patch('monitor.autonomy_policy.gate', return_value=None):
            decision = decide_trigger(incident, playbook)
        self.assertFalse(decision['execute_now'])
        self.assertTrue(decision['need_approval'])

    def test_kill_guard_reads_live_owner_and_rejects_system_accounts(self):
        from unittest import mock
        from monitor.playbook_engine import _assert_safe_session_target

        cursor = mock.MagicMock()
        cursor.fetchone.return_value = {'user': 'root'}
        conn = mock.MagicMock()
        conn.cursor.return_value = cursor
        with self.assertRaisesRegex(ValueError, '受保护账号'):
            _assert_safe_session_target(conn, 'mysql', '42')

        cursor.fetchone.return_value = {'user': 'trade_app'}
        self.assertEqual(
            _assert_safe_session_target(conn, 'mysql', '42'), 'trade_app')
        cursor.execute.assert_called_with(
            'SELECT user FROM information_schema.processlist WHERE id=42')


@tag('unit')
@override_settings(PROCESS_LEASE_TTL_SEC=30, PROCESS_LEASE_RENEW_SEC=10)
class ProcessLeaseTests(TestCase):
    def test_single_owner_and_monotonic_fencing_token(self):
        from monitor.process_lease import ProcessLeaseGuard

        first = ProcessLeaseGuard('collector', owner_id='node-a:1')
        second = ProcessLeaseGuard('collector', owner_id='node-b:2')
        self.assertTrue(first.acquire())
        self.assertEqual(first.fencing_token, 1)
        self.assertFalse(second.acquire())

        first.release()
        self.assertTrue(second.acquire())
        self.assertEqual(second.fencing_token, 2)
        self.assertFalse(first.renew(), '旧 fencing token 不得续租新 leader 的租约')
        second.release()

    def test_expired_lease_can_be_taken_over(self):
        from datetime import timedelta
        from monitor.models import ProcessLease
        from monitor.process_lease import ProcessLeaseGuard

        ProcessLease.objects.create(
            role='sentinel', shard_key='global', owner_id='dead-node',
            fencing_token=7, expires_at=timezone.now() - timedelta(seconds=1))
        leader = ProcessLeaseGuard('sentinel', owner_id='replacement')
        self.assertTrue(leader.acquire())
        self.assertEqual(leader.fencing_token, 8)
        leader.release()

    def test_renew_loss_callback_is_idempotent_and_stale_owner_cannot_release(self):
        from unittest import mock
        from monitor.models import ProcessLease
        from monitor.process_lease import LeaseLost, ProcessLeaseGuard

        callback = mock.Mock()
        leader = ProcessLeaseGuard('pipeline', owner_id='node-a', on_lost=callback)
        self.assertTrue(leader.acquire())
        self.assertTrue(leader.renew())

        ProcessLease.objects.filter(pk=leader._lease_pk).update(
            owner_id='node-b', fencing_token=leader.fencing_token + 1)
        self.assertFalse(leader.renew())
        leader._mark_lost('test token changed')
        leader._mark_lost('duplicate signal')
        callback.assert_called_once_with()
        with self.assertRaises(LeaseLost):
            leader.assert_leader()
        leader.release()
        self.assertEqual(
            ProcessLease.objects.get(pk=leader._lease_pk).owner_id, 'node-b',
            '失租的旧进程不得清空新 leader 的租约')

    def test_start_rejects_second_owner_and_context_releases_first(self):
        from unittest import mock
        from monitor.process_lease import LeaseUnavailable, ProcessLeaseGuard

        first = ProcessLeaseGuard('collector', owner_id='node-a')
        with mock.patch('monitor.process_lease.threading.Thread') as thread_type:
            thread = thread_type.return_value
            self.assertIs(first.start(), first)
            thread.start.assert_called_once_with()
            second = ProcessLeaseGuard('collector', owner_id='node-b')
            with self.assertRaises(LeaseUnavailable):
                second.start()
            first.release()
            thread.join.assert_called_once()

    @override_settings(PROCESS_LEASE_TTL_SEC=10, PROCESS_LEASE_RENEW_SEC=10)
    def test_renew_interval_must_be_shorter_than_ttl(self):
        from monitor.process_lease import ProcessLeaseGuard

        with self.assertRaisesRegex(ValueError, '必须小于'):
            ProcessLeaseGuard('sentinel')
