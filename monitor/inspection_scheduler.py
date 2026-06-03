"""
巡检调度器 - Phase 5 P1-2 调度部分
=================================

三档调度:
- daily: 每天凌晨 2 点 (基础项)
- weekly: 每周日凌晨 3 点 (深度项)
- monthly: 每月 1 日凌晨 4 点 (综合项)

支持手动触发,失败重试,结果通知。

文件: monitor/inspection_scheduler.py
参考: PHASE5_DEVELOPMENT_DESIGN.md 第三部分 P1-2
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from django.utils import timezone

logger = logging.getLogger(__name__)


# ============================================================================
# Celery 任务
# ============================================================================

def schedule_daily_inspection():
    """日检任务 - 由 Celery Beat 触发"""
    return _run_scheduled("daily")


def schedule_weekly_inspection():
    """周检任务 - 由 Celery Beat 触发"""
    return _run_scheduled("weekly")


def schedule_monthly_inspection():
    """月检任务 - 由 Celery Beat 触发"""
    return _run_scheduled("monthly")


def _run_scheduled(level: str) -> Dict[str, Any]:
    """执行调度任务"""
    from monitor.inspection_executor import InspectionExecutor
    executor = InspectionExecutor()
    try:
        run_ids = executor.run_for_all_dbs(level=level)
        # 通知
        _notify_results(level, run_ids)
        return {
            "success": True,
            "level": level,
            "run_ids": run_ids,
            "ran_at": timezone.now().isoformat(),
        }
    except Exception as e:
        logger.exception("调度巡检失败: %s", e)
        return {"success": False, "error": str(e), "level": level}


def _notify_results(level: str, run_ids: List[str]):
    """通知结果(邮件/IM)"""
    from monitor.models import InspectionRun
    if not run_ids:
        return
    try:
        runs = InspectionRun.objects.filter(run_id__in=run_ids)
        critical_runs = [r for r in runs if r.critical_count > 0]
        if not critical_runs:
            logger.info("%s 巡检完成,所有数据库正常", level)
            return
        # 拼接简报
        msg = f"[{level}] 巡检完成,{len(critical_runs)} 个数据库存在严重问题:\n"
        for r in critical_runs:
            msg += f"  - {r.db_config.name}: {r.critical_count} 严重 / {r.warning_count} 警告 (健康度 {r.health_score})\n"
        logger.warning(msg)
        # 复用已有通知
        try:
            from monitor.notifications import send_alert_email
            send_alert_email(
                subject=f"[{level.upper()} INSPECTION] {len(critical_runs)} 库严重问题",
                body=msg,
            )
        except Exception as e:
            logger.debug("邮件通知失败(可忽略): %s", e)
    except Exception as e:
        logger.exception("通知环节失败: %s", e)


# ============================================================================
# 手动触发 API
# ============================================================================

def trigger_inspection_now(db_id: Optional[int] = None,
                           level: str = "daily",
                           item_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """手动触发巡检(同步执行,用于调试)"""
    from monitor.models import DatabaseConfig
    from monitor.inspection_executor import InspectionExecutor
    executor = InspectionExecutor()
    if db_id:
        db = DatabaseConfig.objects.filter(id=db_id, is_active=True).first()
        if not db:
            return {"success": False, "error": f"数据库 ID {db_id} 不存在或未启用"}
        rid = executor.run_for_db(db, level=level, item_ids=item_ids)
        return {"success": True, "run_id": rid, "db_id": db_id, "level": level}
    else:
        rids = executor.run_for_all_dbs(level=level)
        return {"success": True, "run_ids": rids, "level": level}


# ============================================================================
# 调度计划(供 Celery Beat 使用)
# ============================================================================

INSPECTION_BEAT_SCHEDULE = {
    # 日检
    "inspection-daily": {
        "task": "monitor.inspection_scheduler.schedule_daily_inspection",
        "schedule": "0 2 * * *",  # 每天凌晨 2:00
        "options": {"queue": "inspection", "expires": 3600},
    },
    # 周检
    "inspection-weekly": {
        "task": "monitor.inspection_scheduler.schedule_weekly_inspection",
        "schedule": "0 3 * * 0",  # 每周日凌晨 3:00
        "options": {"queue": "inspection", "expires": 7200},
    },
    # 月检
    "inspection-monthly": {
        "task": "monitor.inspection_scheduler.schedule_monthly_inspection",
        "schedule": "0 4 1 * *",  # 每月 1 日凌晨 4:00
        "options": {"queue": "inspection", "expires": 14400},
    },
}


# ============================================================================
# 兼容性包装(无 Celery 环境下使用)
# ============================================================================

def run_inline(level: str = "daily") -> Dict[str, Any]:
    """同步执行(无 Celery 也能跑)"""
    return _run_scheduled(level)
