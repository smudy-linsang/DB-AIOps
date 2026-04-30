"""
Celery 任务定义 v2.0

职责：
- 采集任务异步化
- 基线重算任务
- 容量预测任务
- 健康评分任务
- 报告生成任务
"""

import logging
import json
from datetime import datetime, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def collect_single_db(self, config_id):
    """采集单个数据库的指标"""
    from monitor.models import DatabaseConfig, MonitorLog
    from monitor.management.commands.start_monitor import CHECKER_MAP

    try:
        config = DatabaseConfig.objects.get(id=config_id, is_active=True)
    except DatabaseConfig.DoesNotExist:
        logger.warning(f"[Celery] 数据库配置 {config_id} 不存在或已禁用")
        return {'status': 'skipped', 'config_id': config_id}

    checker_class = CHECKER_MAP.get(config.db_type)
    if not checker_class:
        logger.warning(f"[Celery] 不支持的数据库类型: {config.db_type}")
        return {'status': 'skipped', 'config_id': config_id, 'reason': 'unsupported_type'}

    # 创建一个简单的命令实例用于process_result
    class TaskCommand:
        def process_result(self, config, status, data):
            MonitorLog.objects.create(
                config=config,
                status=status,
                message=json.dumps(data, ensure_ascii=False, default=str)
            )

    checker = checker_class(TaskCommand())

    try:
        checker.check(config)
        return {'status': 'success', 'config_id': config_id}
    except Exception as exc:
        logger.error(f"[Celery] 采集失败 config_id={config_id}: {exc}")
        # 记录失败日志
        MonitorLog.objects.create(
            config=config,
            status='DOWN',
            message=json.dumps({'error': str(exc)}, ensure_ascii=False)
        )
        raise self.retry(exc=exc)


@shared_task
def collect_all_databases():
    """采集所有活跃数据库（分发子任务）"""
    from monitor.models import DatabaseConfig

    configs = DatabaseConfig.objects.filter(is_active=True)
    task_ids = []

    for config in configs:
        result = collect_single_db.delay(config.id)
        task_ids.append(result.id)

    logger.info(f"[Celery] 已分发 {len(task_ids)} 个采集任务")
    return {'dispatched': len(task_ids), 'task_ids': task_ids}


@shared_task
def recalculate_baselines():
    """重算所有数据库的基线模型"""
    from monitor.models import DatabaseConfig, BaselineModel as BaselineModelDB
    from monitor.baseline_engine import BaselineEngine

    configs = DatabaseConfig.objects.filter(is_active=True)
    results = []

    for config in configs:
        try:
            engine = BaselineEngine(config)
            all_baselines = engine.calculate_full_baseline(days=28)

            saved_count = 0
            for metric_key, slot_baselines in all_baselines.items():
                for slot, model in slot_baselines.items():
                    if model.data_sufficient:
                        BaselineModelDB.objects.update_or_create(
                            config=config,
                            metric_key=metric_key,
                            time_slot=slot,
                            defaults={
                                'sample_count': model.sample_count,
                                'mean': model.mean,
                                'std': model.std,
                                'p90': model.p90,
                                'p95': model.p95,
                                'p99': model.p99,
                                'normal_min': model.normal_min,
                                'normal_max': model.normal_max,
                                'data_sufficient': True,
                            }
                        )
                        saved_count += 1

            results.append({'config': config.name, 'saved': saved_count})
            logger.info(f"[Celery] 基线重算完成: {config.name}, 保存 {saved_count} 个基线模型")
        except Exception as e:
            logger.error(f"[Celery] 基线重算失败: {config.name}: {e}")
            results.append({'config': config.name, 'error': str(e)})

    return results


@shared_task
def run_capacity_predictions():
    """运行容量预测"""
    from monitor.models import DatabaseConfig, PredictionResult
    from monitor.capacity_engine import CapacityEngine

    configs = DatabaseConfig.objects.filter(is_active=True)
    results = []

    for config in configs:
        try:
            engine = CapacityEngine(config)
            report = engine.analyze_all_metrics()

            for metric_name, metric_result in report.get('metrics', {}).items():
                if 'error' not in metric_result:
                    PredictionResult.objects.update_or_create(
                        config=config,
                        metric_key=metric_name,
                        resource_name=metric_result.get('resource_name', ''),
                        defaults={
                            'current_value': metric_result.get('current_value'),
                            'monthly_growth_rate': metric_result.get('monthly_growth_rate'),
                            'model_used': metric_result.get('model_used'),
                            'confidence': metric_result.get('confidence'),
                            'recommendation': json.dumps(metric_result, ensure_ascii=False),
                        }
                    )

            results.append({'config': config.name, 'alerts': len(report.get('alerts', []))})
        except Exception as e:
            logger.error(f"[Celery] 容量预测失败: {config.name}: {e}")
            results.append({'config': config.name, 'error': str(e)})

    return results


@shared_task
def run_health_scoring():
    """运行健康评分"""
    from monitor.models import DatabaseConfig, HealthScore
    from monitor.health_engine import HealthEngine

    configs = DatabaseConfig.objects.filter(is_active=True)
    results = []
    today = timezone.now().date()

    for config in configs:
        try:
            engine = HealthEngine(config)
            report = engine.calculate()

            HealthScore.objects.update_or_create(
                config=config,
                score_date=today,
                defaults={
                    'total_score': report.get('overall_score', 0),
                    'availability_score': report.get('dimensions', {}).get('availability', {}).get('score', 0),
                    'capacity_score': report.get('dimensions', {}).get('capacity', {}).get('score', 0),
                    'performance_score': report.get('dimensions', {}).get('performance', {}).get('score', 0),
                    'config_score': report.get('dimensions', {}).get('configuration', {}).get('score', 0),
                    'ops_score': report.get('dimensions', {}).get('operations', {}).get('score', 0),
                    'grade': report.get('grade', 'F'),
                    'score_detail': report.get('dimensions'),
                }
            )

            results.append({
                'config': config.name,
                'score': report.get('overall_score', 0),
                'grade': report.get('grade', 'F')
            })
        except Exception as e:
            logger.error(f"[Celery] 健康评分失败: {config.name}: {e}")
            results.append({'config': config.name, 'error': str(e)})

    return results


@shared_task
def generate_daily_report():
    """生成日报"""
    from monitor.report_engine import ReportService

    try:
        service = ReportService()
        result = service.generate_daily_report()
        logger.info(f"[Celery] 日报生成完成")
        return {'status': 'success', 'result': result}
    except Exception as e:
        logger.error(f"[Celery] 日报生成失败: {e}")
        return {'status': 'error', 'error': str(e)}


@shared_task
def update_platform_metrics():
    """更新平台自身指标"""
    from monitor.models import (
        DatabaseConfig, MonitorLog, AlertLog, PlatformMetric
    )

    now = timezone.now()
    five_min_ago = now - timedelta(minutes=5)

    # 活跃数据库数
    active_dbs = DatabaseConfig.objects.filter(is_active=True).count()
    PlatformMetric.objects.update_or_create(
        name='active_databases',
        labels=None,
        defaults={'metric_type': 'gauge', 'value': active_dbs, 'help_text': '活跃数据库数量'}
    )

    # 最近5分钟有采集的数据库数
    recent_collects = MonitorLog.objects.filter(
        create_time__gte=five_min_ago
    ).values('config_id').distinct().count()
    PlatformMetric.objects.update_or_create(
        name='collected_databases_5min',
        labels=None,
        defaults={'metric_type': 'gauge', 'value': recent_collects, 'help_text': '最近5分钟采集到的数据库数'}
    )

    # 活跃告警数
    active_alerts = AlertLog.objects.filter(status='active').count()
    PlatformMetric.objects.update_or_create(
        name='active_alerts',
        labels=None,
        defaults={'metric_type': 'gauge', 'value': active_alerts, 'help_text': '活跃告警数量'}
    )

    # 采集成功率（最近1小时）
    one_hour_ago = now - timedelta(hours=1)
    total_logs = MonitorLog.objects.filter(create_time__gte=one_hour_ago).count()
    up_logs = MonitorLog.objects.filter(create_time__gte=one_hour_ago, status='UP').count()
    success_rate = (up_logs / total_logs * 100) if total_logs > 0 else 0
    PlatformMetric.objects.update_or_create(
        name='collect_success_rate_1h',
        labels=None,
        defaults={'metric_type': 'gauge', 'value': round(success_rate, 2), 'help_text': '最近1小时采集成功率(%)'}
    )

    return {
        'active_databases': active_dbs,
        'collected_5min': recent_collects,
        'active_alerts': active_alerts,
        'success_rate_1h': round(success_rate, 2),
    }
