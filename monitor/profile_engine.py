"""
资源使用特征画像引擎 (Profile Engine)
==========================================

Phase 3.4 核心模块 - 各库资源使用特征画像（负载类型识别、高峰时段识别）

功能：
1. 负载类型分类（OLTP/OLAP/HTAP/Mixed）
2. 高峰时段分析（工作日高峰、节假日模式）
3. 资源使用模式（CPU密集型/IO密集型/读写比例）
4. 周期性模式识别（日周期、周周期）
5. 画像报告生成

Author: DB-AIOps Team
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Literal
import numpy as np


class LoadType(str, Enum):
    """负载类型枚举"""
    OLTP = "oltp"          # 交易型 - 高QPS、低延迟、小事务
    OLAP = "olap"          # 分析型 - 低QPS、高延迟、复杂查询
    HTAP = "htap"          # 混合型 - 同时支持事务和分析
    MIXED = "mixed"         # 混合负载
    UNKNOWN = "unknown"


class PeakPattern(str, Enum):
    """高峰时段模式"""
    DAYTIME = "daytime"        # 工作时段高峰（9-18点）
    NIGHT = "night"            # 夜间高峰（批处理作业）
    BUSINESS_CYCLE = "business" # 业务周期高峰
    FLAT = "flat"              # 平坦型（无明显高峰）
    IRREGULAR = "irregular"    # 不规则


class ResourcePattern(str, Enum):
    """资源使用模式"""
    CPU_BOUND = "cpu_bound"        # CPU密集型
    IO_BOUND = "io_bound"          # IO密集型
    READ_HEAVY = "read_heavy"      # 读密集型
    WRITE_HEAVY = "write_heavy"    # 写密集型
    BALANCED = "balanced"          # 读写平衡


class WorkDayPattern(str, Enum):
    """工作日模式"""
    WEEKDAY_HEAVY = "weekday_heavy"    # 工作日繁忙
    WEEKEND_HEAVY = "weekend_heavy"    # 周末繁忙
    EVERYDAY_SIMILAR = "everyday_similar"  # 每天相似


@dataclass
class LoadTypeFeatures:
    """负载类型特征"""
    qps_avg: float = 0.0
    qps_max: float = 0.0
    qps_std: float = 0.0
    query_latency_avg: float = 0.0      # 平均查询延迟(ms)
    query_latency_p99: float = 0.0
    transaction_size_avg: float = 0.0   # 平均事务大小
    read_ratio: float = 0.0            # 读操作比例
    write_ratio: float = 0.0           # 写操作比例
    batch_query_ratio: float = 0.0     # 批量查询比例
    connection_usage_avg: float = 0.0   # 平均连接使用率


@dataclass
class PeakHoursProfile:
    """高峰时段画像"""
    pattern: PeakPattern = PeakPattern.FLAT
    peak_hour_start: int = 0           # 高峰开始小时(0-23)
    peak_hour_end: int = 0              # 高峰结束小时
    peak_day_of_week: list[int] = field(default_factory=list)  # 高峰日(0=周一)
    peak_intensity: float = 0.0         # 高峰强度(相对于均值)
    off_peak_hours: list[int] = field(default_factory=list)    # 低谷时段
    has_night_batch: bool = False       # 是否有夜间批处理
    night_batch_start: int = 0          # 批处理开始时间
    night_batch_end: int = 0            # 批处理结束时间
    description: str = ""


@dataclass
class ResourcePatternProfile:
    """资源使用模式画像"""
    pattern: ResourcePattern = ResourcePattern.BALANCED
    cpu_intensity: float = 0.0          # CPU强度(0-1)
    io_intensity: float = 0.0           # IO强度(0-1)
    memory_pressure: float = 0.0        # 内存压力(0-1)
    read_write_ratio: float = 1.0       # 读写比
    lock_contention_level: float = 0.0   # 锁竞争程度(0-1)
    network_io_rate: float = 0.0         # 网络IO速率(MB/s)
    description: str = ""


@dataclass
class WeeklyPattern:
    """周模式"""
    pattern: WorkDayPattern = WorkDayPattern.EVERYDAY_SIMILAR
    weekday_avg_load: float = 0.0
    weekend_avg_load: float = 0.0
    monday_load: float = 0.0
    tuesday_load: float = 0.0
    wednesday_load: float = 0.0
    thursday_load: float = 0.0
    friday_load: float = 0.0
    saturday_load: float = 0.0
    sunday_load: float = 0.0


@dataclass
class DatabaseProfile:
    """数据库完整画像"""
    db_config_id: int = 0
    db_name: str = ""
    db_type: str = ""
    generated_at: datetime = field(default_factory=datetime.now)
    
    # 负载类型
    load_type: LoadType = LoadType.UNKNOWN
    load_type_confidence: float = 0.0   # 分类置信度
    load_features: LoadTypeFeatures = field(default_factory=LoadTypeFeatures)
    
    # 高峰时段
    peak_hours: PeakHoursProfile = field(default_factory=PeakHoursProfile)
    
    # 资源模式
    resource_pattern: ResourcePatternProfile = field(default_factory=ResourcePatternProfile)
    
    # 周模式
    weekly_pattern: WeeklyPattern = field(default_factory=WeeklyPattern)
    
    # 综合描述
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)


class LoadTypeClassifier:
    """负载类型分类器"""
    
    # OLTP特征阈值
    OLTP_QPS_MIN = 50.0           # OLTP最小QPS
    OLTP_LATENCY_MAX = 100.0      # OLTP最大延迟(ms)
    OLTP_READ_RATIO_MIN = 0.7     # OLTP读比例最小值
    OLTP_TRANS_SIZE_MAX = 10.0    # OLTP最大事务大小(rows)
    
    # OLAP特征阈值
    OLAP_QPS_MAX = 20.0           # OLAP最大QPS
    OLAP_LATENCY_MIN = 500.0      # OLAP最小延迟(ms)
    OLAP_BATCH_RATIO_MIN = 0.3   # OLAP批量查询比例最小值
    
    @classmethod
    def classify(cls, features: LoadTypeFeatures) -> tuple[LoadType, float]:
        """
        根据特征分类负载类型
        返回: (负载类型, 置信度)
        """
        scores = {
            LoadType.OLTP: 0.0,
            LoadType.OLAP: 0.0,
            LoadType.HTAP: 0.0,
            LoadType.MIXED: 0.0
        }
        
        # OLTP评分
        if features.qps_avg >= cls.OLTP_QPS_MIN:
            scores[LoadType.OLTP] += 0.3
        if features.query_latency_avg <= cls.OLTP_LATENCY_MAX:
            scores[LoadType.OLTP] += 0.3
        if features.read_ratio >= cls.OLTP_READ_RATIO_MIN:
            scores[LoadType.OLTP] += 0.2
        if features.transaction_size_avg <= cls.OLTP_TRANS_SIZE_MAX:
            scores[LoadType.OLTP] += 0.2
        
        # OLAP评分
        if features.qps_avg <= cls.OLAP_QPS_MAX:
            scores[LoadType.OLAP] += 0.2
        if features.query_latency_avg >= cls.OLAP_LATENCY_MIN:
            scores[LoadType.OLAP] += 0.3
        if features.batch_query_ratio >= cls.OLAP_BATCH_RATIO_MIN:
            scores[LoadType.OLAP] += 0.3
        if features.transaction_size_avg > cls.OLTP_TRANS_SIZE_MAX:
            scores[LoadType.OLAP] += 0.2
        
        # HTAP评分（同时具有OLTP和OLAP特征）
        if scores[LoadType.OLTP] > 0.3 and scores[LoadType.OLAP] > 0.3:
            scores[LoadType.HTAP] = (scores[LoadType.OLTP] + scores[LoadType.OLAP]) / 2
        
        # MIXED评分（无法明确分类）
        max_score = max(scores.values())
        if max_score < 0.5:
            scores[LoadType.MIXED] = 0.5 + (0.5 - max_score)
        
        # 选择最高分
        best_type = max(scores, key=scores.get)
        confidence = scores[best_type]
        
        return best_type, min(confidence, 1.0)


class PeakHoursAnalyzer:
    """高峰时段分析器"""
    
    DAYTIME_HOURS = list(range(9, 19))      # 9-18点
    NIGHT_HOURS = list(range(0, 6))           # 0-5点
    BUSINESS_HOURS = list(range(8, 19))      # 8-18点
    
    @classmethod
    def analyze(cls, hourly_data: np.ndarray, 
                day_of_week_data: np.ndarray) -> PeakHoursProfile:
        """
        分析高峰时段
        hourly_data: shape=(168,) 7天×24小时的负载数据
        day_of_week_data: shape=(7,) 每天的总负载
        """
        profile = PeakHoursProfile()
        
        # 1. 分析小时级别高峰
        hourly_avg = np.mean(hourly_data.reshape(7, 24), axis=0)  # 每天同一小时的平均
        overall_avg = np.mean(hourly_data)
        
        if overall_avg > 0:
            # 找出高于均值的时间段
            peak_mask = hourly_avg > overall_avg * 1.2
            peak_hours = np.where(peak_mask)[0].tolist()
            
            if peak_hours:
                profile.peak_hour_start = peak_hours[0]
                profile.peak_hour_end = peak_hours[-1]
                profile.peak_intensity = np.max(hourly_avg) / overall_avg if overall_avg > 0 else 1.0
        else:
            profile.peak_intensity = 1.0
        
        # 2. 判断白天高峰还是夜间高峰
        daytime_avg = np.mean([hourly_avg[h] for h in cls.DAYTIME_HOURS])
        night_avg = np.mean([hourly_avg[h] for h in cls.NIGHT_HOURS])
        
        if daytime_avg > night_avg * 2:
            profile.pattern = PeakPattern.DAYTIME
        elif night_avg > daytime_avg * 1.5:
            profile.pattern = PeakPattern.NIGHT
            profile.has_night_batch = True
            # 找出夜间高峰时段
            night_peak = [h for h in cls.NIGHT_HOURS if hourly_avg[h] > night_avg * 0.8]
            if night_peak:
                profile.night_batch_start = night_peak[0]
                profile.night_batch_end = night_peak[-1]
        else:
            profile.pattern = PeakPattern.BUSINESS_CYCLE
        
        # 3. 分析周级别高峰
        weekday_avg = np.mean([day_of_week_data[d] for d in range(5)])
        weekend_avg = np.mean([day_of_week_data[d] for d in range(5, 7)])
        
        if weekday_avg > weekend_avg * 1.5:
            profile.peak_day_of_week = [0, 1, 2, 3, 4]  # 周一到周五
        elif weekend_avg > weekday_avg * 1.5:
            profile.peak_day_of_week = [5, 6]  # 周末
        else:
            profile.peak_day_of_week = [0, 1, 2, 3, 4, 5, 6]  # 每天
        
        # 4. 找出低谷时段
        if overall_avg > 0:
            off_peak_mask = hourly_avg < overall_avg * 0.5
            profile.off_peak_hours = np.where(off_peak_mask)[0].tolist()
        
        # 5. 生成描述
        profile.description = cls._generate_description(profile, daytime_avg, night_avg)
        
        return profile
    
    @classmethod
    def _generate_description(cls, profile: PeakHoursProfile, 
                              daytime_avg: float, night_avg: float) -> str:
        """生成时段描述"""
        patterns = {
            PeakPattern.DAYTIME: f"白天高峰型，高峰时段 {profile.peak_hour_start}:00-{profile.peak_hour_end}:00",
            PeakPattern.NIGHT: f"夜间批处理型，批处理时间 {profile.night_batch_start}:00-{profile.night_batch_end}:00",
            PeakPattern.BUSINESS_CYCLE: f"业务周期型，高峰时段 {profile.peak_hour_start}:00-{profile.peak_hour_end}:00",
            PeakPattern.FLAT: "平坦型，无明显高峰",
            PeakPattern.IRREGULAR: "不规则型"
        }
        
        base_desc = patterns.get(profile.pattern, "")
        
        if profile.peak_intensity > 2.0:
            base_desc += "，高峰明显"
        elif profile.peak_intensity > 1.5:
            base_desc += "，高峰较明显"
        else:
            base_desc += "，高峰平缓"
        
        return base_desc


class ResourcePatternAnalyzer:
    """资源使用模式分析器"""
    
    # 资源强度阈值
    CPU_THRESHOLD = 0.7
    IO_THRESHOLD = 0.7
    MEMORY_PRESSURE_THRESHOLD = 0.8
    LOCK_CONTENTION_THRESHOLD = 0.5
    
    @classmethod
    def analyze(cls,
                cpu_usage: Optional[np.ndarray] = None,
                io_rate: Optional[np.ndarray] = None,
                memory_usage: Optional[np.ndarray] = None,
                read_ops: Optional[float] = None,
                write_ops: Optional[float] = None,
                lock_waits: Optional[float] = None) -> ResourcePatternProfile:
        """
        分析资源使用模式
        """
        profile = ResourcePatternProfile()
        
        # 计算各维度强度 - 支持 float 或 np.ndarray 类型
        if cpu_usage is not None:
            if isinstance(cpu_usage, np.ndarray) and len(cpu_usage) > 0:
                profile.cpu_intensity = float(np.mean(cpu_usage))
            elif isinstance(cpu_usage, (int, float)):
                profile.cpu_intensity = float(cpu_usage)
        
        if io_rate is not None:
            if isinstance(io_rate, np.ndarray) and len(io_rate) > 0:
                io_val = float(np.mean(io_rate))
            elif isinstance(io_rate, (int, float)):
                io_val = float(io_rate)
            else:
                io_val = 0.0
            # IO强度归一化（假设IO速率最大值约100MB/s）
            profile.io_intensity = min(io_val / 100.0, 1.0)
        
        if memory_usage is not None:
            if isinstance(memory_usage, np.ndarray) and len(memory_usage) > 0:
                profile.memory_pressure = float(np.mean(memory_usage))
            elif isinstance(memory_usage, (int, float)):
                profile.memory_pressure = float(memory_usage)
        
        # 读写比例
        if read_ops is not None and write_ops is not None:
            total = read_ops + write_ops
            if total > 0:
                profile.read_write_ratio = (read_ops / total) / (write_ops / total) if write_ops > 0 else read_ops
        
        # 锁竞争
        if lock_waits is not None:
            # 锁等待次数归一化（假设超过100次/分钟为严重）
            profile.lock_contention_level = min(lock_waits / 100.0, 1.0)
        
        # 判断资源模式
        if profile.cpu_intensity > cls.CPU_THRESHOLD and profile.io_intensity < cls.IO_THRESHOLD:
            profile.pattern = ResourcePattern.CPU_BOUND
            profile.description = f"CPU密集型，平均CPU使用率 {profile.cpu_intensity:.0%}"
        elif profile.io_intensity > cls.IO_THRESHOLD and profile.cpu_intensity < cls.CPU_THRESHOLD:
            profile.pattern = ResourcePattern.IO_BOUND
            profile.description = f"IO密集型，IO强度 {profile.io_intensity:.0%}"
        elif profile.read_write_ratio > 3.0:
            profile.pattern = ResourcePattern.READ_HEAVY
            profile.description = f"读密集型，读写比 {profile.read_write_ratio:.1f}:1"
        elif profile.read_write_ratio < 0.33:
            profile.pattern = ResourcePattern.WRITE_HEAVY
            profile.description = f"写密集型，读写比 1:{1/profile.read_write_ratio:.1f}" if profile.read_write_ratio > 0 else "写密集型"
        else:
            profile.pattern = ResourcePattern.BALANCED
            profile.description = "读写平衡型"
        
        # 添加锁竞争说明
        if profile.lock_contention_level > cls.LOCK_CONTENTION_THRESHOLD:
            profile.description += f"，存在锁竞争({profile.lock_contention_level:.0%})"
        
        return profile


class WeeklyPatternAnalyzer:
    """周模式分析器"""
    
    @classmethod
    def analyze(cls, day_data: np.ndarray) -> WeeklyPattern:
        """
        分析周模式
        day_data: shape=(7,) 每天的总负载
        """
        profile = WeeklyPattern()
        
        if len(day_data) < 7:
            return profile
        
        profile.monday_load = float(day_data[0])
        profile.tuesday_load = float(day_data[1])
        profile.wednesday_load = float(day_data[2])
        profile.thursday_load = float(day_data[3])
        profile.friday_load = float(day_data[4])
        profile.saturday_load = float(day_data[5])
        profile.sunday_load = float(day_data[6])
        
        profile.weekday_avg_load = float(np.mean(day_data[:5]))
        profile.weekend_avg_load = float(np.mean(day_data[5:]))
        
        # 判断模式
        weekday_std = float(np.std(day_data[:5]))
        weekend_std = float(np.std(day_data[5:]))
        
        if profile.weekend_avg_load > profile.weekday_avg_load * 1.5:
            profile.pattern = WorkDayPattern.WEEKEND_HEAVY
        elif profile.weekday_avg_load > profile.weekend_avg_load * 1.5:
            profile.pattern = WorkDayPattern.WEEKDAY_HEAVY
        else:
            profile.pattern = WorkDayPattern.EVERYDAY_SIMILAR
        
        return profile


class ProfileEngine:
    """
    资源使用特征画像引擎
    
    通过分析历史时序数据，自动识别数据库的负载特征：
    1. 负载类型（OLTP/OLAP/HTAP/Mixed）
    2. 高峰时段（工作日高峰、节假日模式）
    3. 资源使用模式（CPU密集型/IO密集型/读写比例）
    4. 周模式（工作日vs周末）
    """
    
    def __init__(self):
        self.load_classifier = LoadTypeClassifier()
        self.peak_analyzer = PeakHoursAnalyzer()
        self.resource_analyzer = ResourcePatternAnalyzer()
        self.weekly_analyzer = WeeklyPatternAnalyzer()
    
    def generate_profile(
        self,
        db_config_id: int,
        db_name: str,
        db_type: str,
        hourly_metrics: Optional[dict] = None,
        daily_metrics: Optional[dict] = None,
        load_features: Optional[LoadTypeFeatures] = None,
        resource_metrics: Optional[dict] = None
    ) -> DatabaseProfile:
        """
        生成数据库完整画像
        
        Args:
            db_config_id: 数据库配置ID
            db_name: 数据库名称
            db_type: 数据库类型
            hourly_metrics: 小时级指标，包含:
                - qps: (168,) 7天×24小时的QPS数据
                - connections: (168,) 连接数数据
                - cpu: (168,) CPU使用率数据
                - io_rate: (168,) IO速率数据
            daily_metrics: 日级指标，包含:
                - total_load: (7,) 每天总负载
                - transactions: (7,) 每天事务数
            load_features: 预计算的负载特征
            resource_metrics: 资源指标，包含:
                - cpu_usage: 平均CPU使用率
                - io_rate: 平均IO速率
                - memory_usage: 平均内存使用率
                - read_ops: 读操作数
                - write_ops: 写操作数
                - lock_waits: 锁等待次数
        """
        profile = DatabaseProfile(
            db_config_id=db_config_id,
            db_name=db_name,
            db_type=db_type,
            generated_at=datetime.now()
        )
        
        # 1. 负载类型分类
        if load_features is not None:
            profile.load_features = load_features
        elif hourly_metrics is not None and 'qps' in hourly_metrics:
            profile.load_features = self._extract_load_features(hourly_metrics)
        
        if isinstance(profile.load_features, LoadTypeFeatures):
            profile.load_type, profile.load_type_confidence = \
                self.load_classifier.classify(profile.load_features)
        
        # 2. 高峰时段分析
        if hourly_metrics is not None and daily_metrics is not None:
            qps_data = hourly_metrics.get('qps', np.zeros(168))
            day_load = daily_metrics.get('total_load', np.zeros(7))
            profile.peak_hours = self.peak_analyzer.analyze(qps_data, day_load)
        
        # 3. 资源使用模式
        if resource_metrics is not None:
            profile.resource_pattern = self.resource_analyzer.analyze(
                cpu_usage=resource_metrics.get('cpu_usage'),
                io_rate=resource_metrics.get('io_rate'),
                memory_usage=resource_metrics.get('memory_usage'),
                read_ops=resource_metrics.get('read_ops'),
                write_ops=resource_metrics.get('write_ops'),
                lock_waits=resource_metrics.get('lock_waits')
            )
        
        # 4. 周模式分析
        if daily_metrics is not None:
            day_load = daily_metrics.get('total_load', np.zeros(7))
            if len(day_load) == 7:
                profile.weekly_pattern = self.weekly_analyzer.analyze(day_load)
        
        # 5. 生成综合描述和建议
        profile.summary = self._generate_summary(profile)
        profile.recommendations = self._generate_recommendations(profile)
        
        return profile
    
    def _extract_load_features(self, hourly_metrics: dict) -> LoadTypeFeatures:
        """从小时级指标提取负载特征"""
        features = LoadTypeFeatures()
        
        # QPS特征
        if 'qps' in hourly_metrics:
            qps = np.array(hourly_metrics['qps'])
            features.qps_avg = float(np.mean(qps))
            features.qps_max = float(np.max(qps))
            features.qps_std = float(np.std(qps))
        
        # 连接数特征
        if 'connections' in hourly_metrics:
            conn = np.array(hourly_metrics['connections'])
            if 'max_connections' in hourly_metrics:
                max_conn = hourly_metrics['max_connections']
                features.connection_usage_avg = float(np.mean(conn) / max_conn) if max_conn > 0 else 0
        
        # 延迟特征（如果有）
        if 'latency' in hourly_metrics:
            latency = np.array(hourly_metrics['latency'])
            features.query_latency_avg = float(np.mean(latency))
            features.query_latency_p99 = float(np.percentile(latency, 99))
        
        # 读写比例（如果有）
        if 'reads' in hourly_metrics and 'writes' in hourly_metrics:
            reads = np.sum(hourly_metrics['reads'])
            writes = np.sum(hourly_metrics['writes'])
            total = reads + writes
            if total > 0:
                features.read_ratio = float(reads / total)
                features.write_ratio = float(writes / total)
        
        return features
    
    def _generate_summary(self, profile: DatabaseProfile) -> str:
        """生成画像综合描述"""
        parts = []
        
        # 负载类型
        load_type_names = {
            LoadType.OLTP: "交易型(OLTP)",
            LoadType.OLAP: "分析型(OLAP)",
            LoadType.HTAP: "混合型(HTAP)",
            LoadType.MIXED: "混合负载",
            LoadType.UNKNOWN: "未知"
        }
        parts.append(f"负载类型: {load_type_names.get(profile.load_type, '未知')}")
        
        if profile.load_type_confidence > 0:
            parts.append(f"(置信度{profile.load_type_confidence:.0%})")
        
        # 高峰时段
        parts.append(f"，{profile.peak_hours.description}")
        
        # 资源模式
        if profile.resource_pattern.description:
            parts.append(f"，{profile.resource_pattern.description}")
        
        return "".join(parts)
    
    def _generate_recommendations(self, profile: DatabaseProfile) -> list[str]:
        """生成优化建议"""
        recommendations = []
        
        # 基于负载类型的建议
        if profile.load_type == LoadType.OLTP:
            recommendations.append("建议使用连接池优化，减少连接建立开销")
            recommendations.append("关注事务长度，避免长事务影响并发")
        elif profile.load_type == LoadType.OLAP:
            recommendations.append("建议使用读写分离，将分析查询分流到从库")
            recommendations.append("考虑使用物化视图加速复杂查询")
        elif profile.load_type == LoadType.HTAP:
            recommendations.append("建议部署HTAP架构，同时支持事务和分析")
        
        # 基于高峰时段的建议
        if profile.peak_hours.has_night_batch:
            recommendations.append("存在夜间批处理，建议将批处理分散到凌晨低峰时段")
        
        if profile.peak_hours.peak_intensity > 2.0:
            recommendations.append("业务高峰明显，建议实施负载均衡分流")
        
        # 基于资源模式的建议
        if profile.resource_pattern.pattern == ResourcePattern.CPU_BOUND:
            recommendations.append("CPU密集型负载，建议升级CPU或增加计算资源")
        elif profile.resource_pattern.pattern == ResourcePattern.IO_BOUND:
            recommendations.append("IO密集型负载，建议使用SSD或增加IOPS")
        
        if profile.resource_pattern.lock_contention_level > 0.5:
            recommendations.append("存在锁竞争，建议优化事务粒度或使用乐观锁")
        
        # 基于周模式的建议
        if profile.weekly_pattern.pattern == WorkDayPattern.WEEKDAY_HEAVY:
            recommendations.append("工作日繁忙，建议在周末进行维护操作")
        elif profile.weekly_pattern.pattern == WorkDayPattern.WEEKEND_HEAVY:
            recommendations.append("周末负载较重，可能存在特殊业务需求，建议评估")
        
        return recommendations
    
    def compare_profiles(
        self,
        profile1: DatabaseProfile,
        profile2: DatabaseProfile
    ) -> dict:
        """
        对比两个数据库的画像，找出相似和差异
        
        Returns:
            对比结果字典，包含:
            - similarity: 相似度得分(0-1)
            - common_patterns: 共同特征
            - differences: 差异特征
        """
        result = {
            'similarity': 0.0,
            'common_patterns': [],
            'differences': []
        }
        
        similarity_score = 0.0
        total_factors = 5
        
        # 1. 负载类型相似度
        if profile1.load_type == profile2.load_type:
            similarity_score += 1.0
            result['common_patterns'].append(f"负载类型相同({profile1.load_type.value})")
        else:
            result['differences'].append(
                f"负载类型不同: {profile1.load_type.value} vs {profile2.load_type.value}"
            )
        
        # 2. 高峰时段相似度
        if profile1.peak_hours.pattern == profile2.peak_hours.pattern:
            similarity_score += 1.0
            result['common_patterns'].append(f"高峰时段模式相同({profile1.peak_hours.pattern.value})")
        else:
            result['differences'].append(
                f"高峰时段不同: {profile1.peak_hours.pattern.value} vs {profile2.peak_hours.pattern.value}"
            )
        
        # 3. 资源模式相似度
        if profile1.resource_pattern.pattern == profile2.resource_pattern.pattern:
            similarity_score += 1.0
            result['common_patterns'].append(f"资源模式相同({profile1.resource_pattern.pattern.value})")
        else:
            result['differences'].append(
                f"资源模式不同: {profile1.resource_pattern.pattern.value} vs {profile2.resource_pattern.pattern.value}"
            )
        
        # 4. 周模式相似度
        if profile1.weekly_pattern.pattern == profile2.weekly_pattern.pattern:
            similarity_score += 1.0
            result['common_patterns'].append(f"周模式相同({profile1.weekly_pattern.pattern.value})")
        else:
            result['differences'].append(
                f"周模式不同: {profile1.weekly_pattern.pattern.value} vs {profile2.weekly_pattern.pattern.value}"
            )
        
        # 5. 资源强度相似度
        cpu_diff = abs(profile1.resource_pattern.cpu_intensity - profile2.resource_pattern.cpu_intensity)
        if cpu_diff < 0.2:
            similarity_score += 1.0
        
        result['similarity'] = similarity_score / total_factors
        
        return result


# =============================================================================
# 便捷函数
# =============================================================================

def quick_profile(
    db_config_id: int,
    db_name: str,
    db_type: str,
    qps_data: np.ndarray,
    day_load_data: np.ndarray,
    resource_metrics: Optional[dict] = None
) -> DatabaseProfile:
    """
    快速生成数据库画像（简化接口）
    
    Args:
        db_config_id: 数据库配置ID
        db_name: 数据库名称
        db_type: 数据库类型
        qps_data: (168,) 7天×24小时的QPS数据
        day_load_data: (7,) 每天总负载
        resource_metrics: 资源指标字典
    """
    engine = ProfileEngine()
    
    hourly_metrics = {'qps': qps_data}
    daily_metrics = {'total_load': day_load_data}
    
    return engine.generate_profile(
        db_config_id=db_config_id,
        db_name=db_name,
        db_type=db_type,
        hourly_metrics=hourly_metrics,
        daily_metrics=daily_metrics,
        resource_metrics=resource_metrics
    )