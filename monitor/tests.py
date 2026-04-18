import json
from unittest import mock

from django.test import TestCase

from monitor.alert_manager import AlertManager
from monitor.crypto import decrypt_password, encrypt_password, is_encrypted
from monitor.management.commands.start_monitor import Command
from monitor.models import AlertLog, DatabaseConfig, MonitorLog


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


class AlertManagerTests(TestCase):
    def setUp(self):
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
        notifier = mock.Mock()
        am = AlertManager(self.config, notifier)

        am.fire("down", "", "title1", "body1", severity="critical")
        am.fire("down", "", "title2", "body2", severity="critical")

        self.assertEqual(AlertLog.objects.filter(config=self.config, alert_type="down", status="active").count(), 1)
        notifier.assert_called_once_with("title1", "body1")

    def test_resolve_marks_alert_resolved_and_notifies(self):
        notifier = mock.Mock()
        am = AlertManager(self.config, notifier)
        am.fire("down", "", "故障", "连接失败", severity="critical")

        am.resolve("down", "", recovery_title="恢复", recovery_body="已恢复")

        alert = AlertLog.objects.get(config=self.config, alert_type="down")
        self.assertEqual(alert.status, "resolved")
        self.assertIsNotNone(alert.resolved_at)
        self.assertEqual(notifier.call_count, 2)
        notifier.assert_any_call("故障", "连接失败")
        notifier.assert_any_call("恢复", "已恢复")


class ProcessResultTests(TestCase):
    def setUp(self):
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
        with mock.patch.object(self.command, "send_alert") as mocked_sender:
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

        # DOWN 告警只首次触发一次，恢复时再触发一次
        self.assertEqual(mocked_sender.call_count, 2)

        down_alert = AlertLog.objects.filter(config=self.config, alert_type="down").order_by("-create_time").first()
        self.assertIsNotNone(down_alert)
        self.assertEqual(down_alert.status, "resolved")

        # 每次 process_result 都应写入日志
        self.assertEqual(MonitorLog.objects.filter(config=self.config).count(), 3)

    def test_process_result_writes_json_message(self):
        payload = {"conn_usage_pct": 1, "tablespaces": [], "locks": []}
        with mock.patch.object(self.command, "send_alert"):
            self.command.process_result(self.config, "UP", payload)

        log = MonitorLog.objects.filter(config=self.config).latest("create_time")
        stored = json.loads(log.message)
        self.assertEqual(stored["conn_usage_pct"], 1)
