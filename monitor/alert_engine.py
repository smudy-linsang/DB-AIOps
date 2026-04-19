#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
智能告警引擎 v1.0

Phase 3 核心模块：基于168时间槽动态基线的精准告警

核心功能：
1. 基于时间槽的动态阈值 - 使用baseline_engine的168时间槽基线
2. 三重条件触发 - 量级(μ±kσ) + 方向(up/down/both) + 持续性(连续N次)
3. 告警收敛 - 同类型告警在时间窗口内不重复推送
4. 告警升级 - 持续异常自动升级严重级别

作者: DB-AIOps Team
版本: v1.0.0
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from django.utils import timezone

from monitor.baseline_engine import BaselineEngine, METRIC_DIRECTION_CONFIG


# =============================================================================
# 数据结构
# =============================================================================

@dataclass
class AlertEvent:
    """告警事件"""
    metric_name: str
    current_value: float
    baseline_mean: float
    baseline_std: float
    normal_min: float
    normal_max: float
    direction: str  # 'up', 'down', 'both'
    severity: str   # 'info', 'warning', 'critical', 'emergency'
    reason: str
    timestamp: datetime = field(default_factory=timezone.now)


@dataclass
class AlertState:
    """告警状态追踪"""
    first_detected: datetime
    last_detected: datetime
    consecutive_count: int
    current_severity: str
    fire_count: int  # 已触发次数


# =============================================================================
# 告警引擎核心类
# =============================================================================

class AlertEngine:
    """
    智能告警引擎
    
    使用三重条件判断是否触发告警：
    1. 量级条件：current_value 超出 baseline.normal_min ~ baseline.normal_max
    2. 方向条件：direction 指定了偏移方向（up/down/both）
    3. 持续性条件：在时间窗口内连续异常（默认3次）
    
    告警收敛：
    - 同一 (config_id, metric_name, direction) 的告警使用时间窗口收敛
    - 窗口期内不重复推送
    
    告警升级：
    - 持续异常时，每 CONSECUTIVE_THRESHOLDS 次自动升级严重级别
    """

    # 告警级别
    SEVERITY_LEVELS = ['info', 'warning', 'critical', 'emergency']
    
    # 持续性窗口：连续N次检测到异常才触发告警
    PERSISTENCE_THRESHOLD = 3
    
    # 告警升级阈值：连续检测到异常多少次后自动升级
    ESCALATION_THRESHOLDS = {
        3: 'warning',     # 连续3次 -> warning
        5: 'critical',    # 连续5次 -> critical
        10: 'emergency',  # 连续10次 -> emergency
    }
    
    # 收敛时间窗口（秒）
    CONVERGENCE_WINDOW_SEC = 300  # 5分钟内同类型告警不重复推送
    
    # 默认方向配置（当metric未配置方向时使用）
    DEFAULT_DIRECTION = 'both'

    def __init__(self, config, baseline_engine: BaselineEngine):
        """
        初始化告警引擎
        
        :param config: DatabaseConfig 实例
        :param baseline_engine: BaselineEngine 实例（已加载该数据库的168时间槽基线）
        """
        self.config = config
        self.baseline_engine = baseline_engine
        
        # 告警状态追踪：{(metric_name, direction): AlertState}
        self._alert_states: Dict[Tuple[str, str], AlertState] = defaultdict(
            lambda: AlertState(
                first_detected=timezone.now(),
                last_detected=timezone.now(),
                consecutive_count=0,
                current_severity='info',
                fire_count=0
            )
        )
        
        # 收敛追踪：{alert_key: last_fire_time}
        self._convergence_cache: Dict[str, datetime] = {}

    def should_alert(
        self,
        metric_name: str,
        current_value: float,
        direction: Optional[str] = None
    ) -> Tuple[bool, Optional[AlertEvent]]:
        """
        判断是否应该触发告警（三重条件检测）
        
        :param metric_name: 指标名称（如 'active_connections', 'conn_usage_pct'）
        :param current_value: 当前值
        :param direction: 期望的偏移方向，默认从 METRIC_DIRECTION_CONFIG 读取
        
        :return: (should_fire, alert_event_or_none)
            - should_fire: True 表示应该触发告警
            - alert_event: AlertEvent 详情（如果 should_fire 为 True）
        """
        
        # 1. 获取该指标的方向配置
        if direction is None:
            direction = METRIC_DIRECTION_CONFIG.get(metric_name, self.DEFAULT_DIRECTION)
        
        # 2. 获取当前时间对应的时间槽基线
        now = timezone.now()
        baseline_model = self.baseline_engine.get_baseline_for_current_slot(metric_name)
        
        if baseline_model is None:
            # 没有基线数据，不告警
            return False, None
        
        # 3. 量级条件检测：是否超出正常范围
        is_anomaly, anomaly_type = self._check_magnitude(
            current_value, baseline_model, direction
        )
        
        if not is_anomaly:
            # 量级正常，重置持续性计数
            self._reset_persistence(metric_name, direction)
            return False, None
        
        # 4. 方向条件检测
        if not self._check_direction(anomaly_type, direction):
            return False, None
        
        # 5. 持续性条件检测
        state_key = (metric_name, direction)
        state = self._alert_states[state_key]
        
        # 更新时间戳和计数
        state.last_detected = now
        state.consecutive_count += 1
        
        # 检查是否达到持续性阈值
        if state.consecutive_count < self.PERSISTENCE_THRESHOLD:
            return False, None
        
        # 6. 判断是否应该触发（考虑收敛）
        alert_key = self._build_alert_key(metric_name, direction)
        
        if self._is_converged(alert_key):
            # 在收敛窗口期内，不触发但更新状态
            return False, None
        
        # 7. 计算告警级别（可能需要升级）
        severity = self._calculate_severity(state)
        
        # 8. 构建告警事件
        alert_event = AlertEvent(
            metric_name=metric_name,
            current_value=current_value,
            baseline_mean=baseline_model.mean,
            baseline_std=baseline_model.std,
            normal_min=baseline_model.normal_min,
            normal_max=baseline_model.normal_max,
            direction=direction,
            severity=severity,
            reason=self._build_reason(metric_name, current_value, baseline_model, anomaly_type),
            timestamp=now
        )
        
        # 9. 更新收敛缓存
        self._convergence_cache[alert_key] = now
        state.fire_count += 1
        
        return True, alert_event

    def _check_magnitude(
        self,
        current_value: float,
        baseline_model,
        direction: str
    ) -> Tuple[bool, Optional[str]]:
        """
        条件1: 量级条件检测
        
        :return: (is_anomaly, anomaly_type)
            - is_anomaly: True 表示超出正常范围
            - anomaly_type: 'high' 或 'low'
        """
        if current_value > baseline_model.normal_max:
            return True, 'high'
        elif current_value < baseline_model.normal_min:
            return True, 'low'
        else:
            return False, None

    def _check_direction(self, anomaly_type: str, expected_direction: str) -> bool:
        """
        条件2: 方向条件检测
        
        :param anomaly_type: 'high' 或 'low'
        :param expected_direction: 'up', 'down', 'both'
        """
        if expected_direction == 'both':
            return True
        elif expected_direction == 'up':
            return anomaly_type == 'high'
        elif expected_direction == 'down':
            return anomaly_type == 'low'
        else:
            return True

    def _reset_persistence(self, metric_name: str, direction: str):
        """重置持续性计数（当指标恢复正常时）"""
        state_key = (metric_name, direction)
        if state_key in self._alert_states:
            state = self._alert_states[state_key]
            # 保留fire_count用于统计，但重置连续计数
            state.consecutive_count = 0

    def _is_converged(self, alert_key: str) -> bool:
        """检查告警是否在收敛窗口期内"""
        if alert_key not in self._convergence_cache:
            return False
        
        last_fire = self._convergence_cache[alert_key]
        elapsed = (timezone.now() - last_fire).total_seconds()
        
        return elapsed < self.CONVERGENCE_WINDOW_SEC

    def _build_alert_key(self, metric_name: str, direction: str) -> str:
        """构建告警唯一标识"""
        return f"{self.config.id}:{metric_name}:{direction}"

    def _calculate_severity(self, state: AlertState) -> str:
        """
        计算告警级别（支持自动升级）
        
        基于连续异常次数自动升级：
        - 3次 -> warning
        - 5次 -> critical
        - 10次 -> emergency
        """
        severity = 'info'
        
        for threshold, sev in sorted(self.ESCALATION_THRESHOLDS.items()):
            if state.consecutive_count >= threshold:
                severity = sev
        
        state.current_severity = severity
        return severity

    def _build_reason(
        self,
        metric_name: str,
        current_value: float,
        baseline_model,
        anomaly_type: str
    ) -> str:
        """构建告警原因描述"""
        direction_desc = '暴涨' if anomaly_type == 'high' else '骤降'
        deviation = abs(current_value - baseline_model.mean)
        deviation_pct = (deviation / baseline_model.mean * 100) if baseline_model.mean != 0 else 0
        
        return (
            f"{metric_name} 出现异常{direction_desc}：\n"
            f"  当前值：{current_value:.2f}\n"
            f"  基线均值：{baseline_model.mean:.2f} ± {baseline_model.std:.2f}\n"
            f"  正常范围：{baseline_model.normal_min:.2f} ~ {baseline_model.normal_max:.2f}\n"
            f"  偏离程度：{deviation:.2f} ({deviation_pct:.1f}%)"
        )

    def resolve_alert(self, metric_name: str, direction: str) -> Optional[AlertEvent]:
        """
        解除告警（当指标恢复正常时调用）
        
        :return: AlertEvent 如果有活跃告警需要解除
        """
        state_key = (metric_name, direction)
        
        if state_key not in self._alert_states:
            return None
        
        state = self._alert_states[state_key]
        
        if state.consecutive_count == 0 or state.fire_count == 0:
            return None
        
        # 构建恢复事件
        alert_event = AlertEvent(
            metric_name=metric_name,
            current_value=0,  # 已恢复正常
            baseline_mean=0,
            baseline_std=0,
            normal_min=0,
            normal_max=0,
            direction=direction,
            severity='info',
            reason=f"指标 {metric_name} 已恢复至正常范围（持续异常 {state.consecutive_count} 个周期后恢复）",
            timestamp=timezone.now()
        )
        
        # 重置状态
        state.consecutive_count = 0
        state.fire_count = 0
        
        return alert_event

    def get_active_alerts(self) -> List[Dict]:
        """获取当前活跃告警状态"""
        active = []
        now = timezone.now()
        
        for (metric_name, direction), state in self._alert_states.items():
            if state.consecutive_count > 0:
                elapsed = (now - state.last_detected).total_seconds()
                active.append({
                    'metric_name': metric_name,
                    'direction': direction,
                    'consecutive_count': state.consecutive_count,
                    'current_severity': state.current_severity,
                    'fire_count': state.fire_count,
                    'first_detected': state.first_detected.isoformat(),
                    'last_detected': state.last_detected.isoformat(),
                    'last_alert_age_sec': elapsed,
                })
        
        return active


# =============================================================================
# 便捷函数
# =============================================================================

def create_alert_engine(config, baseline_engine: BaselineEngine) -> AlertEngine:
    """
    工厂函数：创建智能告警引擎实例
    """
    return AlertEngine(config, baseline_engine)


def check_metric_with_baseline(
    config,
    baseline_engine: BaselineEngine,
    metric_name: str,
    current_value: float,
    direction: Optional[str] = None
) -> Tuple[bool, Optional[AlertEvent]]:
    """
    便捷函数：使用基线检查单个指标是否应该告警
    
    使用示例：
        should_fire, event = check_metric_with_baseline(
            config=db_config,
            baseline_engine=baseline_eng,
            metric_name='active_connections',
            current_value=150,
        )
        if should_fire:
            send_alert(event)
    """
    engine = create_alert_engine(config, baseline_engine)
    return engine.should_alert(metric_name, current_value, direction)