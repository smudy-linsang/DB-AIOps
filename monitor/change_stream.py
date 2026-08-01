# -*- coding: utf-8 -*-
"""
Phase 8D: 变更事件流 (phase8/20 §16)。

三源汇聚为 ChangeEvent 统一时间线:
1. config_drift 检测器命中时自动登记 (detectors/config_drift.py 挂钩);
2. 人工登记 (api_views_phase8 POST /changes);
3. audit 提取 (AuditLog 中的运维动作, 由每日任务同步)。
dedup_key 幂等去重, 供诊断上下文/Agent recent_changes 工具消费。
"""
import hashlib
import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("monitor.llm")


def _make_dedup_key(config_id, source, title, occurred_at) -> str:
    # 同源同标题按小时桶去重
    bucket = occurred_at.strftime('%Y%m%d%H')
    raw = f"{config_id}:{source}:{title}:{bucket}"
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:40]


def record_change(config, source: str, title: str, detail: dict = None,
                  change_type: str = '', operator: str = '',
                  occurred_at=None, dedup_key: str = None):
    """登记一条变更事件 (幂等)。返回 ChangeEvent 或 None (关闭/重复/失败)。"""
    if not getattr(settings, 'CHANGE_STREAM_ENABLED', True):
        return None
    from monitor.models import ChangeEvent
    occurred_at = occurred_at or timezone.now()
    dedup_key = (dedup_key or _make_dedup_key(config.id, source, title, occurred_at))[:64]
    try:
        obj, created = ChangeEvent.objects.get_or_create(
            dedup_key=dedup_key,
            defaults={
                'config': config, 'source': source,
                'change_type': (change_type or '')[:40],
                'title': (title or '')[:200],
                'detail': detail or {},
                'operator': (operator or '')[:50],
                'occurred_at': occurred_at,
            })
        if created:
            logger.info("[change] %s [%s] %s", config.name, source, title)
        return obj if created else None
    except Exception as e:
        logger.warning("[change] 登记失败 (%s/%s): %s", source, title, e)
        return None


def query_changes(config, hours: int = 72, limit: int = 20) -> list:
    """近 N 小时变更时间线 (诊断上下文/Agent 工具共用)。"""
    from datetime import timedelta
    from monitor.models import ChangeEvent
    try:
        cutoff = timezone.now() - timedelta(hours=hours)
        rows = ChangeEvent.objects.filter(config=config, occurred_at__gte=cutoff)\
            .order_by('-occurred_at')[:limit]
        return [{'source': c.source, 'change_type': c.change_type,
                 'title': c.title, 'detail': c.detail,
                 'operator': c.operator,
                 'occurred_at': c.occurred_at.isoformat()} for c in rows]
    except Exception as e:
        logger.debug("[change] 查询失败: %s", e)
        return []


def sync_from_audit(hours: int = 24) -> dict:
    """从 AuditLog 提取运维动作补录变更流 (每日补偿任务)。"""
    if not getattr(settings, 'CHANGE_STREAM_ENABLED', True):
        return {'synced': 0}
    from datetime import timedelta
    from monitor.models import AuditLog
    # 只提取已执行成功的运维动作 (会改变数据库状态)
    cutoff = timezone.now() - timedelta(hours=hours)
    synced = 0
    try:
        rows = AuditLog.objects.filter(create_time__gte=cutoff, status='success')\
            .order_by('create_time')[:200]
        for a in rows:
            obj = record_change(
                a.config, source='audit',
                title=f"{a.get_action_type_display()}: {(a.description or '')[:150]}",
                detail={'audit_id': a.id, 'action_type': a.action_type,
                        'risk_level': a.risk_level},
                change_type=a.action_type,
                operator=a.executor or a.approver or '',
                occurred_at=a.execute_time or a.create_time,
                dedup_key=f"audit:{a.id}")
            if obj:
                synced += 1
    except Exception as e:
        logger.warning("[change] audit 同步失败: %s", e)
    return {'synced': synced}
