# -*- coding: utf-8 -*-
"""系统自监控：组件心跳上报与失联检测。

动因：本项目能监控 6 种外部数据库，却对自身的采集器/哨兵/流水线消费者
一无所知。BUG-113 之前，哨兵线程异常退出后永久静默，监控大盘毫无异常，
直到有人发现某个库的性能数据断了几天。

设计原则：复用既有告警链路（AlertManager），不引入新的监控组件。
"""
import logging
import os
import socket

from django.utils import timezone

logger = logging.getLogger(__name__)

# 组件编码 → (显示名, 心跳间隔秒, 判定失联秒)
# 失联阈值取心跳间隔的 3 倍余量，避免单次抖动误报
COMPONENTS = {
    'collector': ('指标采集器', 60, 300),
    'sentinel': ('哨兵/ASH采样', 30, 180),
    'pipeline': ('事件流水线消费者', 60, 300),
    'notifier': ('通知发送器', 300, 1200),
}


def instance_id() -> str:
    """区分同一组件的多副本部署。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def report(component: str, meta: dict = None) -> None:
    """上报一次心跳。失败只记日志，绝不影响主流程。"""
    if component not in COMPONENTS:
        logger.warning("[self_monitor] 未知组件编码: %s", component)
        return
    try:
        from monitor.models import ComponentHeartbeat
        ComponentHeartbeat.objects.update_or_create(
            component=component, instance=instance_id(),
            defaults={'last_beat_at': timezone.now(),
                      'meta': meta or {}, 'status': 'up'},
        )
    except Exception as e:
        logger.debug("[self_monitor] 心跳上报失败 %s: %s", component, e)


def check_stale(now=None) -> list:
    """检测失联组件，返回 [{component, instance, silent_sec, display}]。

    由 HeartbeatMonitor 周期调用。已标记 down 的不重复返回（去重交给 AlertManager）。
    """
    from monitor.models import ComponentHeartbeat
    now = now or timezone.now()
    stale = []
    for hb in ComponentHeartbeat.objects.all():
        spec = COMPONENTS.get(hb.component)
        if not spec:
            continue
        display, _interval, stale_after = spec
        silent = (now - hb.last_beat_at).total_seconds()
        if silent > stale_after:
            stale.append({'component': hb.component, 'instance': hb.instance,
                          'silent_sec': int(silent), 'display': display,
                          'stale_after': stale_after})
    return stale


def run_heartbeat_check() -> int:
    """扫描失联组件并发告警。返回本轮发现的失联数。

    复用 AlertManager：去重、静默窗口、通知规则、升级策略全都现成。
    """
    from monitor.alert_manager import AlertManager
    from monitor.models import ComponentHeartbeat, DatabaseConfig

    stale = check_stale()
    # 自监控告警不绑定具体纳管实例，挂在"系统"伪实例上；
    # 无该实例时降级为只记日志，不因为缺配置而让自监控失效
    sys_cfg = DatabaseConfig.objects.filter(name='__system__').first()
    for item in stale:
        msg = (f"{item['display']}（{item['instance']}）已 {item['silent_sec']} 秒"
               f"未上报心跳，超过 {item['stale_after']} 秒阈值")
        logger.error("[self_monitor] %s", msg)
        ComponentHeartbeat.objects.filter(
            component=item['component'], instance=item['instance']).update(status='down')
        if sys_cfg:
            AlertManager(sys_cfg).fire(
                alert_type='component_down', metric_key=item['component'],
                title=f"[自监控] {item['display']} 失联", description=msg,
                severity='critical')
    # 恢复：曾 down 且现在心跳正常的，解除告警
    stale_keys = {(s['component'], s['instance']) for s in stale}
    for hb in ComponentHeartbeat.objects.filter(status='down'):
        if (hb.component, hb.instance) not in stale_keys:
            hb.status = 'up'
            hb.save(update_fields=['status'])
            if sys_cfg:
                AlertManager(sys_cfg).resolve('component_down', hb.component)
    return len(stale)
