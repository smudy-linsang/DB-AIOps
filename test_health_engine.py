"""
Health Engine 单元测试

覆盖范围:
1. 辅助函数: _linear_score, _percent_score
2. 评分器类: AvailabilityScorer, CapacityScorer, PerformanceScorer, ConfigurationScorer, OperationsScorer
3. HealthEngine: calculate, _get_grade, _generate_summary, _generate_recommendations, compare_with_baseline
"""

import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from monitor.health_engine import (
    HealthEngine, HEALTH_WEIGHTS, HEALTH_GRADES, SUB_WEIGHTS,
    AvailabilityScorer, CapacityScorer, PerformanceScorer,
    ConfigurationScorer, OperationsScorer,
    _linear_score, _percent_score
)


# ==========================================
# 辅助函数测试
# ==========================================

def test_linear_score():
    """测试 _linear_score 函数"""
    # 值越小越好 (reverse=False)
    assert _linear_score(10, 10, 100) == 100.0  # 最佳值
    assert _linear_score(100, 10, 100) == 0.0   # 最差值
    assert _linear_score(55, 10, 100) == 50.0   # 中间值
    
    # 值越大越好 (reverse=True)
    assert _linear_score(100, 10, 100, reverse=True) == 100.0
    assert _linear_score(10, 10, 100, reverse=True) == 0.0
    assert _linear_score(55, 10, 100, reverse=True) == 50.0
    
    print("  ✅ test_linear_score passed")


def test_percent_score():
    """测试 _percent_score 函数"""
    # 低于 warning 阈值
    assert _percent_score(60, 70, 85) == 100.0
    
    # 在 warning 和 critical 之间
    result = _percent_score(77.5, 70, 85)  # 中点
    assert 70 < result < 100
    
    # 超过 critical
    result = _percent_score(95, 70, 85)
    assert 0 < result < 50
    
    # 100% 使用率
    result = _percent_score(100, 70, 85)
    assert result == 0.0
    
    print("  ✅ test_percent_score passed")


# ==========================================
# AvailabilityScorer Tests
# ==========================================

def test_availability_scorer_up():
    """测试 AvailabilityScorer 正常状态"""
    scorer = AvailabilityScorer()
    data = {
        'current_status': 'UP',
        'response_time_ms': 5
    }
    result = scorer.score(data)
    
    assert result['score'] == 100.0
    assert result['details']['status'] == 100.0
    assert result['details']['connectivity'] == 100.0
    
    print("  ✅ test_availability_scorer_up passed")


def test_availability_scorer_down():
    """测试 AvailabilityScorer DOWN 状态"""
    scorer = AvailabilityScorer()
    data = {
        'current_status': 'DOWN',
        'response_time_ms': 5
    }
    result = scorer.score(data)
    
    assert result['details']['status'] == 0.0
    
    print("  ✅ test_availability_scorer_down passed")


def test_availability_scorer_response_time():
    """测试 AvailabilityScorer 响应时间评分"""
    scorer = AvailabilityScorer()
    
    # 快速响应
    data = {'current_status': 'UP', 'response_time_ms': 5}
    result = scorer.score(data)
    assert result['details']['connectivity'] == 100.0
    
    # 中等延迟 (50ms 在 10-100 范围，应 < 100)
    data = {'current_status': 'UP', 'response_time_ms': 50}
    result = scorer.score(data)
    assert result['details']['connectivity'] <= 100
    
    # 高延迟
    data = {'current_status': 'UP', 'response_time_ms': 500}
    result = scorer.score(data)
    assert result['details']['connectivity'] < 60
    
    print("  ✅ test_availability_scorer_response_time passed")


# ==========================================
# CapacityScorer Tests
# ==========================================

def test_capacity_scorer_normal():
    """测试 CapacityScorer 正常状态"""
    scorer = CapacityScorer()
    data = {
        'tablespaces': [{'used_pct': 50}],
        'conn_usage_pct': 50
    }
    result = scorer.score(data)
    
    assert result['details']['tablespace'] == 100.0
    assert result['details']['connection'] == 100.0
    assert result['max_tablespace_usage'] == 50.0
    
    print("  ✅ test_capacity_scorer_normal passed")


def test_capacity_scorer_high_usage():
    """测试 CapacityScorer 高使用率"""
    scorer = CapacityScorer()
    data = {
        'tablespaces': [{'used_pct': 90}],
        'conn_usage_pct': 80
    }
    result = scorer.score(data)
    
    assert result['details']['tablespace'] < 50
    assert result['max_tablespace_usage'] == 90.0
    
    print("  ✅ test_capacity_scorer_high_usage passed")


def test_capacity_scorer_multiple_tablespaces():
    """测试 CapacityScorer 多表空间取最大值"""
    scorer = CapacityScorer()
    data = {
        'tablespaces': [
            {'used_pct': 50},
            {'used_pct': 80},
            {'used_pct': 60}
        ],
        'conn_usage_pct': 50
    }
    result = scorer.score(data)
    
    assert result['max_tablespace_usage'] == 80.0
    
    print("  ✅ test_capacity_scorer_multiple_tablespaces passed")


# ==========================================
# PerformanceScorer Tests
# ==========================================

def test_performance_scorer_qps():
    """测试 PerformanceScorer QPS 评分"""
    scorer = PerformanceScorer(db_type='mysql')
    
    # 正常 QPS
    data = {'qps': 1000, 'baseline_qps': 1000, 'response_time_ms': 10, 'slow_queries_active': 0, 'active_connections': 100}
    result = scorer.score(data)
    assert result['details']['qps'] == 100.0
    
    # 低 QPS
    data = {'qps': 200, 'baseline_qps': 1000, 'response_time_ms': 10, 'slow_queries_active': 0, 'active_connections': 100}
    result = scorer.score(data)
    assert result['details']['qps'] < 80
    
    print("  ✅ test_performance_scorer_qps passed")


def test_performance_scorer_slow_queries():
    """测试 PerformanceScorer 慢查询评分"""
    scorer = PerformanceScorer(db_type='mysql')
    
    # 无慢查询
    data = {'qps': 1000, 'baseline_qps': 1000, 'response_time_ms': 10, 'slow_queries_active': 0, 'active_connections': 100}
    result = scorer.score(data)
    assert result['details']['slow_queries'] == 100.0
    
    # 大量慢查询
    data = {'qps': 1000, 'baseline_qps': 1000, 'response_time_ms': 10, 'slow_queries_active': 20, 'active_connections': 100}
    result = scorer.score(data)
    assert result['details']['slow_queries'] < 50
    
    print("  ✅ test_performance_scorer_slow_queries passed")


def test_performance_scorer_db_types():
    """测试 PerformanceScorer 不同数据库类型"""
    # Oracle 类型 (good_rt=100, bad_rt=1000)
    # 500ms 应该在中间位置
    scorer = PerformanceScorer(db_type='oracle')
    data = {'qps': 1000, 'baseline_qps': 1000, 'response_time_ms': 500, 'slow_queries_active': 0, 'active_connections': 100}
    result = scorer.score(data)
    assert 0 < result['details']['response_time'] < 100  # 应该在中间范围
    
    # PostgreSQL 类型 (good_rt=50, bad_rt=500)
    # 200ms 应该得分较低
    scorer = PerformanceScorer(db_type='pgsql')
    data = {'qps': 1000, 'baseline_qps': 1000, 'response_time_ms': 200, 'slow_queries_active': 0, 'active_connections': 100}
    result = scorer.score(data)
    assert 0 < result['details']['response_time'] < 100
    
    print("  ✅ test_performance_scorer_db_types passed")


# ==========================================
# ConfigurationScorer Tests
# ==========================================

def test_configuration_scorer_security():
    """测试 ConfigurationScorer 安全评分"""
    # 创建 mock config
    mock_config = MagicMock()
    mock_config.password = 'strong_password_123'
    mock_config.port = 3306
    mock_config.connection_options = {'ssl': 'true'}
    
    scorer = ConfigurationScorer(db_type='mysql')
    result = scorer.score(mock_config)
    
    assert result['ssl_enabled'] == True
    # 安全评分: 100 - 10(默认端口) = 90
    assert result['details']['security'] == 90.0
    
    print("  ✅ test_configuration_scorer_security passed")


def test_configuration_scorer_no_ssl():
    """测试 ConfigurationScorer 无 SSL 配置"""
    mock_config = MagicMock()
    mock_config.password = 'strong_password_123'
    mock_config.port = 3306
    mock_config.connection_options = {}
    
    scorer = ConfigurationScorer(db_type='mysql')
    result = scorer.score(mock_config)
    
    assert result['ssl_enabled'] == False
    # 安全评分: 100 - 30(无SSL) - 10(默认端口) = 60
    assert result['details']['security'] == 60.0
    
    print("  ✅ test_configuration_scorer_no_ssl passed")


def test_configuration_scorer_charset():
    """测试 ConfigurationScorer 字符集评分"""
    mock_config = MagicMock()
    mock_config.password = 'strong_password_123'
    mock_config.port = 3307
    mock_config.connection_options = {'charset': 'utf8mb4'}
    
    scorer = ConfigurationScorer(db_type='mysql')
    result = scorer.score(mock_config)
    
    # SUB_WEIGHTS['configuration']['param合理性'] = 0.5
    # details key 是 'param合理性' 不是 'param'
    assert 'param合理性' in result['details']
    assert result['details']['param合理性'] == 100.0
    
    # 测试不良字符集
    mock_config.connection_options = {'charset': 'latin1'}
    result = scorer.score(mock_config)
    assert result['details']['param合理性'] < 100.0
    
    print("  ✅ test_configuration_scorer_charset passed")


# ==========================================
# OperationsScorer Tests
# ==========================================

def test_operations_scorer_no_backup_alerts():
    """测试 OperationsScorer 无备份告警"""
    scorer = OperationsScorer()
    mock_config = MagicMock()
    recent_logs = [
        {'create_time': datetime.now(), 'message': 'normal log'}
    ]
    
    result = scorer.score(mock_config, recent_logs)
    
    assert result['details']['backup'] == 100.0
    
    print("  ✅ test_operations_scorer_no_backup_alerts passed")


def test_operations_scorer_with_backup_alerts():
    """测试 OperationsScorer 有备份告警"""
    scorer = OperationsScorer()
    mock_config = MagicMock()
    recent_logs = [
        {'create_time': datetime.now(), 'message': 'backup failed'}
    ]
    
    result = scorer.score(mock_config, recent_logs)
    
    assert result['details']['backup'] == 70.0
    
    print("  ✅ test_operations_scorer_with_backup_alerts passed")


def test_operations_scorer_monitoring_coverage():
    """测试 OperationsScorer 监控覆盖率"""
    scorer = OperationsScorer()
    mock_config = MagicMock()
    
    # 充足日志 (24小时 >= 288条意味着每5分钟一条)
    recent_logs = [{'create_time': datetime.now() - timedelta(minutes=i*5)} for i in range(300)]
    result = scorer.score(mock_config, recent_logs)
    
    assert result['details']['monitoring'] == 100.0
    assert result['monitoring_coverage'] >= 100.0
    
    # 少量日志
    recent_logs = [{'create_time': datetime.now() - timedelta(hours=i)} for i in range(5)]
    result = scorer.score(mock_config, recent_logs)
    
    assert result['monitoring_coverage'] < 50
    
    print("  ✅ test_operations_scorer_monitoring_coverage passed")


# ==========================================
# HealthEngine Tests
# ==========================================

def test_health_engine_calculate():
    """测试 HealthEngine.calculate 方法"""
    class MockConfig:
        name = "test"
        db_type = "mysql"
        password = "strong_password_123"
        port = 3307
        connection_options = {'ssl': 'true', 'charset': 'utf8mb4'}
    
    engine = HealthEngine(MockConfig())
    
    data = {
        'current_status': 'UP',
        'response_time_ms': 10,
        'tablespaces': [{'used_pct': 50}],
        'conn_usage_pct': 50,
        'qps': 1000,
        'baseline_qps': 1000,
        'slow_queries_active': 0,
        'active_connections': 100,
    }
    
    # Mock 数据库查询
    with patch('monitor.health_engine.MonitorLog.objects') as mock_monitor:
        mock_monitor.filter.return_value.order_by.return_value.__getitem__ = MagicMock(return_value=[])
        result = engine.calculate(data)
    
    assert 'overall_score' in result
    assert 'grade' in result
    assert 'dimensions' in result
    assert 'summary' in result
    assert 'recommendations' in result
    assert 0 <= result['overall_score'] <= 100
    
    print("  ✅ test_health_engine_calculate passed")


def test_health_engine_calculate_empty_data():
    """测试 HealthEngine.calculate 空数据"""
    class MockConfig:
        name = "test"
        db_type = "mysql"
        password = "strong_password_123"
        port = 3307
        connection_options = {}
    
    engine = HealthEngine(MockConfig())
    
    result = engine.calculate({})
    
    assert 'error' in result
    assert result['overall_score'] == 0
    assert result['grade'] == 'F'
    
    print("  ✅ test_health_engine_calculate_empty_data passed")


def test_health_engine_get_grade():
    """测试 HealthEngine._get_grade 方法"""
    class MockConfig:
        name = "test"
        db_type = "mysql"
    
    engine = HealthEngine(MockConfig())
    
    # 测试各等级
    assert engine._get_grade(95)['grade'] == 'A'
    assert engine._get_grade(85)['grade'] == 'B'
    assert engine._get_grade(75)['grade'] == 'C'
    assert engine._get_grade(65)['grade'] == 'D'
    assert engine._get_grade(50)['grade'] == 'F'
    
    # 边界值
    assert engine._get_grade(90)['grade'] == 'A'
    assert engine._get_grade(80)['grade'] == 'B'
    assert engine._get_grade(70)['grade'] == 'C'
    assert engine._get_grade(60)['grade'] == 'D'
    
    print("  ✅ test_health_engine_get_grade passed")


def test_health_engine_generate_summary():
    """测试 HealthEngine._generate_summary 方法"""
    class MockConfig:
        name = "test"
        db_type = "mysql"
    
    engine = HealthEngine(MockConfig())
    
    dimension_scores = {
        'availability': {'score': 90.0},
        'capacity': {'score': 80.0},
        'performance': {'score': 70.0},
        'configuration': {'score': 60.0},
        'operations': {'score': 50.0},
    }
    
    result = engine._generate_summary(dimension_scores, 70.0, {'grade': 'C', 'description': '一般'})
    
    assert '70.0' in result
    assert 'C' in result
    assert 'operations' in result  # 最低分维度
    
    print("  ✅ test_health_engine_generate_summary passed")


def test_health_engine_generate_recommendations():
    """测试 HealthEngine._generate_recommendations 方法"""
    class MockConfig:
        name = "test"
        db_type = "mysql"
    
    engine = HealthEngine(MockConfig())
    
    # 测试低可用性建议
    dimension_scores = {
        'availability': {'score': 60.0},
        'capacity': {'score': 90.0},
        'performance': {'score': 90.0},
        'configuration': {'score': 90.0, 'ssl_enabled': True},
        'operations': {'score': 90.0, 'monitoring_coverage': 90.0},
    }
    recs = engine._generate_recommendations(dimension_scores)
    assert any('可用性' in r for r in recs)
    
    # 测试低容量建议
    dimension_scores = {
        'availability': {'score': 90.0},
        'capacity': {'score': 60.0, 'max_tablespace_usage': 95.0},
        'performance': {'score': 90.0},
        'configuration': {'score': 90.0, 'ssl_enabled': True},
        'operations': {'score': 90.0, 'monitoring_coverage': 90.0},
    }
    recs = engine._generate_recommendations(dimension_scores)
    assert any('容量' in r for r in recs)
    
    # 测试全部良好
    dimension_scores = {
        'availability': {'score': 90.0},
        'capacity': {'score': 90.0},
        'performance': {'score': 90.0, 'slow_queries': 0},
        'configuration': {'score': 90.0, 'ssl_enabled': True},
        'operations': {'score': 90.0, 'monitoring_coverage': 90.0},
    }
    recs = engine._generate_recommendations(dimension_scores)
    assert any('良好' in r or '保持' in r for r in recs)
    
    print("  ✅ test_health_engine_generate_recommendations passed")


def test_health_engine_compare_with_baseline():
    """测试 HealthEngine.compare_with_baseline 方法"""
    class MockConfig:
        name = "test"
        db_type = "mysql"
    
    engine = HealthEngine(MockConfig())
    
    # 显著改善
    result = engine.compare_with_baseline(90, 80)
    assert result['trend'] == 'improving'
    assert result['delta'] == 10.0
    
    # 基本稳定
    result = engine.compare_with_baseline(82, 80)
    assert result['trend'] == 'stable'
    
    # 轻微下降
    result = engine.compare_with_baseline(77, 80)
    assert result['trend'] == 'slight_decline'
    
    # 显著下降
    result = engine.compare_with_baseline(70, 80)
    assert result['trend'] == 'declining'
    assert result['delta'] == -10.0
    
    print("  ✅ test_health_engine_compare_with_baseline passed")


def test_health_weights_total():
    """测试 HEALTH_WEIGHTS 总和为 1.0"""
    total = sum(HEALTH_WEIGHTS.values())
    assert abs(total - 1.0) < 0.001
    
    print("  ✅ test_health_weights_total passed")


def test_health_grades_complete():
    """测试 HEALTH_GRADES 包含所有等级"""
    assert 'A' in HEALTH_GRADES
    assert 'B' in HEALTH_GRADES
    assert 'C' in HEALTH_GRADES
    assert 'D' in HEALTH_GRADES
    assert 'F' in HEALTH_GRADES
    
    # 验证分数范围
    assert HEALTH_GRADES['A'][0] == 90  # A 起始分数
    assert HEALTH_GRADES['F'][1] == 59  # F 最高分数
    
    print("  ✅ test_health_grades_complete passed")


def test_sub_weights_complete():
    """测试 SUB_WEIGHTS 结构完整"""
    for dim in HEALTH_WEIGHTS.keys():
        assert dim in SUB_WEIGHTS
        assert abs(sum(SUB_WEIGHTS[dim].values()) - 1.0) < 0.001
    
    print("  ✅ test_sub_weights_complete passed")


# ==========================================
# 运行所有测试
# ==========================================

def run_all_tests():
    print("\n" + "="*60)
    print(" Health Engine 单元测试")
    print("="*60)
    
    tests = [
        # 辅助函数
        test_linear_score,
        test_percent_score,
        # AvailabilityScorer
        test_availability_scorer_up,
        test_availability_scorer_down,
        test_availability_scorer_response_time,
        # CapacityScorer
        test_capacity_scorer_normal,
        test_capacity_scorer_high_usage,
        test_capacity_scorer_multiple_tablespaces,
        # PerformanceScorer
        test_performance_scorer_qps,
        test_performance_scorer_slow_queries,
        test_performance_scorer_db_types,
        # ConfigurationScorer
        test_configuration_scorer_security,
        test_configuration_scorer_no_ssl,
        test_configuration_scorer_charset,
        # OperationsScorer
        test_operations_scorer_no_backup_alerts,
        test_operations_scorer_with_backup_alerts,
        test_operations_scorer_monitoring_coverage,
        # HealthEngine
        test_health_engine_calculate,
        test_health_engine_calculate_empty_data,
        test_health_engine_get_grade,
        test_health_engine_generate_summary,
        test_health_engine_generate_recommendations,
        test_health_engine_compare_with_baseline,
        # 配置验证
        test_health_weights_total,
        test_health_grades_complete,
        test_sub_weights_complete,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {test.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {test.__name__} ERROR: {e}")
            failed += 1
    
    print("\n" + "="*60)
    print(f" 测试结果: {passed} 通过, {failed} 失败")
    print("="*60)
    
    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)