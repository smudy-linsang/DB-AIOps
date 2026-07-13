# -*- coding: utf-8 -*-
"""
Phase 6C: 值班判定 + 升级链 (phase6/30 §7)。
"""
import logging

from django.utils import timezone

logger = logging.getLogger("monitor.oncall")


def get_current_oncall():
    """按当前时间(星期+时段)匹配 enabled 的值班记录。返回 OnCallSchedule 或 None。"""
    from monitor.models import OnCallSchedule
    now = timezone.localtime()
    weekday_bit = 1 << now.weekday()   # 周一=bit0
    hour = now.hour
    for sc in OnCallSchedule.objects.filter(enabled=True):
        if not (sc.weekday_mask & weekday_bit):
            continue
        if sc.start_hour <= hour < sc.end_hour:
            return sc
    return None


def get_escalation_target(schedule):
    """返回升级联系人对应的值班记录(按 user 匹配), 无则 None。"""
    if not schedule or not schedule.escalate_to:
        return None
    from monitor.models import OnCallSchedule
    return OnCallSchedule.objects.filter(user=schedule.escalate_to, enabled=True).first()


def notify_oncall(incident, level=0):
    """按值班表触达。level=0 当前值班人; level=1 升级联系人。返回是否发出。"""
    sc = get_current_oncall()
    if level >= 1:
        sc = get_escalation_target(sc)
    if not sc:
        logger.info("[oncall] 无匹配值班人(level=%d), 走默认通道", level)
        return False
    from monitor.incident_notify import build_incident_card, _dispatch
    card = build_incident_card(incident)
    # 定向到值班人的渠道
    channels = []
    if sc.contact_dingtalk:
        channels.append('dingtalk')
    if sc.contact_wecom:
        channels.append('wecom')
    channels = channels or ['email']
    logger.info("[oncall] 触达值班人 %s (level=%d): %s", sc.user, level, channels)
    _dispatch(card['title'] + f" @值班:{sc.user}", card['body'], channels)
    return True


def check_and_escalate(incident):
    """P1 未 ack 超时升级 (phase6/30 §7)。供调度周期调用。返回是否升级。"""
    from django.conf import settings
    if incident.priority != 'P1' or incident.acked_at:
        return False
    if not incident.plan_ready_at:
        return False
    mins = int(getattr(settings, 'ONCALL_ESCALATE_MIN', 15))
    elapsed = (timezone.now() - incident.plan_ready_at).total_seconds() / 60
    if elapsed >= mins:
        notify_oncall(incident, level=1)
        logger.warning("[oncall] P1 事故 %s 超 %d 分钟未确认, 已升级", incident.incident_id, mins)
        return True
    return False
