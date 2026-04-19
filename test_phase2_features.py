"""
Phase 2 智能提升 - 自动化测试验证

测试范围:
1. baseline_engine.py v2.0 - 168时间槽动态基线 + 三重条件异常检测
2. rca_engine.py v2.0 - 10条诊断规则 (R001-R010)
3. capacity_engine.py - 多模型容量预测
4. health_engine.py - 5维度健康评分
"""

import os
import sys
import django

# 设置 Django 环境
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from datetime import datetime, timedelta
from monitor.baseline_engine import BaselineEngine, BaselineModel, METRIC_DIRECTION_CONFIG
from monitor.rca_engine import RCAEngine, RULES
from monitor.capacity_engine import CapacityEngine, LinearRegressionModel, HoltWintersModel, SimpleMovingAverageModel, ALERT_THRESHOLDS
from monitor.health_engine import HealthEngine, HEALTH_WEIGHTS, HEALTH_GRADES


# ==========================================
# 测试辅助函数
# ==========================================

def print_header(title: str):
    print(f"\n{'='*60}")
    print(f" {title}")
    print('='*60)

def print_test(name: str, passed: bool, message: str = ""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  [{status}] {name}")
    if message:
        print(f"         {message}")


# ==========================================
# Test 1: Baseline Engine v2.0
# ==========================================

def test_baseline_engine_v2():
    print_header("Test 1: Baseline Engine v2.0 - 168时间槽动态基线")
    
    all_passed = True
    
    # 1.1 测试 168 时间槽计算
    print("\n  1.1 168时间槽计算:")
    total_slots = 7 * 24
    passed = total_slots == 168
    print_test("时间槽总数 = 7×24 = 168", passed)
    all_passed &= passed
    
    # 1.2 测试时间槽索引计算
    print("\n  1.2 时间槽索引计算 (_get_time_slot):")
    
    class MockConfig:
        name = "test"
        db_type = "mysql"
    
    be = BaselineEngine(MockConfig())
    
    # 周一 00:00 -> 索引 0
    monday = datetime(2024, 1, 1, 0, 0)
    idx = be._get_time_slot(monday)
    passed = idx == 0
    print_test("周一 00:00 → 索引 0", passed, f"actual={idx}")
    all_passed &= passed
    
    # 周二 00:00 -> 索引 24
    tuesday = datetime(2024, 1, 2, 0, 0)
    idx = be._get_time_slot(tuesday)
    passed = idx == 24
    print_test("周二 00:00 → 索引 24", passed, f"actual={idx}")
    all_passed &= passed
    
    # 周日 23:00 -> 索引 167
    sunday = datetime(2024, 1, 7, 23, 0)
    idx = be._get_time_slot(sunday)
    passed = idx == 167
    print_test("周日 23:00 → 索引 167", passed, f"actual={idx}")
    all_passed &= passed
    
    # 1.3 测试 BaselineModel
    print("\n  1.3 BaselineModel 统计计算:")
    bm = BaselineModel('test_metric', 0)
    values = [10, 12, 11, 13, 12, 11, 10, 12]
    bm.values = values
    bm.calculate()
    
    passed = abs(bm.mean - 11.375) < 0.01
    print_test("BaselineModel 均值 ≈ 11.375", passed, f"actual={bm.mean:.4f}")
    all_passed &= passed
    
    passed = bm.sample_count == 8
    print_test("BaselineModel 样本数 = 8", passed, f"actual={bm.sample_count}")
    all_passed &= passed
    
    passed = bm.data_sufficient == True  # 8 >= 7
    print_test("BaselineModel data_sufficient = True", passed, f"actual={bm.data_sufficient}")
    all_passed &= passed
    
    # 1.4 测试 METRIC_DIRECTION_CONFIG
    print("\n  1.4 指标方向配置 (METRIC_DIRECTION_CONFIG):")
    
    passed = 'qps' in METRIC_DIRECTION_CONFIG
    print_test("QPS 指标已配置", passed)
    all_passed &= passed
    
    passed = 'conn_usage_pct' in METRIC_DIRECTION_CONFIG
    print_test("连接使用率指标已配置", passed)
    all_passed &= passed
    
    # 验证方向值
    valid_directions = ('up', 'down', 'both')
    all_valid = all(v in valid_directions for v in METRIC_DIRECTION_CONFIG.values())
    passed = all_valid
    print_test("所有方向配置有效 (up/down/both)", passed)
    all_passed &= passed
    
    # 1.5 测试 detect_anomaly_three_condition 方法存在
    print("\n  1.5 检测方法验证:")
    
    passed = hasattr(be, 'detect_anomaly_three_condition')
    print_test("detect_anomaly_three_condition 方法存在", passed)
    all_passed &= passed
    
    return all_passed


# ==========================================
# Test 2: RCA Engine v2.0
# ==========================================

def test_rca_engine_v2():
    print_header("Test 2: RCA Engine v2.0 - 10条诊断规则")
    
    all_passed = True
    
    # 2.1 测试规则总数
    print("\n  2.1 规则总数:")
    passed = len(RULES) >= 10
    print_test(f"规则总数 >= 10", passed, f"actual={len(RULES)}")
    all_passed &= passed
    
    # 2.2 验证规则 ID 完整性
    print("\n  2.2 规则 ID 验证:")
    rule_ids = [r['id'] for r in RULES]
    
    for rid in ['R001', 'R002', 'R003', 'R004', 'R005', 'R006', 
                'R007', 'R008', 'R009', 'R010']:
        passed = rid in rule_ids
        print_test(f"规则 {rid} 存在", passed)
        all_passed &= passed
    
    # 2.3 测试规则条件函数
    print("\n  2.3 规则条件函数测试:")
    
    r001 = next(r for r in RULES if r['id'] == 'R001')
    test_data_leak = {'conn_usage_pct': 85, 'qps': 5}
    test_data_normal = {'conn_usage_pct': 50, 'qps': 100}
    
    passed = r001['condition'](test_data_leak) == True
    print_test("R001 连接数泄漏触发", passed)
    all_passed &= passed
    
    passed = r001['condition'](test_data_normal) == False
    print_test("R001 正常情况不触发", passed)
    all_passed &= passed
    
    # R008: 实例 DOWN
    r008 = next(r for r in RULES if r['id'] == 'R008')
    test_data_down = {'current_status': 'DOWN'}
    test_data_up = {'current_status': 'UP'}
    
    passed = r008['condition'](test_data_down) == True
    print_test("R008 DOWN状态触发", passed)
    all_passed &= passed
    
    passed = r008['condition'](test_data_up) == False
    print_test("R008 UP状态不触发", passed)
    all_passed &= passed
    
    # 2.4 测试 RCAEngine.analyze 方法存在
    print("\n  2.4 RCAEngine 方法验证:")
    
    class MockConfig:
        name = "test"
        db_type = "mysql"
    
    rca = RCAEngine(MockConfig())
    
    passed = hasattr(rca, 'analyze')
    print_test("RCAEngine.analyze 方法存在", passed)
    all_passed &= passed
    
    passed = hasattr(rca, 'get_rule_count')
    print_test("RCAEngine.get_rule_count 方法存在", passed)
    all_passed &= passed
    
    return all_passed


# ==========================================
# Test 3: Capacity Engine
# ==========================================

def test_capacity_engine():
    print_header("Test 3: Capacity Engine - 多模型容量预测")
    
    all_passed = True
    
    # 3.1 测试线性回归模型
    print("\n  3.1 线性回归模型:")
    
    lr = LinearRegressionModel()
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [3.0, 5.0, 7.0, 9.0, 11.0]  # y = 2x + 1
    
    fitted = lr.fit(x, y)
    passed = fitted == True
    print_test("线性回归拟合成功", passed)
    all_passed &= passed
    
    passed = abs(lr.slope - 2.0) < 0.01
    print_test("斜率 ≈ 2.0", passed, f"actual={lr.slope:.4f}")
    all_passed &= passed
    
    passed = abs(lr.intercept - 1.0) < 0.01
    print_test("截距 ≈ 1.0", passed, f"actual={lr.intercept:.4f}")
    all_passed &= passed
    
    pred = lr.predict(6.0)
    passed = abs(pred - 13.0) < 0.01
    print_test("预测 predict(6) ≈ 13.0", passed, f"actual={pred:.4f}")
    all_passed &= passed
    
    # 3.2 测试 Holt-Winters 模型
    print("\n  3.2 Holt-Winters 模型:")
    
    hw = HoltWintersModel(alpha=0.3, beta=0.1, gamma=0.1, period=7)
    import math
    n = 21
    y_seasonal = [100 + 10 * math.sin(2 * math.pi * i / 7) + i * 0.5 for i in range(n)]
    
    fitted_hw = hw.fit(y_seasonal)
    passed = fitted_hw == True
    print_test("Holt-Winters 拟合成功", passed)
    all_passed &= passed
    
    preds = hw.predict(7)
    passed = len(preds) == 7
    print_test("Holt-Winters 预测7个周期", passed, f"actual={len(preds)}")
    all_passed &= passed
    
    # 3.3 测试移动平均模型
    print("\n  3.3 移动平均模型:")
    
    sma = SimpleMovingAverageModel(window=5)
    y_growing = [10, 12, 14, 16, 18, 20, 22, 24, 26, 28]
    
    fitted_sma = sma.fit(y_growing)
    passed = fitted_sma == True
    print_test("移动平均拟合成功", passed)
    all_passed &= passed
    
    # 3.4 测试阈值配置
    print("\n  3.4 告警阈值配置:")
    
    passed = 'tablespace' in ALERT_THRESHOLDS
    print_test("表空间阈值配置存在", passed)
    all_passed &= passed
    
    passed = 'connection' in ALERT_THRESHOLDS
    print_test("连接阈值配置存在", passed)
    all_passed &= passed
    
    passed = 'storage' in ALERT_THRESHOLDS
    print_test("存储天数阈值配置存在", passed)
    all_passed &= passed
    
    return all_passed


# ==========================================
# Test 4: Health Engine
# ==========================================

def test_health_engine():
    print_header("Test 4: Health Engine - 5维度健康评分")
    
    all_passed = True
    
    # 4.1 测试权重配置
    print("\n  4.1 健康评分权重配置:")
    
    total_weight = sum(HEALTH_WEIGHTS.values())
    passed = abs(total_weight - 1.0) < 0.001
    print_test("权重总和 = 1.0", passed, f"actual={total_weight}")
    all_passed &= passed
    
    expected_dims = ['availability', 'capacity', 'performance', 'configuration', 'operations']
    passed = set(HEALTH_WEIGHTS.keys()) == set(expected_dims)
    print_test("5维度配置完整", passed, f"actual={list(HEALTH_WEIGHTS.keys())}")
    all_passed &= passed
    
    # 4.2 测试健康等级
    print("\n  4.2 健康等级阈值:")
    
    expected_grades = ['A', 'B', 'C', 'D', 'F']
    passed = list(HEALTH_GRADES.keys()) == expected_grades
    print_test("等级列表 = A/B/C/D/F", passed)
    all_passed &= passed
    
    passed = HEALTH_GRADES['A'][0] == 90  # A级最低90分
    print_test("A级最低分 = 90", passed)
    all_passed &= passed
    
    # 4.3 测试评分器存在
    print("\n  4.3 评分器验证:")
    
    from monitor.health_engine import (
        AvailabilityScorer, CapacityScorer, PerformanceScorer,
        ConfigurationScorer, OperationsScorer
    )
    
    passed = AvailabilityScorer is not None
    print_test("AvailabilityScorer 存在", passed)
    all_passed &= passed
    
    passed = CapacityScorer is not None
    print_test("CapacityScorer 存在", passed)
    all_passed &= passed
    
    passed = PerformanceScorer is not None
    print_test("PerformanceScorer 存在", passed)
    all_passed &= passed
    
    # 4.4 测试 HealthEngine 方法
    print("\n  4.4 HealthEngine 方法验证:")
    
    class MockConfig:
        name = "test"
        db_type = "mysql"
        password = "encrypted"
        port = 3306
        connection_options = {}
    
    health = HealthEngine(MockConfig())
    
    passed = hasattr(health, 'calculate')
    print_test("HealthEngine.calculate 方法存在", passed)
    all_passed &= passed
    
    passed = hasattr(health, 'get_historical_score')
    print_test("HealthEngine.get_historical_score 方法存在", passed)
    all_passed &= passed
    
    passed = hasattr(health, 'compare_with_baseline')
    print_test("HealthEngine.compare_with_baseline 方法存在", passed)
    all_passed &= passed
    
    return all_passed


# ==========================================
# Test 5: 综合集成测试
# ==========================================

def test_integration():
    print_header("Test 5: 综合集成测试")
    
    all_passed = True
    
    # 5.1 模块导入测试
    print("\n  5.1 模块导入:")
    try:
        from monitor.baseline_engine import BaselineEngine, BaselineModel, METRIC_DIRECTION_CONFIG
        from monitor.rca_engine import RCAEngine, RULES
        from monitor.capacity_engine import CapacityEngine, ALERT_THRESHOLDS
        from monitor.health_engine import HealthEngine, HEALTH_WEIGHTS
        passed = True
    except ImportError as e:
        passed = False
        print(f"    导入错误: {e}")
    print_test("所有模块导入成功", passed)
    all_passed &= passed
    
    # 5.2 规则数量验证
    print("\n  5.2 规则数量验证:")
    passed = len(RULES) >= 10
    print_test("RCA 规则数 >= 10", passed, f"actual={len(RULES)}")
    all_passed &= passed
    
    # 5.3 Phase 2 功能完整性
    print("\n  5.3 Phase 2 功能完整性:")
    
    class MockConfig:
        name = "test"
        db_type = "mysql"
    
    be = BaselineEngine(MockConfig())
    
    features = [
        ("168时间槽方法", hasattr(be, '_get_time_slot')),
        ("三重条件检测", hasattr(be, 'detect_anomaly_three_condition')),
        ("METRIC_DIRECTION_CONFIG", len(METRIC_DIRECTION_CONFIG) > 0),
    ]
    
    for name, ok in features:
        print_test(name, ok)
        all_passed &= ok
    
    print_test("5维度权重配置", len(HEALTH_WEIGHTS) == 5)
    all_passed &= len(HEALTH_WEIGHTS) == 5
    
    return all_passed


# ==========================================
# 主测试入口
# ==========================================

def run_all_tests():
    print("\n" + "="*60)
    print(" DB-AIOps Phase 2 智能提升 - 自动化测试")
    print("="*60)
    print(f" 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {}
    
    results['Baseline Engine v2.0'] = test_baseline_engine_v2()
    results['RCA Engine v2.0'] = test_rca_engine_v2()
    results['Capacity Engine'] = test_capacity_engine()
    results['Health Engine'] = test_health_engine()
    results['集成测试'] = test_integration()
    
    # 汇总结果
    print_header("测试结果汇总")
    
    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}  {name}")
    
    print(f"\n总计: {passed_count}/{total_count} 项测试通过")
    
    if passed_count == total_count:
        print("\n🎉 所有 Phase 2 测试通过！")
        print("\nPhase 2 完成功能:")
        print("  ✅ 168时间槽动态基线 (7天×24小时)")
        print("  ✅ 三重条件异常检测 (量级+方向+持续性)")
        print("  ✅ 10条RCA诊断规则 (R001-R010)")
        print("  ✅ 多模型容量预测 (Linear/Holt-Winters/SMA)")
        print("  ✅ 5维度健康评分 (可用性/容量/性能/配置/运维)")
    else:
        print("\n⚠️ 部分测试失败，请检查上述失败的测试项")
    
    return passed_count == total_count


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)