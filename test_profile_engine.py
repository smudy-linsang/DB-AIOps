"""
资源画像引擎测试

测试 profile_engine.py 的功能：
- 负载类型识别
- 高峰时段分析
- 资源模式识别
"""

import pytest
import numpy as np
from unittest.mock import Mock, patch


class TestLoadTypeClassification:
    """负载类型分类测试"""
    
    def test_oltp_classification(self):
        """测试 OLTP 负载类型识别"""
        from monitor.profile_engine import ProfileEngine, LoadType
        
        engine = ProfileEngine()
        
        # 模拟 OLTP 特征：高读/高并发/低延迟
        metrics = {
            'read_ops': 1000,
            'write_ops': 800,
            'avg_latency': 5,  # 低延迟
            'connections': 500
        }
        
        load_type = engine.classify_load_type(metrics)
        
        assert load_type in [LoadType.OLTP, LoadType.MIXED]
    
    def test_olap_classification(self):
        """测试 OLAP 负载类型识别"""
        from monitor.profile_engine import ProfileEngine, LoadType
        
        engine = ProfileEngine()
        
        # 模拟 OLAP 特征：高吞吐/低并发/高延迟
        metrics = {
            'read_ops': 5000,
            'write_ops': 10,
            'avg_latency': 500,  # 高延迟
            'connections': 50
        }
        
        load_type = engine.classify_load_type(metrics)
        
        assert load_type == LoadType.OLAP


class TestPeakHoursAnalysis:
    """高峰时段分析测试"""
    
    def test_identify_peak_hours(self):
        """测试识别高峰时段"""
        from monitor.profile_engine import ProfileEngine
        
        engine = ProfileEngine()
        
        # 模拟 24 小时数据
        hourly_data = np.array([10, 5, 3, 2, 1, 5, 20, 50, 80, 90, 85, 80, 
                                 75, 70, 65, 70, 80, 90, 95, 80, 60, 40, 25, 15])
        
        peak_hours = engine.identify_peak_hours(hourly_data)
        
        assert isinstance(peak_hours, list)
        assert len(peak_hours) > 0
        # 工作日高峰应该在工作时间
        assert any(8 <= h <= 20 for h in peak_hours)
    
    def test_identify_business_cycle(self):
        """测试识别业务周期"""
        from monitor.profile_engine import ProfileEngine
        
        engine = ProfileEngine()
        
        # 模拟 7 天数据（工作日高，周末低）
        daily_data = np.array([100, 110, 105, 108, 95, 20, 15,  # 第一周
                                100, 110, 105, 108, 95, 20, 15])  # 第二周
        
        cycle = engine.identify_business_cycle(daily_data)
        
        assert cycle is not None
        assert 'type' in cycle


class TestResourcePattern:
    """资源模式测试"""
    
    def test_cpu_bound_pattern(self):
        """测试 CPU 密集型模式识别"""
        from monitor.profile_engine import ProfileEngine, ResourcePattern
        
        engine = ProfileEngine()
        
        metrics = {
            'cpu_usage': 0.95,
            'io_wait': 0.05,
            'memory_usage': 0.5
        }
        
        pattern = engine.identify_resource_pattern(metrics)
        
        assert pattern == ResourcePattern.CPU_BOUND
    
    def test_io_bound_pattern(self):
        """测试 IO 密集型模式识别"""
        from monitor.profile_engine import ProfileEngine, ResourcePattern
        
        engine = ProfileEngine()
        
        metrics = {
            'cpu_usage': 0.3,
            'io_wait': 0.6,
            'memory_usage': 0.5
        }
        
        pattern = engine.identify_resource_pattern(metrics)
        
        assert pattern == ResourcePattern.IO_BOUND


class TestProfileEngine:
    """资源画像引擎测试"""
    
    def test_generate_profile(self):
        """测试生成完整画像"""
        from monitor.profile_engine import ProfileEngine
        import numpy as np
        
        engine = ProfileEngine()
        
        # 模拟数据
        db_config_id = 1
        db_name = 'test_db'
        db_type = 'oracle'
        
        qps_data = np.random.rand(168) * 100  # 168 小时数据
        day_load_data = np.array([100, 110, 105, 108, 95, 20, 15])
        resource_metrics = {
            'cpu_usage': 0.75,
            'read_ops': 800,
            'write_ops': 150
        }
        
        profile = engine.generate_profile(
            db_config_id=db_config_id,
            db_name=db_name,
            db_type=db_type,
            qps_data=qps_data,
            day_load_data=day_load_data,
            resource_metrics=resource_metrics
        )
        
        assert profile is not None
        assert 'load_type' in profile
        assert 'peak_hours' in profile
        assert 'resource_pattern' in profile


class TestQuickProfile:
    """快速画像测试"""
    
    def test_quick_profile(self):
        """测试快速画像函数"""
        from monitor.profile_engine import quick_profile
        import numpy as np
        
        profile = quick_profile(
            db_config_id=1,
            db_name='test_db',
            db_type='mysql',
            qps_data=np.random.rand(168) * 100,
            day_load_data=np.array([100, 110, 105, 108, 95, 20, 15]),
            resource_metrics={'cpu_usage': 0.7, 'read_ops': 500, 'write_ops': 100}
        )
        
        assert profile is not None
        assert profile.load_type is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
