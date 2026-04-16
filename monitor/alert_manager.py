"""
告警管理器 v0.1.0

职责：
- 基于 AlertLog 表实现告警去重：同类型告警活跃期间不重复推送
- 首次触发时通知，恢复时通知，中间静默
- 统一对外接口：fire() / resolve()
"""

from django.utils import timezone
from monitor.models import AlertLog


class AlertManager:
    """告警去重与状态管理"""

    def __init__(self, config, notifier):
        """
        :param config: DatabaseConfig 实例
        :param notifier: 负责实际发送通知的可调用对象，签名为 notifier(title, body)
        """
        self.config = config
        self.notifier = notifier

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def fire(self, alert_type, metric_key, title, description, severity='warning'):
        """
        触发一条告警。
        - 若该 (config, alert_type, metric_key) 已有 active 记录 → 静默（不重复发送）
        - 若无 active 记录 → 创建记录并发送通知
        """
        existing = self._get_active(alert_type, metric_key)
        if existing:
            # 已经处于活跃状态，不重复推送
            return

        AlertLog.objects.create(
            config=self.config,
            alert_type=alert_type,
            metric_key=metric_key,
            severity=severity,
            title=title,
            description=description,
            status='active',
            last_notified_at=timezone.now(),
        )
        self.notifier(title, description)

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

        if recovery_title and recovery_body:
            self.notifier(recovery_title, recovery_body)

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
