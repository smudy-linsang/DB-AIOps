# -*- coding: utf-8 -*-
"""
Phase 8: 后台任务 (phase8/20 §19)。

dispatch_distill_incident: transition 钩子入口, 优先 Celery, 不可用时
后台守护线程兜底 (与 start_monitor 的 USE_CELERY 降级策略一致)。
周期任务 (celery beat 注册):
  hourly : 蒸馏补偿扫描 + 向量索引补偿
  daily  : 规则校准重算 + 因果挖掘 + audit 变更同步
"""
import logging
import threading

from celery import shared_task
from django.conf import settings

logger = logging.getLogger("monitor.llm")


def _run_distill(incident_id: str):
    from django.db import close_old_connections
    close_old_connections()
    try:
        from monitor.case_distiller import distill_incident
        from monitor.models import Incident
        inc = Incident.objects.filter(incident_id=incident_id).first()
        if inc:
            distill_incident(inc)
    except Exception as e:
        logger.warning("[distill] %s 后台蒸馏失败(补偿扫描兜底): %s", incident_id, e)


def dispatch_distill_incident(incident_id: str):
    """事故 resolved/closed 时的异步蒸馏分发。绝不抛异常。"""
    if not getattr(settings, 'CASE_DISTILL_ENABLED', True):
        return
    try:
        if getattr(settings, 'MONITOR_USE_CELERY', False):
            distill_incident_task.delay(incident_id)
            return
    except Exception:
        logger.debug("[distill] %s Celery 分发失败, 回退线程兜底", incident_id, exc_info=True)
    try:
        t = threading.Thread(target=_run_distill, args=(incident_id,),
                             name=f"distill-{incident_id}", daemon=True)
        t.start()
    except Exception as e:
        logger.warning("[distill] %s 分发失败: %s", incident_id, e)


# ==========================================================================
# Celery 周期任务
# ==========================================================================
@shared_task
def distill_incident_task(incident_id: str):
    """单事故蒸馏 (Celery 通道)。"""
    _run_distill(incident_id)
    return {'incident_id': incident_id}


@shared_task
def scan_distill_task():
    """每小时: 蒸馏补偿扫描 + ES 向量索引补偿。"""
    from monitor.case_distiller import scan_and_distill
    result = scan_and_distill(hours=24)
    # 向量索引补偿: embedding_indexed=False 的案例重试投影
    indexed = 0
    try:
        from monitor.case_rag_v2 import index_case_to_es
        from monitor.models import AlertCase
        for case in AlertCase.objects.filter(embedding_indexed=False)[:50]:
            try:
                if index_case_to_es(case):
                    indexed += 1
            except Exception:
                logger.warning("[distill] 案例 %s 向量索引补偿失败",
                               getattr(case, 'case_id', '?'), exc_info=True)
    except Exception as e:
        logger.debug("[distill] 向量补偿跳过: %s", e)
    result['reindexed'] = indexed
    logger.info("[task8] 蒸馏补偿: %s", result)
    return result


@shared_task
def recompute_rule_stats_task():
    """每日: 规则置信度校准重算。"""
    from monitor.rule_calibrator import recompute_rule_stats
    return recompute_rule_stats()


@shared_task
def mine_causal_task():
    """每日: 因果边挖掘。"""
    from monitor.causal_miner import mine_all
    return mine_all()


@shared_task
def sync_audit_changes_task():
    """每日: AuditLog → ChangeEvent 变更流补录。"""
    from monitor.change_stream import sync_from_audit
    return sync_from_audit(hours=25)
