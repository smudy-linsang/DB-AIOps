"""
Celery 任务定义

这些任务由 Celery Beat 定时调度，或由其他代码异步触发。
"""

from celery import shared_task
from django.utils import timezone
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def collect_all_databases(self):
    """
    采集所有数据库指标
    
    由 Celery Beat 每 5 分钟调度一次。
    也可以手动触发：collect_all_databases.delay()
    """
    from monitor.models import DatabaseConfig
    from monitor.management.commands.start_monitor import Command
    
    try:
        # 获取所有活跃的数据库配置
        configs = DatabaseConfig.objects.filter(is_active=True)
        
        results = []
        for config in configs:
            try:
                # 调用采集逻辑
                cmd = Command()
                cmd.handle_single(config)
                results.append({
                    'config_id': config.id,
                    'config_name': config.name,
                    'status': 'success'
                })
            except Exception as e:
                results.append({
                    'config_id': config.id,
                    'config_name': config.name,
                    'status': 'failed',
                    'error': str(e)
                })
        
        # 统计结果
        success_count = sum(1 for r in results if r['status'] == 'success')
        failed_count = len(results) - success_count
        
        logger.info(f"采集完成: 成功 {success_count}, 失败 {failed_count}")
        
        return {
            'total': len(results),
            'success': success_count,
            'failed': failed_count,
            'results': results
        }
    except Exception as e:
        logger.error(f"采集任务异常: {e}")
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True)
def collect_single_database(self, config_id: int):
    """
    采集单个数据库指标
    
    Args:
        config_id: 数据库配置 ID
    """
    from monitor.models import DatabaseConfig
    from monitor.management.commands.start_monitor import Command
    
    try:
        config = DatabaseConfig.objects.get(id=config_id)
        cmd = Command()
        cmd.handle_single(config)
        return {'config_id': config_id, 'status': 'success'}
    except DatabaseConfig.DoesNotExist:
        return {'config_id': config_id, 'status': 'error', 'message': 'Config not found'}
    except Exception as e:
        logger.error(f"采集数据库 {config_id} 失败: {e}")
        return {'config_id': config_id, 'status': 'error', 'message': str(e)}


@shared_task
def run_baseline_calculation():
    """
    执行基线重算任务
    
    每天凌晨 2 点执行，计算所有数据库指标的动态基线。
    """
    from monitor.models import DatabaseConfig
    from monitor.baseline_engine import BaselineEngine
    
    try:
        configs = DatabaseConfig.objects.filter(is_active=True)
        
        for config in configs:
            try:
                engine = BaselineEngine(config)
                engine.calculate_all_baselines(days=28)
                logger.info(f"基线重算完成: {config.name}")
            except Exception as e:
                logger.error(f"基线重算失败 {config.name}: {e}")
        
        return {'status': 'completed', 'configs_processed': configs.count()}
    except Exception as e:
        logger.error(f"基线重算任务异常: {e}")
        raise


@shared_task
def run_capacity_prediction():
    """
    执行容量预测任务
    
    每天凌晨 3 点执行，对所有容量类指标进行预测。
    """
    from monitor.models import DatabaseConfig
    from monitor.capacity_engine import CapacityEngine
    
    try:
        configs = DatabaseConfig.objects.filter(is_active=True)
        
        for config in configs:
            try:
                engine = CapacityEngine(config)
                engine.predict_all()
                logger.info(f"容量预测完成: {config.name}")
            except Exception as e:
                logger.error(f"容量预测失败 {config.name}: {e}")
        
        return {'status': 'completed', 'configs_processed': configs.count()}
    except Exception as e:
        logger.error(f"容量预测任务异常: {e}")
        raise


@shared_task
def run_health_check():
    """
    执行健康评分检查任务
    
    每 6 小时执行，计算所有数据库的健康评分。
    """
    from monitor.models import DatabaseConfig
    from monitor.health_engine import HealthEngine
    
    try:
        configs = DatabaseConfig.objects.filter(is_active=True)
        
        for config in configs:
            try:
                engine = HealthEngine(config)
                engine.calculate_health_score()
                logger.info(f"健康评分完成: {config.name}")
            except Exception as e:
                logger.error(f"健康评分失败 {config.name}: {e}")
        
        return {'status': 'completed', 'configs_processed': configs.count()}
    except Exception as e:
        logger.error(f"健康评分任务异常: {e}")
        raise


@shared_task
def check_alerts():
    """
    检查告警状态
    
    每分钟执行，检查活跃告警是否已恢复。
    """
    from monitor.models import AlertLog
    from monitor.alert_manager import AlertManager
    
    try:
        # 获取所有活跃告警
        active_alerts = AlertLog.objects.filter(status='active')
        
        for alert in active_alerts:
            try:
                # 检查告警是否已恢复
                manager = AlertManager(alert.config)
                if manager.check_if_resolved(alert):
                    alert.status = 'resolved'
                    alert.resolved_at = timezone.now()
                    alert.save()
                    logger.info(f"告警已恢复: {alert.title}")
            except Exception as e:
                logger.error(f"告警检查失败 {alert.id}: {e}")
        
        return {'status': 'completed', 'alerts_checked': active_alerts.count()}
    except Exception as e:
        logger.error(f"告警检查任务异常: {e}")
        raise


@shared_task
def send_reminder_notification(alert_id: int):
    """
    发送告警提醒通知
    
    如果告警持续活跃，定期发送提醒。
    """
    from monitor.models import AlertLog
    from monitor.notifications import send_alert_notification
    
    try:
        alert = AlertLog.objects.get(id=alert_id)
        
        # 检查是否需要发送提醒（超过 30 分钟未确认）
        if alert.status == 'active':
            time_since_created = timezone.now() - alert.last_notified_at
            if time_since_created.total_seconds() > 1800:  # 30 分钟
                send_alert_notification(alert)
                alert.last_notified_at = timezone.now()
                alert.save()
                logger.info(f"发送告警提醒: {alert.title}")
        
        return {'status': 'completed', 'alert_id': alert_id}
    except AlertLog.DoesNotExist:
        return {'status': 'error', 'message': 'Alert not found'}
    except Exception as e:
        logger.error(f"发送提醒失败 {alert_id}: {e}")
        raise


@shared_task
def cleanup_old_logs():
    """
    清理旧日志
    
    定期执行，清理过期的监控日志和快照。
    """
    from monitor.models import MonitorLog
    from django.conf import settings
    
    try:
        # 保留天数配置
        retention_days = getattr(settings, 'LOG_RETENTION_DAYS', 90)
        cutoff_date = timezone.now() - timedelta(days=retention_days)
        
        # 删除旧日志
        deleted_count, _ = MonitorLog.objects.filter(create_time__lt=cutoff_date).delete()
        
        logger.info(f"清理旧日志: 删除 {deleted_count} 条")
        
        return {'status': 'completed', 'deleted_count': deleted_count}
    except Exception as e:
        logger.error(f"清理日志任务异常: {e}")
        raise


@shared_task(bind=True)
def run_slow_query_analysis(self, config_id: int, days: int = 7):
    """
    执行慢查询分析任务
    
    Args:
        config_id: 数据库配置 ID
        days: 分析最近几天的数据
    """
    from monitor.models import DatabaseConfig
    from monitor.slow_query_engine import SlowQueryEngine
    
    try:
        config = DatabaseConfig.objects.get(id=config_id)
        engine = SlowQueryEngine(config)
        
        # 采集慢查询
        queries = engine.collect_slow_queries(days=days)
        
        # 分析慢查询
        analysis = engine.analyze_queries(queries)
        
        logger.info(f"慢查询分析完成: {config.name}, 采集 {len(queries)} 条")
        
        return {
            'status': 'completed',
            'config_id': config_id,
            'queries_collected': len(queries),
            'analysis': analysis
        }
    except DatabaseConfig.DoesNotExist:
        return {'status': 'error', 'message': 'Config not found'}
    except Exception as e:
        logger.error(f"慢查询分析失败 {config_id}: {e}")
        raise


@shared_task(bind=True)
def run_config_advisor_check(self, config_id: int, db_type: str = None):
    """
    执行配置检查任务
    
    Args:
        config_id: 数据库配置 ID
        db_type: 数据库类型 (mysql/postgresql/oracle)
    """
    from monitor.models import DatabaseConfig
    from monitor.config_advisor import ConfigAdvisor
    
    try:
        config = DatabaseConfig.objects.get(id=config_id)
        advisor = ConfigAdvisor()
        
        # 如果未指定类型，从配置获取
        if db_type is None:
            db_type = config.db_type
        
        # 执行检查
        results = advisor.check_by_db_type(db_type)
        
        logger.info(f"配置检查完成: {config.name}, 检查 {len(results)} 条规则")
        
        failed_rules = [r for r in results if not r.get('passed', True)]
        
        return {
            'status': 'completed',
            'config_id': config_id,
            'total_rules': len(results),
            'passed_rules': len(results) - len(failed_rules),
            'failed_rules': len(failed_rules)
        }
    except DatabaseConfig.DoesNotExist:
        return {'status': 'error', 'message': 'Config not found'}
    except Exception as e:
        logger.error(f"配置检查失败 {config_id}: {e}")
        raise


@shared_task
def generate_daily_report():
    """
    生成日报任务
    
    每天凌晨 4 点执行，为所有活跃数据库生成监控日报。
    """
    from monitor.models import DatabaseConfig
    from monitor.report_engine import ReportService
    
    try:
        configs = DatabaseConfig.objects.filter(is_active=True)
        service = ReportService()
        
        # 生成所有数据库的日报
        results = service.generate_daily_report(config_ids=[c.id for c in configs])
        
        logger.info(f"日报生成完成: 处理 {len(configs)} 个数据库")
        
        return {
            'status': 'completed',
            'configs_processed': len(configs),
            'reports': results
        }
    except Exception as e:
        logger.error(f"日报生成任务异常: {e}")
        raise


@shared_task
def generate_monthly_report(month: str = None):
    """
    生成月报任务
    
    Args:
        month: 月份，格式 YYYY-MM，默认为上个月
    """
    from monitor.models import DatabaseConfig
    from monitor.report_engine import ReportService
    from dateutil.relativedelta import relativedelta
    
    try:
        if month is None:
            # 上个月
            today = datetime.now()
            last_month = today - relativedelta(months=1)
            month = last_month.strftime('%Y-%m')
        
        configs = DatabaseConfig.objects.filter(is_active=True)
        service = ReportService()
        
        # 生成月报
        results = service.generate_monthly_report(month=month)
        
        logger.info(f"月报生成完成: {month}, 处理 {len(configs)} 个数据库")
        
        return {
            'status': 'completed',
            'month': month,
            'configs_processed': len(configs),
            'reports': results
        }
    except ImportError:
        return {'status': 'error', 'message': 'python-dateutil not installed'}
    except Exception as e:
        logger.error(f"月报生成任务异常: {e}")
        raise


@shared_task
def sync_baseline_to_timeseries(config_id: int = None):
    """
    同步基线数据到 TimescaleDB
    
    Args:
        config_id: 数据库配置 ID，None 表示同步所有
    """
    from monitor.models import DatabaseConfig
    from monitor.timeseries import TimeSeriesWriter
    
    try:
        writer = TimeSeriesWriter()
        
        if config_id is None:
            # 同步所有
            configs = DatabaseConfig.objects.filter(is_active=True)
            for config in configs:
                _sync_single_baseline(writer, config)
        else:
            config = DatabaseConfig.objects.get(id=config_id)
            _sync_single_baseline(writer, config)
        
        return {'status': 'completed'}
    except Exception as e:
        logger.error(f"基线同步任务异常: {e}")
        raise


def _sync_single_baseline(writer, config):
    """同步单个数据库的基线数据"""
    from monitor.baseline_engine import BaselineEngine
    
    try:
        engine = BaselineEngine(config)
        engine.sync_to_timeseries(writer)
        logger.info(f"基线同步完成: {config.name}")
    except Exception as e:
        logger.error(f"基线同步失败 {config.name}: {e}")
