"""
健康评分引擎 v2.0 (Phase 2 - 5维度健康评估)

功能:
- 5维度健康评估体系
- 百分制评分 + 等级评定
- 多维度权重可配置
- 历史趋势分析

设计文档参考: DB_AIOps_DESIGN.md 3.3 节

评分维度及权重:
- Availability (可用性) 25%    - 实例是否在线、连接是否正常
- Capacity (容量) 25%         - 存储空间、连接数使用率
- Performance (性能) 25%       - QPS、响应时间、慢查询
- Configuration (配置) 15%     - 配置合理性、安全设置
- Operations (运维) 10%       - 备份状态、监控覆盖
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any

from monitor.models import MonitorLog, DatabaseConfig, AlertLog


# ==========================================
# 评分维度配置
# ==========================================

# 权重配置 (总和 = 100%)
HEALTH_WEIGHTS = {
    'availability': 0.25,
    'capacity': 0.25,
    'performance': 0.25,
    'configuration': 0.15,
    'operations': 0.10,
}

# 健康等级阈值
HEALTH_GRADES = {
    'A': (90, 100, '优秀', 'green'),
    'B': (80, 89, '良好', 'limegreen'),
    'C': (70, 79, '一般', 'orange'),
    'D': (60, 69, '较差', 'orangered'),
    'F': (0, 59, '危险', 'red'),
}

# 子维度权重 (每个大维度内的权重)
SUB_WEIGHTS = {
    'availability': {
        'status': 0.6,           # 实例状态
        'connectivity': 0.4,     # 连接可达性
    },
    'capacity': {
        'tablespace': 0.6,       # 表空间使用率
        'connection': 0.4,       # 连接使用率
    },
    'performance': {
        'qps': 0.3,              # QPS
        'response_time': 0.3,    # 响应时间
        'slow_queries': 0.4,    # 慢查询
    },
    'configuration': {
        'security': 0.5,         # 安全配置
        'param合理性': 0.5,      # 参数合理性
    },
    'operations': {
        'backup': 0.5,           # 备份状态
        'monitoring': 0.5,       # 监控覆盖
    },
}


# ==========================================
# 评分计算辅助函数
# ==========================================

def _linear_score(value: float, min_val: float, max_val: float, reverse: bool = False) -> float:
    """
    线性评分 (将值转换为 0-100 分)
    
    参数:
        value: 当前值
        min_val: 最佳值 (100分)
        max_val: 最差值 (0分)
        reverse: True 表示值越大越好
    """
    if reverse:
        # 值越大越好 (如 QPS)
        if value >= max_val:
            return 100.0
        if value <= min_val:
            return 0.0
        return 100.0 * (value - min_val) / (max_val - min_val)
    else:
        # 值越小越好 (如 响应时间)
        if value <= min_val:
            return 100.0
        if value >= max_val:
            return 0.0
        return 100.0 * (max_val - value) / (max_val - min_val)


def _percent_score(value: float, warning_threshold: float, critical_threshold: float) -> float:
    """
    百分比类型评分 (如使用率)
    
    评分规则:
    - <= warning: 100分
    - <= critical: 线性下降至 50分
    - > critical: 线性下降至 0分
    """
    if value <= warning_threshold:
        return 100.0
    elif value <= critical_threshold:
        # 线性下降: warning->100, critical->50
        ratio = (value - warning_threshold) / (critical_threshold - warning_threshold)
        return 100.0 - 50.0 * ratio
    else:
        # 线性下降: critical->50, 100->0
        ratio = (value - critical_threshold) / (100.0 - critical_threshold)
        return max(0.0, 50.0 - 50.0 * ratio)


# ==========================================
# 维度评分器
# ==========================================

class AvailabilityScorer:
    """可用性评分器"""
    
    def __init__(self):
        self.weights = SUB_WEIGHTS['availability']
    
    def score(self, data: Dict) -> Dict[str, Any]:
        """
        评分可用性维度
        
        评估项:
        - status: 实例状态 (UP=100, DOWN=0)
        - connectivity: 连接可达性 (响应时间)
        """
        details = {}
        subscores = {}
        
        # 1. 实例状态
        status = data.get('current_status', 'UNKNOWN')
        if status == 'UP':
            details['status'] = 100.0
        elif status == 'DOWN':
            details['status'] = 0.0
        else:
            details['status'] = 50.0  # UNKNOWN
        
        # 2. 连接可达性 (基于响应延迟)
        response_time = data.get('response_time_ms', 0)
        if response_time <= 10:
            details['connectivity'] = 100.0
        elif response_time <= 100:
            details['connectivity'] = 100.0 - (response_time - 10) * 0.5
        elif response_time <= 1000:
            details['connectivity'] = 55.0 - (response_time - 100) * 0.05
        else:
            details['connectivity'] = max(0.0, 10.0 - (response_time - 1000) * 0.001)
        
        # 计算加权得分
        score = sum(details[k] * self.weights[k] for k in self.weights)
        
        return {
            'score': round(score, 1),
            'details': details,
            'subscores': subscores
        }


class CapacityScorer:
    """容量评分器"""
    
    def __init__(self):
        self.weights = SUB_WEIGHTS['capacity']
    
    def score(self, data: Dict) -> Dict[str, Any]:
        """
        评分容量维度
        
        评估项:
        - tablespace: 表空间使用率 (最高表空间)
        - connection: 连接使用率
        """
        details = {}
        
        # 1. 表空间使用率
        tablespaces = data.get('tablespaces', [])
        max_tbs_usage = 0.0
        if tablespaces:
            max_tbs_usage = max([t.get('used_pct', 0) for t in tablespaces])
        
        # 评分: 0-70%=100分, 70-85%=线性下降, 85-100%=快速下降
        if max_tbs_usage <= 70:
            details['tablespace'] = 100.0
        elif max_tbs_usage <= 85:
            details['tablespace'] = 100.0 - (max_tbs_usage - 70) * (50.0 / 15.0)
        elif max_tbs_usage <= 95:
            details['tablespace'] = 50.0 - (max_tbs_usage - 85) * (40.0 / 10.0)
        else:
            details['tablespace'] = max(0.0, 10.0 - (max_tbs_usage - 95) * 2.0)
        
        # 2. 连接使用率
        conn_usage = data.get('conn_usage_pct', 0)
        details['connection'] = _percent_score(conn_usage, warning_threshold=70, critical_threshold=85)
        
        # 计算加权得分
        score = sum(details[k] * self.weights[k] for k in self.weights)
        
        return {
            'score': round(score, 1),
            'details': details,
            'max_tablespace_usage': max_tbs_usage,
            'connection_usage': conn_usage
        }


class PerformanceScorer:
    """性能评分器"""
    
    def __init__(self, db_type: str = 'mysql'):
        self.weights = SUB_WEIGHTS['performance']
        self.db_type = db_type
    
    def score(self, data: Dict) -> Dict[str, Any]:
        """
        评分性能维度
        
        评估项:
        - qps: 每秒查询数 (相对基线)
        - response_time: 响应时间
        - slow_queries: 慢查询数量
        """
        details = {}
        
        # 1. QPS 评分 (假设基线 QPS = 1000)
        qps = data.get('qps', 0)
        baseline_qps = data.get('baseline_qps', 1000)
        
        if qps >= baseline_qps * 0.8:  # 达到基线 80% 以上
            details['qps'] = 100.0
        elif qps >= baseline_qps * 0.5:
            details['qps'] = 80.0 + 20.0 * (qps - baseline_qps * 0.5) / (baseline_qps * 0.3)
        elif qps > 0:
            details['qps'] = 80.0 * qps / (baseline_qps * 0.5)
        else:
            details['qps'] = 0.0
        
        # 2. 响应时间评分 (ms)
        response_time = data.get('response_time_ms', 0)
        # 根据数据库类型设置阈值
        if self.db_type == 'oracle':
            good_rt, bad_rt = 100, 1000
        elif self.db_type == 'pgsql':
            good_rt, bad_rt = 50, 500
        else:
            good_rt, bad_rt = 20, 200
        
        details['response_time'] = _linear_score(response_time, good_rt, bad_rt, reverse=True)
        
        # 3. 慢查询数量评分
        slow_queries = data.get('slow_queries_active', 0)
        # 根据连接数调整阈值
        active_conns = data.get('active_connections', 100)
        threshold = max(5, active_conns * 0.05)  # 至少5个，或连接数的5%
        
        if slow_queries == 0:
            details['slow_queries'] = 100.0
        elif slow_queries <= threshold:
            details['slow_queries'] = 100.0 - (slow_queries / threshold) * 30.0
        elif slow_queries <= threshold * 2:
            details['slow_queries'] = 70.0 - ((slow_queries - threshold) / threshold) * 40.0
        else:
            details['slow_queries'] = max(0.0, 30.0 - (slow_queries - threshold * 2) * 0.1)
        
        # 计算加权得分
        score = sum(details[k] * self.weights[k] for k in self.weights)
        
        return {
            'score': round(score, 1),
            'details': details,
            'qps': qps,
            'response_time_ms': response_time,
            'slow_queries': slow_queries
        }


class ConfigurationScorer:
    """配置评分器"""
    
    def __init__(self, db_type: str = 'mysql'):
        self.weights = SUB_WEIGHTS['configuration']
        self.db_type = db_type
    
    def score(self, config: DatabaseConfig) -> Dict[str, Any]:
        """
        评分配置维度
        
        评估项:
        - security: 安全配置 (密码强度、SSL等)
        - param合理性: 参数合理性
        """
        details = {}
        
        # 1. 安全配置评分
        security_score = 100.0
        
        # 检查密码是否加密存储
        if not config.password:
            security_score -= 20  # 无密码配置
        elif len(config.password) < 12:
            security_score -= 10  # 密码过短
        
        # 检查是否启用 SSL (通过 connection_options)
        options = config.connection_options or {}
        if options.get('ssl') != 'true' and options.get('ssl_mode') != 'required':
            security_score -= 30  # 未启用 SSL
        
        # 检查端口是否为默认端口
        default_ports = {
            'oracle': 1521, 'mysql': 3306, 'pgsql': 5432,
            'gbase': 5258, 'tdsql': 3306, 'dm': 5236
        }
        if config.port == default_ports.get(self.db_type, 0):
            security_score -= 10  # 使用默认端口
        
        details['security'] = max(0.0, security_score)
        
        # 2. 参数合理性 (基于 connection_options)
        param_score = 100.0
        
        # 超时配置
        if options.get('connect_timeout'):
            try:
                timeout = int(options['connect_timeout'])
                if timeout < 5:
                    param_score -= 10
                elif timeout > 60:
                    param_score -= 5
            except:
                param_score -= 10
        
        # 字符集配置 (MySQL)
        if self.db_type in ('mysql', 'tdsql'):
            charset = options.get('charset', '')
            if charset and charset.lower() not in ('utf8mb4', 'utf8', 'gbk'):
                param_score -= 10
        
        details['param合理性'] = max(0.0, param_score)
        
        # 计算加权得分
        score = details['security'] * self.weights['security'] + details['param合理性'] * self.weights['param合理性']
        
        return {
            'score': round(score, 1),
            'details': details,
            'ssl_enabled': options.get('ssl') == 'true' or options.get('ssl_mode') == 'required'
        }


class OperationsScorer:
    """运维评分器"""
    
    def __init__(self):
        self.weights = SUB_WEIGHTS['operations']
    
    def score(self, config: DatabaseConfig, recent_logs: List[Dict]) -> Dict[str, Any]:
        """
        评分运维维度
        
        评估项:
        - backup: 备份状态 (基于最近告警)
        - monitoring: 监控覆盖 (日志覆盖情况)
        """
        details = {}
        
        # 1. 备份状态评分
        # 简化: 检查是否有备份相关告警
        backup_alerts = [a for a in recent_logs if 'backup' in str(a.get('message', '')).lower()]
        if not backup_alerts:
            details['backup'] = 100.0
        elif len(backup_alerts) == 1:
            details['backup'] = 70.0
        else:
            details['backup'] = max(0.0, 40.0 - len(backup_alerts) * 10)
        
        # 2. 监控覆盖评分
        # 基于最近 24 小时日志覆盖率
        cutoff = datetime.now() - timedelta(hours=24)
        logs_24h = [l for l in recent_logs if l.get('create_time', datetime.min) > cutoff]
        
        # 理想: 每5分钟一条日志，24小时应有 288 条
        expected_logs = 288
        actual_logs = len(logs_24h)
        coverage_ratio = actual_logs / expected_logs
        
        if coverage_ratio >= 1.0:
            details['monitoring'] = 100.0
        elif coverage_ratio >= 0.8:
            details['monitoring'] = 100.0
        elif coverage_ratio >= 0.5:
            details['monitoring'] = 80.0 * coverage_ratio / 0.5
        else:
            details['monitoring'] = max(0.0, 40.0 * coverage_ratio / 0.5)
        
        # 计算加权得分
        score = details['backup'] * self.weights['backup'] + details['monitoring'] * self.weights['monitoring']
        
        return {
            'score': round(score, 1),
            'details': details,
            'logs_24h': len(logs_24h),
            'monitoring_coverage': round(coverage_ratio * 100, 1)
        }


# ==========================================
# 健康评分引擎
# ==========================================

class HealthEngine:
    """
    健康评分引擎 v2.0
    
    支持:
    - 5维度百分制评分
    - 等级评定 (A/B/C/D/F)
    - 历史趋势分析
    - 详细评分报告
    """
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.weights = HEALTH_WEIGHTS
        
        # 初始化各维度评分器
        self.scorers = {
            'availability': AvailabilityScorer(),
            'capacity': CapacityScorer(),
            'performance': PerformanceScorer(db_type=config.db_type),
            'configuration': ConfigurationScorer(db_type=config.db_type),
            'operations': OperationsScorer(),
        }
    
    def get_latest_data(self) -> Optional[Dict]:
        """获取最新监控数据"""
        latest_log = MonitorLog.objects.filter(
            config=self.config,
            status='UP'
        ).order_by('-create_time').first()
        
        if not latest_log:
            return None
        
        try:
            data = json.loads(latest_log.message)
            data['current_status'] = latest_log.status
            return data
        except:
            return None
    
    def get_recent_logs(self, hours: int = 24) -> List[Dict]:
        """获取最近 N 小时的日志"""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        logs = MonitorLog.objects.filter(
            config=self.config,
            create_time__gte=cutoff
        ).order_by('-create_time')
        
        return list(logs)
    
    def calculate(self, current_data: Dict = None) -> Dict[str, Any]:
        """
        计算健康评分
        
        参数:
            current_data: 当前监控数据 (如果不传则自动获取)
        
        返回:
            {
                'timestamp': 评分时间,
                'overall_score': 总分 (0-100),
                'grade': 等级 (A/B/C/D/F),
                'dimensions': {...},
                'summary': 总结,
                'recommendations': 改进建议
            }
        """
        if current_data is None:
            current_data = self.get_latest_data()
        
        if not current_data:
            return {
                'error': '无可用数据',
                'config_name': self.config.name,
                'overall_score': 0,
                'grade': 'F'
            }
        
        recent_logs = self.get_recent_logs(hours=24)
        
        # 计算各维度得分
        dimension_scores = {}
        
        # 1. 可用性评分
        dimension_scores['availability'] = self.scorers['availability'].score(current_data)
        
        # 2. 容量评分
        dimension_scores['capacity'] = self.scorers['capacity'].score(current_data)
        
        # 3. 性能评分
        dimension_scores['performance'] = self.scorers['performance'].score(current_data)
        
        # 4. 配置评分
        dimension_scores['configuration'] = self.scorers['configuration'].score(self.config)
        
        # 5. 运维评分
        dimension_scores['operations'] = self.scorers['operations'].score(
            self.config, 
            [{'create_time': log.create_time, 'message': log.message} for log in recent_logs]
        )
        
        # 计算总分
        overall_score = sum(
            dimension_scores[dim]['score'] * self.weights[dim]
            for dim in self.weights
        )
        
        # 确定等级
        grade_info = self._get_grade(overall_score)
        
        # 生成总结和建议
        summary = self._generate_summary(dimension_scores, overall_score, grade_info)
        recommendations = self._generate_recommendations(dimension_scores)
        
        return {
            'timestamp': datetime.now().isoformat(),
            'config_name': self.config.name,
            'db_type': self.config.db_type,
            'overall_score': round(overall_score, 1),
            'grade': grade_info['grade'],
            'grade_description': grade_info['description'],
            'grade_color': grade_info['color'],
            'dimensions': dimension_scores,
            'weights': self.weights,
            'summary': summary,
            'recommendations': recommendations,
            'data_points': len(recent_logs)
        }
    
    def _get_grade(self, score: float) -> Dict[str, Any]:
        """根据分数确定等级"""
        for grade, (min_score, max_score, desc, color) in HEALTH_GRADES.items():
            if min_score <= score <= max_score:
                return {
                    'grade': grade,
                    'description': desc,
                    'color': color
                }
        return {'grade': 'F', 'description': '危险', 'color': 'red'}
    
    def _generate_summary(self, dimension_scores: Dict, overall_score: float, grade_info: Dict) -> str:
        """生成健康评估总结"""
        emoji = {
            'A': '🟢',
            'B': '🟢',
            'C': '🟡',
            'D': '🟠',
            'F': '🔴'
        }.get(grade_info['grade'], '⚪')
        
        parts = [
            f"{emoji} 健康评分: {overall_score:.1f} 分",
            f"等级: {grade_info['grade']} ({grade_info['description']})"
        ]
        
        # 找出最低分维度
        min_dim = min(dimension_scores.items(), key=lambda x: x[1]['score'])
        parts.append(f"最需改进: {min_dim[0]} ({min_dim[1]['score']:.1f})")
        
        return " | ".join(parts)
    
    def _generate_recommendations(self, dimension_scores: Dict) -> List[str]:
        """生成改进建议"""
        recommendations = []
        
        # 可用性问题
        avail = dimension_scores.get('availability', {})
        if avail.get('score', 100) < 70:
            recommendations.append("⚠️ 可用性评分较低，建议检查实例状态和网络连接")
        
        # 容量问题
        cap = dimension_scores.get('capacity', {})
        if cap.get('score', 100) < 70:
            max_tbs = cap.get('max_tablespace_usage', 0)
            recommendations.append(f"⚠️ 容量评分较低，表空间最高使用率 {max_tbs:.1f}%，建议扩容或清理数据")
        
        # 性能问题
        perf = dimension_scores.get('performance', {})
        if perf.get('score', 100) < 70:
            slow_q = perf.get('slow_queries', 0)
            recommendations.append(f"⚠️ 性能评分较低，当前 {slow_q} 个活跃慢查询，建议优化 SQL")
        
        # 配置问题
        conf = dimension_scores.get('configuration', {})
        if conf.get('score', 100) < 70:
            if not conf.get('ssl_enabled', False):
                recommendations.append("⚠️ 安全配置未启用 SSL，建议启用加密连接")
            recommendations.append("⚠️ 配置评分较低，建议审查数据库参数设置")
        
        # 运维问题
        ops = dimension_scores.get('operations', {})
        if ops.get('score', 100) < 70:
            coverage = ops.get('monitoring_coverage', 0)
            recommendations.append(f"⚠️ 运维评分较低，监控覆盖率 {coverage:.1f}%，建议增加监控频率")
        
        if not recommendations:
            recommendations.append("✅ 各维度评分良好，继续保持当前运维状态")
        
        return recommendations
    
    def get_historical_score(self, days: int = 30) -> List[Dict]:
        """
        获取历史健康评分趋势
        
        返回:
            [{'date': '2024-01-01', 'score': 85.5, 'grade': 'B'}, ...]
        """
        cutoff = datetime.now() - timedelta(days=days)
        
        # 每天取一条评分记录 (简化: 取每天最后一条 UP 状态的日志)
        logs = MonitorLog.objects.filter(
            config=self.config,
            create_time__gte=cutoff,
            status='UP'
        ).order_by('create_time')
        
        # 按日期分组
        daily_scores = {}
        for log in logs:
            date_str = log.create_time.strftime('%Y-%m-%d')
            if date_str not in daily_scores:
                daily_scores[date_str] = log
        
        # 计算每日评分
        history = []
        for date_str, log in sorted(daily_scores.items()):
            try:
                data = json.loads(log.message)
                data['current_status'] = log.status
                result = self.calculate(data)
                history.append({
                    'date': date_str,
                    'score': result['overall_score'],
                    'grade': result['grade']
                })
            except:
                pass
        
        return history
    
    def compare_with_baseline(self, current_score: float, baseline_score: float = 80.0) -> Dict[str, Any]:
        """
        与基线对比
        
        返回变化分析
        """
        delta = current_score - baseline_score
        
        if delta >= 5:
            trend = 'improving'
            description = '显著改善'
        elif delta >= 0:
            trend = 'stable'
            description = '基本稳定'
        elif delta >= -5:
            trend = 'slight_decline'
            description = '轻微下降'
        else:
            trend = 'declining'
            description = '显著下降'
        
        return {
            'current_score': current_score,
            'baseline_score': baseline_score,
            'delta': round(delta, 1),
            'trend': trend,
            'description': description
        }


# ==========================================
# 使用示例
# ==========================================
"""
# 在 start_monitor.py 中集成健康评分:

from monitor.health_engine import HealthEngine

# 在 process_result 方法中添加健康评分:
if current_status == 'UP':
    health = HealthEngine(config)
    report = health.calculate(data)
    
    # 发送健康评分告警 (如果等级低于 C)
    if report['grade'] in ('D', 'F'):
        am = AlertManager(config)
        subject = f"{report['summary']} - {config.name}"
        body = f"健康评分: {report['overall_score']}\\n等级: {report['grade']}\\n\\n" + "\\n".join(report['recommendations'])
        
        am.fire(
            alert_type='health',
            metric_key='health_score',
            title=f"🔴 数据库健康评分 {report['grade']}",
            description=body,
            severity='critical' if report['grade'] == 'F' else 'warning'
        )

# 获取历史趋势
history = health.get_historical_score(days=30)
print("近30天健康评分趋势:")
for h in history[-7:]:
    print(f"  {h['date']}: {h['score']} ({h['grade']})")
"""