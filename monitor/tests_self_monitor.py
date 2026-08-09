# -*- coding: utf-8 -*-
"""W4 自监控单元测试（V10/V11 逻辑覆盖，不依赖真实常驻进程）。"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, tag
from django.utils import timezone

from monitor import self_monitor
from monitor.models import ComponentHeartbeat, DatabaseConfig


@tag('unit')
class SelfMonitorHeartbeatTests(TestCase):
    def test_report_upserts_single_row(self):
        self_monitor.report('sentinel', {'config_id': 1})
        self_monitor.report('sentinel', {'config_id': 1})
        self.assertEqual(ComponentHeartbeat.objects.filter(component='sentinel').count(), 1)
        hb = ComponentHeartbeat.objects.get(component='sentinel')
        self.assertEqual(hb.status, 'up')
        self.assertEqual(hb.meta, {'config_id': 1})

    def test_report_unknown_component_is_noop(self):
        self_monitor.report('not-exist')
        self.assertEqual(ComponentHeartbeat.objects.count(), 0)

    def test_check_stale_detects_silent_component(self):
        old = timezone.now() - timedelta(seconds=600)
        ComponentHeartbeat.objects.create(
            component='sentinel', instance='h:1', last_beat_at=old, status='up')
        stale = self_monitor.check_stale()
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]['component'], 'sentinel')
        self.assertGreater(stale[0]['silent_sec'], 180)

    def test_check_stale_ignores_fresh_component(self):
        ComponentHeartbeat.objects.create(
            component='collector', instance='h:1',
            last_beat_at=timezone.now(), status='up')
        self.assertEqual(self_monitor.check_stale(), [])

    def test_run_heartbeat_check_fires_and_marks_down(self):
        """V10 逻辑：失联组件被标记 down 并触发 component_down 告警。"""
        DatabaseConfig.objects.create(
            name='__system__', db_type='mysql', host='localhost', port=0,
            username='-', password='', is_active=False)
        old = timezone.now() - timedelta(seconds=600)
        ComponentHeartbeat.objects.create(
            component='sentinel', instance='h:1', last_beat_at=old, status='up')
        with mock.patch('monitor.alert_manager.AlertManager.fire') as fire:
            n = self_monitor.run_heartbeat_check()
        self.assertEqual(n, 1)
        fire.assert_called_once()
        self.assertEqual(fire.call_args.kwargs['alert_type'], 'component_down')
        hb = ComponentHeartbeat.objects.get(component='sentinel')
        self.assertEqual(hb.status, 'down')

    def test_run_heartbeat_check_recovers_and_resolves(self):
        """V11 逻辑：心跳恢复后状态回 up 并 resolve 告警。"""
        DatabaseConfig.objects.create(
            name='__system__', db_type='mysql', host='localhost', port=0,
            username='-', password='', is_active=False)
        ComponentHeartbeat.objects.create(
            component='sentinel', instance='h:1',
            last_beat_at=timezone.now(), status='down')
        with mock.patch('monitor.alert_manager.AlertManager.resolve') as resolve:
            n = self_monitor.run_heartbeat_check()
        self.assertEqual(n, 0)
        resolve.assert_called_once()
        hb = ComponentHeartbeat.objects.get(component='sentinel')
        self.assertEqual(hb.status, 'up')

    def test_run_heartbeat_check_without_system_cfg_still_marks_down(self):
        """无 __system__ 伪实例时降级为只标记+记日志，不抛异常。"""
        old = timezone.now() - timedelta(seconds=600)
        ComponentHeartbeat.objects.create(
            component='pipeline', instance='h:1', last_beat_at=old, status='up')
        n = self_monitor.run_heartbeat_check()
        self.assertEqual(n, 1)
        hb = ComponentHeartbeat.objects.get(component='pipeline')
        self.assertEqual(hb.status, 'down')
