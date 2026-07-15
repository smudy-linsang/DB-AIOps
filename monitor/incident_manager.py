# -*- coding: utf-8 -*-
"""
Phase 6A: 事故生成/聚合核心 (phase6/10 §4)。

ingest_event(evt): 落 Event → 聚合/新建 Incident → 触发诊断入队。全流程幂等。
"""
import datetime
import logging

from django.conf import settings
from django.utils import timezone

from monitor.models import DatabaseConfig, Event, Incident, Problem, _uid
from monitor.detectors.metric_category import category_for_signal

logger = logging.getLogger(__name__)

# 优先级定级矩阵 (phase6/10 §4.3)
_URGENCY_HIGH = {'instance_down', 'blocked_session', 'conn_storm', 'repl_broken'}
_URGENCY_MID = {'conn_high', 'space_high', 'deadlock_surge', 'repl_lag', 'slow_surge'}
# 其余(config_drift, baseline_deviation) = 低

_PRIORITY_MATRIX = {
    # (影响面, 紧急度) -> P
    ('high', 'high'): 'P1', ('high', 'mid'): 'P1', ('high', 'low'): 'P2',
    ('mid', 'high'): 'P1', ('mid', 'mid'): 'P2', ('mid', 'low'): 'P3',
    ('low', 'high'): 'P2', ('low', 'mid'): 'P3', ('low', 'low'): 'P4',
}

_AGG_WINDOW_MIN = 5


def _impact_scope(config) -> str:
    """由关联业务系统最高重要度决定影响面。"""
    try:
        imps = list(config.business_systems.values_list('importance', flat=True))
    except Exception:
        imps = []
    if any(i in ('critical', '核心') for i in imps):
        return 'high'
    if any(i == 'important' for i in imps):
        return 'mid'
    return 'low'


def _urgency(signal: str) -> str:
    if signal in _URGENCY_HIGH:
        return 'high'
    if signal in _URGENCY_MID:
        return 'mid'
    return 'low'


def compute_priority(config, signal: str) -> str:
    return _PRIORITY_MATRIX.get((_impact_scope(config), _urgency(signal)), 'P3')


def _latest_health(config) -> float:
    try:
        from monitor.models import HealthScore
        hs = HealthScore.objects.filter(config=config).order_by('-score_date').first()
        return float(hs.total_score) if hs else 0.0
    except Exception:
        return 0.0


def _incident_title(category: str, signal: str, evt: dict) -> str:
    d = evt.get('detail') or {}
    titles = {
        'instance_down': '实例不可连接',
        'blocked_session': f"锁等待阻塞: {d.get('waiters', evt.get('value'))} 个会话被阻塞",
        'conn_high': f"连接使用率高: {evt.get('value')}%",
        'conn_storm': '连接风暴',
        'space_high': f"空间将满: {d.get('object', '')} {evt.get('value')}%".strip(),
        'repl_broken': '复制中断',
        'repl_lag': f"复制延迟: {evt.get('value')}s",
        'slow_surge': '慢查询突增',
        'deadlock_surge': '死锁频发',
        'config_drift': '配置漂移',
        'plan_change': f"执行计划突变: {str(d.get('sql_digest', ''))[:16]}",
        'long_transaction': f"长事务: 会话 {d.get('session_id', '')} 持续 {d.get('duration_sec', '')}s",
        'baseline_deviation': f"指标偏离基线: {evt.get('metric_key')}",
    }
    return titles.get(signal, f"{category} 异常: {signal}")


def _storm_check(config_id: int) -> bool:
    """1min 窗内该 config 事件计数超阈返回 True。"""
    try:
        from monitor.redis_bus import get_bus
        bus = get_bus()
        key = f"dbaiops:stormcnt:{config_id}"
        cnt = bus.incr(key)
        if cnt == 1:
            bus.expire(key, 60)
        return cnt > int(getattr(settings, 'INCIDENT_STORM_THRESHOLD', 10))
    except Exception:
        return False


def _flapping_check(dedup_key: str) -> bool:
    """flapping: 窗口内 fire 次数达阈。用 Redis 计数近似。"""
    try:
        from monitor.redis_bus import get_bus
        bus = get_bus()
        win = int(getattr(settings, 'INCIDENT_FLAPPING_WINDOW_MIN', 10))
        key = f"dbaiops:flap:{dedup_key}"
        cnt = bus.incr(key)
        if cnt == 1:
            bus.expire(key, win * 60)
        return cnt >= int(getattr(settings, 'INCIDENT_FLAPPING_COUNT', 3))
    except Exception:
        return False


def _update_problem(config, category: str, signal: str, incident):
    """更新/创建 Problem 签名与计数。"""
    try:
        sig = f"{config.db_type}:{category}:{signal}"
        now = timezone.now()
        prob, created = Problem.objects.get_or_create(
            signature=sig,
            defaults={'problem_id': _uid('PRB'), 'title': f"{category}/{signal}",
                      'first_seen_at': now, 'last_seen_at': now, 'incident_count': 0})
        prob.incident_count = (prob.incident_count or 0) + 1
        prob.last_seen_at = now
        prob.save(update_fields=['incident_count', 'last_seen_at'])
        incident.problem = prob
        incident.save(update_fields=['problem'])
    except Exception as e:
        logger.warning("Problem 更新失败: %s", e)


def ingest_event(evt: dict) -> Incident:
    """检测消费组调用。返回归属 Incident (幂等)。"""
    event_uid = evt.get('event_uid')
    # 1. 幂等
    if event_uid:
        existing = Event.objects.filter(event_uid=event_uid).select_related('incident').first()
        if existing and existing.incident_id:
            return existing.incident

    config = DatabaseConfig.objects.filter(id=evt.get('config_id')).first()
    if not config:
        logger.warning("ingest_event: config %s 不存在", evt.get('config_id'))
        return None

    signal = evt.get('signal', '')
    category = evt.get('category') or category_for_signal(signal, evt.get('metric_key', ''))
    occurred_at = _parse_dt(evt.get('occurred_at')) or timezone.now()
    dedup_key = evt.get('dedup_key') or f"{config.id}:{signal}"

    # 2. 落 Event 行
    event = Event.objects.create(
        event_uid=event_uid or _uid('EVT'),
        config=config, db_type=config.db_type, source=evt.get('source', 'collector'),
        signal=signal, metric_key=evt.get('metric_key', ''),
        value=float(evt.get('value') or 0), threshold=float(evt.get('threshold') or 0),
        severity=evt.get('severity', 'warning'), dedup_key=dedup_key,
        occurred_at=occurred_at, detail=evt.get('detail') or {})

    # 3. 风暴检测
    is_storm = _storm_check(config.id)

    # 4. 聚合: 5min 窗内同 (config, category) 的 open/diagnosing/plan_ready 事故
    win_start = timezone.now() - datetime.timedelta(minutes=_AGG_WINDOW_MIN)
    inc_dedup = f"{config.id}:{category}"
    incident = (Incident.objects
                .filter(config=config, dedup_key=inc_dedup,
                        status__in=['open', 'diagnosing', 'plan_ready', 'executing', 'verifying'],
                        created_at__gte=win_start)
                .order_by('-created_at').first())

    created_new = False
    if incident:
        # 并入
        incident.event_count = (incident.event_count or 0) + 1
        if is_storm:
            incident.is_storm = True
        incident.save(update_fields=['event_count', 'is_storm', 'updated_at'])
        event.incident = incident
        event.save(update_fields=['incident'])
    else:
        priority = compute_priority(config, signal)
        title = ('【事件风暴】' if is_storm else '') + _incident_title(category, signal, evt)
        incident = Incident.objects.create(
            incident_id=f"INC-{timezone.now().strftime('%Y%m%d%H%M%S')}-{config.id}",
            config=config, db_type=config.db_type, category=category, title=title,
            priority=priority, status='open', dedup_key=inc_dedup, event_count=1,
            is_storm=is_storm, occurred_at=occurred_at, detected_at=timezone.now(),
            health_snapshot=_latest_health(config))
        event.incident = incident
        event.save(update_fields=['incident'])
        created_new = True

    # 5. 抖动
    if _flapping_check(dedup_key):
        if not incident.is_flapping:
            incident.is_flapping = True
            # 抖动升级一级
            incident.priority = _escalate(incident.priority)
            incident.save(update_fields=['is_flapping', 'priority'])

    # 6. 新事故 → 触发诊断入队
    if created_new:
        _update_problem(config, category, signal, incident)
        try:
            incident.transition('diagnosing', 'system', '自动诊断入队')
            from monitor.redis_bus import emit_diagnosis
            emit_diagnosis(incident.incident_id, config.id, 'created')
        except Exception as e:
            logger.warning("诊断入队失败: %s", e)

    return incident


def _escalate(priority: str) -> str:
    order = ['P4', 'P3', 'P2', 'P1']
    try:
        i = order.index(priority)
        return order[min(i + 1, len(order) - 1)]
    except ValueError:
        return priority


def _parse_dt(s):
    if not s:
        return None
    if isinstance(s, datetime.datetime):
        return s
    try:
        from django.utils.dateparse import parse_datetime
        return parse_datetime(s)
    except Exception:
        return None
