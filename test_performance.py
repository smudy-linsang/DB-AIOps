"""
性能测试模块
============

对核心模块进行性能基准测试：
- 容量预测引擎
- FFT周期检测
- 缓存性能

Author: DB-AIOps Team
"""

import time
import json
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any


def benchmark_function(func, *args, iterations: int = 100, **kwargs) -> Dict[str, Any]:
    """
    基准测试函数
    
    Args:
        func: 要测试的函数
        *args: 函数参数
        iterations: 迭代次数
        **kwargs: 函数关键字参数
        
    Returns:
        性能统计字典
    """
    times = []
    result = None
    
    for _ in range(iterations):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        times.append(end - start)
    
    times_array = np.array(times) * 1000  # 转换为毫秒
    
    return {
        'iterations': iterations,
        'total_time_ms': float(np.sum(times_array)),
        'avg_time_ms': float(np.mean(times_array)),
        'min_time_ms': float(np.min(times_array)),
        'max_time_ms': float(np.max(times_array)),
        'median_time_ms': float(np.median(times_array)),
        'std_time_ms': float(np.std(times_array)),
        'p95_time_ms': float(np.percentile(times_array, 95)),
        'p99_time_ms': float(np.percentile(times_array, 99)),
        'result': result
    }


def generate_mock_timeseries_data(
    points: int = 1000,
    has_trend: bool = True,
    has_seasonality: bool = True,
    noise_level: float = 0.1
) -> List[Dict]:
    """
    生成模拟时序数据用于测试
    """
    data = []
    base_time = datetime.now() - timedelta(days=points // 24)
    
    for i in range(points):
        timestamp = base_time + timedelta(hours=i)
        value = 50.0  # 基础值
        
        if has_trend:
            value += i * 0.01  # 线性趋势
        
        if has_seasonality:
            # 每日周期 (24小时周期)
            hour = timestamp.hour
            value += 10 * np.sin(2 * np.pi * hour / 24)
        
        # 添加噪声
        value += np.random.normal(0, noise_level * value)
        
        data.append({
            'timestamp': timestamp.isoformat(),
            'value': float(value)
        })
    
    return data


def test_capacity_engine_performance():
    """容量预测引擎性能测试"""
    print("\n" + "="*60)
    print("容量预测引擎性能测试")
    print("="*60)
    
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
    import django
    django.setup()
    
    from monitor.capacity_engine import LinearRegressionModel, HoltWintersModel
    
    # 生成容量增长数据
    np.random.seed(42)
    n_points = 90  # 90天历史数据
    x = np.arange(n_points)
    y = 1000 + x * 10 + np.random.normal(0, 50, n_points)  # 线性增长
    
    print(f"\n1. 线性回归模型训练 ({n_points}个数据点):")
    model = LinearRegressionModel()
    result = benchmark_function(
        model.fit,
        x.tolist(), y.tolist(),
        iterations=100
    )
    print(f"   平均耗时: {result['avg_time_ms']:.2f} ms")
    print(f"   P95耗时: {result['p95_time_ms']:.2f} ms")
    
    print(f"\n2. 线性回归预测 (单次):")
    result = benchmark_function(
        model.predict,
        100.0,
        iterations=1000
    )
    print(f"   平均耗时: {result['avg_time_ms']:.4f} ms")
    
    print(f"\n3. Holt-Winters模型训练 ({n_points}个数据点):")
    hw_model = HoltWintersModel(alpha=0.3, beta=0.1, gamma=0.1, period=7)
    result = benchmark_function(
        hw_model.fit,
        y.tolist(),
        iterations=50
    )
    print(f"   平均耗时: {result['avg_time_ms']:.2f} ms")
    print(f"   P95耗时: {result['p95_time_ms']:.2f} ms")
    
    print(f"\n4. Holt-Winters预测 (7天):")
    result = benchmark_function(
        hw_model.predict,
        7,
        iterations=100
    )
    print(f"   平均耗时: {result['avg_time_ms']:.2f} ms")
    
    return True


def test_cycle_detector_performance():
    """FFT周期检测性能测试"""
    print("\n" + "="*60)
    print("FFT周期检测性能测试")
    print("="*60)
    
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
    import django
    django.setup()
    
    from monitor.cycle_detector import CycleDetector
    
    detector = CycleDetector()
    
    # 生成周期性数据 - 每周周期 (168小时)
    np.random.seed(42)
    n = 504  # 3周数据
    t = np.arange(n)
    # 周周期信号 (周期=168)
    data = 50 + 10 * np.sin(2 * np.pi * t / 168) + np.random.normal(0, 1, n)
    
    print(f"\n1. FFT周期检测 ({n}个数据点, 预期周期168小时):")
    result = benchmark_function(
        detector.detect_periodicity,
        data.tolist(),
        iterations=50
    )
    print(f"   平均耗时: {result['avg_time_ms']:.2f} ms")
    print(f"   P95耗时: {result['p95_time_ms']:.2f} ms")
    
    if result['result']:
        detected = result['result']
        print(f"   检测到的主要周期: {detected.get('dominant_periods', [])}")
        print(f"   是否包含周周期: {detector.has_weekly_cycle(data.tolist())}")
    
    return True


def test_config_advisor_performance():
    """配置检查引擎性能测试"""
    print("\n" + "="*60)
    print("配置检查引擎性能测试")
    print("="*60)
    
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
    import django
    django.setup()
    
    from monitor.config_advisor import ConfigAdvisor, ALL_RULES, MYSQL_RULES
    
    advisor = ConfigAdvisor()
    
    print(f"\n1. 配置规则数量:")
    print(f"   MySQL规则: {len(MYSQL_RULES)} 条")
    print(f"   全部规则: {len(ALL_RULES)} 条")
    
    print(f"\n2. 规则解析性能:")
    result = benchmark_function(
        lambda: [rule for rule in ALL_RULES if rule.db_type == 'mysql'],
        iterations=100
    )
    print(f"   平均耗时: {result['avg_time_ms']:.4f} ms")
    
    return True


def test_sql_parser_performance():
    """SQL解析器性能测试"""
    print("\n" + "="*60)
    print("SQL解析器性能测试")
    print("="*60)
    
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
    import django
    django.setup()
    
    from monitor.index_advisor import SQLParser
    
    parser = SQLParser()
    
    # 复杂SQL
    sql = """
    SELECT o.order_id, o.customer_name, o.order_date, p.product_name, p.price, oi.quantity
    FROM orders o
    INNER JOIN order_items oi ON o.order_id = oi.order_id
    INNER JOIN products p ON oi.product_id = p.product_id
    WHERE o.order_date >= '2024-01-01' 
      AND o.status = 'pending'
      AND p.category IN ('electronics', 'books', 'clothing')
    ORDER BY o.order_date DESC
    LIMIT 100
    """
    
    print(f"\n1. SQL解析 (复杂多表JOIN):")
    result = benchmark_function(
        parser.parse,
        sql,
        iterations=100
    )
    print(f"   平均耗时: {result['avg_time_ms']:.2f} ms")
    print(f"   P95耗时: {result['p95_time_ms']:.2f} ms")
    
    if result['result']:
        parsed = result['result']
        print(f"   提取到表名: {parsed.get('tables', [])}")
        print(f"   提取到列名: {len(parsed.get('columns', []))} 个")
    
    return True


def test_rate_limiter_performance():
    """限流器性能测试"""
    print("\n" + "="*60)
    print("限流器性能测试")
    print("="*60)
    
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
    import django
    django.setup()
    
    from monitor.rate_limit import RateLimiter
    
    limiter = RateLimiter(
        rate=100,  # 100 requests
        per=60     # per 60 seconds
    )
    
    print(f"\n1. 限流检查 (单次):")
    result = benchmark_function(
        limiter.is_allowed,
        'test_user',
        iterations=1000
    )
    print(f"   平均耗时: {result['avg_time_ms']:.4f} ms")
    print(f"   P99耗时: {result['p99_time_ms']:.4f} ms")
    
    return True


def test_cache_performance():
    """缓存性能测试"""
    print("\n" + "="*60)
    print("缓存性能测试")
    print("="*60)
    
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
    import django
    django.setup()
    
    from monitor.cache import CacheManager
    
    cache = CacheManager()
    test_key = "test_perf_key"
    
    # 写入测试
    test_data = {'value': 123, 'data': list(range(100))}
    
    print(f"\n1. 缓存写入:")
    result = benchmark_function(
        cache.set,
        test_key,
        test_data,
        iterations=100
    )
    print(f"   平均耗时: {result['avg_time_ms']:.2f} ms")
    
    print(f"\n2. 缓存读取:")
    result = benchmark_function(
        cache.get,
        test_key,
        iterations=100
    )
    print(f"   平均耗时: {result['avg_time_ms']:.2f} ms")
    
    # 清理
    cache.delete(test_key)
    
    return True


def test_health_score_calculation():
    """健康评分计算性能测试"""
    print("\n" + "="*60)
    print("健康评分计算性能测试")
    print("="*60)
    
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dbmonitor.settings')
    import django
    django.setup()
    
    # 简单性能测试 - 直接计算评分
    print(f"\n1. 综合评分计算:")
    
    def calculate_health_score(metrics):
        # 模拟健康评分计算
        cpu = metrics.get('cpu_usage', 0)
        memory = metrics.get('memory_usage', 0)
        disk = metrics.get('disk_usage', 0)
        
        # 简单加权平均
        score = 100 - (cpu * 0.4 + memory * 0.3 + disk * 0.3)
        return max(0, min(100, score))
    
    metrics = {
        'cpu_usage': 65.5,
        'memory_usage': 78.2,
        'disk_usage': 72.0
    }
    
    result = benchmark_function(
        calculate_health_score,
        metrics,
        iterations=1000
    )
    print(f"   平均耗时: {result['avg_time_ms']:.4f} ms")
    print(f"   P95耗时: {result['p95_time_ms']:.4f} ms")
    
    return True


def run_all_performance_tests():
    """运行所有性能测试"""
    print("\n" + "#"*60)
    print("# DB-AIOps 性能测试套件")
    print("#"*60)
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    all_results = {}
    
    # 1. 容量引擎
    try:
        test_capacity_engine_performance()
        all_results['capacity_engine'] = 'PASS'
    except Exception as e:
        print(f"   [错误] {str(e)}")
        all_results['capacity_engine'] = f'FAIL: {str(e)}'
    
    # 2. 周期检测
    try:
        test_cycle_detector_performance()
        all_results['cycle_detector'] = 'PASS'
    except Exception as e:
        print(f"   [错误] {str(e)}")
        all_results['cycle_detector'] = f'FAIL: {str(e)}'
    
    # 3. 配置检查
    try:
        test_config_advisor_performance()
        all_results['config_advisor'] = 'PASS'
    except Exception as e:
        print(f"   [错误] {str(e)}")
        all_results['config_advisor'] = f'FAIL: {str(e)}'
    
    # 4. SQL解析
    try:
        test_sql_parser_performance()
        all_results['sql_parser'] = 'PASS'
    except Exception as e:
        print(f"   [错误] {str(e)}")
        all_results['sql_parser'] = f'FAIL: {str(e)}'
    
    # 5. 限流器
    try:
        test_rate_limiter_performance()
        all_results['rate_limiter'] = 'PASS'
    except Exception as e:
        print(f"   [错误] {str(e)}")
        all_results['rate_limiter'] = f'FAIL: {str(e)}'
    
    # 6. 缓存
    try:
        test_cache_performance()
        all_results['cache'] = 'PASS'
    except Exception as e:
        print(f"   [错误] {str(e)}")
        all_results['cache'] = f'FAIL: {str(e)}'
    
    # 7. 健康评分
    try:
        test_health_score_calculation()
        all_results['health_score'] = 'PASS'
    except Exception as e:
        print(f"   [错误] {str(e)}")
        all_results['health_score'] = f'FAIL: {str(e)}'
    
    # 汇总
    print("\n" + "#"*60)
    print("# 性能测试结果汇总")
    print("#"*60)
    
    passed = 0
    failed = 0
    for module, status in all_results.items():
        status_symbol = "[OK]" if status == "PASS" else "[FAIL]"
        print(f"  {status_symbol} {module}: {status}")
        if status == "PASS":
            passed += 1
        else:
            failed += 1
    
    print(f"\n通过: {passed}/{len(all_results)}")
    print(f"失败: {failed}/{len(all_results)}")
    
    return all_results


if __name__ == '__main__':
    results = run_all_performance_tests()
