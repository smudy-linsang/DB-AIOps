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
