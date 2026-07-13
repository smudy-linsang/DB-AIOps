# -*- coding: utf-8 -*-
"""
Phase 6B: 事故结构化通知 (phase6/20 §6)。

把通知从"标题+描述"升级为五要素卡片:
  发生了什么 / 为什么(置信度) / 影响谁 / 怎么修 / 作战室直达链接
plan_ready 时发送; P1 未 ack 15min 复提醒(由 6C 值班/升级链接管, 本模块提供发送)。
"""
import logging

from django.conf import settings

logger = logging.getLogger("monitor.incident_notify")

_CATEGORY_CN = {
    'availability': '可用性', 'lock': '锁', 'connection': '连接', 'capacity': '容量',
    'replication': '复制', 'performance': '性能', 'config': '配置', 'other': '其他',
}


def _base_url():
    return getattr(settings, 'PLATFORM_BASE_URL', 'http://localhost:3000')


def build_incident_card(incident) -> dict:
    """构造五要素通知卡片。返回 {title, body, channels}。"""
    rca = incident.rca_result or {}
    impact = incident.impact or {}
    plans = incident.plans or []
    roots = rca.get('root_causes', []) or []

    # 发生了什么
    what = incident.title
    # 为什么(置信度)
    if roots:
        r0 = roots[0]
        why = f"{r0.get('summary', r0.get('name', ''))} [{r0.get('rule_id', '')} 置信{int((r0.get('confidence', 0))*100)}%]"
    else:
        why = "诊断中"
    # 影响谁
    who = impact.get('summary', '影响评估中')
    # 怎么修
    if plans:
        fix = ' / '.join(
            f"[{p.get('scenario', '')}]{p.get('name', '')}"[:40] for p in plans[:3])
    else:
        fix = "方案生成中"
    link = f"{_base_url()}/incidents/{incident.incident_id}"

    cat = _CATEGORY_CN.get(incident.category, incident.category)
    title = f"【{incident.priority}｜{cat}】{incident.config.name}"
    body = (
        f"发生了什么: {what}\n"
        f"为什么: {why}\n"
        f"影响谁: {who}\n"
        f"怎么修: {fix}\n"
        f"处理: {link}"
    )
    # 通道: P1 全渠道, P2 即时, P3/P4 汇总(此处仍发, 汇总节流由 alert_manager 另管)
    channels = ['email', 'dingtalk', 'wecom'] if incident.priority in ('P1', 'P2') else ['email']
    return {'title': title, 'body': body, 'channels': channels,
            'at_all': incident.priority == 'P1'}


def _dispatch(title, body, channels, at_all=False):
    """按渠道发送, 复用 notifications.py。返回各渠道结果。"""
    from monitor import notifications as N
    results = {}
    for ch in channels:
        try:
            if ch == 'email':
                results['email'] = N.send_email_alert(title, body)
            elif ch == 'dingtalk':
                results['dingtalk'] = N.send_dingtalk_alert(title, body)
            elif ch == 'wecom':
                results['wecom'] = N.send_wecom_alert(title, body)
        except Exception as e:
            logger.debug("通道 %s 发送失败(未配置?): %s", ch, e)
            results[ch] = False
    return results


def notify_incident_plan_ready(incident) -> dict:
    """诊断完成/方案就位时发送五要素通知。"""
    card = build_incident_card(incident)
    logger.info("[通知] %s -> %s", card['title'], card['channels'])
    res = _dispatch(card['title'], card['body'], card['channels'], card['at_all'])
    # 记录一条通知日志(复用 AlertNotificationLog 若可用, 失败忽略)
    _log_notification(incident, card, res)
    return {'card': card, 'result': res}


def notify_incident_resolved(incident) -> dict:
    """事故解决时发送(6C 验证回路调用)。"""
    mttr = incident.t_resolve_sec
    cat = _CATEGORY_CN.get(incident.category, incident.category)
    title = f"【已解决｜{cat}】{incident.config.name}"
    body = (f"事故 {incident.incident_id} 已解决\n"
            f"标题: {incident.title}\n"
            f"MTTR: {mttr}s (达标线600s: {'达标' if incident.sla_resolve_ok else '超标'})\n"
            f"处理链接: {_base_url()}/incidents/{incident.incident_id}")
    channels = ['email', 'dingtalk'] if incident.priority in ('P1', 'P2') else ['email']
    res = _dispatch(title, body, channels)
    return {'title': title, 'result': res}


def _log_notification(incident, card, result):
    try:
        from monitor.models import AlertNotificationLog
        AlertNotificationLog.objects.create(
            config=incident.config,
            channel=','.join(card['channels']),
            status='sent' if any(result.values()) else 'failed',
            title=card['title'][:200],
            content=card['body'][:1000],
        )
    except Exception:
        pass  # 通知日志模型字段不匹配时静默(不阻断)
