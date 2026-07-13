"""
告警上下文聚合器 v1.0 (Phase 5 - RCA 2.0)

功能:
- 告警触发时自动拉取相关上下文
- 整合多源数据(指标历史/相关告警/集群状态/业务图谱/近期变更)
- 为 RCA 引擎提供增强的诊断数据

设计文档参考: PHASE5_DEVELOPMENT_DESIGN.md 第二部分 P0-1
"""
import json
import logging
from datetime import timedelta
from typing import Dict, List, Any, Optional

from django.utils import timezone
from monitor.models import (
    AlertLog, AuditLog, DatabaseConfig, DatabaseTopology,
    BusinessSystem, MonitorLog
)

logger = logging.getLogger(__name__)


# 默认上下文窗口
DEFAULT_TIME_WINDOWS = {
    'short': 5,    # 5 分钟
    'medium': 30,  # 30 分钟
    'long': 60,    # 1 小时
}


class ContextAggregator:
    """
    告警上下文聚合器

    用法:
        aggregator = ContextAggregator(alert)
        context = aggregator.aggregate()
    """

    def __init__(self, alert: AlertLog, time_window_min: int = 30):
        self.alert = alert
        self.config: DatabaseConfig = alert.config
        self.db_type = self.config.db_type
        self.time_window_min = time_window_min

    def aggregate(self) -> Dict[str, Any]:
        """
        聚合告警相关上下文

        Returns:
            {
                'alert': 告警详情,
                'related_metrics': {指标: [(ts, value), ...]},
                'related_alerts': [同期告警列表],
                'cluster_context': {集群兄弟节点状态},
                'business_context': {业务系统上下文},
                'recent_changes': [近期变更],
                'topology': {拓扑信息},
                'baseline': {基线对比},
            }
        """
        logger.info(f"[ContextAggregator] 开始聚合告警上下文: {self.alert.title}")
        result = {
            'alert': self._alert_summary(),
            'related_metrics': self._get_related_metrics(),
            'related_alerts': self._get_related_alerts(),
            'cluster_context': self._get_cluster_context(),
            'business_context': self._get_business_context(),
            'recent_changes': self._get_recent_changes(),
            'topology': self._get_topology_info(),
            'baseline_comparison': self._get_baseline_comparison(),
        }
        logger.info(
            f"[ContextAggregator] 聚合完成: metrics={len(result['related_metrics'])}, "
            f"alerts={len(result['related_alerts'])}, "
            f"changes={len(result['recent_changes'])}"
        )
        return result

    def _alert_summary(self) -> Dict[str, Any]:
        """告警摘要"""
        return {
            'id': self.alert.id,
            'title': self.alert.title,
            'message': self.alert.message,
            'severity': self.alert.severity,
            'alert_type': self.alert.alert_type,
            'metric_name': self.alert.metric_name,
            'metric_value': self.alert.metric_value,
            'threshold': self.alert.threshold,
            'start_time': self.alert.start_time.isoformat() if self.alert.start_time else None,
            'status': self.alert.status,
            'db_name': self.config.name,
            'db_type': self.db_type,
        }

    def _get_related_metrics(self) -> Dict[str, List]:
        """
        获取告警前后时间窗口内的关键指标历史
        从 MonitorLog 中拉取最近的指标快照
        """
        try:
            cutoff = timezone.now() - timedelta(minutes=self.time_window_min)
            logs = MonitorLog.objects.filter(
                config=self.config,
                create_time__gte=cutoff
            ).order_by('-create_time')[:20]
        except Exception as e:
            logger.warning(f"[ContextAggregator] 拉取 MonitorLog 失败: {e}")
            return {}

        related = {}
        for log in logs:
            try:
                data = json.loads(log.message) if isinstance(log.message, str) else log.message
                if not isinstance(data, dict):
                    continue
                # 提取所有数值型指标
                for key, value in data.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        if key not in related:
                            related[key] = []
                        related[key].append({
                            'ts': log.create_time.isoformat(),
                            'value': float(value),
                            'status': log.status,
                        })
            except (json.JSONDecodeError, TypeError):
                continue

        # 每个指标只保留最近 10 个点
        for k in related:
            related[k] = related[k][:10]
        return related

    def _get_related_alerts(self) -> List[Dict[str, Any]]:
        """
        获取同对象/同窗口内的相关告警
        """
        try:
            cutoff = timezone.now() - timedelta(minutes=self.time_window_min * 2)
            related = AlertLog.objects.filter(
                config=self.config,
                create_time__gte=cutoff,
                status__in=['active', 'acknowledged'],
            ).exclude(id=self.alert.id).order_by('-create_time')[:20]
        except Exception as e:
            logger.warning(f"[ContextAggregator] 拉取相关告警失败: {e}")
            return []

        return [{
            'id': a.id,
            'title': a.title,
            'alert_type': a.alert_type,
            'severity': a.severity,
            'metric_name': a.metric_name,
            'metric_value': a.metric_value,
            'start_time': a.start_time.isoformat() if a.start_time else None,
            'status': a.status,
        } for a in related]

    def _get_cluster_context(self) -> Dict[str, Any]:
        """
        获取集群/主从兄弟节点状态
        """
        context = {
            'has_topology': False,
            'topology_type': 'single',
            'peers': [],
        }
        try:
            topology = DatabaseTopology.objects.filter(
                db_config=self.config
            ).first()
            if not topology:
                return context

            context['has_topology'] = True
            context['topology_type'] = topology.topology_type
            context['role'] = topology.role
            context['cluster_name'] = topology.cluster_name
            context['sync_mode'] = topology.sync_mode
            context['lag_seconds'] = topology.lag_seconds

            # 拉取兄弟节点最新状态
            for peer in topology.peer_databases.all():
                try:
                    latest = MonitorLog.objects.filter(
                        config=peer
                    ).order_by('-create_time').first()
                    if latest:
                        peer_status = {
                            'name': peer.name,
                            'status': latest.status,
                            'last_collect': latest.create_time.isoformat(),
                        }
                    else:
                        peer_status = {
                            'name': peer.name,
                            'status': 'UNKNOWN',
                        }
                    context['peers'].append(peer_status)
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"[ContextAggregator] 拉取拓扑失败: {e}")
        return context

    def _get_business_context(self) -> Dict[str, Any]:
        """
        获取业务系统上下文
        """
        try:
            systems = self.config.business_systems.all()
        except Exception as e:
            logger.warning(f"[ContextAggregator] 拉取业务系统失败: {e}")
            return {'systems': [], 'critical_count': 0}

        system_list = []
        critical_count = 0
        for sys in systems:
            criticality = sys.importance
            if criticality == 'critical':
                critical_count += 1
            system_list.append({
                'id': sys.id,
                'name': sys.name,
                'importance': criticality,
                'owner': sys.owner,
                'description': sys.description,
            })

        return {
            'systems': system_list,
            'critical_count': critical_count,
            'total_count': len(system_list),
        }

    def _get_recent_changes(self) -> List[Dict[str, Any]]:
        """
        获取近期变更 (DDL/参数变更等)
        """
        try:
            cutoff = timezone.now() - timedelta(hours=72)
            changes = AuditLog.objects.filter(
                config=self.config,
                create_time__gte=cutoff,
            ).order_by('-create_time')[:30]
        except Exception as e:
            logger.warning(f"[ContextAggregator] 拉取变更记录失败: {e}")
            return []

        return [{
            'id': c.id,
            'action_type': c.action_type,
            'description': c.description if hasattr(c, 'description') else str(c.message)[:200],
            'operator': c.operator if hasattr(c, 'operator') else '',
            'create_time': c.create_time.isoformat(),
        } for c in changes]

    def _get_topology_info(self) -> Dict[str, Any]:
        """
        获取数据库拓扑信息
        """
        return self._get_cluster_context()

    def _get_baseline_comparison(self) -> Dict[str, Any]:
        """
        获取基线对比(简化版 - 详细基线对比由 RCA 引擎做)
        """
        return {
            'time_window_min': self.time_window_min,
            'has_baseline': True,
            'note': '详细基线对比由 rca_engine_v2 处理'
        }


def aggregate_alert_context(alert_id: int) -> Dict[str, Any]:
    """
    便捷函数: 给定告警 ID 聚合上下文
    """
    try:
        alert = AlertLog.objects.get(id=alert_id)
    except AlertLog.DoesNotExist:
        return {'error': f'alert {alert_id} not found'}
    aggregator = ContextAggregator(alert)
    return aggregator.aggregate()


# ==========================================================================
# Phase 6B: 事故上下文聚合 (吃 Incident, 不依赖 AlertLog)
# 详细设计: phase6/20_phase6B_diagnosis.md §2
# ==========================================================================
def get_ash_timeline(config_id: int, window_min: int = 30) -> Dict[str, Any]:
    """查 session_sample 事故窗口, 产出 ASH 时间线画像 (phase6/20 §2.1)。"""
    import datetime as _dt
    from collections import Counter
    result = {
        'window': [], 'active_session_trend': [], 'top_wait_events': [],
        'blocking_chains': [], 'top_sql_by_samples': [],
    }
    try:
        from monitor.timeseries import get_timeseries_storage
        ts = get_timeseries_storage()
        until = timezone.now()
        since = until - _dt.timedelta(minutes=window_min)
        rows = ts.query_session_samples(config_id, since, until)
    except Exception as e:
        logger.warning(f"[ASH] 查询失败: {e}")
        return result
    if not rows:
        return result
    result['window'] = [since.isoformat(), until.isoformat()]

    # 按采样点(time)聚合 active/blocked
    by_time = {}
    wait_counter = Counter()
    sql_counter = Counter()
    sql_text_map = {}
    total = len(rows)
    for r in rows:
        t = r.get('time')
        key = t.isoformat() if hasattr(t, 'isoformat') else str(t)
        slot = by_time.setdefault(key, {'t': key, 'active': 0, 'blocked': 0})
        slot['active'] += 1
        if r.get('is_blocked'):
            slot['blocked'] += 1
        we = r.get('wait_event') or r.get('state')
        if we:
            wait_counter[we] += 1
        dig = r.get('sql_digest') or (r.get('sql_text') or '')[:80]
        if dig:
            sql_counter[dig] += 1
            sql_text_map.setdefault(dig, (r.get('sql_text') or '')[:120])
    result['active_session_trend'] = sorted(by_time.values(), key=lambda x: x['t'])
    result['top_wait_events'] = [
        {'wait_event': w, 'samples': c, 'pct': round(c / total * 100, 1)}
        for w, c in wait_counter.most_common(8)]
    result['top_sql_by_samples'] = [
        {'sql_digest': d, 'sql_text': sql_text_map.get(d, ''), 'samples': c}
        for d, c in sql_counter.most_common(10)]

    # 阻塞链: 取每个采样点的阻塞关系
    chains_by_time = {}
    for r in rows:
        if not r.get('is_blocked'):
            continue
        t = r.get('time')
        key = t.isoformat() if hasattr(t, 'isoformat') else str(t)
        c = chains_by_time.setdefault(key, {'t': key, 'blocker': r.get('blocker_id'),
                                            'waiters': [], 'max_wait_sec': 0})
        c['waiters'].append(r.get('session_id'))
        c['max_wait_sec'] = max(c['max_wait_sec'], r.get('active_secs') or 0)
    result['blocking_chains'] = sorted(chains_by_time.values(),
                                       key=lambda x: -x['max_wait_sec'])[:10]
    return result


def _incident_recent_changes(config, hours: int = 72) -> List[Dict[str, Any]]:
    """近期变更(复用 AuditLog), 供 config 类根因关联。"""
    try:
        from monitor.models import AuditLog
        cutoff = timezone.now() - timedelta(hours=hours)
        rows = AuditLog.objects.filter(config=config, create_time__gte=cutoff)\
            .exclude(action_type='incident_transition').order_by('-create_time')[:30]
        return [{'action_type': c.action_type, 'description': (c.description or '')[:200],
                 'create_time': c.create_time.isoformat()} for c in rows]
    except Exception:
        return []


def _incident_business_context(config) -> Dict[str, Any]:
    try:
        systems = list(config.business_systems.all())
    except Exception:
        systems = []
    return {
        'systems': [{'name': s.name, 'importance': s.importance,
                     'owner': s.owner or '', 'contact': s.contact or ''} for s in systems],
        'max_importance': max([s.importance for s in systems], default='normal') if systems else 'normal',
    }


def build_incident_context(incident) -> Dict[str, Any]:
    """事故诊断上下文 (phase6/20 §2.3)。整合 ASH 时间线/变更/业务。"""
    config = incident.config
    return {
        'incident_summary': {
            'incident_id': incident.incident_id, 'category': incident.category,
            'priority': incident.priority, 'title': incident.title,
            'occurred_at': incident.occurred_at.isoformat() if incident.occurred_at else None,
        },
        'ash_timeline': get_ash_timeline(config.id, window_min=30),
        'recent_changes': _incident_recent_changes(config),
        'business_context': _incident_business_context(config),
    }
