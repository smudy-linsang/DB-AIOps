"""
机器学习异常检测模块
====================

基于时序预测的异常检测：
- ARIMA 模型用于时序预测
- 统计方法异常检测 (Z-score, IQR)
- 异常点标记和评分

Author: DB-AIOps Team
"""

import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
import logging

logger = logging.getLogger(__name__)


class TimeSeriesForecaster:
    """
    时序预测器基类
    
    支持多种预测模型：
    - 简单移动平均 (SMA)
    - 指数平滑 (EMA)
    - ARIMA (需安装 statsmodels)
    """
    
    def __init__(self, model_type: str = 'sma', window: int = 7):
        """
        初始化预测器
        
        Args:
            model_type: 模型类型 ('sma', 'ema', 'arima')
            window: 滑动窗口大小
        """
        self.model_type = model_type
        self.window = window
        self.history = []
        self.fitted = False
        
        # EMA 参数
        self.alpha = 2.0 / (window + 1) if window > 0 else 0.3
        
        # ARIMA 参数
        self.arima_model = None
        self.arima_order = (5, 1, 0)
    
    def fit(self, data: List[float]) -> bool:
        """
        训练模型
        
        Args:
            data: 历史数据
            
        Returns:
            是否训练成功
        """
        if len(data) < self.window:
            logger.warning(f"数据量不足，需要至少 {self.window} 个点")
            return False
        
        self.history = list(data)
        
        if self.model_type == 'arima':
            return self._fit_arima(data)
        
        self.fitted = True
        return True
    
    def _fit_arima(self, data: List[float]) -> bool:
        """训练 ARIMA 模型"""
        try:
            from statsmodels.tsa.arima.model import ARIMA
            
            # 限制数据量以加快训练速度
            train_data = data[-min(len(data), 200):]
            
            self.arima_model = ARIMA(train_data, order=self.arima_order)
            self.arima_fitted = self.arima_model.fit()
            self.fitted = True
            return True
        except ImportError:
            logger.warning("statsmodels 未安装，ARIMA 模型不可用")
            self.model_type = 'ema'
            return self.fit(data)
        except Exception as e:
            logger.error(f"ARIMA 训练失败: {e}")
            self.model_type = 'ema'
            return self.fit(data)
    
    def predict(self, steps: int = 1) -> List[float]:
        """
        预测未来值
        
        Args:
            steps: 预测步数
            
        Returns:
            预测值列表
        """
        if not self.fitted:
            return []
        
        if self.model_type == 'sma':
            return self._predict_sma(steps)
        elif self.model_type == 'ema':
            return self._predict_ema(steps)
        elif self.model_type == 'arima':
            return self._predict_arima(steps)
        
        return []
    
    def _predict_sma(self, steps: int) -> List[float]:
        """简单移动平均预测"""
        predictions = []
        current_window = self.history[-self.window:]
        last_value = np.mean(current_window)
        
        for _ in range(steps):
            predictions.append(last_value)
            # 更新窗口
            current_window = current_window[1:] + [last_value]
            last_value = np.mean(current_window)
        
        return predictions
    
    def _predict_ema(self, steps: int) -> List[float]:
        """指数平滑预测"""
        predictions = []
        last_value = self.history[-1] if self.history else 0
        
        for _ in range(steps):
            predictions.append(last_value)
        
        return predictions
    
    def _predict_arima(self, steps: int) -> List[float]:
        """ARIMA 预测"""
        try:
            forecast = self.arima_fitted.forecast(steps=steps)
            return forecast.tolist()
        except:
            return self._predict_ema(steps)
    
    def predict_next(self) -> float:
        """预测下一个值"""
        predictions = self.predict(1)
        return predictions[0] if predictions else 0.0


class AnomalyDetector:
    """
    异常检测器
    
    支持多种检测方法：
    - Z-score (标准分数)
    - IQR (四分位距)
    - 预测误差
    """
    
    def __init__(
        self,
        method: str = 'zscore',
        threshold: float = 3.0,
        window: int = 100
    ):
        """
        初始化异常检测器
        
        Args:
            method: 检测方法 ('zscore', 'iqr', 'prediction')
            threshold: 阈值
            window: 参考窗口大小
        """
        self.method = method
        self.threshold = threshold
        self.window = window
        self.baseline_history = deque(maxlen=window)
        self.forecaster = TimeSeriesForecaster(model_type='sma', window=7)
        
        # 统计量
        self.mean = 0.0
        self.std = 0.0
        self.q1 = 0.0
        self.q3 = 0.0
        self.iqr = 0.0
    
    def update_baseline(self, value: float) -> None:
        """更新基线统计"""
        self.baseline_history.append(value)
        self._update_statistics()
    
    def _update_statistics(self) -> None:
        """更新统计量"""
        if len(self.baseline_history) < 10:
            return
        
        data = list(self.baseline_history)
        self.mean = np.mean(data)
        self.std = np.std(data)
        
        sorted_data = np.sort(data)
        n = len(sorted_data)
        self.q1 = sorted_data[n // 4]
        self.q3 = sorted_data[3 * n // 4]
        self.iqr = self.q3 - self.q1
    
    def detect(self, value: float) -> Dict[str, Any]:
        """
        检测异常
        
        Args:
            value: 待检测的值
            
        Returns:
            检测结果字典
        """
        result = {
            'is_anomaly': False,
            'score': 0.0,
            'method': self.method,
            'value': value,
            'z_score': 0.0,
            'deviation': 0.0
        }
        
        if len(self.baseline_history) < 10:
            result['status'] = 'insufficient_data'
            return result
        
        if self.method == 'zscore':
            return self._detect_zscore(value, result)
        elif self.method == 'iqr':
            return self._detect_iqr(value, result)
        elif self.method == 'prediction':
            return self._detect_prediction(value, result)
        
        return result
    
    def _detect_zscore(self, value: float, result: Dict) -> Dict:
        """Z-score 异常检测"""
        if self.std > 0:
            z_score = abs((value - self.mean) / self.std)
            result['z_score'] = z_score
            result['score'] = z_score
            result['deviation'] = abs(value - self.mean)
            result['is_anomaly'] = z_score > self.threshold
        else:
            result['status'] = 'no_variance'
        
        return result
    
    def _detect_iqr(self, value: float, result: Dict) -> Dict:
        """IQR 异常检测"""
        lower_bound = self.q1 - 1.5 * self.iqr
        upper_bound = self.q3 + 1.5 * self.iqr
        
        result['lower_bound'] = lower_bound
        result['upper_bound'] = upper_bound
        
        if value < lower_bound:
            result['is_anomaly'] = True
            result['score'] = (lower_bound - value) / self.iqr if self.iqr > 0 else 0
            result['direction'] = 'below'
        elif value > upper_bound:
            result['is_anomaly'] = True
            result['score'] = (value - upper_bound) / self.iqr if self.iqr > 0 else 0
            result['direction'] = 'above'
        else:
            result['score'] = 0
        
        return result
    
    def _detect_prediction(self, value: float, result: Dict) -> Dict:
        """基于预测的异常检测"""
        # 训练预测器
        if len(self.baseline_history) >= 20:
            self.forecaster.fit(list(self.baseline_history))
            predicted = self.forecaster.predict_next()
            
            # 计算误差
            error = abs(value - predicted)
            # 使用 MAPE 风格的分数
            if predicted != 0:
                score = error / abs(predicted) * 100
            else:
                score = error
            
            result['predicted'] = predicted
            result['error'] = error
            result['score'] = score
            result['is_anomaly'] = score > self.threshold * 20  # 放宽阈值
            result['deviation'] = error
        
        return result
    
    def batch_detect(self, values: List[float]) -> List[Dict[str, Any]]:
        """
        批量检测
        
        Args:
            values: 值列表
            
        Returns:
            检测结果列表
        """
        results = []
        for value in values:
            result = self.detect(value)
            results.append(result)
            self.update_baseline(value)
        
        return results


class AdaptiveThresholdDetector:
    """
    自适应阈值异常检测器
    
    特点：
    - 动态调整阈值
    - 考虑时间因素（白天/晚上不同阈值）
    - 趋势感知
    """
    
    def __init__(
        self,
        base_threshold: float = 2.5,
        sensitivity: float = 1.0,
        time_window: int = 168  # 一周数据
    ):
        """
        初始化
        
        Args:
            base_threshold: 基础阈值
            sensitivity: 灵敏度 (0.1 - 2.0)
            time_window: 时间窗口大小
        """
        self.base_threshold = base_threshold
        self.sensitivity = sensitivity
        self.time_window = time_window
        
        self.history = deque(maxlen=time_window)
        self.trend = 0.0  # 0: 稳定, 1: 上升, -1: 下降
        self.volatility = 1.0
    
    def update(self, value: float) -> None:
        """更新历史数据"""
        self.history.append(value)
        self._analyze_trend()
    
    def _analyze_trend(self) -> None:
        """分析趋势"""
        if len(self.history) < 24:
            return
        
        recent = list(self.history)[-24:]
        older = list(self.history)[-48:-24] if len(self.history) >= 48 else recent
        
        recent_mean = np.mean(recent)
        older_mean = np.mean(older)
        
        if older_mean != 0:
            change_rate = (recent_mean - older_mean) / older_mean
            
            if change_rate > 0.05:
                self.trend = 1.0
            elif change_rate < -0.05:
                self.trend = -1.0
            else:
                self.trend = 0.0
        
        # 计算波动性
        if len(self.history) >= 24:
            self.volatility = np.std(list(self.history)[-24:]) + 0.001
    
    def get_threshold(self) -> float:
        """获取当前阈值"""
        # 根据趋势和波动性调整阈值
        trend_factor = 1.0 + self.trend * 0.2
        volatility_factor = 1.0 / (self.volatility * 10)
        
        threshold = self.base_threshold * trend_factor * volatility_factor * (2.0 - self.sensitivity)
        
        return max(1.5, min(threshold, 5.0))  # 限制在合理范围
    
    def detect(self, value: float) -> Dict[str, Any]:
        """
        检测异常
        
        Returns:
            检测结果
        """
        if len(self.history) < 10:
            return {
                'is_anomaly': False,
                'status': 'warming_up',
                'threshold': self.base_threshold,
                'value': value
            }
        
        recent = list(self.history)[-min(len(self.history), 100):]
        mean = np.mean(recent)
        std = np.std(recent)
        
        if std == 0:
            std = 0.001
        
        z_score = abs((value - mean) / std)
        threshold = self.get_threshold()
        
        result = {
            'is_anomaly': z_score > threshold,
            'z_score': z_score,
            'threshold': threshold,
            'mean': mean,
            'std': std,
            'trend': self.trend,
            'value': value
        }
        
        return result


class AnomalyScoreCalculator:
    """
    异常评分计算器
    
    计算综合异常分数 (0-100)
    """
    
    @staticmethod
    def calculate_score(
        z_score: float,
        deviation_percent: float,
        persistence: int = 1,
        trend_mismatch: bool = False
    ) -> Tuple[float, str]:
        """
        计算异常分数
        
        Args:
            z_score: Z分数
            deviation_percent: 偏差百分比
            persistence: 持续次数
            trend_mismatch: 是否与趋势不匹配
            
        Returns:
            (分数, 等级)
        """
        # 基础分数
        score = min(100, z_score * 25)
        
        # 偏差加成
        if deviation_percent > 50:
            score += 10
        elif deviation_percent > 100:
            score += 20
        
        # 持续性加成
        if persistence > 1:
            score += min(15, persistence * 5)
        
        # 趋势不匹配加成
        if trend_mismatch:
            score += 15
        
        score = min(100, score)
        
        # 等级
        if score >= 80:
            level = 'critical'
        elif score >= 60:
            level = 'warning'
        elif score >= 40:
            level = 'info'
        else:
            level = 'normal'
        
        return score, level


def quick_anomaly_detect(values: List[float], threshold: float = 3.0) -> List[bool]:
    """
    快速异常检测
    
    Args:
        values: 数据列表
        threshold: Z-score 阈值
        
    Returns:
        异常标记列表
    """
    if len(values) < 10:
        return [False] * len(values)
    
    mean = np.mean(values)
    std = np.std(values)
    
    if std == 0:
        return [False] * len(values)
    
    z_scores = np.abs((np.array(values) - mean) / std)
    return (z_scores > threshold).tolist()


def detect_change_points(values: List[float], window: int = 20) -> List[int]:
    """
    检测变化点
    
    Args:
        values: 时间序列数据
        window: 滑动窗口大小
        
    Returns:
        变化点索引列表
    """
    if len(values) < window * 2:
        return []
    
    change_points = []
    
    for i in range(window, len(values) - window):
        before = values[i-window:i]
        after = values[i:i+window]
        
        before_mean = np.mean(before)
        after_mean = np.mean(after)
        
        # 检测显著变化
        if before_mean != 0:
            change_rate = abs(after_mean - before_mean) / before_mean
            if change_rate > 0.3:  # 30% 变化
                change_points.append(i)
    
    return change_points
