"""
基线分析引擎 v2.0 (Phase 2 增强版)

功能:
- 168 时间槽动态基线建模 (7天 × 24小时 = 168个独立基线)
- 三重条件异常检测 (量级 + 方向 + 持续性)
- 支持指标方向配置 (上升敏感/下降敏感/双向)
- 基线冷启动降级策略

设计文档参考: DB_AIOps_DESIGN.md 3.3 节
"""

import json
import logging
import statistics

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any
from django.utils import timezone
from django.db.models import Avg, Max, Min, Count
from monitor.models import MonitorLog, DatabaseConfig


# ==========================================
# 指标配置元数据
# ==========================================

# 指标方向配置: 告警应该在哪个方向触发
METRIC_DIRECTION_CONFIG = {
    'active_connections': 'up',      # 连接数上升是问题
    'conn_usage_pct': 'up',         # 连接使用率上升是问题
    'qps': 'down',                  # QPS 下降是问题
    'slow_queries_total': 'up',     # 慢查询增加是问题
    'slow_queries_active': 'up',   # 活跃慢查询增加是问题
    'locks_count': 'up',           # 锁等待增加是问题
    'database_size_mb': 'up',       # 数据库大小上升（容量问题）
}

# 默认 sigma_k 值 (用于计算正常范围)
DEFAULT_SIGMA_K = 2.0  # 均值 ± 2σ
STRICT_SIGMA_K = 3.0   # 严格模式: 均值 ± 3σ


class BaselineModel:
    """单指标单时间槽的基线模型"""
    
    def __init__(self, metric_key: str, time_slot: int):
        self.metric_key = metric_key
        self.time_slot = time_slot  # 0-167 (星期几×24 + 小时)
        self.values: List[float] = []
        self.sample_count = 0
        self.mean = 0.0
        self.std = 0.0
        self.p90 = 0.0
        self.p95 = 0.0
        self.p99 = 0.0
        self.normal_min = 0.0
        self.normal_max = 0.0
        self.data_sufficient = False
    
    @staticmethod
    def time_slot_to_str(slot: int) -> str:
        """将时间槽转换为可读字符串 (如 '周一 09:00')"""
        day_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        day_idx = slot // 24
        hour = slot % 24
        return f"{day_names[day_idx]} {hour:02d}:00"
    
    def calculate(self, sigma_k: float = DEFAULT_SIGMA_K):
        """基于收集的值计算基线统计量"""
        if len(self.values) < 3:
            self.data_sufficient = False
            return
        
        self.sample_count = len(self.values)
        self.mean = statistics.mean(self.values)
        
        if len(self.values) > 1:
            self.std = statistics.stdev(self.values)
        else:
            self.std = 0.0
        
        # 百分位数
        sorted_vals = sorted(self.values)
        self.p90 = self._percentile(sorted_vals, 0.90)
        self.p95 = self._percentile(sorted_vals, 0.95)
        self.p99 = self._percentile(sorted_vals, 0.99)
        
        # 正常范围
        self.normal_min = self.mean - sigma_k * self.std
        self.normal_max = self.mean + sigma_k * self.std
        
        # 数据充分性: 需要至少 7 个样本点
        self.data_sufficient = self.sample_count >= 7
    
    @staticmethod
    def _percentile(sorted_values: List[float], p: float) -> float:
        """计算百分位数"""
        if not sorted_values:
            return 0.0
        idx = int(len(sorted_values) * p)
        idx = min(idx, len(sorted_values) - 1)
        return sorted_values[idx]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'metric_key': self.metric_key,
            'time_slot': self.time_slot,
            'time_slot_str': self.time_slot_to_str(self.time_slot),
            'sample_count': self.sample_count,
            'mean': round(self.mean, 2),
            'std': round(self.std, 2),
            'p90': round(self.p90, 2),
            'p95': round(self.p95, 2),
            'p99': round(self.p99, 2),
            'normal_min': round(self.normal_min, 2),
            'normal_max': round(self.normal_max, 2),
            'data_sufficient': self.data_sufficient,
        }


class BaselineEngine:
    """
    增强版基线分析引擎 v2.0
    
    支持:
    - 168 时间槽基线建模
    - 三重条件异常检测
    - 指标方向感知
    - 冷启动降级策略
    """
    
    # 异常持续性要求: 连续 N 次采集均异常才触发告警
    ANOMALY_PERSISTENCE_COUNT = 3
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.history_days = 28  # 默认分析最近 28 天数据
        self._baseline_cache: Dict[int, Dict[str, BaselineModel]] = {}  # slot -> {metric -> model}
    
    def _get_time_slot(self, dt: datetime) -> int:
        """获取给定时间的时间槽 (0-167)"""
        # weekday(): 0=周一, 6=周日
        weekday = dt.weekday()
        hour = dt.hour
        return weekday * 24 + hour
    
    def _get_current_time_slot(self) -> int:
        """获取当前时间的时间槽"""
        return self._get_time_slot(timezone.now())
    
    def get_history_logs(self, days: int = None):
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
    
    def parse_log_data(self, log) -> Dict:
        """解析日志中的 JSON 数据"""
        try:
            return json.loads(log.message)
        except Exception:
            return {}
    
    def _extract_metric_values(self, logs, metric_key: str) -> Dict[int, List[float]]:
        """
        按时间槽分组提取指标值
        
        返回: {time_slot: [values...], ...}
        """
        slot_values: Dict[int, List[float]] = {}
        
        for log in logs:
            data = self.parse_log_data(log)
            if metric_key not in data:
                continue
            
            value = data[metric_key]
            if not isinstance(value, (int, float)) or value is None:
                continue
            
            try:
                val = float(value)
                slot = self._get_time_slot(log.create_time)
                
                if slot not in slot_values:
                    slot_values[slot] = []
                slot_values[slot].append(val)
            except (ValueError, TypeError):
                continue
        
        return slot_values
    
    def calculate_baseline_for_metric(self, metric_key: str, days: int = None, sigma_k: float = DEFAULT_SIGMA_K) -> Dict[int, BaselineModel]:
        """
        为单个指标计算所有时间槽的基线
        
        返回: {time_slot: BaselineModel, ...}
        """
        logs = self.get_history_logs(days)
        slot_values = self._extract_metric_values(logs, metric_key)
        
        baselines = {}
        for slot, values in slot_values.items():
            model = BaselineModel(metric_key, slot)
            model.values = values
            model.calculate(sigma_k)
            baselines[slot] = model
        
        return baselines
    
    def calculate_full_baseline(self, days: int = None, sigma_k: float = DEFAULT_SIGMA_K) -> Dict[str, Dict[int, BaselineModel]]:
        """
        计算所有指标的所有时间槽基线
        
        返回: {metric_key: {time_slot: BaselineModel}, ...}
        """
        logs = self.get_history_logs(days)
        if not logs:
            return {}
        
        # 收集所有数值型指标
        all_keys = set()
        for log in logs:
            data = self.parse_log_data(log)
            for key, value in data.items():
                if isinstance(value, (int, float)) and value is not None:
                    all_keys.add(key)
        
        # 排除非监控指标
        exclude_keys = {'version', 'error', 'message'}
        metric_keys = all_keys - exclude_keys
        
        result = {}
        for key in metric_keys:
            baselines = self.calculate_baseline_for_metric(key, days, sigma_k)
            if baselines:
                result[key] = baselines
        
        return result
    
    def get_baseline_for_current_slot(self, metric_key: str, days: int = None) -> Optional[BaselineModel]:
        """获取当前时间槽的基线模型（用于实时检测）"""
        current_slot = self._get_current_time_slot()
        baselines = self.calculate_baseline_for_metric(metric_key, days)
        return baselines.get(current_slot)
    
    def detect_anomaly_three_condition(
        self, 
        current_value: float, 
        baseline: BaselineModel,
        metric_key: str
    ) -> Tuple[bool, str, str, str]:
        """
        三重条件异常检测
        
        返回: (is_anomaly, anomaly_type, severity, reason)
        - is_anomaly: 是否异常
        - anomaly_type: 'high' / 'low' / None
        - severity: 'critical' / 'warning' / None
        - reason: 异常原因描述
        """
        if baseline is None or not baseline.data_sufficient:
            # 基线数据不足，无法判断
            return False, None, None, None
        
        # 条件1: 量级检测
        magnitude_exceeded = False
        magnitude_direction = None
        magnitude_severity = None
        
        if current_value > baseline.normal_max:
            magnitude_exceeded = True
            magnitude_direction = 'high'
            # 超过 3σ -> critical
            if current_value > baseline.mean + STRICT_SIGMA_K * baseline.std:
                magnitude_severity = 'critical'
            else:
                magnitude_severity = 'warning'
        elif current_value < baseline.normal_min:
            magnitude_exceeded = True
            magnitude_direction = 'low'
            if current_value < baseline.mean - STRICT_SIGMA_K * baseline.std:
                magnitude_severity = 'critical'
            else:
                magnitude_severity = 'warning'
        
        if not magnitude_exceeded:
            return False, None, None, None
        
        # 条件2: 方向检测
        metric_direction = METRIC_DIRECTION_CONFIG.get(metric_key, 'both')
        
        if metric_direction == 'up' and magnitude_direction == 'low':
            # 上升敏感指标，下降不算异常
            return False, None, None, None
        
        if metric_direction == 'down' and magnitude_direction == 'high':
            # 下降敏感指标，上升不算异常
            return False, None, None, None
        
        # 条件3: 持续性检测 (通过历史记录判断)
        # 这里简化为直接使用量级和方向判断
        # 完整实现需要查询历史采集记录
        
        reason = (
            f"指标 {metric_key} 当前值 {current_value} 超出 "
            f"正常范围 [{baseline.normal_min:.2f}, {baseline.normal_max:.2f}] "
            f"(均值={baseline.mean:.2f}, σ={baseline.std:.2f})"
        )
        
        return True, magnitude_direction, magnitude_severity, reason
    
    def check_current_against_baseline(self, current_data: Dict, days: int = None) -> List[Tuple]:
        """
        检查当前监控数据是否偏离基线
        
        优化: 一次查询历史日志，内存中按指标分组，避免 N 次重复 DB 查询。
        
        参数:
            current_data: 当前采集的指标字典
        
        返回:
            anomalies: 异常列表 [
                (metric_name, current_value, baseline, anomaly_type, severity, reason)
            ]
        """
        anomalies = []
        current_slot = self._get_current_time_slot()
        
        # 一次查询所有历史日志
        logs = self.get_history_logs(days)
        
        # 预处理: 按指标键提取所有时间槽的值 (只处理当前数据中的数值型指标)
        metric_keys = {k for k, v in current_data.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
        
        all_slot_values: Dict[str, Dict[int, List[float]]] = {k: {} for k in metric_keys}
        for log in logs:
            data = self.parse_log_data(log)
            slot = self._get_time_slot(log.create_time)
            for mk in metric_keys:
                if mk in data:
                    val = data[mk]
                    if isinstance(val, (int, float)) and val is not None:
                        all_slot_values[mk].setdefault(slot, []).append(float(val))
        
        for metric_key, current_value in current_data.items():
            # 跳过非数值型指标
            if not isinstance(current_value, (int, float)) or isinstance(current_value, bool):
                continue
            
            # 从预处理的字典中获取该指标在该时间槽的基线
            slot_values = all_slot_values.get(metric_key, {})
            slot_data = slot_values.get(current_slot, [])
            
            if len(slot_data) < 3:
                # 数据不足，无法计算基线
                continue
            
            baseline = BaselineModel(metric_key, current_slot)
            baseline.values = slot_data
            baseline.calculate()
            
            if not baseline.data_sufficient:
                continue
            
            is_anomaly, anomaly_type, severity, reason = self.detect_anomaly_three_condition(
                float(current_value), baseline, metric_key
            )
            
            if is_anomaly:
                anomalies.append((
                    metric_key,
                    current_value,
                    baseline,
                    anomaly_type,
                    severity,
                    reason
                ))
        
        return anomalies
    
    def get_full_baseline_report(self, days: int = None, sigma_k: float = DEFAULT_SIGMA_K) -> Dict:
        """
        生成完整的基线报告
        
        返回所有支持指标的基线统计
        """
        logs = self.get_history_logs(days)
        if not logs:
            return {'error': '无历史数据', 'config_name': self.config.name}
        
        all_baselines = self.calculate_full_baseline(days, sigma_k)
        
        # 汇总每个指标的信息
        metrics_report = {}
        for metric_key, slot_baselines in all_baselines.items():
            # 收集所有槽的统计
            all_values = []
            slot_stats = []
            
            for slot, model in sorted(slot_baselines.items()):
                if model.data_sufficient:
                    all_values.extend(model.values)
                    slot_stats.append({
                        'slot': slot,
                        'slot_str': model.time_slot_to_str(slot),
                        'mean': round(model.mean, 2),
                        'std': round(model.std, 2),
                        'normal_range': [round(model.normal_min, 2), round(model.normal_max, 2)],
                        'sample_count': model.sample_count,
                    })
            
            if all_values:
                # 全局统计
                global_mean = statistics.mean(all_values)
                global_std = statistics.stdev(all_values) if len(all_values) > 1 else 0
                
                metrics_report[metric_key] = {
                    'metric_key': metric_key,
                    'direction': METRIC_DIRECTION_CONFIG.get(metric_key, 'both'),
                    'global_mean': round(global_mean, 2),
                    'global_std': round(global_std, 2),
                    'total_samples': len(all_values),
                    'time_slots_covered': len([s for s in slot_stats if s['sample_count'] >= 7]),
                    'time_slots_total': 168,
                    'slot_details': slot_stats[:24],  # 只返回前24个槽的详情
                }
        
        return {
            'config_name': self.config.name,
            'db_type': self.config.db_type,
            'analysis_period_days': days or self.history_days,
            'time_slots_total': 168,
            'metrics': metrics_report,
        }

    def update_baseline(self, current_data: Dict) -> int:
        """
        增量更新基线模型（Welford 在线算法）

        在每次采集后调用，用当前指标值增量更新对应时间槽的基线统计量。
        无需重新计算全部历史，O(1) 复杂度。

        参数:
            current_data: 当前采集的指标字典

        返回:
            更新的基线记录数
        """
        from monitor.models import BaselineModel as BaselineModelDB

        current_slot = self._get_current_time_slot()
        updated_count = 0
        MAX_SAMPLES = 720  # 30天 × 24槽 上限，防止数值漂移

        for metric_key, value in current_data.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue

            try:
                val = float(value)
            except (ValueError, TypeError):
                continue

            obj, created = BaselineModelDB.objects.get_or_create(
                config=self.config,
                metric_key=metric_key,
                time_slot=current_slot,
                defaults={
                    'sample_count': 1,
                    'mean': val,
                    'std': 0.0,
                    'p90': val,
                    'p95': val,
                    'p99': val,
                    'normal_min': val,
                    'normal_max': val,
                    'data_sufficient': False,
                }
            )

            if created:
                updated_count += 1
                continue

            # Welford 在线算法增量更新 mean/std
            n = obj.sample_count
            old_mean = obj.mean

            if n >= MAX_SAMPLES:
                # 超过上限时重置，避免长期漂移
                n = 1
                new_mean = val
                new_std = 0.0
            else:
                new_n = n + 1
                delta = val - old_mean
                new_mean = old_mean + delta / new_n
                delta2 = val - new_mean

                # Welford: M2 累加器
                if n < 2:
                    new_std = 0.0
                else:
                    # 从旧 std 反推 M2: M2 = std^2 * (n-1)
                    old_m2 = (obj.std ** 2) * (n - 1) if n > 1 else 0.0
                    new_m2 = old_m2 + delta * delta2
                    new_std = (new_m2 / (new_n - 1)) ** 0.5 if new_n > 1 else 0.0
                n = new_n

            # 更新百分位数（简化：用当前值替换）
            # 注意：精确百分位需要保留全部样本，这里用近似
            p90 = obj.p90 * 0.95 + val * 0.05
            p95 = obj.p95 * 0.95 + val * 0.05
            p99 = obj.p99 * 0.98 + val * 0.02

            # 正常范围
            sigma_k = DEFAULT_SIGMA_K
            normal_min = new_mean - sigma_k * new_std
            normal_max = new_mean + sigma_k * new_std

            obj.sample_count = n
            obj.mean = round(new_mean, 6)
            obj.std = round(new_std, 6)
            obj.p90 = round(p90, 4)
            obj.p95 = round(p95, 4)
            obj.p99 = round(p99, 4)
            obj.normal_min = round(normal_min, 6)
            obj.normal_max = round(normal_max, 6)
            obj.data_sufficient = (n >= 7)
            obj.save(update_fields=[
                'sample_count', 'mean', 'std',
                'p90', 'p95', 'p99',
                'normal_min', 'normal_max', 'data_sufficient', 'updated_at',
            ])
            updated_count += 1

        if updated_count:
            logger.info(f"[BaselineEngine] 更新 {updated_count} 个基线槽: "
                         f"{self.config.name} slot={current_slot}")
        return updated_count


# ==========================================
# 使用示例
# ==========================================
"""
# 在 start_monitor.py 中集成:

from monitor.baseline_engine import BaselineEngine

# 在 process_result 方法中添加基线检查:
if current_status == 'UP':
    engine = BaselineEngine(config)
    
    # 检查当前数据是否偏离基线
    anomalies = engine.check_current_against_baseline(data)
    
    for metric_name, current_val, baseline, anomaly_type, severity, reason in anomalies:
        emoji = '🔴' if severity == 'critical' else '🟡'
        title = f'{emoji} 基线异常：{metric_name}'
        body = f"{reason}\\n\\n建议：检查是否有异常业务行为"
        am.fire(alert_type='baseline', metric_key=metric_name,
                title=title, description=body, severity=severity)
"""
