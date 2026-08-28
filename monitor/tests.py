import json
from contextlib import ExitStack
from unittest import mock

from django.test import Client, TestCase, tag

from monitor.alert_manager import AlertManager, reset_aggregation
from monitor.auth import Perm, RoleCode
from monitor.crypto import decrypt_password, encrypt_password, is_encrypted
from monitor.management.commands.start_monitor import Command
from monitor.models import (
    AlertLog, AlertNotificationLog, AlertSilenceWindow, DatabaseConfig,
    MonitorLog, NotificationRule,
)
from monitor.pg_capacity import postgresql_db_used_pct
from monitor.tests_bugfix import login, make_db, make_user


# BUG-008: AlertManager v3.0 改为"通知规则驱动"，fire()/resolve() 统一走
# _send_to_channels() 调用真实渠道函数（email/dingtalk/wecom），注入的 notifier
# 已弃用。因此测试需 mock 渠道函数而非 notifier。无匹配规则时默认三渠道全发。
_CHANNEL_FUNCS = (
    'monitor.notifications.send_email_alert',
    'monitor.notifications.send_dingtalk_alert',
    'monitor.notifications.send_wecom_alert',
)


def _patch_channels():
    """同时 mock 三个通知渠道，返回 multi-exit-stack 与 mock 列表。"""
    stack = ExitStack()
    mocks = [stack.enter_context(mock.patch(func, return_value=True))
             for func in _CHANNEL_FUNCS]
    return stack, mocks


def _total_channel_calls(mocks) -> int:
    return sum(m.call_count for m in mocks)


@tag('unit')
class CryptoTests(TestCase):
    def test_encrypt_decrypt_roundtrip(self):
        plaintext = "secret-pass-123"
        encrypted = encrypt_password(plaintext)

        self.assertTrue(encrypted.startswith("enc:"))
        self.assertTrue(is_encrypted(encrypted))
        self.assertEqual(decrypt_password(encrypted), plaintext)

    def test_encrypt_is_idempotent_for_ciphertext(self):
        first = encrypt_password("abc123")
        second = encrypt_password(first)
        self.assertEqual(first, second)

    def test_decrypt_plaintext_keeps_backward_compatibility(self):
        self.assertEqual(decrypt_password("plain_old_value"), "plain_old_value")


@tag('unit')
class PgCapacityTests(TestCase):
    def test_used_pct_proxy(self):
        self.assertEqual(postgresql_db_used_pct(300, 1000), 30.0)
        self.assertEqual(postgresql_db_used_pct(1000, 1000), 100.0)
        self.assertIsNone(postgresql_db_used_pct(0, 0))
        self.assertIsNone(postgresql_db_used_pct(100, 0))


@tag('unit')
class AlertManagerTests(TestCase):
    def setUp(self):
        reset_aggregation()
        self.config = DatabaseConfig.objects.create(
            name="db1",
            db_type="mysql",
            host="127.0.0.1",
            port=3306,
            username="root",
            password=encrypt_password("root123"),
            is_active=True,
        )

    def test_fire_deduplicates_active_alert(self):
        am = AlertManager(self.config)
        stack, mocks = _patch_channels()
        with stack:
            am.fire("down", "", "title1", "body1", severity="critical")
            am.fire("down", "", "title2", "body2", severity="critical")

        # 去重：仅一条活跃告警
        self.assertEqual(
            AlertLog.objects.filter(config=self.config, alert_type="down", status="active").count(),
            1,
        )
        # 首次触发推送一次（默认 3 渠道），第二次静默
        self.assertEqual(_total_channel_calls(mocks), 3)

    def test_resolve_marks_alert_resolved_and_notifies(self):
        am = AlertManager(self.config)
        stack, mocks = _patch_channels()
        with stack:
            am.fire("down", "", "故障", "连接失败", severity="critical")
            am.resolve("down", "", recovery_title="恢复", recovery_body="已恢复")

        alert = AlertLog.objects.get(config=self.config, alert_type="down")
        self.assertEqual(alert.status, "resolved")
        self.assertIsNotNone(alert.resolved_at)
        # 触发推送 + 恢复推送 = 2 轮，每轮默认 3 渠道
        self.assertEqual(_total_channel_calls(mocks), 6)


@tag('unit')
class ProcessResultTests(TestCase):
    def setUp(self):
        reset_aggregation()
        self.config = DatabaseConfig.objects.create(
            name="db-monitor-target",
            db_type="mysql",
            host="127.0.0.1",
            port=3306,
            username="root",
            password=encrypt_password("root123"),
            is_active=True,
        )
        self.command = Command()

    def test_process_result_down_then_up_resolves_alert(self):
        stack, mocks = _patch_channels()
        with stack:
            self.command.process_result(self.config, "DOWN", {"error": "connect timeout"})
            self.command.process_result(self.config, "DOWN", {"error": "connect timeout"})
            self.command.process_result(
                self.config,
                "UP",
                {
                    "conn_usage_pct": 10,
                    "active_connections": 5,
                    "max_connections": 200,
                    "tablespaces": [],
                    "locks": [],
                },
            )

        # DOWN 告警首次触发一轮通知，恢复时再触发一轮（去重：第二次 DOWN 静默）
        self.assertEqual(_total_channel_calls(mocks), 6)

        down_alert = AlertLog.objects.filter(config=self.config, alert_type="down").order_by("-create_time").first()
        self.assertIsNotNone(down_alert)
        self.assertEqual(down_alert.status, "resolved")

        # 每次 process_result 都应写入日志
        self.assertEqual(MonitorLog.objects.filter(config=self.config).count(), 3)

    def test_process_result_writes_json_message(self):
        payload = {"conn_usage_pct": 1, "tablespaces": [], "locks": []}
        stack, _ = _patch_channels()
        with stack:
            self.command.process_result(self.config, "UP", payload)

        log = MonitorLog.objects.filter(config=self.config).latest("create_time")
        stored = json.loads(log.message)
        self.assertEqual(stored["conn_usage_pct"], 1)


@tag('unit')
class AlertConsistencyTests(TestCase):
    """告警模块 PG/ES 一致性与通知健壮性测试"""

    def setUp(self):
        reset_aggregation()
        self.config = DatabaseConfig.objects.create(
            name="alert-cfg", db_type="mysql", host="127.0.0.1", port=3306,
            username="root", password=encrypt_password("root123"), is_active=True,
        )

    def test_resolve_syncs_es(self):
        """resolve 解除 PG 状态后应同步 ES, 避免列表显示陈旧 active"""
        am = AlertManager(self.config)
        with mock.patch("monitor.notifications.send_email_alert", return_value=True), \
             mock.patch("monitor.notifications.send_dingtalk_alert", return_value=True), \
             mock.patch("monitor.notifications.send_wecom_alert", return_value=True):
            am.fire("down", "", "t", "b", severity="critical")
        with mock.patch("monitor.elasticsearch_engine.sync_alert", return_value=True) as sync:
            am.resolve("down", "", recovery_title="r", recovery_body="b")
        self.assertTrue(sync.called)
        self.assertEqual(
            AlertLog.objects.filter(config=self.config, alert_type="down").first().status,
            "resolved",
        )

    def test_acknowledge_syncs_es(self):
        am = AlertManager(self.config)
        with mock.patch("monitor.notifications.send_email_alert", return_value=True), \
             mock.patch("monitor.notifications.send_dingtalk_alert", return_value=True), \
             mock.patch("monitor.notifications.send_wecom_alert", return_value=True):
            am.fire("down", "", "t", "b", severity="critical")
        alert = AlertLog.objects.filter(config=self.config).first()
        with mock.patch("monitor.elasticsearch_engine.sync_alert", return_value=True) as sync:
            self.assertTrue(am.acknowledge(alert.id, "admin"))
        self.assertTrue(sync.called)

    def test_send_alert_notification_no_crash(self):
        """severity 无 choices, send_alert_notification 不应抛 AttributeError"""
        from monitor.notifications import send_alert_notification
        alert = AlertLog.objects.create(
            config=self.config, alert_type="down", severity="critical",
            title="t", description="d", status="active",
        )
        with mock.patch("monitor.notifications.send_email_alert", return_value=True), \
             mock.patch("monitor.notifications.send_dingtalk_alert", return_value=True), \
             mock.patch("monitor.notifications.send_wecom_alert", return_value=True):
            result = send_alert_notification(alert)
        self.assertTrue(result["email"])


@tag('unit')
class AlertOpsTests(TestCase):
    """告警自动升级与聚合功能测试（接通的功能缺口）"""

    def setUp(self):
        from monitor.models import NotificationRule
        reset_aggregation()
        self.config = DatabaseConfig.objects.create(
            name="ops-cfg", db_type="mysql", host="127.0.0.1", port=3306,
            username="root", password=encrypt_password("root123"), is_active=True,
        )
        # 全局规则：1 分钟未确认自动升级（4NF: channels 创建后赋值）
        self.rule = NotificationRule.objects.create(name="escal", escalation_minutes=1)
        self.rule.channels = ["email"]

    def test_escalation_scan_upgrades_severity(self):
        from django.utils import timezone as tz
        from datetime import timedelta
        am = AlertManager(self.config)
        alert = AlertLog.objects.create(
            config=self.config, alert_type="down", severity="warning",
            title="esc", description="d", status="active",
        )
        # 制造 2 分钟前创建且 2 分钟前通知（满足升级+冷却条件）
        AlertLog.objects.filter(id=alert.id).update(
            create_time=tz.now() - timedelta(minutes=2),
            last_notified_at=tz.now() - timedelta(minutes=2),
        )
        with mock.patch("monitor.notifications.send_email_alert", return_value=True), \
             mock.patch("monitor.notifications.send_dingtalk_alert", return_value=True), \
             mock.patch("monitor.notifications.send_wecom_alert", return_value=True), \
             mock.patch("monitor.elasticsearch_engine.sync_alert", return_value=True):
            n = am.run_escalation_scan()
        self.assertEqual(n, 1)
        self.assertEqual(AlertLog.objects.get(id=alert.id).severity, "error")

    def test_aggregation_flush_sends_batch(self):
        am = AlertManager(self.config)
        alerts = [
            AlertLog.objects.create(config=self.config, alert_type="down",
                                    severity="critical", title=f"a{i}", description="d",
                                    status="active")
            for i in range(3)
        ]
        with mock.patch.object(am, "_send_to_channels", return_value={}) as send:
            for a in alerts:
                am._add_to_aggregation(a, ("down", "agg_key"))
        # 达到 MIN_COUNT=3 应触发一次聚合推送
        self.assertTrue(send.called)


@tag('unit')
class AlertViewSecurityTests(TestCase):
    """告警配置/通知规则/通知日志视图的越权与状态机缺陷回归。

    修复前：这些视图只要求登录（require_auth），无任何 require_permission，
    只读账号即可创建全局静默窗口/增删通知规则，且通知日志无数据范围过滤。
    """

    def setUp(self):
        reset_aggregation()
        self.db1 = make_db('sec-db1', port=3306)
        self.db2 = make_db('sec-db2', port=3307)
        # 只读账号：仅 alerts.view，无任何 manage 权限
        self.readonly = make_user(
            'sec-ro', RoleCode.READONLY, [Perm.ALERTS_VIEW], allowed_dbs=[self.db1.id])
        # DBA 账号：数据范围仅限 db1
        self.dba = make_user(
            'sec-dba', RoleCode.DBA,
            [Perm.ALERTS_VIEW, Perm.ALERTS_ACKNOWLEDGE], allowed_dbs=[self.db1.id])

    # ---------- 缺陷A：静默窗口越权 ----------
    def test_readonly_cannot_create_silence_window(self):
        c = Client(); login(c, self.readonly)
        r = c.post('/api/v1/silence-windows/',
                   data=json.dumps({'name': '全局静默'}), content_type='application/json')
        self.assertEqual(r.status_code, 403, '只读账号不应能创建静默窗口')
        self.assertEqual(AlertSilenceWindow.objects.count(), 0)

    def test_readonly_cannot_delete_silence_window(self):
        w = AlertSilenceWindow.objects.create(
            name='w', start_time='00:00', end_time='01:00')
        c = Client(); login(c, self.readonly)
        r = c.delete(f'/api/v1/silence-windows/{w.id}/')
        self.assertEqual(r.status_code, 403, '只读账号不应能删除静默窗口')
        self.assertTrue(AlertSilenceWindow.objects.filter(id=w.id).exists())

    # ---------- 缺陷A：通知规则越权 ----------
    def test_readonly_cannot_create_notification_rule(self):
        c = Client(); login(c, self.readonly)
        r = c.post('/api/v1/notification-rules/',
                   data=json.dumps({'name': 'rule'}), content_type='application/json')
        self.assertEqual(r.status_code, 403, '只读账号不应能创建通知规则')
        self.assertEqual(NotificationRule.objects.count(), 0)

    def test_readonly_cannot_update_notification_rule(self):
        rule = NotificationRule.objects.create(name='r1')
        c = Client(); login(c, self.readonly)
        r = c.put(f'/api/v1/notification-rules/{rule.id}/',
                  data=json.dumps({'is_enabled': False}), content_type='application/json')
        self.assertEqual(r.status_code, 403, '只读账号不应能改通知规则')
        self.assertTrue(NotificationRule.objects.get(id=rule.id).is_enabled)

    # ---------- 缺陷B：通知日志无数据范围 ----------
    def test_notification_log_respects_scope(self):
        a1 = AlertLog.objects.create(config=self.db1, alert_type='down', title='t1',
                                     description='d', status='active')
        a2 = AlertLog.objects.create(config=self.db2, alert_type='down', title='t2',
                                     description='d', status='active')
        AlertNotificationLog.objects.create(alert=a1, channel='email', status='success')
        AlertNotificationLog.objects.create(alert=a2, channel='email', status='success')
        c = Client(); login(c, self.dba)  # 仅可见 db1
        r = c.get('/api/v1/alert-notifications/')
        self.assertEqual(r.status_code, 200)
        ids = {n['alert_id'] for n in r.json()['notifications']}
        self.assertIn(a1.id, ids)
        self.assertNotIn(a2.id, ids, '通知日志泄露了数据范围外实例')

    # ---------- 缺陷C：acknowledge 状态机 ----------
    def test_cannot_acknowledge_resolved_alert(self):
        alert = AlertLog.objects.create(config=self.db1, alert_type='down', title='t',
                                        description='d', status='resolved')
        c = Client(); login(c, self.dba)
        with mock.patch('monitor.elasticsearch_engine.sync_alert', return_value=True):
            r = c.post(f'/api/v1/alerts/{alert.id}/acknowledge/')
        self.assertIn(r.status_code, (400, 409), '已恢复告警不应能被重新确认')
        self.assertEqual(AlertLog.objects.get(id=alert.id).status, 'resolved')

    def test_acknowledge_active_alert_ok_and_syncs_es(self):
        alert = AlertLog.objects.create(config=self.db1, alert_type='down', title='t',
                                        description='d', status='active')
        c = Client(); login(c, self.dba)
        with mock.patch('monitor.elasticsearch_engine.sync_alert', return_value=True) as sync:
            r = c.post(f'/api/v1/alerts/{alert.id}/acknowledge/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AlertLog.objects.get(id=alert.id).status, 'acknowledged')
        self.assertTrue(sync.called, '确认后应同步 ES 避免列表陈旧')

    # ---------- 正向：合法 manage 角色不应被误伤 ----------
    def test_config_operator_can_manage_silence_window(self):
        op = make_user('sec-op', RoleCode.CONFIG_OPERATOR,
                       [Perm.ALERT_CONFIG_VIEW, Perm.ALERT_CONFIG_MANAGE],
                       allowed_dbs=[self.db1.id])
        c = Client(); login(c, op)
        r = c.post('/api/v1/silence-windows/',
                   data=json.dumps({'name': '维护窗口', 'config_id': self.db1.id}),
                   content_type='application/json')
        self.assertEqual(r.status_code, 201, '有 manage 权限应能创建静默窗口')

    def test_config_operator_can_manage_notification_rule(self):
        op = make_user('sec-op2', RoleCode.CONFIG_OPERATOR,
                       [Perm.NOTIFICATION_VIEW, Perm.NOTIFICATION_MANAGE],
                       allowed_dbs=[self.db1.id])
        c = Client(); login(c, op)
        r = c.post('/api/v1/notification-rules/',
                   data=json.dumps({'name': 'r', 'db_config_id': self.db1.id,
                                    'channels': ['email']}),
                   content_type='application/json')
        self.assertEqual(r.status_code, 201, '有 manage 权限应能创建通知规则')

    # ---------- 数据范围：受限用户不能为范围外实例配置 ----------
    def test_scoped_user_cannot_config_other_db(self):
        op = make_user('sec-op3', RoleCode.CONFIG_OPERATOR,
                       [Perm.ALERT_CONFIG_MANAGE, Perm.NOTIFICATION_MANAGE],
                       allowed_dbs=[self.db1.id])  # 仅可见 db1
        c = Client(); login(c, op)
        r = c.post('/api/v1/silence-windows/',
                   data=json.dumps({'name': 'x', 'config_id': self.db2.id}),
                   content_type='application/json')
        self.assertEqual(r.status_code, 403, '不能为数据范围外实例创建静默窗口')
        r2 = c.post('/api/v1/notification-rules/',
                    data=json.dumps({'name': 'x', 'db_config_id': self.db2.id}),
                    content_type='application/json')
        self.assertEqual(r2.status_code, 403, '不能为数据范围外实例创建通知规则')
