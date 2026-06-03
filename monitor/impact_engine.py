"""
影响评估引擎 v1.0 (Phase 5)

功能:
- 健康度影响计算(告警如何影响 5 维度健康分)
- 业务连续性影响评估(基于业务图谱)
- 业务损失估算(每分钟/每小时)
- SLA 违约风险评估

设计文档参考: PHASE5_DEVELOPMENT_DESIGN.md 第二部分 P0-4
"""
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


# ==========================================
# 告警类型到健康度影响映射
# ==========================================
HEALTH_IMPACT_MAP = {
    # alert_type: {affected_dimensions, decay_pct, recovery_hours}
    'tablespace_full': {
        'dimensions': ['capacity'],
        'decay_pct': 0.30,
        'recovery_hours': 4.0,
    },
    'tablespace_warning': {
        'dimensions': ['capacity'],
        'decay_pct': 0.05,
        'recovery_hours': 24.0,
    },
    'connection_exhausted': {
        'dimensions': ['availability', 'performance'],
        'decay_pct': 0.40,
        'recovery_hours': 1.0,
    },
    'connection_warning': {
        'dimensions': ['availability'],
        'decay_pct': 0.10,
        'recovery_hours': 4.0,
    },
    'down': {
        'dimensions': ['availability', 'performance', 'capacity', 'configuration', 'operations'],
        'decay_pct': 0.80,
        'recovery_hours': 2.0,
    },
    'long_transaction': {
        'dimensions': ['performance'],
        'decay_pct': 0.10,
        'recovery_hours': 4.0,
    },
    'replication_lag': {
        'dimensions': ['availability'],
        'decay_pct': 0.20,
        'recovery_hours': 1.0,
    },
    'replication_broken': {
        'dimensions': ['availability'],
        'decay_pct': 0.50,
        'recovery_hours': 6.0,
    },
    'slow_query': {
        'dimensions': ['performance'],
        'decay_pct': 0.15,
        'recovery_hours': 2.0,
    },
    'deadlock': {
        'dimensions': ['performance'],
        'decay_pct': 0.05,
        'recovery_hours': 1.0,
    },
    'invalid_object': {
        'dimensions': ['configuration', 'operations'],
        'decay_pct': 0.10,
        'recovery_hours': 8.0,
    },
    'log_switch_high': {
        'dimensions': ['performance'],
        'decay_pct': 0.10,
        'recovery_hours': 4.0,
    },
    'arch_gap': {
        'dimensions': ['availability'],
        'decay_pct': 0.30,
        'recovery_hours': 6.0,
    },
    'scn_headroom_low': {
        'dimensions': ['availability'],
        'decay_pct': 0.40,
        'recovery_hours': 24.0,
    },
}


# 告警类型到业务影响等级(基础映射,无业务上下文时)
BASE_BUSINESS_IMPACT = {
    'down': 'fatal',
    'tablespace_full': 'severe',
    'connection_exhausted': 'severe',
    'replication_broken': 'severe',
    'arch_gap': 'severe',
    'scn_headroom_low': 'severe',
    'tablespace_warning': 'moderate',
    'connection_warning': 'moderate',
    'replication_lag': 'moderate',
    'slow_query': 'moderate',
    'long_transaction': 'moderate',
    'invalid_object': 'moderate',
    'log_switch_high': 'minor',
    'deadlock': 'minor',
}


@dataclass
class ImpactAssessment:
    """综合影响评估结果"""
    # 健康度影响
    health_score_before: float
    health_score_after: float
    health_score_delta: float
    affected_health_dimensions: List[str]
    health_recovery_hours: float

    # 业务影响
    overall_business_severity: str  # fatal/severe/moderate/minor/none
    total_systems_affected: int
    critical_systems_affected: int
    important_systems_affected: int
    normal_systems_affected: int

    # 业务损失
    estimated_loss_per_minute: float  # 元
    estimated_loss_per_hour: float    # 元
    estimated_loss_total: float       # 元(按预计恢复时长)

    # SLA
    sla_breach_risk: str  # low/medium/high/critical

    # 受影响业务系统详情
    system_impacts: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HealthImpactCalculator:
    """健康度影响计算器"""

    def calculate(
        self,
        alert_type: str,
        current_health_score: float = 80.0,
    ) -> Dict[str, Any]:
        """
        计算告警对健康度的影响

        返回:
            {
                'before': 原始分,
                'after': 影响后分,
                'delta': 差值,
                'affected_dimensions': [...],
                'recovery_hours': ...,
            }
        """
        impact = HEALTH_IMPACT_MAP.get(alert_type, {
            'dimensions': ['availability'],
            'decay_pct': 0.10,
            'recovery_hours': 4.0,
        })
        decay = impact['decay_pct']
        after = max(0, current_health_score * (1 - decay))
        return {
            'before': current_health_score,
            'after': after,
            'delta': after - current_health_score,
            'affected_dimensions': impact['dimensions'],
            'recovery_hours': impact['recovery_hours'],
        }


class BusinessImpactAssessor:
    """业务连续性影响评估器"""

    # 业务损失估算(元/分钟) - 简化估算
    # 实际生产中应通过业务系统配置定义
    DEFAULT_LOSS_PER_MIN = {
        'critical': 1000.0,   # 核心业务 1000元/分钟
        'important': 200.0,   # 重要业务 200元/分钟
        'normal': 20.0,       # 一般业务 20元/分钟
    }

    def __init__(self, db_config, alert):
        self.db_config = db_config
        self.alert = alert
        self.alert_type = alert.alert_type or 'unknown'
        self.severity = alert.severity

    def assess(self) -> ImpactAssessment:
        """
        综合评估业务影响
        """
        # 1. 获取基础业务影响等级
        base_severity = BASE_BUSINESS_IMPACT.get(self.alert_type, 'minor')

        # 2. 评估健康度影响
        health_calc = HealthImpactCalculator()
        health_impact = health_calc.calculate(self.alert_type)

        # 3. 枚举受影响业务系统
        system_impacts = self._enumerate_business_systems(base_severity)
        critical_count = sum(1 for s in system_impacts if s['system_importance'] == 'critical')
        important_count = sum(1 for s in system_impacts if s['system_importance'] == 'important')
        normal_count = sum(1 for s in system_impacts if s['system_importance'] == 'normal')

        # 4. 估算损失
        loss_per_min = sum(
            self.DEFAULT_LOSS_PER_MIN.get(s['system_importance'], 0) * s['impact_multiplier']
            for s in system_impacts
        )
        loss_per_hour = loss_per_min * 60
        total_loss = loss_per_hour * health_impact['recovery_hours']

        # 5. 综合严重度
        overall_severity = self._compute_overall_severity(
            base_severity, critical_count, important_count
        )

        # 6. SLA 风险
        sla_risk = self._assess_sla_risk(overall_severity, critical_count)

        return ImpactAssessment(
            health_score_before=health_impact['before'],
            health_score_after=health_impact['after'],
            health_score_delta=health_impact['delta'],
            affected_health_dimensions=health_impact['affected_dimensions'],
            health_recovery_hours=health_impact['recovery_hours'],
            overall_business_severity=overall_severity,
            total_systems_affected=len(system_impacts),
            critical_systems_affected=critical_count,
            important_systems_affected=important_count,
            normal_systems_affected=normal_count,
            estimated_loss_per_minute=loss_per_min,
            estimated_loss_per_hour=loss_per_hour,
            estimated_loss_total=total_loss,
            sla_breach_risk=sla_risk,
            system_impacts=system_impacts,
        )

    def _enumerate_business_systems(self, base_severity: str) -> List[Dict[str, Any]]:
        """枚举受影响的业务系统"""
        # 尝试查询关联的业务系统
        systems = []
        try:
            related_systems = self.db_config.business_systems.all()
        except Exception as e:
            logger.warning(f"[ImpactEngine] 拉取业务系统失败: {e}")
            related_systems = []

        # 影响乘数(根据告警严重度)
        impact_multiplier_map = {
            'fatal': 1.0,
            'severe': 0.8,
            'moderate': 0.4,
            'minor': 0.1,
        }
        mult = impact_multiplier_map.get(base_severity, 0.5)

        for sys in related_systems:
            # 核心系统影响放大
            sys_mult = mult
            if sys.importance == 'critical':
                sys_mult = min(mult * 1.5, 1.0)

            systems.append({
                'system_id': sys.id,
                'system_name': sys.name,
                'system_importance': sys.importance,
                'owner': sys.owner,
                'impact_level': self._system_impact_level(sys.importance, base_severity),
                'impact_multiplier': sys_mult,
                'sla_target': getattr(sys, 'sla_target', '99.9%'),
            })
        return systems

    def _system_impact_level(self, importance: str, alert_severity: str) -> str:
        """单个系统的影响等级"""
        severity_matrix = {
            ('critical', 'fatal'): 'fatal',
            ('critical', 'severe'): 'severe',
            ('critical', 'moderate'): 'severe',
            ('important', 'fatal'): 'fatal',
            ('important', 'severe'): 'severe',
            ('important', 'moderate'): 'moderate',
            ('normal', 'fatal'): 'severe',
            ('normal', 'severe'): 'moderate',
            ('normal', 'moderate'): 'minor',
        }
        return severity_matrix.get((importance, alert_severity), 'minor')

    def _compute_overall_severity(
        self,
        base: str,
        critical_count: int,
        important_count: int,
    ) -> str:
        """计算综合严重度"""
        # 如果有核心系统受影响,等级上浮
        if critical_count > 0 and base in ('severe', 'fatal'):
            return 'fatal'
        if critical_count > 0 or base == 'fatal':
            return 'fatal' if base == 'fatal' else 'severe'
        if important_count > 0 and base in ('moderate', 'severe'):
            return 'severe'
        return base

    def _assess_sla_risk(self, severity: str, critical_count: int) -> str:
        """SLA 违约风险"""
        if severity == 'fatal' or critical_count > 0:
            return 'critical'
        if severity == 'severe':
            return 'high'
        if severity == 'moderate':
            return 'medium'
        return 'low'


# ==========================================
# 便捷入口
# ==========================================
def assess_impact(alert, db_config) -> Dict[str, Any]:
    """
    便捷函数: 给定告警和 DB 配置,返回综合影响评估
    """
    assessor = BusinessImpactAssessor(db_config, alert)
    result = assessor.assess()
    return result.to_dict()
