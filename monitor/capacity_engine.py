"""
容量预测引擎 v2.0 (Phase 2 - 多模型容量预测)

功能:
- 多模型容量预测 (Linear/Holt-Winters/ARIMA)
- 基于数据特征自动选择最优模型
- 存储空间剩余天数预测
- 告警阈值动态调整

设计文档参考: DB_AIOps_DESIGN.md 3.4 节
"""

import json
import math
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict

from monitor.models import MonitorLog, DatabaseConfig


# ==========================================
# 容量相关常量
# ==========================================

# 告警阈值配置 (百分比)
ALERT_THRESHOLDS = {
    'tablespace': {
        'warning': 75,    # 表空间使用率 > 75% 警告
        'critical': 90,   # 表空间使用率 > 90% 严重
        'emergency': 95,  # 表空间使用率 > 95% 紧急
    },
    'connection': {
        'warning': 70,    # 连接使用率 > 70% 警告
        'critical': 85,   # 连接使用率 > 85% 严重
        'emergency': 90,  # 连接使用率 > 90% 紧急
    },
    'storage': {
        'warning_days': 30,    # 剩余容量不足 30 天警告
        'critical_days': 14,   # 剩余容量不足 14 天严重
        'emergency_days': 7,   # 剩余容量不足 7 天紧急
    }
}

# 模型选择阈值
MODEL_SELECTION = {
    'min_data_points_linear': 7,           # 线性回归最少数据点
    'min_data_points_holt_winters': 14,    # Holt-Winters 最少数据点
    'min_data_points_arima': 30,           # ARIMA 最少数据点
    'seasonality_period': 7,               # 周期 (7天 = 一周)
}


# ==========================================
# 预测模型实现
# ==========================================

class LinearRegressionModel:
    """
    线性回归模型
    
    适用于: 稳定增长/下降趋势，无明显季节性
    优点: 简单、快速、不需要大量数据
    缺点: 无法捕捉季节性和非线性趋势
    """
    
    def __init__(self):
        self.slope = 0.0
        self.intercept = 0.0
        self.r_squared = 0.0
        self.fitted = False
    
    def fit(self, x: List[float], y: List[float]) -> bool:
        """训练模型"""
        n = len(x)
        if n < 2:
            return False
        
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(x[i] * y[i] for i in range(n))
        sum_x2 = sum(xi * xi for xi in x)
        
        denominator = n * sum_x2 - sum_x * sum_x
        if abs(denominator) < 1e-10:
            return False
        
        self.slope = (n * sum_xy - sum_x * sum_y) / denominator
        self.intercept = (sum_y - self.slope * sum_x) / n
        
        # 计算 R²
        y_mean = sum_y / n
        ss_tot = sum((yi - y_mean) ** 2 for yi in y)
        ss_res = sum((yi - (self.slope * xi + self.intercept)) ** 2 for xi, yi in zip(x, y))
        
        if ss_tot > 0:
            self.r_squared = 1 - ss_res / ss_tot
        
        self.fitted = True
        return True
    
    def predict(self, x: float) -> float:
        """预测"""
        if not self.fitted:
            raise ValueError("Model not fitted")
        return self.slope * x + self.intercept
    
    def predict_days(self, current_value: float, max_capacity: float, days_ahead: int = 90) -> Optional[int]:
        """
        预测达到容量上限的天数
        
        返回: 天数 (如果不会达到则返回 None)
        """
        if not self.fitted or self.slope <= 0:
            return None
        
        # 求解: slope * (t + days) + intercept = max_capacity
        # t = (max_capacity - intercept) / slope - days
        days_to_full = (max_capacity - self.intercept) / self.slope
        
        if days_to_full < 0:
            return None
        
        return int(days_to_full)


class HoltWintersModel:
    """
    Holt-Winters 三次指数平滑模型
    
    适用于: 有趋势和季节性的数据
    优点: 考虑季节性，能捕捉趋势变化
    缺点: 需要较多历史数据，参数敏感
    """
    
    def __init__(self, alpha: float = 0.3, beta: float = 0.1, gamma: float = 0.1, period: int = 7):
        self.alpha = alpha  # 水平平滑系数
        self.beta = beta    # 趋势平滑系数
        self.gamma = gamma  # 季节平滑系数
        self.period = period
        
        self.level = 0.0
        self.trend = 0.0
        self.seasonal = []  # 季节因子
        self.fitted = False
    
    def fit(self, y: List[float]) -> bool:
        """训练模型"""
        n = len(y)
        if n < self.period * 2:
            return False
        
        # 初始化
        self.level = sum(y[:self.period]) / self.period
        self.trend = (sum(y[self.period:2*self.period]) - sum(y[:self.period])) / (self.period * self.period)
        
        # 初始化季节因子
        self.seasonal = [1.0] * self.period
        for i in range(self.period):
            avg = sum(y[i:i+self.period]) / self.period
            if avg > 0:
                self.seasonal[i] = y[i] / avg
        
        # 平滑更新
        for t in range(self.period, n):
            last_level = self.level
            self.level = self.alpha * (y[t] / self.seasonal[t % self.period]) + (1 - self.alpha) * (last_level + self.trend)
            self.trend = self.beta * (self.level - last_level) + (1 - self.beta) * self.trend
            self.seasonal[t % self.period] = self.gamma * (y[t] / self.level) + (1 - self.gamma) * self.seasonal[t % self.period]
        
        self.fitted = True
        return True
    
    def predict(self, periods_ahead: int) -> List[float]:
        """预测未来 N 个周期的值"""
        if not self.fitted:
            raise ValueError("Model not fitted")
        
        predictions = []
        for i in range(periods_ahead):
            h = i + 1
            y_hat = (self.level + h * self.trend) * self.seasonal[(len(self.seasonal) - 1 + h) % self.period]
            predictions.append(y_hat)
        
        return predictions
    
    def predict_days(self, current_value: float, max_capacity: float, days_ahead: int = 90) -> Optional[int]:
        """预测达到容量上限的天数"""
        if not self.fitted:
            return None
        
        for i in range(1, days_ahead + 1):
            pred_value = self.predict(i)[0]
            if pred_value >= max_capacity:
                return i
        
        return None


class SimpleMovingAverageModel:
    """
    简单移动平均模型
    
    适用于: 数据波动较大，无明显趋势
    优点: 极其简单，对异常值不敏感
    缺点: 滞后性严重，无法预测长期趋势
    """
    
    def __init__(self, window: int = 7):
        self.window = window
        self.forecast = 0.0
        self.growth_rate = 0.0
        self.fitted = False
    
    def fit(self, y: List[float]) -> bool:
        """训练模型"""
        n = len(y)
        if n < self.window:
            return False
        
        # 计算最近窗口的平均值
        recent = y[-self.window:]
        self.forecast = sum(recent) / len(recent)
        
        # 计算增长率
        if n >= self.window * 2:
            old_avg = sum(y[:self.window]) / self.window
            new_avg = sum(y[-self.window:]) / self.window
            if old_avg > 0:
                self.growth_rate = (new_avg - old_avg) / old_avg / (n - self.window)
        
        self.fitted = True
        return True
    
    def predict(self, days_ahead: int) -> float:
        """预测未来某天的值 (简化: 假设固定增长率)"""
        if not self.fitted:
            raise ValueError("Model not fitted")
        return self.forecast * ((1 + self.growth_rate) ** days_ahead)
    
    def predict_days(self, current_value: float, max_capacity: float, days_ahead: int = 90) -> Optional[int]:
        """预测达到容量上限的天数"""
        if not self.fitted or self.growth_rate <= 0:
            return None
        
        # 求解: current * (1 + growth_rate)^days = max_capacity
        # days = ln(max_capacity/current) / ln(1 + growth_rate)
        if current_value <= 0 or current_value >= max_capacity:
            return None
        
        import math
        days = math.log(max_capacity / current_value) / math.log(1 + self.growth_rate)
        
        if days > days_ahead:
            return None
        
        return int(days)


# ==========================================
# 容量预测引擎
# ==========================================

class CapacityEngine:
    """
    容量预测引擎 v2.0
    
    支持:
    - 多模型自动选择 (Linear/Holt-Winters/SMA)
    - 存储空间预测
    - 连接数预测
    - 动态告警阈值
    """
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.models = {
            'linear': LinearRegressionModel(),
            'holt_winters': HoltWintersModel(period=MODEL_SELECTION['seasonality_period']),
            'sma': SimpleMovingAverageModel(window=7)
        }
    
    def get_historical_data(self, metric_name: str, days: int = 30) -> List[Dict]:
        """
        获取历史监控数据
        
        参数:
            metric_name: 指标名称 (如 'tablespace_used_pct', 'conn_usage_pct')
            days: 获取天数
        
        返回:
            [{'date': '2024-01-01', 'value': 75.5}, ...]
        """
        cutoff = datetime.now() - timedelta(days=days)
        
        logs = MonitorLog.objects.filter(
            config=self.config,
            create_time__gte=cutoff
        ).order_by('create_time')
        
        history = []
        for log in logs:
            try:
                data = json.loads(log.message)
                # 提取需要的指标值
                value = self._extract_metric_value(data, metric_name)
                if value is not None:
                    history.append({
                        'date': log.create_time.strftime('%Y-%m-%d'),
                        'timestamp': log.create_time.timestamp(),
                        'value': value
                    })
            except:
                pass
        
        return history
    
    def _extract_metric_value(self, data: Dict, metric_name: str) -> Optional[float]:
        """从监控数据中提取指定指标值"""
        # 表空间使用率
        if metric_name == 'tablespace_used_pct':
            if 'tablespaces' in data and data['tablespaces']:
                return max([t.get('used_pct', 0) for t in data['tablespaces']])
            return None
        
        # 连接使用率
        if metric_name == 'conn_usage_pct':
            return data.get('conn_usage_pct')
        
        # QPS
        if metric_name == 'qps':
            return data.get('qps')
        
        # 活跃连接数
        if metric_name == 'active_connections':
            return data.get('active_connections')
        
        return None
    
    def _prepare_training_data(self, history: List[Dict]) -> Tuple[List[float], List[float]]:
        """准备训练数据"""
        if not history:
            return [], []
        
        x = [h['timestamp'] for h in history]
        y = [h['value'] for h in history]
        
        # 归一化时间戳
        min_ts = min(x)
        x_normalized = [ts - min_ts for ts in x]
        
        return x_normalized, y
    
    def select_best_model(self, history: List[Dict]) -> str:
        """
        根据数据特征选择最优模型
        
        返回: 模型名称 ('linear', 'holt_winters', 'sma')
        """
        n = len(history)
        
        if n < MODEL_SELECTION['min_data_points_linear']:
            return 'sma'  # 数据太少，用简单平均
        
        if n < MODEL_SELECTION['min_data_points_holt_winters']:
            return 'linear'  # 数据量中等，用线性回归
        
        # 计算数据特征
        values = [h['value'] for h in history]
        
        # 检测趋势强度
        trend_strength = self._calculate_trend_strength(values)
        
        # 检测季节性强度
        seasonality_strength = self._calculate_seasonality(values)
        
        # 选择模型
        if seasonality_strength > 0.3 and n >= MODEL_SELECTION['min_data_points_holt_winters']:
            return 'holt_winters'
        elif trend_strength > 0.5:
            return 'linear'
        else:
            return 'sma'
    
    def _calculate_trend_strength(self, values: List[float]) -> float:
        """计算趋势强度 (0-1)"""
        if len(values) < 3:
            return 0.0
        
        # 简单方法: 比较前半部分和后半部分的平均值差异
        mid = len(values) // 2
        first_half = values[:mid]
        second_half = values[mid:]
        
        if not first_half or not second_half:
            return 0.0
        
        avg1 = sum(first_half) / len(first_half)
        avg2 = sum(second_half) / len(second_half)
        
        overall_avg = sum(values) / len(values)
        if overall_avg == 0:
            return 0.0
        
        return abs(avg2 - avg1) / overall_avg
    
    def _calculate_seasonality(self, values: List[float]) -> float:
        """计算季节性强度 (0-1)"""
        period = MODEL_SELECTION['seasonality_period']
        if len(values) < period * 2:
            return 0.0
        
        # 简化: 计算同一周期位置的值方差
        cycles = len(values) // period
        if cycles < 2:
            return 0.0
        
        position_avgs = []
        for pos in range(period):
            pos_values = [values[i * period + pos] for i in range(cycles) if i * period + pos < len(values)]
            if pos_values:
                position_avgs.append(sum(pos_values) / len(pos_values))
        
        if len(position_avgs) < 2:
            return 0.0
        
        overall_avg = sum(values) / len(values)
        if overall_avg == 0:
            return 0.0
        
        variance = sum((v - overall_avg) ** 2 for v in position_avgs) / len(position_avgs)
        return min(1.0, variance / (overall_avg ** 2))
    
    def predict(self, metric_name: str, days_ahead: int = 30) -> Dict[str, Any]:
        """
        预测指定指标的未来值
        
        参数:
            metric_name: 指标名称
            days_ahead: 预测天数
        
        返回:
            {
                'metric': 指标名,
                'current_value': 当前值,
                'predicted_value': 预测值 (days_ahead 天后),
                'model_used': 使用的模型,
                'confidence': 置信度,
                'days_to_threshold': 到达阈值天数,
                'forecast': [{'day': 1, 'value': 75.5}, ...]
            }
        """
        history = self.get_historical_data(metric_name, days=90)
        
        if len(history) < 2:
            return {
                'metric': metric_name,
                'error': '数据不足',
                'data_points': len(history)
            }
        
        # 选择最优模型
        best_model_name = self.select_best_model(history)
        model = self.models[best_model_name]
        
        # 准备数据
        x, y = self._prepare_training_data(history)
        
        # 训练模型
        model.fit(y)
        
        # 预测
        current_value = history[-1]['value']
        forecast = []
        
        if best_model_name == 'holt_winters':
            preds = model.predict(days_ahead)
            forecast = [{'day': i+1, 'value': round(preds[i], 2)} for i in range(len(preds))]
            predicted_value = preds[-1] if preds else current_value
        elif best_model_name == 'sma':
            forecast = [{'day': i+1, 'value': round(model.predict(i+1), 2)} for i in range(days_ahead)]
            predicted_value = forecast[-1]['value'] if forecast else current_value
        else:  # linear
            future_x = x[-1] + days_ahead * 86400  # 假设每天86400秒
            predicted_value = model.predict(future_x)
            forecast = [{'day': i+1, 'value': round(model.predict(x[-1] + (i+1) * 86400), 2)} for i in range(days_ahead)]
        
        # 计算置信度 (基于 R² 或模型特性)
        confidence = self._calculate_confidence(model, best_model_name)
        
        # 计算到达告警阈值的天数
        threshold = ALERT_THRESHOLDS['tablespace']['warning'] if 'tablespace' in metric_name else \
                    ALERT_THRESHOLDS['connection']['warning']
        
        days_to_threshold = None
        for f in forecast:
            if f['value'] >= threshold:
                days_to_threshold = f['day']
                break
        
        return {
            'metric': metric_name,
            'current_value': round(current_value, 2),
            'predicted_value': round(predicted_value, 2),
            'model_used': best_model_name,
            'confidence': round(confidence, 2),
            'days_to_threshold': days_to_threshold,
            'threshold_used': threshold,
            'forecast': forecast,
            'data_points': len(history)
        }
    
    def _calculate_confidence(self, model, model_name: str) -> float:
        """计算预测置信度"""
        if model_name == 'linear' and hasattr(model, 'r_squared'):
            return max(0, min(1, model.r_squared))
        
        # 简化: 基于模型复杂度
        if model_name == 'holt_winters':
            return 0.75
        elif model_name == 'linear':
            return 0.70
        else:
            return 0.60
    
    def analyze_all_metrics(self) -> Dict[str, Any]:
        """
        分析所有关键容量指标
        
        返回:
            {
                'timestamp': 分析时间,
                'metrics': {...},
                'alerts': [...],
                'summary': 总结
            }
        """
        results = {}
        alerts = []
        
        # 分析表空间使用率
        tbs_result = self.predict('tablespace_used_pct', days_ahead=30)
        results['tablespace'] = tbs_result
        
        if 'error' not in tbs_result:
            if tbs_result.get('days_to_threshold'):
                days = tbs_result['days_to_threshold']
                if days <= ALERT_THRESHOLDS['storage']['emergency_days']:
                    alerts.append({
                        'type': 'tablespace',
                        'severity': 'emergency',
                        'message': f"表空间使用率预计 {days} 天后达到告警阈值",
                        'current': tbs_result['current_value'],
                        'predicted': tbs_result['predicted_value']
                    })
                elif days <= ALERT_THRESHOLDS['storage']['critical_days']:
                    alerts.append({
                        'type': 'tablespace',
                        'severity': 'critical',
                        'message': f"表空间使用率预计 {days} 天后达到告警阈值",
                        'current': tbs_result['current_value'],
                        'predicted': tbs_result['predicted_value']
                    })
                elif days <= ALERT_THRESHOLDS['storage']['warning_days']:
                    alerts.append({
                        'type': 'tablespace',
                        'severity': 'warning',
                        'message': f"表空间使用率预计 {days} 天后达到告警阈值",
                        'current': tbs_result['current_value'],
                        'predicted': tbs_result['predicted_value']
                    })
        
        # 分析连接使用率
        conn_result = self.predict('conn_usage_pct', days_ahead=30)
        results['connection'] = conn_result
        
        if 'error' not in conn_result:
            if conn_result.get('days_to_threshold'):
                days = conn_result['days_to_threshold']
                if days <= 14:
                    alerts.append({
                        'type': 'connection',
                        'severity': 'critical' if days <= 7 else 'warning',
                        'message': f"连接使用率预计 {days} 天后达到告警阈值",
                        'current': conn_result['current_value'],
                        'predicted': conn_result['predicted_value']
                    })
        
        # 生成总结
        summary = self._generate_summary(results, alerts)
        
        return {
            'timestamp': datetime.now().isoformat(),
            'config_name': self.config.name,
            'db_type': self.config.db_type,
            'metrics': results,
            'alerts': alerts,
            'summary': summary
        }
    
    def _generate_summary(self, results: Dict, alerts: List) -> str:
        """生成分析总结"""
        if not alerts:
            return "✅ 容量预测正常，未检测到即将超限的指标。"
        
        parts = []
        emergency = [a for a in alerts if a['severity'] == 'emergency']
        critical = [a for a in alerts if a['severity'] == 'critical']
        warning = [a for a in alerts if a['severity'] == 'warning']
        
        if emergency:
            parts.append(f"🚨 {len(emergency)} 个紧急容量告警")
        if critical:
            parts.append(f"🔴 {len(critical)} 个严重容量告警")
        if warning:
            parts.append(f"🟠 {len(warning)} 个警告")
        
        return " | ".join(parts) if len(parts) <= 2 else "\n".join(parts)
    
    def get_recommended_threshold(self, metric_name: str) -> Dict[str, float]:
        """
        根据历史数据动态计算推荐告警阈值
        
        返回:
            {'warning': 75.0, 'critical': 90.0, ...}
        """
        history = self.get_historical_data(metric_name, days=30)
        
        if len(history) < 7:
            # 返回默认值
            if 'tablespace' in metric_name:
                return ALERT_THRESHOLDS['tablespace'].copy()
            return ALERT_THRESHOLDS['connection'].copy()
        
        values = [h['value'] for h in history]
        
        # 使用统计方法计算阈值
        # 简化: 使用 75th 和 90th 百分位数
        sorted_values = sorted(values)
        n = len(sorted_values)
        
        warning_pct = 0.75
        critical_pct = 0.90
        
        warning_idx = int(n * warning_pct)
        critical_idx = int(n * critical_pct)
        
        warning = sorted_values[min(warning_idx, n-1)]
        critical = sorted_values[min(critical_idx, n-1)]
        
        return {
            'warning': round(warning, 1),
            'critical': round(critical, 1)
        }


# ==========================================
# 使用示例
# ==========================================
"""
# 在 start_monitor.py 中集成容量预测:

from monitor.capacity_engine import CapacityEngine

# 定期执行容量分析 (每天一次)
def run_capacity_analysis():
    for config in DatabaseConfig.objects.filter(is_active=True):
        engine = CapacityEngine(config)
        report = engine.analyze_all_metrics()
        
        # 发送告警
        if report['alerts']:
            am = AlertManager(config)
            for alert in report['alerts']:
                am.fire(
                    alert_type='capacity',
                    metric_key=alert['type'],
                    title=f"{alert['severity'].upper()}: {alert['message']}",
                    description=f"当前值: {alert['current']}%\\n预测值: {alert['predicted']}%",
                    severity=alert['severity']
                )

# 获取单个指标的预测
result = engine.predict('tablespace_used_pct', days_ahead=30)
print(f"使用模型: {result['model_used']}")
print(f"当前值: {result['current_value']}%")
print(f"30天后预测: {result['predicted_value']}%")
print(f"到达阈值天数: {result['days_to_threshold']}")
"""