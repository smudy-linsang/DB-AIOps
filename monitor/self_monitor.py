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

# 承载自监控告警的伪实例名。AlertLog.config 是必填外键，而"哨兵挂了"这类
# 平台级告警并不属于任何纳管实例，故用一个 is_active=False 的占位实例承载。
# REVIEW-02: 该实例**必须**在所有面向用户的实例清单里被过滤掉 —— 统一用这个
# 常量，不要在各处写字面量，否则漏一处就会泄漏到导航树/实例列表里。
SYSTEM_CONFIG_NAME = '__system__'

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
        curr_instance = instance_id()
        # 同一台主机上若进程重启产生新 PID，清理该主机上旧 PID 的历史残留心跳
        hostname = socket.gethostname()
        ComponentHeartbeat.objects.filter(
            component=component,
            instance__startswith=f"{hostname}:"
        ).exclude(instance=curr_instance).delete()

        ComponentHeartbeat.objects.update_or_create(
            component=component, instance=curr_instance,
            defaults={'last_beat_at': timezone.now(),
                      'meta': meta or {}, 'status': 'up'},
        )
    except Exception as e:
        logger.debug("[self_monitor] 心跳上报失败 %s: %s", component, e)


def alert_metric_key(component: str, instance: str) -> str:
    """自监控告警的 metric_key。

    REVIEW-01: 必须带 instance。AlertManager 的去重/解除粒度就是
    (config, alert_type, metric_key) —— 只用 component 的话，同一组件的多个副本
    共用一条告警：A、B 同时失联只产生一条，随后 A 恢复就会把这条告警 resolve 掉，
    而 B 其实还死着。监控系统报"全部正常"而组件仍然躺平，是最不能接受的一类错误。
    """
    return f"{component}@{instance}"


def check_stale(now=None) -> list:
    """检测失联组件，返回 [{component, instance, silent_sec, display, stale_after}]。

    包含已被标记为 down 的条目 —— 告警去重交给 AlertManager，这里只负责如实反映
    "现在谁是失联的"，run_heartbeat_check 依赖这份全集来判断哪些已恢复。
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


def missing_components(now=None) -> list:
    """从未上报过心跳的组件（一条记录都没有）。

    REVIEW-03: check_stale 只能发现"曾经活过然后死了"。而部署时**漏启动**某个
    常驻进程 —— 大概率是最常见的真实故障 —— 一条心跳行都不会有，于是完全不可见。
    W4 要挡的就是这种情况，必须单独识别。
    """
    from monitor.models import ComponentHeartbeat
    seen = set(ComponentHeartbeat.objects.values_list('component', flat=True))
    return [{'component': code, 'display': spec[0]}
            for code, spec in COMPONENTS.items() if code not in seen]


def run_heartbeat_check() -> dict:
    """扫描失联/未启动组件并发告警。

    返回 {'stale': n, 'missing': m} —— 两者含义不同，不该混成一个数字：
    stale 是"活过又死了"，missing 是"根本没启动"，运维要采取的动作也不一样。

    复用 AlertManager：去重、静默窗口、通知规则、升级策略全都现成。
    """
    from monitor.alert_manager import AlertManager
    from monitor.models import ComponentHeartbeat, DatabaseConfig

    stale = check_stale()
    missing = missing_components()
    # 自监控告警不绑定具体纳管实例，挂在"系统"伪实例上；
    # 无该实例时降级为只记日志，不因为缺配置而让自监控失效
    sys_cfg = DatabaseConfig.objects.filter(name=SYSTEM_CONFIG_NAME).first()

    # ① 失联：曾经活过，现在超过阈值没心跳
    for item in stale:
        msg = (f"{item['display']}（{item['instance']}）已 {item['silent_sec']} 秒"
               f"未上报心跳，超过 {item['stale_after']} 秒阈值")
        logger.error("[self_monitor] %s", msg)
        ComponentHeartbeat.objects.filter(
            component=item['component'], instance=item['instance']).update(status='down')
        if sys_cfg:
            AlertManager(sys_cfg).fire(
                alert_type='component_down',
                metric_key=alert_metric_key(item['component'], item['instance']),
                title=f"[自监控] {item['display']} 失联", description=msg,
                severity='critical')

    # ② 从未启动：一条心跳都没有（REVIEW-03）
    for item in missing:
        msg = (f"{item['display']} 从未上报过心跳，该常驻进程可能未启动")
        logger.error("[self_monitor] %s", msg)
        if sys_cfg:
            AlertManager(sys_cfg).fire(
                alert_type='component_missing', metric_key=item['component'],
                title=f"[自监控] {item['display']} 未启动", description=msg,
                severity='critical')

    # ③ 恢复：逐实例解除（REVIEW-01：解除粒度必须与告警粒度一致）
    stale_keys = {(s['component'], s['instance']) for s in stale}
    for hb in ComponentHeartbeat.objects.filter(status='down'):
        if (hb.component, hb.instance) not in stale_keys:
            hb.status = 'up'
            hb.save(update_fields=['status'])
            if sys_cfg:
                AlertManager(sys_cfg).resolve(
                    'component_down', alert_metric_key(hb.component, hb.instance))
    # 组件从"未启动"变为有心跳后，解除 missing 告警
    missing_codes = {m['component'] for m in missing}
    for code in COMPONENTS:
        if code not in missing_codes and sys_cfg:
            AlertManager(sys_cfg).resolve('component_missing', code)

    return {'stale': len(stale), 'missing': len(missing)}
