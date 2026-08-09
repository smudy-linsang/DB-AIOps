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
    def setUp(self):
        # REVIEW-05: alert_manager 的聚合缓冲是模块级全局，不随测试事务回滚。
        # 上一个用例残留的 _AGG_TS 会让本用例的 fire() 直接进聚合分支，
        # 进而对早已回滚的 AlertLog 写通知日志 → FK 违例并污染事务。
        from monitor.alert_manager import reset_aggregation
        reset_aggregation()
        self.addCleanup(reset_aggregation)

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
            name=self_monitor.SYSTEM_CONFIG_NAME, db_type='mysql', host='localhost', port=0,
            username='-', password='', is_active=False)
        old = timezone.now() - timedelta(seconds=600)
        ComponentHeartbeat.objects.create(
            component='sentinel', instance='h:1', last_beat_at=old, status='up')
        with mock.patch('monitor.alert_manager.AlertManager.fire') as fire:
            res = self_monitor.run_heartbeat_check()
        self.assertEqual(res['stale'], 1)
        # 另外 3 个组件从未上报 → component_missing（REVIEW-03）
        self.assertEqual(res['missing'], 3)
        down_calls = [c for c in fire.call_args_list
                      if c.kwargs['alert_type'] == 'component_down']
        self.assertEqual(len(down_calls), 1)
        self.assertEqual(down_calls[0].kwargs['metric_key'], 'sentinel@h:1')
        hb = ComponentHeartbeat.objects.get(component='sentinel')
        self.assertEqual(hb.status, 'down')

    def test_run_heartbeat_check_recovers_and_resolves(self):
        """V11 逻辑：心跳恢复后状态回 up 并 resolve 告警。"""
        DatabaseConfig.objects.create(
            name=self_monitor.SYSTEM_CONFIG_NAME, db_type='mysql', host='localhost', port=0,
            username='-', password='', is_active=False)
        ComponentHeartbeat.objects.create(
            component='sentinel', instance='h:1',
            last_beat_at=timezone.now(), status='down')
        with mock.patch('monitor.alert_manager.AlertManager.resolve') as resolve, \
             mock.patch('monitor.alert_manager.AlertManager.fire'):
            res = self_monitor.run_heartbeat_check()
        self.assertEqual(res['stale'], 0)
        down_resolves = [c for c in resolve.call_args_list
                         if c.args and c.args[0] == 'component_down']
        self.assertEqual(len(down_resolves), 1)
        self.assertEqual(down_resolves[0].args[1], 'sentinel@h:1',
                         '解除粒度必须与告警粒度一致（REVIEW-01）')
        hb = ComponentHeartbeat.objects.get(component='sentinel')
        self.assertEqual(hb.status, 'up')

    def test_run_heartbeat_check_without_system_cfg_still_marks_down(self):
        """无 __system__ 伪实例时降级为只标记+记日志，不抛异常。"""
        old = timezone.now() - timedelta(seconds=600)
        ComponentHeartbeat.objects.create(
            component='pipeline', instance='h:1', last_beat_at=old, status='up')
        res = self_monitor.run_heartbeat_check()
        self.assertEqual(res['stale'], 1)
        hb = ComponentHeartbeat.objects.get(component='pipeline')
        self.assertEqual(hb.status, 'down')


# =============================================================================
# 复测发现的缺陷回归（REVIEW-01/02/03）
# =============================================================================
@tag('unit')
class Review01MultiInstanceAlertTests(TestCase):
    """REVIEW-01: 同一组件多副本，告警与解除必须逐实例。

    原实现 metric_key 只用 component —— AlertManager 的去重/解除粒度是
    (config, alert_type, metric_key)，于是 A、B 两副本共用一条告警：
    A 恢复就把告警 resolve 掉，而 B 还死着。
    监控系统报"全部正常"、组件其实躺平，是最不能接受的一类错误。
    """

    def setUp(self):
        self.sys_cfg = DatabaseConfig.objects.create(
            name=self_monitor.SYSTEM_CONFIG_NAME, db_type='mysql', host='localhost',
            port=0, username='-', password='', is_active=False)
        old = timezone.now() - timedelta(seconds=600)
        for inst in ('hostA:1', 'hostB:2'):
            ComponentHeartbeat.objects.create(
                component='sentinel', instance=inst, last_beat_at=old, status='up')

    def _active(self, alert_type='component_down'):
        from monitor.models import AlertLog
        return AlertLog.objects.filter(alert_type=alert_type, status='active')

    def test_metric_key_includes_instance(self):
        self.assertEqual(self_monitor.alert_metric_key('sentinel', 'hostA:1'),
                         'sentinel@hostA:1')

    def test_two_down_instances_produce_two_alerts(self):
        with mock.patch('monitor.alert_manager.AlertManager._send_to_channels'):
            self_monitor.run_heartbeat_check()
        self.assertEqual(self._active().count(), 2,
                         '两个副本失联应各自产生一条告警')

    def test_one_recovery_does_not_clear_the_other(self):
        with mock.patch('monitor.alert_manager.AlertManager._send_to_channels'):
            self_monitor.run_heartbeat_check()
            ComponentHeartbeat.objects.filter(instance='hostA:1').update(
                last_beat_at=timezone.now())
            self_monitor.run_heartbeat_check()

        self.assertEqual(
            ComponentHeartbeat.objects.get(instance='hostB:2').status, 'down')
        keys = set(self._active().values_list('metric_key', flat=True))
        self.assertEqual(keys, {'sentinel@hostB:2'},
                         'A 恢复后应只剩 B 的告警，不能把 B 的一起解除')


@tag('unit')
class Review03MissingComponentTests(TestCase):
    """REVIEW-03: 从未上报心跳的组件（部署漏启动）必须可见。

    check_stale 只能发现"活过又死了"；一条心跳都没有的组件此前完全不可见，
    /system/health 照报 ok —— 而"忘了启动哨兵"恰恰是最常见的真实故障。
    """

    def test_missing_components_detected(self):
        missing = {m['component'] for m in self_monitor.missing_components()}
        self.assertEqual(missing, set(self_monitor.COMPONENTS),
                         '四个组件都没上报，应全部识别为未启动')

    def test_reported_component_not_missing(self):
        self_monitor.report('collector')
        missing = {m['component'] for m in self_monitor.missing_components()}
        self.assertNotIn('collector', missing)

    def test_missing_fires_alert_and_resolves_after_first_beat(self):
        DatabaseConfig.objects.create(
            name=self_monitor.SYSTEM_CONFIG_NAME, db_type='mysql', host='localhost',
            port=0, username='-', password='', is_active=False)
        from monitor.models import AlertLog
        with mock.patch('monitor.alert_manager.AlertManager._send_to_channels'):
            self_monitor.run_heartbeat_check()
            self.assertEqual(
                AlertLog.objects.filter(alert_type='component_missing',
                                        status='active').count(), 4)
            self_monitor.report('sentinel')
            self_monitor.run_heartbeat_check()
        still = set(AlertLog.objects.filter(alert_type='component_missing',
                                            status='active')
                    .values_list('metric_key', flat=True))
        self.assertNotIn('sentinel', still, '上报心跳后应解除未启动告警')
        self.assertEqual(len(still), 3)
