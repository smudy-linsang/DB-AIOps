"""
基线分析引擎 v1.0

功能:
- 基于历史数据自动计算指标基线 (均值、标准差、百分位数)
- 动态阈值检测异常
- 支持按小时/星期几的周期性基线
"""

import json
import statistics
from datetime import datetime, timedelta
from django.utils import timezone
from monitor.models import MonitorLog, DatabaseConfig


class BaselineEngine:
    """基线分析引擎"""
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.history_days = 7  # 默认分析最近 7 天数据
    
    def get_history_logs(self, days=None):
        """获取历史监控日志"""
        if days is None:
            days = self.history_days
        
        start_time = timezone.now() - timedelta(days=days)
        logs = MonitorLog.objects.filter(
            config=self.config,
            status='UP',
            create_time__gte=start_time
        ).order_by('create_time')
        
        return logs
    
    def parse_log_data(self, log):
        """解析日志中的 JSON 数据"""
        try:
            return json.loads(log.message)
        except:
            return {}
    
    def calculate_baseline(self, metric_key, days=None):
        """
        计算单个指标的基线
        
        参数:
            metric_key: 指标键名 (如 'active_connections', 'qps' 等)
            days: 分析天数
        
        返回:
            {
                'mean': 均值,
                'std': 标准差,
                'min': 最小值,
                'max': 最大值,
                'p95': 95 分位数,
                'p99': 99 分位数,
                'sample_count': 样本数,
                'normal_range': [下限，上限] (均值±2σ)
            }
        """
        logs = self.get_history_logs(days)
        
        values = []
        for log in logs:
            data = self.parse_log_data(log)
            if metric_key in data and data[metric_key] is not None:
                try:
                    val = float(data[metric_key])
                    values.append(val)
                except (ValueError, TypeError):
                    pass
        
        if len(values) < 3:
            return None  # 样本不足
        
        # 计算统计量
        mean_val = statistics.mean(values)
        std_val = statistics.stdev(values) if len(values) > 1 else 0
        min_val = min(values)
        max_val = max(values)
        
        # 百分位数
        sorted_vals = sorted(values)
        p95_idx = int(len(sorted_vals) * 0.95)
        p99_idx = int(len(sorted_vals) * 0.99)
        p95 = sorted_vals[min(p95_idx, len(sorted_vals)-1)]
        p99 = sorted_vals[min(p99_idx, len(sorted_vals)-1)]
        
        # 正常范围 (均值±2σ)
        normal_lower = mean_val - 2 * std_val
        normal_upper = mean_val + 2 * std_val
        
        return {
            'mean': round(mean_val, 2),
            'std': round(std_val, 2),
            'min': round(min_val, 2),
            'max': round(max_val, 2),
            'p95': round(p95, 2),
            'p99': round(p99, 2),
            'sample_count': len(values),
            'normal_range': [round(normal_lower, 2), round(normal_upper, 2)]
        }
    
    def detect_anomaly(self, current_value, baseline):
        """
        检测当前值是否异常
        
        返回:
            (is_anomaly, anomaly_type, severity)
            - is_anomaly: 是否异常
            - anomaly_type: 异常类型 ('high'/'low'/None)
            - severity: 严重程度 ('warning'/'critical')
        """
        if baseline is None:
            return False, None, None
        
        mean = baseline['mean']
        std = baseline['std']
        p99 = baseline['p99']
        
        # 超过 P99 -> 严重异常
        if current_value > p99:
            return True, 'high', 'critical'
        
        # 超过 均值+2σ -> 警告
        if current_value > mean + 2 * std:
            return True, 'high', 'warning'
        
        # 低于 均值 -2σ -> 警告 (可能是连接数骤降等)
        if current_value < mean - 2 * std and mean > 0:
            return True, 'low', 'warning'
        
        return False, None, None
    
    def get_full_baseline_report(self, days=None):
        """
        生成完整的基线报告
        
        返回所有支持指标的基线统计
        """
        logs = self.get_history_logs(days)
        if not logs:
            return {'error': '无历史数据'}
        
        # 收集所有出现过的指标键
        all_keys = set()
        for log in logs:
            data = self.parse_log_data(log)
            all_keys.update(data.keys())
        
        # 排除不适合统计的字段
        exclude_keys = {'version', 'error', 'warning_list', 'locks', 'tablespaces', 
                       'database_sizes', 'cluster_nodes', 'shards', 'message'}
        metric_keys = all_keys - exclude_keys
        
        report = {
            'config_name': self.config.name,
            'db_type': self.config.db_type,
            'analysis_period_days': days or self.history_days,
            'sample_count': len(logs),
            'metrics': {}
        }
        
        for key in metric_keys:
            baseline = self.calculate_baseline(key, days)
            if baseline:
                report['metrics'][key] = baseline
        
        return report
    
    def check_current_against_baseline(self, current_data):
        """
        检查当前监控数据是否偏离基线
        
        参数:
            current_data: 当前采集的指标字典
        
        返回:
            anomalies: 异常列表 [(metric_name, current_value, baseline, anomaly_type, severity)]
        """
        anomalies = []
        
        for metric_key, current_value in current_data.items():
            # 跳过非数值型指标
            if not isinstance(current_value, (int, float)):
                continue
            
            # 跳过列表/字典类型
            if isinstance(current_value, (list, dict)):
                continue
            
            baseline = self.calculate_baseline(metric_key, days=7)
            if baseline:
                is_anomaly, anomaly_type, severity = self.detect_anomaly(current_value, baseline)
                
                if is_anomaly:
                    anomalies.append((
                        metric_key,
                        current_value,
                        baseline,
                        anomaly_type,
                        severity
                    ))
        
        return anomalies


# ==========================================
# 使用示例
# ==========================================
"""
# 在 start_monitor.py 中集成:

from .baseline_engine import BaselineEngine

# 在 process_result 方法中添加基线检查:
if current_status == 'UP':
    # 基线异常检测
    baseline_engine = BaselineEngine(config)
    anomalies = baseline_engine.check_current_against_baseline(data)
    
    for metric_name, current_val, baseline, anomaly_type, severity in anomalies:
        subject = f"{'🔴' if severity == 'critical' else '🟠'} 基线异常：{config.name}"
        message = (
            f"检测到指标异常偏离基线:\n\n"
            f"指标：{metric_name}\n"
            f"当前值：{current_val}\n"
            f"基线均值：{baseline['mean']} ± {baseline['std']}\n"
            f"正常范围：{baseline['normal_range'][0]} ~ {baseline['normal_range'][1]}\n"
            f"偏离类型：{anomaly_type}\n"
            f"严重程度：{severity}"
        )
        self.send_alert_email(config, subject, message)
"""
