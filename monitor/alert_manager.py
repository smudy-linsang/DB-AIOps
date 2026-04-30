"""
告警管理器 v2.0

职责：
- 基于 AlertLog 表实现告警去重：同类型告警活跃期间不重复推送
- 首次触发时通知，恢复时通知，中间静默
- 静默窗口支持：维护期间自动静默告警
- 批量聚合推送：同时间窗口内同类告警合并推送
- 告警确认机制：DBA可标记"已知晓"
- 统一对外接口：fire() / resolve() / acknowledge()
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from django.db import models
from django.utils import timezone

from monitor.models import AlertLog, AlertSilenceWindow, AlertNotificationLog

logger = logging.getLogger(__name__)


class AlertManager:
    """告警去重与状态管理 v2.0"""

    # 批量聚合时间窗口（秒）
    AGGREGATION_WINDOW_SEC = 300  # 5分钟
    # 批量聚合最小告警数
    AGGREGATION_MIN_COUNT = 3

    def __init__(self, config, notifier=None):
        """
        :param config: DatabaseConfig 实例
        :param notifier: 负责实际发送通知的可调用对象，签名为 notifier(title, body)
        """
        self.config = config
        self.notifier = notifier or self._default_notifier
        # 批量聚合缓冲区: {(alert_type, metric_key): [AlertLog, ...]}
        self._aggregation_buffer: Dict[Tuple[str, str], List[AlertLog]] = defaultdict(list)
        self._aggregation_timestamps: Dict[Tuple[str, str], datetime] = {}

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def fire(self, alert_type, metric_key, title, description, severity='warning'):
        """
        触发一条告警。
        - 检查静默窗口：若在静默期内则仅记录不推送
        - 若该 (config, alert_type, metric_key) 已有 active 记录 → 静默（不重复发送）
        - 若无 active 记录 → 创建记录并发送通知
        """
        # 1. 检查是否已有活跃告警
        existing = self._get_active(alert_type, metric_key)
        if existing:
            # 已经处于活跃状态，不重复推送
            return existing

        # 2. 创建告警记录
        alert = AlertLog.objects.create(
            config=self.config,
            alert_type=alert_type,
            metric_key=metric_key,
            severity=severity,
            title=title,
            description=description,
            status='active',
            last_notified_at=timezone.now(),
        )

        # 2.5 同步写入 Elasticsearch
        try:
            from monitor.elasticsearch_engine import index_alert
            index_alert(
                alert_id=alert.id,
                config_id=self.config.id,
                db_name=self.config.name,
                db_type=self.config.db_type,
                alert_type=alert_type,
                severity=severity,
                status='active',
                title=title,
                description=description,
                metric_key=metric_key,
                fired_at=timezone.now()
            )
        except Exception as es_err:
            logger.warning(f"[AlertManager] 写入 ES 告警失败: {es_err}")

        # 3. 检查静默窗口
        if self._is_silenced(alert_type):
            logger.info(f"[AlertManager] 告警已静默: {title} (config={self.config.name})")
            self._log_notification(alert, 'email', 'skipped', '静默窗口内')
            return alert

        # 4. 发送通知（可能进入批量聚合）
        self._send_notification(alert, title, description)

        return alert

    def resolve(self, alert_type, metric_key, recovery_title=None, recovery_body=None):
        """
        解除一条告警。
        - 若存在 active 记录 → 更新为 resolved 并发送恢复通知
        - 若无 active 记录 → 静默（已经是正常状态）
        """
        existing = self._get_active(alert_type, metric_key)
        if not existing:
            return

        existing.status = 'resolved'
        existing.resolved_at = timezone.now()
        existing.save(update_fields=['status', 'resolved_at'])

        # 清理聚合缓冲区
        buffer_key = (alert_type, metric_key)
        self._aggregation_buffer.pop(buffer_key, None)
        self._aggregation_timestamps.pop(buffer_key, None)

        if recovery_title and recovery_body:
            if not self._is_silenced(alert_type):
                self.notifier(recovery_title, recovery_body)

    def acknowledge(self, alert_id, acknowledged_by, comment=None):
        """
        确认告警（DBA标记"已知晓"）
        """
        try:
            alert = AlertLog.objects.get(id=alert_id, status='active')
            alert.status = 'acknowledged'
            alert.last_notified_at = timezone.now()
            alert.save(update_fields=['status', 'last_notified_at'])
            logger.info(f"[AlertManager] 告警已确认: {alert.title} by {acknowledged_by}")
            return True
        except AlertLog.DoesNotExist:
            return False

    def fire_or_resolve(self, condition, alert_type, metric_key,
                        fire_title, fire_body, resolve_title, resolve_body,
                        severity='warning'):
        """
        根据 condition 决定触发还是解除告警的便捷方法。
        :param condition: bool，True 表示当前处于告警状态
        """
        if condition:
            self.fire(alert_type, metric_key, fire_title, fire_body, severity)
        else:
            self.resolve(alert_type, metric_key, resolve_title, resolve_body)

    def flush_aggregation(self):
        """立即刷新所有聚合缓冲区，发送批量告警"""
        for buffer_key, alerts in list(self._aggregation_buffer.items()):
            if len(alerts) >= self.AGGREGATION_MIN_COUNT:
                self._send_aggregated_alert(buffer_key, alerts)
            else:
                # 不够聚合阈值，逐条发送
                for alert in alerts:
                    self.notifier(alert.title, alert.description)
            self._aggregation_buffer.pop(buffer_key, None)
            self._aggregation_timestamps.pop(buffer_key, None)

    # ------------------------------------------------------------------
    # 静默窗口
    # ------------------------------------------------------------------

    def _is_silenced(self, alert_type='') -> bool:
        """检查当前是否在静默窗口内"""
        now = timezone.now()

        # 查询适用的静默窗口
        windows = AlertSilenceWindow.objects.filter(is_active=True).filter(
            # 全局静默或当前数据库的静默
            models.Q(config__isnull=True) | models.Q(config=self.config)
        ).filter(
            # 所有告警类型或指定类型
            models.Q(alert_type='') | models.Q(alert_type=alert_type)
        )

        for window in windows:
            if window.is_in_window():
                return True

        return False

    # ------------------------------------------------------------------
    # 批量聚合
    # ------------------------------------------------------------------

    def _should_aggregate(self, alert_type, metric_key) -> bool:
        """检查是否应该进入聚合模式"""
        buffer_key = (alert_type, metric_key)

        if buffer_key not in self._aggregation_timestamps:
            return False

        elapsed = (timezone.now() - self._aggregation_timestamps[buffer_key]).total_seconds()
        return elapsed < self.AGGREGATION_WINDOW_SEC

    def _add_to_aggregation(self, alert, buffer_key):
        """将告警加入聚合缓冲区"""
        self._aggregation_buffer[buffer_key].append(alert)

        if buffer_key not in self._aggregation_timestamps:
            self._aggregation_timestamps[buffer_key] = timezone.now()

        # 检查是否达到聚合发送条件
        if len(self._aggregation_buffer[buffer_key]) >= self.AGGREGATION_MIN_COUNT:
            self._send_aggregated_alert(buffer_key, self._aggregation_buffer[buffer_key])
            self._aggregation_buffer.pop(buffer_key, None)
            self._aggregation_timestamps.pop(buffer_key, None)

    def _send_aggregated_alert(self, buffer_key, alerts):
        """发送聚合告警"""
        alert_type, metric_key = buffer_key
        count = len(alerts)
        unique_configs = list(set(a.config.name for a in alerts))

        title = f"[聚合告警] {alert_type} - {metric_key} ({count}个实例)"
        body = f"检测到 {count} 个同类告警：\n"
        for a in alerts[:5]:
            body += f"- {a.config.name}: {a.title}\n"
        if count > 5:
            body += f"... 还有 {count - 5} 个\n"

        self.notifier(title, body)

        # 记录通知日志
        for alert in alerts:
            self._log_notification(alert, 'email', 'success', '聚合推送')

    # ------------------------------------------------------------------
    # 通知发送
    # ------------------------------------------------------------------

    def _send_notification(self, alert, title, description):
        """发送单条告警通知"""
        buffer_key = (alert.alert_type, alert.metric_key)

        # 检查是否应该进入聚合模式
        if self._should_aggregate(alert.alert_type, alert.metric_key):
            self._add_to_aggregation(alert, buffer_key)
            return

        # 直接发送
        try:
            self.notifier(title, description)
            self._log_notification(alert, 'email', 'success')
        except Exception as e:
            logger.error(f"[AlertManager] 通知发送失败: {e}")
            self._log_notification(alert, 'email', 'failed', str(e))

    def _default_notifier(self, title, body):
        """默认通知器（日志输出）"""
        logger.warning(f"[ALERT] {title}: {body}")

    def _log_notification(self, alert, channel, status, error_message=None):
        """记录通知发送日志"""
        try:
            AlertNotificationLog.objects.create(
                alert=alert,
                channel=channel,
                status=status,
                error_message=error_message,
            )
        except Exception as e:
            logger.error(f"[AlertManager] 记录通知日志失败: {e}")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_active(self, alert_type, metric_key):
        return AlertLog.objects.filter(
            config=self.config,
            alert_type=alert_type,
            metric_key=metric_key,
            status='active',
        ).first()

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    @staticmethod
    def get_active_alerts(config_id=None, severity=None):
        """获取活跃告警列表"""
        qs = AlertLog.objects.filter(status='active').select_related('config')
        if config_id:
            qs = qs.filter(config_id=config_id)
        if severity:
            qs = qs.filter(severity=severity)
        return qs.order_by('-create_time')

    @staticmethod
    def get_alert_summary():
        """获取告警汇总统计"""
        active_alerts = AlertLog.objects.filter(status='active')
        return {
            'total': active_alerts.count(),
            'critical': active_alerts.filter(severity='critical').count(),
            'warning': active_alerts.filter(severity='warning').count(),
            'info': active_alerts.filter(severity='info').count(),
            'by_type': dict(
                active_alerts.values_list('alert_type').annotate(
                    count=models.Count('id')
                ).values_list('alert_type', 'count')
            ),
        }

    @staticmethod
    def get_silence_windows(config_id=None):
        """获取静默窗口列表"""
        qs = AlertSilenceWindow.objects.filter(is_active=True)
        if config_id:
            qs = qs.filter(models.Q(config__isnull=True) | models.Q(config_id=config_id))
        return qs


