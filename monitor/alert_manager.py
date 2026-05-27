"""
告警管理器 v3.0

职责：
- 基于 AlertLog 表实现告警去重：同类型告警活跃期间不重复推送
- 首次触发时通知，恢复时通知，中间静默
- 静默窗口支持：维护期间自动静默告警
- 通知规则驱动告警路由：根据 NotificationRule 匹配渠道/时间策略/升级
- 批量聚合推送：同时间窗口内同类告警合并推送
- 告警确认机制：DBA可标记"已知晓"
- 告警升级：N分钟未确认自动提升严重级别
- 统一对外接口：fire() / resolve() / acknowledge()
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from django.db import models
from django.utils import timezone

from monitor.models import AlertLog, AlertSilenceWindow, AlertNotificationLog, NotificationRule

logger = logging.getLogger(__name__)


class AlertManager:
    """告警去重与状态管理 v3.0 - 通知规则驱动"""

    # 批量聚合时间窗口（秒）
    AGGREGATION_WINDOW_SEC = 300  # 5分钟
    # 批量聚合最小告警数
    AGGREGATION_MIN_COUNT = 3

    # 严重程度等级映射（用于升级判断）
    SEVERITY_ORDER = ['info', 'warning', 'error', 'critical', 'emergency']

    def __init__(self, config, notifier=None):
        """
        :param config: DatabaseConfig 实例
        :param notifier: 保留兼容，不再作为主要通知路径
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

        # 5. SSE 实时推送告警事件
        try:
            from monitor.sse_views import publish_alert_event
            publish_alert_event(alert_type, 'fire', {
                'alert_id': alert.id,
                'config_id': self.config.id,
                'db_name': self.config.name,
                'db_type': self.config.db_type,
                'severity': severity,
                'title': title,
            })
        except Exception:
            pass

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
                # v3.0: 恢复通知也走通知规则
                matched_rules = self._match_rules(alert_type, 'info')
                if matched_rules:
                    all_channels = []
                    seen = set()
                    for rule in matched_rules:
                        for ch in rule.channels:
                            if ch not in seen:
                                all_channels.append(ch)
                                seen.add(ch)
                    if all_channels:
                        self._send_to_channels(recovery_title, recovery_body, all_channels)
                    else:
                        self.notifier(recovery_title, recovery_body)
                else:
                    self._send_to_channels(recovery_title, recovery_body, ['email', 'dingtalk', 'wecom'])

        # SSE 实时推送恢复事件
        try:
            from monitor.sse_views import publish_alert_event
            publish_alert_event(alert_type, 'resolve', {
                'alert_id': existing.id,
                'config_id': self.config.id,
                'db_name': self.config.name,
                'db_type': self.config.db_type,
                'title': recovery_title or 'Resolved',
            })
        except Exception:
            pass

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
    # 通知规则匹配（v3.0 核心新增）
    # ------------------------------------------------------------------

    def _match_rules(self, alert_type: str, severity: str) -> List[NotificationRule]:
        """
        匹配当前告警的通知规则。

        匹配逻辑：
        1. 规则必须 is_enabled=True
        2. alert_types 为空或包含当前告警类型
        3. severities 为空或包含当前严重程度
        4. db_config 为空（全局规则）或等于当前数据库
        5. 时间策略匹配（如果配置了 schedule）
        6. 按优先级降序排列
        """
        rules = NotificationRule.objects.filter(
            is_enabled=True
        ).filter(
            # 全局规则或当前数据库的规则
            models.Q(db_config__isnull=True) | models.Q(db_config=self.config)
        ).filter(
            # 告警类型匹配（空列表表示匹配所有类型）
            models.Q(alert_types__len=0) | models.Q(alert_types__contains=[alert_type])
        ).filter(
            # 严重程度匹配（空列表表示匹配所有程度）
            models.Q(severities__len=0) | models.Q(severities__contains=[severity])
        ).order_by('-priority', 'name')

        matched = []
        for rule in rules:
            if self._check_schedule(rule):
                matched.append(rule)

        return matched

    def _check_schedule(self, rule: NotificationRule) -> bool:
        """
        校验通知规则的时间策略。

        - schedule 为 None 或空 → 始终匹配
        - schedule.work_hours=True → 仅在工作时间发送
        - schedule.weekdays → 仅在指定星期发送
        """
        schedule = rule.schedule
        if not schedule:
            return True

        now = timezone.now()

        # 检查星期
        weekdays_str = schedule.get('weekdays', '')
        if weekdays_str:
            # weekdays 格式: "1,2,3,4,5"（周一=1，周日=7）
            try:
                allowed_days = [int(d.strip()) for d in weekdays_str.split(',') if d.strip()]
                # Python weekday(): 周一=0，周日=6；规则中周一=1，周日=7
                current_day = now.isoweekday()
                if current_day not in allowed_days:
                    return False
            except (ValueError, TypeError):
                pass

        # 检查工作时间
        if schedule.get('work_hours'):
            try:
                start_str = schedule.get('start', '09:00')
                end_str = schedule.get('end', '18:00')
                start_h, start_m = map(int, start_str.split(':'))
                end_h, end_m = map(int, end_str.split(':'))
                start_min = start_h * 60 + start_m
                end_min = end_h * 60 + end_m
                now_min = now.hour * 60 + now.minute
                if not (start_min <= now_min <= end_min):
                    return False
            except (ValueError, TypeError):
                pass

        return True

    def _send_to_channels(self, title: str, body: str, channels: List[str],
                          alert=None, rule: NotificationRule = None) -> Dict[str, bool]:
        """
        根据指定的渠道列表发送通知，返回每个渠道的发送结果。
        """
        from monitor.notifications import send_email_alert, send_dingtalk_alert, send_wecom_alert

        results = {}
        for channel in channels:
            try:
                if channel == 'email':
                    results['email'] = send_email_alert(title, body)
                elif channel == 'dingtalk':
                    results['dingtalk'] = send_dingtalk_alert(title, body)
                elif channel == 'wecom':
                    results['wecom'] = send_wecom_alert(title, body)
                else:
                    logger.warning(f"[AlertManager] 未知通知渠道: {channel}")
                    results[channel] = False
            except Exception as e:
                logger.error(f"[AlertManager] 渠道 {channel} 发送失败: {e}")
                results[channel] = False

            # 记录每个渠道的通知日志
            if alert:
                status = 'success' if results.get(channel) else 'failed'
                error_msg = None if results.get(channel) else f'渠道 {channel} 发送失败'
                self._log_notification(
                    alert, channel, status, error_msg,
                    rule_id=rule.id if rule else None
                )

        return results

    def _check_escalation(self, alert: AlertLog) -> Optional[str]:
        """
        检查告警是否需要升级。

        逻辑：遍历匹配的通知规则，如果 escalation_minutes > 0 且
        告警持续时间超过该值且仍未被确认，则升级严重程度。

        Returns:
            升级后的 severity 或 None（不需要升级）
        """
        if alert.status in ('resolved', 'rejected'):
            return None

        rules = self._match_rules(alert.alert_type, alert.severity)
        for rule in rules:
            if rule.escalation_minutes > 0 and alert.create_time:
                elapsed = (timezone.now() - alert.create_time).total_seconds()
                if elapsed >= rule.escalation_minutes * 60:
                    # 升级到下一个严重程度
                    try:
                        current_idx = self.SEVERITY_ORDER.index(alert.severity)
                        if current_idx < len(self.SEVERITY_ORDER) - 1:
                            new_severity = self.SEVERITY_ORDER[current_idx + 1]
                            logger.info(
                                f"[AlertManager] 告警升级: {alert.title} "
                                f"{alert.severity} -> {new_severity}"
                            )
                            return new_severity
                    except ValueError:
                        pass

        return None

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
        """发送聚合告警（v3.0: 使用通知规则）"""
        alert_type, metric_key = buffer_key
        count = len(alerts)
        unique_configs = list(set(a.config.name for a in alerts))

        title = f"[聚合告警] {alert_type} - {metric_key} ({count}个实例)"
        body = f"检测到 {count} 个同类告警：\n"
        for a in alerts[:5]:
            body += f"- {a.config.name}: {a.title}\n"
        if count > 5:
            body += f"... 还有 {count - 5} 个\n"

        # 使用第一个告警的严重程度匹配规则
        severity = alerts[0].severity if alerts else 'warning'
        matched_rules = self._match_rules(alert_type, severity)

        if matched_rules:
            all_channels = []
            seen = set()
            for rule in matched_rules:
                for ch in rule.channels:
                    if ch not in seen:
                        all_channels.append(ch)
                        seen.add(ch)
            if all_channels:
                self._send_to_channels(title, body, all_channels, alert=alerts[0])
            else:
                self.notifier(title, body)
        else:
            # 无匹配规则，回退默认
            self._send_to_channels(title, body, ['email', 'dingtalk', 'wecom'])

        # 记录通知日志（对聚合中每个告警）
        for alert in alerts:
            self._log_notification(alert, 'aggregated', 'success', f'聚合推送({count}条)')

    # ------------------------------------------------------------------
    # 通知发送
    # ------------------------------------------------------------------

    def _send_notification(self, alert, title, description):
        """发送单条告警通知（v3.0: 通知规则驱动）"""
        buffer_key = (alert.alert_type, alert.metric_key)

        # 检查是否应该进入聚合模式
        if self._should_aggregate(alert.alert_type, alert.metric_key):
            self._add_to_aggregation(alert, buffer_key)
            return

        # v3.0: 根据通知规则决定发送渠道
        matched_rules = self._match_rules(alert.alert_type, alert.severity)

        if matched_rules:
            # 合并所有匹配规则的渠道（去重）
            all_channels = []
            seen = set()
            for rule in matched_rules:
                for ch in rule.channels:
                    if ch not in seen:
                        all_channels.append(ch)
                        seen.add(ch)

            if all_channels:
                self._send_to_channels(
                    title, description, all_channels,
                    alert=alert, rule=matched_rules[0]
                )
            else:
                # 规则匹配但渠道为空，仅写日志
                logger.info(f"[AlertManager] 匹配到规则但无渠道: {title}")
                self._log_notification(alert, 'none', 'skipped', '规则无渠道配置')
        else:
            # 无匹配规则，回退到默认行为（全部渠道）
            logger.info(f"[AlertManager] 无匹配通知规则，使用默认渠道: {title}")
            self._send_to_channels(
                title, description, ['email', 'dingtalk', 'wecom'],
                alert=alert
            )

    def _default_notifier(self, title, body):
        """默认通知器（日志输出）"""
        logger.warning(f"[ALERT] {title}: {body}")

    def _log_notification(self, alert, channel, status, error_message=None, rule_id=None):
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
    # 静默窗口
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_active(self, alert_type, metric_key):
        # 已确认的告警同样视为"存在"，避免重复触发；只有彻底删除后才解除抑制
        return AlertLog.objects.filter(
            config=self.config,
            alert_type=alert_type,
            metric_key=metric_key,
            status__in=('active', 'acknowledged'),
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


